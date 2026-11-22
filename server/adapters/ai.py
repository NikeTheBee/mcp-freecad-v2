"""Neural networks for piloting and decisions in mechatronics (env `ai`: PyTorch CPU, Gymnasium,
Stable-Baselines3, ONNX Runtime).

Kinds:
- nn_fit     supervised network (regression or classification) on a CSV of the study folder or
             on samples of a DECLARED function; validation metrics, size, operations per inference,
             ONNX export checked against PyTorch, latency, fit into a target's flash / RAM
- rl_train   reinforcement learning (PPO, SAC, A2C, TD3) on a Gymnasium environment of the list
             below (classic control and MuJoCo), OR on YOUR robot (env "custom"): a template
             (cart_pole, balance_bot) or your MJCF, observed sensors, a declared reward and end
             condition, control rate, domain randomisation (mass, sensor noise, initial state);
             evaluation over N episodes, the policy exported to ONNX and checked action by action
- onnx_check an ONNX file of the study folder: inputs/outputs, operators, parameters, size,
             a test inference, latency, and operators outside the microcontroller-friendly set

Device: params.device cpu | gpu | both (default: the workshop's compute profile). gpu/both train
the network on CUDA in the `ai-gpu` environment; RL physics always steps on the CPU; export,
ONNX checks and latency are on the CPU, as on a target. `device_used` says what ran.

Security: models leave as ONNX only. Nothing is saved or loaded with pickle (`.pt`, `.pth`, `.pkl`,
`.zip` of Stable-Baselines3 are refused), an ONNX file with external data or a custom operator
domain is refused, and a declared function goes through the whitelist of `mdo` and an AST walker
(names, numbers, arithmetic, listed functions), never `eval`. A trained policy is a simulation
result: it never drives a real motor from here.

Limits, restated in every result: a network is only as good as the data or the simulator it learnt
from (sim-to-real gap); validation numbers are on held-out samples of the SAME distribution; RL
results depend on the seed -- the result states it; latency is this PC's, not the target's.

Exactness: REFERENCES below -- sin(x) fitted on [-pi, pi] to < 0.01, a circle classified at
>= 97 %, CartPole-v1 solved (mean return >= 475 over 20 episodes, the Gymnasium threshold) with
the ONNX policy taking the same action as the trained one, and a hand-built ONNX graph whose
parameter count and output are known exactly.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

import ast  # noqa: E402
import math  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Tuple  # noqa: E402

from _common import result  # noqa: E402

ENV = "ai"
KINDS = {
    "nn_fit": {"geometry": False, "timeout": 1800,
               "doc": "supervised MLP (regression/classification) on CSV or a declared function: "
                      "metrics, size, MACs, ONNX export checked, latency, flash/RAM fit"},
    "rl_train": {"geometry": False, "timeout": 3600,
                 "doc": "PPO/SAC/A2C/TD3 on a Gymnasium env (classic control, MuJoCo): return, "
                        "ONNX policy checked against the trained one"},
    "onnx_check": {"geometry": False, "timeout": 300,
                   "doc": "ONNX file: I/O, operators, params, size, test inference, latency, MCU-friendly ops"},
}

ENVS = ("CartPole-v1", "Acrobot-v1", "MountainCar-v0", "MountainCarContinuous-v0", "Pendulum-v1",
        "InvertedPendulum-v5", "InvertedDoublePendulum-v5", "Reacher-v5", "Hopper-v5",
        "HalfCheetah-v5", "Walker2d-v5", "Swimmer-v5", "Ant-v5")
ALGOS = ("PPO", "SAC", "A2C", "TD3")
MCU_OPS = {"Gemm", "MatMul", "Add", "Sub", "Mul", "Div", "Relu", "LeakyRelu", "Tanh", "Sigmoid",
           "Softmax", "Conv", "MaxPool", "AveragePool", "GlobalAveragePool", "Flatten", "Reshape",
           "Concat", "Clip", "BatchNormalization", "Squeeze", "Unsqueeze", "Transpose", "ArgMax",
           "Identity", "Constant", "Cast", "Shape", "Gather", "Slice", "Exp", "Log", "Neg", "Abs"}
_FN = {"sqrt", "exp", "log", "log10", "sin", "cos", "tan", "arcsin", "arccos", "arctan", "tanh",
       "sinh", "cosh", "abs", "minimum", "maximum", "sign", "arctan2"}


def versions() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for mod in ("torch", "onnx", "onnxruntime", "gymnasium", "stable_baselines3", "numpy"):
        try:
            out[mod] = str(__import__(mod).__version__)
        except Exception as e:  # noqa: BLE001
            out[mod] = f"missing ({type(e).__name__})"
    return out


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fn = {"nn_fit": _nn_fit, "rl_train": _rl_train, "onnx_check": _onnx_check}.get(kind)
    if fn is None:
        raise ValueError(f"unknown kind {kind!r}; one of {sorted(KINDS)}")
    return fn(inp, Path(work))


def _inside(work: Path, name: str, exts: Tuple[str, ...]) -> Path:
    p = (work / str(name)).resolve()
    if work.resolve() not in p.parents:
        raise ValueError(f"{name!r} must be inside the study folder")
    if p.suffix.lower() in (".pt", ".pth", ".pkl", ".pickle", ".zip", ".joblib", ".npz"):
        raise ValueError(f"{p.name}: pickle-based model files are refused (they run code on load); use ONNX")
    if p.suffix.lower() not in exts:
        raise ValueError(f"{p.name}: expected {exts}")
    return p


def _device(inp: Dict[str, Any]):
    """(torch device, label): params.device, else the runner's MCPFC_DEVICE (cpu/gpu/both).
    gpu and both train the network on CUDA when this environment has it; the physics of RL
    environments always steps on the CPU (that is what `both` means)."""
    import torch
    want = str(inp.get("device") or os.environ.get("MCPFC_DEVICE", "cpu")).lower()
    workers = int(os.environ.get("MCPFC_CPU_WORKERS", "0") or 0) or max(1, min(4, os.cpu_count() or 1))
    torch.set_num_threads(max(1, workers))
    if want in ("gpu", "both") and torch.cuda.is_available():
        return torch.device("cuda"), want, torch.cuda.get_device_name(0)
    return torch.device("cpu"), "cpu", ""


# ---------------------------------------------------------------- safe declared function
def _evaluate(expr: str, env: Dict[str, Any]):
    import numpy as np
    from mdo import _check_expr
    _check_expr(expr, tuple(env))
    tree = ast.parse(expr, mode="eval")
    ops = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide,
           ast.Pow: np.power}

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id in env:
                return env[n.id]
            if n.id == "pi":
                return math.pi
            if n.id == "e":
                return math.e
            raise ValueError(f"unknown name {n.id}")
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            return ops[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FN
                and not n.keywords):
            return getattr(np, n.func.id)(*[ev(a) for a in n.args])
        raise ValueError(f"construct not allowed in {expr!r}")
    return ev(tree)


def _dataset(inp: Dict[str, Any], work: Path):
    import numpy as np
    task = str(inp.get("task", "regression"))
    if inp.get("data_file"):
        p = _inside(work, inp["data_file"], (".csv", ".txt"))
        arr = np.loadtxt(p, delimiter=",", skiprows=int(inp.get("skip_rows", 1)), ndmin=2,
                         max_rows=2_000_000)
        xi = [int(c) for c in inp.get("input_columns") or []]
        yi = [int(c) for c in inp.get("output_columns") or []]
        if not xi or not yi:
            raise ValueError("input_columns and output_columns (indices) are required with data_file")
        return arr[:, xi].astype("float32"), arr[:, yi].astype("float32"), task
    fn = inp.get("function")
    ranges = inp.get("inputs")
    if not isinstance(fn, dict) or not isinstance(ranges, dict) or not fn or not ranges:
        raise ValueError("give data_file, or function {out: expr} and inputs {name: [lo, hi]}")
    n = int(inp.get("samples", 2000))
    if not 10 <= n <= 1_000_000:
        raise ValueError("samples in [10, 1e6]")
    rng = np.random.default_rng(int(inp.get("seed", 0)))
    env = {}
    for k, (lo, hi) in ranges.items():
        env[str(k)] = rng.uniform(float(lo), float(hi), n)
    X = np.stack([env[k] for k in ranges], axis=1)
    Y = np.stack([np.broadcast_to(_evaluate(str(e), env), (n,)) for e in fn.values()], axis=1)
    if task == "classification":
        Y = (Y > 0).astype("float32")
    return X.astype("float32"), Y.astype("float32"), task


def _mlp(n_in: int, n_out: int, hidden: List[int], act: str):
    import torch.nn as nn
    acts = {"relu": nn.ReLU, "tanh": nn.Tanh, "sigmoid": nn.Sigmoid, "leaky_relu": nn.LeakyReLU}
    if act not in acts:
        raise ValueError(f"activation in {sorted(acts)}")
    layers: List[Any] = []
    d = n_in
    for h in hidden:
        layers += [nn.Linear(d, int(h)), acts[act]()]
        d = int(h)
    layers.append(nn.Linear(d, n_out))
    return nn.Sequential(*layers)


def _export(model, example, path: Path, out_names=("y",)):
    import torch
    model.eval()
    torch.onnx.export(model, (example,), str(path), input_names=["x"], output_names=list(out_names),
                      dynamic_axes={"x": {0: "batch"}}, opset_version=17, dynamo=False)


def _ort(path: Path):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])


def _latency_us(sess, x) -> float:
    name = sess.get_inputs()[0].name
    for _ in range(20):
        sess.run(None, {name: x})
    t0 = time.perf_counter()
    for _ in range(200):
        sess.run(None, {name: x})
    return (time.perf_counter() - t0) / 200 * 1e6


def _size_vals(model, inp: Dict[str, Any]) -> Dict[str, Any]:
    params = sum(p.numel() for p in model.parameters())
    macs = sum(m.in_features * m.out_features for m in model.modules()
               if m.__class__.__name__ == "Linear")
    widest = max([m.out_features for m in model.modules() if m.__class__.__name__ == "Linear"] + [1])
    v = {"params": int(params), "macs_per_inference": int(macs), "size_fp32_kB": round(params * 4 / 1024, 3),
         "size_int8_kB": round(params / 1024, 3), "activation_ram_fp32_kB": round(2 * widest * 4 / 1024, 4)}
    if inp.get("target_flash_kB") is not None:
        v["flash_ratio_int8"] = round(v["size_int8_kB"] / float(inp["target_flash_kB"]), 5)
        v["flash_ratio_fp32"] = round(v["size_fp32_kB"] / float(inp["target_flash_kB"]), 5)
    if inp.get("target_ram_kB") is not None:
        v["ram_ratio_fp32"] = round(v["activation_ram_fp32_kB"] / float(inp["target_ram_kB"]), 5)
    return v


def _nn_fit(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import numpy as np
    import torch
    seed = int(inp.get("seed", 0))
    torch.manual_seed(seed)
    dev, dev_label, gpu_name = _device(inp)
    X, Y, task = _dataset(inp, work)
    n = len(X)
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    n_val = max(1, int(n * float(inp.get("val_fraction", 0.2))))
    va, tr = idx[:n_val], idx[n_val:]
    mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-8
    Xn = (X - mu) / sd
    hidden = [int(h) for h in inp.get("hidden", [32, 32])][:8]
    if any(not 1 <= h <= 4096 for h in hidden):
        raise ValueError("hidden layer sizes in [1, 4096], at most 8 layers")
    act = str(inp.get("activation", "tanh"))
    core = _mlp(X.shape[1], Y.shape[1], hidden, act).to(dev)
    epochs = int(inp.get("epochs", 2000))
    if not 1 <= epochs <= 100000:
        raise ValueError("epochs in [1, 1e5]")
    lr = float(inp.get("lr", 3e-3))
    batch = int(inp.get("batch", 0)) or len(tr)
    opt = torch.optim.Adam(core.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    lossf = torch.nn.BCEWithLogitsLoss() if task == "classification" else torch.nn.MSELoss()
    xt, yt = torch.from_numpy(Xn[tr]).to(dev), torch.from_numpy(Y[tr]).to(dev)
    t0 = time.time()
    for ep in range(epochs):
        perm = torch.randperm(len(tr), device=dev)
        for k in range(0, len(tr), batch):
            b = perm[k:k + batch]
            opt.zero_grad()
            loss = lossf(core(xt[b]), yt[b])
            loss.backward()
            opt.step()
        sched.step()
    train_s = time.time() - t0
    core = core.cpu()                       # export, checks and latency on the CPU, like a target

    class Wrapped(torch.nn.Module):            # normalisation (and sigmoid) inside the exported graph
        def __init__(self):
            super().__init__()
            self.core = core
            self.register_buffer("mu", torch.from_numpy(mu))
            self.register_buffer("sd", torch.from_numpy(sd))

        def forward(self, x):
            y = self.core((x - self.mu) / self.sd)
            return torch.sigmoid(y) if task == "classification" else y

    model = Wrapped().eval()
    with torch.no_grad():
        pv = model(torch.from_numpy(X[va])).numpy()
    vals: Dict[str, Any] = {"task_classification": 1.0 if task == "classification" else 0.0,
                            "samples": int(n), "train_s": round(train_s, 2), "seed": seed,
                            "device_used": dev_label}
    if gpu_name:
        vals["gpu"] = gpu_name
    if task == "classification":
        acc = float(((pv > 0.5) == (Y[va] > 0.5)).mean())
        vals["val_accuracy"] = round(acc, 5)
    else:
        err = np.abs(pv - Y[va])
        vals["val_rmse"] = round(float(np.sqrt((err ** 2).mean())), 7)
        vals["val_max_abs_err"] = round(float(err.max()), 7)
        rng_y = float(Y.max() - Y.min()) or 1.0
        vals["val_rmse_rel_range"] = round(vals["val_rmse"] / rng_y, 7)
    vals.update(_size_vals(core, inp))
    onnx_path = work / "model.onnx"
    _export(model, torch.from_numpy(X[:1]), onnx_path)
    sess = _ort(onnx_path)
    po = sess.run(None, {"x": X[va]})[0]
    vals["onnx_vs_torch_max_abs"] = round(float(np.abs(po - pv).max()), 9)
    vals["latency_us_pc"] = round(_latency_us(sess, X[:1]), 2)
    head = (f"nn_fit {task} {hidden} {act}: " + (f"val accuracy {vals.get('val_accuracy')}" if task ==
            "classification" else f"val RMSE {vals['val_rmse']}, max {vals['val_max_abs_err']}")
            + f", {vals['params']} params, ONNX diff {vals['onnx_vs_torch_max_abs']}")
    return result(vals, inp.get("limits"), head, files=[onnx_path.name],
                  notes=["held-out samples of the same distribution; normalisation is inside model.onnx; "
                         "latency is this PC's, not the target's"])


# ---------------------------------------------------------------- reinforcement learning
def _custom_env_factory(inp: Dict[str, Any], work: Path):
    """A Gymnasium env around a MuJoCo robot of the project (templates of _robots, or an MJCF)."""
    import gymnasium as gym
    import mujoco
    import numpy as np
    import _robots
    from mdo import _check_expr

    robot = inp.get("robot") or {}
    m0, meta = _robots.load(robot, work)
    names = list(_robots.sensors(m0, mujoco.MjData(m0)))
    obs_names = [str(n) for n in inp.get("observe") or names]
    acts = [str(n) for n in inp.get("act") or list(meta["actuators"])]
    act_ids = [mujoco.mj_name2id(m0, mujoco.mjtObj.mjOBJ_ACTUATOR, n) for n in acts]
    if any(i < 0 for i in act_ids):
        raise ValueError(f"act: actuators of the model are {list(meta['actuators'])}")
    known = tuple(n for n in names) + tuple(f"u{i}" for i in range(len(acts))) + ("t",)
    reward = str(inp.get("reward", "1"))
    terminate = str(inp.get("terminate", "-1"))
    _check_expr(reward, known)
    _check_expr(terminate, known)
    hz = float(inp.get("control_hz", 50.0))
    episode = float(inp.get("episode_s", 10.0))
    rnd = inp.get("randomize") or {}
    mass_pct = float(rnd.get("mass_pct", 0.0)) / 100.0
    noise = {str(k): float(v) for k, v in (rnd.get("sensor_noise") or {}).items()}
    init = {str(k): [float(a), float(b)] for k, (a, b) in (rnd.get("initial") or {}).items()}

    class MjEnv(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self):
            self.m, _ = _robots.load(robot, work)
            self.d = mujoco.MjData(self.m)
            self.sub = max(1, int(round(1.0 / (hz * self.m.opt.timestep))))
            self.max_k = int(episode * hz)
            self.mass0 = self.m.body_mass.copy()
            self.lo = np.array([meta["actuators"][n][0] for n in acts])
            self.hi = np.array([meta["actuators"][n][1] for n in acts])
            for n in obs_names:
                if n not in names:
                    raise ValueError(f"observe: {n!r} is not a sensor; one of {names}")
            self.observation_space = gym.spaces.Box(-np.inf, np.inf, (len(obs_names),), np.float32)
            self.action_space = gym.spaces.Box(-1.0, 1.0, (len(acts),), np.float32)

        def _obs(self, sd):
            v = [sd[n] if not isinstance(sd[n], tuple) else sd[n][0] for n in obs_names]
            v = [x + (self.np_random.normal(0, noise[n]) if n in noise else 0.0) for x, n in zip(v, obs_names)]
            return np.array(v, dtype=np.float32)

        def reset(self, seed=None, options=None):
            super().reset(seed=seed)
            mujoco.mj_resetData(self.m, self.d)
            self.m.body_mass[:] = self.mass0 * (1 + self.np_random.uniform(-mass_pct, mass_pct, self.mass0.shape))
            for jn, (a, b) in init.items():
                j = mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_JOINT, jn)
                if j < 0:
                    raise ValueError(f"randomize.initial: no joint {jn!r}")
                self.d.qpos[self.m.jnt_qposadr[j]] = self.np_random.uniform(a, b)
            mujoco.mj_forward(self.m, self.d)
            self.k = 0
            return self._obs(_robots.sensors(self.m, self.d)), {}

        def step(self, action):
            a = np.clip(np.asarray(action, dtype=float), -1, 1)
            u = self.lo + (a + 1) / 2 * (self.hi - self.lo)
            for i, ai in enumerate(act_ids):
                self.d.ctrl[ai] = u[i]
            for _ in range(self.sub):
                mujoco.mj_step(self.m, self.d)
            self.k += 1
            sd = _robots.sensors(self.m, self.d)
            env = {n: (v if not isinstance(v, tuple) else v[0]) for n, v in sd.items()}
            env.update({f"u{i}": float(x) for i, x in enumerate(u)}, t=float(self.d.time))
            r = float(_evaluate(reward, env))
            term = bool(float(_evaluate(terminate, env)) > 0)
            return self._obs(sd), r, term, self.k >= self.max_k, {}

    return MjEnv


def _rl_train(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import gymnasium as gym
    import numpy as np
    import torch
    import stable_baselines3 as sb3
    from stable_baselines3.common.env_util import make_vec_env

    env_id = str(inp.get("env", "CartPole-v1"))
    factory = None
    if env_id == "custom":
        factory = _custom_env_factory(inp, work)
    elif env_id not in ENVS:
        raise ValueError(f"env {env_id!r} not in {ENVS} or 'custom' (your robot)")
    algo = str(inp.get("algo", "PPO")).upper()
    if algo not in ALGOS:
        raise ValueError(f"algo in {ALGOS}")
    steps = int(inp.get("timesteps", 100000))
    if not 1000 <= steps <= 5_000_000:
        raise ValueError("timesteps in [1e3, 5e6]")
    seed = int(inp.get("seed", 0))
    torch.manual_seed(seed)
    dev, dev_label, gpu_name = _device(inp)
    hp = dict(inp.get("hyperparams") or {})
    allowed = {"learning_rate", "n_steps", "batch_size", "n_epochs", "gamma", "gae_lambda",
               "ent_coef", "clip_range", "buffer_size", "learning_starts", "tau", "train_freq",
               "gradient_steps", "vf_coef", "max_grad_norm"}
    bad = set(hp) - allowed
    if bad:
        raise ValueError(f"hyperparams not allowed: {sorted(bad)}")
    net = [int(h) for h in inp.get("net_arch", [64, 64])][:6]
    n_envs = int(inp.get("n_envs", 8 if algo in ("PPO", "A2C") else 1))
    venv = make_vec_env(factory or env_id, n_envs=max(1, min(n_envs, 16)), seed=seed)
    cls = getattr(sb3, algo)
    model = cls("MlpPolicy", venv, seed=seed, verbose=0, device=dev.type,
                policy_kwargs={"net_arch": net}, **hp)
    t0 = time.time()
    model.learn(total_timesteps=steps)
    train_s = time.time() - t0
    eval_env = factory() if factory else gym.make(env_id)
    n_ep = int(inp.get("eval_episodes", 20))
    rets: List[float] = []
    obs_bank: List[Any] = []
    for ep in range(max(1, min(n_ep, 200))):
        obs, _ = eval_env.reset(seed=10_000 + seed * 1000 + ep)
        done, ret = False, 0.0
        while not done:
            if len(obs_bank) < 500:
                obs_bank.append(np.array(obs, dtype=np.float32))
            a, _ = model.predict(obs, deterministic=True)
            obs, r, term, trunc, _ = eval_env.step(a)
            ret += float(r)
            done = term or trunc
        rets.append(ret)
    space = eval_env.action_space
    discrete = isinstance(space, gym.spaces.Discrete)

    class Policy(torch.nn.Module):
        def __init__(self, pol):
            super().__init__()
            self.pol = pol

        def forward(self, x):
            if algo in ("SAC", "TD3"):
                a = self.pol.actor(x, deterministic=True) if algo == "SAC" else self.pol.actor(x)
                low = torch.as_tensor(space.low, dtype=torch.float32)
                high = torch.as_tensor(space.high, dtype=torch.float32)
                return low + 0.5 * (a + 1.0) * (high - low)
            act = self.pol(x, deterministic=True)[0]
            if not discrete:
                low = torch.as_tensor(space.low, dtype=torch.float32)
                high = torch.as_tensor(space.high, dtype=torch.float32)
                act = torch.clamp(act, low, high)
            return act

    model.policy.to("cpu")                  # export and the agreement check on the CPU
    model.device = torch.device("cpu")
    pol = Policy(model.policy).eval()
    onnx_path = work / "policy.onnx"
    obs_arr = np.stack(obs_bank).astype(np.float32)
    with torch.no_grad():
        _export(pol, torch.from_numpy(obs_arr[:1]), onnx_path, out_names=("action",))
    sess = _ort(onnx_path)
    oa = sess.run(None, {"x": obs_arr})[0]
    pa = np.stack([model.predict(o, deterministic=True)[0] for o in obs_arr])
    if discrete:
        agree = float((oa.reshape(-1) == pa.reshape(-1)).mean())
    else:
        agree = float((np.abs(oa.reshape(pa.shape) - pa) <= 1e-4 * (1 + np.abs(pa))).all(axis=-1).mean()
                      if pa.ndim > 1 else (np.abs(oa.reshape(-1) - pa) <= 1e-4).mean())
    threshold = inp.get("success_return") if factory else getattr(eval_env.spec, "reward_threshold", None)
    mean = float(np.mean(rets))
    vals: Dict[str, Any] = {"mean_return": round(mean, 3), "std_return": round(float(np.std(rets)), 3),
                            "min_return": round(float(np.min(rets)), 3), "episodes": len(rets),
                            "train_s": round(train_s, 1), "timesteps": steps, "seed": seed,
                            "onnx_action_agreement": round(agree, 5), "device_used": dev_label,
                            "params": int(sum(p.numel() for p in model.policy.parameters())),
                            "latency_us_pc": round(_latency_us(sess, obs_arr[:1]), 2)}
    if gpu_name:
        vals["gpu"] = gpu_name
    if threshold is not None:
        vals["reward_threshold"] = float(threshold)
        vals["solved"] = 1.0 if mean >= float(threshold) else 0.0
    head = (f"{algo} on {env_id}, {steps} steps (seed {seed}): mean return {vals['mean_return']} "
            f"+/- {vals['std_return']} over {len(rets)} episodes, ONNX agreement {vals['onnx_action_agreement']}")
    return result(vals, inp.get("limits"), head, files=[onnx_path.name],
                  notes=["a policy from a simulator: the sim-to-real gap is untested until a bench "
                         "run; another seed gives another policy"])


# ---------------------------------------------------------------- ONNX inspection
def _onnx_check(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import numpy as np
    import onnx
    from onnx import numpy_helper
    p = _inside(work, inp.get("file", "model.onnx"), (".onnx",))
    if p.stat().st_size > 500 * 1024 * 1024:
        raise ValueError("ONNX file above 500 MB")
    m = onnx.load(str(p), load_external_data=False)
    for t in m.graph.initializer:
        if t.data_location == onnx.TensorProto.EXTERNAL:
            raise ValueError("external tensor data refused (it points at other files)")
    domains = {n.domain for n in m.graph.node if n.domain not in ("", "ai.onnx")}
    if domains:
        raise ValueError(f"custom operator domains refused: {sorted(domains)}")
    onnx.checker.check_model(m)
    ops = sorted({n.op_type for n in m.graph.node})
    init_names = {t.name for t in m.graph.initializer}
    params = int(sum(numpy_helper.to_array(t).size for t in m.graph.initializer))
    ins = [i for i in m.graph.input if i.name not in init_names]

    def shape(v):
        return [d.dim_value if d.dim_value > 0 else 1 for d in v.type.tensor_type.shape.dim]
    sess = _ort(p)
    feeds = {}
    if inp.get("test_input") is not None and len(ins) == 1:
        feeds[ins[0].name] = np.asarray(inp["test_input"], dtype=np.float32).reshape(shape(ins[0]))
    else:
        rng = np.random.default_rng(0)
        for i in ins:
            feeds[i.name] = rng.standard_normal(shape(i)).astype(np.float32)
    out = sess.run(None, feeds)
    lat = sess.run  # noqa: F841
    t0 = time.perf_counter()
    for _ in range(100):
        sess.run(None, feeds)
    vals: Dict[str, Any] = {
        "opset": int(max(o.version for o in m.opset_import)), "operators": len(ops), "params": params,
        "size_kB": round(p.stat().st_size / 1024, 3), "inputs": len(ins), "outputs": len(m.graph.output),
        "non_mcu_operators": len([o for o in ops if o not in MCU_OPS]),
        "latency_us_pc": round((time.perf_counter() - t0) / 100 * 1e6, 2),
        "output_0": [round(float(x), 7) for x in np.asarray(out[0]).reshape(-1)[:16]],
    }
    head = f"ONNX opset {vals['opset']}: {params} params, ops {', '.join(ops)}"
    notes = [f"not in the MCU-friendly set: {[o for o in ops if o not in MCU_OPS]}"] if vals["non_mcu_operators"] else []
    res = result({k: v for k, v in vals.items() if k != "output_0"}, inp.get("limits"), head,
                 notes=notes + ["checked: no external data, no custom domain, onnx.checker passed"])
    res["values"]["output_0"] = vals["output_0"]
    res["values"]["output_0_first"] = vals["output_0"][0] if vals["output_0"] else None
    return res


def _setup_tiny_onnx(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """y = relu(x W + b) with W 3x4 = 0.1*(i+j), b = [0, -1, 0.5, 2]: 16 parameters."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    W = np.array([[0.1 * (i + j) for j in range(4)] for i in range(3)], dtype=np.float32)
    b = np.array([0.0, -1.0, 0.5, 2.0], dtype=np.float32)
    g = helper.make_graph(
        [helper.make_node("Gemm", ["x", "W", "b"], ["h"]), helper.make_node("Relu", ["h"], ["y"])],
        "tiny", [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 4])],
        [numpy_helper.from_array(W, "W"), numpy_helper.from_array(b, "b")])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    m.ir_version = 8
    onnx.save(m, str(work / "tiny.onnx"))
    return dict(inp, file="tiny.onnx")


REFERENCES = [
    {"name": "fit_sine", "kind": "nn_fit",
     "inp": {"function": {"y": "sin(x)"}, "inputs": {"x": [-math.pi, math.pi]}, "samples": 2000,
             "hidden": [32, 32], "activation": "tanh", "epochs": 3000, "lr": 0.01, "seed": 0},
     "expect": {"val_max_abs_err": [0.0, 0.01], "onnx_vs_torch_max_abs": [0.0, 1e-5],
                "params": [1153, 0]}},
    {"name": "classify_circle", "kind": "nn_fit",
     "inp": {"function": {"inside": "0.5 - x**2 - y**2"}, "inputs": {"x": [-1, 1], "y": [-1, 1]},
             "task": "classification", "samples": 4000, "hidden": [32, 32], "activation": "relu",
             "epochs": 1500, "lr": 0.01, "seed": 0},
     "expect": {"val_accuracy": [1.0, 0.03], "onnx_vs_torch_max_abs": [0.0, 1e-5]}},
    {"name": "cartpole_ppo_solved", "kind": "rl_train",
     "inp": {"env": "CartPole-v1", "algo": "PPO", "timesteps": 100000, "seed": 0, "n_envs": 8,
             "hyperparams": {"n_steps": 32, "batch_size": 256, "gae_lambda": 0.8, "gamma": 0.98,
                             "n_epochs": 20, "ent_coef": 0.0, "learning_rate": 0.001, "clip_range": 0.2},
             "eval_episodes": 20},
     "expect": {"solved": [1.0, 0], "onnx_action_agreement": [1.0, 0]}},
    {"name": "custom_cart_pole_template_ppo", "kind": "rl_train",
     "inp": {"env": "custom", "robot": {"template": "cart_pole"},
             "observe": ["x", "x_rate", "pitch", "pitch_rate"], "reward": "1 - 0.02*x**2 - 0.01*x_rate**2",
             "terminate": "maximum(abs(pitch) - 0.2, abs(x) - 2.4)", "control_hz": 50,
             "episode_s": 10, "success_return": 475,
             "randomize": {"mass_pct": 10, "initial": {"pitch": [-0.05, 0.05], "x": [-0.05, 0.05]}},
             "algo": "PPO", "timesteps": 300000, "seed": 0, "n_envs": 8,
             "hyperparams": {"n_steps": 256, "batch_size": 256, "n_epochs": 10, "gamma": 0.99,
                             "gae_lambda": 0.95, "learning_rate": 0.0003, "ent_coef": 0.0},
             "eval_episodes": 10},
     "expect": {"solved": [1.0, 0], "onnx_action_agreement": [1.0, 0]}},
    {"name": "onnx_tiny_gemm_relu", "kind": "onnx_check", "setup": "_setup_tiny_onnx",
     "inp": {"test_input": [1.0, 2.0, 3.0]},
     # x W: column j = 0.1 * sum_i x_i (i + j) = 0.1 * (8 + 6 j) -> [0.8, 1.4, 2.0, 2.6] + b
     "expect": {"params": [16, 0], "operators": [2, 0], "non_mcu_operators": [0, 0],
                "output_0_first": [0.8, 1e-6]}},
]
