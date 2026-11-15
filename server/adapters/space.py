"""Small-satellite orbits, manoeuvres and radiative thermal balance (env `space`).

Kinds (no geometry; every input is a number in `inp`):
- orbit       Keplerian/circular/sun-synchronous orbit: period, velocities, J2 secular rates,
              sun-synchronous inclination, eclipse fraction (cylindrical shadow), ground-track
              repeat; optional numerical propagation (`propagate`) with GMAT (batch script run by
              GmatConsole) or Orekit (point mass / J2 only): final state, altitude decay, lifetime
- delta_v     Hohmann and bi-elliptic transfers, plane changes (alone or combined with the apogee
              burn), rocket equation: propellant for a delta-v budget (Isp, dry mass) or the
              delta-v of a given propellant load
- sat_thermal steady radiative balance (sun, albedo, Earth IR, internal power): hot (sunlit) and
              cold (eclipse) equilibrium temperatures, orbit-averaged temperatures, single node,
              optionally one node per face of a box coupled to an internal node

Units: km, km/s, s, deg for orbits (`alt_km` above the equatorial radius, `a_km`, `i_deg`...);
delta-v in km/s (`dv_*_km_s`) and m/s for budgets (`dv_m_s`); Isp in s (g0 = 9.80665 m/s^2);
masses kg; areas m^2; fluxes W/m^2; power W; conductance W/K; temperatures K (`_K`) and degC (`_C`).
Epoch for GMAT: UTC Gregorian 'DD Mon YYYY HH:MM:SS.sss' (default the J2000 epoch, so that the
EarthMJ2000Eq frame and the pole of date coincide for J2).

Earth model (EGM-96/JGM-2, as GMAT's gravity files): mu 398600.4415 km^3/s^2, R 6378.1363 km,
J2 1.08262668e-3, flattening 1/298.257223563, rotation 7.2921158553e-5 rad/s.

Limits (stated in each result): secular J2 rates are first order and take the given elements as
mean elements; the shadow is a cylinder (no penumbra, spherical Earth); drag needs an epoch and
uses constant F10.7 / Kp (no space-weather forecast, no download); thermal is steady state (no
thermal mass: the sunlit and eclipse temperatures bound the real swing, the orbit average is the
thermal-mass-dominated limit), albedo taken at the sub-solar point (conservative), no internal
radiation between faces. Re-entry: propagation stops at `reentry_alt_km` (120 km default).
Orekit runs without its data files: TAI time scale, EME2000 frame, point mass or J2 only.

Exactness: REFERENCES below, closed-form or textbook answers (Vallado, Curtis, Wertz).
"""
from __future__ import annotations

import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from _common import result, run_exe, tool

ENV = "space"
KINDS = {
    "orbit": {"timeout": 1800,
              "doc": "period, velocities, J2 rates, SSO inclination, eclipse, repeat; GMAT/Orekit propagation"},
    "delta_v": {"timeout": 60,
                "doc": "Hohmann / bi-elliptic transfers, plane change, rocket-equation propellant"},
    "sat_thermal": {"timeout": 60,
                    "doc": "radiative balance hot/cold/orbit-average temperatures, single or multi-node"},
}

MU = 398600.4415                      # km^3/s^2
RE = 6378.1363                        # km
J2 = 4.84165390e-4 * math.sqrt(5.0)   # JGM-2 normalised C20 -> J2
FLAT = 1.0 / 298.257223563
W_EARTH = 7.2921158553e-5             # rad/s
SSO_RATE = 2 * math.pi / (365.2421897 * 86400.0)   # rad/s, one turn per tropical year
SIGMA = 5.670374419e-8                # W/(m^2 K^4)
G0 = 9.80665                          # m/s^2
J2000_UTC = "01 Jan 2000 11:58:55.816"
ATMOSPHERES = ("JacchiaRoberts", "MSISE90", "NRLMSISE00", "Exponential")


def versions() -> Dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version
    out: Dict[str, str] = {}
    for p in ("numpy", "scipy", "orekit_jpype"):
        try:
            out[p] = version(p)
        except PackageNotFoundError:
            out[p] = "missing"
    try:
        m = re.search(r"R\d{4}[a-z]?", tool("gmat"))
        out["gmat"] = m.group(0) if m else "?"
    except FileNotFoundError:
        out["gmat"] = "missing"
    return out


def _f(d: Dict[str, Any], key: str, default: Optional[float] = None) -> float:
    v = d.get(key, default)
    if v is None:
        raise ValueError(f"{key} is required")
    v = float(v)
    if not math.isfinite(v):
        raise ValueError(f"{key} must be finite")
    return v


# ------------------------------------------------------------------ orbit: elements and rates
def _elements(inp: Dict[str, Any], mu: float) -> Dict[str, float]:
    """a_km, e, i_deg, raan_deg, argp_deg, ta_deg from the accepted input forms."""
    if "a_km" in inp:
        a, e = _f(inp, "a_km"), _f(inp, "e", 0.0)
    elif "perigee_alt_km" in inp or "apogee_alt_km" in inp:
        rp, ra = RE + _f(inp, "perigee_alt_km"), RE + _f(inp, "apogee_alt_km")
        if ra < rp:
            raise ValueError("apogee_alt_km < perigee_alt_km")
        a, e = (rp + ra) / 2, (ra - rp) / (ra + rp)
    elif "alt_km" in inp:
        a, e = RE + _f(inp, "alt_km"), _f(inp, "e", 0.0)
    else:
        raise ValueError("orbit: give alt_km (circular), a_km (+ e) or perigee_alt_km + apogee_alt_km")
    if not 0 <= e < 1:
        raise ValueError("e must be in [0, 1): closed orbits only")
    if a * (1 - e) < RE:
        raise ValueError(f"perigee radius {a * (1 - e):.1f} km is inside the Earth")
    if inp.get("sun_synchronous"):
        i = _sso_inclination(a, e, mu)
        if i is None:
            raise ValueError(f"no sun-synchronous inclination for a = {a:.1f} km, e = {e:g}")
    else:
        i = _f(inp, "i_deg", 0.0)
    return {"a_km": a, "e": e, "i_deg": i, "raan_deg": _f(inp, "raan_deg", 0.0),
            "argp_deg": _f(inp, "argp_deg", 0.0), "ta_deg": _f(inp, "ta_deg", 0.0)}


def _rates(a: float, e: float, i_deg: float, mu: float) -> Dict[str, float]:
    """First-order secular J2 rates (Vallado 4th ed. eq. 9-41, Curtis 4.47/4.52), rad/s."""
    n = math.sqrt(mu / a ** 3)
    p = a * (1 - e * e)
    k = J2 * (RE / p) ** 2
    ci, si = math.cos(math.radians(i_deg)), math.sin(math.radians(i_deg))
    raan = -1.5 * n * k * ci
    argp = 0.75 * n * k * (4 - 5 * si * si)
    nbar = n * (1 + 0.75 * k * math.sqrt(1 - e * e) * (2 - 3 * si * si))
    return {"n": n, "raan": raan, "argp": argp, "nbar": nbar,
            "nodal_period": 2 * math.pi / (nbar + argp)}


def _sso_inclination(a: float, e: float, mu: float) -> Optional[float]:
    n = math.sqrt(mu / a ** 3)
    c = -SSO_RATE / (1.5 * n * J2 * (RE / (a * (1 - e * e))) ** 2)
    return math.degrees(math.acos(c)) if -1 <= c <= 1 else None


def _revs_per_day(a: float, e: float, i_deg: float, mu: float) -> Tuple[float, float, float]:
    """(revolutions per nodal day, nodal period s, nodal day s)."""
    r = _rates(a, e, i_deg, mu)
    day = 2 * math.pi / (W_EARTH - r["raan"])
    return day / r["nodal_period"], r["nodal_period"], day


def _repeat(a, e, i_deg, mu, max_days: int, tol_km: float) -> Dict[str, Any]:
    rpd, tn, day = _revs_per_day(a, e, i_deg, mu)
    best = None
    for d in range(1, max_days + 1):
        n = round(rpd * d)
        # ground-track shift at the equator after d nodal days (km, + = eastward)
        shift = (d * rpd - n) * tn * (W_EARTH - _rates(a, e, i_deg, mu)["raan"]) * RE
        cand = {"repeat_revs": n, "repeat_days": d, "repeat_shift_km": shift}
        if best is None or abs(shift) < abs(best["repeat_shift_km"]) - 1e-9:
            best = cand
        if abs(shift) <= tol_km:
            return cand
    return best


def _solve_repeat(revs: int, days: int, e: float, i_deg: Optional[float], mu: float) -> Tuple[float, float]:
    """Semi-major axis (and inclination) of an exact revs/days repeat (SSO when i_deg is None)."""
    from scipy.optimize import brentq

    def inc(a):
        return _sso_inclination(a, e, mu) if i_deg is None else i_deg

    def g(a):
        i = inc(a)
        if i is None:
            return float("nan")
        rpd, _, _ = _revs_per_day(a, e, i, mu)
        return rpd - revs / days

    lo, hi = RE + 150.0, RE + 45000.0
    if i_deg is None:
        hi = RE + 5900.0                # no sun-synchronous orbit much above ~6000 km
    a = brentq(g, lo, hi, xtol=1e-9, maxiter=200)
    return a, inc(a)


# ------------------------------------------------------------------ orbit: eclipse
def _kepler_E(M: np.ndarray, e: float) -> np.ndarray:
    E = M.copy() if e < 0.8 else np.full_like(M, math.pi)
    for _ in range(50):
        d = (E - e * np.sin(E) - M) / (1 - e * np.cos(E))
        E -= d
        if np.max(np.abs(d)) < 1e-14:
            break
    return E


def _eclipse_numeric(a: float, e: float, beta_deg: float, phase_deg: float, n: int = 20000) -> float:
    """Fraction of the period in the cylindrical shadow: uniform time samples of the orbit in
    the perifocal frame, shadow function interpolated linearly across each entry/exit."""
    M = np.linspace(0, 2 * math.pi, n, endpoint=False)
    E = _kepler_E(M, e)
    x = a * (np.cos(E) - e)
    y = a * math.sqrt(1 - e * e) * np.sin(E)
    b, ph = math.radians(beta_deg), math.radians(phase_deg)
    s = np.array([math.cos(b) * math.cos(ph), math.cos(b) * math.sin(ph), math.sin(b)])
    d = x * s[0] + y * s[1]
    r2 = x * x + y * y
    perp = np.sqrt(np.maximum(r2 - d * d, 0.0))
    f = np.where(d < 0, perp - RE, np.sqrt(r2))          # < 0 in shadow
    f2 = np.roll(f, -1)
    frac = np.where((f < 0) & (f2 < 0), 1.0, 0.0)
    cross = (f < 0) != (f2 < 0)
    neg = np.where(f < 0, -f, -f2)
    frac = np.where(cross, neg / (np.abs(f) + np.abs(f2)), frac)
    return float(frac.mean())


def _eclipse_circular(r: float, beta_deg: float) -> float:
    """Closed form, circular orbit, cylindrical shadow (Wertz SMAD 3rd ed. sec. 5.1; Gilmore 2002 ch. 2)."""
    cb = math.cos(math.radians(beta_deg))
    x = math.sqrt(r * r - RE * RE) / (r * cb) if cb > 0 else 2.0
    return math.acos(x) / math.pi if x < 1 else 0.0


# ------------------------------------------------------------------ orbit: propagation
def _plan(P: float, T: float, per_orbit: int = 36, max_mid: int = 2000) -> List[Tuple[int, float]]:
    """Sampling segments (count, step s): fine first and last orbit, coarse in between."""
    fine = P / per_orbit
    if T <= 3 * P:
        n = max(1, math.ceil(T / fine - 1e-9))
        return [(n, T / n)]
    mid = T - 2 * P
    nm = min(max_mid, max(1, math.ceil(mid / fine)))
    return [(per_orbit, fine), (nm, mid / nm), (per_orbit, fine)]


_COLS = ("Sat.ElapsedSecs Sat.EarthMJ2000Eq.X Sat.EarthMJ2000Eq.Y Sat.EarthMJ2000Eq.Z "
         "Sat.EarthMJ2000Eq.VX Sat.EarthMJ2000Eq.VY Sat.EarthMJ2000Eq.VZ Sat.Earth.SMA Sat.Earth.ECC "
         "Sat.EarthMJ2000Eq.INC Sat.EarthMJ2000Eq.RAAN Sat.EarthMJ2000Eq.AOP Sat.Earth.TA "
         "Sat.Earth.Altitude")


def _gmat_script(el, pr, plan, report: Path) -> str:
    epoch = str(pr.get("epoch_utc", J2000_UTC))
    if not re.fullmatch(r"\d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2}\.\d{3}", epoch):
        raise ValueError("epoch_utc: 'DD Mon YYYY HH:MM:SS.sss', e.g. '01 Jan 2026 00:00:00.000'")
    grav = str(pr.get("gravity", "j2")).lower()
    if grav not in ("point_mass", "j2"):
        raise ValueError("gravity: point_mass or j2")
    degree = 0 if grav == "point_mass" else 2
    drag = pr.get("drag")
    reentry = _f(pr, "reentry_alt_km", 120.0)
    P = 2 * math.pi * math.sqrt(el["a_km"] ** 3 / MU)
    max_step = min(600.0, max(10.0, P / 100))
    L = ["Create Spacecraft Sat;", "Sat.DateFormat = UTCGregorian;", f"Sat.Epoch = '{epoch}';",
         "Sat.CoordinateSystem = EarthMJ2000Eq;", "Sat.DisplayStateType = Keplerian;",
         f"Sat.SMA = {el['a_km']!r};", f"Sat.ECC = {el['e']!r};", f"Sat.INC = {el['i_deg']!r};",
         f"Sat.RAAN = {el['raan_deg']!r};", f"Sat.AOP = {el['argp_deg']!r};",
         f"Sat.TA = {el['ta_deg']!r};",
         f"Sat.DryMass = {_f(pr, 'mass_kg', 1.0)!r};", f"Sat.Cd = {_f(pr, 'cd', 2.2)!r};",
         f"Sat.DragArea = {_f(pr, 'area_m2', 0.01)!r};",
         "Create ForceModel FM;", "FM.CentralBody = Earth;", "FM.PrimaryBodies = {Earth};",
         "FM.SRP = Off;", "FM.RelativisticCorrection = Off;", "FM.ErrorControl = RSSStep;",
         f"FM.GravityField.Earth.Degree = {degree};", "FM.GravityField.Earth.Order = 0;",
         "FM.GravityField.Earth.PotentialFile = 'JGM2.cof';",
         "FM.GravityField.Earth.TideModel = 'None';"]
    if drag:
        atm = next((m for m in ATMOSPHERES if m.lower() == str(drag).lower()), None)
        if atm is None:
            raise ValueError(f"drag: one of {', '.join(ATMOSPHERES)} (or none)")
        L.append(f"FM.Drag.AtmosphereModel = {atm};")
        if atm != "Exponential":
            L += ["FM.Drag.HistoricWeatherSource = 'ConstantFluxAndGeoMag';",
                  "FM.Drag.PredictedWeatherSource = 'ConstantFluxAndGeoMag';",
                  f"FM.Drag.F107 = {_f(pr, 'f107', 150.0)!r};",
                  f"FM.Drag.F107A = {_f(pr, 'f107a', _f(pr, 'f107', 150.0))!r};",
                  f"FM.Drag.MagneticIndex = {_f(pr, 'kp', 3.0)!r};"]
    else:
        L.append("FM.Drag = None;")
    L += ["Create Propagator Prop;", "Prop.FM = FM;", "Prop.Type = RungeKutta89;",
          "Prop.InitialStepSize = 10;", f"Prop.Accuracy = {1e-12 if drag else 1e-13!r};",
          "Prop.MinStep = 0;", f"Prop.MaxStep = {max_step!r};", "Prop.MaxStepAttempts = 50;",
          "Create ReportFile rf;", f"rf.Filename = '{report}';", "rf.Precision = 17;",
          "rf.WriteHeaders = false;", "rf.LeftJustify = On;", "rf.ZeroFill = Off;",
          "rf.FixedWidth = false;", "rf.Delimiter = ' ';", "rf.WriteReport = false;",
          "Create Variable i;", "BeginMissionSequence;", f"Report rf {_COLS};"]
    guard = reentry + 0.5
    for n, dt in plan:
        L += ["i = 0;", f"While i < {n} & Sat.Earth.Altitude > {guard!r}",
              f"   Propagate Prop(Sat) {{Sat.ElapsedSecs = {dt!r}, Sat.Earth.Altitude = {reentry!r}}};",
              f"   Report rf {_COLS};", "   i = i + 1;", "EndWhile;"]
    return "\n".join(L) + "\n"


def _run_gmat(el, pr, plan, work: Path, timeout: float) -> np.ndarray:
    gmat = tool("gmat")
    work = Path(work).resolve()          # GMAT runs from its own bin folder: absolute paths only
    if "'" in str(work):
        raise ValueError("the study folder path holds a quote: GMAT cannot take it")
    script, report, log = work / "orbit.script", work / "gmat_report.txt", work / "gmat_log.txt"
    report.unlink(missing_ok=True)
    script.write_text(_gmat_script(el, pr, plan, report), encoding="utf-8")
    proc = run_exe([gmat, "--logfile", str(log), "--verbose", "off", "--run", str(script)],
                   work, timeout)
    out = proc.stdout + proc.stderr
    try:
        out += log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    # the atmosphere model stops below its floor: the run before that point is a re-entry
    floor_hit = "not available for altitudes below" in out
    if (proc.returncode != 0 and not floor_hit) or not report.is_file():
        errs = [ln.strip() for ln in out.splitlines() if "ERROR" in ln.upper()][:3]
        raise RuntimeError(f"GMAT failed (code {proc.returncode}): " + (" | ".join(errs) or out[-400:]))
    rows = [ln.split() for ln in report.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip()]
    data = np.array([[float(x) for x in r] for r in rows if len(r) == 14])
    if data.ndim != 2 or len(data) < 2 or not np.all(np.isfinite(data)):
        raise RuntimeError("GMAT report unreadable or not finite")
    return data, floor_hit


_JVM = {"up": False}


def _orekit():
    if not _JVM["up"]:
        jhome = Path(sys.prefix) / "Library" / "lib" / "jvm"
        jvm = jhome / "bin" / "server" / "jvm.dll"
        if not jvm.is_file():
            raise FileNotFoundError("no JVM (openjdk) in the space environment")
        os.environ["JAVA_HOME"] = str(jhome)
        import orekit_jpype
        orekit_jpype.initVM(jvmpath=str(jvm))
        _JVM["up"] = True


def _run_orekit(el, pr, plan) -> np.ndarray:
    if pr.get("drag"):
        raise ValueError("engine orekit: point_mass or j2 only (drag needs Orekit data files: use GMAT)")
    grav = str(pr.get("gravity", "j2")).lower()
    if grav not in ("point_mass", "j2"):
        raise ValueError("gravity: point_mass or j2")
    _orekit()
    from org.hipparchus.ode.nonstiff import DormandPrince853Integrator
    from org.orekit.bodies import OneAxisEllipsoid
    from org.orekit.forces.gravity import J2OnlyPerturbation
    from org.orekit.frames import FramesFactory
    from org.orekit.orbits import KeplerianOrbit, OrbitType, PositionAngleType
    from org.orekit.propagation import SpacecraftState
    from org.orekit.propagation.numerical import NumericalPropagator
    from org.orekit.time import AbsoluteDate, TimeScalesFactory
    tai = TimeScalesFactory.getTAI()
    t0 = AbsoluteDate(2000, 1, 1, 12, 0, 32.0, tai)          # J2000 (TT 12:00)
    fr = FramesFactory.getEME2000()
    mu = MU * 1e9
    rad = math.radians
    orb = KeplerianOrbit(el["a_km"] * 1e3, el["e"], rad(el["i_deg"]), rad(el["argp_deg"]),
                         rad(el["raan_deg"]), rad(el["ta_deg"]), PositionAngleType.TRUE, fr, t0, mu)
    prop = NumericalPropagator(DormandPrince853Integrator(1e-3, 300.0, 1e-7, 1e-14))
    prop.setOrbitType(OrbitType.CARTESIAN)
    prop.setMu(mu)
    prop.setInitialState(SpacecraftState(orb))
    if grav == "j2":
        prop.addForceModel(J2OnlyPerturbation(mu, RE * 1e3, J2, fr))
    earth = OneAxisEllipsoid(RE * 1e3, FLAT, fr)      # geodetic height: rotation does not matter

    def row(t, st):
        o = KeplerianOrbit(st.getOrbit())
        p, v = st.getPosition(), st.getPVCoordinates().getVelocity()
        h = earth.transform(p, fr, st.getDate()).getAltitude()
        return [t, p.getX() / 1e3, p.getY() / 1e3, p.getZ() / 1e3, v.getX() / 1e3, v.getY() / 1e3,
                v.getZ() / 1e3, o.getA() / 1e3, o.getE(), math.degrees(o.getI()),
                math.degrees(o.getRightAscensionOfAscendingNode()) % 360,
                math.degrees(o.getPerigeeArgument()) % 360,
                math.degrees(o.getTrueAnomaly()) % 360, h / 1e3]

    rows = [row(0.0, prop.getInitialState())]
    t = 0.0
    for n, dt in plan:
        for _ in range(n):
            t += dt
            rows.append(row(t, prop.propagate(t0.shiftedBy(float(t)))))
    return np.array(rows)


def _prop_stats(d: np.ndarray, P: float, reentry: float, floor_hit: bool = False) -> Dict[str, Any]:
    t = d[:, 0]
    v: Dict[str, Any] = {}
    last = d[-1]
    v["final_t_s"] = float(t[-1])
    v["final_state_km_km_s"] = [float(x) for x in last[1:7]]
    v["final_r_km"] = float(np.linalg.norm(last[1:4]))
    v["final_v_km_s"] = float(np.linalg.norm(last[4:7]))
    v["final_alt_km"] = float(last[13])
    v["final_sma_km"], v["final_ecc"] = float(last[7]), float(last[8])
    v["final_inc_deg"], v["final_raan_deg"] = float(last[9]), float(last[10])
    v["start_end_distance_m"] = float(np.linalg.norm(last[1:4] - d[0, 1:4]) * 1e3)
    v["min_alt_km"] = float(d[:, 13].min())
    v["reentered"] = bool(last[13] <= reentry + 1e-3 or floor_hit)
    if v["reentered"]:
        v["lifetime_days"] = float(t[-1] / 86400)
    T = t[-1]
    if T >= 2 * P - 1e-6:
        a = t < P - 1e-6
        b = t > T - P + 1e-6
        if a.sum() >= 8 and b.sum() >= 8:
            raan = np.degrees(np.unwrap(np.radians(d[:, 10])))
            tc = (t[b].mean() - t[a].mean())
            v["mean_alt_start_km"] = float(d[a, 13].mean())
            v["mean_alt_end_km"] = float(d[b, 13].mean())
            v["alt_decay_km"] = v["mean_alt_start_km"] - v["mean_alt_end_km"]
            v["sma_decay_km"] = float(d[a, 7].mean() - d[b, 7].mean())
            v["sma_decay_km_day"] = float(v["sma_decay_km"] / tc * 86400)
            v["raan_drift_num_deg_day"] = float((raan[b].mean() - raan[a].mean()) / tc * 86400)
    return v


# ------------------------------------------------------------------ orbit kind
def _run_orbit(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    mu = _f(inp, "mu_km3_s2", MU)
    el = _elements(inp, mu)
    a, e, i = el["a_km"], el["e"], el["i_deg"]
    P = 2 * math.pi * math.sqrt(a ** 3 / mu)
    rp, ra = a * (1 - e), a * (1 + e)
    v: Dict[str, Any] = {k: el[k] for k in el}
    v.update(period_s=P, period_min=P / 60, n_rad_s=math.sqrt(mu / a ** 3),
             alt_perigee_km=rp - RE, alt_apogee_km=ra - RE,
             v_perigee_km_s=math.sqrt(mu * (2 / rp - 1 / a)),
             v_apogee_km_s=math.sqrt(mu * (2 / ra - 1 / a)),
             v_circular_km_s=math.sqrt(mu / a), v_escape_perigee_km_s=math.sqrt(2 * mu / rp))
    r = _rates(a, e, i, mu)
    day = 86400 * 180 / math.pi
    v.update(raan_dot_deg_day=r["raan"] * day, argp_dot_deg_day=r["argp"] * day,
             nodal_period_s=r["nodal_period"])
    sso = _sso_inclination(a, e, mu)
    if sso is not None:
        v["sso_inclination_deg"] = sso
    rpd, _, _ = _revs_per_day(a, e, i, mu)
    v["revs_per_day"] = rpd
    v.update(_repeat(a, e, i, mu, int(inp.get("repeat_max_days", 30)),
                     _f(inp, "repeat_tol_km", 10.0)))
    if inp.get("repeat"):
        rq = inp["repeat"]
        aa, ii = _solve_repeat(int(rq["revs"]), int(rq["days"]), e,
                               None if inp.get("sun_synchronous") else i, mu)
        v.update(repeat_solved_a_km=aa, repeat_solved_alt_km=aa - RE, repeat_solved_i_deg=ii)
    beta = _f(inp, "beta_deg", 0.0)
    v["beta_deg"] = beta
    v["eclipse_fraction"] = _eclipse_numeric(a, e, beta, _f(inp, "sun_phase_deg", 0.0))
    v["eclipse_max_min"] = v["eclipse_fraction"] * P / 60
    if e < 1e-9:
        v["eclipse_fraction_closed"] = _eclipse_circular(a, beta)
    head = (f"orbit a {a:.3f} km e {e:.5g} i {i:.4f} deg: period {P / 60:.3f} min, "
            f"RAAN drift {v['raan_dot_deg_day']:.4f} deg/day, eclipse {v['eclipse_fraction']:.3f} "
            f"(beta {beta:g} deg), {rpd:.4f} rev/day")
    notes = ["J2 rates first order, elements taken as mean; cylindrical shadow"]
    files: List[str] = []
    pr = inp.get("propagate")
    if pr:
        if "duration_s" in pr:
            T = _f(pr, "duration_s")
        elif "duration_days" in pr:
            T = _f(pr, "duration_days") * 86400
        else:
            T = _f(pr, "duration_periods", 1.0) * P
        if not 0 < T <= 3.2e8:
            raise ValueError("propagation duration: (0, 10 years]")
        plan = _plan(P, T)
        engine = str(pr.get("engine", "gmat")).lower()
        if engine not in ("gmat", "orekit", "both"):
            raise ValueError("engine: gmat, orekit or both")
        reentry = _f(pr, "reentry_alt_km", 120.0)
        timeout = float(inp.get("timeout_s", 1700))
        data, floor_hit = None, False
        if engine in ("gmat", "both"):
            data, floor_hit = _run_gmat(el, pr, plan, work, timeout)
            files += ["orbit.script", "gmat_report.txt"]
        if engine in ("orekit", "both"):
            odata = _run_orekit(el, pr, plan)
            if data is not None:
                m = min(len(data), len(odata))
                v["gmat_orekit_diff_m"] = float(np.max(np.linalg.norm(
                    data[:m, 1:4] - odata[:m, 1:4], axis=1)) * 1e3)
            else:
                data = odata
        st = _prop_stats(data, P, reentry, floor_hit)
        v.update(st)
        drag = pr.get("drag")
        head += (f"; {engine} {pr.get('gravity', 'j2')}{' + drag ' + str(drag) if drag else ''} "
                 f"over {T / 86400:.4g} d: final alt {st['final_alt_km']:.3f} km")
        if "alt_decay_km" in st:
            head += f", mean altitude decay {st['alt_decay_km']:.4g} km"
        if st["reentered"]:
            head += f", RE-ENTERED after {st['lifetime_days']:.3g} d"
        if drag:
            notes.append(f"drag {drag}: constant F10.7 {pr.get('f107', 150)} / Kp {pr.get('kp', 3)}, "
                         f"Cd {pr.get('cd', 2.2)}, A {pr.get('area_m2', 0.01)} m2, m {pr.get('mass_kg', 1.0)} kg; "
                         "real decay follows the solar cycle (factor 2-10)")
    return result(v, inp.get("limits"), head, files, notes)


# ------------------------------------------------------------------ delta_v kind
def _radius(d: Dict[str, Any], r_key: str, alt_key: str) -> float:
    if r_key in d:
        return _f(d, r_key)
    return RE + _f(d, alt_key)


def _hohmann(r1: float, r2: float, mu: float, di_deg: float = 0.0) -> Dict[str, float]:
    """Vallado 4th ed. algorithm 36 (Hohmann), combined plane change at the far burn."""
    at = (r1 + r2) / 2
    vc1, vc2 = math.sqrt(mu / r1), math.sqrt(mu / r2)
    vt1, vt2 = math.sqrt(mu * (2 / r1 - 1 / at)), math.sqrt(mu * (2 / r2 - 1 / at))
    out = {"dv1_km_s": abs(vt1 - vc1), "dv2_km_s": abs(vc2 - vt2),
           "tof_h": math.pi * math.sqrt(at ** 3 / mu) / 3600, "a_transfer_km": at}
    out["dv_total_km_s"] = out["dv1_km_s"] + out["dv2_km_s"]
    if di_deg:
        di = math.radians(di_deg)
        # the plane change is done with the burn at the larger radius (the slower one)
        vf, vt = (vc2, vt2) if r2 >= r1 else (vc1, vt1)
        far = math.sqrt(vt * vt + vf * vf - 2 * vt * vf * math.cos(di))
        near = out["dv1_km_s"] if r2 >= r1 else out["dv2_km_s"]
        out["dv_far_combined_km_s"] = far
        out["dv_total_combined_km_s"] = near + far
        out["dv_total_separate_km_s"] = out["dv_total_km_s"] + 2 * vf * math.sin(di / 2)
    return out


def _bielliptic(r1: float, r2: float, rb: float, mu: float) -> Dict[str, float]:
    """Vallado 4th ed. algorithm 37."""
    a1, a2 = (r1 + rb) / 2, (rb + r2) / 2
    d1 = abs(math.sqrt(mu * (2 / r1 - 1 / a1)) - math.sqrt(mu / r1))
    d2 = abs(math.sqrt(mu * (2 / rb - 1 / a2)) - math.sqrt(mu * (2 / rb - 1 / a1)))
    d3 = abs(math.sqrt(mu * (2 / r2 - 1 / a2)) - math.sqrt(mu / r2))
    return {"dv1_km_s": d1, "dv2_km_s": d2, "dv3_km_s": d3, "dv_total_km_s": d1 + d2 + d3,
            "tof_h": (math.pi * math.sqrt(a1 ** 3 / mu) + math.pi * math.sqrt(a2 ** 3 / mu)) / 3600}


def _run_delta_v(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    mu = _f(inp, "mu_km3_s2", MU)
    v: Dict[str, Any] = {}
    parts: List[str] = []
    budget = 0.0
    used = []
    if inp.get("hohmann"):
        h = inp["hohmann"]
        r1, r2 = _radius(h, "r1_km", "from_alt_km"), _radius(h, "r2_km", "to_alt_km")
        res = _hohmann(r1, r2, mu, _f(h, "plane_change_deg", 0.0))
        v.update({f"hohmann_{k}": x for k, x in res.items()})
        dv = res.get("dv_total_combined_km_s", res["dv_total_km_s"])
        budget += dv * 1000
        used.append("hohmann")
        parts.append(f"Hohmann {r1:.3f} -> {r2:.3f} km: {res['dv_total_km_s']:.6f} km/s"
                     + (f" ({dv:.6f} with the plane change combined)" if "dv_total_combined_km_s" in res else "")
                     + f", {res['tof_h']:.4f} h")
    if inp.get("bi_elliptic"):
        b = inp["bi_elliptic"]
        r1, r2 = _radius(b, "r1_km", "from_alt_km"), _radius(b, "r2_km", "to_alt_km")
        rb = _radius(b, "rb_km", "via_alt_km")
        if rb < max(r1, r2):
            raise ValueError("bi_elliptic: the intermediate apoapsis must be above both orbits")
        res = _bielliptic(r1, r2, rb, mu)
        v.update({f"bielliptic_{k}": x for k, x in res.items()})
        if "hohmann" not in used:
            budget += res["dv_total_km_s"] * 1000
            used.append("bi_elliptic")
        parts.append(f"bi-elliptic via {rb:.0f} km: {res['dv_total_km_s']:.6f} km/s, {res['tof_h']:.4f} h")
        if "hohmann_dv_total_km_s" not in v:
            v["bielliptic_vs_hohmann_km_s"] = res["dv_total_km_s"] - _hohmann(r1, r2, mu)["dv_total_km_s"]
    if inp.get("plane_change"):
        pc = inp["plane_change"]
        vv = _f(pc, "v_km_s") if "v_km_s" in pc else math.sqrt(mu / _radius(pc, "r_km", "alt_km"))
        dv = 2 * vv * math.sin(math.radians(_f(pc, "deg")) / 2)
        v["plane_change_dv_km_s"] = dv
        budget += dv * 1000
        used.append("plane_change")
        parts.append(f"plane change {pc['deg']} deg at {vv:.4f} km/s: {dv:.6f} km/s")
    for k, x in (inp.get("extra_dv_m_s") or {}).items():
        budget += float(x)
        used.append(str(k))
    if "dv_m_s" in inp:
        budget = _f(inp, "dv_m_s")
        used = ["dv_m_s"]
    margin = _f(inp, "margin", 0.0)
    budget *= 1 + margin
    if used:
        v["dv_budget_m_s"] = budget
    pp = inp.get("propellant")
    if pp:
        isp = _f(pp, "isp_s")
        ve = isp * G0
        v["exhaust_velocity_m_s"] = ve
        if "dry_kg" in pp and used:                     # Tsiolkovsky: m0/mf = exp(dv/ve)
            dry = _f(pp, "dry_kg")
            mr = math.exp(budget / ve)
            v.update(mass_ratio=mr, propellant_kg=dry * (mr - 1), wet_kg=dry * mr)
            parts.append(f"propellant {v['propellant_kg']:.4g} kg for {budget:.1f} m/s at Isp {isp:g} s "
                         f"(dry {dry:g} kg)")
        if "wet_kg" in pp and "propellant_kg" in pp:
            wet, prop = _f(pp, "wet_kg"), _f(pp, "propellant_kg")
            if not 0 < prop < wet:
                raise ValueError("propellant: 0 < propellant_kg < wet_kg")
            v["dv_available_m_s"] = ve * math.log(wet / (wet - prop))
            parts.append(f"available {v['dv_available_m_s']:.1f} m/s from {prop:g} kg of {wet:g} kg")
    if not parts:
        raise ValueError("delta_v: give hohmann, bi_elliptic, plane_change and/or propellant")
    notes = ["impulsive burns, coplanar circular orbits unless a plane change is given"]
    if margin:
        notes.append(f"budget includes a {margin:.0%} margin")
    return result(v, inp.get("limits"), "delta_v: " + "; ".join(parts), [], notes)


# ------------------------------------------------------------------ sat_thermal kind
_FACES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0),
          "+z": (0, 0, 1), "-z": (0, 0, -1)}       # -z faces nadir
CUBESATS = {"1U": (0.1, 0.1, 0.1135), "1.5U": (0.17025, 0.1, 0.1), "2U": (0.227, 0.1, 0.1),
            "3U": (0.3405, 0.1, 0.1), "6U": (0.366, 0.2263, 0.1), "12U": (0.366, 0.2263, 0.2263)}
HOT = {"solar_W_m2": 1414.0, "albedo": 0.35, "earth_ir_W_m2": 258.0}
COLD = {"solar_W_m2": 1322.0, "albedo": 0.25, "earth_ir_W_m2": 216.0}


def _view_factors(alt_km: Optional[float]) -> Dict[str, float]:
    """Earth view factors (sphere, nadir plate, plate parallel to the local vertical, zenith)."""
    if alt_km is None:
        return {"sphere": 0.0, "nadir": 0.0, "side": 0.0, "zenith": 0.0}
    H = (RE + alt_km) / RE
    X = math.sqrt(H * H - 1)
    return {"sphere": 0.5 * (1 - X / H),                       # sphere to Earth (Gilmore 2002 ch. 2)
            "nadir": 1 / (H * H),                              # plate facing nadir
            "side": (math.atan(1 / X) - X / (H * H)) / math.pi,  # plate normal horizontal
            "zenith": 0.0}


def _nodes(inp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Radiating surfaces: area, alpha, eps, sun cosine, Earth view factor."""
    alpha, eps = _f(inp, "alpha", 0.5), _f(inp, "eps", 0.8)
    alt = inp.get("alt_km")
    F = _view_factors(None if alt is None else float(alt))
    shape = str(inp.get("shape", "box" if ("box_m" in inp or "cubesat" in inp) else "sphere")).lower()
    if shape == "sphere":
        r = _f(inp, "diameter_m", 0.1) / 2
        return [{"name": "sphere", "area": 4 * math.pi * r * r, "alpha": alpha, "eps": eps,
                 "sun_area": math.pi * r * r, "F": F["sphere"]}]
    if shape != "box":
        raise ValueError("shape: sphere or box")
    if "cubesat" in inp:
        key = str(inp["cubesat"]).upper()
        if key not in CUBESATS:
            raise ValueError(f"cubesat: one of {sorted(CUBESATS)}")
        dims = CUBESATS[key]
    else:
        dims = tuple(float(x) for x in inp["box_m"])
        if len(dims) != 3 or min(dims) <= 0:
            raise ValueError("box_m: [x, y, z] in m, all > 0")
    s = np.asarray(inp.get("sun_dir", [1, 0, 0]), dtype=float)
    if s.shape != (3,) or not np.linalg.norm(s):
        raise ValueError("sun_dir: a non-zero [x, y, z] in the body frame")
    s = s / np.linalg.norm(s)
    over = inp.get("faces") or {}
    nodes = []
    for name, n in _FACES.items():
        ax = int(np.argmax(np.abs(n)))
        area = dims[(ax + 1) % 3] * dims[(ax + 2) % 3]
        fo = over.get(name) or {}
        Fi = F["nadir"] if name == "-z" else F["zenith"] if name == "+z" else F["side"]
        nodes.append({"name": name, "area": area, "alpha": float(fo.get("alpha", alpha)),
                      "eps": float(fo.get("eps", eps)),
                      "sun_area": area * max(0.0, float(np.dot(n, s))), "F": Fi})
    return nodes


def _absorbed(nodes, env, sunlit: float) -> np.ndarray:
    """W absorbed per node; `sunlit` = 1 in the sun, 0 in eclipse, (1 - f) orbit-averaged."""
    S, al, ir = env["solar_W_m2"], env["albedo"], env["earth_ir_W_m2"]
    return np.array([sunlit * nd["alpha"] * S * (nd["sun_area"] + al * nd["F"] * nd["area"])
                     + nd["eps"] * ir * nd["F"] * nd["area"] for nd in nodes])


def _single(nodes, Q: float, Ts: float) -> float:
    ea = sum(nd["eps"] * nd["area"] for nd in nodes)
    return (Q / (SIGMA * ea) + Ts ** 4) ** 0.25


def _multi(nodes, q: np.ndarray, P: float, G: float, Ts: float) -> Tuple[np.ndarray, float]:
    """Faces radiating to space, each coupled by G (W/K) to an internal node dissipating P."""
    from scipy.optimize import fsolve
    ea = np.array([nd["eps"] * nd["area"] for nd in nodes])
    n = len(nodes)

    def res(T):
        Tf, Tc = T[:n], T[n]
        r = q - SIGMA * ea * (Tf ** 4 - Ts ** 4) - G * (Tf - Tc)
        return np.append(r, P - G * np.sum(Tc - Tf)) / max(1.0, float(q.sum() + P))

    T0 = np.full(n + 1, _single(nodes, float(q.sum()) + P, Ts))
    sol, info, ier, msg = fsolve(res, T0, full_output=True, xtol=1e-13)
    if ier != 1 or np.max(np.abs(res(sol))) > 1e-9:
        raise RuntimeError(f"multi-node solve did not converge: {msg}")
    return sol[:n], float(sol[n])


def _run_thermal(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    nodes = _nodes(inp)
    hot = dict(HOT, **(inp.get("hot") or {}))
    cold = dict(COLD, **(inp.get("cold") or {}))
    if "solar_W_m2" in inp:                       # deep-space / single-flux shortcut
        hot["solar_W_m2"] = cold["solar_W_m2"] = _f(inp, "solar_W_m2")
    Ts = _f(inp, "T_space_K", 2.7)
    p_int = _f(inp, "internal_W", 0.0)
    Ph, Pc = _f(inp, "P_hot_W", p_int), _f(inp, "P_cold_W", p_int)
    cold_sunlit = 1.0 if str(inp.get("cold_case", "eclipse")).lower() == "sunlit" else 0.0
    alt = inp.get("alt_km")
    v: Dict[str, Any] = {"area_m2": sum(nd["area"] for nd in nodes),
                         "sun_area_m2": sum(nd["sun_area"] for nd in nodes)}
    qh, qc = _absorbed(nodes, hot, 1.0), _absorbed(nodes, cold, cold_sunlit)
    v["Q_in_hot_W"], v["Q_in_cold_W"] = float(qh.sum() + Ph), float(qc.sum() + Pc)
    Th, Tc = _single(nodes, v["Q_in_hot_W"], Ts), _single(nodes, v["Q_in_cold_W"], Ts)
    v.update(T_hot_K=Th, T_cold_K=Tc, T_hot_C=Th - 273.15, T_cold_C=Tc - 273.15)
    head = (f"sat_thermal ({nodes[0]['name'] if len(nodes) == 1 else 'box'}, "
            f"{v['area_m2']:.4g} m2): hot (sunlit) {Th - 273.15:.2f} degC, "
            f"cold ({'sunlit' if cold_sunlit else 'eclipse'}) {Tc - 273.15:.2f} degC")
    notes = ["steady state, no thermal mass: real temperatures swing between these bounds"]
    if alt is not None:
        f_ecl = inp.get("eclipse_fraction")
        if f_ecl is None:
            r = RE + float(alt)
            fh = _eclipse_circular(r, _f(inp, "beta_hot_deg", _f(inp, "beta_deg", 0.0)))
            fc = _eclipse_circular(r, _f(inp, "beta_deg", 0.0))
        else:
            fh = fc = float(f_ecl)
        qah, qac = _absorbed(nodes, hot, 1 - fh), _absorbed(nodes, cold, 1 - fc)
        Tah = _single(nodes, float(qah.sum()) + Ph, Ts)
        Tac = _single(nodes, float(qac.sum()) + Pc, Ts)
        v.update(eclipse_fraction_hot=fh, eclipse_fraction_cold=fc, T_avg_hot_C=Tah - 273.15,
                 T_avg_cold_C=Tac - 273.15, T_avg_hot_K=Tah, T_avg_cold_K=Tac)
        head += f"; orbit-averaged {Tac - 273.15:.2f} .. {Tah - 273.15:.2f} degC"
        notes.append("albedo at the sub-solar point (conservative hot)")
    else:
        notes.append("no alt_km: deep space, sun only (no albedo, no Earth IR)")
    if inp.get("multi_node"):
        if len(nodes) == 1:
            raise ValueError("multi_node needs shape box (one node per face)")
        G = _f(inp, "coupling_W_K", 0.5)
        if G <= 0:
            raise ValueError("coupling_W_K must be > 0")
        for case, q, P in (("hot", qh, Ph), ("cold", qc, Pc)):
            Tf, Ti = _multi(nodes, q, P, G, Ts)
            for nd, T in zip(nodes, Tf):
                v[f"T_{nd['name']}_{case}_C"] = float(T - 273.15)
            v[f"T_internal_{case}_K"] = Ti
            v[f"T_internal_{case}_C"] = Ti - 273.15
            v[f"T_face_max_{case}_C"] = float(Tf.max() - 273.15)
            v[f"T_face_min_{case}_C"] = float(Tf.min() - 273.15)
        head += (f"; multi-node faces {v['T_face_min_cold_C']:.1f} .. {v['T_face_max_hot_C']:.1f} degC, "
                 f"internal {v['T_internal_cold_C']:.1f} .. {v['T_internal_hot_C']:.1f} degC "
                 f"(G {G:g} W/K per face)")
        notes.append("multi-node: faces radiate to space only (no face-to-face radiation)")
    return result(v, inp.get("limits"), head, [], notes)


# ------------------------------------------------------------------ entry
def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    inp = dict(inp)
    if kind == "orbit":
        return _run_orbit(inp, work)
    if kind == "delta_v":
        return _run_delta_v(inp, work)
    if kind == "sat_thermal":
        return _run_thermal(inp, work)
    raise ValueError(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")


# ------------------------------------------------------------------ reference cases (§9)
def _ref_sso(alt_km: float) -> float:
    # cos i = -dOmega_SSO / (3/2 n J2 (R/a)^2), circular (Vallado 4th ed. sec. 9.6; Wertz SMAD)
    a = RE + alt_km
    return math.degrees(math.acos(-(2 * math.pi / (365.2421897 * 86400))
                                  / (1.5 * math.sqrt(MU / a ** 3) * J2 * (RE / a) ** 2)))


def _ref_raan(alt_km: float, i_deg: float) -> float:
    a = RE + alt_km                       # deg/day, -3/2 n J2 (R/p)^2 cos i, e = 0
    return (-1.5 * math.sqrt(MU / a ** 3) * J2 * (RE / a) ** 2 * math.cos(math.radians(i_deg))
            * 86400 * 180 / math.pi)


def _ref_hohmann(r1: float, r2: float) -> float:
    at = (r1 + r2) / 2                    # Curtis 4th ed. sec. 6.2
    return ((math.sqrt(MU * (2 / r1 - 1 / at)) - math.sqrt(MU / r1))
            + (math.sqrt(MU / r2) - math.sqrt(MU * (2 / r2 - 1 / at))))


_R_GEO = (MU / W_EARTH ** 2) ** (1 / 3)
REFERENCES = [
    {   # circular LEO period 2 pi sqrt(a^3/mu) (Curtis ch. 2)
        "name": "leo_period", "kind": "orbit", "inp": {"alt_km": 400, "i_deg": 51.6},
        "expect": {"period_s": [2 * math.pi * math.sqrt((RE + 400) ** 3 / MU), 1e-12]},
    },
    {   # J2 secular RAAN drift, 400 km 51.6 deg (Vallado eq. 9-41): about -5.0 deg/day
        "name": "j2_raan_drift", "kind": "orbit", "inp": {"alt_km": 400, "i_deg": 51.6},
        "expect": {"raan_dot_deg_day": [_ref_raan(400, 51.6), 1e-12]},
    },
    {   # sun-synchronous inclination at 700 km: 98.19 deg (Wertz SMAD 3rd ed. table, closed form)
        "name": "sso_inclination_700km", "kind": "orbit", "inp": {"alt_km": 700, "sun_synchronous": True},
        "expect": {"sso_inclination_deg": [_ref_sso(700), 1e-12], "i_deg": [98.19, 5e-4]},
    },
    {   # numerical cylindrical eclipse vs closed form arcsin(R/r)/pi at beta 0, 400 km
        "name": "eclipse_fraction_400km", "kind": "orbit", "inp": {"alt_km": 400, "i_deg": 51.6},
        "expect": {"eclipse_fraction": [math.asin(RE / (RE + 400)) / math.pi, 1e-6]},
    },
    {   # same, beta 50 deg: (1/pi) acos(sqrt(h^2 + 2Rh) / ((R+h) cos beta))
        "name": "eclipse_fraction_beta50", "kind": "orbit",
        "inp": {"alt_km": 700, "sun_synchronous": True, "beta_deg": 50},
        "expect": {"eclipse_fraction": [math.acos(math.sqrt(700 ** 2 + 2 * RE * 700)
                                                  / ((RE + 700) * math.cos(math.radians(50)))) / math.pi, 1e-6]},
    },
    {   # Hohmann 300 km -> GEO, closed form (Curtis ch. 6): 3.8926 km/s
        "name": "hohmann_300km_geo", "kind": "delta_v",
        "inp": {"hohmann": {"from_alt_km": 300, "r2_km": _R_GEO}},
        "expect": {"hohmann_dv_total_km_s": [_ref_hohmann(RE + 300, _R_GEO), 1e-12]},
    },
    {   # Vallado 4th ed. Example 6-1: 191.34411 km -> 35781.34857 km: 3.935224 km/s, 5.256713 h
        "name": "vallado_ex6_1_hohmann", "kind": "delta_v",
        "inp": {"mu_km3_s2": 398600.4418,
                "hohmann": {"r1_km": 6378.137 + 191.34411, "r2_km": 6378.137 + 35781.34857}},
        "expect": {"hohmann_dv_total_km_s": [3.935224, 1e-6], "hohmann_tof_h": [5.256713, 1e-6]},
    },
    {   # Vallado 4th ed. Example 6-2: bi-elliptic 191.34411 -> 376310 km via 503873 km: 3.904057 km/s
        "name": "vallado_ex6_2_bielliptic", "kind": "delta_v",
        "inp": {"mu_km3_s2": 398600.4418,
                "bi_elliptic": {"r1_km": 6378.137 + 191.34411, "r2_km": 6378.137 + 376310,
                                "rb_km": 6378.137 + 503873}},
        "expect": {"bielliptic_dv_total_km_s": [3.904057, 1e-6], "bielliptic_tof_h": [593.92, 1e-5]},
    },
    {   # plane change 2 v sin(di/2), 10 deg at 500 km; rocket equation dry (e^(dv/g0 Isp) - 1)
        "name": "plane_change_rocket_eq", "kind": "delta_v",
        "inp": {"plane_change": {"deg": 10, "alt_km": 500},
                "propellant": {"isp_s": 220, "dry_kg": 10}},
        "expect": {"plane_change_dv_km_s": [2 * math.sqrt(MU / (RE + 500)) * math.sin(math.radians(5)), 1e-12],
                   "propellant_kg": [10 * (math.exp(2 * math.sqrt(MU / (RE + 500)) * math.sin(math.radians(5))
                                                    * 1000 / (220 * 9.80665)) - 1), 1e-12]},
    },
    {   # GMAT two-body, one period: back to the start, position error < 1 m
        "name": "gmat_two_body_period", "kind": "orbit",
        "inp": {"a_km": 7000, "e": 0.01, "i_deg": 51.6, "raan_deg": 10, "argp_deg": 20, "ta_deg": 30,
                "propagate": {"duration_periods": 1, "gravity": "point_mass", "engine": "gmat"}},
        "expect": {"start_end_distance_m": [0.0, 1.0]},
    },
    {   # Orekit (no data files) two-body, one period: position error < 1 m
        "name": "orekit_two_body_period", "kind": "orbit",
        "inp": {"a_km": 7000, "e": 0.01, "i_deg": 51.6, "raan_deg": 10, "argp_deg": 20, "ta_deg": 30,
                "propagate": {"duration_periods": 1, "gravity": "point_mass", "engine": "orekit"}},
        "expect": {"start_end_distance_m": [0.0, 1.0]},
    },
    {   # sphere in sunlight, deep space: T = (alpha S / (4 eps sigma))^(1/4) (Gilmore 2002 ch. 2)
        "name": "sphere_equilibrium", "kind": "sat_thermal",
        "inp": {"shape": "sphere", "diameter_m": 0.3, "alpha": 0.6, "eps": 0.8, "solar_W_m2": 1361,
                "T_space_K": 0},
        "expect": {"T_hot_K": [(0.6 * 1361 / (4 * 0.8 * 5.670374419e-8)) ** 0.25, 1e-12]},
    },
    {   # box 0.1 x 0.1 x 0.2 m, sun on +x, faces tightly coupled: T^4 = alpha S A_x / (eps sigma A)
        "name": "box_multinode_equilibrium", "kind": "sat_thermal",
        "inp": {"box_m": [0.1, 0.1, 0.2], "alpha": 0.5, "eps": 0.5, "solar_W_m2": 1361,
                "T_space_K": 0, "sun_dir": [1, 0, 0], "multi_node": True, "coupling_W_K": 1e5},
        "expect": {"T_internal_hot_K": [(1361 * 0.02 / (5.670374419e-8 * 0.1)) ** 0.25, 1e-6],
                   "T_hot_K": [(1361 * 0.02 / (5.670374419e-8 * 0.1)) ** 0.25, 1e-12]},
    },
]
