# MCP FREECAD

[![Tests](https://github.com/NikeTheBee/mcp-freecad/actions/workflows/tests.yml/badge.svg)](https://github.com/NikeTheBee/mcp-freecad/actions/workflows/tests.yml)
[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC_BY--NC_4.0-lightgrey.svg)](LICENSE)
[![FreeCAD 1.1](https://img.shields.io/badge/FreeCAD-1.1.x-red.svg)](https://www.freecad.org)
🇫🇷 [Version française](README.fr.md)

Design mechanical, aero & robotics projects in **FreeCAD** using **natural language** — no manual coding — through any AI client that speaks **MCP**. Built for minimal token cost and long, robust design sessions.

> Describe intent ("a 450mm quad frame with foldable arms", "a 3-fin rocket with an ogive nose"); the AI models it, verifies the geometry, checkpoints progress, and prepares it for fabrication.

## Project goal: local-first AI CAD
The end goal of this project is **CAD modeling driven by LOCAL AI models** — everything running on
your own machine, no cloud dependency. That is why the architecture is built on **MCP**, an open
protocol: any MCP-capable agent runner with a local LLM can drive it, today or tomorrow.
Frontier models (e.g. Claude Code) are currently ahead of local models on long tool-use sessions,
so they are used **in the meantime** as the reference client — but nothing here is tied to them.
When local models catch up, you swap the client and keep the whole stack (server, skills, layers).

## How it works
```
You (natural language)
  → AI client (Claude Code / any MCP client)
    → freecad MCP server  (base: blwfish/freecad-mcp, ~32 tools)
      → FreeCAD 1.1.1  (+ domain workbench grafts)
        → home-grown layers: project memory · geometry verification · checkpoints
```
- **Token-minimal:** text-only feedback by default (no auto-screenshots), compact tool results, a
  short `CLAUDE.md` loaded each session, and heavy domain knowledge kept in on-demand **skills**.
- **Robust:** OCCT crash capture in the base, plus first-party verification + checkpoint/rollback layers.

## Prerequisites
- **FreeCAD 1.1.x** · **Python 3.10+** (for the MCP bridge) · **git** · an MCP client (e.g. Claude Code).
- Current target OS: **Windows** (uses TCP `localhost:23456`); the architecture is cross-platform — see
  [`docs/CAHIER_DES_CHARGES.md`](docs/CAHIER_DES_CHARGES.md).

## Install (one command)
```
git clone <repo> "MCP FREECAD" && cd "MCP FREECAD"
python install/bootstrap.py --with-grafts        # add --client both for Claude Code + Desktop
```
`bootstrap.py` auto-detects FreeCAD, resolves its real version-specific Mod dir, installs the base
server + workbench + bridge, registers with your MCP client(s), activates grafts, and runs the tests.
It is idempotent and cross-platform. Manual/explained steps: [`install/AGENT-INSTALL.md`](install/AGENT-INSTALL.md);
as-built reference log: [`install/WINDOWS-SETUP.md`](install/WINDOWS-SETUP.md).

> **What "plug-and-play" can and can't mean:** FreeCAD must be installed, and the AI client must speak
> **MCP** (Claude Code, Claude Desktop, any MCP client). A bare local LLM needs an MCP-capable agent
> runner. CFD needs OpenFOAM; ROS2 simulation needs a ROS2 runtime. CAM/G-code is disabled (see below).

## Verify
```
python install/run_all_tests.py    # grafts + layers + Phase 1 socket + MCP-protocol loop (if :23456 live)
```
The MCP-protocol loop test ([`install/mcp_loop_test.py`](install/mcp_loop_test.py)) drives the bridge as a
real MCP client and proves the full client→bridge→FreeCAD path end-to-end.

## Example
> "Create a 10×20×30mm box."

The AI calls the `freecad` tools, FreeCAD builds the parametric box headless, and the AI reports the
volume / bounding box in plain text — no manual modeling, no screenshot on the happy path.

## Capabilities
| Area | Capability | Status |
|---|---|---|
| Core | Parametric modeling: sketch · Pad/Pocket · spreadsheet-driven variants (loop proven) | ✅ |
| Robustness | Project memory · geometric verification · checkpoints/rollback | ✅ |
| Security | localhost-only RPC · **shared-token socket auth** · no-secrets scan · trust model ([SECURITY.md](SECURITY.md)) | ✅ |
| Onboarding | One-command cross-platform installer; Claude Code + Desktop; FreeCAD auto-started by the bridge (zero manual steps) | ✅ |
| Rocketry | Rocket WB graft — nose/body/fins · Barrowman CP & stability | ✅ |
| Aero | AirPlaneDesign graft — NACA airfoils, fuselage profiles | ✅ |
| Fabrication | STL (watertight-gated) · mesh repair · STEP/IGES exchange | ✅ |
| Analysis | FEM (CalculiX, ships with FreeCAD) · drone sizing (momentum theory: T/W, hover power, endurance) | ✅ setup |
| Memory | Resume a project across sessions from state alone — runnable proof: [examples/resume_between_sessions.py](examples/resume_between_sessions.py) | ✅ |
| Mechanism | Assemblies · spur gears · fillet/chamfer/holes/patterns · fits & tolerances · linkage kinematics (slider-crank/four-bar) | ✅ |
| 2D plans | TechDraw views + dimensions → DXF (headless) | ✅ |
| CAM | G-code generation | ❌ **removed** — the former drilling layer produced unsafe G-code ([audit §2.2](docs/AUDIT_2026-10-11.md)); export STEP and program toolpaths in a CAM tool you control |
| Docs | Bill of materials / nomenclature → CSV | ✅ |
| Robotics | URDF/xacro export + full ament_cmake ROS2 package (CROSS graft, no ROS2 needed) · ros2_control/sensors/Gazebo-Sim tags + controllers YAML | ✅ |
| CFD · Gazebo sim | OpenFOAM · ROS2 simulation | needs external runtimes |

Domain knowledge lives in [`skills/`](skills) — `skill-partdesign`, `skill-rocket`, `skill-drone`,
`skill-print3d`, `skill-exchange`, `skill-fem`, `skill-assembly`, `skill-gear`, `skill-robotics-ros`,
`skill-verify`, `skill-cfd` (external-OpenFOAM fallback), `skill-cam` (disabled: safety guidance only),
`skill-techdraw` (2D plans), `skill-kinematics` (linkage motion), `skill-fasteners-bom` — loaded on
demand to keep token cost low. The full suite (`python install/run_all_tests.py`) is **30/30** and
also runs as a core subset in CI ([.github/workflows/tests.yml](.github/workflows/tests.yml)).

## Repository layout
- `CLAUDE.md` — short, loaded each session · `docs/` — full spec (cahier des charges)
- `server/freecad_layers/` — first-party memory/verify/checkpoint layers · `server/freecad-mcp/` — base (gitignored)
- `addons/` — third-party workbench grafts (gitignored) · `skills/` — domain knowledge
- `install/` — install guides + test suite · `project_state/`, `checkpoints/` — runtime data (gitignored)

## License
[CC BY-NC 4.0](LICENSE) © 2026 NikeTheBee — everyone is free to **download, modify and share** this
project, under two conditions: **attribution is mandatory** (credit NikeTheBee AND the third-party
projects listed in [`CREDITS.md`](CREDITS.md)), and **no commercial use** without prior written
permission. Human-readable summary: [`NOTICE`](NOTICE). FreeCAD and the grafted workbenches keep
their own licenses (LGPL-2.1, not vendored here).
