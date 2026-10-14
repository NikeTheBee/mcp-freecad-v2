# skill-kinematics

**Load when:** motion of a mechanism — linkages, four-bar, slider-crank, a gripper/finger,
transmissions, "input/output ratio", "opening vs stroke" (cahier des charges §4.2 FK, §4.4).

## What this is
First-party `server/freecad_layers/kinematics.py`: an **analytical planar linkage solver**
(dependency-free, headless, exact) plus a passthrough to FreeCAD's native Assembly solver.
It generalizes the method that solved the gripper in `examples/` to 3 µm.

## Solve an input/output law (no GUI, no solver deps)
```python
import sys; sys.path.insert(0, r"<repo>\server")
from freecad_layers import kinematics as kin

# slider-crank (e.g. a gripper finger): crank pinned at A, coupler to a sliding point
sc = kin.SliderCrank(pivot=(-20, 52.25), crank=17.5, link=30.0,
                     slider_origin=(0, 11.39), slider_dir=(0, 1),
                     ref_coupler=(-18.77, 34.79))
sc.law(range(-2, 7))            # [(s_mm, output_deg_vs_reference), ...]
sc.ratio_deg_per_mm(0.0)        # transmission ratio at a stroke position
sc.dead_points()                # {'extended': -2.22, ...} -> travel limits/toggles
```
```python
# four-bar crank-rocker: input crank angle -> output rocker angle
fb = kin.FourBar(a=(0,0), d=(40,0), r_in=15, coupler=45, r_out=25)
fb.law([math.radians(t) for t in range(30,120,10)])
```
Assembly mode: a linkage closes two ways (C on either side of line B→D). It is chosen once, at
`ref_theta` (`elbow_up` = the higher C there; for a slider-crank, the side of `ref_coupler`), and
kept for every angle — the same input always gives the same output, wherever a sweep starts.
`law()` outputs are continuous along the sweep: an output that turns fully counts to 360°.

The primitive under both is `kin.circle_intersect(a, ra, c, rc)` (the RRR dyad) — use it to build
any planar chain. Extract the pivot points from a real model via cylindrical-hole axes (see how
`examples/` reads them from the STEP), then feed them here.

## Native Assembly solver (GUI-authored joints)
FreeCAD's OndselSolver runs headless: `kin.native_solve(assembly_obj)` → `{ok, convergence_code}`.
**Creating** joints needs element references and is GUI-coupled (docs/COMPATIBILITY.md R8), so
author joints in the Assembly GUI, then drive/solve headless. For fully-scripted motion, prefer the
analytical solver above.

## Verify
`install/kinematics_test.py` (`KINEMATICS_OK`) — reproduces the gripper law (~4.5°/mm, toggle at
≈−2.2 mm), a crank-rocker four-bar, and the native-solver passthrough.
