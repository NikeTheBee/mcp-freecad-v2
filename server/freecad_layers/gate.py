"""The step gate: one call that decides whether a modeling step is accepted.

Combines the geometry verdict (or the sketch diagnosis for a sketch) with the
user's recorded requirements (spec.py) and autosaves the document only when
everything passes. Used by the `check` tool of the freecad-layers MCP server.
"""
from __future__ import annotations

from typing import Any, Dict

import FreeCAD

from . import recovery, spec, state, verify


def check_target(name: str, expect_solid: bool = True, autosave: bool = True) -> Dict[str, Any]:
    """Returns {status, text, autosaved}. status is OK only if every check passed."""
    obj = verify._resolve(name)
    if obj.TypeId == "Sketcher::SketchObject":
        r = verify.sketch_check(obj)
        return {"status": r["status"], "text": verify.sketch_verdict(obj), "autosaved": None}

    v = verify.check(obj, expect_solid=expect_solid)
    lines = [verify.verdict(obj, expect_solid=expect_solid)]
    status = v["status"]
    if any(s["target"] == obj.Name or s["target"] == name for s in state.load()["specs"]):
        c = spec.check_all(name)
        lines.append(spec.verdict(name))
        if c["status"] == verify.FAIL or status == verify.FAIL:
            status = verify.FAIL
        elif c["status"] == verify.UNKNOWN:
            status = verify.UNKNOWN
    saved = None
    if autosave and status == verify.OK and FreeCAD.ActiveDocument is not None:
        saved = recovery.autosave(obj.Document)
    return {"status": status, "text": "\n".join(lines), "autosaved": saved}
