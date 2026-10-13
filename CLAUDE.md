# MCP FREECAD 2

AI-piloted FreeCAD (natural language → parametric CAD/robotics), via MCP, on Windows.

## Stack
- FreeCAD 1.1.1 at `A:\FreeCAD\bin\` (`freecad.exe` GUI, `freecadcmd.exe` headless, bundled Python 3.11).
- MCP base: `blwfish/freecad-mcp` v8.3.0, **vendored and modified** in `/server/freecad-mcp`
  (git subtree, stays LGPL). Edit it like our own code, log every change in
  `server/freecad-mcp/CHANGES-NikeTheBee.md`, and pull upstream with
  `python install/bootstrap.py --update-base --base-ref <tag>`. Its unit tests: `pytest tests/unit` from that folder
  (venv Python; deselect `test_headless_server.py::TestMainStartupSuccess`, it hangs on Windows;
  ~132 POSIX-only failures pre-exist — compare against the previous run, never expect 0).
  - Entry point registered as user-scoped MCP server `freecad`: the supervised launcher
    `server/launcher/mcp_freecad_launch.py`, run by the dedicated venv `~/.freecad-mcp/venv`
    (mcp 2.x, psutil — nothing in the system Python). See `claude mcp get freecad`.
  - FreeCAD-side addon `AICopilot`: headless server started from `/server/freecad-mcp`; a copy is
    installed in `%APPDATA%\FreeCAD\v1-1\Mod` for GUI use.
  - `FREECAD_MCP_FREECAD_BIN` (in the server's launch env) points at `A:\FreeCAD\bin\freecadcmd.exe`.

## Key commands
- Start FreeCAD GUI: `A:\FreeCAD\bin\freecad.exe`
- Run a script headless: `A:\FreeCAD\bin\freecadcmd.exe <script.py>`
- Check MCP server health: `claude mcp list` / `claude mcp get freecad`

## FreeCAD startup is automatic
The launcher starts FreeCAD headless on `localhost:23456` when needed, restarts it at once if it
dies, and refuses to run if a non-FreeCAD process holds the port (anti-squatting). Just call
`check_freecad_connection` at session start; expect a one-time ~5–30 s cold start. Never launch
FreeCAD manually. After a crash: `recovery.open_latest(<doc>)` (see `skill-verify`).
Logs: `~/.freecad-mcp/logs/launcher.log`. The base's `spawn_freecad_instance` is Unix-only.
- Re-register / full install: `python install/bootstrap.py [--with-grafts]` (idempotent; stops on any error).
- Run all tests: `python install/run_all_tests.py` (layers, traps, grafts, launcher, MCP loop on an isolated test instance).

## Architecture
```
User (natural language) → Claude Code → MCP `freecad` server (stdio)
   → launcher (supervision, anti-squatting) → base bridge (vendored, see CHANGES-NikeTheBee.md)
   → FreeCAD headless on localhost:23456 (token auth + listener proof: FreeCAD proves itself first)
   → AICopilot workbench executes the operation inside FreeCAD
```

## Golden rules (reliability first, then tokens)
Two MCP servers: `freecad` (modeling tools of the base) and `freecad-layers` (this project's
reliability tools — prefer them over `execute_python`).
- Session start: `project_status` (memory, contract, recovery copies, projects) before asking the user to re-describe anything.
- One project per design: `project_init(name)` creates a NEW project (never overwrites);
  `project_switch(name)` reopens one. A similar part already done? `recipe list` / `recipe show`
  and replay its steps (each still checked). When a part's contract is OK: `recipe save`.
- When the user states dimensions/requirements, record them once with `require(target, kind, params)`
  (bbox, holes, hole_at, mass, min_wall, no_interference...). They are re-checked automatically.
- After each modeling step: `check(target)` must say `OK` on the geometry AND the contract.
  `FAIL` = fix it; `UNKNOWN` = not verified — never treat as OK. A valid solid can still be the wrong part.
- After a validated step: `record_step(...)` (memory + autosave). Before a risky step: `checkpoint save`;
  roll back with `checkpoint restore`. After a FreeCAD crash: `recover`.
- Holes in a PartDesign Body: `pd_hole` (fixed in v2, the base's `partdesign_operations hole` now also cuts inside the Body).
- One rollback system: `freecad-layers` `checkpoint` — not the base's `view_control checkpoint/rollback_to_checkpoint`.
- Sketches: `check(sketch)` before Pad/Pocket (the base's `verify_sketch` now reports DoF/conflicts too since v2).
- Fabrication outputs: STL only after `printability`/`check` OK; **never G-code** (`skill-cam`).
- "Will it hold?": `fem_static` (CalculiX) with the user's supports, loads and material — never guess
  a strength; report the safety factor and its limits (linear elastic only, see `skill-fem`).
- Text-only feedback by default. Pictures only on request or for a complex part/assembly at a milestone
  when measurements alone can't settle it: `render_views` (front/top/right/iso in one image, headless).

## Anti-patterns
- Don't take screenshots by default.
- Don't re-describe the scene/history that's already recoverable from project state or the live FreeCAD document.
- Don't add greffes/domains all at once — one workbench/capability at a time, validated before the next (see `@docs/CAHIER_DES_CHARGES.md` §11).

## Pointers
- Full spec: `@docs/CAHIER_DES_CHARGES.md` · Compatibility audit: `@docs/COMPATIBILITY.md` · Security: `@SECURITY.md`
- Domain knowledge: `/skills/<domain>/SKILL.md` — load on demand. Available: `skill-verify` (reliability layers & tools), `skill-partdesign` (sketch/Pad/Pocket/holes + spreadsheet variants), `skill-assembly` (multi-part assemblies, fits), `skill-print3d` (STL export), `skill-exchange` (STEP/IGES), `skill-techdraw` (2D plans -> DXF), `skill-bom` (nomenclature), `skill-fem` (CalculiX setup), `skill-gear` (involute spur gears), `skill-kinematics` (linkage motion laws), `skill-rocket` (Rocketry WB), `skill-drone` (AirPlaneDesign WB + analytical aero + external CFD), `skill-robotics-ros` (CROSS URDF export + ros2_control/Gazebo), `skill-cam` (DISABLED — never emit machine G-code; read it if CNC is requested).
- Home-grown layers (§7): `server/freecad_layers/` — `state` (project memory), `spec` (design contract), `verify` (geometry checks), `analysis` (holes, walls, printability), `fem` (CalculiX static stress), `checkpoint` (rollback), `recovery` (autosave), `projects` (several designs), `recipes` (verified, reusable), `render` (views), `bom`, `drawing`, `kinematics`, `aero`, `urdf_aug`; exposed as MCP tools by `server/layers_mcp/`. Any NEW file-writing code MUST use the shared gates `safe_out_path`/`safe_under` (SECURITY.md).
- Project memory data: `/project_state/state.json` · Checkpoints: `/checkpoints/` · Autosaves: `/recovery/`
  for the default project; named projects under `/projects/<name>/`; recipes in `/recipes/` (all gitignored)
- Audit & repair plan: `@docs/AUDIT_2026-10-11.md` (§8 final verdict, §9 feature list).
