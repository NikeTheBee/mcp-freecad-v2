"""Verified recipes: a validated design kept as a reusable starting point (audit §9, F14).

save() keeps the current project's recorded steps (record_step) and its design
contract (require) as <workspace>/recipes/<name>.json, but ONLY when that
contract checks OK on the live document right now. An unverified sequence is
refused, so a recipe is always one that produced the right part at least once.

show() gives the AI a compact plan to follow for a similar part:
- the steps, in order, with their parameters;
- the requirements to record again (adapted to the new dimensions);
- the verdict the original got.
The recipe is guidance, not a macro: each step is still checked when replayed.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Tuple

from . import PROJECT_NAME, safe_out_path, safe_under, workspace_dir
from . import state


def _dir():
    return workspace_dir() / "recipes"


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not PROJECT_NAME.fullmatch(name):
        raise ValueError(f"recipe name {name!r}: 1-40 letters, digits, '-' or '_'")
    return name


def save(name: str, description: str = "", overwrite: bool = False) -> Dict[str, Any]:
    """Keep the current project as a recipe if, and only if, its contract is OK now."""
    import FreeCAD as App

    from . import spec

    name = _check_name(name)
    s = state.load()
    if not s["features"]:
        raise ValueError("no recorded steps to keep: record_step after each validated step")
    r = spec.check_all()
    if not r["checked"]:
        raise ValueError("no design contract: record the requirements with require() first "
                         "(a recipe is only kept once it is verified)")
    if r["status"] != spec.OK:
        raise ValueError(f"not verified, so not kept: {spec.verdict()}")
    # the requirements can all hold on a broken solid (a self-intersecting part with
    # the right size): its geometry must be OK too
    from . import verify
    for target in sorted({x["target"] for x in s["specs"]}):
        try:
            v = verify.check(target)
        except ValueError:
            continue                    # not an object (an assembly-wide requirement)
        if v["status"] != verify.OK:
            raise ValueError(f"not verified, so not kept: {verify.verdict(target)}")
    data = {
        "recipe": name,
        "description": description,
        "saved": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "from_project": s["project"],
        "intent": s.get("intent", ""),
        "constraints": s.get("constraints", []),
        "parameters": s.get("parameters", {}),
        "steps": [{"name": f["name"], "type": f["type"], "params": f.get("params", {})}
                  for f in s["features"]],
        "requirements": [{"target": x["target"], "kind": x["kind"], "params": x.get("params", {}),
                          "note": x.get("note", "")} for x in s["specs"]],
        "verified": spec.verdict(),
        "freecad": ".".join(App.Version()[:3]),
    }
    path = safe_out_path(_dir() / f"{name}.json", {".json"}, overwrite=overwrite)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def names() -> List[Tuple[str, str]]:
    """[(name, description)] of the saved recipes."""
    out = []
    if _dir().is_dir():
        for p in sorted(_dir().glob("*.json")):
            try:
                out.append((p.stem, json.loads(p.read_text(encoding="utf-8")).get("description", "")))
            except (OSError, ValueError):
                out.append((p.stem, "(unreadable)"))
    return out


def load(name: str) -> Dict[str, Any]:
    p = safe_under(_dir(), f"{_check_name(name)}.json")
    if not p.is_file():
        raise ValueError(f"no recipe {name!r}; saved: {', '.join(n for n, _ in names()) or 'none'}")
    return json.loads(p.read_text(encoding="utf-8"))


def show(name: str) -> str:
    """Compact plan: steps with parameters, requirements to re-record, original verdict."""
    d = load(name)
    lines = [f"Recipe {d['recipe']}: {d.get('description') or d.get('intent', '')}",
             f"verified {d['saved'][:10]} on FreeCAD {d.get('freecad')}: {d['verified']}"]
    if d.get("constraints"):
        lines.append("Constraints: " + "; ".join(d["constraints"]))
    lines.append("Steps (replay in order, check each one):")
    for i, st in enumerate(d["steps"], 1):
        lines.append(f"  {i}. {st['name']} [{st['type']}] {json.dumps(st['params'], ensure_ascii=False)}")
    lines.append("Requirements (record again with require, adapted to the new part):")
    for q in d["requirements"]:
        lines.append(f"  - {q['kind']} on {q['target']} {json.dumps(q['params'], ensure_ascii=False)}"
                     + (f" ({q['note']})" if q.get("note") else ""))
    return "\n".join(lines)


def listing() -> str:
    items = names()
    return "Recipes: " + (", ".join(f"{n} ({d})" if d else n for n, d in items) if items else "none")
