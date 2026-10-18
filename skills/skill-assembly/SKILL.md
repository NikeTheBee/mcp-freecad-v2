# skill-assembly

**Load when:** combining multiple parts into a product — assemblies, positioning parts, joints/mates,
exploded layouts.

## What this is
The native **Assembly** workbench (FreeCAD 1.1) plus `App::Part`/`App::Link` containers. Structure +
placement-based assembly work headless; the **joint solver** (mates/constraints) is GUI/solver-coupled,
so for headless pipelines prefer explicit `Placement` positioning.

## Build an assembly (headless / execute_python)
```python
import Assembly                       # registers the Assembly type
asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
for part in (base, lid):
    asm.addObject(part)               # parts become assembly children
```

## Position parts (placement-based — robust headless)
```python
from FreeCAD import Vector, Placement, Rotation
lid.Placement = Placement(Vector(0, 0, 10), Rotation(Vector(0,0,1), 0))   # translate + rotate
```
`obj.Shape` bakes in the object's own `Placement` but NOT its container's: a part inside an
`App::Part` or an Assembly that is moved has its `Shape` in the container's frame. Build layouts with
`verify.global_shape(obj)` (document frame), e.g. `Part.makeCompound([verify.global_shape(a), ...])`.
The layers (`check`, `check_assembly`, `no_interference`, `analyze`, `export`, `render_views`) already
measure parts where they are; `fem_static` refuses a part inside a moved container (analyze a copy at
the origin).

## Reuse parts as instances
For repeated parts use links instead of copies:
```python
inst = doc.addObject("App::Link", "Bolt_2"); inst.LinkedObject = bolt
inst.Placement = Placement(Vector(40, 0, 0), Rotation())
```

## Joints (GUI/solver)
Mate/align/axis joints from the Assembly WB need the solver (interactive). For automated builds, express
the same intent as `Placement`s computed from the mating geometry. Document joints as design decisions via
`skill-verify` (`state.add_decision`).

## Fits, tolerances & functional clearances (§4.4)
For mating parts, size the hole from the shaft by fit class (helper `hole_for_shaft` in
`install/tolerances_test.py`):
```python
FITS = {"loose":(0.20,0.50), "clearance":(0.05,0.20), "close":(0.01,0.06),
        "transition":(-0.02,0.02), "press":(-0.06,-0.02)}   # diametral allowance (mm)
hole_d = shaft_d + sum(FITS["clearance"])/2     # e.g. Ø10 shaft -> Ø10.125 hole
```
Validate by booleaning the mated parts: a **clearance** fit has ~zero `common().Volume`, a **press**
fit shows interference. For exact ISO 286 limits use deviation tables; these are practical clearances.

## Verify
Combine and check the assembled envelope / interference with `Part.makeCompound` + `skill-verify`
(`verify.verdict`). Runnable examples: `install/assembly_test.py`, `install/tolerances_test.py`.
