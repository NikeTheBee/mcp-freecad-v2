# skill-bom

**Load when:** the user wants a **bill of materials / nomenclature / parts list** for an assembly
(the pince project's Nomenclature), or counts of identical parts.

## Preferred: the `freecad-layers` tool
`bill_of_materials(csv_path="<out>\\nomenclature.csv")` on the active document returns
`BOM: N distinct part(s), M total` plus the rows (item, designation, qty, sorted size in mm).

## What counts as a part
- A `PartDesign::Body` is **one** part; its features (Pad, Pocket, Hole...) are not parts.
- The inputs of a boolean or a mirror (Base/Tool of a Cut...) are not parts; the result is.
- Containers (App::Part, groups, Part::Compound, assemblies) are not parts; their children are.
- Each `App::Link` is one more instance of the object it points to.
- Identical parts are grouped by measured geometry (volume, area, moments of inertia; a mirror
  image is a different part, listed "(mirrored)") **and**
  name stem: two different shapes of equal volume are never merged.
- Limitation: mirror images with the same name stem group together — name left/right parts
  differently (`Bracket_L`, `Bracket_R`).

## Python fallback
```python
import sys; sys.path.insert(0, r"<repo>\server")
from freecad_layers import bom
res = bom.bom_for(doc, r"<out>\nomenclature.csv")   # .csv only, refuses to overwrite
print(bom.verdict(res))
```
Labels are written CSV-injection-safe (a label like `=HYPERLINK(...)` is neutralised).

## Standard fasteners
No fastener library is installed (the FastenersWB graft was never set up and was dropped).
Model screws as simple cylinders named after the standard (`ISO4762_M5x20`) or ask the user for
STEP files from a supplier; size clearance holes with `skill-assembly`'s fit helpers.

## Verify
`install/bom_test.py` (`BOM_OK`) — includes the audit traps: Body features, boolean inputs,
links, equal-volume different parts.
