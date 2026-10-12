"""Live check of the MCP FREECAD v2 fixes to the vendored base (server/freecad-mcp).

Raw socket on an isolated instance (test port, private home; bridge venv Python):
1. partdesign hole WITHOUT face_index on a PartDesign Body now cuts INSIDE the
   Body (was a Part::Cut outside it: the Body kept no hole);
2. verify_sketch on a closed but free sketch reports its DoF, not
   "Fully constrained: Yes";
3. an oversized reply comes back at once as an explicit error (was a ~120 s
   silent wait, then a misleading timeout).
Sentinel: BASE_FIXES_OK
"""
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fc_instance import Instance  # noqa: E402

BUILD = """
import FreeCAD, Part, Sketcher
doc = FreeCAD.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')
blk = body.newObject('PartDesign::AdditiveBox', 'Blk')
blk.Length, blk.Width, blk.Height = 20, 20, 10
sk = doc.addObject('Sketcher::SketchObject', 'Free')
P = [FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(10, 0, 0),
     FreeCAD.Vector(10, 5, 0), FreeCAD.Vector(0, 5, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(P[i], P[(i + 1) % 4]))
for i in range(4):
    sk.addConstraint(Sketcher.Constraint('Coincident', i, 2, (i + 1) % 4, 1))
doc.recompute()
'built'
"""

PROBE = """
import FreeCAD, json
doc = FreeCAD.ActiveDocument
b = doc.getObject('Body')
json.dumps({'vol': round(b.Shape.Volume, 3), 'tip': b.Tip.TypeId,
            'cuts': [o.Name for o in doc.Objects if o.TypeId == 'Part::Cut']})
"""


def op(inst: Instance, tool: str, args: dict, wait: float = 60.0):
    """Call a tool; queued operations return a job id that is polled to completion."""
    r = inst.call(tool, args)
    deadline = time.monotonic() + wait
    while isinstance(r, dict) and r.get("status") in ("submitted", "running", "queued"):
        assert time.monotonic() < deadline, f"{tool} still running: {r}"
        time.sleep(0.2)
        r = inst.call("poll_job", {"job_id": r.get("job_id") or r.get("id")})
    return r.get("result", r) if isinstance(r, dict) else r


def main() -> int:
    inst = Instance()
    try:
        inst.start()
        inst.call("view_control", {"operation": "create_document", "name": "BaseFixes"})
        r = inst.call("execute_python_sync", {"code": BUILD})
        assert r and "built" in str(r.get("result")), r

        # 1. hole without face_index on a Body object (the feature, then the Body itself)
        r = op(inst, "partdesign_operations", {"operation": "hole", "object_name": "Blk",
                                                "hole_type": "simple", "diameter": 6, "depth": 10,
                                                "x": 10, "y": 10})
        print("hole:", str(r)[:160])
        assert "SubtractiveCylinder" in str(r), r
        p = json.loads(inst.call("execute_python_sync", {"code": PROBE})["result"].strip("'\""))
        print("after hole:", p)
        expected = 20 * 20 * 10 - math.pi * 3 ** 2 * 10
        assert abs(p["vol"] - expected) < 0.5, (p, expected)
        assert p["tip"] == "PartDesign::SubtractiveCylinder", p
        assert p["cuts"] == [], p
        r = op(inst, "partdesign_operations", {"operation": "counterbore", "object_name": "Body",
                                                "diameter": 6})
        print("counterbore, no face:", str(r)[:120])
        assert "needs face_index" in str(r), r

        # 2. verify_sketch on a closed, free quadrilateral (16 params - 4x2 coincident = 8 DoF)
        r = op(inst, "sketch_operations", {"operation": "verify_sketch", "sketch_name": "Free"})
        txt = str(r)
        print("verify_sketch:", txt[:240].replace("\\n", " | "))
        assert "Fully constrained: Yes" not in txt, txt
        assert "Under-constrained: 8 degrees of freedom" in txt, txt

        # 3. oversized reply -> explicit error, fast
        t0 = time.monotonic()
        r = inst.call("execute_python_sync", {"code": "'x' * 60000"}, timeout=60)
        dt = time.monotonic() - t0
        print(f"oversized reply in {dt:.1f}s:", str(r)[:140])
        assert r and r.get("result_too_large") is True, r
        assert dt < 15, dt
        r = inst.call("execute_python_sync", {"code": "1+1"})
        assert r and r.get("result") == "2", r  # the connection protocol is still in sync
    finally:
        inst.stop()
    print("BASE_FIXES_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
