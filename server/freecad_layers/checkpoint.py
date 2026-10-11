"""Checkpoints / rollback — named .FCStd saves (cahier des charges §7.3).

Return to a clean state when the AI goes astray ("revert to the checkpoint
before the fins"). Each checkpoint is a named .FCStd under /checkpoints plus an
entry in the project state.

Guarantees (audit 2026-10-11, §3.5):
- two different names never share a file (slug + short hash of the exact name);
- restore always reloads the checkpoint FROM DISK, even if a document with
  that file is already open (FreeCAD would otherwise hand back the modified
  in-memory copy);
- nothing is lost by a rollback: with replace_active=True the abandoned
  document is first saved to /recovery (see recovery.py).
"""
import hashlib
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import FreeCAD  # available inside FreeCAD / freecadcmd

from . import project_dir, safe_out_path, safe_under
from . import state as _state


def _ckpt_dir() -> Path:
    d = project_dir() / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip()).strip("._")
    return s[:60] or "checkpoint"


def _file_for(name: str) -> str:
    """Unique, readable file name: 'v1/fins' and 'v1 fins' no longer collide."""
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{_slug(name)}_{h}.FCStd"


def _active(doc=None):
    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        raise ValueError("No active document to checkpoint")
    return doc


def save(name: str, note: str = "", doc=None) -> str:
    """Save the document as a checkpoint and record it. Re-saving the SAME
    name replaces that checkpoint (explicit intent); other names never do."""
    if not name or not name.strip():
        raise ValueError("checkpoint name must not be empty")
    doc = _active(doc)
    fname = _file_for(name)
    path = safe_out_path(_ckpt_dir() / fname, {".fcstd"}, overwrite=True)
    doc.saveCopy(str(path))

    try:
        fc_version = ".".join(str(p) for p in FreeCAD.Version()[:3])
    except Exception:  # noqa: BLE001
        fc_version = "unknown"

    def record(s):
        s["checkpoints"] = [c for c in s["checkpoints"] if c.get("name") != name]
        s["checkpoints"].append({
            "name": name,
            "file": fname,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "note": note,
            "source_doc": doc.Name,
            # .FCStd is version-tied: files saved by a newer FreeCAD may lose
            # objects when reopened in an older one (docs/COMPATIBILITY.md).
            "freecad_version": fc_version,
        })
    _state.update(record)
    return str(path)


def list_checkpoints() -> List[Dict[str, Any]]:
    return _state.load().get("checkpoints", [])


def _path_of(name: str) -> Path:
    entry = next((c for c in list_checkpoints() if c.get("name") == name), None)
    if entry is None:
        raise ValueError(f"Checkpoint {name!r} not found "
                         f"(known: {[c.get('name') for c in list_checkpoints()]})")
    # `entry["file"]` comes from state.json (on-disk, tamperable): anchor it
    # under /checkpoints so a doctored state can't make us open an arbitrary
    # path (secure by design — safe_under).
    path = safe_under(_ckpt_dir(), entry["file"])
    if not path.exists():
        raise ValueError(f"Checkpoint {name!r} file missing: {path}")
    return path


def _same_file(a: str, b: Path) -> bool:
    try:
        return bool(a) and Path(a).resolve() == b.resolve()
    except OSError:
        return False


def restore(name: str, replace_active: bool = False):
    """Reload the named checkpoint from disk and return it (becomes active).

    Any open document backed by the checkpoint file is closed first, so the
    result is always the saved content. With replace_active=True the current
    active document is saved to /recovery and closed (true rollback); by
    default it stays open next to the restored one.
    """
    path = _path_of(name)
    for dname, d in list(FreeCAD.listDocuments().items()):
        if _same_file(d.FileName, path):
            FreeCAD.closeDocument(dname)
    if replace_active and FreeCAD.ActiveDocument is not None:
        from . import recovery
        abandoned = FreeCAD.ActiveDocument
        recovery.autosave(abandoned, reason=f"before-restore-{_slug(name)}")
        FreeCAD.closeDocument(abandoned.Name)
    return FreeCAD.openDocument(str(path))


def summary() -> str:
    cks = list_checkpoints()
    if not cks:
        return "No checkpoints."
    return "\n".join(f"- {c['name']} ({c['at']})" + (f": {c['note']}" if c.get("note") else "")
                     for c in cks)
