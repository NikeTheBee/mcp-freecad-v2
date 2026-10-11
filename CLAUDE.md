# MCP FREECAD

AI-piloted FreeCAD (natural language → parametric CAD/robotics), via MCP, on Windows.

## Stack
- FreeCAD 1.1.1 at `A:\FreeCAD\bin\` (`freecad.exe` GUI, `freecadcmd.exe` headless, bundled Python 3.11).
- MCP base: `blwfish/freecad-mcp` **pinned** (v8.2.3, SHA in `install/bootstrap.py`), cloned into
  `/server/freecad-mcp` and run **unmodified** (no patches).
  - Entry point registered as user-scoped MCP server `freecad`: the supervised launcher
    `server/launcher/mcp_freecad_launch.py`, run by the dedicated venv `~/.freecad-mcp/venv`
    (mcp 2.x, psutil — nothing in the system Python). See `claude mcp get freecad`.
  - FreeCAD-side addon `AICopilot`: headless server started from the pinned clone; a copy is
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
   → launcher (supervision, anti-squatting) → upstream bridge (pinned, unmodified)
   → FreeCAD headless on localhost:23456 (token auth from upstream)
   → AICopilot workbench executes the operation inside FreeCAD
```

## Token-minimal golden rules
- Text-only feedback by default. No automatic screenshots — only on explicit request or an ambiguous verification failure.
- Read project memory at session start: `state.summary()` (see `skill-verify`) before asking the user to re-describe context.
- Update project state after each *validated* step, not before: `state.record_feature(...)`.
- Verify geometry before continuing: `verify.verdict(obj)` must say `OK` (`FAIL` = fix it, `UNKNOWN` = not verified, never treat as OK); compare its bbox/volume with what the user asked. Gate STL export on `verify.watertight(obj)`; check assemblies with `verify.check_assembly([...])`.
- Checkpoint before risky/large ops: `checkpoint.save("name")`; roll back with `checkpoint.restore("name", replace_active=True)`. After each validated step: `recovery.autosave()` (after a crash: `recovery.open_latest(doc)`).
- Prefer the bridge's named tools over ad hoc Python execution for recurring operations.

## Anti-patterns
- Don't take screenshots by default.
- Don't re-describe the scene/history that's already recoverable from project state or the live FreeCAD document.
- Don't add greffes/domains all at once — one workbench/capability at a time, validated before the next (see `@docs/CAHIER_DES_CHARGES.md` §11).

## Pointers
- Full spec: `@docs/CAHIER_DES_CHARGES.md` · Compatibility audit: `@docs/COMPATIBILITY.md` · Security: `@SECURITY.md`
- Domain knowledge: `/skills/<domain>/SKILL.md` — load on demand. Available: `skill-partdesign` (core sketch/Pad/Pocket + spreadsheet variants), `skill-rocket` (Rocketry WB), `skill-drone` (AirPlaneDesign WB), `skill-verify` (home-grown layers), `skill-print3d` (STL export), `skill-fem` (CalculiX FEM), `skill-exchange` (STEP/IGES), `skill-assembly` (multi-part assemblies), `skill-gear` (involute spur gears), `skill-robotics-ros` (CROSS URDF export + ros2_control/Gazebo + IK), `skill-cfd` (analytical aero + external-OpenFOAM fallback), `skill-cam` (DISABLED — never emit machine G-code; read it if CNC is requested), `skill-techdraw` (2D plans -> DXF headless), `skill-kinematics` (linkage motion laws), `skill-fasteners-bom` (ISO fasteners + nomenclature CSV).
- Home-grown layers (§7): `server/freecad_layers/` — `state` (project memory), `verify` (geometry checks), `checkpoint` (rollback), `recovery` (autosave), `aero`, `drawing`, `kinematics`, `bom`, `robotics`. Any NEW file-writing code MUST use the shared secure-by-design gates `safe_out_path`/`safe_under` (SECURITY.md).
- Project memory data: `/project_state/state.json` · Checkpoints: `/checkpoints/*.FCStd` (runtime artifacts, gitignored)
