"""Validate PartDesign dress-up features headless (cahier §4.1: fillet/chamfer/holes/patterns).

Run with:  A:\FreeCAD\bin\freecadcmd.exe tests\partdesign_dressup_test.py
Closes the "documented but untested" gap for fillet, chamfer, hole and pattern.
Prints DRESSUP_OK on success.
"""
import FreeCAD
import Part
import Sketcher  # noqa: F401
from FreeCAD import Vector

doc = FreeCAD.newDocument("DressupTest")
body = doc.addObject("PartDesign::Body", "Body")

s = body.newObject("Sketcher::SketchObject", "S")
for a, b in [((0, 0), (30, 0)), ((30, 0), (30, 30)), ((30, 30), (0, 30)), ((0, 30), (0, 0))]:
    s.addGeometry(Part.LineSegment(Vector(*a, 0), Vector(*b, 0)), False)
doc.recompute()
pad = body.newObject("PartDesign::Pad", "Pad"); pad.Profile = s; pad.Length = 10
doc.recompute()

# Fillet the 4 vertical edges
vedges = [f"Edge{i+1}" for i, e in enumerate(pad.Shape.Edges)
          if abs(e.Vertexes[0].Z - e.Vertexes[-1].Z) > 5][:4]
fil = body.newObject("PartDesign::Fillet", "Fillet"); fil.Base = (pad, vedges); fil.Radius = 2
doc.recompute()
assert fil.Shape.isValid(), "fillet invalid"

# Chamfer one edge
ch = body.newObject("PartDesign::Chamfer", "Chamfer")
ch.Base = (fil, ["Edge1"]); ch.Size = 1.0
doc.recompute()
assert ch.Shape.isValid(), "chamfer invalid"

# Hole, then linear-pattern it
hs = body.newObject("Sketcher::SketchObject", "HS")
hs.addGeometry(Part.Circle(Vector(8, 8, 0), Vector(0, 0, 1), 2), False)
doc.recompute()
hole = body.newObject("PartDesign::Hole", "Hole")
hole.Profile = hs; hole.Diameter = 4; hole.Depth = 10
doc.recompute()
assert body.Shape.isValid(), "hole invalid"

lp = body.newObject("PartDesign::LinearPattern", "LP")
lp.Originals = [hole]; lp.Direction = (pad, ["Edge1"]); lp.Length = 18; lp.Occurrences = 3
doc.recompute()
assert body.Shape.isValid() and len(body.Shape.Solids) == 1, "pattern invalid"

print(f"fillet+chamfer+hole+pattern OK; final V={body.Shape.Volume:.1f}")
print("DRESSUP_OK")
