# skill-drone

**Load when:** the task involves fixed-wing / VTOL / drone aero surfaces — airfoils (NACA), wings, fuselage/nacelle profiles. (Quadcopter *frames* — arms, motor mounts, electronics bays — are plain PartDesign; use `skill-partdesign` for those.)

## What this is
The [AirPlaneDesign workbench](https://github.com/FredsFactory/FreeCAD_AirPlaneDesign) (FredsFactory, LGPL-2.1, v0.4.1, min FreeCAD 0.18). Grafted as Phase 3 domain #2.

## Availability & headless gotchas (important)
This workbench is **GUI-only** (`InitGui.py`, no `Init.py`): the wing/rib/panel/nacelle builders all `import FreeCADGui` and won't run under `freecadcmd`. The reusable **headless** core is `libAeroShapes.py` — pure aero-coordinate math.

Cloned at `addons/AirPlaneDesign` (gitignored). It is **NOT** auto-loaded from the Mod dir (pending explicit authorization to activate). Use it on demand:
```python
import sys, types
sys.path.insert(0, r"<repo>\addons\AirPlaneDesign")   # or v1-1\Mod\AirPlaneDesign if activated
# libAeroShapes does `import DraftTools` at load (GUI-only) -> stub it; the coord
# generators are pure math and don't use it:
sys.modules.setdefault("DraftTools", types.ModuleType("DraftTools"))
import libAeroShapes
```

## Coordinate generators (all return a list of `FreeCAD.Vector`)
| Function | Shape |
|---|---|
| `getNACACoords(longueur, diametre, nbPoints=100)` | NACA-4 airfoil (upper surface) |
| `getLyonCoords(longueur, diametre, nbPoints=100)` | Lyon fuselage/nacelle profile |
| `getHoernerCoords(longueur, diametre, xRelEpaisseurMax, nbPoints=100)` | Hoerner profile |
| `getDuhamelCoords(longueur, diametre, nbPoints=100)` | Duhamel profile |

Build geometry from the points with `Part` (no Draft needed):
```python
import Part
coords = libAeroShapes.getNACACoords(longueur=200.0, diametre=30.0, nbPoints=60)
wire = Part.makePolygon(coords)        # or Part.BSplineCurve().interpolate(coords)
obj = doc.addObject("Part::Feature", "Airfoil"); obj.Shape = wire; doc.recompute()
```

## Sizing & performance (analytical — no CFD)
`server/freecad_layers/aero.py`: hover thrust/power (momentum theory), thrust-to-weight,
disk loading, endurance, tip Mach, wing loading, stall speed. One-line verdicts:
```python
import sys; sys.path.insert(0, r"<repo>\server")
from freecad_layers import aero
aero.verdict_multirotor(mass_kg=1.2, n_motors=4, prop_diameter_m=0.254,
                        max_thrust_per_motor_n=8.0, battery_wh=55.5)
```
Inputs are validated (mass, motors, diameters must be > 0 → clear `ValueError`).
Limits: momentum theory ignores profile drag and interference — hover power ±15–25 %.

## Flow analysis (drag / lift) — external CFD only
No CFD runs in this project. If the user needs real flow results:
1. Gate the geometry: `check` (freecad-layers) must be `OK` and the part watertight.
2. Export **STL** (`skill-print3d`, for `snappyHexMesh`) or **STEP** (`skill-exchange`).
3. Describe the case: incompressible steady RANS (`simpleFoam`, k-ω SST), box domain ≥ 10
   body-lengths downstream, `forceCoeffs` for Cd/Cl. Running OpenFOAM is outside this repo.
4. CfdOF (GUI front-end to OpenFOAM) only if the user already has OpenFOAM + ParaView.
Say plainly that prototype CFD is not engineering-grade aerodynamics.

## Verify
`obj.Shape.isValid()`, `len(obj.Shape.Edges)`, `obj.Shape.Length`, `obj.Shape.BoundBox`.
Runnable end-to-end examples: `install/drone_graft_test.py`, `install/drone_aero_test.py`.
