"""Part analysis (holes, walls, overhangs, printability) — runs under freecadcmd.

Known-value cases, including the parts the old CAM layer mistook for holes.
Sentinel: ANALYSIS_OK
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402
from freecad_layers import analysis, verify  # noqa: E402

V = App.Vector


def feat(doc, name, shape):
    o = doc.addObject("Part::Feature", name)
    o.Shape = shape
    return o


def main() -> int:
    doc = App.newDocument("AnalysisTest")

    # plate 60x40x8 with a through Ø6 at the centre and a blind Ø4 (depth 5)
    plate = Part.makeBox(60, 40, 8, V(-30, -20, 0))
    plate = plate.cut(Part.makeCylinder(3, 20, V(0, 0, -5)))
    plate = plate.cut(Part.makeCylinder(2, 10, V(20, 10, 3)))
    feat(doc, "Plate", plate)
    # block with 4 vertical fillets and NO hole (the old CAM drilled these)
    blk = Part.makeBox(40, 30, 10)
    blk = blk.makeFillet(3, [e for e in blk.Edges
                             if abs(e.Vertexes[0].Point.z - e.Vertexes[1].Point.z) > 1e-6])
    feat(doc, "Filleted", blk)
    feat(doc, "Rod", Part.makeCylinder(10, 50))
    tube = Part.makeCylinder(10, 50).cut(Part.makeCylinder(8, 60, V(0, 0, -5)))
    feat(doc, "Tube", tube)
    doc.recompute()

    hs = analysis.holes(doc.Plate)
    print("plate holes:", hs)
    assert len(hs) == 2, hs
    blind, through = hs[0], hs[1]
    assert blind["diameter"] == 4 and not blind["through"] and abs(blind["depth"] - 5) < 1e-6, blind
    assert through["diameter"] == 6 and through["through"], through
    assert through["center"][:2] == [0, 0] and abs(through["center"][2] - 4) < 1e-6, through
    for name in ("Filleted", "Rod"):
        assert analysis.holes(doc.getObject(name)) == [], name
    print("fillets and the outside of a rod are not holes: OK")
    th = analysis.holes(doc.Tube)
    assert len(th) == 1 and th[0]["diameter"] == 16 and th[0]["through"], th
    # a bore made of two half-cylinders, 5 m from the origin: still ONE hole
    half = Part.makeCylinder(4, 20, V(10, 10, 0), V(0, 0, 1), 180)
    other = half.copy()
    other.rotate(V(10, 10, 0), V(0, 0, 1), 180)
    split = Part.makeBox(20, 20, 20).cut(half).cut(other)
    split.translate(V(5000, -3000, 2000))
    sh = analysis.holes(split)
    assert len(sh) == 1 and sh[0]["diameter"] == 8 and sh[0]["through"], sh
    assert sh[0]["center"] == [5010, -2990, 2010], sh
    print("split bore far from the origin is one hole: OK")
    # in an assembly, "through" is judged per part: a pin standing in a plate's through
    # hole made the compound taller, and the hole used to read "blind"
    plate_pin = Part.makeCompound([Part.makeBox(60, 40, 8).cut(Part.makeCylinder(3, 8, V(30, 20, 0))),
                                   Part.makeCylinder(2.9, 30, V(30, 20, -5))])
    ah = analysis.holes(plate_pin)
    assert len(ah) == 1 and ah[0]["through"] and ah[0]["depth"] == 8, ah
    print("assembly: a plate's hole stays through with a pin in it: OK")

    # walls: tube wall is 2 mm; a 20 mm cube with a 17 mm pocket leaves 1.5 mm
    # (exact B-rep intersections: no mesh chord error, so a tight tolerance)
    w = analysis.min_wall(doc.Tube)
    print("tube min wall:", w)
    assert abs(w["min_mm"] - 2.0) < 1e-3, w
    cup = Part.makeBox(20, 20, 20).cut(Part.makeBox(17, 17, 30, V(1.5, 1.5, 1.5)))
    wc = analysis.min_wall(cup)
    print("cup min wall:", wc)
    assert abs(wc["min_mm"] - 1.5) < 1e-3, wc
    for label, shell, expected in (
            ("sphere shell", Part.makeSphere(10).cut(Part.makeSphere(9)), 1.0),
            ("torus shell", Part.makeTorus(30, 5).cut(Part.makeTorus(30, 4)), 1.0)):
        ws = analysis.min_wall(shell)
        assert abs(ws["min_mm"] - expected) < 1e-3 and "unchecked" not in ws, (label, ws)
    print("curved shells (sphere, torus) 1.0 mm: OK")
    # a thin spot on the LAST faces of a many-faced part is still found (the
    # old version stopped sampling after the first faces)
    grid = Part.makeBox(120, 120, 10)
    grid = grid.cut(Part.makeCompound([Part.makeCylinder(3, 10, V(10 + i * 20, 10 + j * 20, 0))
                                       for i in range(6) for j in range(6)])).removeSplitter()
    grid = grid.cut(Part.makeBox(5, 20, 10, V(119.2 - 5, 90, 0)))   # 0.8 mm to the x=120 edge
    wg = analysis.min_wall(grid)
    print("grid with a thin spot:", wg)
    assert abs(wg["min_mm"] - 0.8) < 1e-3 and wg["faces_sampled"] == wg["faces"], wg

    # overhangs: a T (bar on a post) overhangs; the plain plate does not
    tee = Part.makeBox(10, 10, 20, V(15, 0, 0)).fuse(Part.makeBox(40, 10, 5, V(0, 0, 20)))
    tee = tee.removeSplitter()
    ov = analysis.overhangs(tee)
    print("tee overhang:", ov)
    assert abs(ov["area_mm2"] - 300) < 1e-6, ov
    assert analysis.overhangs(doc.Plate)["area_mm2"] == 0

    # printability: plate OK on a 220x220x250 bed; a 300 mm bar does not fit
    pr = analysis.printability("Plate")
    print("plate printability:", pr["status"], pr["issues"], pr["advisories"])
    assert pr["status"] == verify.OK, pr
    feat(doc, "LongBar", Part.makeBox(300, 10, 10))
    doc.recompute()
    pb = analysis.printability("LongBar")
    assert pb["status"] == verify.FAIL and any("build volume" in i for i in pb["issues"]), pb
    feat(doc, "Thin", Part.makeBox(30, 30, 0.4))
    doc.recompute()
    pt = analysis.printability("Thin")
    assert pt["status"] == verify.FAIL and any("wall" in i for i in pt["issues"]), pt
    print("printability verdicts: OK")

    print(analysis.report("Plate"))
    print("ANALYSIS_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.exit(rc)
