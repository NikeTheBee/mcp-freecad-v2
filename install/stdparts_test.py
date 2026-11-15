"""Standard parts (server/freecad_layers/stdparts.py) against exact volumes, and the BOM.

Run with:  <freecadcmd> install/stdparts_test.py      Sentinel: STDPARTS_OK
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import FreeCAD as App  # noqa: E402
from freecad_layers import bom, stdparts  # noqa: E402


def close(a, b, rel=1e-6, what=""):
    assert abs(a - b) <= rel * max(abs(b), 1.0), f"{what}: {a} != {b}"
    print(f"{what}: {a:.4f} OK")


doc = App.newDocument("Std")
w = stdparts.make(doc, "washer", "M6")
close(w["volume_mm3"], math.pi * (6 ** 2 - 3.2 ** 2) * 1.6, 1e-5, "ISO 7089 M6 washer volume")
b = stdparts.make(doc, "bearing", "608", at=(50, 0, 0))
close(b["volume_mm3"], math.pi * (11 ** 2 - 4 ** 2) * 7, 1e-5, "bearing 608 (8x22x7) ring volume")
n = stdparts.make(doc, "nut", "M6", at=(0, 50, 0))
r = 10 / math.sqrt(3)
close(n["volume_mm3"], (3 * math.sqrt(3) / 2 * r * r - math.pi * 9) * 5.2, 1e-5, "ISO 4032 M6 nut volume")
s = stdparts.make(doc, "iso4762", "M6", 20, at=(0, 0, 30), axis="z")
rs = 5 / math.sqrt(3)
want = math.pi * 25 * 6 - 3 * math.sqrt(3) / 2 * rs * rs * 3.6 + math.pi * 9 * 20
close(s["volume_mm3"], want, 1e-4, "ISO 4762 M6x20 (head - socket + shank)")
o = doc.getObject(s["name"])
bb = o.Shape.BoundBox
close(bb.ZMin, 10.0, 1e-9, "shank ends 20 mm under the head plane (z 30)")
close(bb.ZMax, 36.0, 1e-9, "head top 6 mm above it")
assert o.StdPart == "ISO 4762 M6x20" and o.Source == "buy" and s["valid"], (o.StdPart, o.Source)
sx = stdparts.make(doc, "iso4762", "M6", 20, at=(0, 0, 0), axis="x")
bbx = doc.getObject(sx["name"]).Shape.BoundBox
close(bbx.XMin, -20.0, 1e-9, "axis x: the shank points to -x")
for i in range(3):
    stdparts.make(doc, "iso4762", "M6", 20, at=(100 + 20 * i, 0, 0))
close(stdparts.clearance("M6"), 6.6, 1e-12, "ISO 273 medium clearance for M6")
close(stdparts.clearance("M3", "fine"), 3.2, 1e-12, "ISO 273 fine clearance for M3")
for bad in (("iso4762", "M7", 20), ("iso4762", "M6", 0), ("bearing", "999", 0), ("rivet", "M3", 5)):
    try:
        stdparts.make(doc, *bad)
    except ValueError as e:
        print(f"refused {bad}: {str(e)[:60]} OK")
    else:
        raise AssertionError(f"{bad} accepted")

rows = bom.collect(doc)
screws = [r for r in rows if r["designation"] == "ISO 4762 M6x20"]
assert len(screws) == 1 and screws[0]["qty"] == 5 and screws[0]["source"] == "buy", rows
v = bom.verdict(bom.bom_for(doc))
assert "(8 purchased)" in v, v
print(f"BOM: {v} OK")
print("STDPARTS_OK")
