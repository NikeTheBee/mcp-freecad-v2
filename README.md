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

## What's new in 2.0
- **The base server is part of the project.** blwfish/freecad-mcp v8.2.3 is vendored in
  `server/freecad-mcp/` (git subtree): fixed here, and still able to pull upstream updates
  (`python install/bootstrap.py --update-base`). Fixed in it: holes in a PartDesign Body, sketch
  "fully constrained" verdict, oversized replies that hung for 2 minutes
  ([`CHANGES-NikeTheBee.md`](server/freecad-mcp/CHANGES-NikeTheBee.md)).
- **Reliability layer** (`freecad-layers`, 15 tools): OK/FAIL/UNKNOWN verdicts, design contract,
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
    │                      → freecad-mcp bridge (blwfish v8.2.3, vendored + fixed, ~39 tools)
    └─ `freecad-layers`  15 reliability tools: project_status · require · check · analyze · fem_static ·
                          render_views · check_assembly · printability · record_step ·
                          checkpoint · recover · bill_of_materials · drawing_sheet · pd_hole ·
                          project_init
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
~33 tests: the audit's traps (verdicts that used to pass on wrong parts), layers, grafts, the
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
| Core modeling | PartDesign (sketch, Pad/Pocket, real holes in the Body), spreadsheet variants | **Stable** |
| Fabrication | STL gated on watertight + FDM printability (bed, walls, overhangs) · STEP/IGES · mesh repair | **Stable** |
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
