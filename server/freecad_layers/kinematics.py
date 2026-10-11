"""Planar kinematics — linkage input/output laws (GAP G2).

Two complementary paths for §4.2 (FK) / §4.4 (transmissions):

1. **Analytical planar solver** (this module's core) — dependency-free, fully
   headless, exact. Solves revolute/slider linkages (slider-crank, four-bar,
   and the RRR dyad primitive they are built from). This is what solved the
   `examples/` gripper to 3 µm; here it is generalized and tested.

2. **Native Assembly solver passthrough** — `native_solve(assembly)` runs
   FreeCAD's built-in OndselSolver (`AssemblyObject.solve()`), which DOES work
   headless. Creating the joints, however, needs element references and is
   GUI-coupled (see docs/COMPATIBILITY.md R8) — so we surface the solver, not
   fragile headless joint authoring.

Secure by design: pure math + a read-only solver call; no file or network I/O.
Convention: planar points are (x, y) tuples in mm; angles in radians unless a
`_deg` helper says otherwise.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

Point = Tuple[float, float]


# --- core primitive: RRR dyad = circle/circle intersection --------------------
def circle_intersect(a: Point, ra: float, c: Point, rc: float
                     ) -> List[Point]:
    """Points at distance `ra` from `a` and `rc` from `c` (0, 1 or 2).

    The building block of every planar linkage: the free joint of a two-link
    (RRR) dyad whose ends are pinned at `a` and `c`.
    """
    ax, ay = a
    cx, cy = c
    dx, dy = cx - ax, cy - ay
    d = math.hypot(dx, dy)
    if d == 0:
        return []                      # concentric — no discrete solution
    if d > ra + rc + 1e-9 or d < abs(ra - rc) - 1e-9:
        return []                      # no reach (dead point / disassembled)
    aa = (ra * ra - rc * rc + d * d) / (2 * d)
    h2 = ra * ra - aa * aa
    h = math.sqrt(max(0.0, h2))
    xm, ym = ax + aa * dx / d, ay + aa * dy / d
    ox, oy = -dy / d * h, dx / d * h
    if h <= 1e-9:
        return [(xm, ym)]
    return [(xm + ox, ym + oy), (xm - ox, ym - oy)]


def _pick(branch_points: List[Point], near: Point) -> Optional[Point]:
    if not branch_points:
        return None
    return min(branch_points, key=lambda p: (p[0]-near[0])**2 + (p[1]-near[1])**2)


# --- slider-crank (the gripper finger case) -----------------------------------
class SliderCrank:
    """Crank pinned at `pivot` (length `crank`), coupler `link` to a point that
    slides along `slider_dir` through `slider_origin`.

    Input = signed slider displacement `s` (mm) from slider_origin.
    Output = crank angle (rad), continuous with a reference branch.
    """

    def __init__(self, pivot: Point, crank: float, link: float,
                 slider_origin: Point, slider_dir: Point = (0.0, 1.0),
                 ref_coupler: Optional[Point] = None):
        self.pivot = pivot
        self.crank = crank
        self.link = link
        self.slider_origin = slider_origin
        n = math.hypot(*slider_dir)
        self.slider_dir = (slider_dir[0]/n, slider_dir[1]/n)
        # reference crank tip: nearest branch at s=0, or supplied hint
        c0 = self._slide(0.0)
        pts = circle_intersect(pivot, crank, c0, link)
        if ref_coupler is None:
            ref_coupler = _pick(pts, (pivot[0], pivot[1]-crank)) or (pts[0] if pts else pivot)
        self._ref_tip = _pick(pts, ref_coupler) or ref_coupler
        self.ref_angle = math.atan2(self._ref_tip[1]-pivot[1],
                                    self._ref_tip[0]-pivot[0])
        self._prev_tip = self._ref_tip

    def _slide(self, s: float) -> Point:
        return (self.slider_origin[0] + s*self.slider_dir[0],
                self.slider_origin[1] + s*self.slider_dir[1])

    def crank_tip(self, s: float) -> Optional[Point]:
        c = self._slide(s)
        tip = _pick(circle_intersect(self.pivot, self.crank, c, self.link),
                    self._prev_tip)
        if tip is not None:
            self._prev_tip = tip
        return tip

    def angle(self, s: float) -> Optional[float]:
        tip = self.crank_tip(s)
        if tip is None:
            return None
        return math.atan2(tip[1]-self.pivot[1], tip[0]-self.pivot[0])

    def law(self, s_values) -> List[Tuple[float, Optional[float]]]:
        """[(s, output_angle_deg_relative_to_reference)] over the stroke."""
        self._prev_tip = self._ref_tip
        out = []
        for s in s_values:
            a = self.angle(s)
            out.append((s, None if a is None else math.degrees(a - self.ref_angle)))
        return out

    def ratio_deg_per_mm(self, s: float = 0.0, eps: float = 0.5) -> Optional[float]:
        a1, a2 = self.angle(s - eps), self.angle(s + eps)
        if a1 is None or a2 is None:
            return None
        return math.degrees(a2 - a1) / (2 * eps)

    def dead_points(self) -> Dict[str, float]:
        """Slider positions where crank & coupler align (reach limits)."""
        px, py = self.pivot
        ox, oy = self.slider_origin
        dx, dy = self.slider_dir
        out = {}
        for label, reach in (("extended", self.crank + self.link),
                             ("folded", abs(self.crank - self.link))):
            # |pivot - (origin + s*dir)| = reach  -> quadratic in s
            ex, ey = ox - px, oy - py
            b = 2 * (ex*dx + ey*dy)
            c = ex*ex + ey*ey - reach*reach
            disc = b*b - 4*c
            if disc >= 0:
                roots = ((-b - math.sqrt(disc)) / 2, (-b + math.sqrt(disc)) / 2)
                out[label] = min(roots, key=abs)    # the one nearest the working point
        return out

    def dead_points_all(self) -> Dict[str, Tuple[float, ...]]:
        """Both slider positions (if any) for each alignment, sorted."""
        px, py = self.pivot
        ox, oy = self.slider_origin
        dx, dy = self.slider_dir
        out = {}
        for label, reach in (("extended", self.crank + self.link),
                             ("folded", abs(self.crank - self.link))):
            ex, ey = ox - px, oy - py
            b = 2 * (ex*dx + ey*dy)
            c = ex*ex + ey*ey - reach*reach
            disc = b*b - 4*c
            if disc >= 0:
                out[label] = tuple(sorted(((-b - math.sqrt(disc)) / 2,
                                           (-b + math.sqrt(disc)) / 2)))
        return out


# --- four-bar -----------------------------------------------------------------
class FourBar:
    """Ground pivots A (input) and D (output); crank AB=r_in, coupler BC=coupler,
    rocker DC=r_out. Input = crank angle theta_in (rad). Output = rocker angle."""

    def __init__(self, a: Point, d: Point, r_in: float, coupler: float,
                 r_out: float, ref_theta: float = 0.0, elbow_up: bool = True):
        self.a, self.d = a, d
        self.r_in, self.coupler, self.r_out = r_in, coupler, r_out
        self.elbow_up = elbow_up
        self._prev_c = None
        c = self._coupler_point(ref_theta)
        if c is not None:
            self.ref_out = math.atan2(c[1]-d[1], c[0]-d[0])
        else:
            self.ref_out = 0.0

    def _coupler_point(self, theta_in: float) -> Optional[Point]:
        b = (self.a[0] + self.r_in*math.cos(theta_in),
             self.a[1] + self.r_in*math.sin(theta_in))
        pts = circle_intersect(b, self.coupler, self.d, self.r_out)
        if not pts:
            return None
        if self._prev_c is not None:
            c = _pick(pts, self._prev_c)
        else:
            c = max(pts, key=lambda p: p[1]) if self.elbow_up else min(pts, key=lambda p: p[1])
        self._prev_c = c
        return c

    def output_angle(self, theta_in: float) -> Optional[float]:
        c = self._coupler_point(theta_in)
        if c is None:
            return None
        return math.atan2(c[1]-self.d[1], c[0]-self.d[0])

    def law(self, theta_values) -> List[Tuple[float, Optional[float]]]:
        self._prev_c = None
        out = []
        for t in theta_values:
            a = self.output_angle(t)
            out.append((math.degrees(t),
                        None if a is None else math.degrees(a - self.ref_out)))
        return out


# --- native solver passthrough ------------------------------------------------
def native_solve(assembly_obj) -> Dict[str, object]:
    """Run FreeCAD's built-in Assembly (OndselSolver) solve() headlessly.

    For assemblies whose joints were authored in the GUI. Returns a compact
    dict; does not create joints (that path is GUI-coupled — see R8).
    """
    if not hasattr(assembly_obj, "solve"):
        return {"ok": False, "why": "object has no solve() — not an AssemblyObject"}
    try:
        code = assembly_obj.solve()
        return {"ok": code == 0, "convergence_code": code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"solver error: {e}"}
