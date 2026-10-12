"""Headless 3-view render test (freecadcmd).

Run with:  A:\\FreeCAD\\bin\\freecadcmd.exe install\\render_test.py

Checks the PICTURE, not just that a file exists:
- top view of a 60x40 plate with a centred D10 hole: the drawn plate keeps its
  60:40 proportions, the hole centre is background and the plate around it is
  coloured (projection, scale and back-face culling are right);
- a 2x2 grid of an assembly renders with one colour per part;
- bad inputs are refused: unknown view, an object without faces, a non-.png path.
Prints RENDER_OK on success.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))

import FreeCAD  # noqa: E402
import Part  # noqa: E402
from freecad_layers import render  # noqa: E402

out_dir = tempfile.mkdtemp(prefix="mcpfc_render_")
doc = FreeCAD.newDocument("RenderTest")
plate = doc.addObject("Part::Feature", "Plate")
plate.Shape = Part.makeBox(60, 40, 8).cut(Part.makeCylinder(5, 8, FreeCAD.Vector(30, 20, 0)))
post = doc.addObject("Part::Feature", "Post")
post.Shape = Part.makeCylinder(4, 20, FreeCAD.Vector(10, 10, 8))
doc.recompute()

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402

# 1. top view, single panel: measure the picture
r = render.views("Plate", os.path.join(out_dir, "top.png"), which=["top"], size_px=600)
img = mpimg.imread(r["path"])[:, :, :3]
h, w, _ = img.shape
# plate pixels: clearly coloured (not white background, not black frame/title text)
mask = (img.max(axis=2) - img.min(axis=2)) > 0.12
ys, xs = mask.nonzero()
x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
ratio = (x1 - x0) / (y1 - y0)
print(f"plate drawn {x1 - x0}x{y1 - y0} px, ratio {ratio:.3f} (expected 1.5)")
assert abs(ratio - 1.5) < 0.04, ratio
cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
assert not mask[cy, cx], "hole centre should be background"
assert mask[cy, x0 + (x1 - x0) // 8] and mask[y0 + (y1 - y0) // 8, cx], "plate should be coloured"
print("top view: proportions right, hole is see-through, plate is filled: OK")

# 2. 2x2 grid of an assembly: two distinct part colours present
r = render.views(["Plate", "Post"], os.path.join(out_dir, "grid.png"))
img = mpimg.imread(r["path"])[:, :, :3]
assert r["views"] == ["front", "top", "right", "iso"] and r["bbox"] == [60.0, 40.0, 28.0], r
mask = (img.max(axis=2) - img.min(axis=2)) > 0.12
blueish = (img[:, :, 2] > img[:, :, 0]) & mask
orange = (img[:, :, 0] > img[:, :, 2]) & mask
assert blueish.sum() > 5000 and orange.sum() > 500, (blueish.sum(), orange.sum())
print(f"grid: {r['triangles']} triangles, both parts visible in their own colour: OK")

# 3. bad inputs refused
sketchy = doc.addObject("Part::Feature", "Wire")
sketchy.Shape = Part.makeLine(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0))
doc.recompute()
for kw in (dict(targets="Plate", path=os.path.join(out_dir, "a.png"), which=["side"]),
           dict(targets="Wire", path=os.path.join(out_dir, "b.png")),
           dict(targets="Plate", path=os.path.join(out_dir, "c.exe"))):
    try:
        render.views(**kw)
    except (ValueError, FileExistsError) as e:
        print("refused:", str(e)[:70])
    else:
        raise AssertionError(f"accepted {kw}")
print("RENDER_OK")
