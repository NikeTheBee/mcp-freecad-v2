"""Robot and vehicle dynamics: MuJoCo, Pinocchio, python-control, ZMP, terramechanics (env `robot`).

Kinds:
- multibody         MuJoCo time simulation of an MJCF/URDF model (constant torques, PD to targets,
                    actuator ctrl): final state, peak joint torque/speed, contacts, energy drift,
                    optional offscreen video (gif/mp4, skipped with a note if headless GL fails)
- inverse_dynamics  joint torques along a trajectory (Pinocchio RNEA on a URDF), cross-checked
                    against MuJoCo mj_inverse on the same file; actuator sizing (peak/RMS torque,
                    peak power)
- control           python-control on a transfer function or state space: poles, step metrics,
                    gain/phase margins, LQR gains and closed-loop poles
- balance           centre of mass, support polygon, ZMP (cart-table model), capture point,
                    stability margins
- terramech         rigid wheel on deformable soil: Bekker pressure-sinkage, Janosi-Hanamoto
                    shear: sinkage, motion resistance, thrust, drawbar pull, max slope

Units: SI everywhere — m, kg, s, N, N m, rad (angles in params are rad unless the key ends in
_deg), rad/s, W, J, Pa. Soil: kc in N/m^(n+1), kphi in N/m^(n+2), c in Pa, K in m.

Models: params["model"] = a path RELATIVE to the study folder `work` (.xml MJCF or .urdf); it and
every file it references (meshes, textures, includes) must resolve inside `work`, else refused.
Or params["model_xml"] = the model text inline (+ "model_format": "mjcf" | "urdf"), written into
`work`. Or "builtin:pendulum" | "builtin:free_fall" | "builtin:two_link" (+ "model_params").
Joints are addressed by name; only 1-dof joints (hinge/slide, URDF revolute/continuous/prismatic)
take torques, targets or trajectories. inverse_dynamics: fixed-base URDF only.

Exactness: REFERENCES below (closed-form answers). Limits: rigid bodies; MuJoCo soft contacts
(results depend on solref/solimp and the timestep); no motor/gear model (torque at the joint);
terramech is the classical semi-empirical model (no bulldozing, no multi-pass, no lug effect).
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from _common import result

ENV = "robot"
KINDS = {
    "multibody": {"geometry": False, "timeout": 1800,
                  "doc": "MuJoCo simulation of an MJCF/URDF (torques, PD, ctrl): state, peak torque, contacts, energy"},
    "inverse_dynamics": {"geometry": False, "timeout": 900,
                         "doc": "joint torques of a trajectory (Pinocchio RNEA vs MuJoCo): peak/RMS torque, power"},
    "control": {"geometry": False, "timeout": 300,
                "doc": "transfer function / state space: step metrics, margins, LQR gains, poles"},
    "balance": {"geometry": False, "timeout": 300,
                "doc": "CoM, support polygon, ZMP (cart-table), capture point, stability margin"},
    "terramech": {"geometry": False, "timeout": 120,
                  "doc": "wheel on soil (Bekker/Janosi): sinkage, resistance, drawbar pull, max slope"},
}
MAX_STEPS = 2_000_000
MAX_FRAMES = 900


def versions() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for mod, key in (("mujoco", "mujoco"), ("pinocchio", "pinocchio"), ("control", "python-control"),
                     ("scipy", "scipy"), ("numpy", "numpy")):
        try:
            out[key] = __import__(mod).__version__
        except Exception as e:  # noqa: BLE001
            out[key] = f"missing ({type(e).__name__})"
    return out


# ------------------------------------------------------------------ model files (all inside work)
def _inside(p: Path, work: Path, what: str) -> Path:
    r, w = Path(p).resolve(), Path(work).resolve()
    if r != w and w not in r.parents:
        raise ValueError(f"{what} is outside the study folder: refused")
    return r


def _parse_xml(path: Path) -> ET.Element:
    text = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        raise ValueError(f"{path.name}: DOCTYPE/ENTITY declarations refused")
    return ET.fromstring(text)


def _check_assets(path: Path, work: Path, depth: int = 0) -> str:
    """Every file the model references must resolve inside work. Returns 'mjcf' or 'urdf'."""
    if depth > 8:
        raise ValueError("include nesting too deep")
    root = _parse_xml(path)
    base = path.parent
    if root.tag == "robot":                                     # URDF
        meshdir = ""
        for c in root.iter("compiler"):                         # <mujoco><compiler meshdir=..>
            meshdir = c.get("meshdir", meshdir)
        if meshdir:
            _inside(base / meshdir, work, "meshdir")
        for el in root.iter():
            fn = el.get("filename")
            if not fn:
                continue
            if fn.startswith("package://") or fn.startswith("file://"):
                fn = fn.split("://", 1)[1]
                fn = fn.split("/", 1)[1] if "/" in fn and not fn.startswith("/") else fn
            # MuJoCo strips the directory of URDF mesh paths (strippath): it reads the base name
            _inside(base / meshdir / Path(fn).name, work, f"mesh {Path(fn).name}")
            if not Path(fn).is_absolute():
                _inside(base / fn, work, f"mesh {fn}")
        return "urdf"
    if root.tag != "mujoco":
        raise ValueError(f"{path.name}: neither MJCF (<mujoco>) nor URDF (<robot>)")
    dirs = {"mesh": "", "texture": "", "asset": ""}
    for c in root.iter("compiler"):
        dirs["asset"] = c.get("assetdir", dirs["asset"])
        dirs["mesh"] = c.get("meshdir", dirs["asset"] or dirs["mesh"])
        dirs["texture"] = c.get("texturedir", dirs["asset"] or dirs["texture"])
    for d in dirs.values():
        if d:
            _inside(base / d, work, "asset directory")
    for el in root.iter():
        fn = el.get("file")
        if not fn:
            continue
        sub = (dirs["texture"] if el.tag == "texture" else
               "" if el.tag in ("include", "model") else dirs["mesh"])
        p = _inside(base / sub / fn, work, f"{el.tag} file {fn}")
        if el.tag in ("include", "model"):
            if not p.is_file():
                raise FileNotFoundError(f"included file missing: {fn}")
            _check_assets(p, work, depth + 1)
    return "mjcf"


def _model_path(inp: Dict[str, Any], work: Path) -> Tuple[Path, str]:
    """(model file inside work, 'mjcf'|'urdf')."""
    spec = inp.get("model")
    if inp.get("model_xml"):
        fmt = str(inp.get("model_format", "")).lower()
        text = str(inp["model_xml"])
        if fmt not in ("mjcf", "urdf"):
            fmt = "urdf" if re.search(r"<\s*robot[\s>]", text) else "mjcf"
        p = work / ("model.urdf" if fmt == "urdf" else "model.xml")
        p.write_text(text, encoding="utf-8")
    elif isinstance(spec, str) and spec.startswith("builtin:"):
        name = spec.split(":", 1)[1]
        if name not in _BUILTINS:
            raise ValueError(f"builtin model {name!r}: one of {sorted(_BUILTINS)}")
        text, ext = _BUILTINS[name](dict(inp.get("model_params") or {}))
        p = work / f"builtin_{name}{ext}"
        p.write_text(text, encoding="utf-8")
    elif isinstance(spec, str) and spec:
        if Path(spec).is_absolute():
            raise ValueError("model: give a path relative to the study folder, not an absolute one")
        p = _inside(work / spec, work, "model")
        if not p.is_file():
            raise FileNotFoundError(f"model file missing in the study folder: {spec}")
    else:
        raise ValueError("model: a file relative to the study folder, model_xml, or builtin:<name>")
    return p, _check_assets(p, work)


def _builtin_pendulum(mp: Dict[str, Any]) -> Tuple[str, str]:
    L, m, g = float(mp.get("L", 1.0)), float(mp.get("m", 1.0)), float(mp.get("g", 9.81))
    return (f"""<mujoco model="pendulum">
 <option gravity="0 0 {-g}" integrator="RK4" timestep="0.001"/>
 <worldbody>
  <light pos="0 -2 2" dir="0 1 -1"/>
  <body name="bob" pos="0 0 0">
   <joint name="hinge" type="hinge" axis="0 1 0" damping="0" armature="0"/>
   <inertial pos="0 0 {-L}" mass="{m}" diaginertia="1e-12 1e-12 1e-12"/>
   <geom type="capsule" fromto="0 0 0 0 0 {-L}" size="{0.01 * L}" contype="0" conaffinity="0" mass="0"/>
   <geom type="sphere" pos="0 0 {-L}" size="{0.05 * L}" contype="0" conaffinity="0" mass="0"/>
  </body>
 </worldbody>
</mujoco>""", ".xml")


def _builtin_free_fall(mp: Dict[str, Any]) -> Tuple[str, str]:
    z0, m, g = float(mp.get("z0", 10.0)), float(mp.get("m", 1.0)), float(mp.get("g", 9.81))
    return (f"""<mujoco model="free_fall">
 <option gravity="0 0 {-g}" integrator="RK4" timestep="0.001"/>
 <worldbody>
  <light pos="0 -2 {z0 + 2}" dir="0 1 -1"/>
  <body name="ball" pos="0 0 {z0}">
   <freejoint name="free"/>
   <geom type="sphere" size="0.05" mass="{m}"/>
  </body>
 </worldbody>
</mujoco>""", ".xml")


def _builtin_two_link(mp: Dict[str, Any]) -> Tuple[str, str]:
    """Planar 2-link arm, fixed base, axes -y (positive angle raises the arm), links along +x,
    COM on the link axis at c1, c2 (default mid-length), rod inertia m L^2/12."""
    L1, L2 = float(mp.get("L1", 0.5)), float(mp.get("L2", 0.4))
    m1, m2 = float(mp.get("m1", 2.0)), float(mp.get("m2", 1.5))
    c1, c2 = float(mp.get("c1", L1 / 2)), float(mp.get("c2", L2 / 2))

    def inertial(m, c, L):
        i = m * L * L / 12
        return (f'<inertial><origin xyz="{c} 0 0" rpy="0 0 0"/><mass value="{m}"/>'
                f'<inertia ixx="{1e-4 * m}" ixy="0" ixz="0" iyy="{i}" iyz="0" izz="{i}"/></inertial>'
                f'<collision><origin xyz="{L / 2} 0 0" rpy="0 1.5707963267948966 0"/>'
                f'<geometry><cylinder radius="0.02" length="{L}"/></geometry></collision>')
    return (f"""<robot name="two_link">
 <link name="base"><inertial><origin xyz="0 0 0"/><mass value="1"/>
  <inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/></inertial></link>
 <link name="link1">{inertial(m1, c1, L1)}</link>
 <link name="link2">{inertial(m2, c2, L2)}</link>
 <joint name="shoulder" type="revolute"><parent link="base"/><child link="link1"/>
  <origin xyz="0 0 0" rpy="0 0 0"/><axis xyz="0 -1 0"/>
  <limit lower="-3.1" upper="3.1" effort="1000" velocity="100"/></joint>
 <joint name="elbow" type="revolute"><parent link="link1"/><child link="link2"/>
  <origin xyz="{L1} 0 0" rpy="0 0 0"/><axis xyz="0 -1 0"/>
  <limit lower="-3.1" upper="3.1" effort="1000" velocity="100"/></joint>
</robot>""", ".urdf")


_BUILTINS = {"pendulum": _builtin_pendulum, "free_fall": _builtin_free_fall,
             "two_link": _builtin_two_link}


# ------------------------------------------------------------------ MuJoCo helpers
def _mj_load(path: Path):
    import mujoco
    try:
        return mujoco.MjModel.from_xml_path(str(path))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"MuJoCo could not load {path.name}: {str(e)[:300]}") from None


def _mj_joints(m) -> Dict[str, Dict[str, int]]:
    """1-dof joints by name: {qpos, dof, type}."""
    import mujoco
    out = {}
    for j in range(m.njnt):
        t = int(m.jnt_type[j])
        if t in (int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE)):
            name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j) or f"joint{j}"
            out[name] = {"qpos": int(m.jnt_qposadr[j]), "dof": int(m.jnt_dofadr[j]),
                         "slide": t == int(mujoco.mjtJoint.mjJNT_SLIDE)}
    return out


def _per_joint(spec, joints: Dict[str, Any], what: str) -> Dict[str, float]:
    if spec is None:
        return {}
    if isinstance(spec, (int, float)):
        return {j: float(spec) for j in joints}
    if not isinstance(spec, dict):
        raise ValueError(f"{what}: a number or {{joint: value}}")
    bad = [k for k in spec if k not in joints]
    if bad:
        raise ValueError(f"{what}: unknown 1-dof joint(s) {bad}; known: {sorted(joints)[:30]}")
    return {k: float(v) for k, v in spec.items()}


def _period(t: np.ndarray, q: np.ndarray, qd: np.ndarray) -> Tuple[Optional[float], int]:
    """Mean period between successive maxima of q (qd crossing + -> -, linear interpolation)."""
    idx = np.where((qd[:-1] > 0) & (qd[1:] <= 0))[0]
    times = [t[i] + (t[i + 1] - t[i]) * qd[i] / (qd[i] - qd[i + 1]) for i in idx]
    if len(times) < 2:
        return None, len(times)
    return float(np.mean(np.diff(times))), len(times)


def _video(m, frames: List[np.ndarray], work: Path, fmt: str, fps: float) -> str:
    if fmt == "gif":
        import imageio
        name = "multibody.gif"
        imageio.mimsave(str(work / name), frames, duration=1.0 / fps, loop=0)
        return name
    import cv2
    name = "multibody.mp4"
    h, w = frames[0].shape[:2]
    vw = cv2.VideoWriter(str(work / name), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not vw.isOpened():
        raise RuntimeError("OpenCV has no mp4 encoder here")
    for f in frames:
        vw.write(np.ascontiguousarray(f[:, :, ::-1]))
    vw.release()
    return name


def _multibody(inp: Dict[str, Any], work: Path):
    import mujoco
    path, fmt = _model_path(inp, work)
    m = _mj_load(path)
    if "timestep" in inp:
        m.opt.timestep = float(inp["timestep"])
    integ = inp.get("integrator")
    if integ:
        names = {"euler": "mjINT_EULER", "rk4": "mjINT_RK4", "implicit": "mjINT_IMPLICIT",
                 "implicitfast": "mjINT_IMPLICITFAST"}
        if str(integ).lower() not in names:
            raise ValueError(f"integrator: one of {sorted(names)}")
        m.opt.integrator = int(getattr(mujoco.mjtIntegrator, names[str(integ).lower()]))
    if "gravity" in inp:
        m.opt.gravity[:] = np.asarray(inp["gravity"], dtype=float)
    m.opt.enableflags |= int(mujoco.mjtEnableBit.mjENBL_ENERGY)
    dt = float(m.opt.timestep)
    duration = float(inp.get("duration", 1.0))
    n = int(round(duration / dt))
    if n < 1 or n > MAX_STEPS:
        raise ValueError(f"duration/timestep = {n} steps: 1..{MAX_STEPS}")
    joints = _mj_joints(m)
    d = mujoco.MjData(m)
    if inp.get("qpos0") is not None:
        q0 = np.asarray(inp["qpos0"], dtype=float)
        if q0.shape != (m.nq,):
            raise ValueError(f"qpos0: {m.nq} values expected")
        d.qpos[:] = q0
    for k, v in _per_joint(inp.get("q0"), joints, "q0").items():
        d.qpos[joints[k]["qpos"]] = v
    for k, v in _per_joint(inp.get("qd0"), joints, "qd0").items():
        d.qvel[joints[k]["dof"]] = v
    tau_const = _per_joint(inp.get("torques"), joints, "torques")
    pd = inp.get("pd") or {}
    targets = _per_joint(pd.get("targets"), joints, "pd.targets")
    kp = _per_joint(pd.get("kp", 0.0), joints, "pd.kp") if targets else {}
    kd = _per_joint(pd.get("kd", 0.0), joints, "pd.kd") if targets else {}
    tmax = pd.get("max_torque")
    ctrl = inp.get("ctrl") or {}
    for k, v in ctrl.items():
        a = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, k)
        if a < 0:
            raise ValueError(f"ctrl: unknown actuator {k!r}")
        d.ctrl[a] = float(v)
    track = inp.get("track_joint") or (next(iter(joints)) if joints else None)
    if track is not None and track not in joints:
        raise ValueError(f"track_joint {track!r} is not a 1-dof joint")
    body = inp.get("track_body")
    bid = -1
    if body:
        bid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, body)
        if bid < 0:
            raise ValueError(f"track_body: unknown body {body!r}")
    mujoco.mj_forward(m, d)
    e0 = float(d.energy[0] + d.energy[1])
    jn = list(joints)
    dofs = np.array([joints[j]["dof"] for j in jn], dtype=int)
    qadr = np.array([joints[j]["qpos"] for j in jn], dtype=int)
    tau_max = np.zeros(len(jn))
    vel_max = np.zeros(len(jn))
    f6 = np.zeros(6)
    ncon_max, fn_max, fn_tot_max = 0, 0.0, 0.0
    ke_max = float(d.energy[1])
    tq, tqd, tt = [], [], []
    video = bool(inp.get("video"))
    frames: List[np.ndarray] = []
    notes: List[str] = []
    renderer = cam = None
    fps = float(inp.get("fps", 30))
    if video:
        try:
            w_px, h_px = int(inp.get("width", 480)), int(inp.get("height", 360))
            m.vis.global_.offwidth = max(int(m.vis.global_.offwidth), w_px)
            m.vis.global_.offheight = max(int(m.vis.global_.offheight), h_px)
            renderer = mujoco.Renderer(m, h_px, w_px)
            cam = mujoco.MjvCamera()
            mujoco.mjv_defaultFreeCamera(m, cam)
        except Exception as e:  # noqa: BLE001 — no headless GL: say so, keep the numbers
            renderer = None
            notes.append(f"video skipped: offscreen rendering unavailable ({type(e).__name__}: {str(e)[:120]})")
    frame_every = max(1, int(round(1.0 / (fps * dt))))
    diverged = False
    for i in range(n):
        d.qfrc_applied[:] = 0.0
        for k, v in tau_const.items():
            d.qfrc_applied[joints[k]["dof"]] += v
        for k, qt in targets.items():
            jd = joints[k]
            u = kp.get(k, 0.0) * (qt - d.qpos[jd["qpos"]]) - kd.get(k, 0.0) * d.qvel[jd["dof"]]
            if tmax is not None:
                u = float(np.clip(u, -float(tmax), float(tmax)))
            d.qfrc_applied[jd["dof"]] += u
        if track is not None:
            tt.append(d.time)
            tq.append(d.qpos[joints[track]["qpos"]])
            tqd.append(d.qvel[joints[track]["dof"]])
        mujoco.mj_step(m, d)
        if len(jn):
            tau = np.abs(d.qfrc_applied[dofs] + d.qfrc_actuator[dofs])
            np.maximum(tau_max, tau, out=tau_max)
            np.maximum(vel_max, np.abs(d.qvel[dofs]), out=vel_max)
        ke_max = max(ke_max, float(d.energy[1]))
        if d.ncon:
            ncon_max = max(ncon_max, int(d.ncon))
            tot = 0.0
            for c in range(d.ncon):
                mujoco.mj_contactForce(m, d, c, f6)
                fn_max = max(fn_max, abs(f6[0]))
                tot += abs(f6[0])
            fn_tot_max = max(fn_tot_max, tot)
        if renderer is not None and i % frame_every == 0 and len(frames) < MAX_FRAMES:
            renderer.update_scene(d, camera=cam)
            frames.append(renderer.render().copy())
        if not np.isfinite(d.qpos).all() or not np.isfinite(d.qvel).all():
            diverged = True
            break
    if diverged or d.warning[int(mujoco.mjtWarning.mjWARN_BADQACC)].number > 0:
        raise RuntimeError(f"simulation diverged at t = {d.time:.4g} s: reduce the timestep or "
                           f"check the model (masses, inertias, limits)")
    mujoco.mj_forward(m, d)
    e1 = float(d.energy[0] + d.energy[1])
    values: Dict[str, Any] = {
        "model_format": fmt, "nq": int(m.nq), "nv": int(m.nv), "steps": i + 1, "timestep_s": dt,
        "t_end_s": float(d.time),
        "final_q": {j: float(d.qpos[joints[j]["qpos"]]) for j in jn[:60]},
        "final_qd": {j: float(d.qvel[joints[j]["dof"]]) for j in jn[:60]},
        "energy_drift_J": e1 - e0,
        "energy_drift_rel": (e1 - e0) / max(ke_max, 1e-12),
        "contacts_max": ncon_max, "contact_normal_max_N": fn_max,
        "contact_total_normal_max_N": fn_tot_max, "contacts_final": int(d.ncon),
    }
    if len(jn):
        k = int(np.argmax(tau_max))
        values.update(max_joint_torque_Nm=float(tau_max[k]), max_torque_joint=jn[k],
                      max_joint_speed=float(vel_max.max()), max_speed_joint=jn[int(np.argmax(vel_max))],
                      peak_torque_Nm={j: float(v) for j, v in zip(jn[:60], tau_max)})
    if track is not None:
        values["track_joint"] = track
        values["final_track_q"] = float(d.qpos[joints[track]["qpos"]])
        per, nmax = _period(np.array(tt), np.array(tq), np.array(tqd))
        if per is not None:
            values["period_s"] = per
            values["amplitude"] = float(np.max(np.abs(np.array(tq) - np.mean(tq))))
    if bid >= 0:
        x = d.xpos[bid]
        values.update(body_x_m=float(x[0]), body_y_m=float(x[1]), body_z_m=float(x[2]))
        v6 = np.zeros(6)
        mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, bid, v6, 0)
        values.update(body_vz_m_s=float(v6[5]))
    files = [path.name]
    if renderer is not None and frames:
        try:
            files.append(_video(m, frames, work, str(inp.get("video_format", "gif")).lower(),
                                1.0 / (frame_every * dt)))
        except Exception as e:  # noqa: BLE001
            notes.append(f"video not written ({type(e).__name__}: {str(e)[:120]})")
    forced = bool(tau_const or targets or ctrl or m.nu)
    if forced or float(np.abs(m.dof_damping).sum()) or ncon_max:
        notes.append("energy drift includes work of torques/damping/contacts: a conservation check "
                     "only for a passive frictionless model")
    head = (f"multibody ({fmt}, {m.nv} dof, {duration:g} s at {dt:g} s): "
            + (f"peak torque {values['max_joint_torque_Nm']:.4g} N m at {values['max_torque_joint']}, "
               if len(jn) else "")
            + (f"period {values['period_s']:.6g} s, " if "period_s" in values and not forced else "")
            + f"energy drift {values['energy_drift_rel']:.2e} (rel), contacts max {ncon_max}"
            + (f", contact normal max {fn_max:.4g} N" if ncon_max else ""))
    return values, head, files, notes


# ------------------------------------------------------------------ inverse dynamics
def _trajectory(inp: Dict[str, Any], names: List[str]):
    """(t, q, qd, qdd) arrays (samples x joints) in the order of `names`."""
    tr = inp.get("trajectory")
    if tr is None:
        raise ValueError("trajectory: {q: [...]} (static), {q: [[...]...], dt}, or {type: sine, ...}")
    nj = len(names)
    order = tr.get("joints") or names
    if sorted(order) != sorted(names):
        raise ValueError(f"trajectory.joints must list every 1-dof joint: {names}")
    perm = [order.index(n) for n in names]

    def arr(key):
        a = np.atleast_2d(np.asarray(tr[key], dtype=float))
        if a.shape[1] != nj:
            raise ValueError(f"trajectory.{key}: {nj} values per sample ({order})")
        return a[:, perm]
    if tr.get("type") == "sine":
        dt, T = float(tr.get("dt", 0.01)), float(tr.get("duration", 2.0))
        t = np.arange(0.0, T + 0.5 * dt, dt)
        A = np.asarray(tr.get("amplitude", [0.5] * nj), dtype=float)[perm]
        off = np.asarray(tr.get("offset", [0.0] * nj), dtype=float)[perm]
        ph = np.asarray(tr.get("phase", [0.0] * nj), dtype=float)[perm]
        f = np.asarray(tr.get("freq_hz", [0.5] * nj), dtype=float)
        f = (np.full(nj, float(f)) if f.ndim == 0 else f[perm])
        w = 2 * np.pi * f
        arg = np.outer(t, w) + ph
        return t, off + A * np.sin(arg), A * w * np.cos(arg), -A * w * w * np.sin(arg)
    q = arr("q")
    if len(q) == 1:
        z = np.zeros_like(q)
        qd = arr("qd") if "qd" in tr else z
        qdd = arr("qdd") if "qdd" in tr else z
        return np.zeros(1), q, qd, qdd
    dt = float(tr.get("dt", 0))
    if dt <= 0:
        raise ValueError("trajectory.dt (s) is needed for a sampled q")
    t = np.arange(len(q)) * dt
    qd = arr("qd") if "qd" in tr else np.gradient(q, dt, axis=0, edge_order=2)
    qdd = arr("qdd") if "qdd" in tr else np.gradient(qd, dt, axis=0, edge_order=2)
    return t, q, qd, qdd


def _inverse_dynamics(inp: Dict[str, Any], work: Path):
    import mujoco
    path, fmt = _model_path(inp, work)
    g = float(inp.get("g", 9.81))
    notes: List[str] = []
    pin_names: List[str] = []
    tau_pin = None
    if fmt == "urdf":
        import pinocchio as pin
        model = pin.buildModelFromUrdf(str(path))
        model.gravity.linear = np.array([0.0, 0.0, -g])
        data = model.createData()
        jinfo = []
        for j in range(1, model.njoints):
            jm = model.joints[j]
            if jm.nv != 1:
                raise ValueError(f"joint {model.names[j]}: only 1-dof joints (fixed base)")
            jinfo.append((model.names[j], jm.idx_q, jm.nq, jm.idx_v))
        pin_names = [x[0] for x in jinfo]
        names = pin_names
    else:
        notes.append("MJCF: no Pinocchio cross-check (URDF only), MuJoCo mj_inverse alone")
    # MuJoCo on the same file
    m = d = None
    try:
        m = _mj_load(path)
        mj_j = _mj_joints(m)
        if fmt != "urdf":
            names = list(mj_j)
        if m.nv != len(names) or any(n not in mj_j for n in names):
            raise ValueError(f"MuJoCo sees joints {list(mj_j)} (nv {m.nv}), Pinocchio {names}")
        m.opt.gravity[:] = [0.0, 0.0, -g]
        m.dof_damping[:] = 0.0
        m.dof_armature[:] = 0.0
        m.dof_frictionloss[:] = 0.0
        m.opt.disableflags |= int(mujoco.mjtDisableBit.mjDSBL_CONSTRAINT)
        d = mujoco.MjData(m)
    except ValueError as e:
        if fmt != "urdf":
            raise
        notes.append(f"no MuJoCo cross-check: {str(e)[:200]}")
        m = None
    if not names:
        raise ValueError("the model has no 1-dof joint")
    t, q, qd, qdd = _trajectory(inp, names)
    ns, nj = q.shape
    tau = np.zeros((ns, nj))
    tau_mj = np.zeros((ns, nj)) if m is not None else None
    if fmt == "urdf":
        import pinocchio as pin
        for k in range(ns):
            qq = pin.neutral(model)
            v, a = np.zeros(model.nv), np.zeros(model.nv)
            for i, (_, iq, nq, iv) in enumerate(jinfo):
                if nq == 2:                                       # continuous: (cos, sin)
                    qq[iq], qq[iq + 1] = math.cos(q[k, i]), math.sin(q[k, i])
                else:
                    qq[iq] = q[k, i]
                v[iv], a[iv] = qd[k, i], qdd[k, i]
            tau[k] = pin.rnea(model, data, qq, v, a)[[x[3] for x in jinfo]]
        tau_pin = tau
    if m is not None:
        qa = [mj_j[n]["qpos"] for n in names]
        da = [mj_j[n]["dof"] for n in names]
        for k in range(ns):
            d.qpos[qa], d.qvel[da], d.qacc[da] = q[k], qd[k], qdd[k]
            mujoco.mj_inverse(m, d)
            tau_mj[k] = d.qfrc_inverse[da]
        if tau_pin is None:
            tau = tau_mj
    values: Dict[str, Any] = {"model_format": fmt, "samples": ns, "joints": names,
                              "engine": "pinocchio" if tau_pin is not None else "mujoco"}
    peak = np.abs(tau).max(axis=0)
    rms = np.sqrt((tau ** 2).mean(axis=0))
    power = tau * qd
    for i, n in enumerate(names[:40]):
        values[f"peak_tau_{n}_Nm"] = float(peak[i])
        values[f"rms_tau_{n}_Nm"] = float(rms[i])
        values[f"peak_power_{n}_W"] = float(np.abs(power[:, i]).max())
        if ns == 1:
            values[f"tau_{n}_Nm"] = float(tau[0, i])
    values["peak_tau_Nm"] = float(peak.max())
    values["peak_total_power_W"] = float(np.abs(power).sum(axis=1).max())
    values["peak_speed"] = float(np.abs(qd).max())
    if tau_pin is not None and tau_mj is not None:
        diff = float(np.abs(tau_pin - tau_mj).max())
        values["max_abs_diff_Nm"] = diff
        values["max_rel_diff"] = diff / max(float(np.abs(tau_pin).max()), 1e-12)
        notes.append(f"Pinocchio RNEA vs MuJoCo mj_inverse: max gap {diff:.2e} N m "
                     "(MuJoCo damping/armature/friction/limits zeroed for the comparison)")
    k = int(np.argmax(peak))
    head = (f"inverse dynamics ({values['engine']}, {ns} samples): peak {peak[k]:.4g} N m at "
            f"{names[k]}, RMS " + ", ".join(f"{n} {r:.3g}" for n, r in zip(names[:8], rms))
            + f" N m; peak power {values['peak_total_power_W']:.4g} W")
    notes.append("torque at the joint, rigid links, no friction/gearbox/motor losses: add margins "
                 "from the actuator datasheet (continuous torque vs RMS, peak vs peak)")
    return values, head, [path.name], notes


# ------------------------------------------------------------------ control
def _system(inp: Dict[str, Any]):
    import control as ct
    dt = inp.get("dt")
    dt = float(dt) if dt else 0
    if inp.get("tf"):
        tf = inp["tf"]
        return ct.tf(np.asarray(tf["num"], float), np.asarray(tf["den"], float), dt), False
    if inp.get("ss"):
        s = inp["ss"]
        A = np.atleast_2d(np.asarray(s["A"], float))
        B = np.asarray(s["B"], float).reshape(A.shape[0], -1)
        C = np.asarray(s.get("C", np.eye(A.shape[0])), float).reshape(-1, A.shape[0])
        D = np.asarray(s.get("D", np.zeros((C.shape[0], B.shape[1]))), float).reshape(C.shape[0], B.shape[1])
        return ct.ss(A, B, C, D, dt), True
    raise ValueError("control: give tf {num, den} or ss {A, B, C, D} (+ dt for discrete)")


def _to_ss(sys):
    """State space via scipy's tf2ss (deterministic; no dependence on the optional slycot)."""
    import control as ct
    from scipy.signal import tf2ss
    if isinstance(sys, ct.StateSpace):
        return sys
    if sys.ninputs != 1 or sys.noutputs != 1:
        raise ValueError("MIMO transfer functions: give a state space (ss) instead")
    A, B, C, D = tf2ss(np.asarray(sys.num[0][0], float), np.asarray(sys.den[0][0], float))
    return ct.ss(A, B, C, D, sys.dt)


def _step_metrics(t: np.ndarray, y: np.ndarray, yf: float) -> Dict[str, float]:
    out: Dict[str, float] = {"steady_state": yf}
    if yf == 0 or not np.isfinite(yf):
        return out
    s = y / yf

    def cross(level):
        i = int(np.argmax(s >= level))
        if s[i] < level or i == 0:
            return float("nan")
        return float(t[i - 1] + (t[i] - t[i - 1]) * (level - s[i - 1]) / (s[i] - s[i - 1]))
    out["rise_time_s"] = cross(0.9) - cross(0.1)
    k = int(np.argmax(s))
    pk, tp = s[k], t[k]
    if 0 < k < len(s) - 1:                                       # parabolic peak refinement
        a, b, c = s[k - 1], s[k], s[k + 1]
        den = a - 2 * b + c
        if den < 0:
            x = 0.5 * (a - c) / den
            pk, tp = b - 0.25 * (a - c) * x, t[k] + x * (t[k + 1] - t[k])
    out["overshoot_pct"] = max(0.0, float(pk - 1.0) * 100)
    out["peak"] = float(pk * yf)
    out["peak_time_s"] = float(tp)
    outside = np.where(np.abs(s - 1) > 0.02)[0]
    if len(outside) and outside[-1] < len(s) - 1:
        i = outside[-1]
        e0, e1 = abs(s[i] - 1) - 0.02, abs(s[i + 1] - 1) - 0.02
        out["settling_time_s"] = float(t[i] + (t[i + 1] - t[i]) * e0 / (e0 - e1))
    elif not len(outside):
        out["settling_time_s"] = 0.0
    return out


def _control(inp: Dict[str, Any], work: Path):
    import control as ct
    G, is_ss = _system(inp)
    notes: List[str] = []
    values: Dict[str, Any] = {}
    pid = inp.get("pid")
    L = G
    if pid:
        kp, ki, kd = float(pid.get("kp", 0)), float(pid.get("ki", 0)), float(pid.get("kd", 0))
        N = float(pid.get("N", 0))                   # derivative filter kd s / (1 + s kd/(N kp))
        s = ct.tf("s") if not G.dt else None
        if s is None:
            raise ValueError("pid: continuous systems only")
        Cd = kd * s if not N else kd * s / (1 + s * kd / (N * max(kp, 1e-12)))
        C = kp + (ki / s if ki else 0) + Cd
        L = ct.series(ct.tf(C), ct.tf(G))
    loop = str(inp.get("loop", "closed" if pid else "open"))
    sys_step = ct.feedback(L, 1) if loop == "closed" else L
    poles = np.asarray(ct.poles(sys_step))
    stable = bool(np.all(np.abs(poles) < 1) if G.dt else np.all(poles.real < 0))
    marginal = (not stable) and bool(np.all(np.abs(poles) <= 1 + 1e-12) if G.dt
                                     else np.all(poles.real <= 1e-12))
    values["poles"] = [[float(p.real), float(p.imag)] for p in poles[:20]]
    values["stable"] = int(stable)
    values["max_pole_real" if not G.dt else "max_pole_abs"] = (
        float(poles.real.max()) if not G.dt else float(np.abs(poles).max())) if len(poles) else 0.0
    siso = sys_step.ninputs == 1 and sys_step.noutputs == 1
    if siso and stable:
        if G.dt:
            r = ct.step_response(_to_ss(sys_step))
            t, y = np.asarray(r.time), np.squeeze(np.asarray(r.outputs))
        else:
            ss_step = _to_ss(sys_step)
            tf_end = float(np.asarray(ct.step_response(ss_step).time)[-1])
            t = np.linspace(0.0, tf_end, 40001)
            y = np.squeeze(np.asarray(ct.step_response(ss_step, T=t).outputs))
        yf = float(np.squeeze(ct.dcgain(sys_step)))
        values.update(_step_metrics(t, y, yf))
    elif siso:
        notes.append("not asymptotically stable: no step metrics")
    if L.ninputs == 1 and L.noutputs == 1:
        gm, pm, _sm, wpc, wgc, _wms = ct.stability_margins(L)
        values["gain_margin"] = float(gm) if np.isfinite(gm) else None
        values["gain_margin_dB"] = float(20 * np.log10(gm)) if np.isfinite(gm) and gm > 0 else None
        values["phase_margin_deg"] = float(pm) if np.isfinite(pm) else None
        values["w_phase_crossover_rad_s"] = float(wpc) if np.isfinite(wpc) else None
        values["w_gain_crossover_rad_s"] = float(wgc) if np.isfinite(wgc) else None
        if not np.isfinite(gm):
            notes.append("gain margin infinite (phase never reaches -180 deg)")
        notes.append("margins of the open loop " + ("C*G" if pid else "G") + " under unity feedback")
    lq = inp.get("lqr")
    if lq:
        if not is_ss:
            notes.append("LQR on a tf: states are python-control's realisation, not physical ones")
        S = _to_ss(G)
        Q = np.atleast_2d(np.asarray(lq["Q"], float))
        R = np.atleast_2d(np.asarray(lq["R"], float))
        K, _P, E = (ct.dlqr(S, Q, R, method="scipy") if G.dt else ct.lqr(S, Q, R, method="scipy"))
        K = np.atleast_2d(np.asarray(K))
        values["lqr_K"] = K.tolist()
        for i in range(K.shape[0]):
            for j in range(K.shape[1]):
                values[f"lqr_K_{i}_{j}"] = float(K[i, j])
        E = np.asarray(E)
        values["lqr_cl_poles"] = [[float(p.real), float(p.imag)] for p in E]
        values["lqr_cl_max_pole_real"] = float(E.real.max()) if not G.dt else float(np.abs(E).max())
    state = "stable" if stable else "marginally stable (pole on the stability boundary)" if marginal else "UNSTABLE"
    parts = [f"{'closed' if loop == 'closed' else 'open'} loop {state}"]
    if "overshoot_pct" in values:
        parts.append(f"step: rise {values['rise_time_s']:.4g} s, overshoot {values['overshoot_pct']:.4g} %"
                     + (f", settling 2% {values['settling_time_s']:.4g} s" if "settling_time_s" in values else ""))
    if values.get("phase_margin_deg") is not None or values.get("gain_margin") is not None:
        gm = values.get("gain_margin_dB")
        pm = values.get("phase_margin_deg")
        parts.append(f"margins: GM {'inf' if gm is None else f'{gm:.3g} dB'}, "
                     f"PM {'inf' if pm is None else f'{pm:.3g} deg'}")
    if "lqr_K" in values:
        parts.append("LQR K = " + str(np.round(np.asarray(values["lqr_K"]), 5).tolist()))
    return values, "control: " + "; ".join(parts), [], notes


# ------------------------------------------------------------------ balance (ZMP)
def _polygon(pts: np.ndarray) -> np.ndarray:
    """Convex hull vertices (counter-clockwise), or the 1-2 points of a degenerate support."""
    pts = np.unique(np.round(pts, 12), axis=0)
    if len(pts) < 3:
        return pts
    from scipy.spatial import ConvexHull, QhullError
    try:
        return pts[ConvexHull(pts).vertices]
    except QhullError:                                           # collinear points: a segment
        d = pts - pts[0]
        u = d[np.argmax(np.linalg.norm(d, axis=1))]
        s = d @ u
        return pts[[int(np.argmin(s)), int(np.argmax(s))]]


def _seg_dist(p, a, b) -> float:
    ab = b - a
    tt = 0.0 if not ab.any() else float(np.clip((p - a) @ ab / (ab @ ab), 0, 1))
    return float(np.linalg.norm(p - (a + tt * ab)))


def _margin(p: np.ndarray, poly: np.ndarray) -> float:
    """Signed distance of p to the support polygon boundary (> 0 inside)."""
    if len(poly) == 1:
        return -float(np.linalg.norm(p - poly[0]))
    if len(poly) == 2:
        return -_seg_dist(p, poly[0], poly[1])
    dist = min(_seg_dist(p, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))
    def cross(a, b):
        return float(a[0] * b[1] - a[1] * b[0])
    inside = all(cross(poly[(i + 1) % len(poly)] - poly[i], p - poly[i]) >= 0
                 for i in range(len(poly)))
    return dist if inside else -dist


def _balance(inp: Dict[str, Any], work: Path):
    g = float(inp.get("g", 9.81))
    notes: List[str] = []
    files: List[str] = []
    mass = inp.get("mass")
    contacts = inp.get("contacts")
    if inp.get("model") or inp.get("model_xml"):
        import mujoco
        path, fmt = _model_path(inp, work)
        m = _mj_load(path)
        d = mujoco.MjData(m)
        joints = _mj_joints(m)
        if inp.get("qpos") is not None:
            q = np.asarray(inp["qpos"], float)
            if q.shape != (m.nq,):
                raise ValueError(f"qpos: {m.nq} values expected")
            d.qpos[:] = q
        for k, v in _per_joint(inp.get("q"), joints, "q").items():
            d.qpos[joints[k]["qpos"]] = v
        mujoco.mj_forward(m, d)
        com = np.array(d.subtree_com[0], float)
        mass = float(np.sum(m.body_mass[1:]))
        if m.nbody < 2 or any(m.body_parentid[b] == 0 and m.body_jntnum[b] == 0
                              for b in range(1, m.nbody)) or float(m.body_mass[0]) > 0:
            notes.append("bodies welded to the world (fixed base) are not in the CoM/mass")
        files.append(path.name)
        if contacts is None:
            contacts = [list(d.contact[i].pos) for i in range(d.ncon)]
            notes.append(f"support from the model's {d.ncon} contacts at this state")
    else:
        if inp.get("com") is None:
            raise ValueError("balance: give com [x, y, z] (m) or a model + q")
        com = np.asarray(inp["com"], float)
    acc = np.asarray(inp.get("com_acc", [0.0, 0.0, 0.0]), float)
    zg = float(inp.get("ground_z", 0.0))
    h = float(com[2] - zg)
    values: Dict[str, Any] = {"com_x_m": float(com[0]), "com_y_m": float(com[1]),
                              "com_z_m": float(com[2])}
    if mass is not None:
        values["mass_kg"] = float(mass)
    if g + acc[2] <= 0:
        raise ValueError("com_acc z <= -g: the body is in free fall, no ZMP")
    zmp = com[:2] - h * acc[:2] / (g + acc[2])                   # cart-table model (Kajita)
    values.update(zmp_x_m=float(zmp[0]), zmp_y_m=float(zmp[1]))
    if inp.get("com_vel") is not None:
        v = np.asarray(inp["com_vel"], float)
        omega = math.sqrt(g / h)                                 # linear inverted pendulum
        cp = com[:2] + v[:2] / omega
        values.update(capture_x_m=float(cp[0]), capture_y_m=float(cp[1]))
    if not contacts:
        notes.append("no contact point: no support polygon, no margin")
        head = f"balance: CoM ({com[0]:.4g}, {com[1]:.4g}, {com[2]:.4g}) m, ZMP ({zmp[0]:.4g}, {zmp[1]:.4g}) m"
        return values, head, files, notes
    pts = np.asarray(contacts, float)[:, :2]
    poly = _polygon(pts)
    values["support_vertices"] = poly.tolist()
    if len(poly) >= 3:
        x, y = poly[:, 0], poly[:, 1]
        values["support_area_m2"] = float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    else:
        values["support_area_m2"] = 0.0
        notes.append("support is a point or a segment: no static margin (line/point contact)")
    values["zmp_margin_m"] = _margin(zmp, poly)
    values["com_margin_m"] = _margin(com[:2], poly)
    if "capture_x_m" in values:
        values["capture_margin_m"] = _margin(np.array([values["capture_x_m"], values["capture_y_m"]]), poly)
    inside = values["zmp_margin_m"] > 0
    head = (f"balance: CoM ({com[0]:.4g}, {com[1]:.4g}, {com[2]:.4g}) m, ZMP ({zmp[0]:.4g}, {zmp[1]:.4g}) m "
            f"{'inside' if inside else 'OUTSIDE'} the support ({len(poly)} vertices), "
            f"ZMP margin {values['zmp_margin_m'] * 1000:.1f} mm, static CoM margin "
            f"{values['com_margin_m'] * 1000:.1f} mm")
    notes.append("cart-table ZMP: flat ground, rigid feet, angular momentum about the CoM neglected")
    return values, head, files, notes


# ------------------------------------------------------------------ terramechanics
# Wong, J.Y., Theory of Ground Vehicles (Wiley, 3rd ed. 2001 / 4th ed. 2008), Table 2.3
# "Values of terrain values" (sources quoted there: Land Locomotion Laboratory, WES, Thailand),
# and LETE sand (Wong). kc kN/m^(n+1), kphi kN/m^(n+2), c kPa, phi deg. Converted to SI below.
# The table gives no shear deformation modulus K: the caller supplies it with its source.
SOILS = {
    "dry_sand_lll": dict(n=1.1, kc=0.99, kphi=1528.43, c=1.04, phi_deg=28.0),
    "sandy_loam_lll_15pct": dict(n=0.7, kc=5.27, kphi=1515.04, c=1.72, phi_deg=29.0),
    "clayey_soil_thailand_38pct": dict(n=0.5, kc=13.19, kphi=692.15, c=4.14, phi_deg=13.0),
    "heavy_clay_wes_25pct": dict(n=0.13, kc=12.70, kphi=1555.95, c=68.95, phi_deg=34.0),
    "lete_sand": dict(n=0.79, kc=102.0, kphi=5301.0, c=1.3, phi_deg=31.1),
}
_SOIL_SRC = "Wong, Theory of Ground Vehicles, Table 2.3 (transcribed: check against your edition)"


def _soil(spec) -> Dict[str, Any]:
    if isinstance(spec, str):
        if spec not in SOILS:
            raise ValueError(f"soil {spec!r}: one of {sorted(SOILS)} or a dict with its source")
        s = SOILS[spec]
        return dict(n=s["n"], kc=s["kc"] * 1e3, kphi=s["kphi"] * 1e3, c=s["c"] * 1e3,
                    phi=math.radians(s["phi_deg"]), source=_SOIL_SRC, name=spec)
    if isinstance(spec, dict):
        miss = [k for k in ("n", "kc", "kphi", "c", "phi_deg") if k not in spec]
        if miss:
            raise ValueError(f"soil dict needs {miss} (SI: kc N/m^(n+1), kphi N/m^(n+2), c Pa)")
        return dict(n=float(spec["n"]), kc=float(spec["kc"]), kphi=float(spec["kphi"]),
                    c=float(spec["c"]), phi=math.radians(float(spec["phi_deg"])),
                    source=str(spec.get("source") or "UNSOURCED"), name=str(spec.get("name", "custom")))
    raise ValueError("soil: a built-in name or a dict {n, kc, kphi, c, phi_deg, source}")


def _wheel(W: float, D: float, b: float, s: Dict[str, Any], slip: float, K: Optional[float]):
    """Bekker rigid wheel (closed form) + Janosi-Hanamoto thrust. Returns a dict."""
    n, kb = s["n"], s["kc"] + b * s["kphi"]                      # b*k, k = kc/b + kphi
    z0 = (3 * W / ((3 - n) * kb * math.sqrt(D))) ** (2 / (2 * n + 1))
    Rc = kb * z0 ** (n + 1) / (n + 1)
    l = math.sqrt(D * z0)
    out = {"sinkage_m": z0, "contact_length_m": l, "motion_resistance_N": Rc}
    if K:
        A = b * l
        sl = slip * l
        Hmax = A * s["c"] + W * math.tan(s["phi"])
        H = Hmax * (1 - K / sl * (1 - math.exp(-sl / K))) if sl > 0 else 0.0
        out.update(thrust_N=H, thrust_max_N=Hmax, drawbar_pull_N=H - Rc)
    return out


def _sinkage_quad(W: float, D: float, b: float, s: Dict[str, Any]) -> float:
    """Vertical equilibrium W = b * integral over the contact of p(z(x)) dx with the parabolic
    profile z(x) = z0 - x^2/D, solved without Bekker's (1-u^2)^n ~ 1-n u^2 step."""
    from scipy.integrate import quad
    from scipy.optimize import brentq
    n, kb = s["n"], s["kc"] + b * s["kphi"]

    def f(z0):
        l = math.sqrt(D * z0)
        val, _ = quad(lambda u: (1 - u * u) ** n, 0, 1, epsabs=1e-14, epsrel=1e-12)
        return kb * z0 ** n * l * val - W
    z_closed = (3 * W / ((3 - n) * kb * math.sqrt(D))) ** (2 / (2 * n + 1))
    hi = z_closed * 4
    while f(hi) < 0:
        hi *= 4
    return brentq(f, 1e-15, hi, xtol=1e-15, rtol=1e-13)


def _terramech(inp: Dict[str, Any], work: Path):
    from scipy.optimize import brentq
    s = _soil(inp.get("soil"))
    W = float(inp["load_N"])
    D = float(inp["wheel_diameter_m"])
    b = float(inp["wheel_width_m"])
    slip = float(inp.get("slip", 0.2))
    K = inp.get("K_m")
    K = float(K) if K else None
    nw = int(inp.get("n_wheels", 1))
    if not (W > 0 and D > 0 and b > 0 and 0 <= slip <= 1):
        raise ValueError("load_N, wheel_diameter_m, wheel_width_m > 0 and slip in [0, 1]")
    notes = [f"soil {s['name']}: source {s['source']}"]
    if s["source"] == "UNSOURCED":
        notes.append("soil parameters without a source: treat every number as UNKNOWN")
    r = _wheel(W, D, b, s, slip, K)
    values: Dict[str, Any] = {k: v for k, v in r.items()}
    values["sinkage_quad_m"] = _sinkage_quad(W, D, b, s)
    values["motion_resistance_coef"] = r["motion_resistance_N"] / W
    if r["sinkage_m"] > 0.3 * D:
        notes.append(f"sinkage {r['sinkage_m']:.3g} m > 0.3 D: outside Bekker's small-sinkage range")
    if K:
        values["drawbar_pull_coef"] = r["drawbar_pull_N"] / W
        if r["drawbar_pull_N"] <= 0:
            values["max_slope_deg"] = 0.0
            notes.append("drawbar pull <= 0 on flat ground at this slip: the wheel cannot advance")
        else:
            def f(a):
                rr = _wheel(W * math.cos(a), D, b, s, slip, K)
                return rr["drawbar_pull_N"] - W * math.sin(a)
            values["max_slope_deg"] = math.degrees(brentq(f, 0.0, math.radians(89.0)))
        values["vehicle_drawbar_pull_N"] = nw * r["drawbar_pull_N"]
    else:
        notes.append("K_m (shear deformation modulus, m) not given: no thrust, drawbar pull or slope")
    notes.append("Bekker/Janosi: rigid wheel, single pass, no bulldozing/lugs; "
                 "sinkage_quad_m = same model without Bekker's series step (gap = model error)")
    head = (f"terramech (W {W:g} N, D {D:g} m, b {b:g} m, slip {slip:g}): sinkage "
            f"{r['sinkage_m'] * 1000:.1f} mm, resistance {r['motion_resistance_N']:.4g} N"
            + (f", thrust {r['thrust_N']:.4g} N, drawbar pull {r['drawbar_pull_N']:.4g} N, "
               f"max slope {values['max_slope_deg']:.3g} deg" if K else ""))
    return values, head, [], notes


# ------------------------------------------------------------------ entry
_KINDS = {"multibody": _multibody, "inverse_dynamics": _inverse_dynamics, "control": _control,
          "balance": _balance, "terramech": _terramech}


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    if kind not in _KINDS:
        raise ValueError(f"unknown kind {kind!r}; one of {sorted(_KINDS)}")
    values, head, files, notes = _KINDS[kind](inp, Path(work))
    return result(values, inp.get("limits"), head, files, notes)


# ------------------------------------------------------------------ reference cases (§9)
_g = 9.81


def _ellip_period(L: float, theta0: float) -> float:
    # exact pendulum period T = 4 sqrt(L/g) K(k), k = sin(theta0/2) (scipy ellipk takes m = k^2)
    from scipy.special import ellipk
    return 4 * math.sqrt(L / _g) * float(ellipk(math.sin(theta0 / 2) ** 2))


try:
    _T_LARGE = _ellip_period(1.0, 2.0)
except Exception:  # noqa: BLE001 — scipy missing: the case fails instead of the import
    _T_LARGE = float("nan")
# static 2-link arm, axes -y: tau2 = m2 g c2 cos(q1+q2); tau1 = g[(m1 c1 + m2 L1) cos q1 + m2 c2 cos(q1+q2)]
_Q1, _Q2 = 0.3, -0.5
_TAU2 = 1.5 * _g * 0.2 * math.cos(_Q1 + _Q2)
_TAU1 = _g * ((2.0 * 0.25 + 1.5 * 0.5) * math.cos(_Q1)) + _TAU2
# CoM of the moving links: x = [m1 c1 cos q1 + m2 (L1 cos q1 + c2 cos(q1+q2))] / (m1 + m2)
# (the base, welded to the world, is fused into it by MuJoCo and not counted)
_COMX = (2.0 * 0.25 * math.cos(_Q1) + 1.5 * (0.5 * math.cos(_Q1) + 0.2 * math.cos(_Q1 + _Q2))) / 3.5
_COMZ = (2.0 * 0.25 * math.sin(_Q1) + 1.5 * (0.5 * math.sin(_Q1) + 0.2 * math.sin(_Q1 + _Q2))) / 3.5
_ZETA, _WN = 0.3, 2.0
_W_GC = math.sqrt(2 ** (2 / 3) - 1)         # |2/(jw+1)^3| = 1
REFERENCES = [
    {   # small amplitude: T = 2 pi sqrt(L/g) (error theta0^2/16 = 6e-6 at 0.01 rad); energy conserved
        "name": "pendulum_small", "kind": "multibody",
        "inp": {"model": "builtin:pendulum", "model_params": {"L": 1.0, "m": 1.0, "g": _g},
                "q0": {"hinge": 0.01}, "duration": 7.0, "timestep": 0.0005, "integrator": "rk4"},
        "expect": {"period_s": [2 * math.pi * math.sqrt(1.0 / _g), 1e-4],
                   "energy_drift_rel": [0.0, 1e-6]},
    },
    {   # large amplitude 2 rad: exact period 4 sqrt(L/g) K(sin^2(theta0/2)) (complete elliptic integral)
        "name": "pendulum_large", "kind": "multibody",
        "inp": {"model": "builtin:pendulum", "model_params": {"L": 1.0, "m": 1.0, "g": _g},
                "q0": {"hinge": 2.0}, "duration": 9.0, "timestep": 0.0005, "integrator": "rk4"},
        "expect": {"period_s": [_T_LARGE, 1e-4], "energy_drift_rel": [0.0, 1e-6]},
    },
    {   # free fall: z = z0 - g t^2 / 2, vz = -g t (RK4 is exact on a quadratic)
        "name": "free_fall", "kind": "multibody",
        "inp": {"model": "builtin:free_fall", "model_params": {"z0": 10.0, "g": _g},
                "duration": 1.0, "timestep": 0.001, "integrator": "rk4", "track_body": "ball"},
        "expect": {"body_z_m": [10.0 - 0.5 * _g * 1.0 ** 2, 1e-8], "body_vz_m_s": [-_g, 1e-8]},
    },
    {   # Pinocchio RNEA at rest = gravity torques by hand (formulas above)
        "name": "rnea_static_two_link", "kind": "inverse_dynamics",
        "inp": {"model": "builtin:two_link", "g": _g, "trajectory": {"q": [_Q1, _Q2]}},
        "expect": {"tau_shoulder_Nm": [_TAU1, 1e-9], "tau_elbow_Nm": [_TAU2, 1e-9]},
    },
    {   # same URDF, moving (sine on both joints): Pinocchio RNEA vs MuJoCo mj_inverse
        "name": "pinocchio_vs_mujoco", "kind": "inverse_dynamics",
        "inp": {"model": "builtin:two_link", "g": _g,
                "trajectory": {"type": "sine", "amplitude": [1.0, 1.5], "freq_hz": [0.7, 1.3],
                               "phase": [0.0, 1.0], "offset": [0.2, -0.3], "duration": 2.0, "dt": 0.01}},
        "expect": {"max_abs_diff_Nm": [0.0, 1e-6]},
    },
    {   # 2nd order wn^2/(s^2 + 2 zeta wn s + wn^2): overshoot exp(-zeta pi / sqrt(1-zeta^2)),
        # peak time pi / (wn sqrt(1-zeta^2))  (Ogata, Modern Control Engineering, ch. 5)
        "name": "second_order_step", "kind": "control",
        "inp": {"tf": {"num": [_WN ** 2], "den": [1, 2 * _ZETA * _WN, _WN ** 2]}},
        "expect": {"overshoot_pct": [100 * math.exp(-_ZETA * math.pi / math.sqrt(1 - _ZETA ** 2)), 1e-4],
                   "peak_time_s": [math.pi / (_WN * math.sqrt(1 - _ZETA ** 2)), 1e-4]},
    },
    {   # double integrator, Q = diag(q1, q2), R = r: K = [sqrt(q1/r), sqrt(q2/r + 2 sqrt(q1/r))]
        "name": "lqr_double_integrator", "kind": "control",
        "inp": {"ss": {"A": [[0, 1], [0, 0]], "B": [[0], [1]], "C": [[1, 0]], "D": [[0]]},
                "lqr": {"Q": [[4, 0], [0, 1]], "R": [[0.5]]}},
        "expect": {"lqr_K_0_0": [math.sqrt(4 / 0.5), 1e-6],
                   "lqr_K_0_1": [math.sqrt(1 / 0.5 + 2 * math.sqrt(4 / 0.5)), 1e-6]},
    },
    {   # L = 2/(s+1)^3: phase -180 deg at w = sqrt(3), |L| = 2/8 -> GM = 4;
        # |L| = 1 at w = sqrt(2^(2/3) - 1) -> PM = 180 - 3 atan(w) deg
        "name": "margins_third_order", "kind": "control",
        "inp": {"tf": {"num": [2], "den": [1, 3, 3, 1]}},
        "expect": {"gain_margin": [4.0, 1e-6], "w_phase_crossover_rad_s": [math.sqrt(3), 1e-6],
                   "phase_margin_deg": [180 - 3 * math.degrees(math.atan(_W_GC)), 1e-6]},
    },
    {   # static CoM: ZMP = ground projection; margin to the nearest edge of the 0.2 x 0.1 rectangle
        "name": "zmp_static", "kind": "balance",
        "inp": {"com": [0.03, -0.02, 0.8],
                "contacts": [[-0.1, -0.05], [0.1, -0.05], [0.1, 0.05], [-0.1, 0.05], [0.0, 0.0]]},
        "expect": {"zmp_x_m": [0.03, 1e-12], "zmp_y_m": [-0.02, 1e-12], "zmp_margin_m": [0.03, 1e-12]},
    },
    {   # cart-table: x_zmp = x - z a_x / (g + a_z) (Kajita et al., ICRA 2003)
        "name": "zmp_cart_table", "kind": "balance",
        "inp": {"com": [0.03, -0.02, 0.8], "com_acc": [1.0, -0.5, 0.0],
                "contacts": [[-0.1, -0.05], [0.1, -0.05], [0.1, 0.05], [-0.1, 0.05]]},
        "expect": {"zmp_x_m": [0.03 - 0.8 * 1.0 / _g, 1e-12], "zmp_y_m": [-0.02 + 0.8 * 0.5 / _g, 1e-12]},
    },
    {   # CoM of the 2-link URDF loaded in MuJoCo, by hand (moving links only, see above)
        "name": "com_two_link", "kind": "balance",
        "inp": {"model": "builtin:two_link", "q": {"shoulder": _Q1, "elbow": _Q2}, "contacts": [[0, 0]]},
        "expect": {"com_x_m": [_COMX, 1e-9], "com_z_m": [_COMZ, 1e-9], "mass_kg": [3.5, 1e-12]},
    },
    {   # Bekker equilibrium with n = 1: the series step is exact, z0 = (3W / (2 kb sqrt D))^(2/3)
        "name": "terramech_n1", "kind": "terramech",
        "inp": {"soil": {"n": 1.0, "kc": 1000.0, "kphi": 1.5e6, "c": 1000.0, "phi_deg": 28,
                         "source": "synthetic reference soil"},
                "load_N": 500.0, "wheel_diameter_m": 0.5, "wheel_width_m": 0.15, "slip": 0.2, "K_m": 0.02},
        "expect": {"sinkage_quad_m": [(3 * 500.0 / (2 * (1000.0 + 0.15 * 1.5e6) * math.sqrt(0.5))) ** (2 / 3), 1e-9]},
    },
    {   # n = 0.5: integral of sqrt(1-u^2) on [0,1] = pi/4 exactly -> z0 = 4W / (pi kb sqrt D)
        "name": "terramech_n05", "kind": "terramech",
        "inp": {"soil": {"n": 0.5, "kc": 13190.0, "kphi": 692150.0, "c": 4140.0, "phi_deg": 13,
                         "source": "synthetic reference soil"},
                "load_N": 800.0, "wheel_diameter_m": 0.6, "wheel_width_m": 0.2},
        "expect": {"sinkage_quad_m": [4 * 800.0 / (math.pi * (13190.0 + 0.2 * 692150.0) * math.sqrt(0.6)), 1e-9]},
    },
]
