# skill-verify

**Load when:** you need to confirm produced geometry is sound before continuing, gate an STL export, check an assembly for overlaps, save a checkpoint / roll back, recover after a crash, or read/update project memory. These are the home-grown robustness layers (cahier des charges §7).

## Availability
Python package at `server/freecad_layers/` (tracked, first-party). Import it inside FreeCAD / the MCP server's `execute_python`:
```python
import sys; sys.path.insert(0, r"<repo>\server")
from freecad_layers import state, verify, checkpoint, recovery
```
Paths resolve to `<repo>/project_state`, `<repo>/checkpoints`, `<repo>/recovery` (override with the `MCP_FREECAD_PROJECT_DIR` env var).

## verify — deterministic verdicts (§7.2)
The code decides, not you. Three outcomes:
- `OK` — every check ran, none found a problem.
- `FAIL` — a problem was found: **fix it before continuing**.
- `UNKNOWN` — a check could not run: treat as **not verified**, never as OK.
```python
verify.verdict("Box")   # "OK Box: valid=True solids=1 closed=True V=6000 bbox=10x20x30"
verify.check("Box")     # dict: status, ok, issues[], unchecked[], volume, bbox,
                        #       center_of_mass, faces, edges, vertexes, solids, closed
verify.watertight("Box")                     # bool — GATE THIS before any STL export
verify.check_assembly(["Base", "Arm", "Pin"])  # overlaps between separate parts (mm3)
verify.verdict("Sketch", expect_solid=False)   # sketches/wires: "no solid" is expected
```
Checks: recompute state of the object **and its dependencies** (a failed recompute keeps the last
good shape — that is reported, not passed), shape validity, solid present, closed shells,
inverted/degenerate volume, OCCT self-intersection per solid, one solid per PartDesign result,
and overlap volume between solids. Touching parts (shared faces) are not an overlap.

**Compare the measurements with what the user asked** (bbox, volume, centre of mass): a valid
solid can still be the wrong part.

## checkpoint — named saves + rollback (§7.3)
```python
checkpoint.save("before_fins", note="clean body+nose", doc=FreeCAD.ActiveDocument)
checkpoint.list_checkpoints()                         # [{name, file, at, note, ...}]
checkpoint.restore("before_fins")                     # reloads FROM DISK, returns the doc
checkpoint.restore("before_fins", replace_active=True)  # true rollback: the current doc is
                                                        # saved to recovery/ then closed
```
Different names never share a file; re-saving the same name replaces that checkpoint.

## recovery — never lose work
```python
recovery.autosave()            # after each validated step (keeps the 5 newest per document)
recovery.list_recovery()       # newest first
recovery.open_latest("MyDoc")  # after a crash: reopen the last autosaved copy
```

## state — project memory (§7.1)
Read at session start; update **after each validated step** (never before).
```python
print(state.summary())                       # compact digest to read at session start
state.init_project("quad450", intent="...")  # once, at project creation
state.record_feature("Arm", "Part::Box", {"length": 200})  # after a validated step
state.set_parameter("wheelbase", 450); state.add_constraint("foldable arms")
state.add_decision("450mm wheelbase chosen for 10in props")
```

## Standard loop (token-minimal)
1. `state.summary()` at session start (and `recovery.summary()` if FreeCAD restarted).
2. Build a feature. 3. `verify.verdict(obj)` — `OK` only; `FAIL`/`UNKNOWN` → fix or ask.
4. `state.record_feature(...)` + `recovery.autosave()`. 5. `checkpoint.save(...)` before the next risky step.

Runnable examples: `install/layers_test.py`, `install/verify_test.py` (the traps that used to pass).
