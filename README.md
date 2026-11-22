# MCP FREECAD 2

[![Tests](https://github.com/NikeTheBee/mcp-freecad-v2/actions/workflows/tests.yml/badge.svg)](https://github.com/NikeTheBee/mcp-freecad-v2/actions/workflows/tests.yml)
[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm_Noncommercial_1.0.0-lightgrey.svg)](LICENSE)
[![FreeCAD 1.1](https://img.shields.io/badge/FreeCAD-1.1.x-red.svg)](https://www.freecad.org)

[Version française](README.fr.md)

An engineering workshop you drive in plain language. FreeCAD does the parametric CAD, a set of
free and open-source solvers does the physics, and an MCP server connects both to whatever AI
client you use. Every answer is a measurement with a verdict (OK, FAIL or UNKNOWN), not the
model's opinion of its own work.

You describe a part or a whole project ("a 60 x 40 x 8 mm plate with a 6 mm through hole in the
middle", "a self-balancing robot that carries 500 g"). The agent models it, writes your
requirements down as checks, verifies the geometry after every step, runs the simulations, and
stops at the fabrication files. The real build and test stay yours; your measurements then
calibrate the models.

## What it covers

| Area | What you get |
|---|---|
| CAD | PartDesign, sketches, real holes, assemblies and joints, spreadsheet variants, standard parts (ISO screws, nuts, washers), TechDraw plans to DXF, bill of materials and assembly notes |
| Checks | geometry verdicts, the design contract (size, holes, mass, wall, interference), printability, manufacturability, checkpoints and crash recovery |
| Structures | CalculiX static, modal, buckling, thermal and thermo-mechanical; fatigue; composite laminates; crack growth |
| Fluids | SU2, Elmer, OpenFOAM 12 (serial or MPI on your cores), airfoils, wings, nozzles, combustion |
| Aero and space | rocket flight (RocketPy, OpenRocket), flutter and divergence, POGO, OpenVSP, orbits and GMAT, trajectory optimisation (dymos), jet engine cycles (pyCycle) |
| Electronics | SPICE, batteries, magnets, wire sizing, power budget, PCB traces / vias / impedance, netlist ERC, WireViz harnesses, KiCad 10 DRC / Gerber / STEP |
| Robotics | multibody dynamics (MuJoCo, Pinocchio), control, sensors (Allan noise, ADC, Kalman), DC motors, ArduPilot SITL, Gazebo, ROS 2 |
| AI and firmware | neural networks for control and decisions (supervised and reinforcement learning, ONNX export), STM32 firmware in Renode, and that firmware closing the loop on the simulated robot |
| Marine, ground | hydrostatics, thrusters, pressure hulls, O-ring glands, groundwater and soil (OpenGeoSys) |

There are 29 adapters and about 170 reference cases. A simulation kind only runs once its
reference cases pass (NAFEMS-style benchmarks, closed forms, published values), and it is checked
again whenever its code or its locked environment changes.

## How it fits together

```
you, in plain language
  -> any MCP client (CLI harnesses, Cursor, VS Code, Codex, Gemini CLI, LM Studio, Continue...)
     |- freecad          the launcher supervises a headless FreeCAD (localhost only, token auth)
     |                   and serves the modeling tools of the vendored freecad-mcp base
     '- freecad-layers   checks, contract, export, simulate, study, skills
          -> the workshop runner starts each solver in its own locked environment
```

Two things keep the token bill low. Tool results are short text verdicts with a few numbers (the
full logs stay on disk). The tool schemas are compact: the whole surface costs about 15,000
tokens at session start, and a test fails the build if it grows past 15,500. The domain
knowledge sits in `skills/` and is read only when a task needs it, through a `skill` tool, MCP
resources, or a prompt, so it works the same in every client.

## Install (Windows)

```
git clone https://github.com/NikeTheBee/mcp-freecad-v2 "MCP FREECAD" && cd "MCP FREECAD"
python install/bootstrap.py --with-grafts
powershell -ExecutionPolicy Bypass -File install\install.ps1 -Home D:\Atelier -Packs tout -Scan
```

`bootstrap.py` sets up the MCP side: a private venv, both servers registered, the tests run. It
stops on the first error and never overwrites a client config it cannot read.

`install.ps1` builds the workshop under the folder you pick (`-Home`), with pinned and SHA256
checked downloads and locked pixi environments. A few behaviours are worth knowing:

- `-Packs` picks the domains: socle, fluides, fluides-plus, opt, air, fusee, espace, terre,
  soussol, marin, elec, embarque, fab, outillage, ros, ia, or `tout` for all of them.
- `-Scan` first asks for permission, then reads the CPU, RAM, GPU and free disk space. The
  downloads and the default compute profile follow from that. For example, the CUDA build of the
  AI environment is added only for an NVIDIA card with a recent enough driver. Nothing leaves the
  machine.
- Before downloading anything, the installer looks for what is already there, on every drive.
  A copy of the same version is offered for reuse; another version is only reported. Packages
  shared between environments are stored once.
- Before anything is fetched, it shows the total size and asks you to confirm.

For a client other than the one bootstrap finds:
`python install/mcp_clients.py --client cursor --write`. The other clients are vscode, codex,
gemini, lmstudio, continue, windsurf and desktop-harness. Without `--write` it only prints the
configuration.

## Compute: CPU, GPU or both

Any simulation accepts `device`: `cpu`, `gpu`, or `both` (the network trains on the GPU while the
physics runs on the CPU cores). The default comes from the hardware scan; with no scan, everything
runs on the CPU. OpenFOAM takes `cpus` for MPI ranks, and CalculiX uses the profile's threads.

## Safety rules the code enforces

- FreeCAD and every solver listen on localhost only; a process that opens anything else is
  stopped.
- No machine G-code is ever written. Firmware runs in an emulator, and a trained policy leaves as
  an ONNX file. Flashing a board, ordering parts or moving a machine stays a human action.
- Supplied files that could run code (OpenFOAM dictionaries that compile C++, pickled models,
  MJCF includes, KiCad plugins) are refused.
- Tool output is treated as data, never as instructions to the agent.

The details are in [SECURITY.md](SECURITY.md) and [docs/AUDIT_CYBER_2026-11-21.md](docs/AUDIT_CYBER_2026-11-21.md).

## Tests

```
python install/run_all_tests.py
```

There are 58 tests. End-to-end tests use an isolated FreeCAD (port 23499, private home), never
your live one. They cover:
- the traps found in the October audit;
- the layers and the launcher;
- full MCP loops from a bare client;
- the reference cases of the fast adapters;
- the cyber gate and the token budget.

A skipped test says SKIP, never PASS. The vendored base keeps its own unit tests
(`pytest tests/unit` in `server/freecad-mcp`).

## Documentation

- [AGENTS.md](AGENTS.md): the rules any agent follows, and a map of the repository.
- [docs/FEUILLE_DE_ROUTE_ATELIER.md](docs/FEUILLE_DE_ROUTE_ATELIER.md): workshop roadmap, tools and status (French).
- [SECURITY.md](SECURITY.md): trust model, supply chain, what is not protected.
- [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md): version risks and the upgrade checklist.
- [CHANGELOG.md](CHANGELOG.md): history before the workshop.
- `skills/<domain>/SKILL.md`: parameters, limits and validation of each domain.

## License

[PolyForm Noncommercial 1.0.0](LICENSE), © 2026 NikeTheBee. You may use, modify and share it for
any noncommercial purpose; selling it or any commercial use needs written permission. Keep the
`Required Notice` of [NOTICE](NOTICE) and the credits in [CREDITS.md](CREDITS.md).

There are two exceptions:
- `server/freecad-mcp/` (blwfish/freecad-mcp, modified) stays LGPL-2.1-or-later.
- FreeCAD, the grafted workbenches and every workshop tool keep their own licences. They are
  downloaded by the installer, not shipped here.
