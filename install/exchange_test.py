"""Validate STEP / IGES exchange export (missing-domain: CAD interchange).

Run with:  A:\FreeCAD\bin\freecadcmd.exe install\exchange_test.py
Bundled FreeCAD only. Prints EXCHANGE_OK on success.
"""
import os
import sys
import tempfile
from pathlib import Path

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_repo, "server"))
_proj = tempfile.mkdtemp(prefix="mcpfc_exch_proj_")
os.environ["MCP_FREECAD_PROJECT_DIR"] = _proj    # before the layers are imported

import FreeCAD
import Import
import Part

doc = FreeCAD.newDocument("ExchangeTest")
box = doc.addObject("Part::Box", "Box")
box.Length, box.Width, box.Height = 15, 25, 35
doc.recompute()

d = tempfile.mkdtemp(prefix="mcpfc_exch_")
step = os.path.join(d, "part.step")
iges = os.path.join(d, "part.iges")

box.Shape.exportStep(step)
Import.export([box], iges)   # extension-driven (.iges)

for path in (step, iges):
    size = os.path.getsize(path)
    print(f"{os.path.basename(path)}: {size} bytes")
    assert size > 0, f"empty export: {path}"

# round-trip: re-import the STEP and confirm a solid comes back
rt = FreeCAD.newDocument("ExchangeRT")
Import.insert(step, "ExchangeRT")
rt.recompute()
solids = sum(len(o.Shape.Solids) for o in rt.Objects if hasattr(o, "Shape"))
print("reimported solids:", solids)
assert solids >= 1, "STEP round-trip lost the solid"

# --- `export` layer: exact formats read back and compared with the part ---
from freecad_layers import export
FreeCAD.setActiveDocument("ExchangeTest")
part = doc.addObject("Part::Feature", "Bracket")
part.Shape = Part.makeBox(30, 20, 10).cut(Part.makeCylinder(4, 10, FreeCAD.Vector(15, 10, 0)))
part.Placement.Base = FreeCAD.Vector(-5, 7, 2)
doc.recompute()
for fmt in ("step", "stp", "brep"):
    r = export.export("Bracket", fmt=fmt)
    print("EXPORT:", export.summary(r))
    assert r["status"] == "OK" and r["solids"] == 1, r
    assert abs(r["volume"] - part.Shape.Volume) < 1e-6 * part.Shape.Volume, r
    back = Part.read(r["path"])
    assert abs(back.BoundBox.XMin + 5) < 1e-6 and abs(back.BoundBox.ZMin - 2) < 1e-6, back.BoundBox
r = export.export("Bracket", fmt="iges")
print("EXPORT:", export.summary(r))
assert r["status"] == "OK" and r["faces"] == len(part.Shape.Faces) and "prefer STEP" in r["advisory"], r
assert Path(r["path"]).parent == Path(_proj).resolve() / "exports", r
print("export STEP/STP/BREP (same volume, same place) and IGES (same faces): OK")
print("EXCHANGE_OK")
