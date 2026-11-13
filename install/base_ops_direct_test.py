"""The base's Part / boolean / Draft / transform handlers against exact answers,
called directly under freecadcmd (no FreeCAD instance, no socket).

Run with:  A:\\FreeCAD\\bin\\freecadcmd.exe install\\base_ops_direct_test.py
Each case once replied success on a wrong or empty part (audit 2026-10-30).
BASE_OPS_AICOPILOT=<dir> runs it against another copy of the AICopilot addon.
Sentinel: BASE_OPS_OK
"""
import math
import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.environ.get("BASE_OPS_AICOPILOT")
                or str(REPO / "server" / "freecad-mcp" / "AICopilot"))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

V = App.Vector
FAILS = []


def check(what, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {what}" + (f"  [{detail}]" if detail else ""), flush=True)
    if not ok:
        FAILS.append(what)


def close(a, b, rel=1e-9):
    return abs(a - b) <= rel * max(abs(b), 1.0)


def fresh(name):
    for d in list(App.listDocuments()):
        App.closeDocument(d)
    doc = App.newDocument(name)
    App.setActiveDocument(doc.Name)
    return doc


def box(doc, name, l, w, h, x=0, y=0, z=0):
    b = doc.addObject("Part::Box", name)
    b.Length, b.Width, b.Height = l, w, h
    b.Placement.Base = V(x, y, z)
    doc.recompute()
    return b


def sketch(doc, name, segments, placement=None):
    s = doc.addObject("Sketcher::SketchObject", name)
    if placement is not None:
        s.Placement = placement
    for a, b in segments:
        s.addGeometry(Part.LineSegment(V(*a), V(*b)))
    doc.recompute()
    return s


def rect(x0, y0, x1, y1):
    p = [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)]
    return [(p[i], p[(i + 1) % 4]) for i in range(4)]


def booleans():
    from handlers.base import BaseHandler
    from handlers.boolean_ops import BooleanOpsHandler
    h = BooleanOpsHandler()

    doc = fresh("Bool1")
    box(doc, "A", 10, 10, 10)
    box(doc, "Big", 30, 30, 30, -10, -10, -10)
    r = h.cut_objects({"base": "A", "tools": ["Big"]})
    check("cut that swallows the base is refused, base left visible",
          "empty" in r and doc.A.Visibility and doc.getObject("Cut") is None, r)

    doc = fresh("Bool2")
    box(doc, "A", 10, 10, 10)
    box(doc, "B", 10, 10, 10, 50, 0, 0)
    r = h.common_objects({"objects": ["A", "B"]})
    check("common of disjoint boxes is empty, sources left visible",
          "empty" in r and doc.A.Visibility and doc.getObject("Common") is None, r)
    r = h.fuse_objects({"objects": ["A", "B"]})
    check("fuse of boxes that do not touch says 2 separate solids", "2 separate solids" in r, r)

    doc = fresh("Bool3")
    cell = doc.addObject("App::Part", "Cell")
    for o in (box(doc, "A", 10, 10, 10), box(doc, "B", 10, 10, 10, 5, 0, 0)):
        cell.addObject(o)
    cell.Placement.Base = V(100, 0, 0)
    doc.recompute()
    h.common_objects({"objects": ["A", "B"]})
    c = doc.getObject("Common")
    w = BaseHandler._world_shape(c)
    bb = w.optimalBoundingBox(False, False)
    check("common of two boxes in a moved App::Part = 500 mm3 at x 105..110",
          close(w.Volume, 500.0) and close(bb.XMin, 105.0) and close(bb.XMax, 110.0),
          f"V={w.Volume:.6g} x {bb.XMin:g}..{bb.XMax:g}")

    doc = fresh("Bool4")
    cell = doc.addObject("App::Part", "Cell")
    cell.addObject(box(doc, "A", 10, 10, 10))
    cell.Placement.Base = V(100, 0, 0)
    box(doc, "B", 10, 10, 10, 105, 0, 0)
    doc.recompute()
    n = len(doc.Objects)
    r = h.cut_objects({"base": "B", "tools": ["A"]})
    check("cut across differently placed containers is refused, nothing created",
          r.startswith("Cannot make this boolean") and len(doc.Objects) == n, r)


def part_ops():
    from handlers.part_ops import PartOpsHandler
    h = PartOpsHandler()

    doc = fresh("Rev")
    sketch(doc, "S", rect(5, 0, 10, 4))
    r = h.revolve({"profile_sketch": "S", "axis": "z", "angle": 360})
    check("revolving an XY sketch around z (its normal) is refused",
          r.startswith("Invalid axis") and len(doc.Objects) == 1, r)
    r = h.revolve({"profile_sketch": "S", "axis": "y", "angle": 360})
    o = doc.getObject("S_revolved")
    check("ring revolved around y = pi (10^2 - 5^2) 4 mm3, reply names it",
          o is not None and close(o.Shape.Volume, 300 * math.pi) and "S_revolved" in r,
          f"V={o.Shape.Volume if o else None}")

    doc = fresh("Loft")
    sketch(doc, "L1", [((0, 0, 0), (10, 0, 0))])
    sketch(doc, "L2", [((0, 0, 0), (10, 0, 0))], App.Placement(V(0, 0, 10), App.Rotation()))
    sketch(doc, "Path", [((0, 0, 0), (0, 30, 0))],
           App.Placement(V(0, 0, 0), App.Rotation(V(1, 0, 0), 90)))
    r = h.loft({"sketches": ["L1", "L2"]})
    check("solid loft of open lines is refused and not kept",
          "not a solid" in r or "invalid solid" in r, r)
    check("  ... no Loft left in the document", doc.getObject("Loft") is None)
    r = h.sweep({"profile_sketch": "L1", "path_sketch": "Path"})
    check("solid sweep of an open line is refused and not kept",
          "not a solid" in r and doc.getObject("Sweep") is None, r)

    doc = fresh("Scale")
    cone = doc.addObject("Part::Feature", "Cone")
    cone.Shape = Part.makeCone(5, 2, 10)
    hel = doc.addObject("Part::Helix", "Helix")
    hel.Pitch, hel.Height, hel.Radius = 2, 10, 5
    doc.recompute()
    length0 = hel.Shape.Length
    h.scale_object({"object_name": "Cone", "scale_factor": 2})
    s = doc.getObject("Cone_scaled").Shape
    check("cone scaled x2 = 8 x 130 pi mm3, still a cone",
          close(s.Volume, 8 * 130 * math.pi) and any(f.Surface.__class__.__name__ == "Cone" for f in s.Faces),
          f"V={s.Volume:.6f}")
    h.scale_object({"object_name": "Helix", "scale_factor": 2})
    s = doc.getObject("Helix_scaled")
    check("helix scaled x2 is twice as long (pitch scaled too)",
          s is not None and close(s.Shape.Length, 2 * length0, 1e-6),
          f"{s.Shape.Length if s else hel.Shape.Length:.4f} vs {2 * length0:.4f}")


def transforms():
    from handlers.transforms import TransformsHandler
    t = TransformsHandler()
    doc = fresh("Attached")
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "S")
    sk.AttachmentSupport = [(body.Origin.OriginFeatures[4], "")]   # XZ_Plane
    sk.MapMode = "FlatFace"
    doc.recompute()
    r = t.move_object({"object_name": "S", "x": 0, "y": 0, "z": 10})
    check("move of an attached sketch moves it (z = 10), still attached",
          close(sk.Placement.Base.z, 10.0) and sk.MapMode == "FlatFace", f"{r} -> {sk.Placement.Base}")
    want = App.Rotation(V(0, 0, 1), 90).multiply(sk.Placement.Rotation)
    t.rotate_object({"object_name": "S", "axis": "z", "angle": 90})
    p = V(1, 2, 3)
    check("rotate of an attached sketch turns it",
          (sk.Placement.Rotation.multVec(p) - want.multVec(p)).Length < 1e-9, str(sk.Placement))
    for a, b in rect(0, 0, 10, 10):
        sk.addGeometry(Part.LineSegment(V(*a), V(*b)))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile, pad.Length = sk, 5
    doc.recompute()
    bb0 = body.Shape.BoundBox
    r = t.move_object({"object_name": "Pad", "x": 10, "y": 0, "z": 0})
    check("move of a Pad (placed by FreeCAD) says it did not move",
          r.startswith("Error") and "did NOT move" in r
          and abs(body.Shape.BoundBox.XMin - bb0.XMin) < 1e-9, r)


def draft():
    from handlers.draft_ops import DraftOpsHandler
    d = DraftOpsHandler()
    doc = fresh("Polar")
    box(doc, "A", 2, 2, 2, 20, -1, 0)
    d.polar_array({"object_name": "A", "count": 4, "angle": -360})
    arr = doc.Objects[-1]
    centres = sorted((round(s.CenterOfMass.x, 6), round(s.CenterOfMass.y, 6)) for s in arr.Shape.Solids)
    check("polar array of 4 over -360 deg puts 4 copies at 90 deg steps",
          len(set(centres)) == 4, str(centres))


def material():
    # one solid plus faces outside it: the solid's volume, not the whole compound's
    # (a 10 mm cube and an open shell of 5 faces read 1818.18 mm3)
    import Part
    from handlers.base import BaseHandler
    shell = Part.Shell(Part.makeBox(10, 10, 10, App.Vector(50, 0, 0)).Faces[:5])
    m = BaseHandler._material(Part.makeCompound([Part.makeBox(10, 10, 10), shell]))
    check("a solid beside an open shell measures the solid only (1000 mm3)",
          close(m["volume"], 1000.0) and close(m["com"].x, 5.0) and "not counted" in m["note"],
          str(m))


def main():
    for part in (booleans, part_ops, transforms, draft, material):
        try:
            part()
        except Exception:
            FAILS.append(part.__name__)
            print(traceback.format_exc(), flush=True)
    if FAILS:
        print(f"FAILED: {len(FAILS)} check(s): {FAILS}", flush=True)
    else:
        print("BASE_OPS_OK", flush=True)


main()
