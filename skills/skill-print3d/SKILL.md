# skill-print3d

**Load when:** preparing a model for 3D printing — STL/3MF export, watertight check, scale/units.

## What this is
3D-print export using **bundled FreeCAD** (`Part`, `Mesh`/`MeshPart`) — no external install, works on
FreeCAD 1.1 today. The golden rule (cahier des charges §7.2): **never export a non-watertight solid.**

## Use the MCP tools (the code decides)
1. **`printability(target, bed_mm, min_wall_mm)`** (`freecad-layers`) first: watertight valid solid,
   fits the bed (any 90° turn), walls ≥ the minimum (rays on exact surfaces + the closest faces
   measured two by two; a wall between two faces meeting at an edge is still sampled: say "estimate"),
   overhang area (curved faces measured over the face: the ceiling of a horizontal hole counts; a
   wall exactly at 45° does not). Overhangs are an advisory unless you set `max_overhang_area_mm2`.
   - An assembly (several solids) is judged as parts printed **one by one**; the advisory says
     whether they would fit printed together. Print-in-place: check the assembled overhangs too.
2. **`export(target, format="3mf")`** (or `"stl"`): refused unless `check` is OK; the file is read back
   and compared with the solid (closed, oriented, volume, size and position). 3MF is ~5x smaller and
   carries its units (mm); STL has none. Relative paths land in the project folder. Solids that only
   touch each other are refused (internal faces): overlap and fuse them, or export them one by one.

## Checklist before sending to a slicer
- `export` said `OK` (it already checked closed, oriented, volume, size).
- Dimensions in **mm** (`measurement_operations get_bounding_box`); rescale if the source was in cm/inch.
- Orientation: flattest stable face down (set via `move`/`rotate`) to reduce supports.

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

## Only without the tools (execute_python)
`export` is the same code with the checks: `from freecad_layers import export;
export.export(obj_name, "part.3mf")`. A bare `Shape.exportStl("out.stl")` checks nothing and its
default triangles are coarse; never hand such a file over unverified.

Runnable example: `install/print3d_test.py`.
