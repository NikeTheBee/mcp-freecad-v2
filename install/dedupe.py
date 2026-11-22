"""Before downloading anything: is it already on this PC?

For each pinned tool of install/tools.lock.json the installer asks here, in this order:
1. already in the workshop and verified (its SHA256 mark): nothing to do;
2. the archive already in <home>/downloads with the right SHA256: unpacked, not downloaded;
3. a copy installed elsewhere on the PC -- PATH, Program Files, the per-user Programs folder,
   your user folder, and three levels under the root of EVERY drive (folder names only:
   nothing is opened, read or run; Windows, recycle bins and the workshop itself are skipped):
   - the SAME version: offered for reuse instead of a download. Default no: such a copy cannot
     be checked against the pinned SHA256, so reusing it is the owner's choice, recorded as
     "external" in atelier.json;
   - another version: reported only (it is not what the reference cases were validated on).

Conda packages need no such check: pixi keeps one cache for every environment and links the
files (hard links on the same drive), so a package shared by ten environments is downloaded
once and stored once. `same_volume` says whether that holds for this home.
Sentinel for the test: DEDUPE_TEST_OK (dedupe_test.py).
"""
from __future__ import annotations

import os
import re
import shutil
import string
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def product_version(dest: str) -> tuple:
    """'tools/FreeCAD-1.1.4' -> ('freecad', '1.1.4'); no version -> (name, '')."""
    leaf = Path(dest).name
    m = re.match(r"(.+?)[-_ ]v?([A-Za-z]?\d[\w.]*)$", leaf)
    return (m.group(1).lower(), m.group(2)) if m else (leaf.lower(), "")


def search_roots() -> List[Path]:
    roots = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(var)
        if v:
            roots.append(Path(v))
    la = os.environ.get("LOCALAPPDATA")
    if la:
        roots.append(Path(la) / "Programs")
    roots.append(Path.home())
    if os.name == "nt":
        roots += [Path(f"{d}:\\") for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]
    seen, out = set(), []
    for r in roots:
        k = os.path.normcase(str(r))
        if k not in seen and r.is_dir():
            seen.add(k)
            out.append(r)
    return out


def _candidates(root: Path, product: str, depth: int = 3, skip: str = "") -> Iterable[Path]:
    """Folders under root, at most `depth` levels down, whose name contains the product."""
    level = [root]
    for _ in range(depth):
        nxt = []
        for d in level:
            try:
                for c in d.iterdir():
                    if (c.is_dir() and not c.name.startswith(("$", ".")) and c.name.lower() not in (
                            "windows", "system volume information", "programdata", "node_modules",
                            "site-packages", "envs", "pkgs", ".git", "winsxs")
                            and not (skip and os.path.normcase(str(c)).startswith(skip))):
                        if product in c.name.lower().replace(" ", "").replace("-", ""):
                            yield c
                        nxt.append(c)
            except (PermissionError, OSError):
                continue
        level = nxt[:4000]


def find_existing(entry: Dict[str, Any], home: Path, roots: Optional[List[Path]] = None) -> List[Dict[str, str]]:
    """Copies of a lock entry's tool outside the workshop: [{path, match: same|other}]."""
    product, version = product_version(entry["dest"])
    product = product.replace(" ", "").replace("-", "")
    binrel = entry["bin"]
    found: Dict[str, Dict[str, str]] = {}
    home_k = os.path.normcase(str(home.resolve()))

    def add(exe: Path):
        try:
            exe = exe.resolve()
        except OSError:
            return
        k = os.path.normcase(str(exe))
        if k.startswith(home_k) or k in found:
            return
        same = bool(version) and version in str(exe)
        found[k] = {"path": str(exe), "match": "same" if same else "other"}

    w = shutil.which(Path(binrel).name)
    if w and product in w.lower().replace(" ", "").replace("-", ""):
        add(Path(w))
    for r in roots if roots is not None else search_roots():
        try:
            r = r.resolve()                     # same spelling as home (8.3 short names...)
        except OSError:
            continue
        for d in _candidates(r, product, skip=home_k):
            exe = d / binrel
            if exe.is_file():
                add(exe)
            else:
                sub = list(d.glob("*/" + binrel))[:3]
                for e in sub:
                    add(e)
    return sorted(found.values(), key=lambda x: (x["match"] != "same", x["path"]))


def same_volume(a: Path, b: Path) -> bool:
    """True when hard links can join the two folders (pixi's cache and the environments)."""
    return os.path.splitdrive(str(a.resolve()))[0].lower() == os.path.splitdrive(str(b.resolve()))[0].lower()
