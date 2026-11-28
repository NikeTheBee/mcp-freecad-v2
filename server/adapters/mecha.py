"""Mechatronics: sensors, estimation and actuators (env `sim`).

Kinds:
- allan      IMU / sensor noise: overlapping Allan deviation of a simulated sensor (from its
             datasheet noise terms) or of a recorded series (CSV in the study folder); fitted
             angle/velocity random walk N, bias instability B, rate random walk K
- adc        quantisation and noise of an analog-to-digital converter: FFT of a coherently
             sampled sine -> SNR, SINAD, ENOB, against the ideal 6.02 N + 1.76 dB
- kalman     constant-velocity Kalman filter (position measured): steady-state gain and
             covariance (discrete Riccati), then a Monte-Carlo run that must reproduce them
- edf        electric ducted fan by momentum theory: static and in-flight thrust from the shaft
             power (or the power a thrust needs), exit velocity, mass flow, with the fan and exit
             areas, a fan efficiency, and the intake in front of it (capture area, duct length,
             lip and bend losses: the fan pays their total-pressure loss before the jet; the ram
             drag of the captured air is in T = mdot (Ve - V))
- dc_motor   brushed DC motor (+ gearbox): no-load speed, stall torque/current, operating point
             for a load, efficiency, copper loss and winding temperature, time constants, and a
             simulated voltage step

Units: SI (s, Hz, V, A, ohm, H, N m, kg m^2, rad/s, degC, K/W). Sensor noise: N in
unit/sqrt(Hz) (= unit/s/sqrt(Hz) for a rate, i.e. the ARW in deg/s/sqrt(Hz) gives deg/sqrt(s)),
B in unit, K in unit/s/sqrt(s)... always the unit of the signal itself.

Limits, restated in every result: Allan fits read the slopes in the regions you can see -- a
record shorter than ~10x the bias-instability time cannot show it; the ADC model is an ideal
quantiser plus white noise (no INL/DNL, no clock jitter unless given); the Kalman filter is
linear with the noise you declare; the motor model is linear (no saturation, no cogging, constant
resistance unless the temperature loop is asked for). Nothing here talks to a real sensor or
drives a real motor.

Exactness: REFERENCES below. Allan deviation of white noise is N / sqrt(tau) exactly (IEEE
Std 952); an ideal N-bit quantiser has SNR = 6.02 N + 1.76 dB; the steady-state alpha-beta
gains of the white-acceleration model at tracking index 1 are alpha = 0.75, beta = 0.5 exactly
(Kalata 1984); the DC motor's no-load speed V/Ke, stall torque Kt V/R and mechanical time
constant J R / (Kt Ke) are closed forms.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

import math  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

from _common import result  # noqa: E402

ENV = "sim"
KINDS = {
    "allan": {"geometry": False, "timeout": 300,
              "doc": "Allan deviation of a simulated or recorded sensor: random walk N, bias instability B, K"},
    "adc": {"geometry": False, "timeout": 60,
            "doc": "ADC quantisation + noise: FFT SNR, SINAD, ENOB vs ideal 6.02N+1.76 dB"},
    "kalman": {"geometry": False, "timeout": 120,
               "doc": "constant-velocity Kalman filter: steady-state gain/covariance + Monte-Carlo check"},
    "edf": {"geometry": False, "timeout": 60,
            "doc": "ducted fan momentum theory: thrust, exit velocity, mass flow vs shaft power and speed"},
    "dc_motor": {"geometry": False, "timeout": 120,
                 "doc": "DC motor + gearbox: no-load, stall, operating point, efficiency, heating, step"},
}


def versions() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for mod in ("numpy", "scipy"):
        try:
            out[mod] = str(__import__(mod).__version__)
        except Exception as e:  # noqa: BLE001
            out[mod] = f"missing ({type(e).__name__})"
    return out


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fn = {"allan": _allan, "adc": _adc, "kalman": _kalman, "dc_motor": _dc_motor, "edf": _edf}.get(kind)
    if fn is None:
        raise ValueError(f"unknown kind {kind!r}; one of {sorted(KINDS)}")
    return fn(inp, Path(work))


def _f(inp, key, default=None, lo=None, hi=None) -> float:
    v = inp.get(key, default)
    if v is None:
        raise ValueError(f"{key} is required")
    v = float(v)
    if not math.isfinite(v) or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(f"{key}={v} outside [{lo}, {hi}]")
    return v


# ---------------------------------------------------------------- Allan deviation
def _adev(y, fs: float, n_tau: int = 40):
    import numpy as np
    theta = np.cumsum(y) / fs                    # integrate the rate
    n = len(theta)
    ms = np.unique(np.logspace(0, math.log10(max(n // 9, 2)), n_tau).astype(int))
    taus, devs = [], []
    for m in ms:
        d = theta[2 * m:] - 2 * theta[m:-m] + theta[:-2 * m]
        tau = m / fs
        taus.append(tau)
        devs.append(math.sqrt(float(np.mean(d * d)) / (2 * tau * tau)))
    return np.array(taus), np.array(devs)


def _allan(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import numpy as np
    fs = _f(inp, "rate_hz", 100.0, 0.1, 1e5)
    if inp.get("data_file"):
        p = (work / str(inp["data_file"])).resolve()
        if work.resolve() not in p.parents:
            raise ValueError("data_file must be inside the study folder")
        y = np.loadtxt(p, delimiter=",", usecols=[int(inp.get("column", 0))], ndmin=1,
                       max_rows=20_000_000)
        src = "recorded"
    else:
        dur = _f(inp, "duration_s", 3600.0, 1.0, 2e5)
        n = int(dur * fs)
        if n > 20_000_000:
            raise ValueError("duration_s x rate_hz above 2e7 samples")
        rng = np.random.default_rng(int(inp.get("seed", 1)))
        N = _f(inp, "N", 0.0, 0.0)
        B = _f(inp, "B", 0.0, 0.0)
        K = _f(inp, "K", 0.0, 0.0)
        tc = _f(inp, "B_corr_time_s", 100.0, 1e-3)
        y = N * math.sqrt(fs) * rng.standard_normal(n)
        if K > 0:
            y += np.cumsum(K / math.sqrt(fs) * rng.standard_normal(n))
        if B > 0:   # first-order Gauss-Markov whose Allan bump approximates a flicker floor of B
            a = math.exp(-1.0 / (fs * tc))
            s = B / 0.664 * math.sqrt(1 - a * a) * 0.8
            from scipy.signal import lfilter
            y += lfilter([1.0], [1.0, -a], s * rng.standard_normal(n))
        y += _f(inp, "bias", 0.0)
        src = "simulated"
    taus, devs = _adev(y, fs)
    lt, ld = np.log10(taus), np.log10(devs)
    # N: on the -1/2 slope, read at tau = 1 s
    sl = np.gradient(ld, lt)
    i_n = int(np.argmin(np.abs(sl + 0.5) + 0.02 * np.abs(lt)))
    N_est = float(devs[i_n] * math.sqrt(taus[i_n]))
    i_b = int(np.argmin(devs))
    B_est = float(devs[i_b] / 0.664)
    vals: Dict[str, Any] = {"samples": int(len(y)), "N_est": round(N_est, 8),
                            "B_est": round(B_est, 8), "tau_at_min_s": round(float(taus[i_b]), 4),
                            "adev_min": round(float(devs[i_b]), 8)}
    vals["adev_at_1s"] = round(float(10 ** np.interp(0.0, lt, ld)), 8) if lt[0] <= 0 <= lt[-1] else None
    if sl[-1] > 0.3:
        vals["K_est"] = round(float(devs[-1] / math.sqrt(taus[-1] / 3.0)), 8)
    if not inp.get("data_file") and float(inp.get("N", 0) or 0) > 0:
        vals["N_error_rel"] = round(abs(N_est - float(inp["N"])) / float(inp["N"]), 5)
    csv = work / "allan.csv"
    csv.write_text("tau_s,adev\n" + "\n".join(f"{t:.6g},{d:.6g}" for t, d in zip(taus, devs)),
                   encoding="utf-8")
    notes = ["B is read at the curve's minimum (/0.664): a record shorter than ~10x that tau "
             "under-reads it; the curve is in allan.csv"]
    head = f"Allan ({src}, {len(y)} samples): N {vals['N_est']:.4g}, B {vals['B_est']:.4g} at tau {vals['tau_at_min_s']} s"
    return result(vals, inp.get("limits"), head, files=[csv.name], notes=notes)


# ---------------------------------------------------------------- ADC
def _adc(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import numpy as np
    bits = int(_f(inp, "bits", 12, 1, 32))
    vref = _f(inp, "vref_V", 3.3, 1e-3, 100.0)
    n = int(_f(inp, "points", 8192, 256, 1 << 20))
    fs = _f(inp, "rate_hz", 1e6, 1.0, 1e11)
    amp = _f(inp, "amplitude_V", vref / 2 * 0.999, 0.0, vref)
    noise = _f(inp, "noise_rms_V", 0.0, 0.0)
    jitter = _f(inp, "jitter_rms_s", 0.0, 0.0)
    cycles = int(inp.get("cycles", 0) or 0) or _coprime_cycles(n, _f(inp, "f_in_hz", fs / 50, 0.0) * n / fs)
    f_in = cycles * fs / n
    rng = np.random.default_rng(int(inp.get("seed", 1)))
    t = np.arange(n) / fs + (rng.standard_normal(n) * jitter if jitter > 0 else 0.0)
    v = vref / 2 + amp * np.sin(2 * np.pi * f_in * t) + noise * rng.standard_normal(n)
    lsb = vref / (1 << bits)
    code = np.clip(np.floor(v / lsb), 0, (1 << bits) - 1)
    x = (code + 0.5) * lsb - vref / 2
    spec = np.abs(np.fft.rfft(x)) ** 2
    spec[0] = 0.0
    sig = spec[cycles]
    harm = sum(spec[(k * cycles) % n if (k * cycles) % n <= n // 2 else n - (k * cycles) % n]
               for k in range(2, 7))
    noise_p = float(spec.sum() - sig)
    sinad = 10 * math.log10(sig / noise_p)
    snr = 10 * math.log10(sig / max(noise_p - harm, 1e-300))
    enob = (sinad - 1.76) / 6.02
    vals = {"bits": bits, "lsb_V": lsb, "f_in_hz": round(f_in, 6), "SINAD_dB": round(sinad, 4),
            "SNR_dB": round(snr, 4), "ENOB": round(enob, 4), "ideal_SNR_dB": round(6.02 * bits + 1.76, 4),
            "clipped": 1.0 if (v.min() < 0 or v.max() >= vref) else 0.0}
    head = f"ADC {bits} bit: SINAD {vals['SINAD_dB']} dB, ENOB {vals['ENOB']} (ideal {vals['ideal_SNR_dB']} dB)"
    return result(vals, inp.get("limits"), head,
                  notes=["ideal quantiser + white noise + jitter only: no INL/DNL, no reference noise"])


def _coprime_cycles(n: int, target: float) -> int:
    c = max(1, int(round(target)))
    while math.gcd(c, n) != 1:
        c += 1
    return c


# ---------------------------------------------------------------- Kalman
def _kalman(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import numpy as np
    from scipy.linalg import solve_discrete_are
    dt = _f(inp, "dt_s", 0.1, 1e-6, 1e3)
    sa = _f(inp, "accel_noise", 1.0, 0.0)          # sigma of the piecewise-constant acceleration
    sv = _f(inp, "meas_noise", 1.0, 1e-12)          # sigma of the position measurement
    F = np.array([[1.0, dt], [0.0, 1.0]])
    G = np.array([[dt * dt / 2], [dt]])
    Q = sa * sa * (G @ G.T)
    H = np.array([[1.0, 0.0]])
    R = np.array([[sv * sv]])
    Pp = solve_discrete_are(F.T, H.T, Q, R)        # prior (predicted) covariance
    S = H @ Pp @ H.T + R
    K = Pp @ H.T @ np.linalg.inv(S)
    P = (np.eye(2) - K @ H) @ Pp                   # posterior
    steps = int(_f(inp, "steps", 20000, 100, 2_000_000))
    rng = np.random.default_rng(int(inp.get("seed", 1)))
    x = np.zeros(2)
    xh = np.zeros(2)
    err = np.empty((steps, 2))
    for k in range(steps):
        x = F @ x + (G[:, 0] * sa * rng.standard_normal())
        z = x[0] + sv * rng.standard_normal()
        xh = F @ xh
        xh = xh + K[:, 0] * (z - xh[0])
        err[k] = x - xh
    burn = min(steps // 10, 1000)
    emp = err[burn:].std(axis=0)
    lam = sa * dt * dt / sv
    vals = {"alpha": round(float(K[0, 0]), 6), "beta": round(float(K[1, 0] * dt), 6),
            "tracking_index": round(lam, 6),
            "pos_sigma": round(math.sqrt(P[0, 0]), 6), "vel_sigma": round(math.sqrt(P[1, 1]), 6),
            "pos_sigma_mc": round(float(emp[0]), 6), "vel_sigma_mc": round(float(emp[1]), 6)}
    vals["mc_vs_theory_rel"] = round(abs(vals["pos_sigma_mc"] / vals["pos_sigma"] - 1), 5)
    head = (f"Kalman CV: alpha {vals['alpha']}, beta {vals['beta']}, position sigma "
            f"{vals['pos_sigma']} (Monte-Carlo {vals['pos_sigma_mc']})")
    return result(vals, inp.get("limits"), head,
                  notes=["linear model with the declared noises; real sensors need their own "
                         "noise from `allan` and a consistency test (NEES) on recorded data"])


# ---------------------------------------------------------------- DC motor
def _dc_motor(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import numpy as np
    from scipy.integrate import solve_ivp
    V = _f(inp, "voltage_V", None, 0.0, 1000.0)
    R = _f(inp, "R_ohm", None, 1e-5, 1e4)
    L = _f(inp, "L_H", 1e-4, 0.0, 10.0)
    if inp.get("Kv_rpm_per_V") is not None:
        kt = 60.0 / (2 * math.pi * _f(inp, "Kv_rpm_per_V", None, 1e-3, 1e6))
    else:
        kt = _f(inp, "Kt_Nm_per_A", None, 1e-6, 100.0)
    ke = kt                                          # SI: V s/rad = N m/A
    J = _f(inp, "J_kgm2", 1e-5, 1e-12, 1e3)
    b = _f(inp, "b_Nms", 0.0, 0.0, 1e3)
    t_fric = _f(inp, "friction_Nm", 0.0, 0.0, 1e4)
    ratio = _f(inp, "gear_ratio", 1.0, 1e-3, 1e5)
    eta_g = _f(inp, "gear_eff", 1.0, 0.05, 1.0)
    load_out = _f(inp, "load_torque_Nm", 0.0, -1e5, 1e5)     # at the gearbox output
    t_load = load_out / (ratio * eta_g) + t_fric             # at the motor shaft
    i_max = inp.get("max_current_A")
    # steady state: V = R I + ke w ; kt I = t_load + b w
    w = (kt * V - R * t_load) / (kt * ke + R * b)
    i = (t_load + b * w) / kt
    p_in = V * i
    p_mech_out = load_out * w / ratio
    vals: Dict[str, Any] = {
        "Kt_Nm_per_A": round(kt, 8), "no_load_speed_rad_s": round(kt * V / (kt * ke + R * b), 5),
        "stall_torque_Nm": round(kt * V / R, 6), "stall_current_A": round(V / R, 6),
        "speed_rad_s": round(w, 5), "speed_rpm": round(w * 60 / (2 * math.pi), 3),
        "output_speed_rpm": round(w / ratio * 60 / (2 * math.pi), 4),
        "current_A": round(i, 6), "input_power_W": round(p_in, 5),
        "output_power_W": round(p_mech_out, 5),
        "efficiency": round(p_mech_out / p_in, 5) if p_in > 0 and p_mech_out > 0 else 0.0,
        "copper_loss_W": round(i * i * R, 5),
        "tau_mech_s": round(J * R / (kt * ke + R * b), 8),
        "tau_elec_s": round(L / R, 8),
    }
    if inp.get("Rth_K_per_W") is not None:
        rth = _f(inp, "Rth_K_per_W", None, 0.0, 1e4)
        amb = _f(inp, "ambient_C", 25.0, -60, 200)
        alpha = 0.00393
        r_t, temp = R, amb
        for _ in range(100):                       # resistance rises with the winding temperature
            i_t = (t_load + b * w) / kt
            temp_new = amb + i_t * i_t * r_t * rth
            r_t = R * (1 + alpha * (temp_new - 20.0))
            if abs(temp_new - temp) < 1e-6:
                break
            temp = temp_new
        vals["winding_temp_C"] = round(temp, 3)
        if inp.get("max_winding_C") is not None:
            vals["winding_margin_C"] = round(float(inp["max_winding_C"]) - temp, 3)
    if i_max is not None:
        vals["current_to_max"] = round(i / float(i_max), 5)
    if w <= 0:
        vals["stalled"] = 1.0
    # voltage step from rest
    t_end = _f(inp, "step_time_s", 6 * max(vals["tau_mech_s"], vals["tau_elec_s"]), 1e-9, 1e4)

    def rhs(t, s):
        ii, ww = s
        di = (V - R * ii - ke * ww) / L if L > 0 else 0.0
        return [di, (kt * ii - t_load - b * ww) / J]

    if L > 0:
        sol = solve_ivp(rhs, (0, t_end), [0.0, 0.0], method="Radau", rtol=1e-8, atol=1e-10,
                        dense_output=True)
        tt = np.linspace(0, t_end, 2001)
        ww = sol.sol(tt)[1]
        ii = sol.sol(tt)[0]
        vals["step_peak_current_A"] = round(float(ii.max()), 5)
        if w > 0:
            k63 = int(np.argmax(ww >= 0.632120558 * w))
            vals["step_t63_s"] = round(float(tt[k63]), 8) if ww[k63] >= 0.632120558 * w else None
        csv = work / "motor_step.csv"
        csv.write_text("t_s,current_A,speed_rad_s\n" + "\n".join(
            f"{a:.6g},{c:.6g},{d:.6g}" for a, c, d in zip(tt[::10], ii[::10], ww[::10])), encoding="utf-8")
        files = [csv.name]
    else:
        files = []
    head = (f"DC motor: {vals['speed_rpm']} rpm at {round(t_load, 5)} N m (motor side), "
            f"{vals['current_A']} A, efficiency {vals['efficiency']}, stall {vals['stall_torque_Nm']} N m")
    notes = ["linear model: no saturation, no cogging, no driver current limit"]
    if i_max is not None and i > float(i_max):
        notes.insert(0, f"current {i:.3g} A above max_current_A {i_max}")
    return result(vals, inp.get("limits"), head, files=files, notes=notes)

# ---------------------------------------------------------------- ducted fan
def _edf(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """Momentum theory of a ducted fan whose jet leaves through the exit area A_e:
    mdot = rho A_e Ve, T = mdot (Ve - V), ideal power = mdot (Ve^2 - V^2) / 2, shaft power =
    ideal / eta. Static (V = 0): T = (rho A_e)^(1/3) (2 eta P)^(2/3)."""
    import numpy as np
    from scipy.optimize import brentq
    d = _f(inp, "fan_diameter_m", None, 0.01, 2.0)
    dh = _f(inp, "hub_diameter_m", 0.0, 0.0, d * 0.9)
    a_fan = math.pi / 4 * (d * d - dh * dh)
    fsa = _f(inp, "exit_area_ratio", 0.9, 0.3, 1.5)        # exit area / fan swept area
    a_e = fsa * a_fan
    rho = _f(inp, "air_density", 1.225, 0.1, 2.0)
    eta = _f(inp, "fan_efficiency", 0.65, 0.1, 1.0)        # shaft -> jet kinetic power

    # the air path in front of the fan: capture area of the intake, duct length and hydraulic
    # diameter, lip and bend losses. Its total-pressure loss is paid by the fan before the jet:
    # eta P = mdot (Ve^2 - V^2)/2 + dp0 * mdot / rho, dp0 = K * rho Vd^2 / 2, Vd = mdot/(rho A_d)
    intake = inp.get("intake") or {}
    a_in = float(intake.get("area_m2", 0) or 0)
    a_d = float(intake.get("duct_area_m2", a_fan) or a_fan)
    k_lip = float(intake.get("k_lip", 0.05))          # rounded lip 0.03-0.05, sharp 0.5
    k_bend = float(intake.get("k_bends", 0.0))
    f_fric = float(intake.get("friction_factor", 0.025))
    l_d = float(intake.get("duct_length_m", 0.0))
    d_h = float(intake.get("hydraulic_diameter_m", math.sqrt(4 * a_d / math.pi)))
    k_tot = (k_lip + k_bend + f_fric * l_d / d_h) if intake else 0.0

    def loss_power(mdot):
        vd = mdot / (rho * a_d)
        return k_tot * 0.5 * rho * vd * vd * mdot / rho

    def thrust(p_shaft, v):
        pi = eta * p_shaft
        g = lambda x: 0.5 * rho * a_e * x * (x * x - v * v) + loss_power(rho * a_e * x) - pi
        ve = brentq(g, v + 1e-9, v + 1e4)
        mdot = rho * a_e * ve
        return mdot * (ve - v), ve, mdot

    vals: Dict[str, Any] = {"fan_area_m2": round(a_fan, 7), "exit_area_m2": round(a_e, 7)}
    if inp.get("shaft_power_W") is not None:
        p = _f(inp, "shaft_power_W", None, 1.0, 1e6)
    else:
        t_goal = _f(inp, "static_thrust_N", None, 0.01, 1e5)
        ve = (t_goal / (rho * a_e)) ** 0.5
        p = 0.5 * rho * a_e * ve ** 3 / eta
        vals["shaft_power_for_thrust_W"] = round(p, 3)
    t0, ve0, md0 = thrust(p, 0.0)
    if intake:
        ideal = (rho * a_e) ** (1 / 3) * (2 * eta * p) ** (2 / 3)
        jet = 0.5 * md0 * ve0 ** 2
        vals.update({"intake_K_total": round(k_tot, 5),
                     "duct_velocity_m_s": round(md0 / (rho * a_d), 3),
                     "duct_loss_W": round(loss_power(md0), 3),
                     "thrust_loss_vs_ideal_inlet": round(1 - t0 / ideal, 5),
                     "energy_balance_residual_rel": round(abs(jet + loss_power(md0) - eta * p) / (eta * p), 9)})
        if a_in > 0:
            vals["intake_area_ratio"] = round(a_in / a_fan, 4)          # capture / fan swept area
            vals["intake_velocity_static_m_s"] = round(md0 / (rho * a_in), 3)
    vals.update({"shaft_power_W": round(p, 3), "static_thrust_N": round(t0, 5),
                 "static_thrust_kgf": round(t0 / 9.80665, 5), "exit_velocity_m_s": round(ve0, 4),
                 "mass_flow_kg_s": round(md0, 5),
                 "thrust_per_power_g_W": round(t0 / 9.80665 * 1000 / p, 4)})
    speeds = [float(v) for v in (inp.get("speeds_m_s") or [0, 10, 20, 30, 40])]
    curve = [round(thrust(p, v)[0], 4) for v in speeds]
    vals["thrust_vs_speed_N"] = dict(zip([f"{v:g}" for v in speeds], curve))
    if inp.get("mass_kg") is not None:
        vals["thrust_to_weight"] = round(t0 / (9.80665 * _f(inp, "mass_kg", None, 0.01, 1e4)), 4)
    if inp.get("cd0_area_m2") is not None:               # drag area CdS: top speed where T = D
        cds = _f(inp, "cd0_area_m2", None, 1e-6, 100)
        f = lambda v: thrust(p, v)[0] - 0.5 * rho * v * v * cds
        vals["top_speed_m_s"] = round(brentq(f, 0.1, ve0 * 0.999), 3)
        vals["top_speed_kmh"] = round(vals["top_speed_m_s"] * 3.6, 2)
    head = (f"EDF {d * 1000:.0f} mm, {p:.0f} W shaft: static {t0 / 9.80665:.3f} kgf, jet {ve0:.1f} m/s, "
            f"{md0:.3f} kg/s" + (f", intake K {k_tot:.3f} costs {vals['thrust_loss_vs_ideal_inlet'] * 100:.1f} % "
                                 f"of thrust" if intake else ", ideal inlet (no intake given)"))
    notes_extra = []
    if intake and a_in > 0 and a_in / a_fan < 0.9:
        notes_extra.append("intake capture area below 90 % of the fan area: the fan starves on the ground")
    if not intake:
        notes_extra.append("NO INTAKE GIVEN: the thrust assumes a perfect inlet; give `intake` for a real airframe")
    return result(vals, inp.get("limits"), head,
                  notes=notes_extra + ["momentum theory with a lumped fan efficiency (0.6-0.75 for a good 70 mm "
                         "unit): tip losses, inlet distortion and duct friction are inside eta; "
                         "a bench test (thrust, current, rpm) calibrates it"])


REFERENCES = [
    {"name": "edf_static_closed_form", "kind": "edf",   # T = (rho A_e)^(1/3) (2 eta P)^(2/3)
     "inp": {"fan_diameter_m": 0.07, "hub_diameter_m": 0.03, "exit_area_ratio": 0.9,
             "fan_efficiency": 0.6, "shaft_power_W": 700.0},
     "expect": {"static_thrust_N": [(1.225 * 0.9 * math.pi / 4 * (0.07 ** 2 - 0.03 ** 2)) ** (1 / 3)
                                    * (2 * 0.6 * 700.0) ** (2 / 3), 1e-4]}},
    {"name": "edf_intake_energy_balance", "kind": "edf",   # jet power + duct loss = eta P, and
     # a lossy intake gives less thrust than the ideal closed form
     "inp": {"fan_diameter_m": 0.07, "hub_diameter_m": 0.03, "exit_area_ratio": 0.9,
             "fan_efficiency": 0.6, "shaft_power_W": 700.0,
             "intake": {"area_m2": 0.0034, "duct_length_m": 0.22, "k_lip": 0.05, "k_bends": 0.15}},
     "expect": {"energy_balance_residual_rel": [0.0, 1e-6]}},
    {"name": "edf_power_for_thrust_inverse", "kind": "edf",   # P(T) then T(P) gives T back
     "inp": {"fan_diameter_m": 0.07, "hub_diameter_m": 0.03, "static_thrust_N": 13.7},
     "expect": {"static_thrust_N": [13.7, 1e-6]}},
    {"name": "allan_white_noise_N", "kind": "allan",   # sigma(tau) = N / sqrt(tau), IEEE 952
     "inp": {"rate_hz": 100.0, "duration_s": 20000.0, "N": 0.01, "seed": 3},
     "expect": {"N_error_rel": [0.0, 0.03]}},
    {"name": "adc_ideal_12bit", "kind": "adc",          # 6.02 * 12 + 1.76 = 74.0 dB
     "inp": {"bits": 12, "vref_V": 3.3, "points": 16384, "rate_hz": 1e6, "f_in_hz": 10000.0},
     "expect": {"SINAD_dB": [74.0, 4e-3], "ENOB": [12.0, 5e-3], "clipped": [0.0, 0]}},
    {"name": "kalman_alpha_beta_index_1", "kind": "kalman",   # Kalata: lambda = 1 -> 0.75, 0.5
     "inp": {"dt_s": 1.0, "accel_noise": 1.0, "meas_noise": 1.0, "steps": 200000, "seed": 2},
     "expect": {"alpha": [0.75, 1e-5], "beta": [0.5, 1e-5], "pos_sigma": [math.sqrt(0.75), 1e-5],
                "mc_vs_theory_rel": [0.0, 0.02]}},
    {"name": "dc_motor_closed_forms", "kind": "dc_motor",
     "inp": {"voltage_V": 12.0, "R_ohm": 1.0, "L_H": 1e-6, "Kt_Nm_per_A": 0.01, "J_kgm2": 1e-5},
     "expect": {"no_load_speed_rad_s": [1200.0, 1e-9], "stall_torque_Nm": [0.12, 1e-9],
                "stall_current_A": [12.0, 1e-9], "tau_mech_s": [0.1, 1e-9],
                "step_t63_s": [0.1, 2e-3]}},
    {"name": "dc_motor_geared_load_point", "kind": "dc_motor",   # V = R I + Ke w, Kt I = T/(n eta)
     "inp": {"voltage_V": 24.0, "R_ohm": 0.5, "L_H": 1e-4, "Kt_Nm_per_A": 0.05, "J_kgm2": 2e-5,
             "gear_ratio": 10.0, "gear_eff": 0.8, "load_torque_Nm": 2.0},
     "expect": {"current_A": [5.0, 1e-9], "speed_rad_s": [430.0, 1e-9],
                "efficiency": [2.0 * 43.0 / (24.0 * 5.0), 1e-4]}},
]
