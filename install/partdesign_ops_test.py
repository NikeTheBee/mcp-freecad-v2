"""The base's PartDesign operations, called directly, against exact answers.

Run with:  A:\\FreeCAD\\bin\\freecadcmd.exe install\\partdesign_ops_test.py
No server, no port: the handler of server/freecad-mcp is imported and called on
documents made here. Each case was a wrong part (or no part) replied as made
before MCP FREECAD v2 (2026-10-30). Prints PD_OPS_OK.
"""
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server" / "freecad-mcp" / "AICopilot"))

import FreeCAD  # noqa: E402
import Part  # noqa: E402
import Sketcher  # noqa: E402,F401  (registers Sketcher types)
from handlers.partdesign_ops import PartDesignOpsHandler  # noqa: E402

V = FreeCAD.Vector
H = PartDesignOpsHandler()


def newdoc():
    for n in list(FreeCAD.listDocuments()):
        FreeCAD.closeDocument(n)
    return FreeCAD.newDocument("PdOps")


def role(body, name):
    return next(f for f in body.Origin.OriginFeatures if f.Role == name)


def sketch(body, name, plane="XY_Plane", off=(0, 0, 0)):
    s = body.newObject("Sketcher::SketchObject", name)
    s.AttachmentSupport = [(role(body, plane), "")]
    s.MapMode = "FlatFace"
    s.AttachmentOffset = FreeCAD.Placement(V(*off), FreeCAD.Rotation())
    return s


def rect(s, x0, y0, x1, y1):
    p = [V(x0, y0, 0), V(x1, y0, 0), V(x1, y1, 0), V(x0, y1, 0)]
    for i in range(4):
        s.addGeometry(Part.LineSegment(p[i], p[(i + 1) % 4]))


def close(got, want, what, rel=1e-6):
    assert abs(got - want) <= rel * max(abs(want), 1.0), f"{what}: {got} != {want}"
    print(f"{what}: {got:.4f} OK")


def plate(body, x1, y1, h, hole=None):
    """Pad of the rectangle 0..x1 x 0..y1 by h; a through D4 pocket at `hole`."""
    s = sketch(body, "S")
    rect(s, 0, 0, x1, y1)
    if hole:
        k = sketch(body, "K", off=(0, 0, h))
        k.addGeometry(Part.Circle(V(hole[0], hole[1], 0), V(0, 0, 1), 2))
    body.Document.recompute()
    H.pad_sketch({"sketch_name": "S", "length": h})
    if hole:
        H.pocket({"sketch_name": "K", "length": h})


def bores(shape):
    return sorted((round(f.Surface.Center.x, 6), round(f.Surface.Center.y, 6))
                  for f in shape.Faces if isinstance(f.Surface, Part.Cylinder))


# linear pattern: "axis" is the tool schema's name for the direction (it was
# ignored: axis="y" patterned along X)
d = newdoc()
b = d.addObject("PartDesign::Body", "Body")
plate(b, 20, 20, 5, hole=(5, 5))
r = H.linear_pattern({"feature_name": "Pocket", "axis": "y", "count": 3, "spacing": 5})
assert bores(b.Shape) == [(5, 5), (5, 10), (5, 15)], (r, bores(b.Shape))
print("linear pattern axis=y: holes at y 5/10/15 OK")

# a document's 2nd Body: its origin features are X_Axis001... (patterns and
# mirror refused: "Origin has no X_Axis feature")
d = newdoc()
d.addObject("PartDesign::Body", "Body")
b = d.addObject("PartDesign::Body", "Body001")
plate(b, 20, 10, 5, hole=(5, 5))
r = H.linear_pattern({"feature_name": "Pocket", "direction": "x", "count": 2, "spacing": 8})
close(b.Shape.Volume, 1000 - 40 * math.pi, f"2nd Body linear pattern ({r[:24]}...)")
r = H.polar_pattern({"feature_name": "Pocket", "axis": "z", "count": 2, "angle": 360})
assert "Created polar pattern" in r, r
r = H.mirror_feature({"feature_name": "Pad", "plane": "YZ"})
assert "Created mirror" in r and b.Shape.BoundBox.XMin < -19.999, r
print("2nd Body polar pattern and mirror: made OK")

# standalone polar pattern: copies turn around the axis (they stayed where the
# original is), steps as PartDesign's (360: shared; else first to last)
for angle, count, want in ((360, 4, [(0, 10), (-10, 0), (0, -10)]),
                           (180, 3, [(0, 10), (-10, 0)])):
    d = newdoc()
    c = d.addObject("Part::Cylinder", "Cyl")
    c.Radius, c.Height = 2, 5
    c.Placement.Base = V(10, 0, 0)
    d.recompute()
    r = H.polar_pattern({"feature_name": "Cyl", "axis": "z", "count": count, "angle": angle})
    got = [(round(o.Placement.Base.x, 9), round(o.Placement.Base.y, 9)) for o in d.Objects[1:]]
    assert got == want, (r, got)
    print(f"standalone polar pattern {count} over {angle}: {got} OK")

# revolution / groove / helix around the BODY's axis, the sketch's origin off it
d = newdoc()
b = d.addObject("PartDesign::Body", "Body")
s = sketch(b, "R", "XZ_Plane", off=(5, 0, 0))
rect(s, 0, 0, 2, 10)                                  # x 5..7, z 0..10
d.recompute()
r = H.revolution({"sketch_name": "R", "axis": "z", "angle": 360})
close(b.Shape.Volume, math.pi * (7 ** 2 - 5 ** 2) * 10, f"revolution around body Z ({r[:22]}...)")
d = newdoc()
b = d.addObject("PartDesign::Body", "Body")
plate(b, 20, 20, 10)
g = sketch(b, "G", "XZ_Plane", off=(5, 0, 0))
rect(g, 0, 4, 2, 6)                                   # x 5..7, z 4..6: inside the plate
d.recompute()
r = H.groove({"sketch_name": "G", "axis": "z", "angle": 90})
close(4000 - b.Shape.Volume, math.pi * (7 ** 2 - 5 ** 2) * 2 / 4, f"groove 90 around body Z ({r[:18]}...)")
d = newdoc()
b = d.addObject("PartDesign::Body", "Body")
c = sketch(b, "C", "XZ_Plane", off=(10, 0, 0))
c.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), 1))
d.recompute()
r = H.create_helix({"sketch_name": "C", "axis": "z", "pitch": 5, "height": 20})
close(b.Shape.Volume, 80 * math.pi ** 2, f"helix around body Z, D2 at R10 ({r[:16]}...)", rel=1e-5)

# hole on the Body itself with face_index: inside the Body (it was a Part::Cut
# beside it at the global (x, y), the face ignored)
d = newdoc()
b = d.addObject("PartDesign::Body", "Body")
plate(b, 20, 10, 5)
top = [i + 1 for i, f in enumerate(b.Shape.Faces) if abs(f.CenterOfMass.z - 5) < 1e-9][0]
r = H.hole_wizard({"object_name": "Body", "face_index": top, "diameter": 2, "depth": 3})
close(1000 - b.Shape.Volume, 3 * math.pi, f"hole on the Body's Face{top} ({r[:20]}...)")
assert bores(b.Shape) == [(10, 5)], bores(b.Shape)
assert not [o for o in d.Objects if o.TypeId == "Part::Cut"], "a Part::Cut beside the Body"

# draft: the faces hinge on the neutral plane XY (it was ignored: a 5 deg turn
# about a vertical edge, a plan-view wedge)
for side, want in (("y0", 250 * math.tan(math.radians(5))), ("x20", 125 * math.tan(math.radians(5)))):
    d = newdoc()
    b = d.addObject("PartDesign::Body", "Body")
    plate(b, 20, 10, 5)
    pad = d.getObject("Pad")
    fi = [i + 1 for i, f in enumerate(pad.Shape.Faces)
          if (abs(f.CenterOfMass.y) if side == "y0" else abs(f.CenterOfMass.x - 20)) < 1e-9][0]
    r = H.draft_faces({"object_name": "Pad", "faces": [fi], "angle": 5})
    close(1000 - b.Shape.Volume, want, f"draft 5 deg of side {side} ({r[:16]}...)")

# shell of a plain Part box, open at the top: walls inside it (they grew outward)
d = newdoc()
bx = d.addObject("Part::Box", "Box")
bx.Length, bx.Width, bx.Height = 20, 20, 10
d.recompute()
top = [i + 1 for i, f in enumerate(bx.Shape.Faces) if abs(f.CenterOfMass.z - 10) < 1e-9][0]
r = H.shell_solid({"object_name": "Box", "faces": [top], "thickness": 2})
sh = d.getObject("Shell")
close(sh.Shape.Volume, 4000 - 16 * 16 * 8, f"Part shell 2 mm ({r[:14]}...)")
bb = sh.Shape.BoundBox
assert (bb.XLength, bb.YLength, bb.ZLength) == (20, 20, 10), bb

print("PD_OPS_OK", flush=True)
