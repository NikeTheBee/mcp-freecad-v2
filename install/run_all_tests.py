"""Run all MCP FREECAD verification tests and report (Phase 5 reproducibility).

Usage (system Python):  py -3.13 install/run_all_tests.py

- The graft/layer tests run standalone under freecadcmd.
- The Phase 1 smoke test needs the headless MCP socket server live on
  localhost:23456; it is run only if that port is open, otherwise skipped.
- A test that prints "<PREFIX>_SKIP" validated nothing: it is reported as
  SKIP, never counted as a pass.

Exit code 0 iff every test that ran passed.
"""
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def resolve_freecadcmd() -> str:
    """env override -> PATH -> known fallback. Survives a FreeCAD path change."""
    return (os.environ.get("FREECAD_MCP_FREECAD_BIN")
            or shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
            or r"A:\FreeCAD\bin\freecadcmd.exe")


FREECADCMD = resolve_freecadcmd()

# (script, success sentinel) — run under freecadcmd
# Graft tests need the third-party workbench clones under addons/. Set
# MCPFC_SKIP_GRAFTS=1 (e.g. in CI with only bundled FreeCAD) to skip them.
GRAFT_TESTS = [
    ("rocket_graft_test.py", "ROCKET_GRAFT_OK"),
    ("drone_graft_test.py", "DRONE_GRAFT_OK"),
    ("robotics_urdf_test.py", "ROBOTICS_URDF_OK"),
]
CORE_TESTS = [
    ("partdesign_test.py", "PARTDESIGN_OK"),
    ("partdesign_dressup_test.py", "DRESSUP_OK"),
    ("spreadsheet_variants_test.py", "SPREADSHEET_OK"),
    ("tolerances_test.py", "TOLERANCES_OK"),
    ("mesh_repair_test.py", "MESH_REPAIR_OK"),
    ("urdf_package_test.py", "URDF_PACKAGE_OK"),
    ("urdf_control_test.py", "URDF_CONTROL_OK"),
    ("ik_exposure_test.py", "IK_EXPOSURE_OK"),
    ("rocket_cp_test.py", "ROCKET_CP_OK"),
    ("drone_aero_test.py", "DRONE_AERO_OK"),
    ("layers_test.py", "LAYERS_TEST_OK"),
    ("verify_test.py", "VERIFY_OK"),
    ("techdraw_test.py", "TECHDRAW_OK"),
    ("kinematics_test.py", "KINEMATICS_OK"),
    ("bom_test.py", "BOM_OK"),
    ("print3d_test.py", "PRINT3D_OK"),
    ("exchange_test.py", "EXCHANGE_OK"),
    ("fem_test.py", "FEM_OK"),
    ("memory_recovery_test.py", "MEMORY_RECOVERY_OK"),
    ("../examples/resume_between_sessions.py", "RESUME_DEMO_OK"),
    ("assembly_test.py", "ASSEMBLY_OK"),
    ("gear_test.py", "GEAR_OK"),
]
FREECAD_TESTS = (CORE_TESTS if os.environ.get("MCPFC_SKIP_GRAFTS")
                 else GRAFT_TESTS + CORE_TESTS)

_SKIP_LINE = re.compile(r"^[A-Z0-9_]+_SKIP\b.*$", re.MULTILINE)


def _port_open(host="localhost", port=23456) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _run(cmd) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return False, f"<runner error: {e}>"


def _record(results, name: str, ok: bool, out: str, sentinel: str) -> None:
    """PASS only on exit 0 + sentinel; an explicit *_SKIP line is a SKIP."""
    skip = _SKIP_LINE.search(out)
    if ok and sentinel in out:
        status = "PASS"
    elif ok and skip:
        status = "SKIP"
    else:
        status = "FAIL"
    results.append((name, status))
    print(f"[{status}] {name}")
    if status == "SKIP":
        print("    " + skip.group(0))
    elif status == "FAIL":
        print("    " + out.strip().replace("\n", "\n    ")[-1200:])


def _skip(results, name: str, why: str) -> None:
    results.append((name, "SKIP"))
    print(f"[SKIP] {name} ({why})")


def main() -> int:
    results = []

    # Security scan (cyber / NF5 / §13) — pure Python, no FreeCAD needed.
    ok, out = _run([sys.executable, str(HERE / "security_scan.py")])
    _record(results, "security_scan.py", ok, out, "SECURITY_SCAN_OK")

    # Autostart / R10 test — system Python; also boots the headless server so
    # the live phase1/mcp-loop tests below run instead of being skipped.
    # Skipped in CI (MCPFC_SKIP_GRAFTS) where no real FreeCAD install exists.
    if not os.environ.get("MCPFC_SKIP_GRAFTS"):
        for script, sentinel in (("autostart_test.py", "AUTOSTART_OK"),
                                 ("auth_test.py", "AUTH_OK")):
            ok, out = _run([sys.executable, str(HERE / script)])
            _record(results, script, ok, out, sentinel)

    if not Path(FREECADCMD).exists():
        print(f"!! freecadcmd not found at {FREECADCMD} "
              f"(set FREECAD_MCP_FREECAD_BIN)")
        return 2

    for script, sentinel in FREECAD_TESTS:
        ok, out = _run([FREECADCMD, str(HERE / script)])
        _record(results, script, ok, out, sentinel)

    # Phase 1 smoke test needs the live socket server
    if _port_open():
        ok, out = _run([sys.executable, str(HERE / "phase1_smoke_test.py")])
        ok = ok and '"result"' in out and "6000" in out
        _record(results, "phase1_smoke_test.py", ok, out, '"result"')
    else:
        _skip(results, "phase1_smoke_test.py",
              "no server on localhost:23456 — start the 'freecad-headless' server first")

    # MCP-protocol loop test needs the `mcp` SDK + a reachable FreeCAD instance
    have_mcp = subprocess.run([sys.executable, "-c", "import mcp"],
                              capture_output=True).returncode == 0
    if have_mcp and _port_open():
        ok, out = _run([sys.executable, str(HERE / "mcp_loop_test.py")])
        _record(results, "mcp_loop_test.py", ok, out, "MCP_LOOP_OK")
    else:
        _skip(results, "mcp_loop_test.py", "needs the `mcp` SDK and a server on :23456")

    failed = [n for n, s in results if s == "FAIL"]
    skipped = [n for n, s in results if s == "SKIP"]
    passed = len(results) - len(failed) - len(skipped)
    print(f"\n{passed} passed, {len(skipped)} skipped, {len(failed)} failed "
          f"(of {len(results)})"
          + (f"; SKIPPED: {', '.join(skipped)}" if skipped else "")
          + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
