"""MCP FREECAD home-grown layers (cahier des charges §7).

Three token-efficient robustness layers, usable from the MCP server's
execute_python (inside FreeCAD) or standalone via freecadcmd:

- state      : persistent project memory (read at session start, updated per step)
- verify     : compact geometric verification verdicts
- checkpoint : named .FCStd saves + rollback

All paths resolve relative to the repo root (parent of /server), overridable
with the MCP_FREECAD_PROJECT_DIR environment variable.
"""
import os
from pathlib import Path


def project_dir() -> Path:
    """Repo root that holds /project_state and /checkpoints."""
    env = os.environ.get("MCP_FREECAD_PROJECT_DIR")
    if env:
        return Path(env)
    # server/freecad_layers/__init__.py -> repo root is two parents up
    return Path(__file__).resolve().parents[2]


def safe_out_path(path, allowed_exts, overwrite: bool = False) -> Path:
    """Secure-by-design gate for every file the layers WRITE (SECURITY.md).

    - resolves the path (no surprises from `..` / symlinked segments);
    - enforces an allowed extension (a caller asking for "gears.dxf" can't
      end up writing "something.dll");
    - refuses to clobber an existing file unless overwrite=True;
    - creates the parent directory (never a file's parent chain blindly:
      mkdir with parents is bounded by the resolved location).

    Returns the resolved Path. Raises ValueError/FileExistsError on refusal.
    Shared by drawing/bom/... so the policy lives in ONE place.
    """
    raw_name = Path(path).name
    if ":" in raw_name:   # NTFS alternate data stream ("evil.exe:x.csv")
        raise ValueError(f"refusing to write {raw_name!r}: ':' is not allowed in a file name")
    p = Path(path).expanduser().resolve()
    if p.stem.split(".")[0].upper() in _RESERVED:
        raise ValueError(f"refusing to write {p.name!r}: reserved Windows device name")
    exts = {e.lower() if e.startswith(".") else "." + e.lower()
            for e in allowed_exts}
    if p.suffix.lower() not in exts:
        raise ValueError(f"refusing to write {p.name!r}: extension must be "
                         f"one of {sorted(exts)}")
    p.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        try:   # reserve the name atomically: no race between check and write
            p.open("x").close()
        except FileExistsError:
            raise FileExistsError(f"{p} exists; pass overwrite=True to replace it") from None
    return p


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def safe_under(base: Path, candidate) -> Path:
    """Resolve `candidate` and require it to stay under `base` (anti-traversal).

    Use for any name/path a caller supplies that must map into a known
    directory (template names, checkpoint labels used as filenames, ...).
    """
    base = Path(base).resolve()
    p = (base / candidate).resolve()
    if not p.is_relative_to(base):
        raise ValueError(f"path escapes {base}: {candidate!r}")
    return p
