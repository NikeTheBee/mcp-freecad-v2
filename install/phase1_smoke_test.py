"""Phase 1 validation probe — raw socket, isolated instance (bridge venv Python).

Talks directly to a headless FreeCAD started by the launcher on the test port,
with the upstream framing + auth token: create a document and a box, read its
volume back. Never touches the live :23456 instance. Sentinel: PHASE1_OK
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fc_instance import Instance  # noqa: E402


def main() -> int:
    inst = Instance()
    try:
        inst.start()
        r = inst.call("view_control", {"operation": "create_document", "name": "Phase1"})
        print("create_document:", str(r)[:120])
        r = inst.call("part_operations", {"operation": "box", "length": 10, "width": 20,
                                          "height": 30, "name": "SmokeBox"})
        print("box:", str(r)[:120])
        r = inst.call("execute_python_sync", {
            "code": "import FreeCAD\nround(FreeCAD.ActiveDocument.getObject('SmokeBox').Shape.Volume, 6)"})
        print("volume:", r)
        assert r and r.get("result") in ("6000.0", "6000"), r
    finally:
        inst.stop()
    print("PHASE1_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
