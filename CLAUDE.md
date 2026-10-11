# MCP FREECAD

AI-piloted FreeCAD (natural language → parametric CAD/robotics), via MCP, on Windows.

## Stack
- FreeCAD 1.1.1 at `A:\FreeCAD\bin\` (`freecad.exe` GUI, `freecadcmd.exe` headless, bundled Python 3.11).
- MCP base: `blwfish/freecad-mcp` (dev branch), cloned into `/server/freecad-mcp`.
  - Bridge server: `~/.freecad-mcp/freecad_mcp_server.py`, runs under system Python 3.13 (`mcp` package installed there).
  - FreeCAD-side addon: `AICopilot` workbench installed at `%APPDATA%\FreeCAD\Mod\AICopilot`.
  - Registered with Claude Code as user-scoped MCP server `freecad` (see `claude mcp get freecad`).
  - `FREECAD_MCP_FREECAD_BIN` env var (baked into the MCP server's launch env) points at `A:\FreeCAD\bin\freecadcmd.exe` since FreeCAD isn't in a default install location.

## Key commands
- Start FreeCAD GUI: `A:\FreeCAD\bin\freecad.exe`
- Run a script headless: `A:\FreeCAD\bin\freecadcmd.exe <script.py>`
- Check MCP server health: `claude mcp list` / `claude mcp get freecad`

## FreeCAD startup is automatic (R10 fixed)
The bridge auto-starts FreeCAD headless whenever `localhost:23456` is down — on the first tool
call, on `check_freecad_connection`, or via `spawn_freecad_instance` (patch:
`install/apply_bridge_patches.py`, applied by bootstrap). Just call `check_freecad_connection`
at session start; expect a one-time ~10–30 s cold start. Never launch FreeCAD manually.
User-facing fallback if the bridge misbehaves: desktop shortcut "Demarrer FreeCAD pour Claude"
(`start-freecad-server.bat`). Disable autostart with `FREECAD_MCP_AUTOSTART=0`. See
`docs/COMPATIBILITY.md` R10.
- Re-register / full install: `python install/bootstrap.py [--with-grafts]` (idempotent, cross-platform).
- Run all tests: `python install/run_all_tests.py` (grafts, layers, socket smoke, MCP-protocol loop).

## Architecture
```
User (natural language) → Claude Code → MCP `freecad` server (stdio)
   → bridge spawns/talks to FreeCAD (headless by default, GUI on request)
   → AICopilot workbench executes the operation inside FreeCAD
```

## Token-minimal golden rules
- Text-only feedback by default. No automatic screenshots — only on explicit request or an ambiguous verification failure.
- Read project memory at session start: `state.summary()` (see `skill-verify`) before asking the user to re-describe context.
- Update project state after each *validated* step, not before: `state.record_feature(...)`.
- Verify geometry before continuing: `verify.verdict(obj)`; gate STL export on `verify.watertight(obj)`.
- Checkpoint before risky/large ops: `checkpoint.save("name")`; roll back with `checkpoint.restore("name")`.
- Prefer the bridge's named tools over ad hoc Python execution for recurring operations.

## Anti-patterns
- Don't take screenshots by default.
- Don't re-describe the scene/history that's already recoverable from project state or the live FreeCAD document.
- Don't add greffes/domains all at once — one workbench/capability at a time, validated before the next (see `@docs/CAHIER_DES_CHARGES.md` §11).

## Pointers
- Full spec: `@docs/CAHIER_DES_CHARGES.md` · Compatibility audit: `@docs/COMPATIBILITY.md` · Security: `@SECURITY.md`
- Domain knowledge: `/skills/<domain>/SKILL.md` — load on demand. Available: `skill-partdesign` (core sketch/Pad/Pocket + spreadsheet variants), `skill-rocket` (Rocketry WB), `skill-drone` (AirPlaneDesign WB), `skill-verify` (home-grown layers), `skill-print3d` (STL export), `skill-fem` (CalculiX FEM), `skill-exchange` (STEP/IGES), `skill-assembly` (multi-part assemblies), `skill-gear` (involute spur gears), `skill-robotics-ros` (CROSS URDF export + ros2_control/Gazebo + IK), `skill-cfd` (analytical aero + external-OpenFOAM fallback), `skill-cam` (DISABLED — never emit machine G-code; read it if CNC is requested), `skill-techdraw` (2D plans -> DXF headless), `skill-kinematics` (linkage motion laws), `skill-fasteners-bom` (ISO fasteners + nomenclature CSV).
- Home-grown layers (§7): `server/freecad_layers/` — `state` (project memory), `verify` (geometry checks), `checkpoint` (rollback), `aero`, `drawing`, `kinematics`, `bom`, `robotics`. Any NEW file-writing code MUST use the shared secure-by-design gates `safe_out_path`/`safe_under` (SECURITY.md).
- Project memory data: `/project_state/state.json` · Checkpoints: `/checkpoints/*.FCStd` (runtime artifacts, gitignored)
