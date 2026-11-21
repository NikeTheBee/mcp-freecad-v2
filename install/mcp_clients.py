"""Register the workshop's two MCP servers with any MCP client, not only Claude.

The servers speak standard MCP over stdio; only the file each client reads differs. This prints
the configuration for a client, or with --write merges it into that client's user config: other
servers in the file are kept, the previous file is backed up, and an unreadable file is never
touched.

    python install/mcp_clients.py --client cursor            # print
    python install/mcp_clients.py --client all --write       # write every client found
    python install/mcp_clients.py --client vscode --home D:/x --write   (tests: another home)

Clients: claude-desktop, cursor, vscode, windsurf, gemini, lmstudio, continue, codex (TOML),
generic (the common `mcpServers` JSON for anything else).
Sentinel for the test: prints MCP_CLIENTS_OK on success.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVERS = {"freecad": REPO / "server" / "launcher" / "mcp_freecad_launch.py",
           "freecad-layers": REPO / "server" / "layers_mcp" / "freecad_layers_server.py"}

# (relative path under the home folder, top-level key, entry style)
CLIENTS = {
    "claude-desktop": ("AppData/Roaming/Claude/claude_desktop_config.json", "mcpServers", "plain"),
    "cursor": (".cursor/mcp.json", "mcpServers", "plain"),
    "vscode": ("AppData/Roaming/Code/User/mcp.json", "servers", "typed"),
    "windsurf": (".codeium/windsurf/mcp_config.json", "mcpServers", "plain"),
    "gemini": (".gemini/settings.json", "mcpServers", "plain"),
    "lmstudio": (".lmstudio/mcp.json", "mcpServers", "plain"),
    "continue": (".continue/mcpServers/mcp-freecad.json", "mcpServers", "plain"),
    "codex": (".codex/config.toml", "mcp_servers", "toml"),
}


def _freecadcmd() -> str:
    env = os.environ.get("FREECAD_MCP_FREECAD_BIN")
    if env:
        return env
    try:
        home = (Path.home() / ".freecad-mcp" / "atelier_home").read_text(encoding="utf-8").strip()
        cfg = json.loads((Path(home) / "atelier.json").read_text(encoding="utf-8"))
        return str(cfg.get("tools", {}).get("freecadcmd", ""))
    except (OSError, ValueError):
        return ""


def _python(home: Path) -> str:
    sub = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return str(Path.home() / ".freecad-mcp" / "venv" / sub)


def entries(style: str, home: Path) -> dict:
    py, fc = _python(home), _freecadcmd()
    out = {}
    for name, script in SERVERS.items():
        e = {"command": py, "args": [str(script)], "env": {"FREECAD_MCP_FREECAD_BIN": fc}}
        if style == "typed":
            e = {"type": "stdio", **e}
        out[name] = e
    return out


def _toml_str(v: str) -> str:
    return json.dumps(v)          # a JSON string is a valid TOML basic string


def toml_block(home: Path) -> str:
    lines = []
    for name, e in entries("plain", home).items():
        lines += [f"[mcp_servers.{json.dumps(name)}]", f"command = {_toml_str(e['command'])}",
                  "args = [" + ", ".join(_toml_str(a) for a in e["args"]) + "]",
                  f"[mcp_servers.{json.dumps(name)}.env]",
                  *(f"{k} = {_toml_str(v)}" for k, v in e["env"].items()), ""]
    return "\n".join(lines)


def _backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}"))


def write(client: str, home: Path) -> Path:
    rel, key, style = CLIENTS[client]
    path = home / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if style == "toml":
        import tomllib
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        if old:
            tomllib.loads(old)          # unreadable -> raises, file untouched
        kept = _strip_toml(old)
        new = (kept.rstrip() + "\n\n" if kept.strip() else "") + toml_block(home)
        tomllib.loads(new)
        _backup(path)
        path.write_text(new, encoding="utf-8")
        return path
    data = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))     # unreadable -> raises, untouched
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object: not modified")
    data.setdefault(key, {}).update(entries(style, home))
    _backup(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _strip_toml(text: str) -> str:
    """Remove our previous [mcp_servers."freecad*"] tables, keep everything else."""
    out, skip = [], False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("["):
            skip = s.startswith(('[mcp_servers."freecad"', '[mcp_servers."freecad-layers"',
                                 "[mcp_servers.freecad]", "[mcp_servers.freecad.",
                                 "[mcp_servers.freecad-layers"))
        if not skip:
            out.append(ln)
    return "\n".join(out)


def render(client: str, home: Path) -> str:
    if client == "generic":
        return json.dumps({"mcpServers": entries("plain", home)}, indent=2)
    rel, key, style = CLIENTS[client]
    body = toml_block(home) if style == "toml" else json.dumps({key: entries(style, home)}, indent=2)
    return f"# {client}: {home / rel}\n{body}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--client", default="generic", choices=sorted(CLIENTS) + ["generic", "all"])
    ap.add_argument("--write", action="store_true", help="merge into the client's config (backup kept)")
    ap.add_argument("--home", default=str(Path.home()), help="home folder (default: yours)")
    a = ap.parse_args()
    home = Path(a.home)
    todo = sorted(CLIENTS) if a.client == "all" else [a.client]
    for c in todo:
        if a.write and c != "generic":
            if a.client == "all" and not (home / CLIENTS[c][0]).parent.parent.exists():
                print(f"[skip] {c}: not installed")
                continue
            print(f"[ok] {c}: {write(c, home)}")
        else:
            print(render(c, home))
    print("MCP_CLIENTS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
