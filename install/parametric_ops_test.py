"""Exact check of the base's parametric and document operations (spreadsheet,
VarSet, expression binding, recompute, save/insert), handlers called in-process.

Run with:  A:\\FreeCAD\\bin\\freecadcmd.exe install\\parametric_ops_test.py

No FreeCAD instance, no socket: the handler classes of the vendored base
(server/freecad-mcp/AICopilot, or $MCP_BASE_AICOPILOT) run on documents made
here, and every value is compared with the number it must be. Files go to a
fresh folder in the system temp dir (the base only writes under home/temp).
Sentinel: PARAMETRIC_OPS_OK
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.environ.get("MCP_BASE_AICOPILOT",
                                  str(REPO / "server" / "freecad-mcp" / "AICopilot")))

import FreeCAD  # noqa: E402
import Part  # noqa: E402

from handlers.document_ops import DocumentOpsHandler  # noqa: E402
from handlers.spreadsheet_ops import SpreadsheetOpsHandler  # noqa: E402
from handlers.varset_ops import VarSetOpsHandler  # noqa: E402
from handlers.view_ops import ViewOpsHandler  # noqa: E402

failures = []


def check(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


def close(name):
    if name in FreeCAD.listDocuments():
        FreeCAD.closeDocument(name)


def crashed():
    import traceback
    failures.append("exception")
    print(traceback.format_exc(), flush=True)


sh, vs, dh = SpreadsheetOpsHandler(), VarSetOpsHandler(), DocumentOpsHandler()
tmp = tempfile.mkdtemp(prefix="mcp_param_ops_")
try:
    # 1. Spreadsheet alias -> Box.Length: the volume follows the cell exactly.
    doc = FreeCAD.newDocument("ParamOps")
    sh.create_spreadsheet({"name": "Params"})
    sh.set_cell({"spreadsheet_name": "Params", "cell": "B1", "value": "40"})
    sh.set_alias({"spreadsheet_name": "Params", "cell": "B1", "alias": "length"})
    box = doc.addObject("Part::Box", "Box")
    doc.recompute()
    r = sh.bind_property({"object_name": "Box", "property_name": "Length",
                          "spreadsheet_name": "Params", "cell": "length"})
    check(r.startswith("Bound") and abs(box.Shape.Volume - 4000) < 1e-6, f"bind alias: {r}")
    r = sh.set_cell({"spreadsheet_name": "Params", "cell": "B1", "value": "2 in"})
    check("WARNING" not in r and abs(box.Shape.Volume - 50.8 * 100) < 1e-6,
          f"cell '2 in' -> volume {box.Shape.Volume:.6f} (5080)")

    # 2. A cell with the wrong unit: FreeCAD takes 45 deg as a 45 mm length.
    r = sh.set_cell({"spreadsheet_name": "Params", "cell": "B1", "value": "45 deg"})
    check("unit mismatch" in r and "Box.Length" in r, f"cell '45 deg' on a Length is flagged: {r!r}")
    sh.set_cell({"spreadsheet_name": "Params", "cell": "B1", "value": "30 mm"})
    cyl = doc.addObject("Part::Cylinder", "Cyl")
    doc.recompute()
    sh.set_cell({"spreadsheet_name": "Params", "cell": "C1", "value": "90 deg"})
    sh.set_alias({"spreadsheet_name": "Params", "cell": "C1", "alias": "sweep"})
    sh.bind_property({"object_name": "Cyl", "property_name": "Angle",
                      "spreadsheet_name": "Params", "cell": "sweep"})
    r = sh.set_cell({"spreadsheet_name": "Params", "cell": "C1", "value": "180 mm"})
    check("unit mismatch" in r and "Cyl.Angle" in r, f"cell '180 mm' on an Angle is flagged: {r!r}")
    sh.set_cell({"spreadsheet_name": "Params", "cell": "C1", "value": "90 deg"})

    # 3. Binding a property to a value of the wrong unit is refused; the box is unchanged.
    vs.create_varset({"name": "Dims"})
    vs.add_property({"varset_name": "Dims", "type": "App::PropertyAngle", "name": "Tilt", "value": 30})
    box2 = doc.addObject("Part::Box", "Box2")
    doc.recompute()
    r = vs.bind_property({"object_name": "Box2", "property_name": "Length",
                          "varset_name": "Dims", "varset_property": "Tilt"})
    check(r.startswith("Error") and "unit mismatch" in r and not box2.ExpressionEngine
          and box2.Length.Unit.Type == "Length" and abs(box2.Shape.Volume - 1000) < 1e-6,
          f"Angle bound to Box2.Length refused, box unchanged: {r!r} / {box2.Length}")

    # 4. VarSet -> Box2.Width follows; an out-of-range value is not echoed as stored.
    vs.add_property({"varset_name": "Dims", "type": "App::PropertyLength", "name": "BoxW", "value": 20})
    r = vs.bind_property({"object_name": "Box2", "property_name": "Width",
                          "varset_name": "Dims", "varset_property": "BoxW"})
    check(r.startswith("Bound") and abs(box2.Shape.Volume - 2000) < 1e-6, f"bind VarSet: {r}")
    r = vs.set_property({"varset_name": "Dims", "name": "BoxW", "value": 30})
    check("WARNING" not in r and abs(box2.Shape.Volume - 3000) < 1e-6, f"VarSet 30 -> {box2.Shape.Volume:.6f}")
    r = vs.set_property({"varset_name": "Dims", "name": "BoxW", "value": "1 in"})
    check("WARNING" not in r and abs(box2.Shape.Volume - 2540) < 1e-6, f"VarSet '1 in': {r!r}")
    r = vs.set_property({"varset_name": "Dims", "name": "BoxW", "value": -5})
    check("FreeCAD kept 0.0 mm" in r, f"Length -5 clamped to 0 is said: {r!r}")
    r = vs.set_property({"varset_name": "Dims", "name": "Tilt", "value": 400})
    check("FreeCAD kept 360" in r, f"Angle 400 clamped to 360 is said: {r!r}")
    vs.set_property({"varset_name": "Dims", "name": "BoxW", "value": 20})

    # 5. Enumeration created with an initial value keeps it.
    r = vs.add_property({"varset_name": "Dims", "type": "App::PropertyEnumeration", "name": "Finish",
                         "enum_vals": ["raw", "anodized"], "value": "anodized"})
    check(doc.getObject("Dims").Finish == "anodized", f"enum initial value: {r!r}")

    # 6. Bound through a Label that is not an identifier.
    sh.create_spreadsheet({"name": "Spreadsheet"})
    doc.getObject("Spreadsheet").Label = "Param Sheet"
    sh.set_cell({"spreadsheet_name": "Param Sheet", "cell": "A1", "value": "25"})
    sh.set_alias({"spreadsheet_name": "Param Sheet", "cell": "A1", "alias": "tall"})
    r = sh.bind_property({"object_name": "Box2", "property_name": "Height",
                          "spreadsheet_name": "Param Sheet", "cell": "tall"})
    check(r.startswith("Bound") and abs(box2.Height.Value - 25) < 1e-9, f"bind via Label 'Param Sheet': {r!r}")

    # 7. Empty cells read as empty, not as errors.
    sh.create_spreadsheet({"name": "Sparse"})
    sh.set_cell({"spreadsheet_name": "Sparse", "cell": "A1", "value": "1"})
    sh.set_cell({"spreadsheet_name": "Sparse", "cell": "C3", "value": "2"})
    r = sh.get_cell({"spreadsheet_name": "Sparse", "cell": "B2"})
    check(r.startswith("{") and json.loads(r)["value"] is None, f"get_cell on an empty cell: {r}")
    e = json.loads(sh.export_csv({"spreadsheet_name": "Sparse"}))
    check(e["errors"] == [] and e["csv"] == "1,,\r\n,,\r\n,,2\r\n", f"sparse export: {e}")
    sh.set_cell_range({"spreadsheet_name": "Sparse", "start_cell": "D1", "values": [[None, 3]]})
    check(doc.getObject("Sparse").getContents("D1") == "", "set_cell_range null leaves the cell empty")

    # 8. An existing PartDesign feature that adds nothing in this variant is
    #    bound, not deleted from its Body.
    body = doc.addObject("PartDesign::Body", "Body")

    def rect(name, x0, x1, z):
        sk = body.newObject("Sketcher::SketchObject", name)
        sk.Placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, z), FreeCAD.Rotation())
        pts = [FreeCAD.Vector(x0, x0, 0), FreeCAD.Vector(x1, x0, 0),
               FreeCAD.Vector(x1, x1, 0), FreeCAD.Vector(x0, x1, 0)]
        for i in range(4):
            sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
        return sk

    pad = body.newObject("PartDesign::Pad", "Plate")
    pad.Profile, pad.Length = rect("S1", 0, 20, 0), 10
    boss = body.newObject("PartDesign::Pad", "Boss")
    boss.Profile, boss.Length, boss.Reversed = rect("S2", 5, 15, 10), 5, True
    doc.recompute()
    sh.set_cell({"spreadsheet_name": "Params", "cell": "D1", "value": "5"})
    sh.set_alias({"spreadsheet_name": "Params", "cell": "D1", "alias": "boss"})
    r = sh.bind_property({"object_name": "Boss", "property_name": "Length",
                          "spreadsheet_name": "Params", "cell": "boss"})
    check(doc.getObject("Boss") is not None and boss in body.Group, f"bound feature kept: {r!r}")

    # 9. Replies that hid a failure: a formula FreeCAD stored as text, a
    #    document recompute that left an object Invalid.
    r = sh.set_cell({"spreadsheet_name": "Sparse", "cell": "E1", "value": "=2*"})
    check("formula not understood in E1" in r, f"unparsable formula is said: {r!r}")
    flat = doc.addObject("Part::Box", "Flat")
    flat.Length = 0
    r = ViewOpsHandler().recompute_object({})
    check("in error" in r and "Flat" in r, f"document recompute names Flat: {r!r}")
except Exception:
    crashed()
close("ParamOps")

try:
    # 10. save_document: the reply names the file written; another file is not replaced.
    fa = os.path.join(tmp, "partA.FCStd")
    da = FreeCAD.newDocument("SaveA")
    da.addObject("Part::Box", "BigBox")
    da.recompute()
    r = dh.save_document({"filename": fa})
    check(r == f"Document saved as: {fa}" and os.path.isfile(fa), f"save A: {r}")
    db = FreeCAD.newDocument("SaveB")
    db.addObject("Part::Cylinder", "Cyl")
    db.recompute()
    r = dh.save_document({"filename": fa})
    close("SaveA")
    d = FreeCAD.openDocument(fa)
    check(r.startswith("Error") and [o.Name for o in d.Objects] == ["BigBox"],
          f"save B onto A's file refused, A intact: {r}")
    r = dh.save_document({"filename": fa})          # its own file: saved
    check(r == f"Document saved as: {fa}", f"re-save to its own file: {r}")
    close(d.Name)
    if "SaveB" in FreeCAD.listDocuments():
        FreeCAD.setActiveDocument("SaveB")
        r = dh.save_document({"filename": fa, "overwrite": True})
        close("SaveB")
        d = FreeCAD.openDocument(fa)
        check(r == f"Document saved as: {fa}" and [o.Name for o in d.Objects] == ["Cyl"],
              f"overwrite=true replaces it: {r}")
        close(d.Name)
    else:
        check(False, "overwrite=true replaces it: SaveB was closed with A's file")
    dc = FreeCAD.newDocument("SaveC")
    r = dh.save_document({"filename": os.path.join(tmp, "partC")})
    want = os.path.join(tmp, "partC.FCStd")
    check(r == f"Document saved as: {want}" and os.path.isfile(want), f"save without extension: {r}")
    close("SaveC")
except Exception:
    crashed()

try:
    # 11. insert_shape x/y/z offset the source's placement.
    src = FreeCAD.newDocument("InsSrc")
    sb = src.addObject("Part::Box", "SB")
    sb.Placement.Base = FreeCAD.Vector(100, 50, 0)
    src.recompute()
    dst = FreeCAD.newDocument("InsDst")
    FreeCAD.setActiveDocument("InsDst")
    r = dh.insert_shape({"source_doc": "InsSrc", "source_object": "SB", "x": 5})
    bb = dst.getObject("SB_ref").Shape.BoundBox
    check((bb.XMin, bb.YMin, bb.ZMin) == (105, 50, 0), f"insert offset x=5 -> min ({bb.XMin}, {bb.YMin}, {bb.ZMin})")
except Exception:
    crashed()
for name in ("SaveA", "SaveB", "SaveC", "InsSrc", "InsDst"):
    close(name)
shutil.rmtree(tmp, ignore_errors=True)

if failures:
    print(f"PARAMETRIC_OPS_FAILED: {len(failures)} check(s)", flush=True)
else:
    print("PARAMETRIC_OPS_OK", flush=True)
