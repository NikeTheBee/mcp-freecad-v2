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

        # spatial query: the cylinder really pierces the box
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
