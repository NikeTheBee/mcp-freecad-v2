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
With the tools, `revolution`/`groove`/`helix` take `axis` = an axis of the BODY (x, y, z), like
the patterns: a profile drawn on XZ turns around `z`; the sketch's own normal is refused.
`loft`/`sweep` of a Body's sketches make Body features (check the Body after).

## Sketch constraints
After `addGeometry`, add constraints for robust parametrics:
`s1.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 2, 20.0))`, `Horizontal`, `Vertical`,
`Coincident`, `Equal`, `Radius`, etc. Drive constraint *values* by name with
`s1.setDatum(idx, value)` or expressions (see variants below).

## Dress-up: fillet / chamfer
Prefer the tool: `partdesign_operations(operation="fillet", object_name="Pad", radius=2,
edges=["Edge1", 3])`. Chamfer is the same with `distance`. Inside a Body it creates a
PartDesign feature; on a Part object it creates `Part::Fillet` / `Part::Chamfer`.
**Always pass `edges`** (or `auto_select_all=true`): the headless FreeCAD has no interactive
selection; `object_name` may be the Body itself (its Tip). Hollowing: `thickness` (or `shell`,
same result in a Body) and `draft` need `faces=[6]` / `["Face6"]` the same way. Then `check`
the result. The equivalent in code:
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
  (x, y) are in the Body's own frame (= global unless the Body was moved). A blind `depth` is
  measured from the part's surface AT (x, y) (on a stepped part: the step under the hole). The
  hole is measured once made; one that cuts nothing, breaks the part, is not through when asked,
  goes through when asked blind, or misses its depth is not kept: `FAIL pd_hole: ...; hole not
  kept` — fix x/y/Ø/depth.
- Counterbore/countersink/threaded: `PartDesign::Hole` (`.HoleCutType`, `.Threaded`) on a circle sketch.
- After any hole, `check(body)`; record the expected holes once with
  `require(body, "holes", {"count": n, "diameter": d})` so a missing hole FAILs. Holes are
  counted, not bores: a counterbored hole (bore + counterbore), or one pin hole through both
  flanges of a clevis, counts once; `analyze` lists each bore with its `#hole` number.
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
