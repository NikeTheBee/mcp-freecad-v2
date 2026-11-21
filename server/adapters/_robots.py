"""Parametric MuJoCo robots shared by `sil` (firmware in the loop), `ai` (reinforcement learning)
and any study: an inverted pendulum on a cart, and a two-wheeled self-balancing robot (gyropode).

Both are planar: a slider `x` carries the base. The cart-pole has a force actuator on the cart.
The gyropode has a wheel hinge tied to `x` by rolling without slipping (x = R theta, a stiff
equality), and a motor between wheel and chassis (+tau on the wheel, -tau on the chassis, through a
fixed tendon). Named sensors: x, x_rate, pitch, pitch_rate (+ wheel, wheel_rate, and an IMU gyro /
accelerometer on the chassis for the gyropode).

Linearised about upright, both are  M q'' = K q  with  a = (base mass terms), b = m_p l,
c = I_p + m_p l^2, and the open-loop divergence rate  lambda = sqrt(m_p g l a / (a c - b^2)):
the closed form the reference cases compare MuJoCo's free fall against.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Tuple

G = 9.81


def _p(params: Dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    v = float(params.get(key, default))
    if not (math.isfinite(v) and lo <= v <= hi):
        raise ValueError(f"robot param {key}={v} outside [{lo}, {hi}]")
    return v


def template(name: str, params: Dict[str, Any] | None = None) -> Tuple[str, Dict[str, Any]]:
    """(MJCF text, meta) of a template robot. Units SI."""
    p = dict(params or {})
    dt = _p(p, "timestep_s", 0.0005, 1e-5, 0.01)
    if name == "cart_pole":
        M = _p(p, "cart_mass_kg", 1.0, 0.01, 1e3)
        m = _p(p, "pole_mass_kg", 0.1, 0.001, 1e3)
        l = _p(p, "pole_com_m", 0.5, 0.01, 10)
        I = _p(p, "pole_inertia_kgm2", m * (2 * l) ** 2 / 12, 1e-9, 1e4)
        F = _p(p, "max_force_N", 20.0, 0.01, 1e5)
        xml = f"""<mujoco model="cart_pole">
  <option timestep="{dt}" gravity="0 0 {-G}" integrator="RK4"/>
  <worldbody>
    <body name="cart">
      <joint name="x" type="slide" axis="1 0 0" damping="0"/>
      <inertial pos="0 0 0" mass="{M}" diaginertia="1e-3 1e-3 1e-3"/>
      <geom type="box" size="0.1 0.05 0.03" contype="0" conaffinity="0"/>
      <body name="pole">
        <joint name="pitch" type="hinge" axis="0 1 0" damping="0"/>
        <inertial pos="0 0 {l}" mass="{m}" diaginertia="{I} {I} {I}"/>
        <geom type="capsule" fromto="0 0 0 0 0 {2 * l}" size="0.01" contype="0" conaffinity="0" mass="0"/>
        <site name="imu" pos="0 0 {l}"/>
      </body>
    </body>
  </worldbody>
  <actuator><motor name="u" joint="x" gear="1" ctrllimited="true" ctrlrange="{-F} {F}"/></actuator>
  <sensor>
    <jointpos name="x" joint="x"/><jointvel name="x_rate" joint="x"/>
    <jointpos name="pitch" joint="pitch"/><jointvel name="pitch_rate" joint="pitch"/>
  </sensor>
</mujoco>"""
        a, b, c = M + m, m * l, I + m * l * l
        meta = {"actuators": {"u": [-F, F]}, "m_p": m, "l": l, "a": a, "b": b, "c": c}
    elif name == "balance_bot":
        mb = _p(p, "body_mass_kg", 1.2, 0.01, 1e3)
        l = _p(p, "body_com_m", 0.08, 0.005, 5)
        Ib = _p(p, "body_inertia_kgm2", mb * (2 * l) ** 2 / 12, 1e-9, 1e4)
        mw = _p(p, "wheel_mass_kg", 0.1, 0.001, 1e3)          # both wheels together
        R = _p(p, "wheel_radius_m", 0.04, 0.005, 2)
        Iw = _p(p, "wheel_inertia_kgm2", mw * R * R / 2, 1e-9, 1e3)
        T = _p(p, "max_torque_Nm", 0.6, 1e-4, 1e4)             # both motors together
        xml = f"""<mujoco model="balance_bot">
  <option timestep="{dt}" gravity="0 0 {-G}" integrator="RK4"/>
  <worldbody>
    <body name="axle" pos="0 0 {R}">
      <joint name="x" type="slide" axis="1 0 0" damping="0"/>
      <inertial pos="0 0 0" mass="1e-6" diaginertia="1e-9 1e-9 1e-9"/>
      <body name="wheel">
        <joint name="wheel" type="hinge" axis="0 1 0" damping="0"/>
        <inertial pos="0 0 0" mass="{mw}" diaginertia="{Iw} {Iw} {Iw}"/>
        <geom type="cylinder" fromto="0 -0.05 0 0 0.05 0" size="{R}" contype="0" conaffinity="0" mass="0"/>
      </body>
      <body name="chassis">
        <joint name="pitch" type="hinge" axis="0 1 0" damping="0"/>
        <inertial pos="0 0 {l}" mass="{mb}" diaginertia="{Ib} {Ib} {Ib}"/>
        <geom type="box" pos="0 0 {l}" size="0.03 0.05 {l}" contype="0" conaffinity="0" mass="0"/>
        <site name="imu" pos="0 0 {l}"/>
      </body>
    </body>
  </worldbody>
  <equality><joint joint1="x" joint2="wheel" polycoef="0 {R} 0 0 0" solref="{max(2 * dt, 0.001)} 1"/></equality>
  <tendon><fixed name="drive"><joint joint="wheel" coef="1"/><joint joint="pitch" coef="-1"/></fixed></tendon>
  <actuator><motor name="u" tendon="drive" gear="1" ctrllimited="true" ctrlrange="{-T} {T}"/></actuator>
  <sensor>
    <jointpos name="x" joint="x"/><jointvel name="x_rate" joint="x"/>
    <jointpos name="pitch" joint="pitch"/><jointvel name="pitch_rate" joint="pitch"/>
    <jointpos name="wheel" joint="wheel"/><jointvel name="wheel_rate" joint="wheel"/>
    <gyro name="gyro" site="imu"/><accelerometer name="accel" site="imu"/>
  </sensor>
</mujoco>"""
        # chassis pitch phi is relative to the axle frame, which only translates: absolute
        a, b, c = (mw + mb) * R * R + Iw, mb * R * l, Ib + mb * l * l
        meta = {"actuators": {"u": [-T, T]}, "m_p": mb, "l": l, "a": a, "b": b, "c": c, "R": R}
        # in wheel-angle coordinates: x = R theta, so the coupling uses R (b) and R^2 (a)
    else:
        raise ValueError(f"robot template {name!r}: cart_pole or balance_bot")
    meta["template"] = name
    meta["divergence_rate_1_s"] = math.sqrt(meta["m_p"] * G * meta["l"] * meta["a"]
                                            / (meta["a"] * meta["c"] - meta["b"] ** 2))
    return xml, meta


def load(spec: Dict[str, Any], work=None):
    """(mujoco.MjModel, meta) from {"template": name, "params": {...}} or {"mjcf": file in work}."""
    import mujoco
    if spec.get("template"):
        xml, meta = template(str(spec["template"]), spec.get("params") or {})
        return mujoco.MjModel.from_xml_string(xml), meta
    f = spec.get("mjcf")
    if not f or work is None:
        raise ValueError("robot: {template: cart_pole|balance_bot, params} or {mjcf: file in the study folder}")
    from pathlib import Path
    base = Path(work).resolve()
    path = (base / str(f)).resolve()
    if base not in path.parents or path.suffix.lower() != ".xml":
        raise ValueError("robot.mjcf: an .xml file inside the study folder")
    text = path.read_text(encoding="utf-8")
    low = text.lower()
    for bad in ("<include", "file=", "meshdir", "texturedir", "assetdir", "<plugin", "<extension"):
        if bad in low:
            raise ValueError(f"robot.mjcf: {bad!r} refused (reads other files or loads a plugin); "
                             "inline the model")
    m = mujoco.MjModel.from_xml_string(text)
    return m, {"template": None, "actuators": {
        mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_ACTUATOR, i): list(map(float, m.actuator_ctrlrange[i]))
        for i in range(m.nu)}}


def sensors(model, data) -> Dict[str, Any]:
    """{name: float or tuple} of every named sensor."""
    import mujoco
    out: Dict[str, Any] = {}
    for i in range(model.nsensor):
        n = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_SENSOR, i)
        a, d = model.sensor_adr[i], model.sensor_dim[i]
        v = data.sensordata[a:a + d]
        out[n] = float(v[0]) if d == 1 else tuple(float(x) for x in v)
    return out


def measured_divergence(model, signal: str = "pitch", phi0: float = 1e-4, until: float = 0.02) -> float:
    """Open-loop growth rate of `signal` from a tiny tilt: slope of log|phi| while it stays small."""
    import mujoco
    import numpy as np
    d = mujoco.MjData(model)
    jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, signal)
    d.qpos[model.jnt_qposadr[jid]] = phi0
    mujoco.mj_forward(model, d)
    ts, ys = [], []
    while True:
        mujoco.mj_step(model, d)
        y = abs(float(d.qpos[model.jnt_qposadr[jid]]))
        if y > until or d.time > 20:
            break
        ts.append(d.time)
        ys.append(math.log(max(y, 1e-300)))
    ts, ys = np.array(ts), np.array(ys)
    keep = ts > ts[-1] * 0.5               # past the start transient: the unstable mode dominates
    return float(np.polyfit(ts[keep], ys[keep], 1)[0])
