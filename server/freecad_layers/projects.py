"""Several projects side by side, each with its own memory, contract, checkpoints,
autosaves and renders.

Layout: <workspace>/projects/<name>/{project_state,checkpoints,recovery,renders};
the workspace root itself is the "default" project (the original single-project
layout, kept as is). <workspace>/projects/current.json names the selected one.

create() never overwrites an existing project, and switch() never deletes
anything: starting a new design no longer wipes the previous design's memory.
Switching projects does not close FreeCAD documents.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from . import (CURRENT_FILE, PROJECT_NAME, PROJECTS, current_project, safe_out_path, safe_under,
               workspace_dir)
from . import state

_NAME = PROJECT_NAME
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if name == "default":
        return name
    if not _NAME.fullmatch(name) or name.lower() in _RESERVED:
        raise ValueError(f"project name {name!r}: 1-40 letters, digits, '-' or '_', "
                         "starting with a letter or digit")
    return name


def _root():
    return workspace_dir() / PROJECTS


def _set_current(name: str) -> None:
    _root().mkdir(parents=True, exist_ok=True)
    p = safe_out_path(_root() / CURRENT_FILE, {".json"}, overwrite=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"name": name}), encoding="utf-8")
    os.replace(tmp, p)


def names() -> List[str]:
    """Existing projects, "default" first."""
    out = ["default"]
    if _root().is_dir():
        out += sorted(d.name for d in _root().iterdir() if d.is_dir() and _NAME.fullmatch(d.name))
    return out


def create(name: str, intent: str = "") -> Dict[str, Any]:
    """Create a new project, select it and start its memory. Refuses an existing one."""
    name = _check_name(name)
    if name == "default":
        raise ValueError("'default' is the workspace root; pick another name")
    d = safe_under(_root(), name)
    if d.exists():
        raise ValueError(f"project {name!r} already exists; use switch to open it")
    d.mkdir(parents=True)
    _set_current(name)
    return state.init_project(name, intent)


def switch(name: str) -> str:
    """Select an existing project ("default" = the workspace root)."""
    name = _check_name(name)
    if name != "default" and not safe_under(_root(), name).is_dir():
        raise ValueError(f"no project {name!r}; existing: {', '.join(names())}")
    _set_current(name)
    return name


def listing() -> str:
    """One line: every project, the selected one starred."""
    cur = current_project()
    return "Projects: " + ", ".join(n + ("*" if n == cur else "") for n in names())
