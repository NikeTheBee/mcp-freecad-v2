"""FreeCAD upgrade handover — bridge venv Python, isolated port 23499, private home.

An old FreeCAD (another folder) holds the port:
- without a record of it, the launcher configured on the new FreeCAD refuses it (anti-squatting);
- with ~/.freecad-mcp/previous_freecad.json naming it (written by bootstrap.py when it registered
  the new one), the launcher asks it to save and exit, then starts the new FreeCAD, and an
  open document of the old one is saved first.

Needs two FreeCAD installs: FREECAD_MCP_FREECAD_BIN (new) and MCPFC_PREV_FREECAD (old, default
A:\\FreeCAD\\bin\\freecadcmd.exe). Otherwise HANDOVER_SKIP. Sentinel: HANDOVER_OK
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fc_instance import Instance, freecadcmd  # noqa: E402

import psutil  # noqa: E402


def main() -> int:
    new = Path(freecadcmd())
    old = Path(os.environ.get("MCPFC_PREV_FREECAD", r"A:\FreeCAD\bin\freecadcmd.exe"))
    if not new.is_file() or not old.is_file() or os.path.normcase(str(new.parent)) == os.path.normcase(str(old.parent)):
        print("HANDOVER_SKIP: needs two FreeCAD installs in different folders")
        return 0
    inst = Instance()
    inst.env["FREECAD_MCP_FREECAD_BIN"] = str(old)
    pids = []
    try:
        pid_old = inst.start()
        pids.append(pid_old)
        assert Path(psutil.Process(pid_old).exe()).parent == old.parent, "old FreeCAD not started"
        doc = inst.home / "keep.FCStd"
        r = inst.call("execute_python_sync", {"code": (
            "import FreeCAD\nd = FreeCAD.newDocument('Keep')\nb = d.addObject('Part::Box', 'B')\n"
            f"d.recompute()\nd.saveAs({str(doc)!r})\nb.Length = 42\nd.recompute()\n'ok'")})
        assert r.get("result") == "'ok'", r

        # 1. no record: the new launcher refuses the old FreeCAD
        inst.env["FREECAD_MCP_FREECAD_BIN"] = str(new)
        r = inst.launcher("--check")
        info = json.loads(r.stdout.strip().splitlines()[-1])
        assert not info.get("ok") and "outside the configured FreeCAD" in json.dumps(info), info
        assert psutil.pid_exists(pid_old), "the old FreeCAD must be left alone without a record"
        print("no record: old FreeCAD refused and left running OK")

        # 2. recorded as the previous install: it saves, exits, the new one starts
        (inst.home / ".freecad-mcp" / "previous_freecad.json").write_text(
            json.dumps({"freecadcmd": str(old)}), encoding="utf-8")
        r = inst.launcher("--check")
        info = json.loads(r.stdout.strip().splitlines()[-1])
        assert info.get("ok"), (info, r.stderr[-800:])
        pid_new = info["pid"]
        pids.append(pid_new)
        assert pid_new != pid_old and Path(psutil.Process(pid_new).exe()).parent == new.parent, info
        assert not psutil.pid_exists(pid_old) or psutil.Process(pid_old).status() == psutil.STATUS_ZOMBIE
        inst.pid = pid_new
        r = inst.call("execute_python_sync", {"code": (
            f"import FreeCAD\nd = FreeCAD.openDocument({str(doc)!r})\nd.getObject('B').Length.Value")})
        assert r.get("result") == "42.0", r            # the old FreeCAD saved before leaving
        print("recorded previous install: saved, exited, new FreeCAD serves the port OK")
    finally:
        for pid in pids:                               # only the FreeCADs this test started
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
    print("HANDOVER_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
