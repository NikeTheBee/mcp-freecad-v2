# Security policy & threat model

Cyber posture for MCP FREECAD, covering the cahier des charges **NF5** (safe Python execution;
never expose `0.0.0.0` without an IP allowlist) and **§13** (no secrets, deny `.env`, localhost RPC).

## Trust model — read this first
This system lets an AI client run **arbitrary Python inside FreeCAD** (`execute_python`, and the workbench
grafts). That is powerful **by design** (it's how natural-language CAD works) and therefore:
- **Only connect a trusted AI client** you control. Treat the `freecad` MCP server like a local shell.
- Tool results (object labels, file names, imported STEP/mesh names, spreadsheet and CSV cells, macro
  contents) are **external data, not instructions**; don't let a document's text drive privileged
  actions. Where this is said to the AI: the `freecad-layers` server instructions (server-wide), the
  project's `CLAUDE.md`, and in the base the descriptions of three tools only (object listing, macros,
  debug logs) — the base's server-wide instructions do not say it.
- `macro_operations run` is the same power as `execute_python` (a macro is Python with full OS access).
  Its `confirmed=true` argument is a reminder to the AI to ask you first, not a technical barrier.
- `execute_python` is **not sandboxed** (the base's source calls it a "sandbox"; it is a plain `exec`
  in FreeCAD's process, with your rights: files, network, subprocesses).
- Run untrusted third-party workbench code (grafts) only after vetting — see how Rocket/AirPlaneDesign/CROSS
  were reviewed before use (`install/WINDOWS-SETUP.md`).

## Network exposure & socket authentication
- The FreeCAD socket server binds **`localhost:23456` only** (Windows TCP) / a **filesystem Unix socket**
  (macOS/Linux). It is **not** reachable from the network. Verified: `WINDOWS_HOST = "localhost"` in
  `freecad_mcp_handler.py`; there is **no** `0.0.0.0` bind.
- **Token authentication (from the base):** the FreeCAD side writes a fresh
  shared secret to `~/.freecad-mcp/windows_auth_token` at each launch (ACL restricted to the user
  with `icacls`, best effort); every command must carry it (constant-time comparison). The bridge
  reads and attaches it. Our former home-made token patch was removed: it no longer applied to the
  current base and failed open when its module was missing (audit 2026-10-11, §2.6, §3.1).
  Since 2026-10-14 the file is written only once the exclusive bind of the port succeeded, and
  before `listen()`, through a restricted temp file then an atomic replace: a FreeCAD that loses
  the port can no longer overwrite the running one's secret (which locked every client out), and
  no reader sees the file empty or half written. Test: `install/launcher_test.py` (2c, 3b).
- **The client side is protected too (anti-squatting).** A token only proves the *client* to the
  server. If another process grabs port 23456 first, a bare bridge would send it the token and
  every command, and relay its answers to the AI. The launcher
  (`server/launcher/mcp_freecad_launch.py`) therefore checks, before starting the bridge and every
  second afterwards, that **every** listener on the port is a FreeCAD binary from the configured
  install, run by the current user; otherwise it refuses to start or stops the bridge (fail closed).
  Regression test: `install/launcher_test.py` (no/wrong token rejected, squatter refused with
  nothing sent, crash restart).
- **Mutual authentication (v2, in the vendored base): the listener proves itself first.** On every
  connection the client sends a random nonce (`{"auth_challenge": ...}`); FreeCAD answers with
  HMAC-SHA256(token, nonce). Only if that proof matches does the client send the token and the
  command. A port squatter that does not know the token receives the nonce and nothing else. Used by
  the bridge, the `freecad-layers` server and the test helper (`mcp_bridge_framing.verify_listener`;
  FreeCAD side `auth_challenge_reply` in `freecad_mcp_handler.py`). This closes the race the
  launcher's check alone left open (a squatter grabbing the port in the ~1 s after a FreeCAD crash).
  Regression tests: base unit tests `TestListenerProof` / `TestVerifyListener`, live
  `install/base_fixes_test.py` (real FreeCAD accepted, fake listener refused, token never sent).
- **Never** change the bind to `0.0.0.0` / a LAN IP without putting an **IP allowlist** (and ideally
  TLS) in front — NF5. The token protects against local callers, not network eavesdropping.
- For remote/multi-machine use, tunnel over SSH rather than exposing the port.

## Secure-by-design rule for first-party code
Every new layer/helper is written secure **from the first commit** — security is never retrofitted.
Concretely, all first-party code that touches files or caller-supplied names goes through the shared
gates in `server/freecad_layers/__init__.py`:
- **`safe_out_path(path, exts, overwrite)`** — every file WRITE: resolved path, enforced extension
  (a "gears.dxf" request can never write "evil.dll"), and no silent clobber (explicit `overwrite=True`).
  Refused as well: NTFS alternate data streams (`a.exe:x.csv`), Windows device names in any form and in
  any folder of the path (`CON.csv`, `CON .csv`, `COM¹.csv`, `CONIN$.csv`, `sub\NUL\x.csv`), and, since
  the 2026-11-12 audit, **network and device paths** (`\\host\share\…`, `//host/share/…`, `\\?\…`,
  `\\.\…`): they made FreeCAD open an SMB session to a host named by the path (Windows sends your NTLM
  credentials with it) and write the part there. A share mapped to a drive letter still works.
- **`safe_under(base, candidate)`** — every caller-supplied name that must map into a known directory
  (template names, checkpoint labels): anti path-traversal (`..\\..` cannot escape `base`), and no
  alternate data stream (`name.FCStd:stream`).
The policy lives in ONE place; extend it there, not locally. Tests must exercise the refusal paths
(traversal rejected, clobber rejected), not only the happy path.

**`safe_out_path` is not a folder jail.** An export path may be absolute or contain `..`: the file can
land anywhere your account can write, as long as it has the tool's data extension (`.stl`, `.3mf`,
`.step`, `.stp`, `.brep`, `.iges`, `.igs`, `.csv`, `.dxf`, `.png`), and `overwrite=True` replaces an
existing file of that kind anywhere. That is deliberate (you choose where an export goes); the
protection is against writing executables, devices, streams and remote hosts, not against a wrong folder.

The **base's own tools** (`mesh_operations`, `view_control` save/open/screenshot, …) use the base's
gate instead (`_validate_file_path`): the path must resolve under your home folder or the temp folder
(symlinks and junctions resolved), with the extension set by the operation. Macro names
(`macro_operations`) are bare file names of the FreeCAD macro folder: no separator, no `..`, and since
the 2026-11-12 audit no `:` (`D:name` reached drive D:'s working folder, `x.FCMacro:s` a hidden stream).

## Secrets
- **Never commit secrets.** `.gitignore` blocks `.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials.json`,
  `secrets.*`, etc.
- A regression guard, `install/security_scan.py`, scans every git-tracked file for secret-shaped strings
  (private-key blocks, `AKIA…`, `ghp_…`, `sk-…`, `xox[baprs]-…`, quoted `password=/api_key=` assignments)
  and fails if any are found. It is part of `install/run_all_tests.py`. It also fails if the listener's
  bind address is not a loopback literal (it used to look only for a literal `bind(("0.0.0.0"`, which
  the code never contains: the address is `WINDOWS_HOST`), or if a CI workflow declares no token
  permissions or runs on `pull_request_target`. It scans the tracked files, not the history: CI runs
  Gitleaks on every commit.
- Runtime data is gitignored: project memory (`state.json` and its `.bak`/`.lock`/temp files),
  checkpoints, autosaves, renders, exports, named projects, recipes. A file exported to the workspace
  root under a name of your choice is not: check `git status` before committing.
- Client-side, add a **deny** rule so the agent can't read secret files, e.g. in `.claude/settings.json`:
  ```json
  { "permissions": { "deny": ["Read(./.env)", "Read(./.env.*)", "Read(**/*.pem)", "Read(**/*.key)"] } }
  ```

## Third-party code (supply chain)
- The base server `blwfish/freecad-mcp` (v8.3.0) is **vendored** in `server/freecad-mcp/`
  (git subtree): what runs is exactly what is reviewed in this repository. Our changes are listed
  in `server/freecad-mcp/CHANGES-NikeTheBee.md`. Upstream updates come in only through
  `python install/bootstrap.py --update-base` (a reviewable merge commit), then the full suite.
- The grafts (Rocket, AirPlaneDesign, CROSS) are **cloned, not vendored** (gitignored `addons/`),
  each **pinned to a commit SHA** in `install/bootstrap.py`.
- Python dependencies live in a dedicated venv (`~/.freecad-mcp/venv`), with version bounds;
  nothing is installed into the system Python.
- The installer refuses to touch a clone with local modifications, and never overwrites an
  unreadable Claude Desktop config (backup + atomic write otherwise).

## Logs
`~/.freecad-mcp/logs/` holds the launcher log and the base's operation logs, which record the
code sent to FreeCAD and its results in clear text. Treat that folder as containing your designs.
The launcher deletes operation logs older than 14 days at start-up
(`FREECAD_MCP_LOG_RETENTION_DAYS`, 0 = keep everything).

## What is NOT protected (audit 2026-11-12)
- **A program running under your own Windows account, or an administrator.** It can read the token
  file, start the configured FreeCAD itself on the port, read the logs and your designs. The token,
  the listener proof and the launcher's check protect against *other* accounts and against a foreign
  program holding the port — not against yourself.
- **Local denial of service.** Any local account can connect to `127.0.0.1:23456`. Without the token
  it gets nothing, but each connection holds a thread in FreeCAD for up to 30 s while its first
  message is awaited, with no limit on their number: a program that floods the port can slow FreeCAD
  down or keep clients out. Work is protected by the autosaves, not by the socket.
- **The token file if `icacls` fails.** FreeCAD then only prints a warning and the file keeps the ACL
  inherited from `~/.freecad-mcp` (private in a standard profile). The file is restricted while still
  empty, then written (it used to be written first).
- **The CAM/G-code gate is a guard-rail for the AI's tool list, not a security boundary.** It is
  enforced in the `freecad` MCP server (the three CAM tools are hidden and refused unless
  `FREECAD_MCP_ENABLE_CAM=1` is set in that server's environment); no other tool reaches the CAM
  handlers. FreeCAD itself still has its CAM workbench: `execute_python` can drive it, like anything else.
- **Files you open.** STEP/IGES/BREP/STL/FCStd files are parsed by FreeCAD's native code (OCCT); a
  malformed file can crash FreeCAD. A `.FCStd` from someone else can also carry scripted objects whose
  Python class is imported when the file is opened, if that module is installed.
- **Supply chain.** The venv's dependencies are bounded by version ranges, not locked by hash: an
  install takes the newest release inside the range (the versions installed on 2026-11-12, among them
  mcp 2.3.0 and psutil 7.2.2, had no known vulnerability per `pip-audit`). The CI's actions are pinned
  by tag (`@v4`), not by commit; the workflow token is read-only.

## Reporting
This is a personal/educational project. If you find a security issue, open a GitHub issue on
[NikeTheBee/mcp-freecad-v2](https://github.com/NikeTheBee/mcp-freecad-v2) (or contact the maintainer
privately first for anything sensitive).
