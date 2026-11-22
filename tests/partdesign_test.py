"""Validate the core PartDesign workflow headless (cahier des charges §4.1 core).

Run with:  A:\FreeCAD\bin\freecadcmd.exe tests\partdesign_test.py

Body -> Sketch -> Pad (additive) -> Sketch -> Pocket through-all (subtractive),
the parametric-modeling backbone. Bundled FreeCAD only. Prints PARTDESIGN_OK.
"""
import math

import FreeCAD
import Part
import Sketcher  # noqa: F401  (registers Sketcher types)
from FreeCAD import Vector

doc = FreeCAD.newDocument("PartDesignTest")
body = doc.addObject("PartDesign::Body", "Body")

# --- Sketch 1: 20x10 rectangle on the body's default (XY) plane ---
s1 = body.newObject("Sketcher::SketchObject", "Profile")
for a, b in [((0, 0), (20, 0)), ((20, 0), (20, 10)),
             ((20, 10), (0, 10)), ((0, 10), (0, 0))]:
    s1.addGeometry(Part.LineSegment(Vector(*a, 0), Vector(*b, 0)), False)
doc.recompute()

# --- Pad (additive) ---
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = s1
pad.Length = 15
doc.recompute()
pad_vol = pad.Shape.Volume
assert pad.Shape.isValid() and abs(pad_vol - 3000) < 1e-3, f"pad volume {pad_vol}"
print(f"PAD: valid={pad.Shape.isValid()} vol={pad_vol:.1f}")

# --- Sketch 2: circle (r=3) at the centre, on XY ---
s2 = body.newObject("Sketcher::SketchObject", "Hole")
s2.addGeometry(Part.Circle(Vector(10, 5, 0), Vector(0, 0, 1), 3), False)
doc.recompute()

# --- Pocket through-all (subtractive) ---
pocket = body.newObject("PartDesign::Pocket", "Pocket")
pocket.Profile = s2
pocket.Type = "ThroughAll"
pocket.Reversed = True
doc.recompute()

final = body.Shape
expected = 3000 - math.pi * 9 * 15
print(f"POCKET: valid={final.isValid()} vol={final.Volume:.1f} (expect ~{expected:.1f})")
assert final.isValid() and abs(final.Volume - expected) < 1.0, "pocket result wrong"
print("PARTDESIGN_OK")
