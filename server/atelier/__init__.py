"""Engineering workshop: simulations and studies outside FreeCAD (FEUILLE_DE_ROUTE_ATELIER.md §3).

Pure standard library: this package runs inside the freecad-layers MCP server (its own venv). The
tools themselves run in separate pixi environments as subprocesses (server/adapters/), never
imported here.

Where things live:
- MCPFC_HOME (env var) or the path written by install/install.ps1 in ~/.freecad-mcp/atelier_home:
  pixi, the environments, downloaded tools, the validation results. Heavy files never go into the
  repo nor, by default, onto the system disk.
- <home>/atelier.json: what install.ps1 found (python of each environment, tool executables).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parents[2]
ADAPTERS = REPO / "server" / "adapters"
POINTER = Path.home() / ".freecad-mcp" / "atelier_home"


def home() -> Optional[Path]:
    """The workshop root, or None when nothing was installed."""
    env = os.environ.get("MCPFC_HOME")
    if env:
        return Path(env)
    try:
        p = Path(POINTER.read_text(encoding="utf-8").strip())
    except OSError:
        return None
    return p if p.is_absolute() else None


def config() -> Dict[str, Any]:
    """<home>/atelier.json, or {} when absent or unreadable."""
    h = home()
    if h is None:
        return {}
    try:
        data = json.loads((h / "atelier.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def env_python(env: str) -> Optional[Path]:
    """python.exe of a pixi environment, from atelier.json, if it exists."""
    p = (config().get("envs") or {}).get(env)
    if not p:
        return None
    exe = Path(p) / ("python.exe" if os.name == "nt" else "bin/python")
    return exe if exe.is_file() else None


def env_prefix(env: str) -> Optional[Path]:
    p = (config().get("envs") or {}).get(env)
    return Path(p) if p and Path(p).is_dir() else None


def tool_path(name: str) -> Optional[Path]:
    """An executable installed outside conda (tools.lock.json), if present."""
    p = (config().get("tools") or {}).get(name)
    return Path(p) if p and Path(p).exists() else None
