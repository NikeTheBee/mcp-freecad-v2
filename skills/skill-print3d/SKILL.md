# skill-print3d

**Load when:** preparing a model for 3D printing — STL/mesh export, watertight check, scale/units.

## What this is
3D-print export using **bundled FreeCAD** (`Part`, `Mesh`/`MeshPart`) — no external install, works on
FreeCAD 1.1 today. The golden rule (cahier des charges §7.2): **never export a non-watertight solid.**

## Recipe (headless / execute_python)
```python
import sys; sys.path.insert(0, r"<repo>\server")
from freecad_layers import verify

# 1. GATE on watertightness before exporting
assert verify.watertight(obj_name), verify.verdict(obj_name)

# 2. Export STL (units are mm — FreeCAD's native; most slicers expect mm)
doc.getObject(obj_name).Shape.exportStl(r"out.stl")
```
For finer mesh control use the `Mesh` workbench:
```python
import Mesh, MeshPart
m = MeshPart.meshFromShape(Shape=obj.Shape, LinearDeflection=0.1, AngularDeflection=0.5,
                           Relative=False)
Mesh.Mesh(m.Topology).write(r"out.stl")
```
Lower `LinearDeflection` → finer surface (smaller facets, bigger file).

## Repair imported / dirty geometry (§4.6)
Before printing a supplier STL or a messy mesh, run the standard repair passes (bundled `Mesh`):
```python
import Mesh, MeshPart
m = MeshPart.meshFromShape(Shape=obj.Shape, LinearDeflection=0.2)   # or Mesh.read("supplier.stl")
m.harmonizeNormals(); m.removeNonManifolds(); m.fillupHoles(10)
m.removeDuplicatedPoints(); m.removeDuplicatedFacets()
assert not m.hasNonManifolds() and m.isSolid()   # clean + watertight before export
```
Checks: `hasNonManifolds()`, `hasSelfIntersections()`, `isSolid()`. Example: `install/mesh_repair_test.py`.

## Via the MCP tools (AI-driven)
1. **`printability(target, bed_mm, min_wall_mm)`** (`freecad-layers`) first: watertight valid solid,
   fits the bed (any 90° turn), walls ≥ the minimum (exact ray-cast, still sampled: say "estimate"),
   overhang area. Export only on `OK`.
   - An assembly (several solids) is judged as parts printed **one by one**; the advisory says
     whether they would fit printed together. Print-in-place: check the assembled overhangs too.
2. `mesh_operations` (export/convert); `measurement_operations` `get_volume` for a last sanity check.

## Checklist before sending to a slicer
- `verify.watertight(obj)` is True (closed solid, valid).
- Dimensions in **mm** (`measurement_operations get_bounding_box`); rescale if the source was in cm/inch.
- Orientation: flattest stable face down (set via `move`/`rotate`) to reduce supports.

Runnable example: `install/print3d_test.py`.
