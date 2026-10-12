# skill-partdesign

**Load when:** parametric solid modeling of a single part — sketches, Pad/Pocket/Revolution/Loft, fillets,
chamfers, holes, patterns, and spreadsheet-driven variants. This is the core modeling domain (§4.1).

## Backbone: Body → Sketch → Pad/Pocket (validated headless)
```python
import Part, Sketcher
from FreeCAD import Vector
body = doc.addObject("PartDesign::Body", "Body")

s1 = body.newObject("Sketcher::SketchObject", "Profile")     # default = XY plane
for a, b in [((0,0),(20,0)),((20,0),(20,10)),((20,10),(0,10)),((0,10),(0,0))]:
    s1.addGeometry(Part.LineSegment(Vector(*a,0), Vector(*b,0)), False)
doc.recompute()

pad = body.newObject("PartDesign::Pad", "Pad"); pad.Profile = s1; pad.Length = 15
doc.recompute()

s2 = body.newObject("Sketcher::SketchObject", "Hole")
s2.addGeometry(Part.Circle(Vector(10,5,0), Vector(0,0,1), 3), False)
pk = body.newObject("PartDesign::Pocket", "Pocket"); pk.Profile = s2
pk.Type = "ThroughAll"; pk.Reversed = True
doc.recompute()
```
Other additive/subtractive features (same pattern): `PartDesign::Revolution` (set `.Angle`),
`PartDesign::Groove`, `PartDesign::AdditiveLoft`/`AdditivePipe` (loft/sweep along sketches).

## Sketch constraints
After `addGeometry`, add constraints for robust parametrics:
`s1.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 2, 20.0))`, `Horizontal`, `Vertical`,
`Coincident`, `Equal`, `Radius`, etc. Drive constraint *values* by name with
`s1.setDatum(idx, value)` or expressions (see variants below).

## Dress-up: fillet / chamfer
```python
f = body.newObject("PartDesign::Fillet", "Fillet")
f.Base = (pad, ["Edge1", "Edge3"]); f.Radius = 2     # PartDesign::Chamfer is analogous (.Size)
doc.recompute()
```
Pick edge ids from `pad.Shape.Edges` / `measurement_operations list_faces`.

## Holes & patterns
- Simple vertical hole (through or blind): **`pd_hole(body, diameter, x, y, through, depth)`** from
  the `freecad-layers` server — a real feature inside the Body. Since v2 the base tool
  `partdesign_operations hole` (no `face_index`) also cuts inside the Body (it used to build a
  `Part::Cut` *outside* it, audit 2026-10-11 §2.4); `pd_hole` stays the preferred, tested path.
- Counterbore/countersink/threaded: `PartDesign::Hole` (`.HoleCutType`, `.Threaded`) on a circle sketch.
- After any hole, `check(body)`; record the expected holes once with
  `require(body, "holes", {"count": n, "diameter": d})` so a missing hole FAILs.
- Repetition: `PartDesign::LinearPattern` / `PolarPattern` / `Mirrored` — set `.Originals=[feature]`,
  `.Direction`, `.Occurrences`/`.Angle`.

## Spreadsheet-driven variants (validated headless)
One cell drives a whole design family:
```python
sheet = doc.addObject("Spreadsheet::Sheet", "Params")
sheet.set("B1", "40"); sheet.setAlias("B1", "length")
box.setExpression("Length", "Params.length")     # any property: pad.setExpression("Length","Params.height")
doc.recompute()
sheet.set("B1", "60"); doc.recompute()            # -> the part rebuilds at the new size
```

## Verify
After each feature: `check(target)` (freecad-layers) must be `OK`, then `record_step(...)`
(records + autosaves). Python fallback: `verify.verdict(obj)` (see `skill-verify`).
Runnable examples: `install/partdesign_test.py`, `install/spreadsheet_variants_test.py`.
