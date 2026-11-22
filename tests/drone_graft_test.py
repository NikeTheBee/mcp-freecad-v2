"""Controlled, on-demand validation of the AirPlaneDesign workbench (Phase 3 graft #2).

Run with:  A:\FreeCAD\bin\freecadcmd.exe tests\drone_graft_test.py

The workbench's higher-level builders (wing/rib/panel) import FreeCADGui and are
GUI-only. The reusable headless core is libAeroShapes.py (pure FreeCAD/Part/math):
aero coordinate generators. This builds a NACA-4 airfoil profile from those coords
with Part and reports the resulting wire. Prints DRONE_GRAFT_OK on success.
"""
import os
import sys

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WB_ROOT = os.path.join(_repo, "addons", "AirPlaneDesign")
if not os.path.isdir(WB_ROOT):
    sys.exit(f"AirPlaneDesign clone not found at {WB_ROOT} — see install/WINDOWS-SETUP.md")
sys.path.insert(0, WB_ROOT)

import types
import FreeCAD
import Part

# libAeroShapes does `import DraftTools` at module load, which needs FreeCADGui
# (GUI-only) and fails under freecadcmd. getNACACoords itself is pure math, so
# stub DraftTools to let the module import headless.
sys.modules.setdefault("DraftTools", types.ModuleType("DraftTools"))
import libAeroShapes

doc = FreeCAD.newDocument("DroneGraftTest")

coords = libAeroShapes.getNACACoords(longueur=200.0, diametre=30.0, nbPoints=60)
print(f"NACA_POINTS={len(coords)}")
wire = Part.makePolygon(coords)
obj = doc.addObject("Part::Feature", "Airfoil")
obj.Shape = wire
doc.recompute()

shp = obj.Shape
print(f"VALID={shp.isValid()}  EDGES={len(shp.Edges)}  LENGTH={shp.Length:.2f}")
print(f"BBOX={shp.BoundBox}")
assert shp.isValid() and len(coords) == 60, "AirPlaneDesign graft produced invalid geometry"
print("DRONE_GRAFT_OK")
