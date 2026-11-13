"""MCP FREECAD home-grown layers (cahier des charges §7).

Three token-efficient robustness layers, usable from the MCP server's
execute_python (inside FreeCAD) or standalone via freecadcmd:

- state      : persistent project memory (read at session start, updated per step)
- verify     : compact geometric verification verdicts
- checkpoint : named .FCStd saves + rollback

All paths resolve under the workspace: the repo root (parent of /server), or
the MCP_FREECAD_PROJECT_DIR environment variable. Each named project lives in
<workspace>/projects/<name>/ (see projects.py); with no project selected, the
workspace root itself is the "default" project (the original layout).
"""
import json
import os
import re
from pathlib import Path

PROJECTS = "projects"
CURRENT_FILE = "current.json"
PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")


def workspace_dir() -> Path:
    """Root holding the default project and /projects."""
    env = os.environ.get("MCP_FREECAD_PROJECT_DIR")
    if env:
        return Path(env)
    # server/freecad_layers/__init__.py -> repo root is two parents up
    return Path(__file__).resolve().parents[2]


def current_project() -> str:
    """Name of the selected project ("default" = the workspace root)."""
    try:
        name = json.loads((workspace_dir() / PROJECTS / CURRENT_FILE).read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        return "default"
    if not isinstance(name, str) or not PROJECT_NAME.fullmatch(name):
        return "default"            # a tampered pointer never leaves /projects
    return name if (workspace_dir() / PROJECTS / name).is_dir() else "default"


def project_dir() -> Path:
    """Folder of the selected project: /project_state, /checkpoints, /recovery, /renders."""
    name = current_project()
    return workspace_dir() if name == "default" else workspace_dir() / PROJECTS / name


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
    # Security audit 2026-11-12: "\\host\share\x.csv", "\\?\UNC\host\...",
    # "\\.\pipe\x.csv" and "\\?\C:\..." were accepted, and the parent mkdir /
    # the name reservation below then reached that host or device: an export
    # path taken from a document or a web page made FreeCAD open an SMB session
    # to a machine of the attacker's choice (Windows sends the user's NTLM
    # credentials with it) and write the part there. A network share the user
    # mapped to a drive letter still works.
    if str(path).replace("/", "\\").startswith("\\\\"):
        raise ValueError(f"refusing to write {str(path)!r}: network (UNC) and device paths "
                         "are not allowed; use a local folder or a mapped drive letter")
    p = Path(path).expanduser()
    if not p.is_absolute():
        # relative to the PROJECT, not to wherever FreeCAD was started: the
        # launcher starts it in the addon folder, so "plan.dxf" landed in the code
        p = project_dir() / p
    p = p.resolve()
    # every component, and the forms Windows also maps to a device: "CON .csv",
    # "COM¹.csv", "CONIN$.csv" and "sub\NUL\x.csv" passed the former check of
    # the file name alone
    for part in (p.parts[1:] if os.name == "nt" else (p.name,)):
        if _device_name(part):
            raise ValueError(f"refusing to write {p.name!r}: {part!r} is a reserved "
                             "Windows device name")
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
             *(f"LPT{i}" for i in range(1, 10)), "CONIN$", "CONOUT$"}
_SUPERSCRIPT_DIGITS = str.maketrans("\u00b9\u00b2\u00b3", "123")


def _device_name(component: str) -> bool:
    """Is this path component a Windows device (CON, NUL, COM1...)? As Windows
    reads it: whatever the case and the extension, with spaces before the dot,
    and with a superscript digit (COM¹ is COM1)."""
    stem = component.split(".")[0].rstrip(" ").upper().translate(_SUPERSCRIPT_DIGITS)
    return stem in _RESERVED


def safe_under(base: Path, candidate) -> Path:
    """Resolve `candidate` and require it to stay under `base` (anti-traversal).

    Use for any name/path a caller supplies that must map into a known
    directory (template names, checkpoint labels used as filenames, ...).
    """
    base = Path(base).resolve()
    # "name.FCStd:stream" stays under `base` but names an NTFS alternate data
    # stream of the file (hidden from a folder listing): refused like in
    # safe_out_path. A leading drive ("C:\...") is judged by the check below.
    rel = str(candidate)
    if ":" in (rel[2:] if rel[1:2] == ":" else rel):
        raise ValueError(f"path names an alternate data stream: {candidate!r}")
    p = (base / candidate).resolve()
    if not p.is_relative_to(base):
        raise ValueError(f"path escapes {base}: {candidate!r}")
    return p
