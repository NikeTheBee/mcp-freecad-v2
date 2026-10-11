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

    print("BOM_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.exit(rc)
