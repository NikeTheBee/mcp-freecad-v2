# MCP FREECAD

[![Tests](https://github.com/NikeTheBee/mcp-freecad/actions/workflows/tests.yml/badge.svg)](https://github.com/NikeTheBee/mcp-freecad/actions/workflows/tests.yml)
[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC_BY--NC_4.0-lightgrey.svg)](LICENSE)
[![FreeCAD 1.1](https://img.shields.io/badge/FreeCAD-1.1.x-red.svg)](https://www.freecad.org)
🇫🇷 [Version française](README.fr.md)

Design mechanical, aero & robotics parts in **FreeCAD** using **natural language**, through any AI
client that speaks **MCP** — with a reliability layer that **measures** the result instead of
trusting the AI's reading of it.

> Describe intent ("a 60×40×8 mm plate with a Ø6 through-hole in the centre"); the AI models it,
> records your requirements as checks, verifies geometry and requirements after every step,
> autosaves, and lets you roll back.

## Project goal: local-first AI CAD
The end goal is CAD driven by **local** AI models. The architecture is built on **MCP**, an open
protocol, so any MCP-capable agent can drive it. Frontier models (e.g. Claude Code) are used as the
reference client in the meantime.

## How it works
```
You (natural language)
  → AI client (Claude Code / Claude Desktop / any MCP client)
    ├─ `freecad`         launcher (starts/supervises FreeCAD, refuses a hijacked port)
    │                      → blwfish/freecad-mcp bridge (pinned v8.2.3, unmodified, ~39 tools)
    └─ `freecad-layers`  13 reliability tools: project_status · require · check · analyze ·
                          check_assembly · printability · record_step · checkpoint · recover ·
                          bill_of_materials · drawing_sheet · pd_hole · project_init
         → FreeCAD 1.1 headless on localhost:23456 (token auth) + domain workbench grafts
```
- **The code decides, not the AI.** `check` returns `OK`, `FAIL` or `UNKNOWN` (a check that could
  not run is never reported as OK), plus kernel measurements and the user's requirements
  (bounding box, holes and their positions, mass, wall thickness, interferences).
- **Nothing is lost.** Project memory survives sessions and concurrent writers; autosave copies
  after each validated step; checkpoints reload from disk; FreeCAD is restarted at once after a
  crash.
- **Token-aware.** The reliability tools cost ~2,000 tokens of schema; results are short text
  verdicts; no screenshots by default.

## Install (one command, Windows)
```
git clone https://github.com/NikeTheBee/mcp-freecad "MCP FREECAD" && cd "MCP FREECAD"
python install/bootstrap.py --with-grafts
```
Needs **FreeCAD 1.1.x**, **Python 3.10+**, **git**, and an MCP client. The installer pins every
third-party component to a commit, creates a dedicated venv (`~/.freecad-mcp/venv`, nothing goes
into your system Python), registers both MCP servers, and runs the tests. It stops on the first
error and never overwrites an unreadable Claude Desktop config. Re-running it is safe.
Linux/macOS: the pieces are portable but untested — contributions welcome.

## Verify
```
python install/run_all_tests.py
```
~33 tests: the audit's traps (verdicts that used to pass on wrong parts), layers, grafts, the
launcher (auth, crash restart, port-squatting refusal) and full MCP loops — all end-to-end tests
run on an **isolated** FreeCAD (port 23499, private home), never on your live instance. A skipped
test is reported as SKIP, never as a pass.

## Capabilities and maturity
**Stable** = covered by exactness tests (known values + traps). **Beta** = tested end to end, limits
documented. **Experimental** = works on the tested cases only.

| Area | Capability | Maturity |
|---|---|---|
| Reliability | Geometry verdicts (stale recompute, self-intersection, interference), design contract, part analysis (holes, walls), project memory, checkpoints, autosave/recovery | **Stable** |
| Core modeling | PartDesign (sketch, Pad/Pocket, real holes in the Body), spreadsheet variants | **Stable** |
| Fabrication | STL gated on watertight + FDM printability (bed, walls, overhangs) · STEP/IGES · mesh repair | **Stable** |
| Documentation | Bill of materials (real parts, links, CSV) | **Stable** |
| 2D plans | TechDraw views, auto ISO scale, page-fit check, overall dims → DXF (headless) | **Beta** (PDF needs the GUI) |
| Mechanism | Assemblies (placement), spur gears, fits, linkage kinematics (slider-crank, four-bar) | **Beta** |
| Analysis | FEM CalculiX (study setup; solving is not automated) · analytical drone sizing · rocket CP/stability (Barrowman) | **Experimental** |
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
- `server/launcher/` — supervised launcher · `server/freecad-mcp/` — pinned base (cloned, gitignored)
- `skills/` — domain knowledge · `install/` — installer + tests · `addons/` — grafts (cloned, gitignored)
- `project_state/`, `checkpoints/`, `recovery/` — runtime data (gitignored)

## License
[CC BY-NC 4.0](LICENSE) © 2026 NikeTheBee — free to **download, modify and share**, with
**attribution** (NikeTheBee and the projects in [`CREDITS.md`](CREDITS.md)) and **no commercial use**
without written permission. Summary: [`NOTICE`](NOTICE). FreeCAD and the grafted workbenches keep
their own licenses (LGPL-2.1, not vendored here).
