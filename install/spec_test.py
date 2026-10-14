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

    # hole_at: any point of the hole's axis between its ends -- the top face it is
    # drilled from included (it compared the axis midpoint: z = 8 failed on this part)
    def hole_at(center, d=6):
        return spec._eval_spec({"id": "T", "kind": "hole_at", "target": "Body",
                                "params": {"diameter": d, "center": center}})["status"]
    assert hole_at([0, 0, 8]) == verify.OK and hole_at([0, 0, 0]) == verify.OK
    assert hole_at([0, 0, 4]) == verify.OK and hole_at([0, 0, None]) == verify.OK
    assert hole_at([0, 0, 12]) == verify.FAIL      # above the part: not on the hole
    assert hole_at([1, 0, 8]) == verify.FAIL and hole_at([0, 0, 8], d=5) == verify.FAIL
    print("hole_at: entry face, exit face and middle all on the axis; off it: FAIL: OK")

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
    # every malformed spec is refused at require time, with the reason
    bad = [("bbox", {"size": "abc"}), ("bbox", {"size": [60, -1, 8]}),
           ("holes", {"diam": 6}),                      # typo: used to be silently ignored
           ("holes", {"count": -1}), ("holes", {"count": 1, "through": "yes"}),
           ("min_wall", {"min": "x"}), ("min_wall", {"min": 0}),
           ("volume", {"value": None}), ("volume", {"min": 5, "max": 1}),
           ("mass", {"density_g_cm3": 0, "max": 10}), ("solids", {"count": 0}),
           ("hole_at", {"diameter": 5, "center": [None, None, None]}),
           ("no_interference", {"objects": "Body"}), ("no_interference", {"objects": ["A", "A"]})]
    for kind, params in bad:
        try:
            spec.require("Body", kind, **params)
        except ValueError as e:
            assert kind in str(e) or "size" in str(e) or "center" in str(e), (kind, params, e)
        else:
            print(f"FAIL: accepted {kind} {params}"); return 1
    print(f"{len(bad)} malformed specs refused with a reason: OK")
    # a spec saved before validation existed is UNKNOWN for itself, not a crash of check
    state.update(lambda s: s["specs"].append({"id": "S99", "target": "Body", "kind": "bbox",
                                              "params": {"size": "abc"}, "note": "", "at": ""}))
    r = spec.check_all("Body")
    s99 = [x for x in r["results"] if x["id"] == "S99"][0]
    assert s99["status"] == verify.UNKNOWN and "could not evaluate" in s99["message"], s99
    assert r["status"] != verify.OK, r
    spec.remove("S99")
    try:
        spec.remove("S99")
        print("FAIL: removing an unknown spec was silent"); return 1
    except ValueError:
        pass
    print("old poisoned spec -> UNKNOWN for itself; remove() refuses unknown ids: OK")

    # no_interference names the TARGET too: "the plate must not interfere with the pins"
    # checked the pins against each other only, so an oversize pin in the plate passed
    import Part
    for i, (x, r) in enumerate(((-10, 2.0), (20, 2.5))):
        pin = doc.addObject("Part::Feature", f"Pin{i + 1}")
        pin.Shape = Part.makeCylinder(r, 30, V(x, 0, -10))
    doc.recompute()
    sid = spec.require("Body", "no_interference", objects=["Pin1", "Pin2"])
    res = [x for x in spec.check_all("Body")["results"] if x["id"] == sid][0]
    assert res["status"] == verify.FAIL and res["measured"][0]["a"] == "Body", res   # Pin1 in the plate
    try:
        spec.require("Pin1", "no_interference", objects=["Pin1"])
        print("FAIL: a one-part no_interference was accepted"); return 1
    except ValueError:
        pass
    spec.remove(sid)
    print("no_interference includes its target; a single part is refused: OK")

    _tmp.cleanup()
    print("SPEC_OK")
    return 0


# No __main__ guard: freecadcmd executes scripts with a different __name__.
rc = main()
if rc:
    sys.exit(rc)
