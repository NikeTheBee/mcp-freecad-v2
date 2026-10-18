"""Verification traps (audit 2026-10-11, §2.2 V1–V3) — runs under freecadcmd.

Every case here once returned "OK" on a wrong part. The verifier must now
say FAIL (or UNKNOWN when a check cannot run) — never OK. Sentinel: VERIFY_OK
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402
from freecad_layers import verify  # noqa: E402

V = App.Vector


def expect(name, status, **kw):
    r = verify.check(name, **kw)
    line = verify.verdict(name, **kw)
    print(f"{status:7s} expected | {line}")
    assert r["status"] == status, (name, r)
    return r


def main() -> int:
    doc = App.newDocument("VerifyTraps")

    # healthy part: OK, with kernel measurements
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 10, 20, 30
    doc.recompute()
    r = expect("Box", verify.OK)
    assert abs(r["volume"] - 6000) < 1e-6 and r["bbox"] == [10, 20, 30], r
    assert r["center_of_mass"] == [5, 10, 15] and r["faces"] == 6, r
    assert verify.watertight("Box")
    # a bare shape (execute_python use) is checked too, not "no shape"
    bare = verify.check(Part.makeSphere(10))
    assert bare["status"] == verify.OK and bare["name"] == "shape", bare

    # V1: a wire has no solid -> FAIL (unless the caller says no solid is expected)
    w = doc.addObject("Part::Feature", "Wire")
    w.Shape = Part.makePolygon([V(0, 0, 0), V(10, 0, 0), V(10, 10, 0)])
    doc.recompute()
    expect("Wire", verify.FAIL)
    expect("Wire", verify.OK, expect_solid=False)
    assert not verify.watertight("Wire")

    # V2: self-intersecting (bow-tie) prism -> FAIL
    bow = Part.Face(Part.makePolygon([V(0, 0, 0), V(10, 10, 0), V(10, 0, 0),
                                      V(0, 10, 0), V(0, 0, 0)])).extrude(V(0, 0, 5))
    o = doc.addObject("Part::Feature", "Bowtie")
    o.Shape = bow
    doc.recompute()
    expect("Bowtie", verify.FAIL)

    # V2b: two overlapping solids in one compound -> FAIL with the overlap volume
    ov = doc.addObject("Part::Feature", "Overlap")
    ov.Shape = Part.makeCompound([Part.makeBox(10, 10, 10),
                                  Part.makeBox(10, 10, 10, V(5, 5, 5))])
    doc.recompute()
    r = expect("Overlap", verify.FAIL)
    assert any("interference" in i and "125" in i for i in r["issues"]), r

    # two solids that only TOUCH (shared face) are not an interference
    tc = doc.addObject("Part::Feature", "Touching")
    tc.Shape = Part.makeCompound([Part.makeBox(10, 10, 10),
                                  Part.makeBox(10, 10, 10, V(10, 0, 0))])
    doc.recompute()
    expect("Touching", verify.OK)

    # V3: failed recompute keeps the LAST GOOD shape -> must FAIL, not pass stale geometry
    sb = doc.addObject("Part::Box", "StaleBox")
    doc.recompute()
    sb.Length = 0
    doc.recompute()
    expect("StaleBox", verify.FAIL)

    # a part built on a broken dependency -> FAIL
    cut = doc.addObject("Part::Cut", "CutOnStale")
    cut.Base, cut.Tool = sb, box
    doc.recompute()
    expect("CutOnStale", verify.FAIL)

    # touched but never recomputed -> stale -> FAIL
    box2 = doc.addObject("Part::Box", "Edited")
    doc.recompute()
    box2.Length = 25  # no recompute
    expect("Edited", verify.FAIL)
    doc.recompute()
    expect("Edited", verify.OK)

    # UNKNOWN when a check cannot run: never reported as OK
    many = doc.addObject("Part::Feature", "Many")
    many.Shape = Part.makeCompound([Part.makeBox(1, 1, 1, V(2 * i, 0, 0)) for i in range(5)])
    doc.recompute()
    old = verify.MAX_INTERFERENCE_SOLIDS
    verify.MAX_INTERFERENCE_SOLIDS = 3
    try:
        r = expect("Many", verify.UNKNOWN)
        assert r["ok"] is False
    finally:
        verify.MAX_INTERFERENCE_SOLIDS = old

    # isValid() is skipped after a clean OCCT check (same analysis): a broken
    # shape must still be reported invalid
    op = doc.addObject("Part::Feature", "OpenShell")
    op.Shape = Part.Solid(Part.Shell(Part.makeBox(10, 10, 10).Faces[:5]))
    doc.recompute()
    r = expect("OpenShell", verify.FAIL)
    assert r["valid"] is False and "shape.isValid() is False" in r["issues"], r
    print("broken shape still reported invalid: OK")

    # the OCCT check is remembered per shape: an unchanged shape is not
    # re-analysed, a changed one always is (never a stale verdict)
    verify.check("Box")
    n = len(verify._SHAPE_CHECKS)
    verify.check("Box")
    assert len(verify._SHAPE_CHECKS) == n, "unchanged shape analysed twice"
    sw = doc.addObject("Part::Feature", "Swap")
    sw.Shape = Part.makeBox(10, 10, 5)
    doc.recompute()
    expect("Swap", verify.OK)
    sw.Shape = bow                       # same object, now the bow-tie
    doc.recompute()
    expect("Swap", verify.FAIL)
    sw.Shape = Part.makeBox(10, 10, 5)
    doc.recompute()
    expect("Swap", verify.OK)
    assert len(verify._SHAPE_CHECKS) <= verify._SHAPE_CHECKS_SIZE
    print("OCCT check cache: no stale verdict: OK")

    # assembly-level interference between separate parts
    a = doc.addObject("Part::Box", "PartA")
    b = doc.addObject("Part::Box", "PartB")
    b.Placement.Base = V(8, 0, 0)
    c = doc.addObject("Part::Box", "PartC")
    c.Placement.Base = V(40, 0, 0)
    doc.recompute()
    asm = verify.check_assembly(["PartA", "PartB", "PartC"])
    print("assembly:", asm)
    assert asm["status"] == verify.FAIL and len(asm["interferences"]) == 1, asm
    assert abs(asm["interferences"][0]["volume"] - 200) < 1e-6, asm
    # parts without a solid are refused: a sketch "overlapped nothing" (silent OK), a
    # shell gave 400 mm3 for a 500 mm3 overlap, an empty Body raised a bounding-box error
    sh = doc.addObject("Part::Feature", "AsmShell")
    sh.Shape = Part.makeBox(10, 10, 10, V(5, 0, 0)).Shells[0]
    sk = doc.addObject("Sketcher::SketchObject", "AsmSketch")
    sk.addGeometry(Part.Circle(V(5, 5, 0), V(0, 0, 1), 3))
    doc.addObject("PartDesign::Body", "AsmEmpty")
    doc.recompute()
    for other, why in (("AsmShell", "not a solid"), ("AsmSketch", "not a solid"),
                       ("AsmEmpty", "no shape")):
        try:
            verify.check_assembly(["PartA", other])
            raise AssertionError(f"check_assembly accepted {other}")
        except ValueError as e:
            assert why in str(e), e
    print("assembly: parts without a solid refused: OK")

    # parts inside a moved container are compared where they ARE: a box in an App::Part
    # moved 100 mm overlapping a free box by 500 mm3 read OK, and a box at the origin it
    # never touched read a 1000 mm3 collision (each shape was in its container's frame)
    grp = doc.addObject("App::Part", "Moved")
    grp.Placement = App.Placement(V(100, 0, 0), App.Rotation(V(0, 0, 1), 90))
    nested = doc.addObject("Part::Box", "Nested")          # global x 90..100, y 0..10
    grp.addObject(nested)
    near = doc.addObject("Part::Box", "Near")
    near.Placement.Base = V(95, 0, 0)
    ghost = doc.addObject("Part::Box", "Ghost")             # at the origin: never touches it
    doc.recompute()
    r = verify.check_assembly(["Nested", "Near"])
    assert r["status"] == verify.FAIL and abs(r["interferences"][0]["volume"] - 500) < 1e-6, r
    assert verify.check_assembly(["Nested", "Ghost"])["status"] == verify.OK
    print("assembly: a part in a moved, turned App::Part compared where it is: OK")

    # sketches: the base's verify_sketch calls an under-constrained sketch
    # "fully constrained" (it reads solve()'s 0); ours uses DoF and the
    # conflicting/redundant lists
    import Sketcher
    free = doc.addObject("Sketcher::SketchObject", "FreeLine")
    free.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)))
    rect = doc.addObject("Sketcher::SketchObject", "Rect")
    pts = [V(0, 0, 0), V(20, 0, 0), V(20, 10, 0), V(0, 10, 0)]
    for i in range(4):
        rect.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    for i in range(4):
        rect.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    bad = doc.addObject("Sketcher::SketchObject", "Conflict")
    bad.addGeometry(Part.LineSegment(V(0, 0, 0), V(10, 0, 0)))
    bad.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 10))
    bad.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 12))
    doc.recompute()
    r = verify.sketch_check("FreeLine")
    print(verify.sketch_verdict("FreeLine"))
    assert r["status"] == verify.FAIL and r["dof"] == 4 and "open wire" in " ".join(r["issues"]), r
    r = verify.sketch_check("Rect")
    print(verify.sketch_verdict("Rect"))
    assert r["status"] == verify.OK and r["dof"] > 0 and r["advisories"], r   # closed, not fully constrained
    assert verify.sketch_check("Rect", require_fully_constrained=True)["status"] == verify.FAIL
    r = verify.sketch_check("Conflict", require_closed=False)
    print(verify.sketch_verdict("Conflict", require_closed=False))
    assert r["status"] == verify.FAIL and any("conflicting" in i for i in r["issues"]), r
    # closed loops that cross: they read OK and the Pad failed or made a broken solid
    # (a figure-8 -> two solids; overlapping squares -> zero area; a hole cutting the edge)
    def loops(name, segs, circles=()):
        s = doc.addObject("Sketcher::SketchObject", name)
        for a, b in segs:
            s.addGeometry(Part.LineSegment(V(*a, 0), V(*b, 0)))
        for c, rad in circles:
            s.addGeometry(Part.Circle(V(*c, 0), V(0, 0, 1), rad))
        return s
    sq = [((0, 0), (10, 0)), ((10, 0), (10, 10)), ((10, 10), (0, 10)), ((0, 10), (0, 0))]
    sq2 = [((a[0] + 5, a[1] + 5), (b[0] + 5, b[1] + 5)) for a, b in sq]
    far = [((a[0] + 20, a[1]), (b[0] + 20, b[1])) for a, b in sq]
    for name, segs, circles, why in (
            ("SkFig8", [((0, 0), (10, 10)), ((10, 10), (10, 0)), ((10, 0), (0, 10)), ((0, 10), (0, 0))],
             (), "crosses itself"),
            ("SkOverlap", sq + sq2, (), "overlap each other"),
            ("SkCutting", sq, (((10, 5), 2),), "crosses itself or another")):
        loops(name, segs, circles)
        doc.recompute()
        r = verify.sketch_check(name)
        assert r["status"] == verify.FAIL and any(why in i for i in r["issues"]), (name, r)
    for name, segs, circles in (("SkHoled", sq, (((5, 5), 2),)), ("SkIslands", sq + far, ())):
        loops(name, segs, circles)
        doc.recompute()
        assert verify.sketch_check(name)["status"] == verify.OK, (name, verify.sketch_verdict(name))
    print("crossing loops caught at the sketch (figure-8, overlap, hole on the edge): OK")

    # a Body's shape is its Tip's: a hole after the Tip, or suppressed, is not in
    # the part, yet the Body is a valid solid (it used to check OK at 4000 mm3)
    body = doc.addObject("PartDesign::Body", "Body")
    blk = body.newObject("PartDesign::AdditiveBox", "Block")
    blk.Length, blk.Width, blk.Height = 20, 20, 10
    hole = body.newObject("PartDesign::SubtractiveCylinder", "Hole")
    hole.Radius, hole.Height = 3, 10
    hole.Placement.Base = V(10, 10, 0)
    doc.recompute()
    expect("Body", verify.OK)
    body.Tip = blk
    doc.recompute()
    r = expect("Body", verify.FAIL)
    assert abs(r["volume"] - 4000) < 1e-6 and "Hole after the Tip (Block)" in r["issues"][0], r
    body.Tip = hole
    hole.Suppressed = True
    doc.recompute()
    r = expect("Body", verify.FAIL)
    assert r["issues"] == ["Hole suppressed: not in the part"], r
    hole.Suppressed = False
    body.Tip = blk                     # a new feature goes in after the Tip: Hole is left out
    hole2 = body.newObject("PartDesign::SubtractiveCylinder", "Hole2")
    hole2.Radius, hole2.Height = 2, 10
    doc.recompute()
    assert body.Tip == hole2, body.Tip.Name
    r = expect("Body", verify.FAIL)
    assert "Hole after the Tip (Hole2)" in r["issues"][0], r
    body.Tip = hole
    doc.recompute()
    expect("Body", verify.OK)
    try:   # a step "overlaps" its own Body by its whole volume
        verify.check_assembly(["Body", "Block"])
        raise AssertionError("check_assembly accepted a step of its Body")
    except ValueError as e:
        assert "Block is a step of Body" in str(e), e

    # Shape.BoundBox bounds a loft's B-spline control points: 37.3x26.8 for this
    # 30x20 part (a size requirement could pass on the wrong part)
    lo = doc.addObject("Part::Feature", "Loft")
    lo.Shape = Part.makeLoft([Part.Wire(Part.makeCircle(10)),
                              Part.Wire(Part.Ellipse(V(0, 0, 20), 15, 5).toShape())], True)
    doc.recompute()
    assert lo.Shape.BoundBox.XLength > 37, "FreeCAD's box got exact: drop this trap"
    r = expect("Loft", verify.OK)
    assert r["bbox"] == [30, 20, 20], r
    # ...and comes out too SMALL on a revolved B-spline: the interference
    # pre-filter skipped a real overlap
    bs = Part.BSplineCurve()
    bs.interpolate([V(5, 0, 0), V(12, 0, 5), V(8, 0, 10), V(14, 0, 15), V(6, 0, 20)])
    prof = Part.Wire([Part.LineSegment(V(0, 0, 0), V(5, 0, 0)).toShape(), bs.toShape(),
                      Part.LineSegment(V(6, 0, 20), V(0, 0, 20)).toShape(),
                      Part.LineSegment(V(0, 0, 20), V(0, 0, 0)).toShape()])
    vase = Part.Face(prof).revolve(V(0, 0, 0), V(0, 0, 1), 360)
    side = Part.makeBox(5, 10, 20, V(15.1, -5, 0))
    assert vase.BoundBox.XMax < 15.1 < verify.box(vase).XMax, (vase.BoundBox, verify.box(vase))
    hits = verify.interference([vase, side])
    assert hits and hits[0]["volume"] > 1e-4, hits
    print(f"exact boxes: loft 30x20 (BoundBox said 37.3x26.8), hidden overlap "
          f"{hits[0]['volume']:.3g} mm3 found: OK")

    print("VERIFY_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.stdout.flush()  # freecadcmd drops unflushed output on sys.exit
    sys.exit(rc)
