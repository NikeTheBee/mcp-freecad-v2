# AGENT-INSTALL - reproducible setup for a fresh machine

Forward-looking install guide for an AI agent (or a human) to stand up this system from scratch.
`WINDOWS-SETUP.md` is the as-built log for the reference machine; this file is the general recipe.

## Fastest path: one command
```
python install/bootstrap.py --with-grafts            # see --help for flags
```
This automates everything below (detect FreeCAD, resolve the real versioned Mod dir, install base +
workbench + bridge, pip deps, register the client(s), activate grafts, run tests). The manual steps
below are for understanding or when the bootstrap can't run.

## 0. Prerequisites
- **FreeCAD 1.1.x** installed (note the path to `freecadcmd`/`FreeCADCmd` and the bundled `python`).
- **Python 3.10+** on PATH (for the MCP bridge server) with `pip`.
- **git**, and the **`claude`** CLI (for `claude mcp add`). Any MCP client works (see install/mcp_clients.py); the steps below use the `claude` CLI as an example.

> **Find FreeCAD's user dir robustly**: it is version-specific. Run:
> `freecadcmd -c "import FreeCAD; print(FreeCAD.getUserAppDataDir())"`
> The user addon/workbench dir is `<that>/Mod`. On the reference machine that is
> `%APPDATA%\FreeCAD\v1-1\Mod` (NOT the plain `%APPDATA%\FreeCAD\Mod`).

## 1. Clone this repo
```
git clone <this-repo-url> "MCP FREECAD" && cd "MCP FREECAD"
```

## 2. Install the base MCP server (Phase 0)
```
git clone https://github.com/blwfish/freecad-mcp server/freecad-mcp
```
- Copy `server/freecad-mcp/AICopilot/` -> `<FreeCAD user dir>/Mod/AICopilot` (for the GUI workbench /
  auto-load) **and/or** keep the explicit-path headless flow (see §5).
- Copy `server/freecad-mcp/freecad_mcp_server.py` and `mcp_bridge_framing.py` -> `~/.freecad-mcp/`.
- `python -m pip install "mcp>=1.27.2" "mcp-events>=0.1.0"` (use the Python that will run the bridge).

## 3. Register the MCP server
```
claude mcp add -s user freecad -e FREECAD_MCP_FREECAD_BIN="<path>\freecadcmd.exe" -- \
  "<python>" "<home>\.freecad-mcp\freecad_mcp_server.py"
claude mcp get freecad        # expect: Connected
```
- `-e` must come **after** the server name and before `--` (it is variadic).
- The `FREECAD_MCP_FREECAD_BIN` override is required when FreeCAD is not on a standard path.
- Windows uses TCP `localhost:23456` (override `FREECAD_MCP_PORT`); macOS/Linux use a Unix socket.

## 4. Install domain grafts (one at a time - §11)
Each graft is third-party code that runs on FreeCAD startup once activated - vet before activating.
```
git clone --depth 1 https://github.com/davesrocketshop/Rocket addons/Rocket
git clone --depth 1 https://github.com/FredsFactory/FreeCAD_AirPlaneDesign addons/AirPlaneDesign
```
To **activate** a graft for auto-load, copy it into `<FreeCAD user dir>/Mod/<Name>` (exclude `.git`).
Rocket auto-loads cleanly; AirPlaneDesign is GUI-only - use its `libAeroShapes` core on demand
(stub `DraftTools` first). See `skills/skill-rocket` and `skills/skill-drone`.

## 5. Verify
- Start the headless FreeCAD server (or let the bridge spawn it). Reference machine uses
  `.claude/launch.json`'s `freecad-headless` (runs `AICopilot/headless_server.py`, binds `:23456`).
- Run the full test suite:
  ```
  python install/run_all_tests.py
  ```
  Expect the graft + layer tests to PASS; the Phase 1 socket test runs if `:23456` is live.
- In a **fresh** harness session, ask in natural language to create a simple part - the `freecad`
  MCP tools only load at session start.

## Security
- RPC/socket stays on `localhost`; never bind `0.0.0.0` without an IP allowlist.
- Never commit secrets; the client should `deny` reads of `.env`.
- Third-party clones live under gitignored `/server/freecad-mcp` and `/addons`: re-cloned, not vendored.
