# Changes made to freecad-mcp in MCP FREECAD 2

This folder is a modified copy of [freecad-mcp](https://github.com/blwfish/freecad-mcp) by
Brian L Wong, imported into [MCP FREECAD 2](https://github.com/NikeTheBee/mcp-freecad-v2) as a
squashed git subtree.

| Upstream version | Commit | Imported |
|---|---|---|
| v8.2.3 | `00732265e0aa70ef6c85bd465a3049262e6fdb89` | 2026-10-12 |
| **v8.3.0** (current) | tag `v8.3.0`, commit `9c577f81c881407245dad37f771ecbf13df8b1d6` | 2026-10-13 |

It stays under its original licence, the **GNU LGPL v2.1 or later** ([LICENSE](LICENSE)). The
PolyForm Noncommercial licence of the surrounding project does **not** apply to this folder.
LGPL §2(a) requires every modified file to carry a notice of the changes and their date: that is
this file.

Pull upstream updates, keeping our changes, with:

```bash
python install/bootstrap.py --update-base --base-ref v8.3.0
```

It runs `git subtree pull --squash` on a clean tree. Then run the base's unit tests and
`install/run_all_tests.py`, and add a section below.

## 2026-10-12 — NikeTheBee

| File | Change | Why |
|---|---|---|
| `AICopilot/handlers/partdesign_ops.py` | `hole_wizard` without `face_index` on an object inside a PartDesign Body now adds a `PartDesign::SubtractiveCylinder` **inside the Body**: a through-hole, or a blind hole from the top face. Counterbore and countersink holes there now ask for `face_index`. | The old path built a `Part::Cut` outside the Body. The Body, and every feature added after it, silently kept no hole. |
| same | Standalone (non-Body) CSG holes are anchored on the part's own `BoundBox.ZMin` instead of a fixed `z = -2.5`. | A part not sitting at z = 0 got a hole that missed it, or only partly cut it. |
| `AICopilot/handlers/sketch_ops.py` | `verify_sketch` reads the sketch's `DoF` and its Conflicting, Redundant, PartiallyRedundant and Malformed constraint lists. `solve()` is used only as a status code (`< 0` means failure). The verdict is `VALID` only when DoF = 0 and the profile is closed. | `solve()` returns 0 for "solved", and that 0 was read as "fully constrained": a free 8-DoF profile was reported "Fully constrained: Yes / VALID". |
| `AICopilot/freecad_mcp_handler.py` | `send_message` replaces an oversized reply (over `MAX_MESSAGE_SIZE`) with a short JSON error, `{"error": "Result too large ...", "result_too_large": true}`. | Before, nothing was sent: the bridge waited about 120 s and then reported a misleading timeout. |
| `tests/unit/test_partdesign_ops.py` | The test that asserted the old outside-the-Body hole is replaced by 3 tests of the new behaviour. | That test encoded the bug. |
| `tests/unit/test_sketch_ops.py` | New `TestVerifySketchConstraintVerdict` (4 tests). | Covers the DoF fix. |
| `tests/unit/test_freecad_mcp_handler.py` | New `test_oversized_reply_becomes_explicit_error`. It uses `socketpair`, so it also runs on Windows. | Covers the oversized-reply fix. |
| `tests/unit/conftest.py` | The unit tests run with `HOME`/`USERPROFILE` pointed at a throwaway folder. | On Windows some tests ran `start_server`'s real Windows branch and overwrote `~/.freecad-mcp/windows_auth_token`, which locked every client out of the user's live FreeCAD. They also wrote logs and crash files into the real home. |

## 2026-10-12 (later) — NikeTheBee

| File | Change | Why |
|---|---|---|
| `mcp_bridge_framing.py`, `AICopilot/freecad_mcp_handler.py`, `freecad_mcp_server.py` | **Listener proof.** On a fresh Windows connection the client sends `{"auth_challenge": nonce}`, and FreeCAD answers `{"auth_proof": HMAC-SHA256(token, nonce)}` (`server_proof`, the same function on both sides). The bridge sends the token and the command only if that proof matches (`verify_listener`); otherwise it returns "Not sent: ...". Plain token requests are still accepted, so older clients keep working. | The token proved only the client. Any process holding the port received the token and every command, and could forge results. |
| `AICopilot/crash_watcher.py`, `freecad_crash_report.py`, `AICopilot/freecad_debug.py`, `AICopilot/freecad_health.py`, `AICopilot/handlers/diagnostics_ops.py`, `freecad_mcp_server.py` | On Windows, crash, op-log and debug files go to `tempfile.gettempdir()`; POSIX keeps `/tmp`. `O_NOFOLLOW` goes through `getattr(os, "O_NOFOLLOW", 0)`, and `os.fchmod` is called only where it exists (also in `AICopilot/instance_registry.py`). | On Windows `"/tmp"` meant `<current drive>:\tmp`, so FreeCAD and the bridge wrote to different folders. The op-log write failed on every call (`os.O_NOFOLLOW` missing), so the crash diagnosis never worked on Windows. |
| `AICopilot/freecad_debug.py` | The rotating debug log is opened as UTF-8. | cp1252 could not write the ✓/✗ health lines, which raised "--- Logging error ---" tracebacks. |
| `freecad_mcp_server.py` | stderr uses `errors="backslashreplace"`. stdout, the MCP channel, is untouched. | The same failure, on a cp1252 console. |
| `tests/unit/conftest.py` | The sandbox home also gets a fake token. A fixture makes the bridge trust the mocked FreeCAD in the protocol tests; the proof itself is tested directly. | The bridge's Windows-path tests depended on the user's real token file. |
| `tests/unit/test_crash_report.py` | The last-op test path uses the module's temp dir. | Follows the path change. |
| `tests/unit/test_freecad_mcp_handler.py`, `tests/unit/test_bridge_framing.py` | New `TestListenerProof` (5 tests) and `TestVerifyListener` (2 tests). | Cover the listener proof, including "a squatter receives only the nonce". |

## 2026-10-13 — NikeTheBee: upstream v8.3.0 merged

- `git subtree pull` merged v8.3.0 automatically, with no textual conflict. Upstream's v8.3.0
  changes:
  - screenshots handed over by file path and returned as MCP image content;
  - `execute_python` runs code with `__name__ == "__main__"` and reports a result only when the
    call set it;
  - sketches diagnose isolated vertices;
  - a preference turns off the dedicated-tool tip.
- Every change listed above is still in place after the merge.

| File | Change | Why |
|---|---|---|
| `tests/unit/test_sketch_isolated_vertices.py` | The `TestVerifySketchSection` fixture sets `DoF = 0`. | That new upstream test expects `VALID` from a mocked sketch without a DoF. With our DoF fix, `VALID` requires a fully constrained sketch, and that is what the fixture stands for. |

## 2026-10-13 (later) — NikeTheBee

| File | Change | Why |
|---|---|---|
| `freecad_mcp_server.py` | **CAM tools hidden and refused by default** (`cam_operations`, `cam_tools`, `cam_tool_controllers`). They come back with `FREECAD_MCP_ENABLE_CAM=1`. | They produce machine G-code, which this project never emits (its former CAM layer produced unsafe G-code). The rule is now enforced in code, not only in instructions. |
| same | **On Windows, `spawn_freecad_instance`, `select_freecad_instance` and `stop_freecad_instance` are hidden and refused**, with a message pointing to the launcher. "FreeCAD not running" also points to the launcher log instead of `spawn_freecad_instance`. | They cannot work there: there is one FreeCAD (TCP port, one token file), supervised by `server/launcher`. Together with the CAM tools, that is 39 → 33 tools, about 2,200 schema tokens saved per session. |
| same | `check_for_update` adds `how_to_update` (`bootstrap.py --update-base --base-ref <tag>`). | Installing the upstream release over this copy would drop these changes. |
| `tests/unit/conftest.py` | Upstream tests run with `FREECAD_MCP_ENABLE_CAM=1`. | They exercise the CAM tools. |
| `tests/unit/test_mcp_protocol.py` | New `TestCamDisabledByDefault` (3 tests) and `TestUnixOnlyToolsHiddenOnWindows` (2 tests). | Cover the defaults. |
| `tests/unit/test_version_check.py` | Two exact-dict assertions also check `how_to_update`. | Follows the new key. |
| `AICopilot/handlers/view_ops.py` | On a headless FreeCAD, `set_view`, `fit_all`, `zoom_in` and `zoom_out` say that there is no 3D view. The screenshot error adds the same hint. Both point to the `freecad-layers` tool `render_views`. | They crashed with "'NoneType' object has no attribute 'ActiveDocument'" (`FreeCADGui` is `None` headless), which told the AI nothing. |
| `tests/unit/test_view_ops_screenshot.py` | New `TestHeadlessViewOps` (5 tests). | Covers it. |
| `AICopilot/handlers/view_ops.py` | Headless `hide_object`/`show_object` set the document's own `Visibility`. Headless `get_report_view` reads the console log that the launcher keeps (`~/.freecad-mcp/logs/freecad_headless.log`), with the same `tail` and `filter` arguments. | They raised "'NoneType' object has no attribute ..."; a full sweep of the 190 operations found them. |
| `AICopilot/handlers/partdesign_ops.py`, `freecad_mcp_server.py` | `fillet` and `chamfer` take `edges` (now in the schema) as 1-based indices or names like `"Edge3"` (`edge_indices`); `auto_select_all` is in the schema too. On a headless FreeCAD without edges, they return a clear message instead of an interactive selection request that can never complete. | The tool schema had no way to name edges, so a headless fillet or chamfer was impossible through MCP. Passing `"Edge1"` also raised a raw `TypeError`. |
| `AICopilot/handlers/partdesign_ops.py` | `Part::Chamfer` edges are `(edge, size1, size2)` triples (was `(edge, size)`). | Every non-Body chamfer, including the GUI selection path and `auto_select_all`, failed with "tuple index out of range". |
| `AICopilot/handlers/sketch_ops.py`, `freecad_mcp_server.py` | `add_circle`, `add_polygon` and `add_slot` also accept `center_x`/`center_y`, and `add_arc` also accepts `x`/`y`; explicit names win. The schema descriptions say so. | The schema called the arc's centre `center_x`/`center_y`, and the circle's `x`/`y`. A circle given `center_x=10` was silently drawn at (0,0), giving a wrong part, and the reply said "center (0,0)". Found by the real-geometry test (`install/base_exactness_test.py`). |
| `AICopilot/freecad_mcp_handler.py` | **Ignored arguments are reported.** Every operation routed through `_call_on_gui_thread_async` receives a `TrackedArgs` dict that records the keys it reads. A key it never reads is appended to the reply: `(Note: argument(s) not used by <op>: ...)`, or `"ignored_args": [...]` for JSON or dict replies, errors included. Bulk access (iteration, copy, `**`) counts as reading everything, so a used argument is never reported as ignored. | A misnamed argument (`centre_x`, an arc's `center_x` on a circle, `units`...) used to be dropped silently. The operation then "succeeded" on the wrong part. |
| `AICopilot/handlers/draft_ops.py`, `freecad_mcp_server.py` | Draft `array` reads the schema's `count` and `spacing` (X direction). `count_x/y/z` and `interval_x/y/z` are in the schema now and win when given. | The schema exposed only `count` and `spacing`, which the code never read, so every array through MCP came out 2 copies, 100 mm apart. Found thanks to the ignored-argument note. |
| `AICopilot/handlers/assembly_ops.py` | `ground_part`, `create_joint` and `get_part_status` accept the part's own name (`A`) and map it to its component (`A001`), when exactly one component links to it. If several do, the error names them. | `add_component` links a part in as `A001`. Every following operation then refused `A` with "Add it first with add_component", although it had just been added. |
| `AICopilot/handlers/draft_ops.py` | Draft `text` applies `size` as the font size when a 3D view exists; headless, it says the size could not be applied. | The schema described `size` as "Text size in mm", but it was never used. |
| `AICopilot/handlers/varset_ops.py` | `add_property` applies an optional initial `value`, through `set_property`. | The value was ignored, so binding the new property right away set the bound dimension to 0, giving an invalid part. |
| tests | `TestEdgeIndices`, `TestHeadlessFilletChamfer`, and 2 more headless view tests. Two upstream "awaiting selection" tests now set `GuiUp = True`, since they model the GUI. | Coverage; the live check is in `install/base_fixes_test.py`. |

## Test status

Upstream unit tests on Windows, compared with the unmodified upstream version in a sandboxed home:

| Version | Passed | New failures | Fixed on Windows |
|---|---|---|---|
| v8.2.3 + changes | 2118 | 0 | 9 (instance registry writes) |
| v8.3.0 + changes | 2191 | 0 | 9 |

- The remaining ~123 failures and 39 errors also exist on the unmodified upstream version: those
  tests use AF_UNIX sockets, `/tmp` and POSIX paths. On Linux, CI runs them all green.
- `tests/unit/test_headless_server.py::TestMainStartupSuccess` hangs on Windows and must be
  deselected.

These fixes are also checked live against a real FreeCAD by `install/base_fixes_test.py` in the
parent project. `install/base_exactness_test.py` compares the base's geometry operations with
known values: booleans, move, scale, spreadsheet-driven parameters, measurements, interference,
distance and clearance. It is the guard to run after every upstream update (34/34 project tests, including the Windows CI job `windows-full`).
