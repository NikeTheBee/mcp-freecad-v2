"""Validate the 3D-printing / STL-export path (Part B1).

Run with:  A:\FreeCAD\bin\freecadcmd.exe install\print3d_test.py

Uses only bundled FreeCAD (Part/Mesh) — no external install. Builds a solid,
gates export on the home-grown watertight check (reuse of freecad_layers.verify),
exports STL, and asserts a non-empty file. Then the `export` layer: STL/3MF
only for a part `check` passes, the file read back and compared with the solid,
nothing left behind when it is refused. Prints PRINT3D_OK on success.
"""
import os
import sys
import tempfile
from pathlib import Path

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_repo, "server"))
_tmp = tempfile.mkdtemp(prefix="mcpfc_print3d_")
os.environ["MCP_FREECAD_PROJECT_DIR"] = _tmp     # before the layers are imported

import FreeCAD
import Part
from freecad_layers import export, spec, verify

doc = FreeCAD.newDocument("Print3DTest")
box = doc.addObject("Part::Box", "Box")
box.Length, box.Width, box.Height = 20, 20, 20
doc.recompute()

# Gate export on watertightness (the §7.2 rule)
assert verify.watertight("Box"), "refusing to export non-watertight shape"
print("WATERTIGHT:", verify.verdict("Box"))

out = os.path.join(tempfile.mkdtemp(prefix="mcpfc_stl_"), "box.stl")
box.Shape.exportStl(out)
size = os.path.getsize(out)
print(f"STL: {out} ({size} bytes)")
assert size > 0, "STL export produced an empty file"


def refused(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
    except (ValueError, FileExistsError) as e:
        return str(e)
    raise AssertionError(f"accepted: {a} {kw}")


# --- `export`: a plate with holes and rounded corners, moved off the origin ---
plate = Part.makeBox(60, 40, 8)
for x, y in ((10, 10), (50, 10), (30, 30)):
    plate = plate.cut(Part.makeCylinder(3, 8, FreeCAD.Vector(x, y, 0)))
plate = plate.makeFillet(2, [e for e in plate.Edges if e.Curve.TypeId == "Part::GeomLine"
                             and abs(e.BoundBox.ZLength - 8) < 1e-6])
p = doc.addObject("Part::Feature", "Plate")
p.Shape = plate
p.Placement.Base = FreeCAD.Vector(100, -50, 5)
doc.recompute()
spec.require("Plate", "bbox", size=[60, 40, 8])
proj = Path(_tmp).resolve()

r = export.export("Plate", "out/plate.stl")            # relative -> the project folder
print("EXPORT:", export.summary(r))
f = Path(r["path"])
assert r["status"] == "OK" and f.is_file() and f.is_relative_to(proj), r
assert abs(r["volume"] - p.Shape.Volume) < 0.01 * p.Shape.Volume, r
assert r["bbox"] == [60, 40, 8] and r["facets"] > 200, r
import Mesh
m = Mesh.Mesh(str(f))
assert abs(m.BoundBox.XMin - 100) < 1e-3 and abs(m.BoundBox.ZMin - 5) < 1e-3, m.BoundBox  # placed
assert [x.name for x in f.parent.iterdir()] == ["plate.stl"], "temporary file left"

r3 = export.export("Plate", fmt="3mf")                  # default: <project>/exports/<name>.3mf
assert Path(r3["path"]) == proj / "exports" / "Plate.3mf" and r3["status"] == "OK", r3
assert Path(r3["path"]).stat().st_size < f.stat().st_size, "3MF should be the compact one"
assert export.export("Plate", "named", fmt="STL")["path"].endswith("named.stl")
# a loft: Shape.BoundBox says 37.3x26.8 for this 30x20 part, the mesh read back
# says 30x20 -> compared with the exact box, it is the same part
lo = doc.addObject("Part::Feature", "Loft")
lo.Shape = Part.makeLoft([Part.Wire(Part.makeCircle(10)),
                          Part.Wire(Part.Ellipse(FreeCAD.Vector(0, 0, 20), 15, 5).toShape())], True)
doc.recompute()
r = export.export("Loft", "out/loft.stl")
assert r["status"] == "OK" and r["solid_bbox"] == [30, 20, 20], r
assert all(abs(g - w) < 0.01 for g, w in zip(r["bbox"], (30, 20, 20))), r   # chords: a hair inside
print("export STL/3MF: placed, measured, relative path in the project: OK")

# --- refusals leave nothing behind ---
assert "exists" in refused(export.export, "Plate", "out/plate.stl")
assert "format must be" in refused(export.export, "Plate", fmt="obj")
pdb = doc.addObject("PartDesign::Body", "PB")
pad = pdb.newObject("PartDesign::AdditiveBox", "PadStep")
pdb.newObject("PartDesign::SubtractiveCylinder", "Drill").Height = 20
doc.recompute()
assert "is a step of PB, not the whole part: export PB" in refused(export.export, "PadStep", "out/x.stl")
assert not (proj / "out" / "x.stl").exists()
sid =spec.require("Plate", "mass", density_g_cm3=1.24, max=5)        # ~23 g: FAIL
why = refused(export.export, "Plate", "out/heavy.stl")
assert "check says FAIL" in why and "mass" in why, why
assert not (proj / "out" / "heavy.stl").exists(), "refused export left its file"
spec.remove(sid)
shell = doc.addObject("Part::Feature", "OpenBox")
shell.Shape = Part.Shell(Part.makeBox(10, 10, 10).Faces[:5])
doc.recompute()
assert "check says FAIL" in refused(export.export, "OpenBox", "out/open.stl")
assert not (proj / "out" / "open.stl").exists()

# a file that does not match the part is never handed over, nor replaces a good one
good = f.read_bytes()
real = export._write_mesh
export._write_mesh = lambda shp, tmp, defl: real(shp.translated(FreeCAD.Vector(0, 0, 1)), tmp, defl)
try:
    why = refused(export.export, "Plate", "out/plate.stl", overwrite=True)
    assert "does not match" in why and "ZMin +1" in why, why
    assert f.read_bytes() == good, "the good file was replaced by a bad one"
    why = refused(export.export, "Plate", "out/new.stl")
    assert not (proj / "out" / "new.stl").exists(), "bad export left a file"
finally:
    export._write_mesh = real
assert sorted(x.name for x in (proj / "out").iterdir()) == ["loft.stl", "plate.stl"], "file left behind"
assert export.export("Plate", "out/plate.stl", overwrite=True)["status"] == "OK"
print("export refusals: FAIL contract, open shell, mismatching file, no clobber: OK")
print("PRINT3D_OK")
