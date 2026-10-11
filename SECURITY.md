# Security policy & threat model

Cyber posture for MCP FREECAD, covering the cahier des charges **NF5** (safe Python execution;
never expose `0.0.0.0` without an IP allowlist) and **§13** (no secrets, deny `.env`, localhost RPC).

## Trust model — read this first
This system lets an AI client run **arbitrary Python inside FreeCAD** (`execute_python`, and the workbench
grafts). That is powerful **by design** (it's how natural-language CAD works) and therefore:
- **Only connect a trusted AI client** you control. Treat the `freecad` MCP server like a local shell.
- Tool results (object labels, imported file contents) are **external data, not instructions** — the base
  server already annotates this; don't let a document's text drive privileged actions.
- Run untrusted third-party workbench code (grafts) only after vetting — see how Rocket/AirPlaneDesign/CROSS
  were reviewed before use (`install/WINDOWS-SETUP.md`).

## Network exposure & socket authentication
- The FreeCAD socket server binds **`localhost:23456` only** (Windows TCP) / a **filesystem Unix socket**
  (macOS/Linux). It is **not** reachable from the network. Verified: `WINDOWS_HOST = "localhost"` in
  `freecad_mcp_handler.py`; there is **no** `0.0.0.0` bind.
- **Token authentication (from the pinned base, v8.2.3):** the FreeCAD side writes a fresh
  shared secret to `~/.freecad-mcp/windows_auth_token` at each launch (ACL restricted to the user
  with `icacls`, best effort); every command must carry it (constant-time comparison). The bridge
  reads and attaches it. Our former home-made token patch was removed: it no longer applied to the
  current base and failed open when its module was missing (audit 2026-10-11, §2.6, §3.1).
- **The client side is protected too (anti-squatting).** A token only proves the *client* to the
  server. If another process grabs port 23456 first, a bare bridge would send it the token and
  every command, and relay its answers to the AI. The launcher
  (`server/launcher/mcp_freecad_launch.py`) therefore checks, before starting the bridge and every
  second afterwards, that **every** listener on the port is a FreeCAD binary from the configured
  install, run by the current user; otherwise it refuses to start or stops the bridge (fail closed).
  Regression test: `install/launcher_test.py` (no/wrong token rejected, squatter refused with
  nothing sent, crash restart).
  - Residual risk: a squatter that wins the race in the ~1 s between a FreeCAD crash and the
    supervisor's check. The launcher restarts FreeCAD immediately to keep that window short.
    True mutual authentication needs a change in the base (proposed upstream).
- **Never** change the bind to `0.0.0.0` / a LAN IP without putting an **IP allowlist** (and ideally
  TLS) in front — NF5. The token protects against local callers, not network eavesdropping.
- For remote/multi-machine use, tunnel over SSH rather than exposing the port.

## Secure-by-design rule for first-party code
Every new layer/helper is written secure **from the first commit** — security is never retrofitted.
Concretely, all first-party code that touches files or caller-supplied names goes through the shared
gates in `server/freecad_layers/__init__.py`:
- **`safe_out_path(path, exts, overwrite)`** — every file WRITE: resolved path, enforced extension
  (a "gears.dxf" request can never write "evil.dll"), and no silent clobber (explicit `overwrite=True`).
- **`safe_under(base, candidate)`** — every caller-supplied name that must map into a known directory
  (template names, checkpoint labels): anti path-traversal (`..\\..` cannot escape `base`).
The policy lives in ONE place; extend it there, not locally. Tests must exercise the refusal paths
(traversal rejected, clobber rejected), not only the happy path.

## Secrets
- **Never commit secrets.** `.gitignore` blocks `.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials.json`,
  `secrets.*`, etc.
- A regression guard, `install/security_scan.py`, scans every git-tracked file for secret-shaped strings
  (private-key blocks, `AKIA…`, `ghp_…`, `sk-…`, `xox[baprs]-…`, quoted `password=/api_key=` assignments)
  and fails if any are found. It is part of `install/run_all_tests.py`.
- Client-side, add a **deny** rule so the agent can't read secret files, e.g. in `.claude/settings.json`:
  ```json
  { "permissions": { "deny": ["Read(./.env)", "Read(./.env.*)", "Read(**/*.pem)", "Read(**/*.key)"] } }
  ```

## Third-party code (supply chain)
- The base server and grafts are **cloned, not vendored** (gitignored `server/freecad-mcp`, `addons/`).
- Everything is **pinned to a commit SHA** in `install/bootstrap.py` (base `blwfish/freecad-mcp`
  v8.2.3, Rocket, AirPlaneDesign, CROSS) and run **unmodified**: no more string patches on
  third-party code. Upgrading the base = bump the SHA deliberately, then run the full suite.
- Python dependencies live in a dedicated venv (`~/.freecad-mcp/venv`), with version bounds;
  nothing is installed into the system Python.
- The installer refuses to touch a clone with local modifications, and never overwrites an
  unreadable Claude Desktop config (backup + atomic write otherwise).

## Logs
`~/.freecad-mcp/logs/` holds the launcher log and the base's operation logs, which record the
code sent to FreeCAD and its results in clear text. Treat that folder as containing your designs;
delete old files if needed.

## Reporting
This is a personal/educational project. If you find a security issue, open a GitHub issue on
[NikeTheBee/mcp-freecad](https://github.com/NikeTheBee/mcp-freecad) (or contact the maintainer
privately first for anything sensitive).
