# Changes made to freecad-mcp in MCP FREECAD 2

This folder is a modified copy of [freecad-mcp](https://github.com/blwfish/freecad-mcp) by
Brian L Wong, **v8.2.3** (commit `00732265e0aa70ef6c85bd465a3049262e6fdb89`), imported into
[MCP FREECAD 2](https://github.com/NikeTheBee/mcp-freecad-v2) as a squashed git subtree.
It stays under its original licence, the **GNU LGPL v2.1 or later** ([LICENSE](LICENSE)); the
PolyForm Noncommercial licence of the surrounding project does **not** apply to this folder.

Upstream updates are pulled with:

```bash
git subtree pull --prefix=server/freecad-mcp https://github.com/blwfish/freecad-mcp main --squash
```

## 2026-10-12 — NikeTheBee

| File | Change | Why |
|---|---|---|
| `AICopilot/handlers/partdesign_ops.py` | `hole_wizard` without `face_index` on an object inside a PartDesign Body now adds a `PartDesign::SubtractiveCylinder` **inside the Body** (through-hole, or blind from the top face). Counterbore/countersink there now ask for `face_index`. | The old path built a `Part::Cut` outside the Body: the Body, and every feature added after it, silently kept no hole. |
| same | Standalone (non-Body) CSG holes are anchored on the part's own `BoundBox.ZMin` instead of a fixed `z = -2.5`. | A part not sitting at z = 0 got a hole that missed it, or only partly cut it. |
| `AICopilot/handlers/sketch_ops.py` | `verify_sketch` reads the sketch's `DoF` and its Conflicting / Redundant / PartiallyRedundant / Malformed constraint lists. `solve()` is only used as a status code (`< 0` = failure). The verdict is `VALID` only when DoF = 0 and the profile is closed. | `solve()` returns 0 for "solved", which was read as "fully constrained": a free 8-DoF profile was reported "Fully constrained: Yes / VALID". |
| `AICopilot/freecad_mcp_handler.py` | `send_message`: an oversized reply (over `MAX_MESSAGE_SIZE`) is replaced by a short JSON error `{"error": "Result too large ...", "result_too_large": true}`. | Before, nothing was sent: the bridge waited about 120 s and then reported a misleading timeout. |
| `tests/unit/test_partdesign_ops.py` | The test that asserted the old outside-the-Body hole was replaced by 3 tests of the new behaviour. | It encoded the bug. |
| `tests/unit/test_sketch_ops.py` | New `TestVerifySketchConstraintVerdict` (4 tests). | Covers the DoF fix. |
| `tests/unit/test_freecad_mcp_handler.py` | New `test_oversized_reply_becomes_explicit_error` (uses `socketpair`, so it also runs on Windows). | Covers the oversized-reply fix. |
| `tests/unit/conftest.py` | The unit tests run with `HOME`/`USERPROFILE` pointed at a throwaway folder. | On Windows some tests ran `start_server`'s real Windows branch and overwrote `~/.freecad-mcp/windows_auth_token`, which locked every client out of the user's live FreeCAD. They also wrote logs and crash files into the real home. |
| `CHANGES-NikeTheBee.md` | This file. |

## 2026-10-12 (later) — NikeTheBee

| File | Change | Why |
|---|---|---|
| `mcp_bridge_framing.py`, `AICopilot/freecad_mcp_handler.py`, `freecad_mcp_server.py` | **Listener proof.** On a fresh Windows connection the client sends `{"auth_challenge": nonce}`, and FreeCAD answers `{"auth_proof": HMAC-SHA256(token, nonce)}` (`server_proof`, same function on both sides). The bridge sends the token and the command only if that proof matches (`verify_listener`); otherwise it returns "Not sent: ...". Plain token requests are still accepted, so older clients keep working. | The token only proved the client: any process holding the port received the token and every command, and could forge results. |
| `AICopilot/crash_watcher.py`, `freecad_crash_report.py`, `AICopilot/freecad_debug.py`, `AICopilot/freecad_health.py`, `AICopilot/handlers/diagnostics_ops.py`, `freecad_mcp_server.py` | Crash, op-log and debug files go to `tempfile.gettempdir()` on Windows (`/tmp` unchanged on POSIX). `O_NOFOLLOW` goes through `getattr(os, "O_NOFOLLOW", 0)`, and `os.fchmod` is called only where it exists (also in `AICopilot/instance_registry.py`). | `"/tmp"` meant `<current drive>:\tmp` on Windows, so FreeCAD and the bridge wrote to different folders. The op-log write failed on every call (`os.O_NOFOLLOW` missing), so the crash diagnosis never worked on Windows. |
| `AICopilot/freecad_debug.py` | The rotating debug log is opened as UTF-8. | cp1252 could not write the ✓/✗ health lines: "--- Logging error ---" tracebacks. |
| `freecad_mcp_server.py` | stderr uses `errors="backslashreplace"`; stdout, the MCP channel, is untouched. | Same failure on a cp1252 console. |
| `tests/unit/conftest.py` | The sandbox home also gets a fake token. A fixture makes the bridge trust the mocked FreeCAD in the protocol tests; the proof itself is tested directly. | The bridge's Windows-path tests depended on the user's real token file. |
| `tests/unit/test_crash_report.py` | The last-op test path uses the module's temp dir. | Follows the path change. |
| `tests/unit/test_freecad_mcp_handler.py`, `tests/unit/test_bridge_framing.py` | New `TestListenerProof` (5 tests) and `TestVerifyListener` (2 tests). | Cover the listener proof, including "a squatter receives only the nonce". |

Upstream unit tests on Windows after these changes: 2118 passed, 0 new failures. 9 tests that
failed on the unmodified v8.2.3 under Windows now pass (instance registry writes). LGPL §2(a): modified files must carry a notice of the changes and their date. |

These fixes are also checked live against a real FreeCAD by `install/base_fixes_test.py` in the
parent project.

Upstream unit tests, on Windows, after these changes: 2102 passed, 0 new failures. The 132
failures and 39 errors are already present on the unmodified v8.2.3: those tests use AF_UNIX
sockets, `/tmp` and POSIX paths.
`tests/unit/test_headless_server.py::TestMainStartupSuccess` hangs on Windows and must be
deselected.
