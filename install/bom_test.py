"""BOM / nomenclature test (GAP G6) — runs under freecadcmd.

Builds an assembly with repeated parts, extracts the grouped nomenclature and
exports CSV. Checks identical parts collapse into one line with the right qty,
and the secure output gate. Sentinel: BOM_OK
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402
from freecad_layers import bom, safe_out_path  # noqa: E402


def main() -> int:
    doc = App.newDocument("BOMTest")
    # 1 base + 4 identical screws (same size) + 1 lever
    base = doc.addObject("Part::Box", "Base")
    base.Length, base.Width, base.Height = 40, 40, 5
    for i in range(4):
        s = doc.addObject("Part::Cylinder", f"Screw{i+1}")
        s.Radius, s.Height = 2.5, 12
        s.Placement.Base = App.Vector(5 + i * 8, 5, 0)
    lever = doc.addObject("Part::Box", "Lever")
    lever.Length, lever.Width, lever.Height = 30, 6, 6
    # CSV-injection probe: a label that would be a formula in Excel.
    evil = doc.addObject("Part::Box", "Evil")
    evil.Label = "=HYPERLINK(evil)"
    evil.Length, evil.Width, evil.Height = 2, 2, 2
    doc.recompute()

    rows = bom.collect(doc)
    print([(r["designation"], r["qty"]) for r in rows])
    by_name = {r["designation"]: r for r in rows}
    assert by_name["Screw"]["qty"] == 4, rows        # 4 identical screws -> one line
    assert by_name["Base"]["qty"] == 1 and by_name["Lever"]["qty"] == 1, rows
    assert len(rows) == 4, rows                       # Base, Screw, Lever, Evil

    with tempfile.TemporaryDirectory() as td:
        csv_path = str(Path(td) / "nomenclature.csv")
        summary = bom.bom_for(doc, csv_path)
        print(bom.verdict(summary))
        assert summary["total_parts"] == 7, summary   # 1+4+1+1
        text = Path(csv_path).read_text(encoding="utf-8")
        assert "Designation" in text and "Screw" in text, text
        # formula neutralized: the cell must be quoted, never bare "=..."
        assert "'=HYPERLINK(evil)" in text and '\n"=HYPERLINK' not in text \
            and ",=HYPERLINK" not in text, text
        print("CSV formula injection neutralized: OK")

        # secure-by-design: wrong extension + clobber refused
        try:
            safe_out_path(str(Path(td) / "x.bat"), {".csv"})
            print("FAIL: wrong extension accepted"); return 1
        except ValueError:
            print("wrong extension refused: OK")
        try:
            bom.export_csv(summary["rows"], csv_path)   # exists, no overwrite
            print("FAIL: clobber allowed"); return 1
        except FileExistsError:
            print("clobber refused without overwrite=True: OK")

    # --- audit B1: structure, not raw objects ---------------------------------
    d = App.newDocument("BOMStructure")
    body = d.addObject("PartDesign::Body", "Body")
    pbox = body.newObject("PartDesign::AdditiveBox", "Box")
    pbox.Length, pbox.Width, pbox.Height = 20, 20, 5
    hole = body.newObject("PartDesign::SubtractiveCylinder", "Hole")
    hole.Radius, hole.Height = 2, 10
    st = d.addObject("Part::Box", "Stock")
    tl = d.addObject("Part::Cylinder", "Cutter")
    tl.Radius = 2
    cut = d.addObject("Part::Cut", "Plate")
    cut.Base, cut.Tool = st, tl
    # an expression reference must NOT make the referenced part disappear
    pin = d.addObject("Part::Cylinder", "Pin")
    pin.setExpression("Radius", "Cutter.Radius")
    pin.Placement.Base = App.Vector(100, 0, 0)
    d.recompute()
    rows = bom.collect(d)
    print([(r["designation"], r["qty"]) for r in rows])
    names = sorted(r["designation"] for r in rows)
    assert names == ["Body", "Pin", "Plate"], rows   # not Box/Hole/Stock/Cutter
    print("Body features and boolean inputs are not parts: OK")

    # links are instances of their target; a box and a cylinder never merge
    d2 = App.newDocument("BOMLinks")
    screw = d2.addObject("Part::Cylinder", "Screw")
    screw.Radius, screw.Height = 1.5, 10
    for i in range(3):
        lk = d2.addObject("App::Link", f"ScrewLink{i}")
        lk.LinkedObject = screw
    a = d2.addObject("Part::Box", "Bracket")
    a.Length, a.Width, a.Height = 10, 20, 30
    c = d2.addObject("Part::Cylinder", "Bracket001")
    c.Radius, c.Height = (6000 / (3.141592653589793 * 30)) ** 0.5, 30
    d2.recompute()
    rows = bom.collect(d2)
    print([(r["designation"], r["qty"]) for r in rows])
    by = {}
    for r in rows:
        by.setdefault(r["designation"], []).append(r["qty"])
    assert by["Screw"] == [4], rows                    # source + 3 links
    assert sorted(by["Bracket"]) == [1, 1], rows       # equal volume, different parts
    print("links counted as instances; equal-volume parts kept apart: OK")

    # identical parts turned different ways are ONE line (the global bounding box
    # used to split 4 screws into 3 lines), listed with their own size
    d3 = App.newDocument("BOMTurned")
    V, R = App.Vector, App.Rotation
    bolt = Part.makeCylinder(2, 20).fuse(Part.makeCylinder(3.5, 3, V(0, 0, 20))).removeSplitter()
    for i, (axis, ang) in enumerate(((V(0, 0, 1), 0), (V(1, 0, 0), 90), (V(1, 1, 0), 30),
                                     (V(0, 1, 0), 45))):
        o = d3.addObject("Part::Feature", f"Bolt{i:03d}" if i else "Bolt")
        o.Shape = bolt
        o.Placement = App.Placement(V(30 * i, 0, 0), R(axis, ang))
    d3.recompute()
    rows = bom.collect(d3)
    assert [(r["designation"], r["qty"], r["bbox_mm"]) for r in rows] == \
        [("Bolt", 4, [7.0, 7.0, 23.0])], rows
    print("identical parts in 4 orientations: one line, own size 7x7x23: OK")

    # a left and a right hand have the same volume, area and moments of inertia, but are
    # two parts: an L-bracket with an off-centre hole, its turned copy, its mirror image
    d4 = App.newDocument("BOMHanded")
    lb = Part.makeBox(40, 20, 4).fuse(Part.makeBox(4, 20, 30)).removeSplitter()
    lb = lb.cut(Part.makeCylinder(2.5, 4, V(30, 6, 0)))
    turned = lb.copy()
    turned.rotate(V(0, 0, 0), V(0, 0, 1), 90)
    mirror = lb.mirror(V(0, 0, 0), V(1, 0, 0))
    mirror2 = mirror.copy()
    mirror2.rotate(V(0, 0, 0), V(1, 0, 0), 30)
    for i, s in enumerate((lb, turned, mirror, mirror2)):
        o = d4.addObject("Part::Feature", f"Bracket{i + 1:03d}")
        o.Shape = s
        o.Placement.Base = V(100 * i, 0, 0)
    d4.recompute()
    rows = bom.collect(d4)
    assert [(r["designation"], r["qty"]) for r in rows] == \
        [("Bracket", 2), ("Bracket (mirrored)", 2)], rows
    print("left and right hands: two lines, turned copies still one: OK")

    # a volume on a rounding boundary (12.5 x 10.5 x 2.5 = 328.125 mm3): turned copies
    # read 328.12 or 328.13 and 40 identical plates came out as two lines (15 + 25)
    import random
    rnd = random.Random(3)
    d5 = App.newDocument("BOMBoundary")
    plate = Part.makeBox(12.5, 10.5, 2.5)
    for i in range(40):
        o = d5.addObject("Part::Feature", f"Plate{i:03d}")
        o.Shape = plate
        o.Placement = App.Placement(V(rnd.uniform(-500, 500), rnd.uniform(-500, 500), 0),
                                    R(V(rnd.random(), rnd.random(), rnd.random()),
                                      rnd.uniform(0, 360)))
    d5.recompute()
    rows = bom.collect(d5)
    assert [(r["designation"], r["qty"]) for r in rows] == [("Plate", 40)], rows
    print("40 turned plates on a rounding boundary: one line: OK")

    print("BOM_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.exit(rc)
