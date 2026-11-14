"""Aerodynamics, gas dynamics, rocket propulsion and flight, fluid properties (env `aero`).

Kinds (no geometry; every input is a number in `inp`):
- airfoil        NeuralFoil polar of a 2-D section (AeroSandbox geometry: NACA/UIUC name or coordinates)
- wing           AeroSandbox vortex-lattice (inviscid: CL, induced drag, span efficiency) or AeroBuildup
- isentropic     exact isentropic-flow ratios from a Mach, area, pressure or temperature ratio
- nozzle         ideal rocket nozzle, constant gamma: exit state, c*, CF, Isp, optimum expansion
- combustion     Cantera chemical equilibrium (HP) of a liquid/gas propellant pair + CEA-style expansion
- rocket_flight  RocketPy 6-DOF flight to apogee, standard atmosphere only (offline)
- fluid_props    CoolProp (HEOS) properties of a named fluid at T, p

Units: SI unless the key says otherwise. Angles in degrees (`alpha_deg`, `sweep_le_deg`, ...);
pressures in bar (`pc_bar`, `pa_bar`, `p_bar`); temperatures in K (`Tc_K`, `T_K`) or degC (`T_C`);
lengths in m (`span_m`, `radius_m`, positions measured from the TAIL of the rocket, nozzle exit = 0);
masses kg; thrust N; molar mass g/mol; enthalpy kJ/mol; Isp in s (g0 = 9.80665 m/s^2);
velocities m/s; viscosity Pa s; static margin in calibers (body diameters).

Limits (stated in each result): NeuralFoil is a neural surrogate of XFOIL (2-D, subsonic,
attached-to-mild-stall; check `min_confidence`); VLM is inviscid and linear (no stall, no profile
drag); the nozzle and combustion kinds are 1-D, isentropic, ideal (no divergence, friction, heat
loss or two-phase flow: real Isp is typically 90-97 % of ideal); combustion is gas-phase only (no
soot, no condensed water); rocket_flight has no wind and no weather download.
Out of scope: solid-propellant formulation (only gases and (L) liquids of C, H, O, N are accepted
as reactants), any weapon use.

Exactness: REFERENCES below, closed-form or published answers.
"""
from __future__ import annotations

import os

# OpenBLAS 0.3.34 (conda-forge, pthreads) crashes with an access violation in its AVX2 kernels
# (Haswell/Zen/SkylakeX) on some small matrix products (e.g. 256x256 @ 256x2..16) on this
# machine (Ryzen 7800X3D): NeuralFoil's network hits exactly that. The Sandybridge (AVX) kernels
# are correct. Must be set before numpy loads OpenBLAS.
os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")

import math  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

from _common import result  # noqa: E402

ENV = "aero"
KINDS = {
    "airfoil": {"timeout": 120,
                "doc": "NeuralFoil polar: Cl, Cd, Cm vs alpha at Re, Mach; Cl_max, L/D max, lift slope"},
    "wing": {"timeout": 300,
             "doc": "AeroSandbox VLM (CL, CDi, span efficiency) or AeroBuildup of a simple wing"},
    "isentropic": {"timeout": 60,
                   "doc": "isentropic ratios from mach / area_ratio / p_ratio / T_ratio, gamma"},
    "nozzle": {"timeout": 60,
               "doc": "ideal rocket nozzle (constant gamma): exit Mach, pe, c*, CF, Isp, optimum expansion"},
    "combustion": {"timeout": 300,
                   "doc": "Cantera equilibrium: flame T, M, gamma, c*, Isp (equilibrium + frozen expansion)"},
    "rocket_flight": {"timeout": 600,
                      "doc": "RocketPy 6-DOF to apogee: apogee, max v/a, rail exit v, static margin"},
    "fluid_props": {"timeout": 60,
                    "doc": "CoolProp: density, viscosity, cp, conductivity, saturation of a fluid at T, p"},
}

G0 = 9.80665                       # standard gravity, m/s^2
R_U = 8314.462618                  # universal gas constant, J/(kmol K)
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _\-(),.\[\]&]{0,60}")


def versions() -> Dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for p in ("neuralfoil", "aerosandbox", "cantera", "rocketpy", "coolprop",
              "numpy", "scipy"):
        try:
            out[p] = version(p)
        except PackageNotFoundError:
            out[p] = "missing"
    return out


def _f(x) -> float:
    """A plain float (numpy scalars, 1-element arrays) for JSON."""
    try:
        import numpy as np
        x = np.asarray(x, dtype=float).ravel()[0] if hasattr(x, "__len__") or hasattr(x, "dtype") else x
    except ImportError:
        pass
    return float(x)


def _num(inp, key, default=None, positive=False) -> Optional[float]:
    v = inp.get(key, default)
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: a number, got {v!r}") from None
    if not math.isfinite(v) or (positive and v <= 0):
        raise ValueError(f"{key} must be a finite{' positive' if positive else ''} number")
    return v


def _need(inp, key, positive=True) -> float:
    if inp.get(key) is None:
        raise ValueError(f"{key} is required")
    return _num(inp, key, positive=positive)


# ================================================================== airfoil (NeuralFoil)
def _airfoil(inp):
    import aerosandbox as asb
    import numpy as np
    coords = inp.get("coordinates")
    if coords is not None:
        c = np.asarray(coords, dtype=float)
        if c.ndim != 2 or c.shape[1] != 2 or len(c) < 20 or not np.isfinite(c).all():
            raise ValueError("coordinates: >= 20 [x, y] points, chord-normalised, Selig order "
                             "(TE -> upper -> LE -> lower -> TE)")
        return asb.Airfoil(name=str(inp.get("airfoil", "custom"))[:40], coordinates=c)
    name = str(inp.get("airfoil", "")).strip().lower()
    if not _NAME.fullmatch(name or "?"):
        raise ValueError("airfoil: a NACA 4/5-digit name ('naca2412') or a UIUC name, or coordinates")
    af = asb.Airfoil(name)
    if af.coordinates is None:
        raise ValueError(f"airfoil {name!r} unknown (NACA 4/5-digit or a UIUC database name), "
                         f"or pass coordinates")
    return af


def _run_airfoil(inp) -> Dict[str, Any]:
    import numpy as np
    af = _airfoil(inp)
    Re = _need(inp, "Re")
    mach = _num(inp, "mach", 0.0)
    if not 0 <= mach < 0.9:
        raise ValueError("mach: 0 <= mach < 0.9 (subsonic sections only)")
    if inp.get("alpha_deg") is not None:
        a = np.atleast_1d(np.asarray(inp["alpha_deg"], dtype=float))
    else:
        lo, hi, step = (inp.get("alpha_range") or [-5, 20, 0.5])
        a = np.arange(float(lo), float(hi) + 1e-9, float(step))
    if len(a) == 0 or len(a) > 2000 or np.abs(a).max() > 90:
        raise ValueError("alpha_deg: 1..2000 angles within +-90 deg")
    kw = dict(Re=Re, mach=mach, n_crit=_num(inp, "n_crit", 9.0),
              xtr_upper=_num(inp, "xtr_upper", 1.0), xtr_lower=_num(inp, "xtr_lower", 1.0),
              model_size=str(inp.get("model_size", "xlarge")))
    if kw["model_size"] not in ("xxsmall", "xsmall", "small", "medium", "large", "xlarge",
                                "xxlarge", "xxxlarge"):
        raise ValueError("model_size: xxsmall..xxxlarge")
    r = af.get_aero_from_neuralfoil(alpha=a, **kw)
    CL, CD, CM = (np.asarray(r[k], dtype=float) for k in ("CL", "CD", "CM"))
    conf = np.asarray(r["analysis_confidence"], dtype=float)
    LD = CL / CD
    # lift slope and zero-lift angle from a separate small-alpha fit (-2..+2 deg)
    small = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
    rs = af.get_aero_from_neuralfoil(alpha=small, **kw)
    slope, icpt = np.polyfit(np.radians(small), np.asarray(rs["CL"], dtype=float), 1)
    i_cl, i_ld = int(np.argmax(CL)), int(np.argmax(LD))
    values = {
        "airfoil": af.name, "Re": Re, "mach": mach,
        "max_thickness": round(_f(af.max_thickness()), 5), "max_camber": round(_f(af.max_camber()), 5),
        "lift_slope_per_rad": _f(slope), "lift_slope_per_deg": _f(slope) * math.pi / 180,
        "lift_slope_over_2pi": _f(slope) / (2 * math.pi),
        "alpha_L0_deg": -math.degrees(_f(icpt) / _f(slope)),
        "CL_max": _f(CL[i_cl]), "alpha_CL_max_deg": _f(a[i_cl]),
        "LD_max": _f(LD[i_ld]), "alpha_LD_max_deg": _f(a[i_ld]), "CD_min": _f(CD.min()),
        "min_confidence": _f(conf.min()),
        "mach_crit": _f(np.asarray(rs["mach_crit"])[2]),
        "polar": {"alpha_deg": [round(_f(x), 4) for x in a], "CL": [round(_f(x), 5) for x in CL],
                  "CD": [round(_f(x), 6) for x in CD], "CM": [round(_f(x), 5) for x in CM],
                  "confidence": [round(_f(x), 3) for x in conf]},
    }
    notes = ["NeuralFoil (XFOIL surrogate), 2-D; compressibility by AeroSandbox's correction"]
    if i_cl in (0, len(a) - 1) and len(a) > 1:
        notes.append("CL_max is at the end of the alpha range: not a stall peak, widen alpha_range")
    if values["min_confidence"] < 0.7:
        notes.append(f"low analysis confidence ({values['min_confidence']:.2f}) on part of the polar: "
                     "check those points with XFOIL")
    if mach > values["mach_crit"]:
        notes.append(f"mach {mach} > critical Mach {values['mach_crit']:.3f} at alpha 0: wave drag not modelled")
    head = (f"airfoil {af.name} Re {Re:.3g} M {mach:g}: CL_max {values['CL_max']:.3f} at "
            f"{values['alpha_CL_max_deg']:g} deg, L/D max {values['LD_max']:.1f} at "
            f"{values['alpha_LD_max_deg']:g} deg, CD_min {values['CD_min']:.5f}, "
            f"dCL/da {values['lift_slope_per_rad']:.3f}/rad, alpha_L0 {values['alpha_L0_deg']:.2f} deg")
    return result(values, inp.get("limits"), head, notes=notes)


# ================================================================== wing (VLM / AeroBuildup)
def _run_wing(inp) -> Dict[str, Any]:
    import aerosandbox as asb
    import numpy as np
    af = _airfoil({"airfoil": inp.get("airfoil", "naca0012"), "coordinates": inp.get("coordinates")})
    b = _need(inp, "span_m")
    planform = str(inp.get("planform", "trapezoid")).lower()
    dih = math.radians(_num(inp, "dihedral_deg", 0.0))
    if planform == "elliptic":
        c0 = _need(inp, "root_chord_m")
        ys = np.sin(np.linspace(0, np.pi / 2, 41)) * b / 2
        xsecs = []
        for y in ys:
            c = max(c0 * math.sqrt(max(1 - (2 * y / b) ** 2, 0.0)), 1e-3 * c0)
            xsecs.append(asb.WingXSec(xyz_le=[c0 / 4 - c / 4, y, y * math.tan(dih)], chord=c, airfoil=af))
        ns = 2
    elif planform == "trapezoid":
        cr = _need(inp, "root_chord_m")
        ct = _num(inp, "tip_chord_m", cr, positive=True)
        sweep = math.radians(_num(inp, "sweep_le_deg", 0.0))
        twist = _num(inp, "twist_tip_deg", 0.0)
        xsecs = [asb.WingXSec(xyz_le=[0, 0, 0], chord=cr, airfoil=af),
                 asb.WingXSec(xyz_le=[b / 2 * math.tan(sweep), b / 2, b / 2 * math.tan(dih)],
                              chord=ct, twist=twist, airfoil=af)]
        ns = 30
    else:
        raise ValueError("planform: trapezoid or elliptic")
    wing = asb.Wing(name="wing", symmetric=True, xsecs=xsecs)
    S, AR, mac = _f(wing.area()), _f(wing.aspect_ratio()), _f(wing.mean_aerodynamic_chord())
    cr0 = xsecs[0].chord
    plane = asb.Airplane(wings=[wing], s_ref=S, c_ref=mac, b_ref=b, xyz_ref=[cr0 / 4, 0, 0])
    alphas = np.atleast_1d(np.asarray(inp.get("alpha_deg", [0.0, 2.0, 4.0]), dtype=float))
    if len(alphas) > 50 or np.abs(alphas).max() > 30:
        raise ValueError("alpha_deg: up to 50 angles within +-30 deg")
    method = str(inp.get("method", "vlm")).lower()
    V = _num(inp, "velocity_m_s", 20.0, positive=True)
    atm = asb.Atmosphere(altitude=_num(inp, "altitude_m", 0.0))
    CL, CD, CM = [], [], []
    for a in alphas:
        op = asb.OperatingPoint(atmosphere=atm, velocity=V, alpha=float(a))
        if method == "vlm":
            r = asb.VortexLatticeMethod(plane, op, spanwise_resolution=ns, chordwise_resolution=8).run()
        elif method == "aerobuildup":
            r = asb.AeroBuildup(plane, op).run()
        else:
            raise ValueError("method: vlm or aerobuildup")
        CL.append(_f(r["CL"]))
        CD.append(_f(r["CD"]))
        CM.append(_f(r["Cm"]))
    values: Dict[str, Any] = {"method": method, "planform": planform, "S_ref_m2": S,
                              "aspect_ratio": AR, "mac_m": mac,
                              "alpha_deg": [float(a) for a in alphas], "CL": CL, "CD": CD, "Cm": CM}
    if len(alphas) >= 2:
        values["CL_alpha_per_rad"] = _f(np.polyfit(np.radians(alphas), CL, 1)[0])
    k = int(np.argmax(np.abs(CL)))
    if method == "vlm":
        values["CDi"] = CD[k]
        if abs(CL[k]) > 1e-6 and CD[k] > 0:
            values["span_efficiency"] = CL[k] ** 2 / (math.pi * AR * CD[k])
        head = (f"wing VLM AR {AR:.3g} S {S:.4g} m2: CL {CL[k]:.4f} at {alphas[k]:g} deg, "
                f"CDi {CD[k]:.5f}, e {values.get('span_efficiency', float('nan')):.3f}"
                + (f", CL_alpha {values['CL_alpha_per_rad']:.3f}/rad" if "CL_alpha_per_rad" in values else ""))
        notes = ["inviscid vortex lattice: no profile drag, no stall (linear); CD = induced drag"]
    else:
        values["LD"] = [c / d if d else float("nan") for c, d in zip(CL, CD)]
        head = (f"wing AeroBuildup AR {AR:.3g} V {V:g} m/s: CL {CL[k]:.4f}, CD {CD[k]:.5f} at "
                f"{alphas[k]:g} deg")
        notes = ["AeroBuildup: semi-empirical buildup (NeuralFoil sections + lifting-line corrections)"]
    return result(values, inp.get("limits"), head, notes=notes)


# ================================================================== isentropic / nozzle
def _gamma(inp) -> float:
    g = _num(inp, "gamma", 1.4)
    if not 1.0 < g <= 5 / 3 + 1e-9:
        raise ValueError("gamma: 1 < gamma <= 5/3")
    return g


def _ise_m(M: float, g: float) -> Dict[str, float]:
    """Exact isentropic relations of a calorically perfect gas (NACA Report 1135, 1953)."""
    k = 1 + (g - 1) / 2 * M * M
    out = {"m": M, "tr": 1 / k, "pr": k ** (-g / (g - 1)), "dr": k ** (-1 / (g - 1)),
           "ars": (1 / M) * (2 * k / (g + 1)) ** ((g + 1) / (2 * (g - 1))) if M > 0 else float("inf"),
           "trs": (g + 1) / 2 / k}
    out["prs"] = out["trs"] ** (g / (g - 1))
    if M >= 1:
        out["ma"] = math.degrees(math.asin(1 / M))
        a = math.sqrt((g + 1) / (g - 1))
        out["pm"] = math.degrees(a * math.atan(math.sqrt(M * M - 1) / a) - math.atan(math.sqrt(M * M - 1)))
    return out


def _ise(name: str, value: float, g: float) -> Dict[str, float]:
    """Isentropic state from one ratio. Exact closed forms, inverted with Brent's method.
    (pygasflow gives the same numbers but takes 10-60 s to import: not worth it here.)"""
    from scipy.optimize import brentq
    if name == "m":
        return _ise_m(value, g)
    if name == "pressure":
        if not 0 < value < 1:
            raise ValueError("p_ratio p/p0 must be in (0, 1)")
        return _ise_m(math.sqrt(2 / (g - 1) * (value ** (-(g - 1) / g) - 1)), g)
    if name == "temperature":
        if not 0 < value < 1:
            raise ValueError("T_ratio T/T0 must be in (0, 1)")
        return _ise_m(math.sqrt(2 / (g - 1) * (1 / value - 1)), g)
    if name in ("crit_area_super", "crit_area_sub"):
        if value < 1:
            raise ValueError("area ratio A/A* >= 1")
        if value == 1:
            return _ise_m(1.0, g)
        f = lambda M: _ise_m(M, g)["ars"] - value
        if name == "crit_area_sub":
            return _ise_m(brentq(f, 1e-9, 1.0, xtol=1e-14), g)
        hi = 2.0
        while f(hi) < 0:
            hi *= 2
        return _ise_m(brentq(f, 1.0, hi, xtol=1e-14), g)
    raise ValueError(name)


def _run_isentropic(inp) -> Dict[str, Any]:
    g = _gamma(inp)
    if inp.get("mach") is not None:
        r = _ise("m", _need(inp, "mach"), g)
    elif inp.get("area_ratio") is not None:
        branch = str(inp.get("branch", "supersonic")).lower()
        if branch not in ("supersonic", "subsonic"):
            raise ValueError("branch: supersonic or subsonic")
        ar = _need(inp, "area_ratio")
        if ar < 1:
            raise ValueError("area_ratio A/A* >= 1")
        r = _ise("crit_area_super" if branch == "supersonic" else "crit_area_sub", ar, g)
    elif inp.get("p_ratio") is not None:
        r = _ise("pressure", _need(inp, "p_ratio"), g)
    elif inp.get("T_ratio") is not None:
        r = _ise("temperature", _need(inp, "T_ratio"), g)
    else:
        raise ValueError("give one of mach, area_ratio (+branch), p_ratio (p/p0), T_ratio (T/T0)")
    values = {"gamma": g, "mach": r["m"], "p_p0": r["pr"], "T_T0": r["tr"], "rho_rho0": r["dr"],
              "A_Astar": r["ars"], "p_pstar": r["prs"], "T_Tstar": r["trs"]}
    if r["m"] >= 1:
        values["mach_angle_deg"] = r["ma"]
        values["prandtl_meyer_deg"] = r["pm"]
    head = (f"isentropic gamma {g:g}: M {r['m']:.5g}, A/A* {r['ars']:.6g}, p/p0 {r['pr']:.6g}, "
            f"T/T0 {r['tr']:.6g}, rho/rho0 {r['dr']:.6g}")
    return result(values, inp.get("limits"), head, notes=["exact relations, calorically perfect gas"])


def _cf(g: float, pe_pc: float, eps: float, pa_pc: float) -> float:
    """Ideal thrust coefficient (Sutton & Biblarz, Rocket Propulsion Elements, eq. 3-30)."""
    mom = math.sqrt(2 * g * g / (g - 1) * (2 / (g + 1)) ** ((g + 1) / (g - 1))
                    * (1 - pe_pc ** ((g - 1) / g)))
    return mom + (pe_pc - pa_pc) * eps


def _run_nozzle(inp) -> Dict[str, Any]:
    g = _gamma(inp)
    pc = _need(inp, "pc_bar")
    Tc = _need(inp, "Tc_K")
    if inp.get("R_J_kgK") is not None:
        R = _need(inp, "R_J_kgK")
    else:
        R = R_U / _need(inp, "molar_mass_g_mol")
    pa = _num(inp, "pa_bar", 1.01325)
    if pa < 0:
        raise ValueError("pa_bar >= 0")
    notes = []
    if inp.get("area_ratio") is not None:
        eps = _need(inp, "area_ratio")
        if eps < 1:
            raise ValueError("area_ratio Ae/At >= 1")
        ex = _ise("crit_area_super", eps, g)
    else:
        if pa <= 0:
            raise ValueError("no area_ratio: give pa_bar > 0 for the optimum expansion (pe = pa)")
        if pa >= pc / ((g + 1) / 2) ** (g / (g - 1)):
            raise ValueError("pa_bar too high: the nozzle would not choke (pc/pa below critical)")
        ex = _ise("pressure", pa / pc, g)
        eps = ex["ars"]
        notes.append("area_ratio = optimum expansion for pa (pe = pa)")
    pe_pc = ex["pr"]
    cstar = math.sqrt(g * R * Tc) / (g * (2 / (g + 1)) ** ((g + 1) / (2 * (g - 1))))  # S&B eq. 3-32
    CF = _cf(g, pe_pc, eps, pa / pc)
    CFv = _cf(g, pe_pc, eps, 0.0)
    ve = ex["m"] * math.sqrt(g * R * Tc * ex["tr"])
    values = {"gamma": g, "area_ratio": eps, "exit_mach": ex["m"], "pe_bar": pe_pc * pc,
              "pe_pc": pe_pc, "Te_K": Tc * ex["tr"], "ve_m_s": ve, "c_star_m_s": cstar,
              "CF": CF, "CF_vac": CFv, "Isp_s": cstar * CF / G0, "Isp_vac_s": cstar * CFv / G0,
              "pa_bar": pa}
    if pa > 0 and pa < pc / ((g + 1) / 2) ** (g / (g - 1)):
        values["optimum_area_ratio"] = _ise("pressure", pa / pc, g)["ars"]
    At = None
    if inp.get("throat_diameter_mm") is not None:
        At = math.pi * (_need(inp, "throat_diameter_mm") / 2000) ** 2
    elif inp.get("throat_area_mm2") is not None:
        At = _need(inp, "throat_area_mm2") * 1e-6
    if At:
        mdot = pc * 1e5 * At / cstar
        values.update(throat_area_m2=At, mdot_kg_s=mdot, thrust_N=CF * pc * 1e5 * At,
                      thrust_vac_N=CFv * pc * 1e5 * At)
    if pa > 0 and pe_pc * pc < 0.4 * pa:
        notes.append(f"pe = {pe_pc * pc:.3g} bar < 0.4 pa: flow separation likely in the nozzle "
                     "(Summerfield criterion); the CF above is then not reached")
    notes.append("ideal 1-D isentropic, constant gamma: no divergence, friction or heat loss "
                 "(real Isp ~90-97 % of this)")
    head = (f"nozzle gamma {g:g} eps {eps:.4g}: Me {ex['m']:.4f}, pe {pe_pc * pc:.4g} bar, "
            f"c* {cstar:.1f} m/s, CF {CF:.4f} (vac {CFv:.4f}) at pa {pa:g} bar, Isp {cstar * CF / G0:.1f} s "
            f"(vac {cstar * CFv / G0:.1f} s)")
    return result(values, inp.get("limits"), head, notes=notes)


# ================================================================== combustion (Cantera)
_MECHS = {"gri30": "gri30.yaml", "gri30_highT": "gri30_highT.yaml", "h2o2": "h2o2.yaml"}
_ALLOWED_ELEMENTS = {"C", "H", "O", "N", "Ar", "He"}


def _phase(mech: str, elements):
    import cantera as ct
    if mech == "nasa":
        sp = [s for s in ct.Species.list_from_file("nasa_gas.yaml")
              if set(s.composition) <= set(elements) and not re.search(r"[+\-]$", s.name)]
        return ct.Solution(thermo="ideal-gas", species=sp)
    if mech not in _MECHS:
        raise ValueError(f"mechanism: nasa (NASA thermo database, as CEA) or one of {sorted(_MECHS)}")
    return ct.Solution(_MECHS[mech])


def _species_db():
    import cantera as ct
    gas = {s.name: s for s in ct.Species.list_from_file("nasa_gas.yaml")}
    liq = {s.name: s for s in ct.Species.list_from_file("nasa_condensed.yaml") if s.name.endswith("(L)")}
    return gas, liq


def _lookup(name: str, mech_species: Dict[str, Any], db) -> Tuple[Any, bool]:
    """(Species, is_liquid) for a reactant name, in the mechanism first, then the NASA tables."""
    gas_db, liq_db = db
    for table, liquid in ((mech_species, False), (gas_db, False), (liq_db, True)):
        if name in table:
            return table[name], liquid
        low = {k.lower(): k for k in table}
        if name.lower() in low:
            return table[low[name.lower()]], liquid
    raise ValueError(f"reactant {name!r}: not a gas species of the mechanism or NASA database, nor a "
                     f"NASA (L) liquid (solids are out of scope)")


def _reactants(inp, mech) -> List[Dict[str, Any]]:
    """[{species, liquid, moles, T, h_J_kmol}] from `reactants` or fuel/oxidizer + of_ratio/phi."""
    import cantera as ct
    db = _species_db()
    mech_sp = {}
    if mech != "nasa":
        mech_sp = {s.name: s for s in ct.Species.list_from_file(_MECHS.get(mech, "gri30.yaml"))}

    def one(name, T, h_kJ_mol):
        sp, liq = _lookup(str(name), mech_sp, db)
        bad = set(sp.composition) - _ALLOWED_ELEMENTS
        if bad:
            raise ValueError(f"reactant {sp.name}: elements {sorted(bad)} out of scope (only C, H, O, N, "
                             f"Ar, He; no metals, no solid-propellant oxidisers)")
        if h_kJ_mol is not None:
            h = float(h_kJ_mol) * 1e6
        else:
            # 298.15 K is the standard reference even for fits starting at 300 K (GRI-Mech)
            if not min(sp.thermo.min_temp, 298.15) - 1e-6 <= T <= sp.thermo.max_temp + 1e-6:
                raise ValueError(f"{sp.name}: T {T} K outside its data range {sp.thermo.min_temp:g}-"
                                 f"{sp.thermo.max_temp:g} K (give h_kJ_mol, e.g. from NASA CEA)")
            h = sp.thermo.h(T)
        M = sum(n * _ATOMIC_MASS(el) for el, n in sp.composition.items())
        return {"species": sp, "liquid": liq, "T": T, "h": h, "M": M}

    out = []
    if inp.get("reactants"):
        for r in inp["reactants"]:
            T = float(r.get("T_K", 298.15))
            d = one(r["name"], T, r.get("h_kJ_mol"))
            if "moles" in r:
                d["moles"] = float(r["moles"])
            elif "mass" in r:
                d["moles"] = float(r["mass"]) / d["M"]
            else:
                raise ValueError("each reactant: name + moles or mass (kg)")
            out.append(d)
        return out

    def side(key):
        spec = inp.get(key)
        if spec is None:
            raise ValueError("give reactants=[...] or fuel + oxidizer (+ of_ratio or phi)")
        comp = {spec: 1.0} if isinstance(spec, str) else dict(spec)
        T = float(inp.get(f"T_{'fuel' if key == 'fuel' else 'ox'}_K", 298.15))
        h_over = inp.get(f"h_{'fuel' if key == 'fuel' else 'ox'}_kJ_mol")
        if h_over is not None and len(comp) != 1:
            raise ValueError(f"h_{key}_kJ_mol applies to a single-species {key} only")
        parts = []
        tot = sum(float(v) for v in comp.values())
        for nm, x in comp.items():
            d = one(nm, T, h_over)
            d["x"] = float(x) / tot
            parts.append(d)
        return parts

    fuel, ox = side("fuel"), side("oxidizer")

    def per_mole(parts):                   # composition and mass of 1 mol of the side
        el: Dict[str, float] = {}
        for d in parts:
            for e, n in d["species"].composition.items():
                el[e] = el.get(e, 0.0) + d["x"] * n
        return el, sum(d["x"] * d["M"] for d in parts)

    ef, Mf = per_mole(fuel)
    eo, Mo = per_mole(ox)
    need = lambda e: 2 * e.get("C", 0) + e.get("H", 0) / 2 - e.get("O", 0)   # O atoms lacking
    stoich = (need(ef) / -need(eo)) if need(eo) < 0 else float("nan")       # mol ox / mol fuel
    if inp.get("of_ratio") is not None:
        n_ox = _need(inp, "of_ratio") * Mf / Mo
    elif inp.get("phi") is not None:
        if not math.isfinite(stoich) or stoich <= 0:
            raise ValueError("phi needs a C/H/O/N fuel and an oxidising oxidizer: give of_ratio")
        n_ox = stoich / _need(inp, "phi")
    else:
        raise ValueError("give of_ratio (oxidizer/fuel mass) or phi (equivalence ratio)")
    for d in fuel:
        d["moles"] = d["x"]
    for d in ox:
        d["moles"] = d["x"] * n_ox
    inp["_of"] = n_ox * Mo / Mf
    inp["_of_stoich"] = stoich * Mo / Mf if math.isfinite(stoich) else None
    return fuel + ox


_AW: Dict[str, float] = {}


def _ATOMIC_MASS(el: str) -> float:
    if not _AW:
        import cantera as ct
        for e in _ALLOWED_ELEMENTS:
            _AW[e] = ct.Element(e).weight
    return _AW[el]


def _run_combustion(inp) -> Dict[str, Any]:
    import cantera as ct
    import numpy as np
    from scipy.optimize import brentq, minimize_scalar
    mech = str(inp.get("mechanism", "nasa"))
    reac = _reactants(inp, mech)
    elements: Dict[str, float] = {}
    H = mass = 0.0
    for d in reac:
        if d["moles"] < 0:
            raise ValueError("negative reactant amount")
        H += d["moles"] * d["h"]
        mass += d["moles"] * d["M"]
        for e, n in d["species"].composition.items():
            elements[e] = elements.get(e, 0.0) + d["moles"] * n
    h_target = H / mass                                     # J/kg
    gas = _phase(mech, elements)
    missing = set(elements) - set(gas.element_names)
    if missing:
        raise ValueError(f"mechanism {mech} lacks elements {sorted(missing)}")
    # initial composition carrying the right elements: the reactant gases, atoms for the liquids
    X0: Dict[str, float] = {}
    for d in reac:
        nm = d["species"].name
        if not d["liquid"] and nm in gas.species_names:
            X0[nm] = X0.get(nm, 0.0) + d["moles"]
        else:
            for e, n in d["species"].composition.items():
                if e not in gas.species_names:
                    raise ValueError(f"mechanism {mech} has no {e} atom species to carry {nm}")
                X0[e] = X0.get(e, 0.0) + d["moles"] * n
    pc = _need(inp, "pc_bar") * 1e5
    T_hi = min(6000.0, max(gas.max_temp, 3000.0))

    def h_eq(T):
        gas.TPX = T, pc, X0
        gas.equilibrate("TP")
        return gas.enthalpy_mass - h_target

    Tc = brentq(h_eq, 250.0, T_hi, xtol=1e-4)
    h_eq(Tc)
    Xc, s0, h0 = gas.X.copy(), gas.entropy_mass, gas.enthalpy_mass
    Mc, gam_f, cp_c = gas.mean_molecular_weight, gas.cp_mass / gas.cv_mass, gas.cp_mass

    def expand(p, mode):
        gas.SPX = s0, p, Xc
        if mode == "eq":
            gas.equilibrate("SP")
        u = math.sqrt(max(2 * (h0 - gas.enthalpy_mass), 0.0))
        return gas.density * u, u, gas.density, gas.T, gas.mean_molecular_weight

    # equilibrium isentropic exponent gamma_s = (dln p / dln rho)_s at the chamber
    r1, r2 = expand(pc * 1.001, "eq")[2], expand(pc / 1.001, "eq")[2]
    gam_s = math.log(1.001 ** 2) / math.log(r1 / r2)
    values: Dict[str, Any] = {
        "mechanism": mech, "pc_bar": pc / 1e5, "T_chamber_K": Tc, "M_chamber_g_mol": Mc,
        "gamma_frozen": gam_f, "gamma_s_equilibrium": gam_s, "cp_chamber_J_kgK": cp_c,
        "h_reactants_kJ_kg": h_target / 1e3,
    }
    if inp.get("_of") is not None:
        values["of_ratio"] = inp["_of"]
        if inp.get("_of_stoich"):
            values["of_stoich"] = inp["_of_stoich"]
            values["phi"] = inp["_of_stoich"] / inp["_of"]
    order = np.argsort(Xc)[::-1]
    values["chamber_X"] = {gas.species_names[i]: round(float(Xc[i]), 5) for i in order[:10] if Xc[i] > 1e-4}
    notes = [f"HP equilibrium at {pc / 1e5:g} bar ({mech}{' NASA thermo (as CEA), ions excluded' if mech == 'nasa' else ''}); "
             f"gas phase only"]
    if all(not d["liquid"] for d in reac) and not any(
            r.get("h_kJ_mol") is not None for r in (inp.get("reactants") or [])) \
            and inp.get("h_fuel_kJ_mol") is None and inp.get("h_ox_kJ_mol") is None:
        notes.append("reactants as GASES at their T: cryogenic liquids (LOX, LH2, LCH4) need h_kJ_mol "
                     "(NASA CEA values), Isp otherwise ~1-2 % high")
    head = f"combustion: Tc {Tc:.1f} K, M {Mc:.3f} g/mol, gamma_s {gam_s:.4f}"
    if Tc > gas.max_temp:
        notes.append(f"Tc above the mechanism's data range ({gas.max_temp:g} K): use mechanism nasa")
    eps = _num(inp, "area_ratio")
    pa = _num(inp, "pa_bar")
    if eps is not None or pa is not None or inp.get("expansion", True):
        for mode in ("eq", "frozen"):
            res = minimize_scalar(lambda p: -expand(p, mode)[0], bounds=(0.3 * pc, 0.9 * pc),
                                  method="bounded", options={"xatol": 1e-7 * pc})
            pt = float(res.x)
            Gt, ut, _, Tt, _ = expand(pt, mode)
            cstar = pc / Gt
            values[f"c_star_{mode}_m_s"] = cstar
            values[f"T_throat_{mode}_K"] = Tt
            if eps is None and pa is None:
                continue
            if eps is not None:
                if eps < 1:
                    raise ValueError("area_ratio >= 1")
                lo = pt * 0.5                       # walk down until the area ratio is passed
                while Gt / expand(lo, mode)[0] < eps:
                    lo /= 4
                    if lo < pt * 1e-8:
                        raise ValueError(f"area_ratio {eps:g} too large for this gas model")
                pe = math.exp(brentq(lambda lp: Gt / expand(math.exp(lp), mode)[0] - eps,
                                     math.log(lo), math.log(pt * 0.9999), xtol=1e-10))
                e_here = eps
            else:
                if pa * 1e5 >= pt:
                    raise ValueError("pa_bar above the throat pressure: no supersonic expansion")
                pe = pa * 1e5
                e_here = Gt / expand(pe, mode)[0]
            Ge, ue, rhoe, Te, Me = expand(pe, mode)
            vac = ue + pe / Ge                       # thrust per mass flow in vacuum, m/s
            values[f"Isp_vac_{mode}_s"] = vac / G0
            values[f"CF_vac_{mode}"] = vac / cstar
            values[f"pe_{mode}_bar"] = pe / 1e5
            values[f"Te_{mode}_K"] = Te
            values[f"area_ratio_{mode}"] = e_here
            if pa is not None:
                amb = ue + (pe - pa * 1e5) / Ge
                values[f"Isp_{mode}_s"] = amb / G0
                values[f"CF_{mode}"] = amb / cstar
            if Te < 300:
                notes.append(f"{mode} exit T {Te:.0f} K: condensation (water ice/liquid) not modelled")
        head += f", c* {values['c_star_eq_m_s']:.1f} m/s (frozen {values['c_star_frozen_m_s']:.1f})"
        if "Isp_vac_eq_s" in values:
            head += (f", Isp vac {values['Isp_vac_eq_s']:.1f} s shifting / {values['Isp_vac_frozen_s']:.1f} s "
                     f"frozen at eps {values['area_ratio_eq']:.3g}")
            if "Isp_eq_s" in values:
                head += f", Isp at {pa:g} bar {values['Isp_eq_s']:.1f} / {values['Isp_frozen_s']:.1f} s"
            notes.append("expansion: isentropic from an infinite-area chamber; shifting = equilibrium "
                         "at every station, frozen = chamber composition; throat at max mass flux "
                         "(NASA RP-1311 method); ideal (no losses)")
    return result(values, inp.get("limits"), head, notes=notes)


# ================================================================== rocket flight (RocketPy)
def _run_rocket_flight(inp, work: Path) -> Dict[str, Any]:
    import numpy as np
    from rocketpy import Environment, Flight, Function, GenericMotor, Rocket
    tc = np.asarray(inp.get("thrust_curve") or [], dtype=float)
    if tc.ndim != 2 or tc.shape[1] != 2 or len(tc) < 2 or not np.isfinite(tc).all():
        raise ValueError("thrust_curve: [[t_s, thrust_N], ...] (>= 2 points, t from 0)")
    if (np.diff(tc[:, 0]) <= 0).any() or tc[0, 0] < 0 or (tc[:, 1] < 0).any():
        raise ValueError("thrust_curve: increasing times from 0, thrust >= 0")
    burn = float(tc[-1, 0])
    mp = _need(inp, "propellant_mass_kg")
    R = _need(inp, "radius_m")
    m_dry = _need(inp, "dry_mass_kg")                 # rocket without motor
    cg = _need(inp, "cg_m", positive=False)           # rocket CG without motor, from the tail
    rm = _num(inp, "motor_radius_m", 0.8 * R, positive=True)
    lm = _num(inp, "motor_length_m", 0.3, positive=True)
    mdry_m = _num(inp, "motor_dry_mass_kg", 0.0)
    mpos = _num(inp, "motor_position_m", 0.0)         # nozzle exit, from the tail
    motor = GenericMotor(thrust_source=[(float(t), float(f)) for t, f in tc], burn_time=burn,
                         chamber_radius=rm, chamber_height=lm, chamber_position=lm / 2,
                         propellant_initial_mass=mp,
                         nozzle_radius=_num(inp, "nozzle_radius_m", 0.5 * rm, positive=True),
                         dry_mass=mdry_m, center_of_dry_mass_position=lm / 2,
                         dry_inertia=(mdry_m * (3 * rm ** 2 + lm ** 2) / 12,) * 2 + (mdry_m * rm ** 2 / 2,),
                         nozzle_position=0)
    nose = inp.get("nose") or {}
    if not nose:
        raise ValueError("nose: {length_m, kind, tip_position_m (from the tail = rocket length)}")
    L = float(nose["tip_position_m"])
    notes = []
    inertia = inp.get("inertia_kg_m2")
    if inertia is None:
        i_lat = m_dry * (3 * R ** 2 + L ** 2) / 12
        inertia = (i_lat, i_lat, m_dry * R ** 2)
        notes.append("inertia estimated as a uniform thin tube of the rocket length: give inertia_kg_m2 "
                     "[I_lateral, I_lateral, I_axial] from CAD")

    def drag(key_cd, key_curve, default):
        if inp.get(key_curve) is not None:
            c = np.asarray(inp[key_curve], dtype=float)
            if c.ndim != 2 or c.shape[1] != 2:
                raise ValueError(f"{key_curve}: [[mach, cd], ...]")
            return [(float(m), float(d)) for m, d in c]
        if inp.get(key_cd) is not None:
            return float(_num(inp, key_cd))
        return default

    cd_off = drag("cd", "drag_curve", None)
    if cd_off is None:
        raise ValueError("drag: cd (constant) or drag_curve [[mach, cd], ...] (power off)")
    cd_on = drag("cd_power_on", "drag_curve_power_on", cd_off)
    rocket = Rocket(radius=R, mass=m_dry, inertia=tuple(float(x) for x in inertia),
                    power_off_drag=cd_off, power_on_drag=cd_on, center_of_mass_without_motor=cg,
                    coordinate_system_orientation="tail_to_nose")
    rocket.add_motor(motor, position=mpos)
    rocket.add_nose(length=float(nose["length_m"]), kind=str(nose.get("kind", "vonKarman")), position=L)
    fins = inp.get("fins")
    if fins:
        rocket.add_trapezoidal_fins(n=int(fins["n"]), root_chord=float(fins["root_chord_m"]),
                                    tip_chord=float(fins["tip_chord_m"]), span=float(fins["span_m"]),
                                    position=float(fins["position_m"]),
                                    sweep_length=fins.get("sweep_length_m"))
    else:
        notes.append("no fins: the rocket is very likely unstable")
    tail = inp.get("tail")
    if tail:
        rocket.add_tail(top_radius=float(tail["top_radius_m"]), bottom_radius=float(tail["bottom_radius_m"]),
                        length=float(tail["length_m"]), position=float(tail["position_m"]))
    elev = _num(inp, "elevation_m", 0.0)
    env = Environment(gravity=_num(inp, "gravity_m_s2"), latitude=_num(inp, "latitude", 0.0),
                      longitude=0.0, elevation=elev)
    env.set_atmospheric_model(type="standard_atmosphere")
    rail = _need(inp, "rail_length_m")
    fl = Flight(rocket=rocket, environment=env, rail_length=rail,
                inclination=_num(inp, "inclination_deg", 85.0), heading=_num(inp, "heading_deg", 0.0),
                terminate_on_apogee=True, max_time=_num(inp, "max_time_s", 600.0, positive=True),
                rtol=1e-8)
    t_rail = _f(fl.out_of_rail_time)
    m0 = m_dry + mdry_m + mp
    impulse = float(np.trapezoid(tc[:, 1], tc[:, 0])) if hasattr(np, "trapezoid") else float(np.trapz(tc[:, 1], tc[:, 0]))
    values = {
        "apogee_m": _f(fl.apogee) - elev, "apogee_time_s": _f(fl.apogee_time),
        "max_speed_m_s": _f(fl.max_speed), "max_mach": _f(fl.max_mach_number),
        "max_acceleration_m_s2": _f(fl.max_acceleration), "max_acceleration_g": _f(fl.max_acceleration) / G0,
        "rail_exit_velocity_m_s": _f(fl.out_of_rail_velocity), "rail_exit_time_s": t_rail,
        "static_margin_initial_cal": _f(rocket.static_margin(0)),
        "static_margin_rail_exit_cal": _f(fl.static_margin(t_rail)),
        "static_margin_burnout_cal": _f(rocket.static_margin(burn)),
        "liftoff_mass_kg": m0, "total_impulse_Ns": impulse, "burn_time_s": burn,
        "thrust_to_weight": float(tc[:, 1].max()) / (m0 * G0),
    }
    if values["rail_exit_velocity_m_s"] < 15:
        notes.append(f"rail exit {values['rail_exit_velocity_m_s']:.1f} m/s < 15 m/s (common rule of thumb)")
    sm = values["static_margin_rail_exit_cal"]
    if sm < 1.0:
        notes.append(f"static margin {sm:.2f} cal at rail exit < 1 cal (Barrowman rule of thumb 1-2)")
    notes.append("RocketPy 6-DOF, standard atmosphere, no wind, flight to apogee only (no recovery)")
    head = (f"rocket: apogee {values['apogee_m']:.1f} m AGL at {values['apogee_time_s']:.2f} s, v max "
            f"{values['max_speed_m_s']:.1f} m/s (M {values['max_mach']:.3f}), a max "
            f"{values['max_acceleration_g']:.2f} g, rail exit {values['rail_exit_velocity_m_s']:.2f} m/s, "
            f"static margin {sm:.2f} cal at rail exit")
    return result(values, inp.get("limits"), head, notes=notes)


# ================================================================== fluid properties (CoolProp)
def _run_fluid_props(inp) -> Dict[str, Any]:
    import CoolProp.CoolProp as CP
    fluid = str(inp.get("fluid", "")).strip()
    if not _NAME.fullmatch(fluid or "?") or "::" in fluid:
        raise ValueError("fluid: a CoolProp fluid name (Water, Nitrogen, Oxygen, NitrousOxide, Methane, "
                         "Hydrogen, Air, CarbonDioxide, Ethanol, n-Dodecane...)")
    try:
        CP.PropsSI("Tcrit", fluid)
    except ValueError:
        raise ValueError(f"fluid {fluid!r} unknown to CoolProp (HEOS)") from None
    if inp.get("T_K") is not None:
        T = _need(inp, "T_K")
    elif inp.get("T_C") is not None:
        T = _need(inp, "T_C", positive=False) + 273.15
    else:
        raise ValueError("give T_C or T_K")
    p = _num(inp, "p_bar", 1.01325, positive=True) * 1e5
    values: Dict[str, Any] = {"fluid": fluid, "T_K": T, "p_bar": p / 1e5}
    notes = []
    for key, code in (("density_kg_m3", "D"), ("viscosity_Pa_s", "V"), ("cp_J_kgK", "C"),
                      ("cv_J_kgK", "O"), ("conductivity_W_mK", "L"), ("speed_of_sound_m_s", "A"),
                      ("enthalpy_J_kg", "H")):
        try:
            values[key] = CP.PropsSI(code, "T", T, "P", p, fluid)
        except ValueError as e:
            notes.append(f"{key}: not available ({str(e)[:80]})")
    try:
        values["phase"] = CP.PhaseSI("T", T, "P", p, fluid)
    except ValueError:
        pass
    Tc, pcr = CP.PropsSI("Tcrit", fluid), CP.PropsSI("pcrit", fluid)
    values.update(T_crit_K=Tc, p_crit_bar=pcr / 1e5, molar_mass_g_mol=CP.PropsSI("M", fluid) * 1e3)
    try:
        if T < Tc:
            values["p_sat_bar"] = CP.PropsSI("P", "T", T, "Q", 0, fluid) / 1e5
            values["rho_sat_liquid_kg_m3"] = CP.PropsSI("D", "T", T, "Q", 0, fluid)
            values["rho_sat_vapour_kg_m3"] = CP.PropsSI("D", "T", T, "Q", 1, fluid)
            values["h_vap_J_kg"] = (CP.PropsSI("H", "T", T, "Q", 1, fluid)
                                    - CP.PropsSI("H", "T", T, "Q", 0, fluid))
        if p < pcr:
            values["T_sat_K"] = CP.PropsSI("T", "P", p, "Q", 0, fluid)
    except ValueError as e:
        notes.append(f"saturation: not available ({str(e)[:80]})")
    parts = [f"{lab} {values[k]:{fmt}} {unit}" for lab, k, fmt, unit in (
        ("rho", "density_kg_m3", ".6g", "kg/m3"), ("mu", "viscosity_Pa_s", ".4g", "Pa s"),
        ("cp", "cp_J_kgK", ".5g", "J/(kg K)"), ("p_sat", "p_sat_bar", ".5g", "bar")) if k in values]
    head = (f"{fluid} at {T - 273.15:.2f} degC, {p / 1e5:.5g} bar ({values.get('phase', '?')}): "
            + ", ".join(parts))
    notes.append("CoolProp HEOS (reference equations of state, e.g. IAPWS-95 for water)")
    return result(values, inp.get("limits"), head, notes=notes)


# ================================================================== dispatch
def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    inp = dict(inp)
    if kind == "airfoil":
        return _run_airfoil(inp)
    if kind == "wing":
        return _run_wing(inp)
    if kind == "isentropic":
        return _run_isentropic(inp)
    if kind == "nozzle":
        return _run_nozzle(inp)
    if kind == "combustion":
        return _run_combustion(inp)
    if kind == "rocket_flight":
        return _run_rocket_flight(inp, work)
    if kind == "fluid_props":
        return _run_fluid_props(inp)
    raise ValueError(f"unknown kind {kind!r} for the aero adapter: {sorted(KINDS)}")


# ================================================================== reference cases (§9)
def _vertical_rocket(F: float, tb: float, m0: float, mp: float, g: float) -> Dict[str, float]:
    """Closed form, vertical, no drag, constant g and thrust, linear mass loss (c = F/mdot):
    v(t) = c ln(m0/m) - g t ; h(t) = c t - g t^2/2 - (c m/mdot) ln(m0/m); coast h += v_b^2/(2g)."""
    mdot = mp / tb
    c = F / mdot
    mb = m0 - mp
    vb = c * math.log(m0 / mb) - g * tb
    hb = c * tb - g * tb ** 2 / 2 - (c * mb / mdot) * math.log(m0 / mb)
    return {"apogee": hb + vb ** 2 / (2 * g), "v_burnout": vb, "a_max": F / mb - g}


_ROCKET = dict(F=200.0, tb=3.0, m0=1.5 + 0.1 + 0.3, mp=0.3, g=G0)
_RK = _vertical_rocket(**_ROCKET)
# argon (monatomic, cp = 5/2 R exactly): gamma = 5/3, and A/A* = 3 at M = 3 exactly, so
# pe/pc = (1 + M^2/3)^-5/2 = 1/32, CF_vac = 1.40625 + 3/32 = 1.5; c* = sqrt(gamma R Tc)/(gamma (3/4)^2)
_R_AR = R_U / 39.95
_CSTAR_AR = math.sqrt(5 / 3 * _R_AR * 3000.0) / (5 / 3 * 0.5625)
_ELLIPTIC_AR = 8.0
REFERENCES = [
    {   # exact: A/A* = (1/M) [(2/(g+1)) (1 + (g-1)/2 M^2)]^((g+1)/(2(g-1))) = 1.6875 at M 2, g 1.4
        # (NACA Report 1135, Equations, Tables and Charts for Compressible Flow, 1953)
        "name": "isentropic_area_ratio_M2", "kind": "isentropic", "inp": {"mach": 2.0, "gamma": 1.4},
        "expect": {"A_Astar": [1.6875, 1e-9], "p_p0": [(1 + 0.2 * 4) ** -3.5, 1e-9],
                   "prandtl_meyer_deg": [26.3798, 1e-5]},   # nu(2) tabulated, NACA 1135
    },
    {   # inverse: A/A* 1.6875 supersonic branch -> M 2 (same source)
        "name": "isentropic_inverse_area_ratio", "kind": "isentropic",
        "inp": {"area_ratio": 1.6875, "gamma": 1.4, "branch": "supersonic"},
        "expect": {"mach": [2.0, 1e-6]},
    },
    {   # ideal nozzle, gamma 5/3, eps 3: exact Me = 3, pe/pc = 1/32, CF_vac = 1.5 (see _CSTAR_AR)
        # (c*: Sutton & Biblarz, Rocket Propulsion Elements, eq. 3-32; CF: eq. 3-30)
        "name": "nozzle_gamma53_eps3", "kind": "nozzle",
        "inp": {"gamma": 5 / 3, "pc_bar": 20, "Tc_K": 3000, "molar_mass_g_mol": 39.95,
                "area_ratio": 3.0, "pa_bar": 0},
        "expect": {"exit_mach": [3.0, 1e-6], "pe_pc": [1 / 32, 1e-6], "CF_vac": [1.5, 1e-6],
                   "c_star_m_s": [_CSTAR_AR, 1e-9]},
    },
    {   # thin-airfoil theory: dCl/dalpha = 2 pi per rad (Anderson, Fundamentals of Aerodynamics,
        # §4.7). For a real 12 %-thick section the measured slope is ~0.105-0.11 /deg = 0.96-1.0 of
        # 2 pi (Abbott & von Doenhoff, Theory of Wing Sections, App. IV; Ladson, NASA TM-4074,
        # 1988): the reference itself is good to ~5 %, hence the tolerance. Symmetric: alpha_L0 = 0.
        "name": "naca0012_lift_slope", "kind": "airfoil",
        "inp": {"airfoil": "naca0012", "Re": 3e6, "mach": 0.0, "alpha_range": [-4, 4, 1]},
        "expect": {"lift_slope_per_rad": [2 * math.pi, 0.05], "alpha_L0_deg": [0.0, 0.01]},
    },
    {   # elliptic planform: span efficiency e = 1 exactly (Prandtl lifting line, Anderson §5.3.1);
        # lift slope a = 2 pi AR / (2 + sqrt(AR^2 + 4)) (Helmbold 1942, Anderson eq. 5.81, a
        # lifting-surface approximation good to ~2-3 % at AR 8). VLM: discretisation ~1-2 %.
        "name": "elliptic_wing_vlm", "kind": "wing",
        "inp": {"planform": "elliptic", "span_m": 2.0, "root_chord_m": 4 * 2.0 / (math.pi * _ELLIPTIC_AR),
                "airfoil": "naca0012", "alpha_deg": [0.0, 4.0], "velocity_m_s": 20},
        "expect": {"span_efficiency": [1.0, 0.02],
                   "CL_alpha_per_rad": [2 * math.pi * _ELLIPTIC_AR / (2 + math.sqrt(_ELLIPTIC_AR ** 2 + 4)), 0.03]},
    },
    {   # stoichiometric CH4/air (O2 + 3.76 N2), 298 K, 1 atm: Tad = 2226 K (Turns, An Introduction
        # to Combustion, Appendix B, Table B.1, equilibrium products)
        "name": "ch4_air_flame_T", "kind": "combustion",
        "inp": {"fuel": "CH4", "oxidizer": {"O2": 1, "N2": 3.76}, "phi": 1.0, "pc_bar": 1.01325,
                "mechanism": "nasa", "expansion": False},
        "expect": {"T_chamber_K": [2226.0, 0.01]},
    },
    {   # same with the GRI-Mech 3.0 thermo (Cantera bundled): same published value
        "name": "ch4_air_flame_T_gri30", "kind": "combustion",
        "inp": {"fuel": "CH4", "oxidizer": {"O2": 1, "N2": 3.76}, "phi": 1.0, "pc_bar": 1.01325,
                "mechanism": "gri30", "expansion": False},
        "expect": {"T_chamber_K": [2226.0, 0.01]},
    },
    {   # stoichiometric H2/O2, 298 K, 1 atm: Tad ~ 3080 K (Glassman & Yetter, Combustion, Table 1.1)
        "name": "h2_o2_flame_T", "kind": "combustion",
        "inp": {"fuel": "H2", "oxidizer": "O2", "phi": 1.0, "pc_bar": 1.01325, "mechanism": "h2o2",
                "expansion": False},
        "expect": {"T_chamber_K": [3080.0, 0.01]},
    },
    {   # expansion machinery against the exact argon nozzle above: c*, CF_vac = 1.5, Isp_vac;
        # shifting and frozen must coincide for an inert gas
        "name": "argon_expansion_exact", "kind": "combustion",
        "inp": {"reactants": [{"name": "Ar", "moles": 1.0, "T_K": 3000.0}], "pc_bar": 20.0,
                "area_ratio": 3.0, "mechanism": "nasa"},
        "expect": {"T_chamber_K": [3000.0, 1e-4], "c_star_eq_m_s": [_CSTAR_AR, 2e-3],
                   "CF_vac_eq": [1.5, 2e-3], "Isp_vac_eq_s": [1.5 * _CSTAR_AR / G0, 2e-3],
                   "Isp_vac_frozen_s": [1.5 * _CSTAR_AR / G0, 2e-3], "pe_eq_bar": [20.0 / 32, 2e-3]},
    },
    {   # vertical, no drag, constant thrust and gravity vs the closed form _vertical_rocket
        # (Tsiolkovsky with gravity loss + ballistic coast)
        "name": "rocket_no_drag_closed_form", "kind": "rocket_flight",
        "inp": {"thrust_curve": [[0.0, _ROCKET["F"]], [_ROCKET["tb"], _ROCKET["F"]]],
                "propellant_mass_kg": _ROCKET["mp"], "motor_dry_mass_kg": 0.1, "motor_radius_m": 0.02,
                "motor_length_m": 0.2, "radius_m": 0.04, "dry_mass_kg": 1.5, "cg_m": 0.5,
                "inertia_kg_m2": [0.1, 0.1, 0.001], "cd": 0.0, "rail_length_m": 2.0,
                "inclination_deg": 90.0, "gravity_m_s2": G0,
                "nose": {"length_m": 0.2, "kind": "vonKarman", "tip_position_m": 1.0},
                "fins": {"n": 4, "root_chord_m": 0.1, "tip_chord_m": 0.05, "span_m": 0.08,
                         "position_m": 0.12}},
        "expect": {"apogee_m": [_RK["apogee"], 0.005], "max_speed_m_s": [_RK["v_burnout"], 0.005],
                   "max_acceleration_m_s2": [_RK["a_max"], 0.005]},
    },
    {   # water, 25 degC, 1 atm: 997.05 kg/m^3, viscosity 890.0 uPa s (IAPWS-95 / IAPWS 2008,
        # NIST Chemistry WebBook); saturation pressure at 100 degC 1.01418 bar (IAPWS-95)
        "name": "water_25C", "kind": "fluid_props",
        "inp": {"fluid": "Water", "T_C": 25.0, "p_bar": 1.01325},
        "expect": {"density_kg_m3": [997.05, 1e-4], "viscosity_Pa_s": [890.0e-6, 2e-3]},
    },
    {
        "name": "water_psat_100C", "kind": "fluid_props",
        "inp": {"fluid": "Water", "T_C": 100.0, "p_bar": 1.01325},
        "expect": {"p_sat_bar": [1.01418, 1e-4]},
    },
]
