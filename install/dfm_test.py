"""Design-for-manufacturing checks (server/freecad_layers/dfm.py) on parts with known answers.

Run with:  <freecadcmd> install/dfm_test.py      Sentinel: DFM_OK
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import FreeCAD as App  # noqa: E402
import Part  # noqa: E402
from freecad_layers import dfm  # noqa: E402

V = App.Vector
doc = App.newDocument("Dfm")


def feat(name, shape):
    o = doc.addObject("Part::Feature", name)
    o.Shape = shape
    doc.recompute()
    return o


block = Part.makeBox(40, 40, 20)
pocket = block.cut(Part.makeBox(20, 20, 10, V(10, 10, 10)))
feat("Pocket", pocket)
r = dfm.check("Pocket", "cnc", tool_d=6)
assert r["status"] == "FAIL" and "4 sharp inside corner" in r["issues"][0], r
print("square pocket: 4 sharp inside corners found OK")

feat("Block", block)
r = dfm.check("Block", "cnc", tool_d=6)
assert r["status"] == "OK", r                       # outside corners of a block are millable
print("plain block: no issue OK")

edges = [e for e in pocket.Edges if isinstance(e.Curve, Part.Line)
         and abs(abs(e.tangentAt(e.FirstParameter).z) - 1) < 1e-9 and 9.9 < e.CenterOfMass.x < 30.1
         and 9.9 < e.CenterOfMass.y < 30.1]
assert len(edges) == 4, len(edges)
round4 = pocket.makeFillet(4, edges)
feat("Rounded", round4)
r = dfm.check("Rounded", "cnc", tool_d=6)
assert r["status"] == "OK", r
print("pocket with R4 corners, D6 mill: OK")
r = dfm.check("Rounded", "cnc", tool_d=10)
assert r["status"] == "FAIL" and "internal radius under the tool's R5" in r["issues"][0], r
print("same pocket, D10 mill: R4 corners under R5 found OK")
r = dfm.check("Block", "cnc", tool_d=6, axis="x")
assert r["status"] == "OK", r
print("block milled along x: OK")

r = dfm.check("Block", "mold", axis="z", draft_deg=1)
assert r["status"] == "FAIL" and "4 side face(s)" in r["issues"][0], r
print("block, no draft: 4 side faces under 1 deg OK")
lo = Part.makePolygon([V(0, 0, 0), V(40, 0, 0), V(40, 40, 0), V(0, 40, 0), V(0, 0, 0)])
import math  # noqa: E402
k = 20 * math.tan(math.radians(2))
hi = Part.makePolygon([V(k, k, 20), V(40 - k, k, 20), V(40 - k, 40 - k, 20), V(k, 40 - k, 20), V(k, k, 20)])
feat("Tapered", Part.makeLoft([lo, hi], True))
r = dfm.check("Tapered", "mold", axis="z", draft_deg=1)
assert r["status"] == "OK", r
r = dfm.check("Tapered", "mold", axis="z", draft_deg=3)
assert r["status"] == "FAIL" and "2.00 deg" in r["issues"][0], r
print("2 deg tapered block: OK at 1 deg, 2.00 deg reported under 3 deg OK")
sealed = Part.makeCylinder(50, 200).cut(Part.makeCylinder(46, 190, V(0, 0, 5)))
feat("Sealed", sealed)
r = dfm.check("Sealed", "cnc", tool_d=6)
assert r["status"] == "FAIL" and "closed internal cavity" in r["issues"][0], r
print("sealed hollow housing: closed cavity found, split required OK")
print("DFM_OK")
