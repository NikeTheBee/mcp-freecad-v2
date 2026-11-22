"""Controlled, on-demand validation of the Rocket workbench (Phase 2 graft #1).

Run with:  A:\FreeCAD\bin\freecadcmd.exe tests\rocket_graft_test.py

Loads the workbench from the in-project clone at addons/Rocket (NOT the auto-load
Mod dir), builds a body tube via the headless-safe Feature proxy (bypassing the
GUI command wrapper, which imports FreeCADGui unconditionally), and reports the
resulting solid. Prints ROCKET_GRAFT_OK on success.
"""
import os
import sys

# repo root = parent of this file's dir (install/ -> repo)
_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROCKET_ROOT = os.path.join(_repo, "addons", "Rocket")
if not os.path.isdir(ROCKET_ROOT):
    sys.exit(f"Rocket clone not found at {ROCKET_ROOT} - see install/WINDOWS-SETUP.md")
sys.path.insert(0, ROCKET_ROOT)

import FreeCAD
from Rocket.FeatureBodyTube import FeatureBodyTube

doc = FreeCAD.newDocument("RocketGraftTest")
obj = doc.addObject("Part::FeaturePython", "BodyTube")
FeatureBodyTube(obj)
obj.Proxy.setDefaults()
doc.recompute()

shp = obj.Shape
print(f"OBJECT={obj.Name}  DIAMETER={obj.Diameter}  THICKNESS={obj.Thickness}")
print(f"VALID={shp.isValid()}  SOLIDS={len(shp.Solids)}  VOLUME={shp.Volume:.2f}")
print(f"BBOX={shp.BoundBox}")
assert shp.isValid() and len(shp.Solids) == 1, "Rocket graft produced invalid geometry"
print("ROCKET_GRAFT_OK")
