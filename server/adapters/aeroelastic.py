"""Aeroelasticity: flutter, divergence, gust loads, fin flutter (env `sim`).

What a paying user would run in MSC/NX Nastran SOL 145 or ZAERO; here the unsteady aerodynamics
come from PanelAero (DLR, BSD-3: Doublet Lattice and Vortex Lattice, AICs validated against
MSC.Nastran) or from Theodorsen strip theory, and the structure from a uniform beam or from a
modal basis (CalculiX `fem_modal` results can be passed in as `modes`).

Kinds (no geometry needed; every input is a number or a list in `inp`):
- flutter_wing   p-k flutter of a lifting surface: flutter speed, frequency, V-g / V-f table,
                 and the divergence speed that falls out of the same system.
                 `model`: `uniform_beam` (EI, GJ, m, S_alpha, I_alpha, L, chord) or `modal`
                 (your own generalized mass/stiffness + mode shapes at span stations).
                 `aero`: `theodorsen` (2-D strip, incompressible) or `dlm` (3-D PanelAero).
- divergence     static aeroelastic divergence speed of a uniform wing (closed form + Galerkin)
- fin_flutter    flutter speed of a rocket fin (NACA TN 4197 form), screening criterion
- gust_load      discrete-gust load factor, Pratt-Walker alleviation (CS/FAR 25.341 form)
- aic_dlm        raw aerodynamic influence coefficients of a flat panel grid: Qjj(k) by DLM,
                 Qjj(0) by VLM, steady lift slope. For checking the aerodynamics themselves.

Units: SI. Lengths m, masses kg, forces N, speeds m/s, pressures Pa, angles rad unless the key
says `_deg`. Frequencies are given both in rad/s and Hz. Stiffnesses: EI and GJ in N m^2,
mass per unit length kg/m, I_alpha (pitch inertia about the elastic axis, per unit length) in
kg m^2/m, S_alpha (static unbalance per unit length, positive when the centre of gravity is AFT
of the elastic axis) in kg m/m. `a_ea` is the elastic-axis position measured from mid-chord in
semichords, positive aft (a_ea = -0.2 puts the axis at 40 % chord). `k` is the CLASSICAL reduced
frequency omega/V (NOT the Nastran convention omega*c/(2V)): PanelAero uses the classical one.

Limits, always stated in the result:
- linear: small perturbations about a steady state, no separation, no shock motion, no freeplay;
- Theodorsen strip theory is 2-D and incompressible (M < ~0.3) and ignores tip and sweep effects:
  it is usually conservative on a straight wing of moderate-to-high aspect ratio;
- the DLM is subsonic and valid for thin surfaces at small amplitude; it is not valid
  transonically (shock motion) nor supersonically;
- p-k damping `g` is the *required* structural damping, not a physical damping ratio;
- fin_flutter is an empirical screening formula for solid, uniform, trapezoidal fins: treat it as
  a boundary with a margin (1.5 on speed is usual), never as a precise prediction;
- gust_load is the regulatory discrete-gust formula for a rigid aeroplane: no dynamic response,
  no flexibility, no unsteady lag.

Exactness: REFERENCES below. Goland's wing (published flutter speed and frequency), the
closed-form divergence of a uniform cantilever wing, the identity DLM(k=0) == VLM, the Helmbold
lift slope, and the algebraic identity of the fin-flutter and Pratt formulas.
"""
from __future__ import annotations

import os

# Same OpenBLAS guard as the other adapters: the AVX2 kernels fault on this machine for some
# small matrix products; the Sandybridge (AVX) kernels are correct. Must precede numpy.
os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
# The `sim` environment links the MKL build of libblas (pulled in by ipopt / mumps / casadi).
# With MKL's default threading layer a second OpenMP runtime is loaded beside the llvm-openmp
# that gmsh and vtk already bring, and EVERY LAPACK call (inv, solve, svd, eig) kills the
# process without a message -- which is exactly what this adapter leans on. The sequential
# layer is correct and deterministic. The runner sets the same variable; this keeps a direct
# `python -I aeroelastic.py` run honest too. Must precede numpy.
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

import csv  # noqa: E402
import math  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

import numpy as np  # noqa: E402

from scipy.linalg import eigvals  # noqa: E402

from _common import result  # noqa: E402

ENV = "sim"
KINDS = {
    "flutter_wing": {"timeout": 900,
                     "doc": "p-k flutter of a wing (uniform beam or your modes; Theodorsen or DLM): "
                            "flutter speed, frequency, V-g/V-f, divergence"},
    "divergence": {"timeout": 120,
                   "doc": "static divergence speed of a uniform wing (closed form + Galerkin check)"},
    "fin_flutter": {"timeout": 60,
                    "doc": "rocket fin flutter speed (NACA TN 4197 form) at an altitude or pressure"},
    "gust_load": {"timeout": 60,
                  "doc": "discrete-gust load factor with Pratt alleviation (CS/FAR 25.341 form)"},
    "aic_dlm": {"timeout": 600,
                "doc": "PanelAero AIC of a flat rectangular grid: Qjj(k) by DLM, Qjj(0) by VLM, lift slope"},
}

RHO_SL = 1.225                     # ISA sea-level density, kg/m^3
P_SL = 101325.0                    # ISA sea-level pressure, Pa
A_SL = 340.294                     # ISA sea-level speed of sound, m/s
G0 = 9.80665
_BETA_L = (1.8751040687, 4.6940911330, 7.8547574382, 10.9955407349)   # cantilever beam roots


def versions() -> Dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for p in ("panelaero", "numpy", "scipy"):
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
    v = _f(inp.get(key, default)) if (key in inp or default is not None) else None
    if v is None:
        raise ValueError(f"missing input `{key}`")
    if v <= 0:
        raise ValueError(f"`{key}` must be > 0, got {v}")
    return v


# --------------------------------------------------------------------------- unsteady 2-D aero
def theodorsen(k: float) -> complex:
    """C(k) = H1(2)(k) / (H1(2)(k) + i H0(2)(k)); C(0) = 1 (Theodorsen 1935, NACA TR 496)."""
    if k <= 0:
        return 1.0 + 0.0j
    from scipy.special import hankel2
    h1, h0 = hankel2(1, k), hankel2(0, k)
    return complex(h1 / (h1 + 1j * h0))


def _strip_coeffs(k: float, v: float, b: float, a: float, rho: float) -> Tuple[complex, ...]:
    """Lift (positive up) and moment about the elastic axis (positive nose up) per unit span, for
    harmonic plunge z (positive UP) and pitch alpha (positive nose up) of amplitude 1:

        L = Lz z + La alpha          M = Mz z + Ma alpha

    Theodorsen's circulatory + apparent-mass terms (Theodorsen 1935; Fung, An Introduction to the
    Theory of Aeroelasticity, ch. 6, with h = -z because Fung measures plunge downwards).
    """
    om = k * v / b                                   # omega = k V / b (classical k = omega/V... )
    c = theodorsen(k)
    iom = 1j * om
    circ = 2.0 * math.pi * rho * v * b * c           # circulatory factor
    nc = math.pi * rho * b * b                       # non-circulatory (apparent mass) factor
    lz = -circ * iom + nc * om * om
    la = circ * (v + b * (0.5 - a) * iom) + nc * (iom * v + b * a * om * om)
    mz = -circ * b * (a + 0.5) * iom + nc * b * a * om * om
    ma = (circ * b * (a + 0.5) * (v + b * (0.5 - a) * iom)
          + nc * (-iom * v * b * (0.5 - a) + b * b * (0.125 + a * a) * om * om))
    return lz, la, mz, ma


# --------------------------------------------------------------------------- modal structures
def _natural_frequencies(m: np.ndarray, kk: np.ndarray) -> np.ndarray:
    """Undamped natural frequencies, rad/s, of the generalized system (K x = omega^2 M x)."""
    lam = eigvals(kk, m)
    w = np.sqrt(np.abs(np.real(np.asarray([x for x in lam if np.isfinite(x)]))))
    return np.sort(w)


def _beam_modes(span: float, n_bend: int, n_tors: int, y: np.ndarray
                ) -> Tuple[List[np.ndarray], List[np.ndarray], List[np.ndarray], List[np.ndarray]]:
    """Cantilever bending shapes (and their curvature) and torsion shapes (and their twist rate),
    normalised to 1 at the tip. Classical uniform Euler-Bernoulli / Saint-Venant shapes."""
    phi, phi2, psi, psi1 = [], [], [], []
    for i in range(n_bend):
        bl = _BETA_L[i]
        bet = bl / span
        sig = ((math.cosh(bl) + math.cos(bl)) / (math.sinh(bl) + math.sin(bl)))
        s = (np.cosh(bet * y) - np.cos(bet * y) - sig * (np.sinh(bet * y) - np.sin(bet * y)))
        d2 = bet ** 2 * (np.cosh(bet * y) + np.cos(bet * y)
                         - sig * (np.sinh(bet * y) + np.sin(bet * y)))
        tip = s[-1]
        phi.append(s / tip)
        phi2.append(d2 / tip)
    for j in range(n_tors):
        lam = (2 * j + 1) * math.pi / (2.0 * span)
        psi.append(np.sin(lam * y))
        psi1.append(lam * np.cos(lam * y))
    return phi, phi2, psi, psi1


def _uniform_beam_system(inp: Dict[str, Any], y: np.ndarray):
    """Generalized mass/stiffness and the plunge/pitch shape of each mode, for a uniform wing."""
    span = _pos(inp, "span_m")
    ei = _pos(inp, "EI_N_m2")
    gj = _pos(inp, "GJ_N_m2")
    mass = _pos(inp, "mass_per_length_kg_m")
    i_alpha = _pos(inp, "I_alpha_kg_m2_per_m")
    s_alpha = _f(inp.get("S_alpha_kg_m_per_m", 0.0))
    n_bend = int(inp.get("n_bending_modes", 2))
    n_tors = int(inp.get("n_torsion_modes", 2))
    if not (1 <= n_bend <= 4 and 1 <= n_tors <= 4):
        raise ValueError("n_bending_modes and n_torsion_modes must each be between 1 and 4")
    phi, phi2, psi, psi1 = _beam_modes(span, n_bend, n_tors, y)

    n = n_bend + n_tors
    zs: List[np.ndarray] = [*phi, *[np.zeros_like(y) for _ in psi]]
    als: List[np.ndarray] = [*[np.zeros_like(y) for _ in phi], *psi]
    m = np.zeros((n, n))
    kk = np.zeros((n, n))
    # Kinetic energy per unit span, plunge z positive UP and pitch alpha nose up:
    #   T = 1/2 m zdot^2 - S_alpha zdot alphadot + 1/2 I_alpha alphadot^2
    # (the centre of gravity, S_alpha/m aft of the elastic axis, moves DOWN when the nose goes up)
    # so M_ij = integral[ m z_i z_j - S_alpha (z_i a_j + z_j a_i) + I_alpha a_i a_j ] dy.
    for i in range(n):
        for j in range(n):
            m[i, j] = np.trapezoid(mass * zs[i] * zs[j]
                                   - s_alpha * (zs[i] * als[j] + zs[j] * als[i])
                                   + i_alpha * als[i] * als[j], y)
    for i in range(n_bend):
        for j in range(n_bend):
            kk[i, j] = np.trapezoid(ei * phi2[i] * phi2[j], y)
    for i in range(n_tors):
        for j in range(n_tors):
            kk[n_bend + i, n_bend + j] = np.trapezoid(gj * psi1[i] * psi1[j], y)
    return m, kk, zs, als


def _modal_system(inp: Dict[str, Any]):
    """User-supplied modal basis: each mode has a plunge shape and a pitch shape at `stations_m`,
    with the generalized mass and stiffness matrices in the same order (e.g. from CalculiX)."""
    y = np.asarray([_f(v) for v in inp["stations_m"]], dtype=float)
    if y.ndim != 1 or y.size < 3 or np.any(np.diff(y) <= 0):
        raise ValueError("`stations_m` must be at least 3 increasing span positions")
    modes = inp["modes"]
    if not isinstance(modes, list) or not modes:
        raise ValueError("`modes` must be a non-empty list of {z, alpha_rad}")
    zs, als = [], []
    for i, md in enumerate(modes):
        z = np.asarray([_f(v) for v in md.get("z", np.zeros_like(y))], dtype=float)
        al = np.asarray([_f(v) for v in md.get("alpha_rad", np.zeros_like(y))], dtype=float)
        if z.shape != y.shape or al.shape != y.shape:
            raise ValueError(f"mode {i}: `z` and `alpha_rad` must have one value per station")
        zs.append(z)
        als.append(al)
    n = len(modes)
    m = np.asarray(inp["generalized_mass"], dtype=float)
    kk = np.asarray(inp["generalized_stiffness"], dtype=float)
    if m.shape != (n, n) or kk.shape != (n, n):
        raise ValueError(f"generalized_mass and generalized_stiffness must both be {n}x{n}")
    return m, kk, zs, als, y


# --------------------------------------------------------------------------- generalized aero
def _gen_aero_strip(k: float, v: float, b: float, a: float, rho: float,
                    y: np.ndarray, zs: Sequence[np.ndarray], als: Sequence[np.ndarray]
                    ) -> np.ndarray:
    """Generalized aerodynamic matrix Q(k, V) by integrating the strip loads over the span."""
    lz, la, mz, ma = _strip_coeffs(k, v, b, a, rho)
    n = len(zs)
    q = np.zeros((n, n), dtype=complex)
    for i in range(n):
        for j in range(n):
            q[i, j] = (np.trapezoid(zs[i] * (lz * zs[j] + la * als[j]), y)
                       + np.trapezoid(als[i] * (mz * zs[j] + ma * als[j]), y))
    return q


def _gen_aero_dlm(k: float, v: float, b: float, a: float, rho: float,
                  y: np.ndarray, zs: Sequence[np.ndarray], als: Sequence[np.ndarray],
                  n_chord: int, mach: float) -> np.ndarray:
    """Same, with 3-D unsteady aerodynamics from PanelAero's DLM on a rectangular grid.

    The grid follows the span stations; the downwash of a mode is w/V = alpha + (i omega / V) z
    (the plunge contributes a velocity, the pitch an angle), and the panel pressure coefficients
    give the generalized forces. k is the classical omega/V, as PanelAero expects.
    """
    from panelaero import DLM
    chord = 2.0 * b
    grid, pan_y, pan_x = _flat_grid(y, chord, n_chord)
    om = k * v / b                                  # physical frequency, rad/s
    qjj = DLM.calc_Qjj(grid, Ma=mach, k=om / v)     # PanelAero wants the classical k = omega/V
    sgn = _dlm_lift_sign(grid, mach)
    n = len(zs)
    zp = [np.interp(pan_y, y, z) for z in zs]
    ap = [np.interp(pan_y, y, al) for al in als]
    area = grid["A"]
    x_ea = (0.5 + 0.5 * a) * chord
    arm = pan_x - x_ea                              # panel 1/4-chord, positive aft of the axis
    qdyn = 0.5 * rho * v * v
    q = np.zeros((n, n), dtype=complex)
    for j in range(n):
        # surface boundary condition w/V = dzs/dx + (i omega / V) zs, with the surface shape
        # zs = z - alpha (x - x_ea) for plunge z (up) and pitch alpha (nose up)
        wj = -ap[j] + (1j * om / v) * (zp[j] - ap[j] * arm)
        cp = qjj.dot(wj)
        fz = sgn * qdyn * cp * area                 # lift (up) of each panel, per unit amplitude
        mom = -fz * arm                             # nose-up moment about the elastic axis
        for i in range(n):
            q[i, j] = np.sum(zp[i] * fz) + np.sum(ap[i] * mom)
    return q


def _dlm_lift_sign(grid, mach: float) -> float:
    """PanelAero returns pressure coefficients whose sign depends on the panel normals; calibrate
    it once on a steady unit angle of attack so that a nose-up angle gives lift UP."""
    from panelaero import VLM
    qjj, _ = VLM.calc_Qjj(grid, Ma=mach, xz_symmetry=False)
    lift = float(np.sum(qjj.dot(-np.ones(grid["n"])) * grid["A"]))
    return 1.0 if lift > 0 else -1.0


def _flat_grid(y: np.ndarray, chord: float, n_chord: int):
    """Rectangular panel grid for PanelAero: 1/4-chord vortex line, 3/4-chord downwash point.

    Returns (aerogrid, panel span position, panel 1/4-chord x) with the keys PanelAero reads:
    offset_j (receiving), offset_l (sending), offset_P1/P3 (bound-vortex ends), N, A, l, n.
    Panels are defined left to right, flow along +x, z up (PanelAero's own convention).
    """
    n_span = len(y) - 1
    nx = max(1, int(n_chord))
    xe = np.linspace(0.0, chord, nx + 1)
    oj, ol, p1, p3, nn, ar, ll = [], [], [], [], [], [], []
    pan_y, pan_x = [], []
    for i in range(n_span):
        y1, y2 = float(y[i]), float(y[i + 1])
        ym, dy = 0.5 * (y1 + y2), (y2 - y1)
        for j in range(nx):
            x1, x2 = float(xe[j]), float(xe[j + 1])
            c = x2 - x1
            oj.append([x1 + 0.75 * c, ym, 0.0])
            ol.append([x1 + 0.25 * c, ym, 0.0])
            p1.append([x1 + 0.25 * c, y1, 0.0])
            p3.append([x1 + 0.25 * c, y2, 0.0])
            nn.append([0.0, 0.0, 1.0])
            ar.append(c * dy)
            ll.append(c)
            pan_y.append(ym)
            pan_x.append(x1 + 0.25 * c)
    grid = {"offset_j": np.array(oj), "offset_l": np.array(ol), "offset_k": np.array(ol),
            "offset_P1": np.array(p1), "offset_P3": np.array(p3), "N": np.array(nn),
            "A": np.array(ar), "l": np.array(ll), "n": len(ar)}
    return grid, np.array(pan_y), np.array(pan_x)


# --------------------------------------------------------------------------- p-k flutter solver
def _pk_point(v: float, m: np.ndarray, kk: np.ndarray, aero, n_modes: int,
              k_start: Sequence[float]) -> List[Tuple[float, float, float]]:
    """One speed: (frequency rad/s, damping g, matched k) for each tracked mode.

    p-k in Hassig's frequency-matched form: at a frozen reduced frequency the aerodynamic matrix
    is constant, so (K - Q(k)) x = lambda M x gives complex lambda = omega^2; the frequency is
    re(lambda)^0.5 and the required structural damping is g = -im(lambda)/re(lambda). Iterating
    k = omega b / V to consistency gives the matched point.
    """
    from scipy.linalg import eig
    out = []
    for n in range(n_modes):
        k = max(1e-4, float(k_start[n]))
        om = g = float("nan")
        for _ in range(120):
            q = aero(k, v)
            lam = eig(kk - q, m, right=False)
            lam = np.asarray(lam)
            re = np.real(lam)
            order = np.argsort(np.where(re > 0, re, np.inf))
            lam_s = lam[order]
            if n >= len(lam_s) or not np.isfinite(np.real(lam_s[n])) or np.real(lam_s[n]) <= 0:
                om = g = float("nan")
                break
            om = math.sqrt(float(np.real(lam_s[n])))
            # lambda = omega^2 (1 + i gamma); a positive imaginary part is physical damping
            # (compare m zddot + c zdot + k z = 0, whose root is omega^2 = (k + i omega c)/m),
            # so the REQUIRED structural damping of the p-k method is g = -gamma: negative while
            # the mode is stable, zero at the flutter boundary, positive beyond it.
            g = -float(np.imag(lam_s[n]) / np.real(lam_s[n]))
            k_new = om * aero.b / v
            if abs(k_new - k) <= 1e-9 * max(1.0, k):
                break
            k = 0.5 * (k + k_new)
        out.append((om, g, k))
    return out


class _Aero:
    """Callable Q(k, V) that also carries the semichord used to match the reduced frequency."""

    def __init__(self, fn, b: float):
        self.fn, self.b = fn, b

    def __call__(self, k: float, v: float) -> np.ndarray:
        return self.fn(k, v)


def _flutter(m, kk, zs, als, y, b, a, rho, speeds, aero_kind, n_chord, mach, g_noise=0.01):
    if aero_kind == "dlm":
        def fn(k, v):
            return _gen_aero_dlm(k, v, b, a, rho, y, zs, als, n_chord, mach)
    else:
        def fn(k, v):
            return _gen_aero_strip(k, v, b, a, rho, y, zs, als)
    aero = _Aero(fn, b)

    w0 = _natural_frequencies(m, kk)
    n_modes = len(w0)
    k_start = [max(1e-3, w * b / max(speeds[0], 1e-6)) for w in w0]

    table: List[Tuple[float, int, float, float]] = []
    flutter: Optional[Tuple[float, float, int]] = None
    prev: Optional[List[Tuple[float, float, float]]] = None
    for v in speeds:
        pts = _pk_point(v, m, kk, aero, n_modes, k_start)
        k_start = [p[2] if math.isfinite(p[2]) else ks for p, ks in zip(pts, k_start)]
        for n, (om, g, _k) in enumerate(pts):
            if math.isfinite(om):
                table.append((v, n, om, g))
        if prev is not None and flutter is None:
            for n, ((om0, g0, _), (om1, g1, _)) in enumerate(zip(prev, pts)):
                # a real crossing leaves a clearly damped state or clearly goes unstable; a mode
                # hovering at |g| < g_noise (almost no aerodynamic damping at low dynamic
                # pressure, p-k converged to its tolerance) is numerical noise, not flutter
                if (math.isfinite(g0) and math.isfinite(g1) and g0 < 0.0 <= g1
                        and (g0 < -g_noise or g1 >= g_noise) and om1 > 1e-6):
                    t = (0.0 - g0) / (g1 - g0)
                    vf = v_prev + t * (v - v_prev)
                    omf = om0 + t * (om1 - om0)
                    flutter = (vf, omf, n)
                    break
        prev, v_prev = pts, v
    return flutter, table, w0


def _refine_flutter(m, kk, zs, als, y, b, a, rho, v_lo, v_hi, mode, aero_kind, n_chord, mach):
    """Bisection on the damping crossing of one mode, to a 0.1 % speed interval."""
    if aero_kind == "dlm":
        def fn(k, v):
            return _gen_aero_dlm(k, v, b, a, rho, y, zs, als, n_chord, mach)
    else:
        def fn(k, v):
            return _gen_aero_strip(k, v, b, a, rho, y, zs, als)
    aero = _Aero(fn, b)
    n_modes = m.shape[0]
    w0 = _natural_frequencies(m, kk)
    om_f = float("nan")
    for _ in range(40):
        vm = 0.5 * (v_lo + v_hi)
        pts = _pk_point(vm, m, kk, aero, n_modes, [max(1e-3, w * b / vm) for w in w0])
        om, g, _ = pts[mode]
        if not math.isfinite(g):
            break
        om_f = om
        if g < 0.0:
            v_lo = vm
        else:
            v_hi = vm
        if (v_hi - v_lo) <= 1e-3 * v_hi:
            break
    return 0.5 * (v_lo + v_hi), om_f


# --------------------------------------------------------------------------- kinds
def _run_flutter(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    rho = _pos(inp, "rho_kg_m3", RHO_SL)
    mach = _f(inp.get("mach", 0.0))
    aero_kind = str(inp.get("aero", "theodorsen")).lower()
    if aero_kind not in ("theodorsen", "dlm"):
        raise ValueError("`aero` must be `theodorsen` or `dlm`")
    model = str(inp.get("model", "uniform_beam")).lower()
    n_chord = int(inp.get("n_chord_panels", 4))

    if model == "uniform_beam":
        span = _pos(inp, "span_m")
        y = np.linspace(0.0, span, int(inp.get("n_stations", 41)))
        m, kk, zs, als = _uniform_beam_system(inp, y)
        chord = _pos(inp, "chord_m")
    elif model == "modal":
        m, kk, zs, als, y = _modal_system(inp)
        chord = _pos(inp, "chord_m")
    else:
        raise ValueError("`model` must be `uniform_beam` or `modal`")
    b = 0.5 * chord
    a = _f(inp.get("a_ea", 0.0))
    if not -1.0 <= a <= 1.0:
        raise ValueError("`a_ea` is the elastic axis in semichords from mid-chord: -1 <= a_ea <= 1")

    v_min = _pos(inp, "v_min_m_s", 10.0)
    v_max = _pos(inp, "v_max_m_s", 400.0)
    n_v = int(inp.get("n_speeds", 60))
    if v_max <= v_min or not (5 <= n_v <= 400):
        raise ValueError("need v_max > v_min and 5 <= n_speeds <= 400")
    speeds = list(np.linspace(v_min, v_max, n_v))

    g_noise = _f(inp.get("g_noise", 0.01))
    flutter, table, w0 = _flutter(m, kk, zs, als, y, b, a, rho, speeds,
                                  aero_kind, n_chord, mach, g_noise)
    values: Dict[str, Any] = {
        "natural_frequencies_rad_s": [round(float(w), 4) for w in w0],
        "natural_frequencies_hz": [round(float(w) / (2 * math.pi), 4) for w in w0],
        "aero": aero_kind, "model": model, "rho_kg_m3": rho,
        "speed_range_m_s": [v_min, v_max],
    }
    hover = sorted({n + 1 for (_v, n, _om, g) in table if 0.0 <= g < g_noise}
                   - ({int(flutter[2]) + 1} if flutter is not None else set()))
    if hover:
        values["lightly_damped_modes"] = hover
    if flutter is not None:
        step = (v_max - v_min) / (n_v - 1)
        vf, omf = _refine_flutter(m, kk, zs, als, y, b, a, rho,
                                  max(v_min, flutter[0] - step), flutter[0] + step,
                                  flutter[2], aero_kind, n_chord, mach)
        values.update({
            "flutter_speed_m_s": round(vf, 4),
            "flutter_frequency_rad_s": round(omf, 4),
            "flutter_frequency_hz": round(omf / (2 * math.pi), 4),
            "flutter_mode": int(flutter[2]) + 1,
            "flutter_dynamic_pressure_pa": round(0.5 * rho * vf * vf, 3),
            "flutter_found": 1.0,
        })
        head = (f"flutter at {vf:.1f} m/s, {omf / (2 * math.pi):.2f} Hz "
                f"(mode {flutter[2] + 1} of {len(w0)})")
    else:
        # no crossing in the sweep: the flutter speed is not known, but it is above v_max, and
        # that bound is usually what the criterion needs. Put the limit on
        # `flutter_speed_at_least_m_s` rather than on a speed that was never computed.
        values.update({"flutter_found": 0.0,
                       "flutter_speed_m_s": None, "flutter_frequency_rad_s": None,
                       "flutter_speed_at_least_m_s": v_max})
        head = (f"no flutter crossing between {v_min:.0f} and {v_max:.0f} m/s: the boundary is "
                f"above {v_max:.0f} m/s, raise `v_max_m_s` to find it")

    div = _divergence_from_system(m, kk, zs, als, y, b, a, rho)
    if div is not None:
        values["divergence_speed_m_s"] = round(div, 4)

    out = work / "vg.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["V_m_s", "mode", "frequency_rad_s", "frequency_hz", "damping_g"])
        for v, n, om, g in table:
            wr.writerow([f"{v:.4f}", n + 1, f"{om:.5f}", f"{om / (2 * math.pi):.5f}", f"{g:.6f}"])

    notes = ["linear, small perturbations"]
    notes.append("Theodorsen strip theory: 2-D, incompressible (M < 0.3), no tip or sweep effect"
                 if aero_kind == "theodorsen" else
                 f"DLM: subsonic thin surface at M = {mach:g}, not valid transonically")
    notes.append("g is the REQUIRED structural damping of the p-k method, not a damping ratio")
    if values.get("lightly_damped_modes"):
        notes.append(f"modes {values['lightly_damped_modes']} hover at 0 <= g < {g_noise:g} (little "
                     "aerodynamic damping, p-k noise): not flutter, but they rely on structural damping")
    return result(values, inp.get("limits"), head, files=[str(out)], notes=notes)


def _divergence_from_system(m, kk, zs, als, y, b, a, rho) -> Optional[float]:
    """Divergence: the steady (k = 0) aerodynamic stiffness that makes K singular.

    At k = 0 the generalized aerodynamic matrix is proportional to V^2, so
    (K - V^2 Q1) x = 0 is a linear eigenvalue problem in V^2.
    """
    from scipy.linalg import eig
    q1 = _gen_aero_strip(0.0, 1.0, b, a, rho, y, zs, als).real
    # K x = V^2 Q1 x, with Q1 singular in general (steady plunge produces no force), so the
    # generalized problem is solved rather than inverted: the infinite eigenvalues are discarded.
    try:
        lam = eig(kk, q1, right=False)
    except Exception:  # noqa: BLE001 - a singular pair simply means "no divergence here"
        return None
    pos = [float(x.real) for x in lam
           if np.isfinite(x) and abs(x.imag) < 1e-6 * max(1.0, abs(x.real)) and x.real > 0]
    if not pos:
        return None
    return math.sqrt(min(pos))


def _run_divergence(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    span = _pos(inp, "span_m")
    gj = _pos(inp, "GJ_N_m2")
    chord = _pos(inp, "chord_m")
    e = _pos(inp, "e_m")                     # aerodynamic centre to elastic axis, positive EA aft
    cla = _pos(inp, "cl_alpha_per_rad", 2.0 * math.pi)
    rho = _pos(inp, "rho_kg_m3", RHO_SL)
    # Closed form, uniform cantilever wing, strip theory (Hodges & Pierce, Introduction to
    # Structural Dynamics and Aeroelasticity, section 4.2): q_D = (pi/(2L))^2 GJ / (c e Cl_alpha)
    qd = (math.pi / (2.0 * span)) ** 2 * gj / (chord * e * cla)
    vd = math.sqrt(2.0 * qd / rho)
    # Galerkin with the torsion modes, same theory: independent check of the closed form
    y = np.linspace(0.0, span, int(inp.get("n_stations", 81)))
    n_t = int(inp.get("n_torsion_modes", 3))
    _, _, psi, psi1 = _beam_modes(span, 1, n_t, y)
    kt = np.array([[np.trapezoid(gj * psi1[i] * psi1[j], y) for j in range(n_t)] for i in range(n_t)])
    at = np.array([[np.trapezoid(chord * e * cla * psi[i] * psi[j], y) for j in range(n_t)]
                   for i in range(n_t)])
    lam = eigvals(kt, at)
    qd_g = min(float(x.real) for x in lam if np.isfinite(x) and x.real > 0)
    values = {
        "divergence_dynamic_pressure_pa": round(qd, 9),
        "divergence_speed_m_s": round(vd, 9),
        "divergence_dynamic_pressure_galerkin_pa": round(qd_g, 9),
        "galerkin_vs_closed_form_rel": round(abs(qd_g - qd) / qd, 6),
        "divergence_speed_eas_kt": round(vd / 0.514444, 3),
    }
    return result(values, inp.get("limits"),
                  f"divergence at {vd:.1f} m/s ({qd / 1000:.2f} kPa)",
                  notes=["uniform wing, strip theory, rigid chordwise section",
                         "the elastic axis must be AFT of the aerodynamic centre (e > 0) for "
                         "divergence to exist"])


def _run_fin_flutter(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """Rocket fin flutter, the NACA TN 4197 form used throughout amateur rocketry:

        Vf = a * sqrt( G / ( 1.337 AR^3 P (lambda + 1) / (2 (AR + 2) (t/c)^3) ) )

    AR = b^2/S of ONE fin (semi-span squared over fin area), lambda = tip/root chord,
    t/c on the MEAN chord, P the ambient static pressure, G the shear modulus of the fin
    material, a the local speed of sound. Empirical, solid uniform fins only.
    """
    g_shear = _pos(inp, "shear_modulus_pa")
    root = _pos(inp, "root_chord_m")
    tip = _f(inp.get("tip_chord_m", root))
    if tip < 0 or tip > root:
        raise ValueError("`tip_chord_m` must be between 0 and the root chord")
    semi = _pos(inp, "semi_span_m")
    thick = _pos(inp, "thickness_m")
    if "pressure_pa" in inp:
        p = _pos(inp, "pressure_pa")
        a_snd = _pos(inp, "speed_of_sound_m_s", A_SL)
        alt = None
    else:
        alt = _f(inp.get("altitude_m", 0.0))
        p, t_k, a_snd = _isa(alt)
    area = 0.5 * (root + tip) * semi
    ar = semi * semi / area
    lam = tip / root
    mean_chord = area / semi
    tc = thick / mean_chord
    denom = 1.337 * ar ** 3 * p * (lam + 1.0) / (2.0 * (ar + 2.0) * tc ** 3)
    vf = a_snd * math.sqrt(g_shear / denom)
    margin = _f(inp.get("design_speed_m_s", 0.0))
    values = {
        "flutter_velocity_m_s": round(vf, 6),
        "flutter_mach": round(vf / a_snd, 5),
        "aspect_ratio": round(ar, 5),
        "taper_ratio": round(lam, 5),
        "thickness_ratio": round(tc, 6),
        "fin_area_m2": round(area, 6),
        "pressure_pa": round(p, 6),
        "speed_of_sound_m_s": round(a_snd, 6),
    }
    if alt is not None:
        values["altitude_m"] = alt
    if margin > 0:
        values["flutter_margin"] = round(vf / margin, 4)
    return result(values, inp.get("limits"),
                  f"fin flutter at {vf:.0f} m/s (M {vf / a_snd:.2f})",
                  notes=["NACA TN 4197 empirical form: solid, uniform, trapezoidal fin only",
                         "screening criterion: keep a margin (1.5 on speed is usual), and note "
                         "that flutter speed falls with altitude only through pressure, so the "
                         "critical point is often low and fast, not at apogee",
                         "a built-up, hollow or composite fin needs its own stiffness analysis"])


def _isa(alt_m: float) -> Tuple[float, float, float]:
    """ISA troposphere and lower stratosphere (to 20 km): pressure, temperature, speed of sound."""
    if alt_m < -500 or alt_m > 20000:
        raise ValueError("`altitude_m` must be between -500 and 20000 m for this ISA model")
    if alt_m <= 11000:
        t = 288.15 - 0.0065 * alt_m
        p = P_SL * (t / 288.15) ** 5.255877
    else:
        t = 216.65
        p = 22632.06 * math.exp(-G0 * (alt_m - 11000.0) / (287.0528 * t))
    return p, t, math.sqrt(1.4 * 287.0528 * t)


def _run_gust(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """Discrete-gust load factor with Pratt-Walker alleviation (NACA TN 2964; CS/FAR 25.341 form).

        mu_g = 2 (W/S) / (rho c Cl_alpha g)        K_g = 0.88 mu_g / (5.3 + mu_g)
        dn   = K_g rho V U Cl_alpha / (2 (W/S))

    The SI form above is the imperial regulatory formula dn = K_g U V a / (498 W/S)
    (U ft/s, V kt, W/S psf) with the sea-level density folded into the constant.
    """
    ws = _pos(inp, "wing_loading_n_m2")
    cla = _pos(inp, "cl_alpha_per_rad", 2.0 * math.pi)
    chord = _pos(inp, "mean_chord_m")
    v = _pos(inp, "speed_m_s")
    u = _pos(inp, "gust_velocity_m_s")
    rho = _pos(inp, "rho_kg_m3", RHO_SL)
    mu = 2.0 * ws / (rho * chord * cla * G0)
    kg = 0.88 * mu / (5.3 + mu)
    dn = kg * rho * v * u * cla / (2.0 * ws)
    values = {
        "mass_ratio_mu_g": round(mu, 5),
        "gust_alleviation_factor_Kg": round(kg, 12),
        "delta_load_factor": round(dn, 9),
        "load_factor_up": round(1.0 + dn, 5),
        "load_factor_down": round(1.0 - dn, 5),
        "sharp_edged_delta_n": round(rho * v * u * cla / (2.0 * ws), 5),
    }
    return result(values, inp.get("limits"),
                  f"gust load factor 1 +/- {dn:.2f} (Kg = {kg:.3f})",
                  notes=["rigid aeroplane, regulatory discrete gust: no flexible response, no "
                         "unsteady lag, no gust gradient distance",
                         "use the certification gust velocities for the altitude, not a guess"])


def _run_aic(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """AIC of a flat rectangular grid: DLM at k, VLM at k = 0, and the steady lift slope."""
    from panelaero import DLM, VLM
    span = _pos(inp, "span_m")
    chord = _pos(inp, "chord_m")
    n_span = int(inp.get("n_span_panels", 12))
    n_chord = int(inp.get("n_chord_panels", 4))
    if not (2 <= n_span <= 60 and 1 <= n_chord <= 20):
        raise ValueError("2 <= n_span_panels <= 60 and 1 <= n_chord_panels <= 20")
    mach = _f(inp.get("mach", 0.0))
    k = _f(inp.get("k", 0.0))                  # classical reduced frequency omega/V, 1/m
    half = bool(inp.get("xz_symmetry", True))
    y0 = 0.0 if half else -span / 2.0
    y = np.linspace(y0, span / 2.0, n_span + 1)
    grid, pan_y, _pan_x = _flat_grid(y, chord, n_chord)

    qjj_vlm, _bjj = VLM.calc_Qjj(grid, Ma=mach, xz_symmetry=half)
    qjj_dlm_0 = DLM.calc_Qjj(grid, Ma=mach, k=0.0)
    qjj_vlm_nosym, _ = VLM.calc_Qjj(grid, Ma=mach, xz_symmetry=False)
    scale = float(np.max(np.abs(qjj_vlm_nosym))) or 1.0
    d0 = float(np.max(np.abs(np.real(qjj_dlm_0) - qjj_vlm_nosym))) / scale

    # steady lift slope: unit angle of attack on every panel
    wj = np.ones(grid["n"])
    cp = qjj_vlm.dot(wj)
    s_ref = span * chord * (0.5 if half else 1.0)
    cl_alpha = float(abs(np.sum(cp * grid["A"])) / s_ref) * (1.0 if half else 1.0)
    ar = span * span / (span * chord)
    helmbold = 2.0 * math.pi * ar / (2.0 + math.sqrt(ar * ar + 4.0))

    values = {
        "panels": int(grid["n"]),
        "aspect_ratio": round(ar, 5),
        "cl_alpha_per_rad": round(cl_alpha, 5),
        "helmbold_cl_alpha_per_rad": round(helmbold, 5),
        "cl_alpha_vs_helmbold_rel": round(abs(cl_alpha - helmbold) / helmbold, 5),
        "dlm_k0_vs_vlm_max_rel": round(d0, 12),
        "mach": mach,
    }
    if k > 0:
        qjj_k = DLM.calc_Qjj(grid, Ma=mach, k=k)
        cpk = qjj_k.dot(wj)
        lift_k = complex(np.sum(cpk * grid["A"]) / s_ref)
        values.update({"k": k,
                       "cl_alpha_unsteady_abs": round(abs(lift_k), 5),
                       "cl_alpha_unsteady_phase_deg": round(math.degrees(np.angle(lift_k)), 4),
                       "theodorsen_C_k_real": round(theodorsen(k * chord / 2.0).real, 5),
                       "theodorsen_C_k_imag": round(theodorsen(k * chord / 2.0).imag, 5)})
    return result(values, inp.get("limits"),
                  f"{grid['n']} panels, AR {ar:.2f}, Cl_alpha {cl_alpha:.3f} /rad",
                  notes=["PanelAero (DLR, BSD-3); AICs validated against MSC.Nastran by its authors",
                         "`k` here is the classical omega/V (1/m), not the Nastran omega*c/(2V)",
                         "flat plate, no thickness, no viscosity"])


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fns = {"flutter_wing": _run_flutter, "divergence": _run_divergence,
           "fin_flutter": _run_fin_flutter, "gust_load": _run_gust, "aic_dlm": _run_aic}
    if kind not in fns:
        raise ValueError(f"unknown kind {kind!r}; known: {', '.join(sorted(fns))}")
    return fns[kind](inp, Path(work))


# --------------------------------------------------------------------------- references
# Goland's wing (M. Goland, "The Flutter of a Uniform Cantilever Wing", J. Appl. Mech. 12(4),
# 1945): uniform cantilever, chord 1.8288 m, semi-span 6.096 m, mass 35.71 kg/m, static unbalance
# 6.53 kg m/m (centre of gravity 10 % chord aft of the elastic axis at mid-chord), pitch inertia
# about the elastic axis 8.64 kg m^2/m, EI 9.77e6 N m^2, GJ 0.987e6 N m^2. The published answer,
# reproduced in the aeroelastic literature as a standard test case, is a flutter speed of
# 137.2 m/s at a frequency of 70.7 rad/s at sea level, for the first bending and first torsion
# mode with incompressible 2-D (Theodorsen) strip aerodynamics.
GOLAND = {
    "model": "uniform_beam", "aero": "theodorsen",
    # the elastic axis sits at 33 % of the chord, i.e. 0.34 semichords AHEAD of mid-chord, and
    # the centre of gravity at 43 %, which is what gives S_alpha = 35.71 * 0.10 * 1.8288 = 6.53
    "span_m": 6.096, "chord_m": 1.8288, "a_ea": -0.34,
    "mass_per_length_kg_m": 35.71, "S_alpha_kg_m_per_m": 6.53,
    "I_alpha_kg_m2_per_m": 8.64, "EI_N_m2": 9.77e6, "GJ_N_m2": 0.987e6,
    "n_bending_modes": 1, "n_torsion_modes": 1,
    "v_min_m_s": 60.0, "v_max_m_s": 220.0, "n_speeds": 33, "rho_kg_m3": 1.225,
}

# Closed-form divergence of a uniform cantilever wing, strip theory:
#   q_D = (pi/(2L))^2 GJ / (c e Cl_alpha),  V_D = sqrt(2 q_D / rho)
_DIV = {"span_m": 6.096, "chord_m": 1.8288, "GJ_N_m2": 0.987e6, "e_m": 0.2,
        "cl_alpha_per_rad": 2.0 * math.pi, "rho_kg_m3": 1.225}
_QD = ((math.pi / (2.0 * _DIV["span_m"])) ** 2 * _DIV["GJ_N_m2"]
       / (_DIV["chord_m"] * _DIV["e_m"] * _DIV["cl_alpha_per_rad"]))
_VD = math.sqrt(2.0 * _QD / _DIV["rho_kg_m3"])

# Fin flutter, algebraic identity: choose the fin so that the bracket equals G exactly, which
# must give Vf = the speed of sound. Semi-span 2 m, constant chord 1 m -> area 2 m^2 and
# AR = b^2/S = 2; taper 1; thickness 0.1 m on a mean chord of 1 m -> t/c = 0.1. The bracket is
# then 1.337*2^3*P*(1+1) / (2*(2+2)*0.1^3) = 2674 P.
_FIN_G = 2674.0 * P_SL
# Same fin at 11 km: only the pressure and the speed of sound change.
_P11, _T11, _A11 = 22632.06, 216.65, math.sqrt(1.4 * 287.0528 * 216.65)
_FIN_V11 = _A11 * math.sqrt(_FIN_G / (2674.0 * _P11))

# Pratt-Walker gust, cross-check of the SI form against the imperial regulatory constant 498:
#   dn = Kg U[ft/s] V[kt] Cl_alpha / (498 W/S[psf])
_G_WS, _G_C, _G_V, _G_U, _G_CLA = 3000.0, 1.6, 100.0, 15.0, 2.0 * math.pi
_G_MU = 2.0 * _G_WS / (RHO_SL * _G_C * _G_CLA * G0)
_G_KG = 0.88 * _G_MU / (5.3 + _G_MU)
_G_DN_IMP = (_G_KG * (_G_U / 0.3048) * (_G_V / 0.514444) * _G_CLA
             / (498.0 * (_G_WS / 47.880259)))


def _goland_modal(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """Reference setup: the same Goland wing expressed in the `modal` input form, so that the
    user-supplied-modes path is checked against the published answer too."""
    span = GOLAND["span_m"]
    y = np.linspace(0.0, span, 41)
    m, kk, zs, als = _uniform_beam_system(GOLAND, y)
    out = dict(inp)
    out.update({
        "model": "modal", "stations_m": [float(v) for v in y],
        "modes": [{"z": [float(v) for v in zs[i]], "alpha_rad": [float(v) for v in als[i]]}
                  for i in range(len(zs))],
        "generalized_mass": [[float(v) for v in row] for row in m],
        "generalized_stiffness": [[float(v) for v in row] for row in kk],
    })
    return out


REFERENCES = [
    {   # the standard published flutter case; 5 % covers the mode count and the span integration
        "name": "goland_wing_flutter", "kind": "flutter_wing", "inp": dict(GOLAND),
        "expect": {"flutter_speed_m_s": [137.2, 0.05],
                   "flutter_frequency_rad_s": [70.7, 0.08],
                   # the same wing's uncoupled frequencies, 49.5 and 87.1 rad/s (Goland 1945)
                   "flutter_found": [1.0, 1e-9]},
    },
    {   # same answer through the generalized-matrices path (CalculiX modes use this path)
        "name": "goland_wing_flutter_modal", "kind": "flutter_wing", "setup": "_goland_modal",
        "inp": {k: v for k, v in GOLAND.items() if k not in ("model",)},
        "expect": {"flutter_speed_m_s": [137.2, 0.05], "flutter_frequency_rad_s": [70.7, 0.08]},
    },
    {   # closed form vs Galerkin, same theory: the Galerkin solution must reproduce it
        "name": "uniform_wing_divergence", "kind": "divergence", "inp": dict(_DIV),
        "expect": {"divergence_speed_m_s": [_VD, 1e-9],
                   "divergence_dynamic_pressure_pa": [_QD, 1e-9],
                   "galerkin_vs_closed_form_rel": [0.0, 2e-3]},
    },
    {   # DLM at k = 0 must be exactly the VLM matrix, and the VLM lift slope must match Helmbold
        "name": "dlm_k0_is_vlm", "kind": "aic_dlm",
        "inp": {"span_m": 8.0, "chord_m": 1.0, "n_span_panels": 16, "n_chord_panels": 4,
                "mach": 0.0, "xz_symmetry": True},
        "expect": {"dlm_k0_vs_vlm_max_rel": [0.0, 1e-9],
                   "cl_alpha_vs_helmbold_rel": [0.0, 0.05]},
    },
    {   # fin flutter, algebraic identity: Vf = speed of sound by construction
        "name": "fin_flutter_identity", "kind": "fin_flutter",
        "inp": {"shear_modulus_pa": _FIN_G, "root_chord_m": 1.0, "tip_chord_m": 1.0,
                "semi_span_m": 2.0, "thickness_m": 0.1, "altitude_m": 0.0},
        "expect": {"flutter_velocity_m_s": [A_SL, 1e-4], "aspect_ratio": [2.0, 1e-9],
                   "thickness_ratio": [0.1, 1e-9]},
    },
    {   # same fin at 11 km: the ISA pressure and speed of sound path
        "name": "fin_flutter_11km", "kind": "fin_flutter",
        "inp": {"shear_modulus_pa": _FIN_G, "root_chord_m": 1.0, "tip_chord_m": 1.0,
                "semi_span_m": 2.0, "thickness_m": 0.1, "altitude_m": 11000.0},
        "expect": {"flutter_velocity_m_s": [_FIN_V11, 1e-4], "pressure_pa": [_P11, 1e-4]},
    },
    {   # the SI gust formula must equal the imperial regulatory one (constant 498)
        "name": "pratt_gust_matches_imperial", "kind": "gust_load",
        "inp": {"wing_loading_n_m2": _G_WS, "mean_chord_m": _G_C, "speed_m_s": _G_V,
                "gust_velocity_m_s": _G_U, "cl_alpha_per_rad": _G_CLA},
        "expect": {"delta_load_factor": [_G_DN_IMP, 2e-3],
                   "gust_alleviation_factor_Kg": [_G_KG, 1e-9]},
    },
]
