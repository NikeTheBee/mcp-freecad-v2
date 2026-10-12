"""End-to-end test of the home-grown layers (Phase 4): state, verify, checkpoint.

Run with:  A:\FreeCAD\bin\freecadcmd.exe install\layers_test.py
Uses a temp MCP_FREECAD_PROJECT_DIR so it never touches the repo's real
project_state/ or checkpoints/. Prints LAYERS_TEST_OK on success.
"""
import os
import sys
import tempfile

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_repo, "server"))

# Isolate all layer I/O into a temp dir BEFORE importing the layers.
_tmp = tempfile.mkdtemp(prefix="mcpfc_layers_")
os.environ["MCP_FREECAD_PROJECT_DIR"] = _tmp

import FreeCAD
import Part
from freecad_layers import state, verify, checkpoint

# --- project memory --------------------------------------------------------
state.init_project("LayerTest", intent="validate the three home-grown layers")
state.add_constraint("max envelope 100mm")
state.set_parameter("box_len", 10)
state.record_feature("Box", "Part::Box", {"length": 10, "width": 20, "height": 30})
state.add_decision("use a simple box as the test feature")
print("STATE_SUMMARY:\n" + state.summary())
assert "Box(Part::Box)" in state.summary()

# --- geometric verification ------------------------------------------------
doc = FreeCAD.newDocument("LayerTest")
box = doc.addObject("Part::Box", "Box")
box.Length, box.Width, box.Height = 10, 20, 30
doc.recompute()

v = verify.verdict("Box")
print("VERDICT:", v)
assert verify.check("Box")["ok"] is True
assert verify.watertight("Box") is True

# a bare wire is not a watertight solid -> verification must say so
w = doc.addObject("Part::Feature", "OpenWire")
w.Shape = Part.makePolygon([FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(10, 0, 0),
                            FreeCAD.Vector(10, 10, 0)])
doc.recompute()
print("WIRE_VERDICT:", verify.verdict("OpenWire"))
assert verify.watertight("OpenWire") is False

# --- checkpoints / rollback ------------------------------------------------
path = checkpoint.save("before_changes", note="box only", doc=doc)
print("CHECKPOINT_SAVED:", os.path.basename(path))
assert os.path.exists(path)
assert any(c["name"] == "before_changes" for c in checkpoint.list_checkpoints())

restored = checkpoint.restore("before_changes")
names = [o.Name for o in restored.Objects]
print("RESTORED_OBJECTS:", names)
assert "Box" in names

# --- audit C1: two different names must never share a checkpoint file ------
b = doc.getObject("Box")
checkpoint.save("v1/fins", doc=doc)
b.Length = 99
doc.recompute()
checkpoint.save("v1 fins", doc=doc)
r = checkpoint.restore("v1/fins")
assert r.getObject("Box").Length.Value == 10, "checkpoint name collision"
print("distinct names keep distinct checkpoints: OK")

# --- audit C2: restore must reload from DISK, not the modified open copy ----
r.getObject("Box").Length = 777
r.recompute()
r2 = checkpoint.restore("v1/fins")
assert r2.getObject("Box").Length.Value == 10, "rollback returned in-memory edits"
print("restore reloads the saved content: OK")

# --- true rollback: the abandoned document is kept in /recovery, then closed --
from freecad_layers import recovery
work = FreeCAD.newDocument("Work")
wb = work.addObject("Part::Box", "B")
work.recompute()
checkpoint.save("good", doc=work)
wb.Length = 555
work.recompute()
FreeCAD.setActiveDocument("Work")
back = checkpoint.restore("good", replace_active=True)
assert "Work" not in FreeCAD.listDocuments(), "abandoned doc still open"
assert back.getObject("B").Length.Value == 10
saved = recovery.latest("Work")
assert saved, "abandoned work was not autosaved"
lost = FreeCAD.openDocument(saved)
assert lost.getObject("B").Length.Value == 555, "recovery copy is not the abandoned state"
print("rollback keeps the abandoned work in recovery: OK")

# --- autosave keeps only the newest copies ---------------------------------
for _ in range(7):
    recovery.autosave(doc)
assert len(recovery.list_recovery(doc.Name)) == recovery.KEEP
print("recovery pruning keeps", recovery.KEEP, "copies: OK")

# --- two autosaves in the same millisecond (CI on Linux hit this) ----------
_real_time = recovery.time.time
recovery.time.time = lambda: 4102444800.123     # frozen in 2100: same stamp, newest copies
try:
    a, b = recovery.autosave(doc), recovery.autosave(doc)
finally:
    recovery.time.time = _real_time
assert a != b and os.path.exists(a) and os.path.exists(b), (a, b)
assert recovery.latest(doc.Name) == b, (recovery.latest(doc.Name), b)
print("same-millisecond autosaves get distinct names, latest() = newest: OK")

# --- audit P1: output gate refuses NTFS streams and device names -----------
from freecad_layers import safe_out_path
for bad in (os.path.join(_tmp, "evil.exe:x.csv"), os.path.join(_tmp, "CON.csv"),
            os.path.join(_tmp, "nul.csv")):
    try:
        safe_out_path(bad, {".csv"})
        print(f"FAIL: accepted {bad}")
        sys.exit(1)
    except ValueError:
        pass
assert not os.path.exists(os.path.join(_tmp, "evil.exe")), "ADS host file created"
ok_path = safe_out_path(os.path.join(_tmp, "out", "a.csv"), {".csv"})
try:
    safe_out_path(ok_path, {".csv"})          # name already reserved -> refused
    print("FAIL: second reservation accepted"); sys.exit(1)
except FileExistsError:
    pass
print("output gate: streams/device names refused, atomic reservation: OK")

# --- secure-by-design: a tampered state.json must not escape /checkpoints --
s = state.load()
s["checkpoints"].append({"name": "evil", "file": "..\\..\\outside.FCStd",
                         "at": "now", "note": ""})
state.save(s)
try:
    checkpoint.restore("evil")
    print("FAIL: traversal filename accepted")
    sys.exit(1)
except ValueError as e:
    print("tampered checkpoint path refused: OK")

print("LAYERS_TEST_OK")
