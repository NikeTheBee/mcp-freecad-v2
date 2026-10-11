# skill-techdraw

**Load when:** the task asks for 2D drawings/plans — "plan 2D", workshop drawing, dimensioned
sheet, DXF/blueprint of a part (cahier des charges §4.6; e.g. the pince project's "plan 2D pdf
par pièce").

## What this is
First-party layer `server/freecad_layers/drawing.py` over the **TechDraw** workbench (ships with
FreeCAD). Fully **headless** under freecadcmd: pages, projected views, dimensions, DXF export.

## One call for a standard sheet
```python
import sys; sys.path.insert(0, r"<repo>\server")
from freecad_layers import drawing
res = drawing.sheet_for(part, dxf_path=r"<out>\part.dxf",
                        views=("front", "top", "right"))   # scale="auto" by default
# {'page': 'Page_Part', 'views': [...], 'scale': 0.5, 'dims': 4, 'dxf': '...', 'verdict': 'OK ...'}
print(res["verdict"])   # must start with OK
```

## Building blocks
| Call | Does |
|---|---|
| `make_page(doc, template="A4_Landscape")` | page + SVG template by name fragment (e.g. `"A3_Landscape_ISO5457_minimal"`); unknown name = error |
| `add_view(page, obj, "front"/"back"/"top"/"bottom"/"left"/"right"/"iso", scale, x, y)` | projected view; unknown name = error; `x, y` = page mm from bottom-left |
| `auto_scale(shapes, views, slot_w, slot_h)` | largest ISO scale (2:1, 1:1, 1:2, 1:5…) that fits |
| `add_extent_dims(view)` | overall width/height dimensions |
| `add_distance_dim(view, (x1,y1), (x2,y2), "Distance"/"DistanceX"/"DistanceY")` | point-to-point dim (unscaled 2D view coords) |
| `export_dxf(page, path, overwrite=False)` | full page → DXF (refuses clobber without overwrite) |
| `view_svg(view)` | one view's edges as SVG string (cheap text preview) |

## Honest limits
- **PDF/SVG page export is GUI-coupled** in FreeCAD 1.1 — headless deliverable is **DXF**
  (universally accepted by CAD/CAM). For a PDF: open the saved .FCStd in the GUI and print,
  or convert the DXF externally.
- Auto-dimensioning beyond overall extents (each hole, each feature) is per-feature work:
  use `add_distance_dim` with coordinates from the model.

## verdict(page)
`OK` only if every view has a source and fits inside the page; `FAIL` names the view that sticks
out; `UNKNOWN` if an extent could not be computed. `sheet_for` returns it in `res["verdict"]`.

## Verify
`install/techdraw_test.py` (sentinel `TECHDRAW_OK`) — includes the secure-by-design refusal
paths (clobber/extension/traversal).
