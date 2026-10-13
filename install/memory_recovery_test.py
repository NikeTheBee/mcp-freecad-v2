"""Demonstrate multi-session memory + crash recovery (cahier des charges §14).

Run with:  A:\FreeCAD\bin\freecadcmd.exe install\memory_recovery_test.py

- Multi-session: write project state + a checkpoint in THIS process, then spawn a
  SECOND freecadcmd process that reads the state from disk — proving continuity
  across sessions without re-describing context.
- Crash recovery: build geometry, checkpoint it, simulate a crash (close the doc),
  then restore from the checkpoint and confirm the geometry is back.

Uses a temp MCP_FREECAD_PROJECT_DIR so the repo is untouched. Prints MEMORY_RECOVERY_OK.
"""
import os
import subprocess
import sys
import tempfile

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(_repo, "server")
sys.path.insert(0, SERVER)

_tmp = tempfile.mkdtemp(prefix="mcpfc_mem_")
os.environ["MCP_FREECAD_PROJECT_DIR"] = _tmp

import FreeCAD
from freecad_layers import state, checkpoint

import shutil
FREECADCMD = (os.environ.get("FREECAD_MCP_FREECAD_BIN")
              or shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
              or r"A:\FreeCAD\bin\freecadcmd.exe")

# --- Session 1: record intent + a validated feature, then checkpoint ---
state.init_project("rocket-v1", intent="3-fin rocket, 450mm")
state.record_feature("BodyTube", "Rocket::BodyTube", {"diameter": 24.79, "length": 457})

doc = FreeCAD.newDocument("MemRec")
box = doc.addObject("Part::Box", "Hull")
box.Length, box.Width, box.Height = 24, 24, 457
doc.recompute()
ckpt = checkpoint.save("after_hull", note="hull before fins", doc=doc)
print("SESSION1 wrote state + checkpoint:", os.path.basename(ckpt))

# --- Session 2: a separate freecadcmd process reads the persisted state ---
child = (
    "import sys; sys.path.insert(0, r'%s'); "
    "from freecad_layers import state; s = state.summary(); "
    "print('CHILD_SEES:', s); "
    "assert 'rocket-v1' in s and 'BodyTube' in s, 'state did not persist across sessions'"
    % SERVER
)
r = subprocess.run([FREECADCMD, "-c", child], capture_output=True, text=True,
                   env=os.environ, timeout=120)
assert "CHILD_SEES:" in r.stdout and "rocket-v1" in r.stdout, \
    f"second session could not read state:\n{r.stdout}\n{r.stderr}"
print("SESSION2 (separate process) read persisted state OK")

# --- Crash recovery: lose the live doc, restore from checkpoint ---
FreeCAD.closeDocument(doc.Name)            # simulate crash / lost session
assert "MemRec" not in [d for d in FreeCAD.listDocuments()]
restored = checkpoint.restore("after_hull")
assert any(o.Name == "Hull" for o in restored.Objects), "geometry not recovered"
print("CRASH RECOVERY: restored 'Hull' from checkpoint after doc loss")

# --- No FreeCAD backup files pile up: autosaves reserve an empty file that FreeCAD
#     used to "back up" as a .FCBak on each save; a replaced checkpoint left one too ---
from pathlib import Path  # noqa: E402
from freecad_layers import recovery  # noqa: E402
for _ in range(3):
    recovery.autosave(restored)
checkpoint.save("after_hull", note="replaced", doc=restored)
baks = sorted(p.name for p in Path(_tmp).rglob("*.FCBak"))
assert not baks, f"FreeCAD backup files left behind: {baks}"
print("autosaves and a replaced checkpoint leave no .FCBak behind: OK")

print("MEMORY_RECOVERY_OK")
