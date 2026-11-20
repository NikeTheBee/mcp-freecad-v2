"""POGO: longitudinal structure-propulsion coupling of a liquid rocket (env `sim`).

POGO is the closed loop of a liquid launch vehicle: the structure vibrates longitudinally, the
acceleration shakes the propellant column, the pressure oscillation reaches the pump, the pump
modulates the chamber pressure, the thrust oscillation shakes the structure again. Below a
certain stability margin the loop diverges; Titan II, Saturn V and Ariane all had to be fixed
with accumulators. NASA's own tool (ML_POGO, MFS-33024-1) is US-release-only and there is no
free equivalent, so this adapter implements the published method: hydraulic lines as transfer
matrices, a pump transfer function, a modal structure, and a closed-loop eigenvalue search.

Kinds:
- feedline        hydraulic dynamics of a propellant line: inertance, compliance, resistance,
                  organ-pipe modes, accumulator (Helmholtz) frequency, line transfer matrix
- pogo_stability  the closed loop: per-mode closed-loop frequency and damping, the gain margin
                  (how much the pump gain may be multiplied before a mode reaches zero damping),
                  and the Nyquist margins of the loop
- accumulator     sizing: the compliance needed to reach a target gain margin, and what it does
                  to the feed-system frequency

Units: SI. Lengths m, areas m^2, densities kg/m^3, pressures Pa, volumes m^3, flows m^3/s,
compliance m^3/Pa, inertance Pa s^2/m^3, times s, frequencies rad/s and Hz. `damping_ratio` is
the fraction of critical damping (0.005 = 0.5 %), the usual way launcher modal damping is given.

Model and its limits, restated in every result:
- linear, small perturbations about ONE frozen flight point: run several points along the
  trajectory (propellant level, thrust, pump speed all change) rather than trusting one;
- one propellant line and one engine per run; several engines in parallel are represented by
  their summed thrust and a single equivalent line;
- the structure enters as a modal basis (frequency, damping, modal mass, mode shape value at the
  engine and at the tank bottom) which is what `fem_modal` (CalculiX) produces;
- the pump is a gain with a transport delay and a cavitation compliance, not a blade-row model;
- combustion is a first-order lag: no high-frequency chamber instability (chug, buzz, screech)
  is covered here, those are a different family;
- POGO is longitudinal: lateral and torsional coupling are out of scope.

Exactness: REFERENCES below. Degenerate cases whose closed-loop roots, gain margin, organ-pipe
frequencies and Helmholtz frequency are exact closed forms.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
# MKL's default threading layer loads a second OpenMP runtime next to llvm-openmp in this
# environment and then kills the process inside any LAPACK call. See the runner's note.
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

import cmath  # noqa: E402
import csv  # noqa: E402
import math  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

import numpy as np  # noqa: E402

from _common import result  # noqa: E402

ENV = "sim"
KINDS = {
    "feedline": {"timeout": 120,
                 "doc": "propellant line dynamics: inertance, compliance, organ-pipe modes, "
                        "accumulator (Helmholtz) frequency, transfer matrix at a frequency"},
    "pogo_stability": {"timeout": 300,
                       "doc": "closed-loop POGO: per-mode frequency and damping, gain margin on "
                              "the pump gain, Nyquist margins, stable or not"},
    "accumulator": {"timeout": 300,
                    "doc": "accumulator sizing: compliance needed for a target POGO gain margin"},
}


def versions() -> Dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for p in ("numpy", "scipy", "control", "fluids"):
        try:
            out[p] = version(p)
        except PackageNotFoundError:
            out[p] = "missing"
    return out


def _f(x) -> float:
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(f"not a finite number: {x!r}")
    return v


def _pos(inp: Dict[str, Any], key: str, default=None) -> float:
    if key in inp:
        v = _f(inp[key])
    elif default is not None:
        v = _f(default)
    else:
        raise ValueError(f"missing input `{key}`")
    if v <= 0:
        raise ValueError(f"`{key}` must be > 0, got {v}")
    return v


# --------------------------------------------------------------------------- line hydraulics
class Line:
    """A uniform liquid line: distributed inertance, compliance and resistance.

    Per unit length: L' = rho/A (Pa s^2/m^3 per m), C' = A/(rho c^2), R' from the friction
    factor. The transfer matrix of a length l at s (Laplace) is the classical lossy-line one

        [P2]   [ cosh(gamma l)      -Zc sinh(gamma l) ] [P1]
        [Q2] = [ -sinh(gamma l)/Zc   cosh(gamma l)    ] [Q1]

    with gamma = sqrt((R' + s L') s C') and Zc = sqrt((R' + s L')/(s C')).
    """

    def __init__(self, length: float, area: float, rho: float, sound_speed: float,
                 resistance_per_length: float = 0.0):
        self.l, self.a, self.rho, self.c = length, area, rho, sound_speed
        self.rp = resistance_per_length
        self.lp = rho / area                        # inertance per unit length
        self.cp = area / (rho * sound_speed ** 2)   # compliance per unit length

    @property
    def inertance(self) -> float:
        return self.lp * self.l

    @property
    def compliance(self) -> float:
        return self.cp * self.l

    def matrix(self, s: complex) -> np.ndarray:
        if s == 0:
            return np.array([[1.0, -self.rp * self.l], [0.0, 1.0]], dtype=complex)
        zs = self.rp + s * self.lp
        gam = cmath.sqrt(zs * s * self.cp)
        zc = cmath.sqrt(zs / (s * self.cp))
        ch, sh = cmath.cosh(gam * self.l), cmath.sinh(gam * self.l)
        return np.array([[ch, -zc * sh], [-sh / zc, ch]], dtype=complex)


def _organ_pipe(c: float, length: float, n: int, both_open: bool) -> List[float]:
    """Acoustic modes of a liquid column: closed-open (quarter wave) or open-open (half wave)."""
    out = []
    for i in range(1, n + 1):
        out.append(c * (2 * i - 1) / (4.0 * length) if not both_open else c * i / (2.0 * length))
    return out


def _run_feedline(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    rho = _pos(inp, "density_kg_m3")
    c = _pos(inp, "sound_speed_m_s")
    length = _pos(inp, "length_m")
    area = _pos(inp, "area_m2") if "area_m2" in inp else math.pi / 4.0 * _pos(inp, "diameter_m") ** 2
    rp = _f(inp.get("resistance_pa_s_m3_per_m", 0.0))
    if rp < 0:
        raise ValueError("`resistance_pa_s_m3_per_m` must be >= 0")
    line = Line(length, area, rho, c, rp)
    c_acc = _f(inp.get("accumulator_compliance_m3_pa", 0.0))
    if c_acc < 0:
        raise ValueError("`accumulator_compliance_m3_pa` must be >= 0")
    both_open = bool(inp.get("both_ends_open", False))
    modes = _organ_pipe(c, length, int(inp.get("n_modes", 3)), both_open)
    values: Dict[str, Any] = {
        "inertance_pa_s2_m3": line.inertance,
        "compliance_m3_pa": line.compliance,
        "area_m2": area,
        "liquid_mass_kg": rho * area * length,
        "organ_pipe_hz": [round(f, 6) for f in modes],
        "first_organ_pipe_hz": round(modes[0], 9),
        "wave_travel_time_s": round(length / c, 9),
    }
    if c_acc > 0:
        # accumulator compliance + line inertance = Helmholtz resonator, f = 1/(2 pi sqrt(L C))
        f_h = 1.0 / (2.0 * math.pi * math.sqrt(line.inertance * c_acc))
        values.update({"accumulator_compliance_m3_pa": c_acc,
                       "helmholtz_hz": round(f_h, 9),
                       "helmholtz_rad_s": round(2.0 * math.pi * f_h, 9)})
    if "frequency_hz" in inp:
        f = _pos(inp, "frequency_hz")
        m = line.matrix(2j * math.pi * f)
        values.update({"frequency_hz": f,
                       "transfer_P2_P1_abs": round(float(abs(m[0, 0])), 9),
                       "transfer_P2_Q1_abs": round(float(abs(m[0, 1])), 9),
                       "characteristic_impedance_pa_s_m3": round(rho * c / area, 6)})
    head = (f"line {length:g} m, {area * 1e4:.1f} cm2: first acoustic mode "
            f"{modes[0]:.2f} Hz, inertance {line.inertance:.3g} Pa s2/m3")
    return result(values, inp.get("limits"), head,
                  notes=["uniform line, liquid treated as a 1-D compressible column",
                         "the organ-pipe frequencies assume a rigid end condition: a real line "
                         "ends on a pump with a cavitation compliance, which lowers them",
                         "no two-phase flow, no gas ingestion, no bellows dynamics"])


# --------------------------------------------------------------------------- the POGO loop
class Loop:
    """The POGO loop gain L(s): thrust -> structure -> tank acceleration -> line -> pump ->
    chamber -> thrust. The closed-loop characteristic equation is 1 + L(s) = 0.

    Blocks, each one explicit so that the result can be defended:

    1. structure  q_n(s) = phi_n(engine) dF / (M_n (s^2 + 2 zeta_n omega_n s + omega_n^2))
    2. tank       dP_in = -rho g_eff... here: the acceleration of the tank bottom times the
                  effective liquid column, dP_in(s) = rho h_column * a_tank(s),
                  a_tank = s^2 sum phi_n(tank) q_n
    3. line       dP_pump = H_line(s) dP_in, lossy line closed by the pump impedance, with an
                  accumulator compliance shunting the inlet
    4. pump       dP_discharge = Kp e^(-s tau_pump) dP_pump
    5. chamber    dPc = dP_discharge / (1 + s tau_chamber)
    6. thrust     dF = (F / Pc) dPc
    """

    def __init__(self, inp: Dict[str, Any]):
        self.modes = _read_modes(inp)
        feed = inp.get("feed") or {}
        self.model = str(feed.get("model", "line")).lower()
        if self.model not in ("line", "pure_gain"):
            raise ValueError("`feed.model` must be `line` or `pure_gain`")
        if self.model == "pure_gain":
            # a feed system reduced to two real gains, used to validate the loop machinery:
            # dF = -(K x + B xdot) at the engine station
            self.k_gain = _f(feed.get("thrust_per_displacement_N_m", 0.0))
            self.b_gain = _f(feed.get("thrust_per_velocity_N_s_m", 0.0))
        else:
            rho = _pos(feed, "density_kg_m3")
            c = _pos(feed, "sound_speed_m_s")
            length = _pos(feed, "length_m")
            area = (_pos(feed, "area_m2") if "area_m2" in feed
                    else math.pi / 4.0 * _pos(feed, "diameter_m") ** 2)
            self.line = Line(length, area, rho, c,
                             _f(feed.get("resistance_pa_s_m3_per_m", 0.0)))
            self.h_col = _pos(feed, "column_height_m")
            self.rho = rho
            self.c_acc = _f(feed.get("accumulator_compliance_m3_pa", 0.0))
            pump = inp.get("pump") or {}
            self.kp = _f(pump.get("gain", 1.0))
            self.tau_p = _f(pump.get("delay_s", 0.0))
            self.cb = _f(pump.get("cavitation_compliance_m3_pa", 0.0))
            eng = inp.get("engine") or {}
            self.thrust = _pos(eng, "thrust_n")
            self.pc = _pos(eng, "chamber_pressure_pa")
            self.tau_c = _f(eng.get("chamber_time_constant_s", 0.0))
            for name, v in (("pump.delay_s", self.tau_p), ("pump.cavitation_compliance_m3_pa", self.cb),
                            ("engine.chamber_time_constant_s", self.tau_c),
                            ("feed.accumulator_compliance_m3_pa", self.c_acc)):
                if v < 0:
                    raise ValueError(f"`{name}` must be >= 0")

    def structure(self, s: complex) -> complex:
        """Transfer from a thrust perturbation to the tank-bottom ACCELERATION."""
        acc = 0.0 + 0.0j
        for om, ze, mn, phe, pht in self.modes:
            den = s * s + 2.0 * ze * om * s + om * om
            acc += (phe * pht / mn) * (s * s) / den
        return acc

    def displacement(self, s: complex) -> complex:
        """Transfer from a thrust perturbation to the engine-station DISPLACEMENT."""
        d = 0.0 + 0.0j
        for om, ze, mn, phe, _pht in self.modes:
            d += (phe * phe / mn) / (s * s + 2.0 * ze * om * s + om * om)
        return d

    def char(self, s: complex, scale: float = 1.0) -> complex:
        """(1 + L(s)) times the open-loop modal denominators, normalised to O(1).

        1 + L(s) has a POLE at every open-loop modal pole, right next to the closed-loop root we
        are looking for, and a secant iteration on it simply oscillates. Multiplying by the
        denominators removes those poles and leaves an analytic function with the same roots --
        for a feed system without delay it is exactly the characteristic polynomial.
        """
        num = 1.0 + self.gain(s, scale)
        for om, ze, _mn, _pe, _pt in self.modes:
            num = num * (s * s + 2.0 * ze * om * s + om * om) / (om * om)
        return num

    def gain(self, s: complex, scale: float = 1.0) -> complex:
        if self.model == "pure_gain":
            return (self.k_gain + self.b_gain * s) * scale * self.displacement(s)
        # tank: the column above the line inlet converts acceleration into pressure
        p_in = self.rho * self.h_col * self.structure(s)
        z_pump = (1.0 / (s * self.cb)) if (self.cb > 0 and s != 0) else complex(1e18)
        h_line = _line_gain(self.line, s, self.c_acc, z_pump)
        pump = self.kp * scale * cmath.exp(-s * self.tau_p)
        chamber = 1.0 / (1.0 + s * self.tau_c)
        thrust = self.thrust / self.pc
        # the loop is NEGATIVE feedback in the sign convention used here: a positive pressure
        # perturbation raises the chamber pressure and the thrust, which pushes the vehicle up
        return -(p_in * h_line * pump * chamber * thrust)


def _line_gain(line: Line, s: complex, c_acc: float, z_pump: complex) -> complex:
    """Pump-inlet pressure for a unit inlet pressure, line closed by `z_pump`, accumulator of
    compliance `c_acc` in parallel at the inlet (it lowers the inlet impedance seen by the tank).

    With the accumulator the inlet is loaded by its admittance s*c_acc, so the pressure that
    actually reaches the line inlet is divided by (1 + s c_acc Z_in), Z_in being the input
    impedance of the line terminated by the pump.
    """
    if s == 0:
        return 1.0 + 0.0j
    m = line.matrix(s)
    # terminated line: P2 = z_pump Q2 gives Q1/P1 = -(m00 - z_pump m10)/(m01 - z_pump m11)
    num = m[0, 0] - z_pump * m[1, 0]
    den = m[0, 1] - z_pump * m[1, 1]
    if den == 0:
        return complex(float("nan"))
    y_in = -num / den                       # input admittance Q1/P1
    z_in = (1.0 / y_in) if y_in != 0 else complex(1e18)
    p1 = 1.0 / (1.0 + s * c_acc * z_in) if c_acc > 0 else 1.0 + 0.0j
    q1 = y_in * p1
    return complex(m[0, 0] * p1 + m[0, 1] * q1)


def _read_modes(inp: Dict[str, Any]) -> List[Tuple[float, float, float, float, float]]:
    raw = inp.get("modes")
    if not isinstance(raw, list) or not raw:
        raise ValueError("`modes` must be a non-empty list of longitudinal structural modes")
    out = []
    for i, md in enumerate(raw):
        if "frequency_hz" in md:
            om = 2.0 * math.pi * _pos(md, "frequency_hz")
        else:
            om = _pos(md, "frequency_rad_s")
        ze = _f(md.get("damping_ratio", 0.005))
        if not 0.0 <= ze < 1.0:
            raise ValueError(f"mode {i}: `damping_ratio` must be in [0, 1)")
        mn = _pos(md, "modal_mass_kg")
        phe = _f(md.get("phi_engine", 1.0))
        pht = _f(md.get("phi_tank", md.get("phi_engine", 1.0)))
        out.append((om, ze, mn, phe, pht))
    return out


def _closed_loop_pole(loop: Loop, om0: float, ze0: float, scale: float,
                      seed: Optional[complex] = None) -> Optional[complex]:
    """Root of 1 + L(s) = 0 belonging to the mode whose open-loop pole is at
    s0 = -ze0 om0 + i om0 sqrt(1 - ze0^2).

    Solved on `Loop.char` (the same roots, without the open-loop poles) by a secant iteration in
    the complex plane: with a transport delay in the loop the equation is not a polynomial. An
    unstable root sits in the RIGHT half plane while s0 is in the left one, so several start
    points around s0 are tried, and among the roots found the one closest in FREQUENCY to the
    open-loop mode is kept -- which is how a mode is tracked from one gain to the next.
    """
    s0 = complex(-ze0 * om0, om0 * math.sqrt(max(0.0, 1.0 - ze0 * ze0)))

    def f(s: complex) -> complex:
        try:
            return loop.char(s, scale)
        except ZeroDivisionError:
            return complex(float("inf"))

    starts: List[complex] = [s0] if seed is None else [seed, s0]
    for rad in (0.05, 0.2, 0.5):
        for ang in (0.0, 0.5, 1.0, 1.5):          # around the open-loop pole
            starts.append(s0 + rad * om0 * cmath.exp(1j * math.pi * ang))
    roots: List[complex] = []
    for s_init in starts:
        s1, s2 = s_init, s_init * (1.0 + 1e-3) + 1e-9j
        f1, f2 = f(s1), f(s2)
        if not (cmath.isfinite(f1) and cmath.isfinite(f2)):
            continue
        for _ in range(200):
            if f2 == f1:
                break
            s3 = s2 - f2 * (s2 - s1) / (f2 - f1)
            if not cmath.isfinite(s3) or abs(s3) > 1e4 * om0:
                break
            step = abs(s3 - s2)
            s1, f1, s2 = s2, f2, s3
            f2 = f(s2)
            if not cmath.isfinite(f2):
                break
            if step <= 1e-11 * max(1.0, abs(s2)):
                break
        if cmath.isfinite(f2) and abs(f2) < 1e-8 and abs(s2.imag) > 1e-6 * om0:
            roots.append(s2 if s2.imag > 0 else s2.conjugate())
    if not roots:
        return None
    ref = seed if seed is not None else s0
    return min(roots, key=lambda s: abs(abs(s.imag) - abs(ref.imag)))


def _pole_damping(s: complex) -> Tuple[float, float]:
    """(frequency rad/s, damping ratio) of a complex pole."""
    mag = abs(s)
    if mag == 0:
        return 0.0, 0.0
    return mag, float(-s.real / mag)


def _margins(loop: Loop, n_decade: int = 400):
    """Classical stability margins of the loop on the imaginary axis.

    The characteristic equation is 1 + L(s) = 0, so the critical point is L = -1:
    - gain margin: at the frequency where L is real and negative (phase crossover), the factor
      1/|L| by which the loop gain may be multiplied before reaching it. This IS the number
      launcher programmes quote as the POGO gain margin;
    - phase margin: at the frequency where |L| = 1, how much phase lag is left before -180 deg.

    The grid is logarithmic, refined around every structural mode because the loop gain peaks
    there and a coarse grid can step straight over a crossing.
    """
    oms = []
    om_hi = 8.0 * max(md[0] for md in loop.modes)
    om_lo = 0.02 * min(md[0] for md in loop.modes)
    n = max(200, int(n_decade * math.log10(om_hi / om_lo)))
    oms.extend(float(x) for x in np.logspace(math.log10(om_lo), math.log10(om_hi), n))
    for om, ze, *_ in loop.modes:           # refine +/- 20 % around each mode
        w = max(0.2, 8.0 * ze)
        oms.extend(float(x) for x in np.linspace(om * (1.0 - w * 0.5), om * (1.0 + w * 0.5), 600)
                   if x > 0)
    oms = sorted(set(oms))
    vals = []
    for om in oms:
        try:
            vals.append(loop.gain(complex(0.0, om)))
        except ZeroDivisionError:
            vals.append(complex(float("nan")))
    gm = None
    gm_om = None
    pm = None
    pm_om = None
    for i in range(len(oms) - 1):
        a, b = vals[i], vals[i + 1]
        if not (cmath.isfinite(a) and cmath.isfinite(b)):
            continue
        # phase crossover: the imaginary part changes sign while the real part is negative
        if (a.imag == 0.0 or (a.imag < 0) != (b.imag < 0)) and (a.real < 0 or b.real < 0):
            tt = 0.0 if a.imag == b.imag else a.imag / (a.imag - b.imag)
            lr = a.real + tt * (b.real - a.real)
            if lr < 0:
                cand = 1.0 / abs(lr)
                if gm is None or cand < gm:
                    gm = cand
                    gm_om = oms[i] + tt * (oms[i + 1] - oms[i])
        # gain crossover: |L| passes through 1
        if (abs(a) - 1.0) * (abs(b) - 1.0) < 0:
            tt = (1.0 - abs(a)) / (abs(b) - abs(a))
            om_c = oms[i] + tt * (oms[i + 1] - oms[i])
            ph = math.degrees(cmath.phase(a + tt * (b - a)))
            cand = 180.0 - abs(ph)
            if pm is None or cand < pm:
                pm, pm_om = cand, om_c
    return gm, gm_om, pm, pm_om


def _min_damping(loop: Loop, scale: float, seeds=None):
    """Least damping ratio over the modes at this loop gain, with the closed-loop poles.

    `seeds` are the poles found at a nearby gain: following them keeps the search reliable when
    the gain is swept, because a pole can wander far from its open-loop position.
    """
    poles = []
    worst = float("inf")
    lost = False
    for i, (om, ze, _mn, _pe, _pt) in enumerate(loop.modes):
        seed = seeds[i] if (seeds and i < len(seeds)) else None
        s = _closed_loop_pole(loop, om, ze, scale, seed)
        poles.append(s)
        if s is None:
            lost = True            # one mode untracked makes the whole answer unusable
            continue
        worst = min(worst, _pole_damping(s)[1])
    return (float("nan") if (lost or worst == float("inf")) else worst), poles


def _run_pogo(inp, work):
    loop = Loop(inp)
    worst, poles = _min_damping(loop, 1.0)
    fz = [(_pole_damping(s) if s is not None else (float("nan"), float("nan"))) for s in poles]
    values = {
        "open_loop_frequencies_hz": [round(om / (2 * math.pi), 6) for om, *_ in loop.modes],
        "closed_loop_frequencies_hz": [round(f / (2 * math.pi), 6) for f, _ in fz],
        "closed_loop_damping_ratios": [round(z, 12) for _, z in fz],
        "closed_loop_frequency_rad_s": round(fz[0][0], 12) if fz else None,
        "closed_loop_damping_ratio": round(fz[0][1], 12) if fz else None,
        "min_damping_ratio": round(worst, 12) if math.isfinite(worst) else None,
        "stable": 1.0 if (math.isfinite(worst) and worst > 0) else 0.0,
        "damping_change_vs_open_loop": [round(z - md[1], 12) for (_, z), md in zip(fz, loop.modes)],
    }
    gm, bound, lost = _gain_margin(loop, worst, poles)
    if gm is not None:
        values["gain_margin"] = round(gm, 6)
        values["gain_margin_db"] = round(20.0 * math.log10(gm), 4) if gm > 0 else None
    elif math.isfinite(worst):
        values["gain_margin_at_least"] = round(bound, 4)
        values["gain_margin_search"] = "pole lost" if lost else "no crossing within x1000"
    nq, nq_om, pm, pm_om = _margins(loop)
    if nq is not None:
        values["nyquist_gain_margin"] = round(nq, 6)
        values["phase_crossover_hz"] = round(nq_om / (2 * math.pi), 6)
    if pm is not None:
        values["phase_margin_deg"] = round(pm, 4)
        values["gain_crossover_hz"] = round(pm_om / (2 * math.pi), 6)
    out = work / "pogo_modes.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["mode", "open_loop_hz", "open_loop_damping_ratio",
                     "closed_loop_hz", "closed_loop_damping_ratio"])
        for i, ((om, ze, *_), (f, z)) in enumerate(zip(loop.modes, fz)):
            wr.writerow([i + 1, f"{om / (2 * math.pi):.6f}", f"{ze:.6f}",
                         f"{f / (2 * math.pi):.6f}", f"{z:.8f}"])
    if math.isfinite(worst):
        head = (f"POGO loop {'stable' if values['stable'] else 'UNSTABLE'}: least damped mode "
                f"{worst * 100:.3f} % of critical")
    else:
        head = "POGO loop: a closed-loop pole could not be tracked"
    if gm:
        head += f", gain margin x{gm:.2f}"
    elif math.isfinite(worst) and lost:
        head += (f", still damped at x{bound:.1f} of the nominal gain, beyond which the solver "
                 f"lost the pole: treat x{bound:.1f} as a LOWER BOUND on the margin")
    elif math.isfinite(worst):
        head += ", no zero-damping crossing within a factor 1000 of the nominal gain"
    return result(values, inp.get("limits"), head, files=[str(out)],
                  notes=["linear, one frozen flight point, one line and one engine",
                         "modal damping is an assumption, not a measurement: 0.5 % is the usual "
                         "launcher value and the answer is sensitive to it",
                         "`gain_margin` is the factor on the loop gain that zeroes the damping "
                         "of the least damped mode: below about 2 (6 dB) is where launcher "
                         "programmes fit an accumulator. `nyquist_gain_margin` is the classical "
                         "loop margin and is a different, much larger number here",
                         "no chamber instability (chug, buzz, screech) is covered here"])


def _gain_margin(loop: Loop, worst: float, seeds=None):
    """Factor on the loop gain that brings the least damped mode to exactly zero damping.

    This is the number a POGO analysis is run for: "how much worse could the pump gain be before
    a mode stops being damped". Above 1 for a stable loop, below 1 for one already unstable.

    It is NOT the classical Nyquist gain margin, and for a lightly damped mode the two differ by
    orders of magnitude: the Nyquist margin measures the distance from -1 along the imaginary
    axis, while what destabilises a POGO mode is the loop gain AT THE POLE. Both are reported.

    The gain is walked in small steps, each seeded with the poles of the previous one, because a
    jump of several decades loses the pole entirely; the crossing is then bisected. None when no
    Returns (margin, bound, lost): `margin` when the crossing was found, otherwise `bound` is
    the largest gain at which every mode was still damped and `lost` says whether the search
    stopped because a pole could no longer be tracked (it can migrate far at high gain) rather
    than because no crossing exists within a factor 1000.
    """
    if not math.isfinite(worst):
        return None, 1.0, True
    up = worst > 0
    step = 1.25 if up else 1.0 / 1.25
    g_prev, s_prev, g = 1.0, seeds, 1.0
    for _ in range(32):                        # 1.25^32 is about 1e3
        g *= step
        w, s = _min_damping(loop, g, s_prev)
        if not math.isfinite(w):
            # the pole left the neighbourhood: all we can honestly say is that the loop was
            # still damped at the previous gain
            return None, g_prev, True
        if (w > 0) != up:                      # crossing between g_prev and g
            # `lo` is the STABLE end of the bracket and `hi` the unstable one, whichever way
            # the walk went; the bisection keeps that invariant
            lo, hi = (g_prev, g) if up else (g, g_prev)
            s_lo = s_prev if up else s
            for _ in range(60):
                mid = math.sqrt(lo * hi)
                wm, sm = _min_damping(loop, mid, s_lo)
                if not math.isfinite(wm):
                    return None, lo, True
                if wm > 0:
                    lo, s_lo = mid, sm
                else:
                    hi = mid
                if hi / lo < 1.0 + 1e-10:
                    break
            return math.sqrt(lo * hi), None, False
        g_prev, s_prev = g, s
    return None, g_prev, False


def _run_accumulator(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """Smallest accumulator compliance that reaches the target gain margin."""
    target = _pos(inp, "target_gain_margin", 2.0)
    feed = dict(inp.get("feed") or {})
    if str(feed.get("model", "line")).lower() != "line":
        raise ValueError("accumulator sizing needs `feed.model` = `line`")
    lo, hi = 0.0, _f(inp.get("max_compliance_m3_pa", 1e-5))
    if hi <= 0:
        raise ValueError("`max_compliance_m3_pa` must be > 0")

    def margin(c_acc: float) -> float:
        t = dict(inp)
        t["feed"] = dict(feed, accumulator_compliance_m3_pa=c_acc)
        lp = Loop(t)
        w, poles = _min_damping(lp, 1.0)
        if not math.isfinite(w) or w <= 0:
            return 0.0           # already unstable at the nominal gain: no margin at all
        gm, bound, _lost = _gain_margin(lp, w, poles)
        # no crossing found: the best honest value is the largest gain still fully damped
        return gm if gm else max(bound, 1.0)

    m0, mhi = margin(0.0), margin(hi)
    values: Dict[str, Any] = {"gain_margin_without_accumulator": round(m0, 6),
                              "gain_margin_at_max_compliance": round(mhi, 6),
                              "target_gain_margin": target}
    if m0 >= target:
        values["required_compliance_m3_pa"] = 0.0
        head = f"no accumulator needed: gain margin is already x{m0:.2f}"
    elif mhi < target:
        values["required_compliance_m3_pa"] = None
        head = (f"target x{target:g} not reached even at {hi:g} m3/Pa "
                f"(x{mhi:.2f}): the loop needs another fix (line, pump, or structure)")
    else:
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if margin(mid) >= target:
                hi = mid
            else:
                lo = mid
            if (hi - lo) <= 1e-12 + 1e-6 * hi:
                break
        c_req = hi
        line = Line(_pos(feed, "length_m"),
                    (_pos(feed, "area_m2") if "area_m2" in feed
                     else math.pi / 4.0 * _pos(feed, "diameter_m") ** 2),
                    _pos(feed, "density_kg_m3"), _pos(feed, "sound_speed_m_s"))
        f_h = 1.0 / (2.0 * math.pi * math.sqrt(line.inertance * c_req))
        values.update({"required_compliance_m3_pa": c_req,
                       "helmholtz_hz_with_accumulator": round(f_h, 6),
                       "gain_margin_with_accumulator": round(margin(c_req), 6)})
        head = f"accumulator of {c_req:.3g} m3/Pa reaches a gain margin of x{target:g}"
    return result(values, inp.get("limits"), head,
                  notes=["a compliance in m3/Pa is what the hardware must provide: a gas-filled "
                         "bellows of volume V at pressure p gives about V/(gamma p)",
                         "sizing at one flight point only; check the whole trajectory",
                         "the accumulator also has mass, a resonance of its own and a failure "
                         "mode (gas ingestion): this is a first sizing, not a design"])


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fns = {"feedline": _run_feedline, "pogo_stability": _run_pogo, "accumulator": _run_accumulator}
    if kind not in fns:
        raise ValueError(f"unknown kind {kind!r}; known: {', '.join(sorted(fns))}")
    return fns[kind](inp, Path(work))


# --------------------------------------------------------------------------- references
# 1. Pure stiffness feedback. One mode (m, c, k) with dF = -K x at the engine station, with
#    phi = 1 and modal mass m: the closed loop is m s^2 + c s + (k + K), so
#    omega = sqrt((k+K)/m) and zeta = c/(2 sqrt((k+K) m)) exactly.
_M1, _Z1, _OM1 = 1000.0, 0.01, 2.0 * math.pi * 10.0      # 1 t, 1 %, 10 Hz
_K1 = _M1 * _OM1 ** 2
_C1 = 2.0 * _Z1 * _OM1 * _M1
_KG = 0.44 * _K1                                          # thrust per displacement, N/m
_OM_CL = math.sqrt((_K1 + _KG) / _M1)
_Z_CL = _C1 / (2.0 * math.sqrt((_K1 + _KG) * _M1))

# 2. Pure velocity feedback (negative damping), the mechanism POGO actually destabilises with:
#    dF = -B xdot gives m s^2 + (c - B) s + k, so zeta = (c - B)/(2 sqrt(k m)) -- negative when
#    B > c -- and the gain margin, the factor on B that zeroes the damping, is exactly c/B.
_B2 = 2.5 * _C1
_Z_CL2 = (_C1 - _B2) / (2.0 * math.sqrt(_K1 * _M1))
_GM2 = _C1 / _B2

# 3. A line of water: quarter-wave frequency c/(4l) and the Helmholtz frequency of the line
#    inertance with an accumulator compliance, 1/(2 pi sqrt(L C)).
_L3, _D3, _RHO3, _C3 = 6.0, 0.15, 1000.0, 1200.0
_A3 = math.pi / 4.0 * _D3 ** 2
_IN3 = _RHO3 * _L3 / _A3
_CA3 = 2.0e-8
_F_QW = _C3 / (4.0 * _L3)
_F_HELM = 1.0 / (2.0 * math.pi * math.sqrt(_IN3 * _CA3))

REFERENCES = [
    {   # closed-loop root of a pure stiffness feedback: exact
        "name": "pure_stiffness_feedback", "kind": "pogo_stability",
        "inp": {"modes": [{"frequency_rad_s": _OM1, "damping_ratio": _Z1, "modal_mass_kg": _M1,
                           "phi_engine": 1.0, "phi_tank": 1.0}],
                "feed": {"model": "pure_gain", "thrust_per_displacement_N_m": _KG}},
        "expect": {"closed_loop_frequency_rad_s": [_OM_CL, 1e-8],
                   "closed_loop_damping_ratio": [_Z_CL, 1e-8],
                   "stable": [1.0, 1e-9]},
    },
    {   # negative damping: the closed-loop damping ratio and the gain margin are exact
        "name": "negative_damping_feedback", "kind": "pogo_stability",
        "inp": {"modes": [{"frequency_rad_s": _OM1, "damping_ratio": _Z1, "modal_mass_kg": _M1,
                           "phi_engine": 1.0, "phi_tank": 1.0}],
                "feed": {"model": "pure_gain", "thrust_per_velocity_N_s_m": -_B2}},
        "expect": {"closed_loop_damping_ratio": [_Z_CL2, 1e-7],
                   "closed_loop_frequency_rad_s": [_OM1, 1e-6],
                   "stable": [0.0, 1e-9], "gain_margin": [_GM2, 1e-5]},
    },
    {   # line hydraulics against closed forms
        "name": "water_line_acoustics", "kind": "feedline",
        "inp": {"density_kg_m3": _RHO3, "sound_speed_m_s": _C3, "length_m": _L3,
                "diameter_m": _D3, "accumulator_compliance_m3_pa": _CA3},
        "expect": {"first_organ_pipe_hz": [_F_QW, 1e-9],
                   "inertance_pa_s2_m3": [_IN3, 1e-9],
                   "compliance_m3_pa": [_A3 * _L3 / (_RHO3 * _C3 ** 2), 1e-9],
                   "helmholtz_hz": [_F_HELM, 1e-9],
                   "wave_travel_time_s": [_L3 / _C3, 1e-9]},
    },
    {   # a lossless line at zero frequency must pass the pressure through unchanged, and the
        # transfer matrix must be the identity: a check of the matrix itself at a known point
        "name": "line_transfer_half_wave", "kind": "feedline",
        "inp": {"density_kg_m3": _RHO3, "sound_speed_m_s": _C3, "length_m": _L3,
                "diameter_m": _D3, "both_ends_open": True, "frequency_hz": _C3 / (2.0 * _L3)},
        # at the half-wave frequency gamma*l = i*pi, so cosh = cos(pi) = -1 and sinh = 0:
        # |P2/P1| = 1 and the cross term vanishes
        "expect": {"transfer_P2_P1_abs": [1.0, 1e-9], "transfer_P2_Q1_abs": [0.0, 1e-6],
                   "first_organ_pipe_hz": [_C3 / (2.0 * _L3), 1e-9],
                   "characteristic_impedance_pa_s_m3": [_RHO3 * _C3 / _A3, 1e-9]},
    },
]
