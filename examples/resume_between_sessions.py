"""Resume-between-sessions demo - proves cahier des charges §14.1 criterion
"reprendre un projet entre 2 sessions via la mémoire projet (sans re-décrire)".

Two genuinely separate FreeCAD processes share only /project_state/state.json:

  Session 1  (freecadcmd, process #1)
    - init project memory: intent, parameters
    - build the base plate from those parameters, verify it, record the feature
  Session 2  (freecadcmd, FRESH process #2 - knows nothing)
    - reads `state.summary()` (the token-minimal digest an AI reads at session
      start), rebuilds context from it - no re-description by the user
    - continues the design: drills the holes using the STORED parameters,
      verifies, records the new feature

Run it (orchestrates both sessions):    freecadcmd examples/resume_between_sessions.py
Run one side: add --session1 / --session2. Sentinel: RESUME_DEMO_OK

The demo state is isolated under examples/demo_state/ (wiped at start) so it
never touches your real project memory.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO / "examples" / "demo_state"

# Isolate the demo's memory from the real /project_state BEFORE importing layers.
os.environ["MCP_FREECAD_PROJECT_DIR"] = str(DEMO_DIR)
sys.path.insert(0, str(REPO / "server"))

from freecad_layers import state, verify  # noqa: E402


def session1() -> None:
    import FreeCAD
    import Part

    state.init_project("DemoBracket", "flat plate 60x40x5 mm with two M5 holes")
    state.set_parameter("plate_mm", [60, 40, 5])
    state.set_parameter("hole_d_mm", 5.0)
    state.set_parameter("hole_inset_mm", 10.0)

    doc = FreeCAD.newDocument("DemoBracket")
    plate = doc.addObject("Part::Box", "Plate")
    plate.Length, plate.Width, plate.Height = 60, 40, 5
    doc.recompute()

    v = verify.verdict(plate)
    print("S1 verify:", v)
    assert v.startswith("OK"), v
    state.record_feature("Plate", "Part::Box", {"size": [60, 40, 5]})
    doc.saveAs(str(DEMO_DIR / "DemoBracket.FCStd"))
    print("S1 done: plate built, memory written")


def session2() -> None:
    import FreeCAD
    import Part

    # The ONLY context this fresh process gets is the memory digest:
    digest = state.summary()
    print("S2 reads memory:\n" + digest)
    assert "DemoBracket" in digest and "Plate(Part::Box)" in digest, digest

    s = state.load()
    size = s["parameters"]["plate_mm"]
    hole_d = s["parameters"]["hole_d_mm"]
    inset = s["parameters"]["hole_inset_mm"]

    doc = FreeCAD.openDocument(str(DEMO_DIR / "DemoBracket.FCStd"))
    plate = doc.getObject("Plate")

    drilled = plate.Shape
    for x in (inset, size[0] - inset):
        cyl = Part.makeCylinder(hole_d / 2.0, size[2] + 2,
                                FreeCAD.Vector(x, size[1] / 2.0, -1))
        drilled = drilled.cut(cyl)
    out = doc.addObject("Part::Feature", "DrilledPlate")
    out.Shape = drilled
    doc.recompute()

    v = verify.verdict(out)
    print("S2 verify:", v)
    assert v.startswith("OK"), v
    # Two Ø5 holes removed from 60x40x5: volume must have dropped accordingly.
    import math
    expect = 60 * 40 * 5 - 2 * math.pi * (hole_d / 2) ** 2 * 5
    assert abs(out.Shape.Volume - expect) < 1.0, (out.Shape.Volume, expect)

    state.record_feature("DrilledPlate", "Part::Feature",
                         {"holes": 2, "d_mm": hole_d})
    doc.save()
    print("S2 done: continued the design purely from memory")
    print("RESUME_DEMO_OK")


def orchestrate() -> int:
    """Run session 1 and session 2 in two SEPARATE FreeCAD processes."""
    import shutil
    import subprocess
    if DEMO_DIR.exists():
        shutil.rmtree(DEMO_DIR)
    DEMO_DIR.mkdir(parents=True)

    freecadcmd = (os.environ.get("FREECAD_MCP_FREECAD_BIN")
                  or shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
                  or r"A:\FreeCAD\bin\freecadcmd.exe")
    me = str(Path(__file__).resolve())
    for flag in ("--session1", "--session2"):
        # `--pass` stops freecadcmd from parsing our flag as its own CLI option.
        p = subprocess.run([freecadcmd, me, "--pass", flag], capture_output=True,
                           text=True, timeout=180)
        sys.stdout.write(p.stdout)
        if p.returncode != 0 or ("--session2" == flag and "RESUME_DEMO_OK" not in p.stdout):
            sys.stdout.write(p.stderr)
            print(f"FAIL in {flag}")
            return 1
    return 0


# No `__main__` guard: freecadcmd executes scripts with a different __name__
# (same top-level pattern as the install/ tests).
if "--session1" in sys.argv:
    session1()
elif "--session2" in sys.argv:
    session2()
else:
    _rc = orchestrate()
    if _rc != 0:
        sys.exit(_rc)
