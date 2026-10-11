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

    print("VERIFY_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.exit(rc)
