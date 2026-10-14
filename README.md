# MCP FREECAD 2

[![Tests](https://github.com/NikeTheBee/mcp-freecad-v2/actions/workflows/tests.yml/badge.svg)](https://github.com/NikeTheBee/mcp-freecad-v2/actions/workflows/tests.yml)
[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm_Noncommercial_1.0.0-lightgrey.svg)](LICENSE)
[![FreeCAD 1.1](https://img.shields.io/badge/FreeCAD-1.1.x-red.svg)](https://www.freecad.org)
🇫🇷 [Version française](README.fr.md)

Design mechanical, aero & robotics parts in **FreeCAD** using **natural language**, through any AI
client that speaks **MCP** — with a reliability layer that **measures** the result instead of
trusting the AI's reading of it.

> Describe intent ("a 60×40×8 mm plate with a Ø6 through-hole in the centre"); the AI models it,
> records your requirements as checks, verifies geometry and requirements after every step,
> autosaves, and lets you roll back.

## Since 2.1 (unreleased)
- **Verified export** (`export`, new tool): STL or 3MF for printing, STEP, BREP or IGES for another
  CAD, written only for a part `check` passes. The file is then read back and compared with the
  solid (volume, size, position, closed mesh). A file that does not match is never handed over,
  and never replaces a good one.
- **Overhangs of curved faces are measured**: each face was judged by its middle normal only,
  so the ceiling of a horizontal hole, the underside of a sphere or of a rod lying down counted
  0 mm2. They are now measured over the face (within 1 % of the known values), and a wall
  exactly at the limit (the 45° chamfer printed without supports) is not counted.
- **Exact sizes on curved parts**: FreeCAD's quick bounding box read a 30×20 loft as 37.3×26.8,
  and a revolved B-spline as smaller than it is (so a real collision was skipped). Sizes,
  requirements, bed fit, exports and collision checks now use the exact box, in the layers
  and in the base.
- **Holes measured right on real parts**: a through hole in the base of an L-bracket, under a
  counterbore or tilted read "blind" (it was compared with the height of the whole part), and
  a bore stored as a B-spline (a STEP from another CAD) was not seen at all. "Through" now
  means both ends open, decided on the faces at each end (1,600 holes: under a second).
  A blind hole made by `pd_hole` (or the base's hole on a Body) starts at the surface under it,
  not at the top of the whole part (on a stepped part it began in the air), and is measured once
  made: through, depth. On a plain Part solid, the base's countersink cut nothing (its cone lay
  under the part) and the counterbore went on the bottom face: both now sit on the top.
- **Extrusions keep their holes**: the base's Part extrude/revolve used the sketch's first wire
  only, so a plate sketch came out without its holes (or as a lone cylinder when a hole was drawn
  first); a rib went along the global Y axis and made solids of zero volume. Both now use the
  whole profile along the sketch's own normal, and refuse a direction lying in the sketch.
- **Fillets, chamfers and shells land in the part**: "fillet all edges" of a PartDesign part made
  a separate object beside the Body, which kept its sharp edges while the reply said "all 12
  edges" (same for chamfer, shell, loft and sweep: a loft of a Body's sketches left the Body
  empty). They are now features of the Body (and `check` notes any solid still built on a Body's
  feature outside it), checked against the
  same operation done directly (to 1e-6). Thickness/shell/draft take `faces=[...]` headless,
  where they used to ask for a selection in a window that does not exist.
- **Revolve around the axis you name**: on a sketch drawn in the XZ plane, "around y" turned around
  Z while the reply said "around Y-axis" (the letter named the sketch's own axes). Revolution,
  groove and helix now take the part's axes, like the patterns; the sketch's normal is refused.
- **"No interference" includes the part it is set on**: `require("Plate", "no_interference",
  objects=[pins])` checked the pins against each other only, so an oversize pin in the plate
  passed.
- **A step is not the part**: `export`, `printability`, `fem_static` and `drawing_sheet` on a
  PartDesign step ("Pad") worked on that step: `export("Pad")` after drilling wrote the part
  without its holes. They now refuse it and name the Body; `check` and `require` on a step say so.
- **A feature left out of the part is a FAIL**: a Body whose Tip stops before its last feature, or
  with a suppressed feature, used to check OK (a valid solid, without the hole).
- **Files land in the project**: a file named without a folder (`plan.dxf`, `part.stl`) used to land
  among the server's code; it now goes to the project folder.
- **Heavy parts** (measured on a plate with 1,600 holes):
  - wall thickness: 73 s → 1.1 s, and now exact (rays meet the real surfaces, no mesh; faces
    closer than the rays found are measured two by two: a hole 0.3 mm from an edge read 0.81 mm);
  - `printability`: 129 s → 2 s; `analyze`: 132 s → 2 s; `render_views`: 48 s → 13 s;
  - hole detection: 2.7 s → 0.2 s (same result, without comparing every pair);
  - `check` runs the slow OCCT analysis once per shape version;
  - the analysis tools wait up to 5 minutes for FreeCAD instead of 2, and a call that is
    still running is explained plainly instead of a raw "no response".
- **Bad inputs get a reason, not a wrong answer** (all 17 layer tools swept): malformed
  requirements are refused (a typo no longer weakens the contract), a requirement can be
  replaced or removed (`replaces="S3"`), and a hole that cuts nothing or breaks the part is
  rolled back.
- `fem_static` refuses a hopeless mesh size at once (Gmsh used to grind on it first) and says
  which size works.
- **A long operation no longer looks like a crash**: past 35 s, the base reported "FreeCAD
  crashed" while headless FreeCAD was still computing. It now returns the result (or, past
  2 minutes, a job id to poll). A real crash during a layers call is reported as one, with the
  way back (`recover`).
- **A restart can no longer lock you out**: when two FreeCADs started at once during a restart,
  the one that could not get the port overwrote the other's access key before quitting, and
  every call was then refused ("did not prove it is your FreeCAD") until the next restart. The
  key is now written only by the FreeCAD that holds the port, and the call whose FreeCAD lost
  the race gets the winner instead of "FreeCAD exited during start".
- **A stuck FreeCAD can be restarted by the assistant** (`restart_freecad`, headless too): it saves
  what it safely can, exits, and the launcher starts a fresh one. Before, only killing the
  process by hand helped.
- **Assemblies measured part by part**: the parts list counts identical parts once whatever
  their orientation, and keeps a left and a right hand apart (same measures, mirror images); holes, overhangs and printability judge each part on its own; a plan
  never puts an overall size on an iso view (a projected length is not a size).
- **Rollback and recovery keep the work whole**: a restored checkpoint or recovered copy reopens
  under the document's own name, as a working copy (a later save no longer overwrites the
  checkpoint), and after a crash `recover` brings back the rollback you chose.

## What's new in 2.1
- **Stress analysis** (`fem_static`, CalculiX): displacement, von Mises stress and safety factor,
  with a verdict. A cantilever comes within 0.3 % of F·L³/3EI.
- **Pictures without the GUI** (`render_views`): shaded front, top, right and iso views in one
  image.
- **Several projects side by side** (`project_init` never overwrites, `project_switch`) and
  **verified recipes** (`recipe`): a design is kept only when its contract is OK.
- **Safer by construction**:
  - FreeCAD proves it knows the token before anything is sent (HMAC challenge);
  - the CAM tools (machine G-code) are off unless you opt in;
  - the tools that cannot work on Windows are hidden (about 2,200 tokens saved per session).
- **Base updated to blwfish v8.3.0**, our changes kept. Also fixed in the base:
  - headless fillet and chamfer with named edges;
  - every non-Body chamfer;
  - crash tracking on Windows;
  - clear headless messages instead of Python errors, found by a sweep of all 190 operations.
- **Windows CI**: every push installs the official FreeCAD 1.1.1 and runs all 36 tests,
  nothing skipped.

## What's new in 2.0
- **The base server is part of the project.** blwfish/freecad-mcp v8.3.0 is vendored in
  `server/freecad-mcp/` (git subtree): fixed here, and still able to pull upstream updates
  (`python install/bootstrap.py --update-base`). Fixed in it: holes in a PartDesign Body, sketch
  "fully constrained" verdict, oversized replies that hung for 2 minutes
  ([`CHANGES-NikeTheBee.md`](server/freecad-mcp/CHANGES-NikeTheBee.md)).
- **Reliability layer** (`freecad-layers`, 17 tools): OK/FAIL/UNKNOWN verdicts, design contract,
  part analysis, **real FEM stress analysis (CalculiX)**, checkpoints, autosave/recovery,
  supervised FreeCAD restart, anti-squatting, and FreeCAD proves its identity before any command.
- **New licence**: PolyForm Noncommercial (free to use and modify, not to sell).
- Version 1 stays available, unchanged, at
  [NikeTheBee/mcp-freecad](https://github.com/NikeTheBee/mcp-freecad).

## Project goal: local-first AI CAD
The end goal is CAD driven by **local** AI models. The architecture is built on **MCP**, an open
protocol, so any MCP-capable agent can drive it. Frontier models (e.g. Claude Code) are used as the
reference client in the meantime.

## How it works
```
You (natural language)
  → AI client (Claude Code / Claude Desktop / any MCP client)
    ├─ `freecad`         launcher (starts/supervises FreeCAD, refuses a hijacked port)
    │                      → freecad-mcp bridge (blwfish v8.3.0, vendored + fixed, ~39 tools)
    └─ `freecad-layers`  18 reliability tools: project_status · project_init · project_switch · recipe ·
                          require · check · analyze · fem_static · render_views ·
                          check_assembly · printability · export · record_step · checkpoint ·
                          recover · bill_of_materials · drawing_sheet · pd_hole
         → FreeCAD 1.1 headless on localhost:23456 (token auth) + domain workbench grafts
```
- **The code decides, not the AI.** `check` returns `OK`, `FAIL` or `UNKNOWN` (a check that could
  not run is never reported as OK), plus kernel measurements and the user's requirements
  (bounding box, holes and their positions, mass, wall thickness, interferences).
- **Nothing is lost.** Project memory survives sessions and concurrent writers; autosave copies
  after each validated step; checkpoints reload from disk; FreeCAD is restarted at once after a
  crash.
- **Token-aware.** The reliability tools cost ~3,300 tokens of schema; results are short text
  verdicts; no screenshots by default.

## Install (one command, Windows)
```
git clone https://github.com/NikeTheBee/mcp-freecad-v2 "MCP FREECAD" && cd "MCP FREECAD"
python install/bootstrap.py --with-grafts
```
Needs **FreeCAD 1.1.x**, **Python 3.10+**, **git**, and an MCP client. The base server ships in the
repository; the installer pins every other third-party component to a commit, creates a dedicated venv (`~/.freecad-mcp/venv`, nothing goes
into your system Python), registers both MCP servers, and runs the tests. It stops on the first
error and never overwrites an unreadable Claude Desktop config. Re-running it is safe.
Linux/macOS: the pieces are portable but untested — contributions welcome.

## Verify
```
python install/run_all_tests.py
```
~37 tests: the audit's traps (verdicts that used to pass on wrong parts), layers, grafts, the
launcher (auth, crash restart, port-squatting refusal) and full MCP loops — all end-to-end tests
run on an **isolated** FreeCAD (port 23499, private home), never on your live instance. A skipped
test is reported as SKIP, never as a pass. The vendored base also keeps its own ~2,100 unit tests
(`pytest tests/unit` in `server/freecad-mcp`).

## Capabilities and maturity
**Stable** = covered by exactness tests (known values + traps). **Beta** = tested end to end, limits
documented. **Experimental** = works on the tested cases only.

| Area | Capability | Maturity |
|---|---|---|
| Reliability | Geometry verdicts (stale recompute, self-intersection, interference), design contract, part analysis (holes, walls), project memory, checkpoints, autosave/recovery | **Stable** |
| Workflow | Several projects side by side (`project_init` never overwrites, `project_switch`); verified recipes (a design is kept only when its contract is OK) | **Beta** |
| Core modeling | PartDesign (sketch, Pad/Pocket, real holes in the Body), spreadsheet variants | **Stable** |
| Fabrication | STL/3MF/STEP/IGES export only after `check` OK, the file read back and compared with the solid · FDM printability (bed, walls, overhangs) · mesh repair | **Stable** |
| Documentation | Bill of materials (real parts, links, CSV) | **Stable** |
| Visual check | `render_views`: shaded front/top/right/iso views in one image, headless, on request | **Beta** |
| 2D plans | TechDraw views, auto ISO scale, page-fit check, overall dims → DXF (headless) | **Beta** (PDF needs the GUI) |
| Mechanism | Assemblies (placement), spur gears, fits, linkage kinematics (slider-crank, four-bar) | **Beta** |
| Structural FEM | `fem_static`: CalculiX linear static, displacement, von Mises, safety factor, convergence check (matches F·L³/3EI within 0.3 %) | **Beta** (linear elastic only) |
| Analysis | Analytical drone sizing · rocket CP/stability (Barrowman) | **Experimental** |
| Grafts | Rocket WB · AirPlaneDesign (headless core only) · CROSS URDF/xacro + ROS 2 package, ros2_control/sensor/Gazebo tags | **Experimental** |
| CAM / G-code | — | **Removed**: the former layer produced unsafe G-code. Export STEP and program toolpaths in a CAM tool you control. |
| Inverse kinematics | — | **Removed**: needs Pinocchio, unavailable on Windows. Use MoveIt with the exported URDF. |
| CFD | — | Not included: export for an external OpenFOAM case (see `skill-drone`). |

Domain knowledge lives in [`skills/`](skills), loaded on demand.

## Documentation
- [`docs/AUDIT_2026-10-11.md`](docs/AUDIT_2026-10-11.md) — full audit, test results, repair plan.
- [`SECURITY.md`](SECURITY.md) — trust model, auth, anti-squatting, supply chain.
- [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) — version risks and upgrade checklist.
- [`docs/CAHIER_DES_CHARGES.md`](docs/CAHIER_DES_CHARGES.md) — original specification (French).

## Repository layout
- `server/freecad_layers/` — first-party layers · `server/layers_mcp/` — the `freecad-layers` MCP server
- `server/launcher/` — supervised launcher · `server/freecad-mcp/` — base server (vendored git subtree, LGPL, modified)
- `skills/` — domain knowledge · `install/` — installer + tests · `addons/` — grafts (cloned, gitignored)
- `project_state/`, `checkpoints/`, `recovery/` — runtime data (gitignored)

## License
[PolyForm Noncommercial 1.0.0](LICENSE) © 2026 NikeTheBee — free to **use, modify and share**
for any noncommercial purpose; **selling it or any commercial use** needs written permission. Keep
the `Required Notice` of [`NOTICE`](NOTICE) and the credits in [`CREDITS.md`](CREDITS.md).
Exception: `server/freecad-mcp/` (blwfish/freecad-mcp, modified) stays **LGPL-2.1-or-later**.
FreeCAD and the grafted workbenches keep their own licences (LGPL-2.1, not included here).
