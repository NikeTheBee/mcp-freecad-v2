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
    # keep the submission's id: a "running" poll reply does not repeat it
    job_id = r.get("job_id") if isinstance(r, dict) else None
    t0 = time.monotonic()
    while isinstance(r, dict) and r.get("status") in ("submitted", "running", "queued"):
        assert time.monotonic() - t0 < wait, f"{tool} still running"
        time.sleep(0.2)
        r = inst.call("poll_job", {"job_id": job_id})
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
            assert f"{want:.6g} mm³" in str(r) or operation == "cut", r           # said, measured
        # a cut whose tool does not touch the base removed nothing: said so (it read "Created cut")
        py(inst, SETUP + "\nd.getObject('Cyl').Placement.Base = FreeCAD.Vector(100, 0, 0)\n"
                         "d.recompute()\n'ok'")
        r = str(op(inst, "part_operations", {"operation": "cut", "base": "Box", "tools": ["Cyl"]}))
        assert "removed NOTHING" in r, r
        print("booleans report their volume; a cut that removes nothing says so: OK")

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

        # Draft array: count/spacing from the schema (ignored before v2: always 2 x 100 mm)
        py(inst, SETUP)
        op(inst, "draft_operations", {"operation": "array", "object_name": "Box", "count": 3, "spacing": 15})
        v, xmin, _, name, _ = shape_of(inst, "Array")
        close(v, 3 * V_BOX, f"draft array count=3 ({name})")
        span = float(py(inst, "import FreeCAD\nround(FreeCAD.ActiveDocument.getObject('Array').Shape.BoundBox.XLength, 6)"))
        close(span, 2 * 15 + 10, "draft array spacing=15: overall X length")

        # VarSet: initial value given with add_property drives the box at once
        py(inst, SETUP)
        for args in ({"operation": "create_varset", "name": "Vars"},
                     {"operation": "add_property", "varset_name": "Vars", "name": "Len",
                      "type": "App::PropertyLength", "value": 25},
                     {"operation": "bind_property", "varset_name": "Vars", "varset_property": "Len",
                      "object_name": "Box", "property_name": "Length"}):
            r = op(inst, "varset_operations", args)
            assert "rror" not in str(r), r
        py(inst, "import FreeCAD\nFreeCAD.ActiveDocument.recompute()\n'ok'")
        close(shape_of(inst, "Box")[0], 25 * 20 * 30, "varset Len=25 (set at creation) drives Box")
        op(inst, "varset_operations", {"operation": "set_property", "varset_name": "Vars",
                                       "name": "Len", "value": 40})
        py(inst, "import FreeCAD\nFreeCAD.ActiveDocument.recompute()\n'ok'")
        close(shape_of(inst, "Box")[0], 40 * 20 * 30, "varset Len=40 drives Box again")

        # meshes: a meshed box keeps its exact volume/area, is watertight, and goes back to a solid
        py(inst, SETUP + "\nimport MeshPart\nm = d.addObject('Mesh::Feature', 'BoxMesh')\n"
                 "m.Mesh = MeshPart.meshFromShape(Shape=b.Shape, LinearDeflection=0.1)\nd.recompute()\n'ok'")
        txt = str(op(inst, "mesh_operations", {"operation": "get_mesh_info", "object_name": "BoxMesh"}))
        assert "Volume: 6000.00" in txt and "Area: 2200.00" in txt and "Is watertight: True" in txt, txt
        txt = str(op(inst, "mesh_operations", {"operation": "validate_mesh", "object_name": "BoxMesh"}))
        assert "Status: VALID" in txt, txt
        op(inst, "mesh_operations", {"operation": "mesh_to_solid", "object_name": "BoxMesh"})
        v, _, area, name, valid = shape_of(inst, "BoxMesh_Solid")
        assert valid, name
        close(v, V_BOX, "mesh -> solid volume")
        close(area, 2200.0, "mesh -> solid area")

        # assembly: B is mated by a Fixed joint onto A's +X face; parts are named as the
        # user knows them (A, B), not by their component links (A001, B001)
        py(inst, "import FreeCAD\nfor _n in list(FreeCAD.listDocuments()):\n    FreeCAD.closeDocument(_n)\n"
                 "d = FreeCAD.newDocument('ASM')\nd.addObject('Part::Box', 'A')\nb = d.addObject('Part::Box', 'B')\n"
                 "b.Placement.Base = FreeCAD.Vector(30, 5, 0)\nd.recompute()\n'ok'")
        for args in ({"operation": "create_assembly", "name": "Asm"},
                     {"operation": "add_component", "object_name": "A"},
                     {"operation": "add_component", "object_name": "B", "x": 30, "y": 5},
                     {"operation": "ground_part", "object_name": "A"},
                     {"operation": "create_joint", "joint_type": "Fixed", "ref1_object": "A",
                      "ref1_element": "Face2", "ref2_object": "B", "ref2_element": "Face1"},
                     {"operation": "solve"}):
            r = op(inst, "assembly_operations", args)
            assert "not a component" not in str(r) and "rror" not in str(r), (args, r)
        txt = str(op(inst, "assembly_operations", {"operation": "get_part_status", "object_name": "B"}))
        assert "connected_to_ground=True" in txt and "placement=(10.00,0.00,0.00)" in txt, txt
        print("assembly: B mated against A's +X face, connected to ground: OK")

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

        # curved faces: Shape.BoundBox bounds a loft's control points (37.3 x 26.8 for
        # this 30 x 20 part) and comes out too SMALL on a revolved B-spline, so the
        # batch pre-filter skipped a real collision
        py(inst, SETUP + (
            "\nimport Part\nV = FreeCAD.Vector\n"
            "lo = d.addObject('Part::Feature', 'Loft')\n"
            "lo.Shape = Part.makeLoft([Part.Wire(Part.makeCircle(10)),\n"
            "    Part.Wire(Part.Ellipse(V(0, 0, 20), 15, 5).toShape())], True)\n"
            "lo.Placement.Base = V(100, 0, 0)\n"
            "bs = Part.BSplineCurve()\n"
            "bs.interpolate([V(5, 0, 0), V(12, 0, 5), V(8, 0, 10), V(14, 0, 15), V(6, 0, 20)])\n"
            "w = Part.Wire([Part.LineSegment(V(0, 0, 0), V(5, 0, 0)).toShape(), bs.toShape(),\n"
            "    Part.LineSegment(V(6, 0, 20), V(0, 0, 20)).toShape(),\n"
            "    Part.LineSegment(V(0, 0, 20), V(0, 0, 0)).toShape()])\n"
            "va = d.addObject('Part::Feature', 'Vase')\n"
            "va.Shape = Part.Face(w).revolve(V(0, 0, 0), V(0, 0, 1), 360)\n"
            "va.Placement.Base = V(-50, 0, 0)\n"
            "sd = d.addObject('Part::Box', 'Side')\n"
            "sd.Length, sd.Width, sd.Height = 5, 10, 20\n"
            "sd.Placement.Base = V(-50 + 15.1, -5, 0)\n"
            "d.recompute()\n'ok'"))
        txt = str(op(inst, "measurement_operations", {"operation": "get_bounding_box",
                                                      "object_name": "Loft"}))
        assert "length: 30.00" in txt and "width: 20.00" in txt, txt
        txt = str(op(inst, "spatial_query", {"operation": "batch_interference",
                                             "objects": ["Vase", "Side", "Box"]}))
        assert "Collisions: 1" in txt and "Vase ↔ Side" in txt, txt
        print("curved faces: loft measured 30 x 20, hidden collision found:",
              txt[:90].replace("\n", " | "))

        # holes in a plain Part box (CSG path), 20 x 20 x 10: depths from the TOP surface.
        # The countersink cone used to lie entirely under the part (cut nothing), the
        # counterbore sat on the bottom face, a blind depth was counted from below.
        for args, removed in (({"hole_type": "simple", "depth": 4}, 36 * math.pi),
                              ({"hole_type": "counterbore", "depth": 10, "cb_diameter": 12,
                                "cb_depth": 4}, 198 * math.pi),
                              ({"hole_type": "countersink", "depth": 10, "cb_diameter": 12,
                                "cb_depth": 3}, 126 * math.pi)):
            py(inst, "import FreeCAD\nfor _n in list(FreeCAD.listDocuments()):\n"
                     "    FreeCAD.closeDocument(_n)\n"
                     "d = FreeCAD.newDocument('HW')\nb = d.addObject('Part::Box', 'Blk')\n"
                     "b.Length, b.Width, b.Height = 20, 20, 10\nd.recompute()\n'ok'")
            r = op(inst, "partdesign_operations", {"operation": "hole", "object_name": "Blk",
                                                   "diameter": 6, "x": 10, "y": 10, **args})
            got = float(py(inst, "import FreeCAD\nround(4000 - [o for o in FreeCAD.ActiveDocument"
                                 ".Objects if o.TypeId == 'Part::Cut'][-1].Shape.Volume, 6)"))
            close(got, removed, f"CSG {args['hole_type']} hole: volume removed ({str(r)[:40]}...)",
                  rel=1e-6)
        # list_faces says what each face is: the D6 bore of the last part is a cylinder r=3
        # around Z (its "normal" was taken at parameters (0, 0): arbitrary on a curved face)
        cut = py(inst, "import FreeCAD\n[o.Name for o in FreeCAD.ActiveDocument.Objects"
                       " if o.TypeId == 'Part::Cut'][-1]").strip("'\"")
        txt = str(op(inst, "measurement_operations", {"operation": "list_faces", "object_name": cut}))
        assert "cylinder normal=" in txt and "r=3.00 axis=(+0.00, +0.00, " in txt, txt
        assert "plane normal=(+0.00, +0.00, +1.00)" in txt, txt
        print("list_faces: plane/cylinder kinds, bore radius and axis: OK")

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
        # the axis letter names a BODY axis: Ring is on XZ, "y" is its normal (refused);
        # it used to name the sketch's own V axis, so "y" turned around Z, said "Y-axis"
        r = str(op(inst, "partdesign_operations", {"operation": "revolution", "sketch_name": "Ring",
                                                   "angle": 360, "axis": "y"}))
        assert "N_Axis" in r and "holds the x and z axes" in r, r
        r = op(inst, "partdesign_operations", {"operation": "revolution", "sketch_name": "Ring",
                                               "angle": 360, "axis": "z"})
        assert "Z" in str(r), r
        close(body_volume(inst), math.pi * (7 ** 2 - 5 ** 2) * 10, "partdesign revolution (ring)", rel=1e-5)
        py(inst, PD_SETUP)
        op(inst, "partdesign_operations", {"operation": "pad", "sketch_name": "Base", "length": 5})
        op(inst, "partdesign_operations", {"operation": "groove", "sketch_name": "Ring",
                                           "angle": 360, "axis": "z"})
        # a ring r 5..7 around Z, in the first quadrant of the 10x10x5 pad: 30 pi removed
        close(body_volume(inst), 500 - 30 * math.pi, "partdesign groove around Z (XZ sketch)", rel=1e-5)

        # dress-ups on every edge / an opened face land INSIDE the Body: "all edges" made a
        # Part::Fillet beside it (the Body kept 500 mm3), thickness only asked to "select
        # faces in FreeCAD". Reference: the same operation through the Part API.
        for args, ref in (({"operation": "fillet", "radius": 1}, "s.makeFillet(1, s.Edges)"),
                          ({"operation": "chamfer", "distance": 1}, "s.makeChamfer(1, s.Edges)"),
                          ({"operation": "thickness", "thickness": 1, "faces": ["Face6"]}, None)):
            py(inst, PD_SETUP)
            op(inst, "partdesign_operations", {"operation": "pad", "sketch_name": "Base", "length": 5})
            extra = {} if args["operation"] == "thickness" else {"auto_select_all": True}
            r = op(inst, "partdesign_operations", {"object_name": "Body", **args, **extra})
            want = (500 - 8 * 8 * 4) if ref is None else float(
                py(inst, f"import Part\ns = Part.makeBox(10, 10, 5)\nround({ref}.Volume, 9)").split("\n")[0])
            close(body_volume(inst), want, f"partdesign {args['operation']} in the Body ({str(r)[:30]}...)",
                  rel=1e-6)

        # loft / sweep of a Body's sketches go INSIDE it (a Part::Loft or Part::Sweep beside
        # it left the Body empty); helix around the body's Z from an XZ profile (Pappus)
        body_sk = (
            "import FreeCAD, Part\nfor _n in list(FreeCAD.listDocuments()):\n    FreeCAD.closeDocument(_n)\n"
            "d = FreeCAD.newDocument('PD2')\nbody = d.addObject('PartDesign::Body', 'Body')\n"
            "xy, xz = body.Origin.OriginFeatures[3], body.Origin.OriginFeatures[4]\n"
            "def circ(name, r, z=0.0, cx=0.0, sup=None):\n"
            "    s = body.newObject('Sketcher::SketchObject', name)\n"
            "    s.AttachmentSupport = [(sup or xy, '')]; s.MapMode = 'FlatFace'\n"
            "    s.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, z), FreeCAD.Rotation())\n"
            "    s.addGeometry(Part.Circle(FreeCAD.Vector(cx, 0, 0), FreeCAD.Vector(0, 0, 1), r))\n"
            "circ('C1', 5)\ncirc('C2', 5, 20)\ncirc('Coil', 1, 0, 10, xz)\n"
            "p = body.newObject('Sketcher::SketchObject', 'Path')\n"
            "p.AttachmentSupport = [(xz, '')]; p.MapMode = 'FlatFace'\n"
            "p.addGeometry(Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 30, 0)))\n"
            "d.recompute()\n'ok'")
        for args, want, what in (
                ({"operation": "loft", "sketches": ["C1", "C2"]}, 500 * math.pi, "loft of two D10 circles 20 apart"),
                ({"operation": "sweep", "profile_sketch": "C1", "path_sketch": "Path"}, 750 * math.pi,
                 "sweep of a D10 circle along 30 mm"),
                ({"operation": "helix", "sketch_name": "Coil", "axis": "z", "pitch": 5, "height": 20},
                 80 * math.pi ** 2, "helix: D2 profile at R10, 4 turns")):
            py(inst, body_sk)
            r = op(inst, "partdesign_operations", args)
            close(body_volume(inst), want, f"partdesign {what} ({str(r)[:28]}...)", rel=1e-6)

        # Part extrude of a plate sketch whose FIRST wire is a hole: the whole profile, holes
        # cut (it extruded Wires[0] alone: a cylinder instead of the plate); an XZ sketch with
        # no direction goes along its normal (a fixed "z" lay in its plane)
        py(inst, "import FreeCAD, Part\nfor _n in list(FreeCAD.listDocuments()):\n"
                 "    FreeCAD.closeDocument(_n)\nd = FreeCAD.newDocument('Ext')\nV = FreeCAD.Vector\n"
                 "s = d.addObject('Sketcher::SketchObject', 'Plate')\n"
                 "s.addGeometry(Part.Circle(V(5, 5, 0), V(0, 0, 1), 1))\n"
                 "P = [V(0, 0, 0), V(20, 0, 0), V(20, 10, 0), V(0, 10, 0)]\n"
                 "for i in range(4):\n    s.addGeometry(Part.LineSegment(P[i], P[(i + 1) % 4]))\n"
                 "s.addGeometry(Part.Circle(V(15, 5, 0), V(0, 0, 1), 1))\n"
                 "x = d.addObject('Sketcher::SketchObject', 'Side')\n"
                 "x.Placement = FreeCAD.Placement(V(), FreeCAD.Rotation(V(1, 0, 0), 90))\n"
                 "Q = [V(0, 0, 0), V(8, 0, 0), V(8, 5, 0), V(0, 5, 0)]\n"
                 "for i in range(4):\n    x.addGeometry(Part.LineSegment(Q[i], Q[(i + 1) % 4]))\n"
                 "d.recompute()\n'ok'")
        r = str(op(inst, "sketch_operations", {"operation": "verify_sketch", "sketch_name": "Plate"}))
        assert f"Face area: {200 - 2 * math.pi:.2f}" in r, r          # holes subtracted
        # a figure-8 outline: the Pad made two solids, and verify_sketch said VALID
        py(inst, "import FreeCAD, Part\nV = FreeCAD.Vector\nd = FreeCAD.ActiveDocument\n"
                 "s = d.addObject('Sketcher::SketchObject', 'Fig8')\n"
                 "P = [V(0, 0, 0), V(10, 10, 0), V(10, 0, 0), V(0, 10, 0)]\n"
                 "for i in range(4):\n    s.addGeometry(Part.LineSegment(P[i], P[(i + 1) % 4]))\n"
                 "d.recompute()\n'ok'")
        r = str(op(inst, "sketch_operations", {"operation": "verify_sketch", "sketch_name": "Fig8"}))
        assert "INVALID - loop(s) [1] cross themselves" in r, r
        r = op(inst, "part_operations", {"operation": "extrude", "profile_sketch": "Plate", "height": 3})
        close(float(py(inst, "import FreeCAD\nFreeCAD.ActiveDocument.getObject('Plate_extruded').Shape.Volume")),
              (200 - 2 * math.pi) * 3, f"part extrude, plate with 2 holes ({str(r)[:24]}...)", rel=1e-9)
        r = op(inst, "part_operations", {"operation": "extrude", "profile_sketch": "Side", "height": 2})
        close(float(py(inst, "import FreeCAD\nFreeCAD.ActiveDocument.getObject('Side_extruded').Shape.Volume")),
              80.0, f"part extrude of an XZ sketch, no direction ({str(r)[:30]}...)", rel=1e-9)
        r = str(op(inst, "part_operations", {"operation": "extrude", "profile_sketch": "Side",
                                             "height": 2, "direction": "z"}))
        assert "lies in the plane" in r, r

        # section of a part beyond 500 mm: cut at the plane, exact (a fixed 1000 x 1000 plane
        # centred on the origin missed it: an empty section replied as "Created")
        py(inst, "import FreeCAD\nb = FreeCAD.ActiveDocument.addObject('Part::Box', 'Far')\n"
                 "b.Length, b.Width, b.Height = 10, 20, 30\nb.Placement.Base = FreeCAD.Vector(600, 0, 0)\n"
                 "FreeCAD.ActiveDocument.recompute()\n'ok'")
        r = str(op(inst, "part_operations", {"operation": "section", "object_name": "Far",
                                             "plane": "XY", "offset": 15}))
        assert "area 200.00 mm2" in r, r
        r = str(op(inst, "part_operations", {"operation": "section", "object_name": "Far",
                                             "plane": "XY", "offset": 100}))
        assert "misses Far" in r and "z 0..30" in r, r
        print("section of a part 600 mm away: area 200 mm2; a plane off the part is refused: OK")

        # a pocket sketched on the bottom face cuts into empty space by default: it removed
        # nothing and was replied as made. It is cut on the other side; a profile off the part
        # is refused and not kept
        py(inst, PD_SETUP)
        op(inst, "partdesign_operations", {"operation": "pad", "sketch_name": "Base", "length": 5})
        py(inst, "import FreeCAD, Part\nd = FreeCAD.ActiveDocument\nbody = d.getObject('Body')\n"
                 "for name, x in (('Under', 5), ('Off', 50)):\n"
                 "    s = body.newObject('Sketcher::SketchObject', name)\n"
                 "    s.AttachmentSupport = [(body.Origin.OriginFeatures[3], '')]; s.MapMode = 'FlatFace'\n"
                 "    s.addGeometry(Part.Circle(FreeCAD.Vector(x, 5, 0), FreeCAD.Vector(0, 0, 1), 2))\n"
                 "d.recompute()\n'ok'")
        r = str(op(inst, "partdesign_operations", {"operation": "pocket", "sketch_name": "Under", "length": 3}))
        close(500 - body_volume(inst), 12 * math.pi, f"pocket from the bottom face ({r[60:100]}...)", rel=1e-6)
        assert "other side of the sketch" in r, r
        r = str(op(inst, "partdesign_operations", {"operation": "pocket", "sketch_name": "Off", "length": 3}))
        assert "removes no material on either side" in r, r
        close(500 - body_volume(inst), 12 * math.pi, "a pocket off the part is not kept", rel=1e-6)
        # likewise any PartDesign feature that leaves the part unchanged: a pad buried in the
        # material, a groove in the air -- computed fine, replied "Created", nothing changed
        py(inst, "import FreeCAD, Part\nd = FreeCAD.ActiveDocument\nbody = d.getObject('Body')\n"
                 "s = body.newObject('Sketcher::SketchObject', 'Inside')\n"
                 "s.AttachmentSupport = [(body.Origin.OriginFeatures[3], '')]; s.MapMode = 'FlatFace'\n"
                 "s.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, 1), FreeCAD.Rotation())\n"
                 "s.addGeometry(Part.Circle(FreeCAD.Vector(2, 8, 0), FreeCAD.Vector(0, 0, 1), 1))\n"
                 "g = body.newObject('Sketcher::SketchObject', 'Air')\n"
                 "g.AttachmentSupport = [(body.Origin.OriginFeatures[4], '')]; g.MapMode = 'FlatFace'\n"
                 "P = [FreeCAD.Vector(30, 0, 0), FreeCAD.Vector(32, 0, 0), FreeCAD.Vector(32, 2, 0),"
                 " FreeCAD.Vector(30, 2, 0)]\n"
                 "for i in range(4):\n    g.addGeometry(Part.LineSegment(P[i], P[(i + 1) % 4]))\n"
                 "d.recompute()\n'ok'")
        v_before = body_volume(inst)
        r = str(op(inst, "partdesign_operations", {"operation": "pad", "sketch_name": "Inside", "length": 2}))
        assert "adds no material" in r and "not kept" in r, r
        r = str(op(inst, "partdesign_operations", {"operation": "groove", "sketch_name": "Air",
                                                   "angle": 90, "axis": "z"}))
        assert "removes no material" in r and "not kept" in r, r
        close(body_volume(inst), v_before, "a buried pad and a groove in the air are not kept", rel=1e-9)

        # rib of a 10 x 4 rectangle sketched on XY, 3 mm: along the sketch's normal (it went
        # along global Y, within the sketch's plane: a solid of zero volume, replied as made)
        py(inst, "import FreeCAD, Part\nfor _n in list(FreeCAD.listDocuments()):\n"
                 "    FreeCAD.closeDocument(_n)\nd = FreeCAD.newDocument('Rib')\n"
                 "s = d.addObject('Sketcher::SketchObject', 'S')\n"
                 "P = [FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(10, 0, 0), FreeCAD.Vector(10, 4, 0),"
                 " FreeCAD.Vector(0, 4, 0)]\n"
                 "for i in range(4):\n    s.addGeometry(Part.LineSegment(P[i], P[(i + 1) % 4]))\n"
                 "d.recompute()\n'ok'")
        r = op(inst, "partdesign_operations", {"operation": "rib", "sketch_name": "S", "thickness": 3})
        close(float(py(inst, "import FreeCAD\nFreeCAD.ActiveDocument.getObject('Rib').Shape.Volume")), 120.0,
              f"rib along the sketch's normal ({str(r)[:24]}...)", rel=1e-9)
        r = str(op(inst, "partdesign_operations", {"operation": "rib", "sketch_name": "S",
                                                   "direction": "horizontal", "name": "Rib2"}))
        assert "zero volume" in r, r

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
