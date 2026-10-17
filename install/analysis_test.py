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
    # "through" = both ends open onto air, not "as long as the whole part": the
    # base hole of an L-bracket and the bore under a counterbore read "blind"
    lb = Part.makeBox(60, 40, 5).fuse(Part.makeBox(5, 40, 50)).removeSplitter()
    lh = analysis.holes(lb.cut(Part.makeCylinder(3, 5, V(30, 20, 0))))
    assert len(lh) == 1 and lh[0]["through"] and lh[0]["depth"] == 5, lh
    cb = Part.makeBox(40, 40, 10).cut(Part.makeCylinder(3, 10, V(20, 20, 0)))
    ch = analysis.holes(cb.cut(Part.makeCylinder(5.5, 4, V(20, 20, 6))))
    assert [(h["diameter"], h["through"]) for h in ch] == [(6, True), (11, False)], ch
    # ... and the bore and its counterbore are ONE hole (same "hole" number)
    assert ch[0]["hole"] == ch[1]["hole"], ch
    assert "holes (1, 2 bores)" in analysis.report(cb.cut(Part.makeCylinder(5.5, 4, V(20, 20, 6))))
    # one pin hole through both flanges of a clevis: two 4 mm bores (it read ONE
    # 24 mm bore across the air), one hole
    uc = Part.makeBox(40, 30, 4).fuse(Part.makeBox(40, 30, 4, V(0, 0, 20)))
    uh = analysis.holes(uc.fuse(Part.makeBox(4, 30, 24)).removeSplitter().cut(
        Part.makeCylinder(3, 40, V(20, 15, -5))))
    assert [(h["depth"], h["through"], h["hole"]) for h in uh] == [(4, True, 1), (4, True, 1)], uh
    # two blind holes from opposite sides, material between: two blind holes (it
    # read ONE through hole)
    ob = Part.makeBox(40, 40, 10).cut(Part.makeCylinder(3, 4, V(20, 20, 0)))
    oh = analysis.holes(ob.cut(Part.makeCylinder(3, 3, V(20, 20, 7))))
    assert [(h["depth"], h["through"]) for h in oh] == [(4, False), (3, False)], oh
    assert oh[0]["hole"] != oh[1]["hole"], oh
    # a hole drilled across a bigger bore: two bores, one hole, through
    xb = Part.makeBox(40, 40, 30).cut(Part.makeCylinder(8, 40, V(0, 20, 15), V(1, 0, 0)))
    xh = [h for h in analysis.holes(xb.cut(Part.makeCylinder(3, 40, V(20, 20, -5))))
          if h["diameter"] == 6]
    assert len(xh) == 2 and xh[0]["hole"] == xh[1]["hole"] and all(h["through"] for h in xh), xh
    print("holes vs bores: counterbore, clevis, opposite blind holes, cross-drilled: OK")
    pt = Part.makeBox(40, 40, 10).cut(Part.makeCylinder(3, 6, V(20, 20, 4)))
    pt = pt.cut(Part.makeCone(3, 0, 1.8, V(20, 20, 4), V(0, 0, -1)))     # 118 deg drill point
    ph = analysis.holes(pt)
    assert len(ph) == 1 and not ph[0]["through"], ph
    cs = Part.makeBox(40, 40, 10).cut(Part.makeCylinder(3, 10, V(20, 20, 0)))
    cs = cs.cut(Part.makeCone(3, 6, 3, V(20, 20, 7)))                    # countersink
    sh2 = analysis.holes(cs)
    assert len(sh2) == 1 and sh2[0]["through"] and sh2[0]["depth"] == 7, sh2
    # tilted through a 10 mm plate: through, centred mid-plate (vertices alone
    # missed the ellipse ends: centre at z 5.86)
    tl = analysis.holes(Part.makeBox(40, 40, 10).cut(
        Part.makeCylinder(3, 40, V(20, 20, -10), V(0.3, 0, 1))))
    assert len(tl) == 1 and tl[0]["through"] and abs(tl[0]["center"][2] - 5) < 0.01, tl
    print("through = both ends open: L-bracket, counterbore, countersink, drill point, tilted: OK")
    # the same holes stored as B-splines (a STEP from another CAD): same answer;
    # a boss (convex) is still not a hole
    nb = plate.toNurbs()
    assert {type(f.Surface).__name__ for f in nb.Faces} == {"BSplineSurface"}
    assert analysis.holes(nb) == hs, (analysis.holes(nb), hs)
    boss = Part.makeBox(20, 20, 5).fuse(Part.makeCylinder(3, 10, V(10, 10, 5))).toNurbs()
    assert analysis.holes(boss) == [], analysis.holes(boss)
    print("B-spline bores fitted (same holes as the analytic part), boss rejected: OK")
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
    # a hole close to an edge: the thin wall lies between sample points (0.3 mm read
    # 0.81, above the 0.8 printing minimum); the closest faces are measured exactly
    for wall in (0.3, 0.5):
        edge = Part.makeBox(100, 60, 5).cut(Part.makeCylinder(3, 5, V(3 + wall, 30, 0)))
        we = analysis.min_wall(edge)
        assert abs(we["min_mm"] - wall) < 1e-6, (wall, we)
    assert analysis.printability(edge.copy())["status"] == "FAIL"     # 0.5 < 0.8 mm
    print("thin wall between a hole and an edge: exact (0.3, 0.5 mm), printability FAIL: OK")
    # crossing holes leave a knife edge where they meet (it thins to 0 at their edge):
    # a corner, not a wall -- it read 0.03 mm and failed printability. The thinnest real
    # wall here is the D6 hole to the x=0 face: 7 mm
    cross = Part.makeBox(40, 30, 20).cut(Part.makeCylinder(3, 20, V(10, 15, 0)))
    cross = cross.cut(Part.makeCylinder(2, 8, V(30, 15, 12)))
    cross = cross.cut(Part.makeCylinder(2.5, 40, V(0, 15, 10), V(1, 0, 0)))
    wx = analysis.min_wall(cross)
    assert abs(wx["min_mm"] - 7.0) < 1e-6, wx
    print("crossing holes: the knife edge where they meet is not a wall (7 mm, was 0.03): OK")
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
    # curved faces, judged by their middle normal alone, all read 0: the ceiling of a
    # horizontal hole, the underside of a sphere, a rod lying down. Known values: the
    # part of the surface whose normal is within 45 deg of straight down.
    import math
    lim = 1 - math.cos(math.pi / 4)
    for what, shp, want in (
            ("sphere r10 on the bed", Part.makeSphere(10, V(0, 0, 10)), 2 * math.pi * 100 * lim),
            ("rod r5 L20 lying down", Part.makeCylinder(5, 20, V(0, 0, 5), V(1, 0, 0)), 2 * math.pi * 5 * 20 / 4),
            ("horizontal hole D6 L20", Part.makeBox(20, 20, 20).cut(
                Part.makeCylinder(3, 20, V(0, 10, 10), V(1, 0, 0))), 2 * math.pi * 3 * 20 / 4)):
        got = analysis.overhangs(shp)["area_mm2"]
        assert abs(got - want) < 0.01 * want, (what, got, want)
    # exactly at the limit is printable without supports (rounding put half of a 45 deg
    # cone over it); a 30 deg slope is not
    cham = Part.makeBox(20, 20, 10)
    cham = cham.makeChamfer(5, [e for e in cham.Edges if e.BoundBox.ZMax < 1e-9])
    assert analysis.overhangs(cham)["area_mm2"] == 0
    assert analysis.overhangs(Part.makeCone(10, 0, 10, V(0, 0, 10), V(0, 0, -1)))["area_mm2"] == 0
    c30 = Part.makeCone(10 * math.tan(math.radians(60)), 0, 10, V(0, 0, 10), V(0, 0, -1))
    side = [f for f in c30.Faces if f.Surface.TypeId == "Part::GeomCone"][0]
    assert abs(analysis.overhangs(c30)["area_mm2"] - side.Area) < 0.01, analysis.overhangs(c30)
    print("overhangs of curved faces within 1 % (sphere, rod, horizontal hole); 45 deg walls free: OK")
    # assembly: each part sits on its own plate (a pin hanging below a plate made the
    # plate's bottom an "overhang"), and face names stay those of the whole shape
    plate_pin2 = Part.makeCompound([Part.makeBox(60, 40, 8).cut(Part.makeCylinder(3, 8, V(30, 20, 0))),
                                    Part.makeCylinder(2.9, 30, V(30, 20, -5))])
    assert analysis.overhangs(plate_pin2)["area_mm2"] == 0, analysis.overhangs(plate_pin2)
    cmp = Part.makeCompound([Part.makeBox(5, 5, 5, V(100, 0, 0)), tee])
    oc = analysis.overhangs(cmp)
    named = sum(cmp.Faces[int(n[4:]) - 1].Area for n in oc["faces"])
    assert abs(oc["area_mm2"] - 300) < 1e-6 and abs(named - 300) < 1e-6, oc
    print("assembly overhangs judged per part, faces named in the whole shape: OK")

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
    # an assembly: two 150 mm parts side by side, 310 mm in all. Printed one by one they
    # fit a 220 bed (OK); the advisory says that together they would not
    feat(doc, "Pair", Part.makeCompound([Part.makeBox(150, 20, 10),
                                         Part.makeBox(150, 20, 10, V(160, 0, 0))]))
    doc.recompute()
    pp = analysis.printability("Pair")
    assert pp["status"] == verify.OK, pp
    assert any("each judged as printed on its own" in a and "do NOT fit" in a
               for a in pp["advisories"]), pp
    feat(doc, "PairBig", Part.makeCompound([Part.makeBox(150, 20, 10),
                                            Part.makeBox(300, 20, 10, V(0, 40, 0))]))
    doc.recompute()
    pq = analysis.printability("PairBig")
    assert pq["status"] == verify.FAIL and any("solid2 does not fit" in i for i in pq["issues"]), pq
    print("assembly printability: per part, with the print-together advisory: OK")

    print(analysis.report("Plate"))
    print("ANALYSIS_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.exit(rc)
