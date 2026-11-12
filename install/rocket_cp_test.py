"""Rocket centre-of-pressure & stability via the Barrowman method (cahier §4.3).

Run with:  A:/FreeCAD/bin/freecadcmd.exe install/rocket_cp_test.py

Analytical (subsonic) aerodynamics — NO CFD/OpenFOAM needed. Computes the centre
of pressure (CP) from nose + body-transitions + fins, and the static stability
margin in calibers given the centre of gravity (CG). Prints ROCKET_CP_OK.
"""
import math
from dataclasses import dataclass, field
from typing import List

# Nose CP as a fraction of the nose length, slender-body theory:
# X/L = 1 - V/(A_base*L) (cone 2/3, paraboloid 1/2, ellipsoid 1/3, Von Karman 1/2,
# LV-Haack 0.437, tangent ogive 0.466 = Barrowman's value).
# Measured before: "cone" was 0.666 (not 2/3) and any other name -- "conical",
# the Rocket workbench's "elliptical", "parabola", "Von Karman" -- silently got
# the ogive factor: an elliptical nose came out at 0.466 L instead of 0.333 L.
NOSE_K = {"ogive": 0.466, "cone": 2.0 / 3.0, "conical": 2.0 / 3.0,
          "parabolic": 0.5, "parabola": 0.5, "elliptical": 1.0 / 3.0,
          "ellipsoid": 1.0 / 3.0, "von karman": 0.5, "haack": 0.437}


def nose_k(shape: str) -> float:
    key = str(shape).strip().lower().replace("_", " ")
    if key not in NOSE_K:
        raise ValueError(f"unknown nose shape {shape!r}; use one of {sorted(NOSE_K)}")
    return NOSE_K[key]


@dataclass
class Nose:
    length: float
    diameter: float
    shape: str = "ogive"


@dataclass
class Transition:
    x: float          # position of fore end from tip
    length: float
    d_fore: float
    d_aft: float


@dataclass
class FinSet:
    x_root_le: float  # root leading-edge position from nose tip
    root_chord: float
    tip_chord: float
    semispan: float
    sweep: float      # LE sweep distance (root LE to tip LE, axial)
    count: int
    body_d: float     # body diameter at the fins


@dataclass
class Rocket:
    d_ref: float
    nose: Nose
    transitions: List[Transition] = field(default_factory=list)
    fins: FinSet = None


def barrowman_cp(r: Rocket):
    """Return (cp_from_tip_mm, total_CN). Classic Barrowman component sum."""
    terms = []  # (CN, X)

    # Nose: CN = 2 (referenced to d_ref via area ratio; nose d == d_ref assumed)
    cn_nose = 2.0 * (r.nose.diameter / r.d_ref) ** 2
    x_nose = nose_k(r.nose.shape) * r.nose.length
    terms.append((cn_nose, x_nose))

    # Conical transitions
    for t in r.transitions:
        cn = 2.0 * ((t.d_aft / r.d_ref) ** 2 - (t.d_fore / r.d_ref) ** 2)
        # Barrowman: X = Xp + L/3 * [1 + (1 - df/da) / (1 - (df/da)^2)]; the bracket
        # equals 1 + da/(da + df), which has no 0/0 at df == da and no division
        # by zero for a boat-tail closing to a point (d_aft = 0 used to raise
        # ZeroDivisionError).
        if t.d_fore < 0 or t.d_aft < 0 or t.d_fore + t.d_aft <= 0 or t.length <= 0:
            raise ValueError(f"transition needs length > 0 and diameters >= 0: {t}")
        xt = t.x + (t.length / 3.0) * (1 + t.d_aft / (t.d_aft + t.d_fore))
        terms.append((cn, xt))

    # Fins (Barrowman)
    f = r.fins
    if f:
        # Barrowman's fin sum assumes >= 3 identical, evenly spaced fins: with 1 or
        # 2 the normal force depends on the roll angle and there is no single CP
        # (2 fins used to return a confident stability margin).
        if f.count < 3:
            raise ValueError(f"Barrowman needs >= 3 fins (got {f.count})")
        R = f.body_d / 2.0
        s = f.semispan
        # mid-chord sweep length
        lf = math.sqrt(s ** 2 + (f.sweep + (f.tip_chord - f.root_chord) / 2.0) ** 2)
        cn_fins = (1 + R / (s + R)) * (
            (4 * f.count * (s / r.d_ref) ** 2) /
            (1 + math.sqrt(1 + (2 * lf / (f.root_chord + f.tip_chord)) ** 2))
        )
        x_fins = f.x_root_le \
            + (f.sweep / 3.0) * (f.root_chord + 2 * f.tip_chord) / (f.root_chord + f.tip_chord) \
            + (1.0 / 6.0) * ((f.root_chord + f.tip_chord)
                             - (f.root_chord * f.tip_chord) / (f.root_chord + f.tip_chord))
        terms.append((cn_fins, x_fins))

    total_cn = sum(cn for cn, _ in terms)
    # A boat-tail has a NEGATIVE CN. When the sum is <= 0 the quotient is not a
    # centre of pressure (d_ref 48, nose d 24, boat-tail 24 -> 0, no fins:
    # CN = 0.5 - 0.5 -> ZeroDivisionError; nearby values: a "CP" km away).
    if total_cn <= 1e-9:
        raise ValueError(f"total CN_alpha = {total_cn:.3g} <= 0: no restoring force, "
                         "the CP is undefined (add fins)")
    cp = sum(cn * x for cn, x in terms) / total_cn
    return cp, total_cn


def stability_calibers(cp_mm: float, cg_mm: float, d_ref: float) -> float:
    return (cp_mm - cg_mm) / d_ref


# --- A plausible 3-fin model rocket ---
rocket = Rocket(
    d_ref=24.0,
    nose=Nose(length=70, diameter=24, shape="ogive"),
    fins=FinSet(x_root_le=300, root_chord=50, tip_chord=25,
                semispan=30, sweep=25, count=3, body_d=24),
)
cp, cn = barrowman_cp(rocket)
cg = 200.0  # assumed centre of gravity from tip (mm)
margin = stability_calibers(cp, cg, rocket.d_ref)
print(f"CP = {cp:.1f} mm from tip | total CN_alpha = {cn:.2f}")
print(f"CG = {cg:.1f} mm | stability margin = {margin:.2f} calibers")

assert 70 < cp < 350, "CP outside the rocket body — calc wrong"
assert cn > 0, "non-physical normal-force slope"
assert margin > 0, "CP ahead of CG -> unstable"  # stable rockets: CP behind CG
# typical model-rocket stability is ~1-2 calibers; sanity-bound it
assert 0.2 < margin < 5, f"implausible stability margin {margin:.2f}"


# --- regression: each term against a hand computation ---------------------------
def _close(a, b, tol=1e-9):
    assert abs(a - b) <= tol * max(1.0, abs(b)), (a, b)


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


# nose only: CN = 2, X = k*L for each shape (names of the Rocket workbench too)
for shape, k in (("cone", 2 / 3), ("conical", 2 / 3), ("ogive", 0.466),
                 ("parabolic", 0.5), ("elliptical", 1 / 3), ("Von Karman", 0.5)):
    cp_n, cn_n = barrowman_cp(Rocket(d_ref=24.0, nose=Nose(90, 24, shape)))
    _close(cn_n, 2.0)
    _close(cp_n, k * 90)
assert _raises(lambda: barrowman_cp(Rocket(24.0, Nose(90, 24, "ogiv")))), \
    "unknown nose shape must be refused, not computed as an ogive"

# nose + shoulder (24 -> 36) + boat-tail (36 -> 24) + 4 fins, d_ref = 24.
#   nose      CN = 2                         X = 0.466*70             = 32.62
#   shoulder  CN = 2*(1.5^2 - 1) = 2.5       X = 100 + 30/3*(1+36/60) = 116
#   boat-tail CN = 2*(1 - 1.5^2) = -2.5      X = 400 + 30/3*(1+24/60) = 414
#   fins      lf = sqrt(40^2 + (30 + (20-60)/2)^2) = sqrt(1700)
#             CN = (1 + 18/58) * 4*4*(40/24)^2 / (1 + sqrt(1 + (2*lf/80)^2))
#             X  = 330 + 30/3*(60+2*20)/80 + (80 - 1200/80)/6 = 353.3333
_lf = math.sqrt(1700.0)
_cnf = (1 + 18.0 / 58.0) * (16 * (40 / 24.0) ** 2) / (1 + math.sqrt(1 + (2 * _lf / 80.0) ** 2))
_xf = 330 + 12.5 + 65.0 / 6.0
_fins4 = FinSet(330, 60, 20, 40, 30, 4, 36)
cp_f, cn_f = barrowman_cp(Rocket(
    d_ref=24.0, nose=Nose(70, 24, "ogive"),
    transitions=[Transition(100, 30, 24, 36), Transition(400, 30, 36, 24)],
    fins=_fins4))
_close(cn_f, 2.0 + 2.5 - 2.5 + _cnf)
_close(cp_f, (2 * 32.62 + 2.5 * 116 - 2.5 * 414 + _cnf * _xf) / (2.0 + _cnf))
# the boat-tail (negative CN, far aft) moves the CP FORWARD
cp_nobt, _ = barrowman_cp(Rocket(24.0, Nose(70, 24, "ogive"),
                                 [Transition(100, 30, 24, 36)], _fins4))
assert cp_f < cp_nobt, (cp_f, cp_nobt)
# no fins, no transition: only the nose
_close(barrowman_cp(Rocket(24.0, Nose(70, 24)))[0], 32.62)
# boat-tail closing to a point: CN = -2, X = Xp + L/3 (was ZeroDivisionError)
cp_b, cn_b = barrowman_cp(Rocket(24.0, Nose(70, 24), [Transition(400, 30, 24, 0)],
                                 FinSet(330, 60, 20, 40, 30, 4, 24)))
_cnf24 = (1 + 12.0 / 52.0) * (16 * (40 / 24.0) ** 2) / (1 + math.sqrt(1 + (2 * _lf / 80.0) ** 2))
_close(cn_b, _cnf24)
_close(cp_b, (2 * 32.62 - 2 * 410 + _cnf24 * _xf) / _cnf24)
assert _raises(lambda: barrowman_cp(Rocket(48.0, Nose(70, 24), [Transition(100, 30, 24, 0)]))), \
    "total CN = 0 must be refused"
assert _raises(lambda: barrowman_cp(Rocket(24.0, Nose(70, 24),
                                           fins=FinSet(300, 50, 25, 30, 25, 2, 24)))), \
    "2 fins must be refused"
print("ROCKET_CP_OK")
