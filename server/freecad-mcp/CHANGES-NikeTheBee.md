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

## 2026-10-13 (evening) — NikeTheBee

| File | Change | Why |
|---|---|---|
| `AICopilot/freecad_mcp_handler.py` | **Headless async jobs run on their own thread** (still under `_headless_exec_lock`): the submission answers at once with the job id, the bridge polls. A job cancelled before it starts is skipped, as in the GUI path. `_headless_async_inline = True` restores the inline behaviour (set by the unit-test fixture). | Headless jobs ran inline on the submitting socket thread, so the submission only answered when the job was DONE. The bridge waits 35 s for that answer: any operation longer than that came back as a crash report ("FreeCAD process: not found — crashed and exited") while FreeCAD was still computing, and the next call waited behind it. |
| same | The lock-free `view_control` operations (`create_document`, `list_objects`, `get_object_properties`, `checkpoint`) take the headless lock. A job holding it for more than 25 s gets a plain "FreeCAD is busy with a long operation (job ...)" reply. The lock is now reentrant (`RLock`). | They ran beside a job computing on its own thread, so a `checkpoint` could save a document mid-change (FreeCAD is not thread-safe). `create_document` calls `_run_on_gui_thread` itself, so it takes the lock again on the same thread: a plain `Lock` deadlocked there. |
| `freecad_mcp_server.py`, `AICopilot/freecad_mcp_handler.py` | **A job lost in a FreeCAD restart is reported as lost.** The bridge's poll loop turns "Unknown job_id" (for a job it submitted and has not collected) into "FreeCAD stopped while <op> was running and was restarted: that operation and any unsaved changes are lost". `poll_job`/`cancel_job` on an unknown id also say how long this FreeCAD has been running. | The supervisor restarts a dead FreeCAD within seconds. The new one did not know the job and answered "Already retrieved or never submitted", which read as success. |
| `AICopilot/handlers/diagnostics_ops.py` | **`restart_freecad` works headless when the parent project's launcher manages FreeCAD** (`MCP_FREECAD_MANAGED=1`, set by `server/launcher`). If no job holds FreeCAD, it saves the documents that have a file and copies the untitled ones to `%TEMP%/freecad_mcp_restart/`. If a job holds it, it saves nothing and says why. Then it replies and exits; the launcher starts a fresh FreeCAD, which the bridge waits for. Unmanaged headless keeps the upstream refusal. | It answered "not available in headless mode", so a headless FreeCAD stuck in an OCCT call that never returns had no way out: `cancel_job` cannot interrupt it, and only killing the process by hand helped. |
| `freecad_crash_report.py` | `_fc_process_info` finds FreeCAD on Windows (psutil, else `tasklist`): `freecad.exe` or the headless `freecadcmd.exe`. | It used `pgrep`, which does not exist on Windows, so every timeout said "crashed and exited". |
| tests | `TestHeadlessAsyncThread` (answers before the job ends, jobs still serialised, errors recorded, cancel before start, busy read, no deadlock on a nested call, unknown job uptime), `TestRestartManagedHeadless`; the autosave surface gate registers `_restart_managed_headless` (`doc.save`, `doc.saveCopy`), `TestWindowsProcessInfo`. The `server` fixture sets `_headless_async_inline = True` for the upstream tests that inspect a job right after submitting it. | Live checks: `install/mcp_loop_test.py` (a 40 s operation through the real bridge returns its result) `install/base_fixes_test.py` (a read during a 30 s job says busy, then works), and `mcp_loop_test` again (FreeCAD killed mid-operation: reported as lost; `restart_freecad` headless: copied, restarted, usable). |

## 2026-10-14 — NikeTheBee

| File | Change | Why |
|---|---|---|
| `AICopilot/handlers/base.py` | `BaseHandler._exact_bbox(shape)`: `optimalBoundingBox(False, False)` (AddOptimal), falling back to `BoundBox` for meshes and mocks. | `Shape.BoundBox` bounds the control points or the triangulation of curved faces: a 30 x 20 loft reads 37.3 x 26.8, a tilted torus 8 % too big, and a revolved B-spline SMALLER than the part. AddOptimal measures the surfaces at the same speed (0.003 s on a plate with 1,600 holes). |
| `AICopilot/handlers/measurement_ops.py` (`get_bounding_box`), `document_ops.py` (insert report), `mesh_ops.py` (import report) | Report the exact box. | The size the AI reads to check a dimension was wrong on lofts, sweeps and fillets on tilted parts. |
| `AICopilot/handlers/spatial_ops.py` | `batch_interference` pre-filters on exact boxes (computed once per object); `interference_check`, `containment` and `alignment_check` use them too. | A too-small box made the pre-filter skip a pair that really collides: a revolved B-spline overlapping a block by 0.004 mm3 was reported "No collisions". |
| tests | `install/base_exactness_test.py` (parent project): `get_bounding_box` on a loft says 30.00 x 20.00; `batch_interference` finds the hidden collision. | Unit tests: same 162 POSIX-only failures as before, no new one. |
| `AICopilot/handlers/partdesign_ops.py` (`_body_simple_hole`) | A blind hole starts at the part's surface AT (x, y), found by a vertical section of the Body in its own frame; "through" when the depth reaches the bottom there; "no material at (x, y)" refused before any feature is made. | It started from the top of the whole Body, measured in the GLOBAL frame: on a stepped part or an L-bracket a 3 mm hole began in the air and cut nothing (or came out too short), and a moved Body put it at the wrong height. |
| tests | `test_partdesign_ops`: the surface under (x, y), no material; `install/base_fixes_test.py`: stepped Body, the hole removes exactly pi x 2^2 x 3 mm3. | |
| `AICopilot/handlers/partdesign_ops.py` (`hole_wizard`, CSG path for an object outside a Body) | Same rule as in a Body: depths from the surface at (x, y), through when the depth reaches the bottom there; the counterbore is on the top face; the countersink cone flares from the hole up to the surface (carried 1 mm above it). The reply says "through" or the depth and its start. | The cylinder was anchored on the part's BOTTOM (a blind depth counted from below, 2.5 mm too deep), the counterbore sat on the bottom face, and the countersink cone lay entirely under the part: it cut nothing. Unit tests only checked which object types were added. |
| tests | `test_partdesign_ops`: placements of the blind hole, counterbore and countersink; `install/base_exactness_test.py`: volumes removed from a 20 x 20 x 10 box are exactly 36 pi (blind 4 mm), 198 pi (counterbore), 126 pi (countersink). | |
| `AICopilot/handlers/partdesign_ops.py` (`revolution` in a Body, `groove`, `helix` in a Body) | `_sketch_axis_ref`: the `axis` letter names a BODY axis (as it does for patterns and the standalone revolution) and maps to the sketch's H_Axis or V_Axis that runs along it. The sketch's normal is refused with the axes its plane holds; a tilted sketch is refused. | The letter named the SKETCH's own axes (x = H, y = V): on an XZ sketch "y" revolved around the body's Z while the reply said "around Y-axis", and "z" (the axis the user meant) was refused. On XY sketches nothing changes. |
| `AICopilot/handlers/primitives.py` | Cone and torus replies say `R1=5, R2=2`. | They said `R15, R22` (R1 then the value, no separator): read as a radius of 15. |
| tests | `test_partdesign_ops`: XZ sketch, "z" -> V_Axis, "y" refused, groove "x" -> H_Axis; `test_primitives`: `R1=5`; `install/base_exactness_test.py`: revolution and groove of an XZ ring around Z, exact volumes. | |
| `AICopilot/handlers/partdesign_ops.py` (fillet/chamfer `auto_select_all`, `shell`) | On a feature of a Body, or the Body itself (= its Tip), "all edges" makes a `PartDesign::Fillet`/`Chamfer` INSIDE the Body, as explicit `edges` already did; `shell` makes a `PartDesign::Thickness` inside it. `_dressup_target` maps the Body to its Tip for every dress-up. | They made a `Part::Fillet`/`Part::Chamfer`/`Part::Thickness` BESIDE the Body: the Body (what gets checked, exported, drilled next) kept 500 mm3 while the reply said "all 12 edges". |
| same, `freecad_mcp_server.py` schema | `thickness`, `shell` and `draft` take `faces=[6, "Face6"]` (or `face_index`), validated against the part; headless without them, a plain "needs faces=[...]" message. | Headless they returned an "awaiting selection" request asking to select faces in a FreeCAD window that does not exist. |
| tests | `test_partdesign_ops`: all-edges fillet/chamfer inside the Body (feature and Body), faces param, shell in a Body, headless message; `install/base_exactness_test.py`: fillet 479.879, chamfer 455.333 (equal to the Part API), thickness 244 mm3. | |
| `AICopilot/handlers/partdesign_ops.py` (`loft`, `sweep`) | Sketches in a Body: `PartDesign::AdditiveLoft` (Profile = first, Sections = the rest) / `PartDesign::AdditivePipe` INSIDE the Body, like `subtractive_loft` and `additive_pipe` already were. Outside a Body: `Part::Loft` / `Part::Sweep` as before. | They always made a `Part::Loft` / `Part::Sweep` beside the Body: the Body stayed empty (its Shape invalid) while the reply said "Created loft". |
| `AICopilot/handlers/partdesign_ops.py` (`rib`) | `direction='normal'` (the default) is the sketch's own normal; a direction lying in the sketch's plane is refused; a Body's sketch gets a `PartDesign::Pad` inside the Body. | "normal" was the global Y axis: an XY sketch was swept within its own plane, a solid of ZERO volume replied as "Created rib ... 3mm thickness" ("horizontal" likewise). |
| tests | `test_partdesign_ops` (rib: normal of XY and XZ sketches, in-plane refused, Body pad); `install/base_exactness_test.py`: rib 120 mm3, in-plane refused. | |
| `AICopilot/handlers/part_ops.py` (`extrude`, `revolve`) | The whole profile: every closed wire, inner loops cut out, in any order (`Part.makeFace(wires, "Part::FaceMakerBullseye")`). `extrude` without `direction` goes along the sketch's normal (reply says which axis, with its sign); a direction lying in the sketch's plane is refused. | They used `Wires[0]` alone: a plate sketch extruded without its holes, or as a mere cylinder when a hole's circle came first. A fixed default "z" lay in the plane of an XZ sketch (no volume). |
| `AICopilot/freecad_mcp_handler.py` (`start_server`, `_init_windows_auth_token`) | The Windows auth token is written AFTER the exclusive `bind()` and before `listen()`, through a temp file restricted first and then `os.replace`d. | It was written before `bind()`: when two FreeCADs started together (the launcher's supervisor and an `ensure()` during a restart), the one that lost the port exited after overwriting the winner's token, and every client was refused ("did not prove it is your FreeCAD") until the next restart. Seen as a flaky `mcp_loop_test` on the Windows CI; reproduced and checked by `install/launcher_test.py` (2c) and two unit tests. |
| `AICopilot/handlers/measurement_ops.py` (`list_faces`) | Each face says its kind (plane, cylinder, cone...), its normal at the middle of its own parameters, and for a cylinder its radius and axis; no "-0.00". | The normal was taken at parameters (0, 0): arbitrary on a curved face (a bore read "(+1, 0, 0)"), and nothing told a bore from a plane when picking `face_index` / `faces=`. |
| `AICopilot/handlers/part_ops.py` (`section`) | `Shape.slice` at the plane (exact, unbounded); the reply gives the section's area; a plane that misses the part is refused with the part's extent. | It cut with a fixed 1000 x 1000 mm plane centred on the origin: a part beyond +-500 mm was cut partly or not at all, and an empty section was replied as "Created". |
| `AICopilot/handlers/transforms.py` (`rotate_object`) | Rotates around the GLOBAL axis named (new * old) through the object's placement point, and the reply states that point. | It composed old * new: an object already turned rotated around its OWN axis while the reply named the global one (10x20x30 turned about X then "about Z" came out 20x30x10 instead of 30x10x20); the pivot was not said. |
| `AICopilot/handlers/boolean_ops.py` | Fuse, cut and common replies give the measured volume (a cut: removed and left); a cut that removes nothing says so. | A tool placed beside the base removed nothing and was replied "Created cut". |
| `AICopilot/handlers/base.py` (`_check_feature_state` -> `_no_material_change`) | Every additive PartDesign feature (Pad, Revolution, Additive*) must add volume to the feature it builds on, every subtractive one (Pocket, Groove, Hole, Subtractive*) must remove some; otherwise the feature is removed from its Body and the call fails. `pocket` handles its own case first (other side). Not judged when the numbers are not real (unit-test mocks). | A pad buried in the part, a groove in the air, a hole off the part computed fine and were replied "Created ..." with the part unchanged. |
| `AICopilot/handlers/partdesign_ops.py` (`pocket`) | The Body's volume is measured before and after: a pocket that removes nothing is tried on the other side of the sketch (said in the reply); if neither side removes material the pocket is removed and the call fails. | A pocket sketched on the bottom face cut into empty space by default: it removed 0 mm3 and was replied "Created pocket ... depth 3mm". |
| `AICopilot/handlers/sketch_ops.py` (`verify_sketch`, again) | Closed loops that cross give `INVALID`: each loop alone must make one face with an area, all together a valid face with an area. | A figure-8 outline (the Pad made two solids) or two overlapping outlines (zero area) read "VALID - Ready for pad". Unit-test stubs without real geometry give "Can create face: unknown", never a verdict. |
| `AICopilot/handlers/sketch_ops.py` (`verify_sketch`) | "Face area" is that of the whole profile, holes subtracted. | It was the first wire's: a plate with holes reported the plain rectangle -- or one hole's circle. |
| tests | `test_part_ops`: in-plane refused, x on a YZ sketch (strict reply); `_freecad_mocks._PART_SPEC` lists `makeFace`; `install/base_exactness_test.py`: plate with 2 holes (circle first) = (200 - 2 pi) x 3 mm3, XZ sketch 80 mm3 along -y; `verify_sketch` of that plate says 193.72 mm2. | |
| `AICopilot/handlers/spatial_ops.py` (`face_relationship`) | The overlap area is that of the faces' common; faces that only meet along an edge or at a point say so. Normals are taken at the middle of each face; a curved face says "Parallel: n/a". | The area was built from the faces' section, which only holds the overlap's boundary edges (never a closed wire): two coplanar faces sharing 25 mm2 read "edges found (4) but no closed wire". `normalAt(0, 0)` is an arbitrary point of a curved face. |
| `AICopilot/handlers/spatial_ops.py` (`clearance`, `batch_interference`) | At zero distance, `clearance` measures the shared volume: `INTERFERING — they overlap by V mm3` or `TOUCHING`. `batch_interference` lists flush contacts under "Touching (contact, no shared volume)", not as collisions. | Two cubes overlapping by 500 mm3 (or one inside the other) read "TOUCHING (zero clearance)"; two parts merely resting on each other counted as a collision. |
| `AICopilot/handlers/base.py` (`_face_normal`), `verification_ops.py` (`verify_orientation`), `measurement_ops.py` (`list_faces`), `spatial_ops.py` (`face_relationship`) | A face's direction is its normal where the surface is closest to the face's centroid (then mid-parameters, then (0, 0)). | `normalAt(0, 0)` is an arbitrary point of a curved face: a dome facing +Z read its equator's horizontal normal, `verify_orientation(+Z)` failed it. |
| tests | `test_spatial_ops`: overlap area from the common, edge contact, overlap reported as interfering, contact as touching; `install/base_exactness_test.py`: coplanar faces share 25 mm2, flush cubes touch (no collision), overlapping cubes interfere by 500 mm3, a dome passes `verify_orientation(+Z)` (dot 1.0); `test_base_handler`: `_face_normal` at the centroid, then mid-parameters. | |
| tests | `test_partdesign_ops`: loft and sweep of a Body's sketches; `install/base_exactness_test.py`: loft 500 pi, sweep 750 pi, helix 80 pi^2 (Pappus) mm3. A sweep of the remaining operations found the others exact (primitives, Part extrude/revolve/loft/sweep, rotate, mirror, Draft arrays, polar pattern, PartDesign::Hole counterbore/countersink on a face). | |

## Test status

Upstream unit tests on Windows, compared with the unmodified upstream version in a sandboxed home:

| Version | Passed | New failures | Fixed on Windows |
|---|---|---|---|
| v8.2.3 + changes | 2118 | 0 | 9 (instance registry writes) |
| v8.3.0 + changes | 2191 | 0 | 9 |
| v8.3.0 + changes (2026-10-14) | 2254 | 0 | 9 |

- The remaining ~123 failures and 39 errors also exist on the unmodified upstream version: those
  tests use AF_UNIX sockets, `/tmp` and POSIX paths. On Linux, CI runs them all green.
- `tests/unit/test_headless_server.py::TestMainStartupSuccess` hangs on Windows and must be
  deselected.

These fixes are also checked live against a real FreeCAD by `install/base_fixes_test.py` in the
parent project. `install/base_exactness_test.py` compares the base's geometry operations with
known values: booleans, move, scale, spreadsheet-driven parameters, measurements, interference,
distance and clearance. It is the guard to run after every upstream update: since 2026-10-14 it also checks volumes of
PartDesign holes, dress-ups, loft, sweep, helix, rib, Part extrude/section against exact values
(37/37 project tests, including the Windows CI job `windows-full`).
