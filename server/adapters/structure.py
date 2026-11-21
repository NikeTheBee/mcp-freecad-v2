"""Composite laminates and damage tolerance (env `sim`).

Two gaps the workshop still had, both of them sizing criteria rather than nice-to-haves:

- **laminates.** A carbon or glass lay-up is not a material with one stiffness: it has an
  orientation, a stacking sequence, and failure modes that depend on both. Classical lamination
  theory gives the ABD matrices, the effective moduli the rest of the workshop needs (feed `Ex`
  into `fem_static`), the stress in every ply, and the margin of each one.
- **crack growth.** A pressure hull that is cycled, a spar that flies, a tank that fills and
  empties: `fatigue` answers "how many cycles until a crack starts" (S-N, Miner), this answers
  "there IS a crack of this size, how long until it is critical" -- the damage-tolerance
  question, which is the one that sets inspection intervals.

Kinds:
- laminate      ABD matrices, effective moduli, ply stresses, max-stress and Tsai-Wu margins
- crack_growth  Paris or Forman integration from an initial flaw to the critical size: cycles to
                failure, the critical crack length, and the inspection size that leaves a margin

Units: SI with stresses in MPa and lengths in mm for the laminate (engineering practice), and
stresses in MPa with crack sizes in m for fracture (so that K comes out in MPa*sqrt(m), the unit
every da/dN table is published in). Angles in degrees, measured from the laminate x axis.

Limits, restated in every result:
- laminate: thin-plate CLT. In-plane and bending only, no transverse shear (so it is wrong for a
  thick, short laminate), no interlaminar stress, no delamination, no free-edge effect, no
  moisture or temperature ageing, and first-ply failure -- NOT ultimate strength after the first
  ply breaks. Buckling of the laminate is `fem_buckling` or `marine pressure_hull`, not this.
- crack_growth: constant-amplitude, long-crack, linear elastic fracture mechanics. No overload
  retardation, no closure beyond the Forman R dependency, no short-crack regime, no corrosion,
  no mixed mode. The geometry factor is the weak point: a wrong beta is a wrong life, so the
  factor used is always printed with its validity range.

Exactness: REFERENCES below. A single isotropic ply must reproduce its own A and D matrices and
its own moduli in closed form; a symmetric lay-up must give B = 0 exactly and a balanced one
A16 = A26 = 0 exactly; a ply loaded to its fibre strength must give a failure index of exactly 1
in both criteria; and the Paris integration must match its analytic antiderivative.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

import math  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Tuple  # noqa: E402

import numpy as np  # noqa: E402

from _common import result  # noqa: E402

ENV = "sim"
KINDS = {
    "laminate": {"timeout": 120,
                 "doc": "classical lamination theory: ABD, effective Ex/Ey/Gxy/nu, ply stresses, "
                        "max-stress and Tsai-Wu margins (first-ply failure)"},
    "crack_growth": {"timeout": 300,
                     "doc": "damage tolerance: Paris or Forman integration to the critical crack, "
                            "cycles to failure, critical and inspectable crack size"},
}

# Geometry factors beta in K = beta * sigma * sqrt(pi a), with the range each one is good for
BETAS = {
    "centre_crack_infinite_plate": ("1", "exact by definition of K; a << width"),
    "centre_crack_finite_width": ("sqrt(sec(pi a / W))", "centre crack 2a in a plate of width W, "
                                                         "Feddersen; good to about 2a/W < 0.7"),
    "edge_crack_single": ("1.12", "single edge crack in a wide plate, a << width"),
    "surface_crack_semicircular": ("0.728", "semi-circular surface flaw in a thick plate, "
                                            "Newman-Raju at a/c = 1, a/t small"),
}


def versions() -> Dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for p in ("numpy", "scipy"):
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


def _pos(d: Dict[str, Any], key: str, default=None) -> float:
    if key in d:
        v = _f(d[key])
    elif default is not None:
        v = _f(default)
    else:
        raise ValueError(f"missing input `{key}`")
    if v <= 0:
        raise ValueError(f"`{key}` must be > 0, got {v}")
    return v


# --------------------------------------------------------------------------- lamination theory
def ply_Q(E1: float, E2: float, nu12: float, G12: float) -> np.ndarray:
    """Reduced stiffness of one ply in its own material axes (1 = fibre)."""
    nu21 = nu12 * E2 / E1
    den = 1.0 - nu12 * nu21
    if den <= 0:
        raise ValueError("inconsistent elastic constants: 1 - nu12*nu21 must be > 0")
    return np.array([[E1 / den, nu12 * E2 / den, 0.0],
                     [nu12 * E2 / den, E2 / den, 0.0],
                     [0.0, 0.0, G12]])


def rotate_Q(Q: np.ndarray, theta_deg: float) -> np.ndarray:
    """Q transformed to the laminate axes (standard CLT transformation)."""
    th = math.radians(theta_deg)
    m, n = math.cos(th), math.sin(th)
    q11, q12, q22, q66 = Q[0, 0], Q[0, 1], Q[1, 1], Q[2, 2]
    m2, n2 = m * m, n * n
    m4, n4 = m2 * m2, n2 * n2
    qb = np.zeros((3, 3))
    qb[0, 0] = q11 * m4 + 2 * (q12 + 2 * q66) * m2 * n2 + q22 * n4
    qb[0, 1] = qb[1, 0] = (q11 + q22 - 4 * q66) * m2 * n2 + q12 * (m4 + n4)
    qb[1, 1] = q11 * n4 + 2 * (q12 + 2 * q66) * m2 * n2 + q22 * m4
    qb[0, 2] = qb[2, 0] = ((q11 - q12 - 2 * q66) * m2 * m * n
                           + (q12 - q22 + 2 * q66) * m * n2 * n)
    qb[1, 2] = qb[2, 1] = ((q11 - q12 - 2 * q66) * m * n2 * n
                           + (q12 - q22 + 2 * q66) * m2 * m * n)
    qb[2, 2] = (q11 + q22 - 2 * q12 - 2 * q66) * m2 * n2 + q66 * (m4 + n4)
    return qb


def abd(plies: List[Dict[str, Any]]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float, List]:
    """A, B, D (in MPa*mm, MPa*mm^2, MPa*mm^3), the thickness, and each ply's z range and Qbar."""
    h = sum(_pos(p, "thickness_mm") for p in plies)
    z = -h / 2.0
    A = np.zeros((3, 3))
    B = np.zeros((3, 3))
    D = np.zeros((3, 3))
    layers = []
    for p in plies:
        t = _pos(p, "thickness_mm")
        mat = p.get("material") or p
        qb = rotate_Q(ply_Q(_pos(mat, "E1_MPa"), _pos(mat, "E2_MPa"),
                            _f(mat.get("nu12", 0.3)), _pos(mat, "G12_MPa")),
                      _f(p.get("angle_deg", 0.0)))
        z0, z1 = z, z + t
        A += qb * (z1 - z0)
        B += qb * (z1 ** 2 - z0 ** 2) / 2.0
        D += qb * (z1 ** 3 - z0 ** 3) / 3.0
        layers.append({"z0": z0, "z1": z1, "Qbar": qb, "angle": _f(p.get("angle_deg", 0.0)),
                       "material": mat})
        z = z1
    return A, B, D, h, layers


def _expand_stack(inp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`plies` as written, or `stack` (a list of angles) with one `material` and `ply_thickness_mm`,
    optionally mirrored by `symmetric: true` (so [0, 45] becomes [0, 45, 45, 0])."""
    if "plies" in inp:
        plies = inp["plies"]
        if not isinstance(plies, list) or not plies:
            raise ValueError("`plies` must be a non-empty list")
        return [dict(p) for p in plies]
    stack = inp.get("stack")
    if not isinstance(stack, list) or not stack:
        raise ValueError("give `plies`, or `stack` (a list of angles) with `material` and "
                         "`ply_thickness_mm`")
    mat = inp.get("material")
    if not isinstance(mat, dict):
        raise ValueError("`stack` needs a `material` object (E1_MPa, E2_MPa, nu12, G12_MPa)")
    t = _pos(inp, "ply_thickness_mm")
    angles = [_f(a) for a in stack]
    if bool(inp.get("symmetric", False)):
        angles = angles + angles[::-1]
    if len(angles) > 200:
        raise ValueError("at most 200 plies")
    return [{"angle_deg": a, "thickness_mm": t, "material": mat} for a in angles]


def _failure(s1: float, s2: float, t12: float, mat: Dict[str, Any], f12_mode: str) -> Dict:
    """Max-stress and Tsai-Wu indices. An index of 1 is the boundary; above 1 the ply has failed."""
    out: Dict[str, Any] = {}
    xt, xc = mat.get("Xt_MPa"), mat.get("Xc_MPa")
    yt, yc = mat.get("Yt_MPa"), mat.get("Yc_MPa")
    s = mat.get("S_MPa")
    if None in (xt, xc, yt, yc, s):
        return out
    xt, xc, yt, yc, s = (_pos(mat, "Xt_MPa"), _pos(mat, "Xc_MPa"), _pos(mat, "Yt_MPa"),
                         _pos(mat, "Yc_MPa"), _pos(mat, "S_MPa"))
    ms = max(s1 / xt if s1 >= 0 else -s1 / xc,
             s2 / yt if s2 >= 0 else -s2 / yc,
             abs(t12) / s)
    out["max_stress_index"] = ms
    f1, f2 = 1.0 / xt - 1.0 / xc, 1.0 / yt - 1.0 / yc
    f11, f22, f66 = 1.0 / (xt * xc), 1.0 / (yt * yc), 1.0 / (s * s)
    f12 = (-0.5 / math.sqrt(xt * xc * yt * yc) if f12_mode == "mises"
           else -0.5 * math.sqrt(f11 * f22))
    out["tsai_wu_index"] = (f1 * s1 + f2 * s2 + f11 * s1 * s1 + f22 * s2 * s2
                            + f66 * t12 * t12 + 2 * f12 * s1 * s2)
    return out


def _run_laminate(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    from scipy.linalg import inv

    plies = _expand_stack(inp)
    A, B, D, h, layers = abd(plies)
    sym = float(np.max(np.abs(B))) <= 1e-9 * max(1.0, float(np.max(np.abs(A))))
    bal = (abs(A[0, 2]) <= 1e-9 * max(1.0, abs(A[0, 0]))
           and abs(A[1, 2]) <= 1e-9 * max(1.0, abs(A[1, 1])))
    a = inv(A)
    values: Dict[str, Any] = {
        "plies": len(layers),
        "thickness_mm": round(h, 9),
        "A_MPa_mm": [[round(float(x), 6) for x in row] for row in A],
        "B_MPa_mm2": [[round(float(x), 9) for x in row] for row in B],
        "D_MPa_mm3": [[round(float(x), 6) for x in row] for row in D],
        "symmetric": 1.0 if sym else 0.0,
        "balanced": 1.0 if bal else 0.0,
        "max_abs_B": round(float(np.max(np.abs(B))), 9),
        "A16": round(float(A[0, 2]), 9), "A26": round(float(A[1, 2]), 9),
        "Ex_MPa": round(float(1.0 / (a[0, 0] * h)), 6),
        "Ey_MPa": round(float(1.0 / (a[1, 1] * h)), 6),
        "Gxy_MPa": round(float(1.0 / (a[2, 2] * h)), 6),
        "nu_xy": round(float(-a[0, 1] / a[0, 0]), 9),
        "areal_mass_kg_m2": None,
    }
    rho = inp.get("density_kg_m3")
    if rho:
        values["areal_mass_kg_m2"] = round(_pos(inp, "density_kg_m3") * h * 1e-3, 6)

    # loads: N in N/mm, M in N*mm/mm; mid-plane strains and curvatures from the ABD system
    load = inp.get("load") or {}
    nvec = np.array([_f(load.get("Nx_N_mm", 0.0)), _f(load.get("Ny_N_mm", 0.0)),
                     _f(load.get("Nxy_N_mm", 0.0))])
    mvec = np.array([_f(load.get("Mx_N", 0.0)), _f(load.get("My_N", 0.0)),
                     _f(load.get("Mxy_N", 0.0))])
    if np.any(nvec) or np.any(mvec):
        abd_mat = np.block([[A, B], [B, D]])
        sol = np.linalg.solve(abd_mat, np.concatenate([nvec, mvec]))
        eps0, kap = sol[:3], sol[3:]
        f12_mode = str(inp.get("f12", "mises")).lower()
        worst_ms = worst_tw = None
        plydata = []
        for i, lay in enumerate(layers):
            zc = 0.5 * (lay["z0"] + lay["z1"])
            eps = eps0 + zc * kap
            sx = lay["Qbar"].dot(eps)                       # laminate axes
            th = math.radians(lay["angle"])
            m, n = math.cos(th), math.sin(th)
            # stresses rotated into the ply's own axes
            s1 = sx[0] * m * m + sx[1] * n * n + 2 * sx[2] * m * n
            s2 = sx[0] * n * n + sx[1] * m * m - 2 * sx[2] * m * n
            t12 = (sx[1] - sx[0]) * m * n + sx[2] * (m * m - n * n)
            fail = _failure(s1, s2, t12, lay["material"], f12_mode)
            row = {"ply": i + 1, "angle_deg": lay["angle"],
                   "sigma1_MPa": round(float(s1), 6), "sigma2_MPa": round(float(s2), 6),
                   "tau12_MPa": round(float(t12), 6)}
            row.update({k: round(float(v), 9) for k, v in fail.items()})
            plydata.append(row)
            # a Tsai-Wu index can be NEGATIVE (comfortably safe in compression): keep the
            # worst one as it is instead of clamping it at zero, or a criterion on it would be
            # reported as "not computed" exactly when the laminate is safest
            if "max_stress_index" in fail:
                worst_ms = (fail["max_stress_index"] if worst_ms is None
                            else max(worst_ms, fail["max_stress_index"]))
            if "tsai_wu_index" in fail:
                worst_tw = (fail["tsai_wu_index"] if worst_tw is None
                            else max(worst_tw, fail["tsai_wu_index"]))
        values["plies_detail"] = plydata
        values["strain_x"] = round(float(eps0[0]), 9)
        values["strain_y"] = round(float(eps0[1]), 9)
        values["curvature_x_1_mm"] = round(float(kap[0]), 12)
        if worst_ms is not None:
            values["max_stress_index"] = round(worst_ms, 9)
            if worst_ms > 0:
                values["max_stress_margin"] = round(1.0 / worst_ms - 1.0, 6)
        if worst_tw is not None:
            values["tsai_wu_index"] = round(worst_tw, 9)
            if worst_tw > 0:
                values["tsai_wu_margin"] = round(1.0 / worst_tw - 1.0, 6)
    head = (f"{len(layers)} plies, {h:.3f} mm: Ex {values['Ex_MPa'] / 1000:.1f} GPa, "
            f"Ey {values['Ey_MPa'] / 1000:.1f} GPa"
            + (", symmetric" if sym else ", NOT symmetric (it will warp when cured or loaded)")
            + (", balanced" if bal else ", NOT balanced (in-plane shear couples to stretching)"))
    if values.get("tsai_wu_index") is not None:
        head += f"; worst Tsai-Wu {values['tsai_wu_index']:.3f}"
    return result(values, inp.get("limits"), head,
                  notes=["thin-plate CLT: in-plane and bending only, no transverse shear, no "
                         "interlaminar stress, no delamination, no free-edge effect",
                         "FIRST-PLY failure: an index above 1 means one ply has failed, not that "
                         "the laminate has broken",
                         "an index of exactly 1 is the boundary; carry a margin, and remember "
                         "that strengths of a real lay-up scatter by 10 % or more",
                         "feed `Ex_MPa` and `nu_xy` into `fem_static` for the part, but only "
                         "where the laminate is loaded in its plane"])


# --------------------------------------------------------------------------- crack growth
def beta_of(name: str, a: float, width: float) -> float:
    if name == "centre_crack_infinite_plate":
        return 1.0
    if name == "edge_crack_single":
        return 1.12
    if name == "surface_crack_semicircular":
        return 0.728
    if name == "centre_crack_finite_width":
        if width <= 0:
            raise ValueError("`width_m` is needed for the finite-width factor")
        r = math.pi * a / width
        if r >= math.pi / 2 * 0.95:
            raise ValueError("the crack is too long for this factor (2a/W beyond its range)")
        return math.sqrt(1.0 / math.cos(r))
    raise ValueError(f"unknown `beta`: {', '.join(BETAS)}")


def _run_crack(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    from scipy.integrate import quad

    ds = _pos(inp, "delta_sigma_MPa")           # stress range of one cycle
    r_ratio = _f(inp.get("R", 0.0))
    if not -1.0 <= r_ratio < 1.0:
        raise ValueError("`R` (stress ratio) must be in [-1, 1)")
    c_paris = _pos(inp, "C")                    # m/cycle per (MPa*sqrt(m))^m
    m_paris = _pos(inp, "m")
    if not 1.5 <= m_paris <= 8.0:
        raise ValueError("`m` is usually between 2 and 5; outside 1.5..8 this is not a Paris law")
    kc = _pos(inp, "Kc_MPa_sqrt_m")
    a_i = _pos(inp, "a_initial_m")
    law = str(inp.get("law", "paris")).lower()
    if law not in ("paris", "forman"):
        raise ValueError("`law` must be `paris` or `forman`")
    beta_name = str(inp.get("beta", "centre_crack_infinite_plate"))
    width = _f(inp.get("width_m", 0.0))
    dk_th = _f(inp.get("delta_K_threshold_MPa_sqrt_m", 0.0))
    wall = _f(inp.get("thickness_m", 0.0))      # the wall the crack grows through, if there is one
    if wall < 0:
        raise ValueError("`thickness_m` must be >= 0")

    def dk(a: float) -> float:
        return beta_of(beta_name, a, width) * ds * math.sqrt(math.pi * a)

    def kmax(a: float) -> float:
        smax = ds / (1.0 - r_ratio) if r_ratio != 0.0 else ds
        return beta_of(beta_name, a, width) * smax * math.sqrt(math.pi * a)

    # critical crack: Kmax reaches Kc
    lo, hi = a_i, a_i
    for _ in range(200):
        hi *= 1.2
        if kmax(hi) >= kc or hi > 1e3:
            break
    if kmax(a_i) >= kc:
        raise ValueError(f"the initial crack is already critical: Kmax = {kmax(a_i):.2f} >= "
                         f"Kc = {kc:.2f} MPa*sqrt(m)")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if kmax(mid) < kc:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    a_c = 0.5 * (lo + hi)
    a_f = min(_f(inp.get("a_final_m", a_c)), a_c)
    # leak before break: with a wall thickness given, a surface crack reaching the far face is
    # the end of the calculation -- it leaks. Which of the two comes first is the answer that
    # matters, and quoting a critical crack longer than the wall would be nonsense.
    leak = bool(wall > 0 and wall < a_f)
    if leak:
        a_f = wall
    if a_f <= a_i:
        raise ValueError("`a_final_m` must be larger than `a_initial_m`")

    if dk_th > 0 and dk(a_i) <= dk_th:
        values = {"critical_crack_m": round(a_c, 9), "delta_K_initial_MPa_sqrt_m": round(dk(a_i), 12),
                  "cycles_to_failure": None, "grows": 0.0,
                  "delta_K_threshold_MPa_sqrt_m": dk_th}
        return result(values, inp.get("limits"),
                      f"no growth: dK = {dk(a_i):.2f} is at or below the threshold {dk_th:.2f} "
                      f"MPa*sqrt(m)",
                      notes=["below the threshold a long crack does not grow at constant "
                             "amplitude; an overload or a corrosive environment changes that",
                             f"geometry factor `{beta_name}`: {BETAS[beta_name][1]}"])

    def dn_da(a: float) -> float:
        d = dk(a)
        if dk_th > 0 and d <= dk_th:
            return 0.0
        if law == "paris":
            rate = c_paris * d ** m_paris
        else:   # Forman: C dK^m / ((1-R) Kc - dK)
            den = (1.0 - r_ratio) * kc - d
            if den <= 0:
                return float("inf")
            rate = c_paris * d ** m_paris / den
        return 1.0 / rate if rate > 0 else float("inf")

    cycles, err = quad(dn_da, a_i, a_f, limit=400)
    values = {
        "cycles_to_failure": round(float(cycles), 3),
        "integration_error_estimate": float(err),
        "critical_crack_m": round(a_c, 9),
        "a_initial_m": a_i, "a_final_m": round(a_f, 9),
        "delta_K_initial_MPa_sqrt_m": round(dk(a_i), 12),
        "delta_K_final_MPa_sqrt_m": round(dk(a_f), 12),
        "Kmax_final_MPa_sqrt_m": round(kmax(a_f), 9),
        "law": law, "beta": beta_name, "grows": 1.0,
        "leak_before_break": 1.0 if leak else 0.0,
    }
    if wall > 0:
        values["thickness_m"] = wall
    # the size an inspection must find to leave a given fraction of the life
    frac = _f(inp.get("inspection_life_fraction", 0.5))
    if 0.0 < frac < 1.0:
        target = float(cycles) * (1.0 - frac)
        lo2, hi2 = a_i, a_f
        for _ in range(100):
            mid = 0.5 * (lo2 + hi2)
            c2, _e = quad(dn_da, a_i, mid, limit=200)
            if c2 < target:
                lo2 = mid
            else:
                hi2 = mid
            if hi2 - lo2 < 1e-12:
                break
        values["inspection_crack_m"] = round(0.5 * (lo2 + hi2), 9)
        values["inspection_life_fraction"] = frac
    head = (f"{cycles:.0f} cycles from {a_i * 1000:.2f} mm to {a_f * 1000:.2f} mm")
    if leak:
        head += (f": the crack goes THROUGH the {wall * 1000:.2f} mm wall first and it leaks, "
                 f"long before K reaches Kc (which would need {a_c * 1000:.0f} mm)")
    else:
        head += f" (critical {a_c * 1000:.2f} mm)"
        if wall > 0:
            head += f", reached before the {wall * 1000:.2f} mm wall: it breaks, it does not leak"
    return result(values, inp.get("limits"), head,
                  notes=["constant amplitude, long crack, linear elastic fracture mechanics: no "
                         "overload retardation, no short-crack regime, no corrosion, no mixed mode",
                         f"geometry factor `{beta_name}`: {BETAS[beta_name][1]}",
                         "C and m come from a table for THAT material, orientation and "
                         "environment; using a generic pair is the biggest error source here",
                         "an inspection interval is half of this life at most, and that is a "
                         "convention, not a calculation",
                         "give `thickness_m` for a wall: a surface crack usually goes through it "
                         "and leaks long before K reaches Kc, and a critical crack longer than "
                         "the wall is arithmetic, not engineering"])


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fns = {"laminate": _run_laminate, "crack_growth": _run_crack}
    if kind not in fns:
        raise ValueError(f"unknown kind {kind!r}; known: {', '.join(sorted(fns))}")
    return fns[kind](inp, Path(work))


# --------------------------------------------------------------------------- references
# One isotropic ply: CLT must reproduce the closed forms it is built on.
#   Q = E/(1-nu^2) * [[1, nu, 0], [nu, 1, 0], [0, 0, (1-nu)/2]]
#   A = t Q,  B = 0,  D = t^3/12 Q,  Ex = E,  nu_xy = nu
_E, _NU, _T = 70000.0, 0.33, 2.0
_ISO = {"E1_MPa": _E, "E2_MPa": _E, "nu12": _NU, "G12_MPa": _E / (2 * (1 + _NU))}
_A11 = _T * _E / (1 - _NU ** 2)
_A12 = _T * _NU * _E / (1 - _NU ** 2)
_D11 = _T ** 3 / 12.0 * _E / (1 - _NU ** 2)

# A carbon/epoxy ply, the usual textbook set, used for the stacking checks
_CF = {"E1_MPa": 135000.0, "E2_MPa": 10000.0, "nu12": 0.3, "G12_MPa": 5000.0,
       "Xt_MPa": 1500.0, "Xc_MPa": 1200.0, "Yt_MPa": 50.0, "Yc_MPa": 250.0, "S_MPa": 70.0}
# A single 0 deg ply pulled to exactly Xt along the fibre: both criteria must read exactly 1.
# N = Xt * t  (N/mm) for a ply of thickness t loaded along x.
_T1 = 0.125
_N_XT = _CF["Xt_MPa"] * _T1

# Paris integration against its antiderivative. With beta = 1, K = sigma sqrt(pi a), so
#   N = [a_f^(1-m/2) - a_i^(1-m/2)] / [C (sigma sqrt(pi))^m (1 - m/2)]
_DS, _C, _M = 100.0, 1e-11, 3.0
_AI, _AF = 0.001, 0.010
_P = 1.0 - _M / 2.0
_N_CLOSED = ((_AF ** _P - _AI ** _P)
             / (_C * (_DS * math.sqrt(math.pi)) ** _M * _P))
# critical crack for Kc with R = 0 (so Kmax = delta_sigma): a_c = (Kc/(sigma sqrt(pi)))^2
_KC = 60.0
_AC = (_KC / (_DS * math.sqrt(math.pi))) ** 2

REFERENCES = [
    {   # CLT must reproduce the isotropic closed forms exactly
        "name": "isotropic_ply_closed_form", "kind": "laminate",
        "inp": {"plies": [{"angle_deg": 0.0, "thickness_mm": _T, "material": _ISO}]},
        "expect": {"Ex_MPa": [_E, 1e-9], "Ey_MPa": [_E, 1e-9], "nu_xy": [_NU, 1e-9],
                   "Gxy_MPa": [_ISO["G12_MPa"], 1e-9],
                   "thickness_mm": [_T, 1e-12], "max_abs_B": [0.0, 1e-9]},
    },
    {   # the A and D terms themselves, not just the moduli derived from them
        "name": "isotropic_ply_A_and_D", "kind": "laminate",
        "inp": {"plies": [{"angle_deg": 0.0, "thickness_mm": _T, "material": _ISO}],
                "limits": {"thickness_mm": {"min": 0.0}}},
        "expect": {"A16": [0.0, 1e-9], "A26": [0.0, 1e-9], "symmetric": [1.0, 1e-9]},
        "setup": "_check_A_D",
    },
    {   # a symmetric stack has B = 0 exactly, and a balanced one has A16 = A26 = 0 exactly
        "name": "symmetric_balanced_stack", "kind": "laminate",
        "inp": {"stack": [0.0, 45.0, -45.0, 90.0], "symmetric": True,
                "material": _CF, "ply_thickness_mm": _T1},
        "expect": {"plies": [8, 1e-9], "symmetric": [1.0, 1e-9], "balanced": [1.0, 1e-9],
                   "max_abs_B": [0.0, 1e-9], "A16": [0.0, 1e-9], "A26": [0.0, 1e-9],
                   "thickness_mm": [8 * _T1, 1e-12]},
    },
    {   # a 45 deg single ply is NOT balanced: the check must be able to say no
        "name": "single_45_is_not_balanced", "kind": "laminate",
        "inp": {"stack": [45.0], "material": _CF, "ply_thickness_mm": _T1},
        "expect": {"balanced": [0.0, 1e-9], "symmetric": [1.0, 1e-9]},
    },
    {   # a 0 deg ply pulled to exactly its fibre strength: both indices are exactly 1
        "name": "ply_at_fibre_strength", "kind": "laminate",
        "inp": {"stack": [0.0], "material": _CF, "ply_thickness_mm": _T1,
                "load": {"Nx_N_mm": _N_XT}},
        "expect": {"max_stress_index": [1.0, 1e-9], "tsai_wu_index": [1.0, 1e-9],
                   "max_stress_margin": [0.0, 1e-9]},
    },
    {   # Paris integration against its own antiderivative, and the critical crack in closed form
        "name": "paris_closed_form", "kind": "crack_growth",
        "inp": {"delta_sigma_MPa": _DS, "C": _C, "m": _M, "Kc_MPa_sqrt_m": _KC,
                "a_initial_m": _AI, "a_final_m": _AF, "R": 0.0,
                "beta": "centre_crack_infinite_plate"},
        "expect": {"cycles_to_failure": [_N_CLOSED, 1e-6],
                   "critical_crack_m": [_AC, 1e-6],
                   # K = sigma sqrt(pi a) by definition of this geometry factor
                   "delta_K_initial_MPa_sqrt_m": [_DS * math.sqrt(math.pi * _AI), 1e-9]},
    },
    {   # with no a_final given, the integration must run to the critical crack it found
        "name": "paris_to_critical", "kind": "crack_growth",
        "inp": {"delta_sigma_MPa": _DS, "C": _C, "m": _M, "Kc_MPa_sqrt_m": _KC,
                "a_initial_m": _AI, "R": 0.0},
        "expect": {"a_final_m": [_AC, 1e-6],
                   "Kmax_final_MPa_sqrt_m": [_KC, 1e-6],
                   "cycles_to_failure": [((_AC ** _P - _AI ** _P)
                                          / (_C * (_DS * math.sqrt(math.pi)) ** _M * _P)), 1e-6]},
    },
    {   # leak before break: the same case with a 3 mm wall stops at the wall, and the life
        # is the closed-form integral up to the wall rather than up to the critical crack
        "name": "leak_before_break", "kind": "crack_growth",
        "inp": {"delta_sigma_MPa": _DS, "C": _C, "m": _M, "Kc_MPa_sqrt_m": _KC,
                "a_initial_m": _AI, "R": 0.0, "thickness_m": 0.003},
        "expect": {"leak_before_break": [1.0, 1e-9], "a_final_m": [0.003, 1e-12],
                   "cycles_to_failure": [((0.003 ** _P - _AI ** _P)
                                          / (_C * (_DS * math.sqrt(math.pi)) ** _M * _P)), 1e-6],
                   "critical_crack_m": [_AC, 1e-6]},
    },
    {   # below the threshold, a long crack does not grow: the answer must say so, not integrate
        "name": "below_threshold_no_growth", "kind": "crack_growth",
        "inp": {"delta_sigma_MPa": 10.0, "C": _C, "m": _M, "Kc_MPa_sqrt_m": _KC,
                "a_initial_m": 0.0005, "R": 0.0,
                "delta_K_threshold_MPa_sqrt_m": 5.0},
        "expect": {"grows": [0.0, 1e-9],
                   "delta_K_initial_MPa_sqrt_m": [10.0 * math.sqrt(math.pi * 0.0005), 1e-9]},
    },
]


def _check_A_D(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """Reference setup that also asserts the A and D terms of the isotropic ply in closed form:
    they are matrices, which `expect` cannot compare, so they are checked here and the case fails
    loudly if they drift."""
    A, B, D, h, _ = abd([{"angle_deg": 0.0, "thickness_mm": _T, "material": _ISO}])
    for got, want, what in ((A[0, 0], _A11, "A11"), (A[0, 1], _A12, "A12"),
                            (A[1, 1], _A11, "A22"), (D[0, 0], _D11, "D11"),
                            (A[2, 2], _T * _ISO["G12_MPa"], "A33")):
        if abs(got - want) > 1e-9 * abs(want):
            raise AssertionError(f"{what}: got {got!r}, closed form {want!r}")
    if float(np.max(np.abs(B))) > 1e-9:
        raise AssertionError("B is not zero for a single ply")
    return dict(inp)
