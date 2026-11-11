"""Autosave / crash recovery — never lose work (cahier des charges §1.3-3).

A headless FreeCAD has no GUI autosave: a kernel crash used to lose every
unsaved document (audit 2026-10-11, §2.3). `autosave()` writes a timestamped
copy of a document under /recovery/<doc>/, keeping the newest `keep` copies.
Call it after every validated step (the freecad-layers tools do it for you);
`latest()` / `open_latest()` bring the work back after a crash.
"""
import hashlib
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import FreeCAD  # available inside FreeCAD / freecadcmd

from . import project_dir, safe_out_path, safe_under

KEEP = 5
# Working copies of reopened snapshots (see open_copy). Sanitized document
# names never start with "_", so this is never a document's folder.
_OPENED = "_opened"


def _root() -> Path:
    d = project_dir() / "recovery"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _doc_dir(doc_name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", doc_name).strip("._") or "doc"
    # FreeCAD names are case-sensitive, Windows folders are not: documents "Part"
    # and "part" shared one folder, so recover("Part") reopened the copy of
    # "part" (and each one's autosaves pruned the other's). A name that differs
    # only by case from an existing folder gets its own, "<name>-<hash>" (a
    # FreeCAD document name never holds "-").
    taken = {p.name for p in _root().iterdir()}
    if safe not in taken and safe.lower() in {n.lower() for n in taken}:
        safe += "-" + hashlib.sha1(safe.encode("utf-8")).hexdigest()[:8]
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
    # number; it sorts after the first, so latest() stays right. Whatever their
    # tags: "...-00.FCStd" then "...-00-restored-x.FCStd" both took 00 and the
    # tag ("-" < ".") put the older one first.
    for seq in range(100):
        if any(d.glob(f"{stamp}-{seq:02d}*.FCStd")):
            continue
        try:
            path = safe_out_path(d / f"{stamp}-{seq:02d}{tag}.FCStd", {".fcstd"})
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(f"100 autosaves of {doc.Name} in the same millisecond")
    try:
        doc.saveCopy(str(path))
    except BaseException:
        # the reserved name is still an empty file: left there, it became the
        # newest "copy" and recover() opened nothing
        path.unlink(missing_ok=True)
        raise
    # safe_out_path reserved the name with an empty file; FreeCAD "backs up"
    # that empty file as <name>.<time>.FCBak on every save. Our copies are the
    # backups here: drop those, or one more piles up per autosave.
    for bak in d.glob("*.FCBak"):
        bak.unlink(missing_ok=True)
    for cut in d.glob("*.FCStd"):   # name reserved by a save that a crash cut short
        if cut != path and cut.stat().st_size == 0:
            cut.unlink(missing_ok=True)
    for old in _newest_first(d.glob("*.FCStd"))[max(keep, 1):]:
        old.unlink(missing_ok=True)
    return str(path)


def _newest_first(files) -> List[Path]:
    """Copies ordered by when they were WRITTEN, newest first.

    Not by name: the name holds the local wall-clock time, which goes back an
    hour at the autumn DST change (or when the clock or time zone is set back).
    Measured: the autosave made after the change sorted below the 5 copies of
    the hour before, was pruned the moment it was written, and open_latest()
    reopened an older state. File times are coarse (5 distinct values for 30
    quick writes): copies of the same tick tie, and their name (stamp, then
    sequence number) orders them.
    An empty file is not a copy: the name reserved by an autosave that a crash
    cut short (FreeCAD killed during saveCopy).
    """
    keyed = []
    for f in files:
        try:
            st = f.stat()
        except OSError:          # pruned meanwhile
            continue
        if st.st_size:
            keyed.append((st.st_mtime_ns, f.name, f))
    return [f for _, _, f in sorted(keyed, reverse=True)]


def list_recovery(doc_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Recovery copies, newest first: [{doc, file, size}]."""
    dirs = [_doc_dir(doc_name)] if doc_name else [p for p in _root().iterdir()
                                                  if p.is_dir() and not p.name.startswith("_")]
    return [{"doc": f.parent.name.split("-")[0], "file": str(f), "size": f.stat().st_size}
            for f in _newest_first(f for d in dirs for f in d.glob("*.FCStd"))]


def latest(doc_name: str) -> Optional[str]:
    items = list_recovery(doc_name)
    return items[0]["file"] if items else None


def open_latest(doc_name: str):
    """Open the newest recovery copy of `doc_name` as document `doc_name`."""
    f = latest(doc_name)
    if f is None:
        raise ValueError(f"no recovery copy for {doc_name!r}")
    # the working-copy folder follows the copies' folder: "recovery-Part" and
    # "recovery-part" are one folder on Windows too
    return open_copy(f, doc_name, origin=f"recovery-{_doc_dir(doc_name).name}")


def open_copy(path, doc_name: str, origin: str):
    """Open the snapshot at `path` (a checkpoint or an autosave) as document
    `doc_name`, working on a copy under recovery/_opened/<origin>/.

    Opened directly, the snapshot became the document's own file, so a later
    save overwrote the checkpoint or the autosave; and the document took the
    file's name ("_20261013_..."), so its autosaves went to another folder and
    recover(doc) no longer found the latest work. Documents still open from an
    earlier copy of the same origin are autosaved, then closed: the result is
    always the saved content.
    """
    name = re.sub(r"[^A-Za-z0-9_]+", "_", doc_name).strip("_") or "Restored"
    folder = safe_under(safe_under(_root(), _OPENED),
                        re.sub(r"[^A-Za-z0-9_.-]+", "_", origin).strip("._") or "copy")
    for dname, doc in list(FreeCAD.listDocuments().items()):
        if doc.FileName and os.path.normcase(os.path.dirname(os.path.abspath(doc.FileName)))                 == os.path.normcase(str(folder)):
            autosave(doc, reason="before-reopen")
            FreeCAD.closeDocument(dname)
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{name}.FCStd"
    shutil.copyfile(str(path), str(dst))
    return FreeCAD.openDocument(str(dst))


def summary() -> str:
    items = list_recovery()
    if not items:
        return "No recovery copies."
    by_doc: Dict[str, int] = {}
    for e in items:
        by_doc[e["doc"]] = by_doc.get(e["doc"], 0) + 1
    return "Recovery: " + ", ".join(f"{k} ({n})" for k, n in sorted(by_doc.items()))
