"""Firmware in the loop with the physics (env `robot`): the REAL STM32 binary, compiled with the
Arm GNU toolchain and run in Renode, closes the loop on a MuJoCo model of the robot.

Kinds:
- robot_model  a parametric robot (cart_pole, balance_bot) or your MJCF: its sensors and
               actuators, and the open-loop divergence rate measured in MuJoCo against the closed
               form (the model is checked before anything is tuned on it)
- sil_run      firmware + physics in lockstep: every control period the sensors (with the noise
               and the quantisation you declare) are written into the firmware's mailbox in RAM,
               Renode runs for one period, the firmware's command is read back and applied to the
               actuators for the next period (one period of computation delay, as on the board)

The firmware includes "mcpfc_io.h" (written for you):
    typedef struct { volatile uint32_t seq_in, seq_out; volatile float in[16], out[8]; } mcpfc_io_t;
    extern mcpfc_io_t mcpfc_io;   uint32_t mcpfc_wait(uint32_t last);   // blocks until new inputs
  and loops:  last = mcpfc_wait(last); ... read in[i] ... out[j] = ...; mcpfc_io.seq_out = last;
A period where seq_out was not updated is a DEADLINE MISS: the previous command stays applied.

`reference_gains` {actuator: [k_i per input]} also runs u = -sum(k_i s_i) in Python on the same
model, noise and delay: `max_dev_vs_reference` says whether the firmware implements the law you
designed (float32 on the MCU vs float64 here: ~1e-6, not 0).

Security: the firmware only runs in the emulator. Renode connects to a socket bound to
127.0.0.1 (External Control API, client mode): nothing listens outside the loopback, and Renode's
sockets are audited every 250 periods (any one outside the loopback stops the run). A supplied MJCF may not
include files, meshes or plugins.

Limits, restated in every result: Renode is instruction-accurate, not cycle-accurate: CPU load and
timing margins are indicative; peripherals (timers, PWM, ADC registers) are bypassed by the
mailbox: the sensor and actuator models are the declared noise, quantisation and saturation.

Exactness: REFERENCES below. Divergence rates of both templates against
sqrt(m g l a / (a c - b^2)); the cart-pole stabilised by an LQR law compiled for the STM32, whose
trajectory matches the same law computed in Python.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

import math  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import struct  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

from _common import result, run_exe, tool  # noqa: E402

ENV = "robot"
KINDS = {
    "robot_model": {"geometry": False, "timeout": 120,
                    "doc": "parametric robot (cart_pole, balance_bot) or MJCF: sensors, actuators, "
                           "open-loop divergence rate vs closed form"},
    "sil_run": {"geometry": False, "timeout": 1800,
                "doc": "STM32 firmware in Renode closes the loop on the MuJoCo robot: trajectory, "
                       "deadline misses, deviation from the designed law"},
}

IO_H = r'''#ifndef MCPFC_IO_H
#define MCPFC_IO_H
#include <stdint.h>
typedef struct { volatile uint32_t seq_in, seq_out; volatile float in[16]; volatile float out[8]; } mcpfc_io_t;
extern mcpfc_io_t mcpfc_io;
uint32_t mcpfc_wait(uint32_t last);
#endif
'''
IO_C = r'''#include "mcpfc_io.h"
mcpfc_io_t mcpfc_io;
uint32_t mcpfc_wait(uint32_t last){ while(mcpfc_io.seq_in == last){} return mcpfc_io.seq_in; }
'''
OFF_IN, OFF_OUT, N_IN, N_OUT = 8, 72, 16, 8


def versions() -> Dict[str, str]:
    out: Dict[str, str] = {"renode": "1.17.0"}
    for mod in ("mujoco", "numpy"):
        try:
            out[mod] = str(__import__(mod).__version__)
        except Exception as e:  # noqa: BLE001
            out[mod] = f"missing ({type(e).__name__})"
    return out


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    if kind == "robot_model":
        return _robot_model(inp, Path(work))
    if kind == "sil_run":
        return _sil_run(inp, Path(work))
    raise ValueError(f"unknown kind {kind!r}; one of {sorted(KINDS)}")


def _robot_model(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import mujoco
    import _robots
    m, meta = _robots.load(inp.get("robot") or {}, work)
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    vals: Dict[str, Any] = {"nq": int(m.nq), "nu": int(m.nu), "nsensor": int(m.nsensor),
                            "timestep_s": float(m.opt.timestep)}
    notes = [f"sensors: {', '.join(_robots.sensors(m, d))}", f"actuators: {meta['actuators']}"]
    if meta.get("template"):
        lam = _robots.measured_divergence(m)
        vals["divergence_rate_1_s"] = round(lam, 6)
        vals["divergence_rate_closed_form"] = round(meta["divergence_rate_1_s"], 6)
        vals["divergence_error_rel"] = round(abs(lam / meta["divergence_rate_1_s"] - 1), 6)
        vals["time_to_double_s"] = round(math.log(2) / lam, 5)
        if inp.get("write_mjcf", True):
            xml, _ = _robots.template(meta["template"], (inp.get("robot") or {}).get("params") or {})
            (work / f"{meta['template']}.xml").write_text(xml, encoding="utf-8")
    head = f"robot {meta.get('template') or 'mjcf'}: {m.nq} dof, {m.nu} actuator(s)" + (
        f", open-loop divergence {vals['divergence_rate_1_s']} 1/s (closed form "
        f"{vals['divergence_rate_closed_form']})" if "divergence_rate_1_s" in vals else "")
    files = [p.name for p in work.glob("*.xml")]
    return result(vals, inp.get("limits"), head, files=files, notes=notes)


def _plant(inp: Dict[str, Any], work: Path):
    import mujoco
    import numpy as np
    import _robots
    m, meta = _robots.load(inp.get("robot") or {}, work)
    names_in = [str(n) for n in inp.get("inputs") or []]
    names_out = [str(n) for n in inp.get("outputs") or list(meta["actuators"])]
    if not 1 <= len(names_in) <= N_IN or not 1 <= len(names_out) <= N_OUT:
        raise ValueError(f"inputs: 1..{N_IN} sensor names, outputs: 1..{N_OUT} actuator names")
    d = mujoco.MjData(m)
    sens = _robots.sensors(m, d)
    for n in names_in:
        if n not in sens or isinstance(sens[n], tuple):
            raise ValueError(f"input {n!r} is not a scalar sensor; one of {sorted(sens)}")
    act = []
    for n in names_out:
        i = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, n)
        if i < 0:
            raise ValueError(f"output {n!r} is not an actuator; one of {list(meta['actuators'])}")
        act.append(i)
    hz = float(inp.get("control_hz", 100.0))
    sub = max(1, int(round(1.0 / (hz * m.opt.timestep))))
    dt = sub * m.opt.timestep
    steps = int(float(inp.get("duration_s", 5.0)) / dt)
    if not 1 <= steps <= 200000:
        raise ValueError("duration_s x control_hz: 1..200000 control periods")
    rng = np.random.default_rng(int(inp.get("seed", 0)))
    noise = np.zeros((steps, len(names_in)))
    for j, n in enumerate(names_in):
        s = float((inp.get("sensor_noise") or {}).get(n, 0.0))
        if s > 0:
            noise[:, j] = rng.normal(0, s, steps)
    lsb = [float((inp.get("quantization") or {}).get(n, 0.0)) for n in names_in]
    return m, meta, names_in, names_out, act, sub, dt, steps, noise, lsb


def _simulate(inp, plant, controller) -> Dict[str, Any]:
    import mujoco
    import numpy as np
    import _robots
    m, meta, names_in, names_out, act, sub, dt, steps, noise, lsb = plant
    d = mujoco.MjData(m)
    for jn, v in (inp.get("initial") or {}).items():
        j = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, str(jn))
        if j < 0:
            raise ValueError(f"initial: no joint {jn!r}")
        d.qpos[m.jnt_qposadr[j]] = float(v)
    push = inp.get("push")                      # {t_s, joint, dv}: a velocity kick
    mujoco.mj_forward(m, d)
    u_prev = np.zeros(len(act))
    traj = np.zeros((steps, len(names_in)))
    misses = 0
    for k in range(steps):
        sd = _robots.sensors(m, d)
        s = np.array([sd[n] for n in names_in]) + noise[k]
        s = np.array([round(v / q) * q if q > 0 else v for v, q in zip(s, lsb)])
        traj[k] = [sd[n] for n in names_in]
        u_new = controller(k, s)
        if u_new is None:
            misses += 1
            u_new = u_prev
        for i, a in enumerate(act):
            d.ctrl[a] = u_prev[i]
        for _ in range(sub):
            mujoco.mj_step(m, d)
        if push and abs(d.time - float(push["t_s"])) < dt / 2:
            j = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, str(push["joint"]))
            d.qvel[m.jnt_dofadr[j]] += float(push["dv"])
        u_prev = np.asarray(u_new, dtype=float)
    return {"traj": traj, "misses": misses}


def _firmware(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import firmware
    fw_inp = dict(inp)
    fw_inp["sources"] = dict(inp.get("sources") or {}, **{"mcpfc_io.h": IO_H, "mcpfc_io.c": IO_C})
    b = firmware._build(fw_inp, work)
    nm = run_exe([tool("arm_gnu", "arm-none-eabi-nm.exe"), str(b["elf"])], work, 30).stdout
    mm = re.search(r"^([0-9a-fA-F]+)\s+[BbDd]\s+mcpfc_io$", nm, re.M)
    if not mm:
        raise RuntimeError("mcpfc_io not found in the firmware: include mcpfc_io.h and use it")
    b["io_addr"] = int(mm.group(1), 16)
    return b


def _foreign_sockets(pid: int) -> List[str]:
    """Sockets of process `pid` bound outside the loopback (netstat: no extra package needed)."""
    exe = shutil.which("netstat")
    if not exe:
        return ["netstat not found: the socket audit cannot run"]
    r = run_exe([exe, "-ano"], Path.cwd(), 30)
    out = (r.stdout or "") if isinstance(r.stdout, str) else (r.stdout or b"").decode("utf-8", "replace")
    bad = []
    for ln in out.splitlines():
        f = ln.split()
        if len(f) >= 4 and f[0] in ("TCP", "UDP") and f[-1] == str(pid):
            local = f[1]
            if not (local.startswith("127.0.0.1:") or local.startswith("[::1]:")):
                bad.append(local)
    return bad


def _sil_run(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import numpy as np
    from _renode_ext import Listener, RenodeError, renode_env
    plant = _plant(inp, work)
    m, meta, names_in, names_out, act, sub, dt, steps, noise, lsb = plant
    b = _firmware(inp, work)
    addr = b["io_addr"]
    root = work / "renode"
    root.mkdir(exist_ok=True)
    if " " in str(root):
        raise RuntimeError(f"Renode needs a folder without spaces (got {root})")
    shutil.copy2(b["elf"], root / "fw.elf")
    lis = Listener()
    r = str(root).replace("\\", "/")
    (root / "run.resc").write_text(
        'mach create "mcu"\nmachine LoadPlatformDescription @platforms/cpus/stm32f4.repl\n'
        f"sysbus LoadELF @{r}/fw.elf\nsysbus.usart2 CreateFileBackend @{r}/uart.txt true\n"
        f'emulation CreateExternalControlClient "ext" {lis.port}\n', encoding="utf-8")
    flags = 0x08000000 if os.name == "nt" else 0
    log = open(root / "renode.log", "w")
    proc = subprocess.Popen([tool("renode"), "--disable-gui", "-P", "-1", "-p", "-e", f"include @{r}/run.resc"],
                            cwd=str(root), env=renode_env(root), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            creationflags=flags)
    t0 = time.time()
    rn = None
    try:
        rn = lis.accept(float(inp.get("connect_timeout_s", 90)))
        rn.handshake("mcu")
        rn.run_for_us(20000)                     # boot: .bss (the mailbox) zeroed by the startup
        dt_us = int(round(dt * 1e6))
        fmt_in = f"<{N_IN}f"

        def fw_controller(k, s):
            if k % 250 == 0:
                bad = _foreign_sockets(proc.pid)
                if bad:
                    raise PermissionError(f"Renode opened {', '.join(bad)} outside the loopback: stopped")
            vec = list(map(float, s)) + [0.0] * (N_IN - len(s))
            rn.write(addr + OFF_IN, struct.pack(fmt_in, *vec))
            rn.write(addr, struct.pack("<I", k + 1))
            rn.run_for_us(dt_us)
            seq_out = struct.unpack("<I", rn.read(addr + 4, 4))[0]
            if seq_out != k + 1:
                return None
            return struct.unpack(f"<{len(act)}f", rn.read(addr + OFF_OUT, 4 * len(act)))

        fw = _simulate(inp, plant, fw_controller)
        t_emu = rn.time_us()
    except (OSError, RenodeError) as e:
        raise RuntimeError(f"Renode co-simulation failed: {e}; see renode.log") from None
    finally:
        if rn:
            rn.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(15)
        log.close()
    wall = time.time() - t0
    traj = fw["traj"]
    vals: Dict[str, Any] = {"steps": steps, "control_dt_s": round(dt, 6), "deadline_misses": fw["misses"],
                            "emulated_s": round(t_emu / 1e6, 4), "wall_s": round(wall, 2),
                            "flash_bytes": b["flash_bytes"], "ram_static_bytes": b["ram_static_bytes"]}
    for j, n in enumerate(names_in):
        vals[f"final_abs_{n}"] = round(float(abs(traj[-1, j])), 7)
        vals[f"max_abs_{n}"] = round(float(np.abs(traj[:, j]).max()), 7)
    gains = inp.get("reference_gains")
    if gains:
        kmat = np.array([[float(x) for x in gains[n]] for n in names_out])
        if kmat.shape != (len(names_out), len(names_in)):
            raise ValueError("reference_gains: one list of len(inputs) gains per output")
        ref = _simulate(inp, plant, lambda k, s: -(kmat @ s))
        vals["max_dev_vs_reference"] = round(float(np.abs(ref["traj"] - traj).max()), 9)
    np.savetxt(work / "sil_trajectory.csv", traj, delimiter=",", header=",".join(names_in), comments="")
    uart = (root / "uart.txt").read_text(encoding="utf-8", errors="replace") if (root / "uart.txt").is_file() else ""
    (work / "uart.txt").write_text(uart, encoding="utf-8")
    shutil.copy2(root / "renode.log", work / "renode.log")
    shutil.rmtree(root, ignore_errors=True)
    head = (f"SIL {meta.get('template') or 'mjcf'}: {steps} periods of {dt * 1e3:g} ms, "
            f"{fw['misses']} deadline miss(es), " + ", ".join(
                f"final |{n}| {vals[f'final_abs_{n}']}" for n in names_in[:3]))
    notes = ["Renode is instruction-accurate, not cycle-accurate; peripherals bypassed by the "
             "mailbox (noise, quantisation, saturation are the declared models)"]
    if fw["misses"]:
        notes.insert(0, f"{fw['misses']} deadline miss(es): the firmware did not answer within one period")
    return result(vals, inp.get("limits"), head, files=["sil_trajectory.csv", "uart.txt", "renode.log"],
                  notes=notes)


_K = [-2.8283, -5.3632, -48.1699, -12.4544]      # discrete LQR at 100 Hz, one-period delay
_LQR_C = r'''#include "mcpfc_io.h"
static const float K[4] = {-2.8283f, -5.3632f, -48.1699f, -12.4544f};
int main(void){
  uint32_t last = 0;
  for(;;){
    last = mcpfc_wait(last);
    float u = 0.0f;
    for(int i = 0; i < 4; i++) u -= K[i] * mcpfc_io.in[i];
    mcpfc_io.out[0] = u;
    mcpfc_io.seq_out = last;
  }
}
'''

REFERENCES = [
    {"name": "cart_pole_divergence_closed_form", "kind": "robot_model",
     "inp": {"robot": {"template": "cart_pole"}, "write_mjcf": False},
     "expect": {"divergence_error_rel": [0.0, 2e-3]}},
    {"name": "balance_bot_divergence_closed_form", "kind": "robot_model",
     "inp": {"robot": {"template": "balance_bot"}, "write_mjcf": False},
     "expect": {"divergence_error_rel": [0.0, 2e-3]}},
    {"name": "stm32_lqr_balances_cart_pole", "kind": "sil_run",
     "inp": {"robot": {"template": "cart_pole"}, "inputs": ["x", "x_rate", "pitch", "pitch_rate"],
             "outputs": ["u"], "control_hz": 100, "duration_s": 3.0, "initial": {"pitch": 0.1},
             "sources": {"main.c": _LQR_C}, "reference_gains": {"u": _K}},
     "expect": {"deadline_misses": [0, 0], "final_abs_pitch": [0.0, 5e-3],
                "max_dev_vs_reference": [0.0, 1e-4], "steps": [300, 0]}},
]
