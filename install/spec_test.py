"""Design contract (audit §9 F3) — runs under freecadcmd.

Replays the audit's natural-language scenario "60x40x8 plate with one Ø6
through-hole in the centre": the upstream `hole` tool left the PartDesign
Body without the hole and verify said OK. The contract must catch it.
Sentinel: SPEC_OK
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))
_tmp = tempfile.TemporaryDirectory(prefix="mcpfc_spec_")
os.environ["MCP_FREECAD_PROJECT_DIR"] = _tmp.name

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402
from freecad_layers import spec, state, verify  # noqa: E402

V = App.Vector


def main() -> int:
    state.init_project("Plate", intent="60x40x8 plate, one D6 through hole in the centre")
    spec.require("Body", "bbox", size=[60, 40, 8])
    spec.require("Body", "holes", count=1, diameter=6, through=True)
    spec.require("Body", "hole_at", diameter=6, center=[0, 0, None])
    spec.require("Body", "mass", density_g_cm3=2.7, max=60, note="aluminium, < 60 g")
    print("summary:", state.summary().splitlines()[1:3])

    doc = App.newDocument("Contract")
    body = doc.addObject("PartDesign::Body", "Body")
    box = body.newObject("PartDesign::AdditiveBox", "Plate")
    box.Length, box.Width, box.Height = 60, 40, 8
    box.Placement.Base = V(-30, -20, 0)
    doc.recompute()

    # the hole is missing from the Body (what the upstream tool produced)
    v = spec.verdict("Body")
    print(v)
    assert v.startswith("FAIL") and "holes" in v, v
    assert verify.verdict("Body").startswith("OK")  # geometry is valid, just WRONG
    print("missing hole caught by the contract although the geometry is valid: OK")

    # an off-centre hole is still wrong
    off = body.newObject("PartDesign::SubtractiveCylinder", "Hole")
    off.Radius, off.Height = 3, 20
    off.Placement.Base = V(10, 0, -5)
    doc.recompute()
    v = spec.verdict("Body")
    print(v)
    assert v.startswith("FAIL") and "hole_at" in v and "holes" not in v.split("|")[1], v

    # correct part -> OK on every requirement
    off.Placement.Base = V(0, 0, -5)
    doc.recompute()
    r = spec.check_all("Body")
    print(spec.verdict("Body"))
    assert r["status"] == verify.OK and r["checked"] == 4, r

    # requirements survive a "new session" (they live in state.json)
    assert len(spec.list_specs()) == 4
    # unknown target -> FAIL, never silently skipped
    spec.require("Lid", "valid")
    assert spec.check_all()["status"] == verify.FAIL
    try:
        spec.require("Body", "bbox", size=[1, 2])
        print("FAIL: malformed spec accepted"); return 1
    except ValueError:
        print("malformed spec refused: OK")

    _tmp.cleanup()
    print("SPEC_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.exit(rc)
