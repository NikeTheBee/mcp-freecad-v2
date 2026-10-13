"""Exactness of the base's geometry operations on a real FreeCAD (isolated instance).

The base's own unit tests mock FreeCAD. This one runs its operations for real
and compares the results with values known in advance, so that an "OK" reply
on a wrong part cannot pass. It is also the guard for every upstream update
(bootstrap.py --update-base).

Setup: Box 10x20x30 at the origin; Cyl r=3, h=40 at (5, 10, -5), which
pierces the box along Z. Their common volume is pi*9*30.
Sentinel: BASE_EXACT_OK
"""
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fc_instance import Instance  # noqa: E402

PI9 = math.pi * 9
V_BOX, V_CYL, V_COMMON = 6000.0, PI9 * 40, PI9 * 30

SETUP = """
import FreeCAD
for _n in list(FreeCAD.listDocuments()):
    FreeCAD.closeDocument(_n)
d = FreeCAD.newDocument("Exact")
b = d.addObject("Part::Box", "Box"); b.Length, b.Width, b.Height = 10, 20, 30
c = d.addObject("Part::Cylinder", "Cyl"); c.Radius, c.Height = 3, 40
c.Placement.Base = FreeCAD.Vector(5, 10, -5)
d.recompute()
'ok'
"""


# PartDesign: a Body with a 10x10 rectangle on XY ("Base"), a D4 circle centred
# at (5, 5) on a plane 5 mm up ("Hole"), and a 2x10 rectangle on XZ at x = 5..7 ("Ring").
PD_SETUP = r"""
import FreeCAD, Part, Sketcher
for _n in list(FreeCAD.listDocuments()):
    FreeCAD.closeDocument(_n)
d = FreeCAD.newDocument("PD")
body = d.addObject("PartDesign::Body", "Body")
xy = body.Origin.OriginFeatures[3]; xz = body.Origin.OriginFeatures[4]
def rect(name, x0, y0, x1, y1, support):
    s = body.newObject("Sketcher::SketchObject", name)
    s.AttachmentSupport = [(support, "")]; s.MapMode = "FlatFace"
    P = [FreeCAD.Vector(x0, y0, 0), FreeCAD.Vector(x1, y0, 0), FreeCAD.Vector(x1, y1, 0), FreeCAD.Vector(x0, y1, 0)]
    for i in range(4):
        s.addGeometry(Part.LineSegment(P[i], P[(i + 1) % 4]))
    for i in range(4):
        s.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
rect("Base", 0, 0, 10, 10, xy)
c = body.newObject("Sketcher::SketchObject", "Hole")
c.AttachmentSupport = [(xy, "")]; c.MapMode = "FlatFace"
c.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, 5), FreeCAD.Rotation())
c.addGeometry(Part.Circle(FreeCAD.Vector(5, 5, 0), FreeCAD.Vector(0, 0, 1), 2))
rect("Ring", 5, 0, 7, 10, xz)
d.recompute()
'ok'
"""


def body_volume(inst):
    return float(py(inst, "import FreeCAD\nround(FreeCAD.ActiveDocument.getObject('Body').Shape.Volume, 6)"))


def op(inst, tool, args, wait=60.0):
    r = inst.call(tool, args)
    t0 = time.monotonic()
    while isinstance(r, dict) and r.get("status") in ("submitted", "running", "queued"):
        assert time.monotonic() - t0 < wait, f"{tool} still running"
        time.sleep(0.2)
        r = inst.call("poll_job", {"job_id": r.get("job_id")})
    return r.get("result", r) if isinstance(r, dict) else r


def py(inst, code):
    r = inst.call("execute_python_sync", {"code": code})
    assert r and "result" in r, r
    return r["result"]


def shape_of(inst, name):
    """(volume, bbox min x, area) of the newest object whose name starts with `name`."""
    out = py(inst, (
        "import FreeCAD, json\n"
        f"o = [x for x in FreeCAD.ActiveDocument.Objects if x.Name.startswith({name!r})][-1]\n"
        "s = o.Shape\n"
        "json.dumps([s.Volume, s.BoundBox.XMin, s.Area, o.Name, s.isValid()])"))
    return json.loads(out.strip("'\""))


def close(got, want, what, rel=1e-6):
    assert abs(got - want) <= rel * max(abs(want), 1.0), f"{what}: got {got}, want {want}"
    print(f"{what}: {got:.4f} (expected {want:.4f}) OK")


def main() -> int:
    inst = Instance()
    try:
        inst.start()

        # booleans
        for operation, want in (("cut", V_BOX - V_COMMON), ("common", V_COMMON),
                                ("fuse", V_BOX + V_CYL - V_COMMON)):
            py(inst, SETUP)
            r = op(inst, "part_operations", {"operation": operation, "base": "Box", "tools": ["Cyl"],
                                             "objects": ["Box", "Cyl"], "name": f"R_{operation}"})
            v, _, _, name, valid = shape_of(inst, f"R_{operation}")
            assert valid, (operation, r)
            close(v, want, f"part {operation} volume ({name})", rel=1e-4)

        # transforms
        py(inst, SETUP)
        op(inst, "part_operations", {"operation": "move", "object_name": "Box", "x": 10, "y": 0, "z": 0})
        v, xmin, _, _, _ = shape_of(inst, "Box")
        close(xmin, 10.0, "move: Box XMin")
        close(v, V_BOX, "move: volume unchanged")
        py(inst, SETUP)
        op(inst, "part_operations", {"operation": "scale", "object_name": "Box", "scale_factor": 2})
        close(shape_of(inst, "Box")[0], V_BOX * 8, "scale x2 (in place): volume x8")

        # spreadsheet-driven parameter: the box follows the cell, then follows it again
        py(inst, SETUP)
        op(inst, "spreadsheet_operations", {"operation": "create_spreadsheet", "name": "Sheet"})
        op(inst, "spreadsheet_operations", {"operation": "set_cell", "spreadsheet_name": "Sheet",
                                            "cell": "A1", "value": "25"})
        op(inst, "spreadsheet_operations", {"operation": "set_alias", "spreadsheet_name": "Sheet",
                                            "cell": "A1", "alias": "len"})
        r = op(inst, "spreadsheet_operations", {"operation": "bind_property", "spreadsheet_name": "Sheet",
                                                "object_name": "Box", "property_name": "Length",
                                                "cell": "len"})
        py(inst, "import FreeCAD\nFreeCAD.ActiveDocument.recompute()\n'ok'")
        close(shape_of(inst, "Box")[0], 25 * 20 * 30, f"spreadsheet len=25 drives Box ({str(r)[:40]})")
        op(inst, "spreadsheet_operations", {"operation": "set_cell", "spreadsheet_name": "Sheet",
                                            "cell": "A1", "value": "40"})
        py(inst, "import FreeCAD\nFreeCAD.ActiveDocument.recompute()\n'ok'")
        close(shape_of(inst, "Box")[0], 40 * 20 * 30, "spreadsheet len=40 drives Box again")

        # distance / clearance to a separate sphere: 30 - 5 - 10 = 15 mm
        py(inst, SETUP + "\ns = d.addObject('Part::Sphere', 'Sph'); s.Radius = 5\n"
                 "s.Placement.Base = FreeCAD.Vector(30, 10, 15)\nd.recompute()\n'ok'")
        for tool, args in (("measurement_operations", {"operation": "measure_distance",
                                                       "object1": "Box", "object2": "Sph"}),
                           ("spatial_query", {"operation": "clearance", "object1": "Box", "object2": "Sph"})):
            txt = str(op(inst, tool, args))
            print(f"{args['operation']}:", txt[:120].replace("\n", " | "))
            assert "15.0" in txt or "15 mm" in txt or "15mm" in txt, txt

        # measurements
        py(inst, SETUP)
        txt = str(op(inst, "measurement_operations", {"operation": "get_volume", "object_name": "Box"}))
        assert "6000" in txt, txt
        txt = str(op(inst, "measurement_operations", {"operation": "get_surface_area", "object_name": "Box"}))
        assert "2200" in txt, txt
        txt = str(op(inst, "measurement_operations", {"operation": "get_center_of_mass",
                                                      "object_name": "Box"}))
        assert all(k in txt for k in ("5", "10", "15")), txt
        print("measurements: volume 6000, area 2200, centre (5, 10, 15) OK")

        # PartDesign through the base's tool, volume after each step
        py(inst, PD_SETUP)
        seg = 4 * math.acos(0.5) - math.sqrt(3)             # D4 circle cut 1 mm from its centre
        pocket = PI9 * 4 / 9 * 5                             # pi * 2^2 * 5
        steps = (
            ({"operation": "pad", "sketch_name": "Base", "length": 5}, 500.0),
            ({"operation": "pocket", "sketch_name": "Hole", "length": 5}, 500.0 - pocket),
            # 2nd copy at x = 9: part of the circle (x > 10) lies outside the block
            ({"operation": "linear_pattern", "feature_name": "Pocket", "axis": "x", "count": 2,
              "spacing": 4}, 500.0 - pocket - (math.pi * 4 - seg) * 5),
            ({"operation": "mirror", "feature_name": "Pad", "plane": "YZ"},
             1000.0 - pocket - (math.pi * 4 - seg) * 5),
        )
        for args, want in steps:
            r = op(inst, "partdesign_operations", args)
            close(body_volume(inst), want, f"partdesign {args['operation']} ({str(r)[:30]}...)", rel=1e-5)
        py(inst, PD_SETUP)
        op(inst, "partdesign_operations", {"operation": "revolution", "sketch_name": "Ring",
                                           "angle": 360, "axis": "y"})
        close(body_volume(inst), math.pi * (7 ** 2 - 5 ** 2) * 10, "partdesign revolution (ring)", rel=1e-5)

        # sketch tools: 20x10 rectangle + D4 circle at its centre, padded 3 mm
        py(inst, "import FreeCAD\nfor _n in list(FreeCAD.listDocuments()):\n    FreeCAD.closeDocument(_n)\n"
                 "d = FreeCAD.newDocument('SK')\nd.addObject('PartDesign::Body', 'Body')\n'ok'")
        for args in ({"operation": "create_sketch", "name": "S", "plane": "XY"},
                     {"operation": "add_rectangle", "sketch_name": "S", "x": 0, "y": 0,
                      "width": 20, "height": 10, "constrain": True},
                     # center_x/center_y: the arc's names, silently ignored for circles before v2
                     {"operation": "add_circle", "sketch_name": "S", "center_x": 10, "center_y": 5,
                      "radius": 2}):
            op(inst, "sketch_operations", args)
        txt = str(op(inst, "sketch_operations", {"operation": "verify_sketch", "sketch_name": "S"}))
        assert "Verdict: VALID" in txt and "Closed wires (valid for extrusion): 2" in txt, txt
        op(inst, "partdesign_operations", {"operation": "pad", "sketch_name": "S", "length": 3})
        close(body_volume(inst), 600 - math.pi * 4 * 3, "sketch rect + centred circle, pad 3", rel=1e-5)

        # spatial query: the cylinder really pierces the box
        py(inst, SETUP)
        txt = str(op(inst, "spatial_query", {"operation": "interference_check",
                                             "object1": "Box", "object2": "Cyl"}))
        print("interference:", txt[:160])
        assert f"{V_COMMON:.1f}" in txt or f"{V_COMMON:.2f}" in txt or "848.2" in txt, txt
    finally:
        inst.stop()
    print("BASE_EXACT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
