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
    p = Path(path).expanduser().resolve()
    exts = {e.lower() if e.startswith(".") else "." + e.lower()
            for e in allowed_exts}
    if p.suffix.lower() not in exts:
        raise ValueError(f"refusing to write {p.name!r}: extension must be "
                         f"one of {sorted(exts)}")
    if p.exists() and not overwrite:
        raise FileExistsError(f"{p} exists; pass overwrite=True to replace it")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


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
