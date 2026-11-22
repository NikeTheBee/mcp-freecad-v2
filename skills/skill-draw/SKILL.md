# skill-draw

**Load when:** the user wants to draw something, has a sketch on paper, a photo, a scan, a Paint
screenshot or an SVG and wants it as CAD; "I'll draw it for you", "trace this", "turn this
picture into a part", laser-cut outline from a drawing.

## Use the tool
`freecad-layers` -> **`simulate(kind, target, params)`**, adapter `draw` (env `sim`). No image
editor is installed and none is driven through its windows: everything is files, so every step can
be re-run and checked.

| kind | params | main values / files |
|---|---|---|
| `trace_image` | `image` (a file name, put it in the project and pass it via `params.files`), `mm_per_pixel` **or** (`reference_length_mm` + `reference_pixels`), `threshold` (0-1), `dark_is_ink`, `simplify_mm`, `min_area_mm2` | `outlines`, `largest_width_mm`, `largest_height_mm`, `largest_area_mm2`, `largest_perimeter_mm`; files `trace.svg`, `trace.dxf` |
| `sketch_board` | none | file `sketch_board.html`: one offline page, draw with mouse/pen/finger, Save writes an SVG in real millimetres |
| `trace_svg` | `svg` (file name), `scale`, `simplify_mm` | `outlines`, `width_mm`, `height_mm`; files `clean.svg`, `clean.dxf` |

## The workflow that works
1. **The user draws.** Either run `sketch_board` and tell them to open the HTML file (any
   browser, no network), or take the photo/scan/PNG they already have.
2. **Set the scale honestly.** `mm_per_pixel` if the image came from a scanner at a known DPI
   (25.4/DPI), otherwise put a ruler or a coin in the photo and use
   `reference_length_mm` + `reference_pixels` measured on the same image.
3. **Trace**, then **check one dimension you know** against `largest_width_mm` before going
   further. A perspective error in a photo is a scale error no tolerance will catch.
4. **Into FreeCAD**: import `trace.svg` or `trace.dxf` (DXF polylines import cleanest), then
   build a real sketch on top of it and **constrain it**. A traced outline is not a parametric
   design: it is a backdrop you dimension against.
5. `check(target)` and `require(...)` as usual. Then Pad/Pocket.

## What to expect
- The outline is the edge of the **ink**, so a pencil line gives two outlines (both sides of the
  stroke). Trace a **filled** shape, or keep the outer outline only.
- Contours follow the pixel grid, so corners are chamfered by half a pixel and a circle's
  perimeter comes out a few per cent long (the area is right to a few parts in 10 000). Raise
  `simplify_mm` or draw bigger; do not argue about the last 0.1 mm.
- `simplify_mm` keeps the extent exactly but cuts corners: a 0.3 mm tolerance on a 20 mm
  rectangle cost 3 % of area in the reference case. State the tolerance you used.
- `trace_svg` reads straight-segment paths (M/L/H/V/Z). Curves must be flattened on export, and
  group transforms are ignored - export without them.

## Validated on
Synthetic images whose geometry is countable by hand: a 40 x 25 pixel rectangle (exact size, area,
perimeter and vertex count, including the half-pixel corner chamfers), the same with the scale
given as a measured reference length, a disc of radius 30 px (area within 0.2 % of the circle),
an SVG rectangle round-trip, and a check that the drawing board calls nothing on the network.
See `REFERENCES` in `server/adapters/draw.py`.

## Limits
It does not recognise intent: you get the outline you drew, hand wobble included. No curve
fitting (no arcs or splines are recovered - everything is polylines). No perspective correction.
No text recognition. For a real technical drawing with dimensions, go the other way:
model it, then `drawing_sheet` (skill-techdraw).
