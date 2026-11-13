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

## Joints (Assembly solver)
The base's `assembly_operations add_component` puts an `App::Link` to the part in the assembly
(e.g. `Top` -> `Top001`), starting where the part is (rotation and moved containers included;
`x/y/z` set the position) and leaves the source visible. Joints and `solve` move the LINK: check
interference, measure and render the links (`Top001`), not the sources. Hide the sources if the
scene should show only the assembly.

Verified headless against known landings (`install/assembly_ops_test.py`, link bbox exact to 1e-6):
| joint | verified | note |
|---|---|---|
| Fixed, Revolute, Cylindrical, Slider | yes | face-to-face / circle-to-circle seat on the element centre; Slider keeps the part on the axis |
| Ball | yes | vertex onto vertex |
| Distance | partly | plane-plane gap exact; other pairs (circle-circle...) follow FreeCAD's own meaning -- measure |
| Parallel, Perpendicular, Angle | yes | orientation only, position left free |
| RackPinion, Screw, Gears, Belt | no | motion couplings: a static solve moves nothing -- not measured |

- Every `create_joint` / `solve` / `set_joint_offset` / `set_joint_limits` reply measures each joint on
  its two connectors after FreeCAD's solve. FreeCAD returns 0 even for conflicting joints:
  `solve` then says `joints_not_satisfied` and lists what does not hold. Never treat that as success.
- Limits (`set_joint_limits`): length only on Cylindrical/Slider, angle only on Revolute/Cylindrical.
  A headless solve does NOT enforce them (they bound GUI dragging); a part outside them is reported.
- `ref*_vertex` must be a vertex of its element (or, on a face, one of its circular edges = centre).
  Omit it to use the element's centre.
- The solver keeps the connector orientation (matched or flipped) nearer the part's start: a part
  starting upside down can land INSIDE the other one. The reply warns ("overlap"); delete the joint
  and recreate it with `reverse=true` (Fixed/Revolute/Cylindrical/Slider/Distance/Parallel).
- `set_joint_offset` sets (replaces) the offset in that connector's own frame: connector 1 `z=5`
  lifts the part 5 mm off a face; connector 2 `z=5` on a box's bottom face sank it 5 mm (that
  connector's Z pointed up, into the part): the sign follows the connector, measure after.
- No part grounded: the solver may move every part (the reply says so) -- `ground_part` one first.
- Joints on parts inside a moved/rotated assembly are relative to the assembly: verified.
Document joints as design decisions via `skill-verify` (`state.add_decision`).

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
