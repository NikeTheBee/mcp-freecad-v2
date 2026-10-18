"""Planar kinematics test (GAP G2) — runs under freecadcmd or system Python.

Validates the analytical linkage solver against (a) the real gripper geometry
from the pince test — must reproduce the ~4.5 deg/mm law and the toggle dead
point — and (b) a textbook crank-rocker four-bar. Also checks native_solve on
a real AssemblyObject when FreeCAD is present. Sentinel: KINEMATICS_OK
"""
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))

from freecad_layers import kinematics as kin  # noqa: E402


def main() -> int:
    # --- dyad primitive: circle/circle intersection sanity ---
    pts = kin.circle_intersect((0, 0), 5, (6, 0), 5)
    assert len(pts) == 2 and abs(pts[0][0] - 3) < 1e-9, pts
    assert kin.circle_intersect((0, 0), 1, (10, 0), 1) == [], "no-reach must be empty"

    # --- gripper (pince): pivot A, crank 17.5, link 30, slider along +z(=y) ---
    # From the assembly: A=(-20,52.25), B=(-18.77,34.79), C0=(0,11.39).
    sc = kin.SliderCrank(pivot=(-20.0, 52.25), crank=17.50, link=30.00,
                         slider_origin=(0.0, 11.39), slider_dir=(0.0, 1.0),
                         ref_coupler=(-18.77, 34.79))
    r = sc.ratio_deg_per_mm(0.0)
    print(f"gripper ratio: {r:.2f} deg/mm per finger")
    assert r is not None and abs(abs(r) - 4.5) < 0.6, r          # matches manual result
    law = dict(sc.law(range(-1, 7)))
    assert law[0] is not None and abs(law[0]) < 1e-6, law         # reference at s=0
    # closing direction: +s must rotate the finger monotonically one way
    assert law[6] is not None and law[1] is not None
    assert (law[6] - law[1]) * (law[1] - (law[0] or 0)) > 0, "non-monotonic"
    dp = sc.dead_points()
    print(f"gripper dead points (mm): {{k: round(v,2) for k,v in dp.items()}}".replace("{k: round(v,2) for k,v in dp.items()}", str({k: round(v,2) for k,v in dp.items()})))
    assert any(-3.0 < v < -1.0 for v in dp.values()), dp          # toggle ~ -2.2 mm

    # --- in-line slider-crank against the textbook law x = r cos t + sqrt(l^2 - r^2 sin^2 t) ---
    r_, l_ = 10.0, 30.0
    inline = kin.SliderCrank(pivot=(0.0, 0.0), crank=r_, link=l_, slider_origin=(40.0, 0.0),
                             slider_dir=(1.0, 0.0), ref_coupler=(r_ * math.cos(0.3), r_ * math.sin(0.3)))
    for deg in (10, 30, 60, 90, 120, 150):
        t = math.radians(deg)
        x = r_ * math.cos(t) + math.sqrt(l_ ** 2 - (r_ * math.sin(t)) ** 2)
        inline._prev_tip = (r_ * math.cos(t), r_ * math.sin(t))      # the upper branch
        got = inline.angle(x - 40.0)
        assert got is not None and abs(math.degrees(got) - deg) < 1e-9, (deg, got)
    print("in-line slider-crank: crank angle from the slider position, exact (10-150 deg): OK")

    # --- four-bar crank-rocker: output must move and stay bounded ---
    fb = kin.FourBar(a=(0, 0), d=(40, 0), r_in=15, coupler=45, r_out=25,
                     ref_theta=math.radians(45))
    fl = fb.law([math.radians(t) for t in range(30, 120, 10)])
    outs = [o for _, o in fl if o is not None]
    assert len(outs) >= 6, fl
    assert max(outs) - min(outs) > 5, "rocker should swing"
    print(f"four-bar rocker swing: {max(outs)-min(outs):.1f} deg over crank sweep")
    # ...and the rocker angle equals the triangle solution (law of cosines on D-B-C)
    for deg in (40, 60, 90, 110):
        t = math.radians(deg)
        bx, by = 15 * math.cos(t), 15 * math.sin(t)
        bd = math.hypot(bx - 40, by)
        psi = math.atan2(by, bx - 40)
        gam = math.acos((25 ** 2 + bd ** 2 - 45 ** 2) / (2 * 25 * bd))
        cands = [psi + gam, psi - gam]
        want = max(cands, key=lambda a: 25 * math.sin(a))          # elbow up: the higher C
        fb._prev_c = None
        got = fb.output_angle(t)
        assert abs(math.remainder(got - want, 2 * math.pi)) < 1e-9, (deg, got, want)
    print("four-bar rocker angle = law-of-cosines solution: OK")

    # --- drag link (ground shortest): B goes around D, so "the higher C" changes assembly
    # mode on the way. The law at 180 deg depended on where the sweep began (-208.5 deg from
    # 0, +35.7 deg started at 180); the mode is now the side of B->D, fixed at ref_theta.
    dl = kin.FourBar(a=(0, 0), d=(1, 0), r_in=3, coupler=3.5, r_out=3, ref_theta=0.0)
    swept = dict(dl.law([math.radians(t) for t in range(0, 361, 10)]))
    alone = dict(dl.law([math.pi]))
    assert abs(swept[180.0] - alone[180.0]) < 1e-9, (swept[180.0], alone[180.0])
    # by hand: B=(-3,0), C below B->D... -> output -122.09 deg, reference +86.42 deg
    assert abs(swept[180.0] - 151.4934) < 1e-3, swept[180.0]
    assert abs(swept[360.0] - 360.0) < 1e-9, swept[360.0]     # a double crank: one output turn
    # same for the slider-crank: one stroke position, wherever the sweep starts
    assert abs(dict(sc.law([5]))[5] - law[5]) < 1e-9, (sc.law([5]), law[5])
    print("drag link / slider-crank: same output wherever a sweep starts, full turn = 360 deg: OK")

    # --- native solver passthrough (only if FreeCAD present) ---
    try:
        import FreeCAD as App
        doc = App.newDocument("KinNative")
        asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
        res = kin.native_solve(asm)
        print(f"native_solve on empty assembly: {res}")
        assert res["ok"], res
        # a non-assembly must be refused cleanly
        box = doc.addObject("Part::Box", "Box")
        assert not kin.native_solve(box)["ok"]
        print("native_solve refuses non-assembly: OK")
    except ImportError:
        print("FreeCAD not importable here — native_solve path skipped")

    print("KINEMATICS_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.stdout.flush()  # freecadcmd drops unflushed output on sys.exit
    sys.exit(rc)
