"""Autosave / crash recovery — never lose work (cahier des charges §1.3-3).

A headless FreeCAD has no GUI autosave: a kernel crash used to lose every
unsaved document (audit 2026-10-11, §2.3). `autosave()` writes a timestamped
copy of a document under /recovery/<doc>/, keeping the newest `keep` copies.
Call it after every validated step (the freecad-layers tools do it for you);
`latest()` / `open_latest()` bring the work back after a crash.
"""
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import FreeCAD  # available inside FreeCAD / freecadcmd

from . import project_dir, safe_out_path, safe_under

KEEP = 5


def _root() -> Path:
    d = project_dir() / "recovery"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _doc_dir(doc_name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", doc_name).strip("._") or "doc"
    d = safe_under(_root(), safe)
    d.mkdir(parents=True, exist_ok=True)
    return d


def autosave(doc=None, keep: int = KEEP, reason: str = "") -> str:
    """Save a recovery copy of `doc` (default: active) and prune old copies."""
    doc = doc or FreeCAD.ActiveDocument
    if doc is None:
        raise ValueError("No document to autosave")
    d = _doc_dir(doc.Name)
    now = time.time()   # one clock read: seconds and milliseconds must agree
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"
    tag = ("-" + re.sub(r"[^A-Za-z0-9_-]+", "_", reason)[:40]) if reason else ""
    # Two saves in the same millisecond (fast machines) get the next sequence
    # number; it sorts after the first, so latest() stays right.
    for seq in range(100):
        try:
            path = safe_out_path(d / f"{stamp}-{seq:02d}{tag}.FCStd", {".fcstd"})
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(f"100 autosaves of {doc.Name} in the same millisecond")
    doc.saveCopy(str(path))
    # safe_out_path reserved the name with an empty file; FreeCAD "backs up"
    # that empty file as <name>.<time>.FCBak on every save. Our copies are the
    # backups here: drop those, or one more piles up per autosave.
    for bak in d.glob("*.FCBak"):
        bak.unlink(missing_ok=True)
    for old in sorted(d.glob("*.FCStd"), reverse=True)[max(keep, 1):]:
        old.unlink(missing_ok=True)
    return str(path)


def list_recovery(doc_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Recovery copies, newest first: [{doc, file, size}]."""
    dirs = [_doc_dir(doc_name)] if doc_name else [p for p in _root().iterdir() if p.is_dir()]
    out = []
    for d in dirs:
        for f in sorted(d.glob("*.FCStd"), reverse=True):
            out.append({"doc": d.name, "file": str(f), "size": f.stat().st_size})
    return sorted(out, key=lambda e: Path(e["file"]).name, reverse=True)


def latest(doc_name: str) -> Optional[str]:
    items = list_recovery(doc_name)
    return items[0]["file"] if items else None


def open_latest(doc_name: str):
    """Open the newest recovery copy of `doc_name` (as a new document)."""
    f = latest(doc_name)
    if f is None:
        raise ValueError(f"no recovery copy for {doc_name!r}")
    return FreeCAD.openDocument(f)


def summary() -> str:
    items = list_recovery()
    if not items:
        return "No recovery copies."
    by_doc: Dict[str, int] = {}
    for e in items:
        by_doc[e["doc"]] = by_doc.get(e["doc"], 0) + 1
    return "Recovery: " + ", ".join(f"{k} ({n})" for k, n in sorted(by_doc.items()))
