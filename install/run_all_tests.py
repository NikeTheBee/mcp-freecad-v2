"""Run all MCP FREECAD verification tests and report (Phase 5 reproducibility).

Usage (system Python):  py -3.13 install/run_all_tests.py

- The graft/layer tests run standalone under freecadcmd.
- The end-to-end tests (launcher, socket smoke, MCP loop) run with the bridge
  venv on an ISOLATED FreeCAD (port 23499, private home): they never touch the
  live localhost:23456 instance.
- A test that prints "<PREFIX>_SKIP" validated nothing: it is reported as
  SKIP, never counted as a pass.

Exit code 0 iff every test that ran passed.
"""
import os
import re
import shutil
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

# (script, success sentinel) — run under the system Python, no FreeCAD needed
PURE_TESTS = [
    ("security_scan.py", "SECURITY_SCAN_OK"),
    ("state_test.py", "STATE_OK"),
    ("projects_test.py", "PROJECTS_OK"),
]

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
    ("rocket_cp_test.py", "ROCKET_CP_OK"),
    ("drone_aero_test.py", "DRONE_AERO_OK"),
    ("layers_test.py", "LAYERS_TEST_OK"),
    ("verify_test.py", "VERIFY_OK"),
    ("analysis_test.py", "ANALYSIS_OK"),
    ("spec_test.py", "SPEC_OK"),
    ("techdraw_test.py", "TECHDRAW_OK"),
    ("render_test.py", "RENDER_OK"),
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

# (script, sentinel) — run with the bridge venv Python on an isolated instance
LIVE_TESTS = [
    ("launcher_test.py", "LAUNCHER_OK"),
    ("phase1_smoke_test.py", "PHASE1_OK"),
    ("mcp_loop_test.py", "MCP_LOOP_OK"),
    ("layers_mcp_test.py", "LAYERS_MCP_OK"),
    ("base_fixes_test.py", "BASE_FIXES_OK"),
]
VENV_PY = (Path.home() / ".freecad-mcp" / "venv"
           / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))

_SKIP_LINE = re.compile(r"^[A-Z0-9_]+_SKIP\b.*$", re.MULTILINE)


def _run(cmd, env=None) -> tuple[bool, str]:
    try:
        # freecadcmd writes in the ANSI code page whatever PYTHONUTF8 says; a strict
        # decode lost the WHOLE output on the first "Ø" (sentinels are ASCII).
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300, env=env)
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

    # Pure-Python tests (no FreeCAD needed) — also run in CI.
    for script, sentinel in PURE_TESTS:
        ok, out = _run([sys.executable, str(HERE / script)])
        _record(results, script, ok, out, sentinel)

    if not Path(FREECADCMD).exists():
        print(f"!! freecadcmd not found at {FREECADCMD} "
              f"(set FREECAD_MCP_FREECAD_BIN)")
        return 2

    for script, sentinel in FREECAD_TESTS:
        ok, out = _run([FREECADCMD, str(HERE / script)])
        _record(results, script, ok, out, sentinel)

    # End-to-end tests: launcher -> isolated FreeCAD (test port, private home)
    # -> upstream bridge. Need the bridge venv (install/bootstrap.py); they
    # never touch the live :23456 instance.
    for script, sentinel in LIVE_TESTS:
        if os.environ.get("MCPFC_SKIP_GRAFTS"):
            _skip(results, script, "CI: no Windows FreeCAD install")
        elif not VENV_PY.exists():
            _skip(results, script, f"bridge venv missing at {VENV_PY} — run install/bootstrap.py")
        else:
            env = dict(os.environ, FREECAD_MCP_FREECAD_BIN=FREECADCMD)
            ok, out = _run([str(VENV_PY), str(HERE / script)], env=env)
            _record(results, script, ok, out, sentinel)

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
