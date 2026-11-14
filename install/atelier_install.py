"""Install the engineering workshop in one command (FEUILLE_DE_ROUTE_ATELIER.md §8.2).

    powershell -ExecutionPolicy Bypass -File install\\install.ps1 -Home D:\\Atelier -Packs tout
    (or: py -3 install/atelier_install.py --home D:\\Atelier --packs socle,marin,elec)

1. pixi, pinned, SHA256 checked (install/tools.lock.json).
2. `pixi install --locked` for the environments of the chosen packs (packs/packs.json): exactly the
   packages and hashes of pixi.lock, in <home>/envs (never in the repo, never in FreeCAD's Python).
3. The tools outside conda of those packs (tools.lock.json): official URL, SHA256 checked BEFORE
   extraction, archive members checked against path traversal.
4. <home>/atelier.json (where each environment and tool is) + ~/.freecad-mcp/atelier_home.
5. The reference cases of every adapter whose environment is installed; an adapter that fails
   stays unusable (simulate refuses it).
6. --register: re-registers the MCP servers on the workshop's FreeCAD (install/bootstrap.py).

Idempotent: what is installed and matches its hash is skipped. Stops at the first error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
LOCK = json.loads((HERE / "tools.lock.json").read_text(encoding="utf-8"))
PACKS = json.loads((REPO / "packs" / "packs.json").read_text(encoding="utf-8"))
POINTER = Path.home() / ".freecad-mcp" / "atelier_home"
MARK = ".mcpfc_sha256"


def say(msg: str) -> None:
    print(f"[atelier] {msg}", flush=True)


def default_home() -> Path:
    if os.environ.get("MCPFC_HOME"):
        return Path(os.environ["MCPFC_HOME"])
    try:
        return Path(POINTER.read_text(encoding="utf-8").strip())
    except OSError:
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "mcp-freecad-atelier"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(name: str, home: Path, dry: bool) -> Path:
    t = LOCK[name]
    d = home / "downloads" / name
    f = d / t["url"].rsplit("/", 1)[1]
    if f.is_file() and sha256(f) == t["sha256"]:
        return f
    if dry:
        say(f"would download {t['url']}")
        return f
    if not t["url"].startswith("https://"):
        raise SystemExit(f"{name}: refusing a non-https URL")
    d.mkdir(parents=True, exist_ok=True)
    say(f"downloading {name} {t['version']} ({t['url']})")
    tmp = f.with_suffix(f.suffix + ".part")
    req = urllib.request.Request(t["url"], headers={"User-Agent": "mcp-freecad-atelier-installer"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as out:
        shutil.copyfileobj(r, out, 1 << 20)
    got = sha256(tmp)
    if got != t["sha256"]:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"{name}: SHA256 mismatch (got {got}, want {t['sha256']}): NOT installed")
    tmp.replace(f)
    return f


def _safe_members(names, root: Path) -> None:
    for n in names:
        p = (root / n).resolve()
        if not p.is_relative_to(root.resolve()):
            raise SystemExit(f"archive member escapes the folder: {n!r}")


def install_tool(name: str, home: Path, dry: bool) -> Path:
    t = LOCK[name]
    dest = home / t["dest"]
    exe = dest / t["bin"]
    mark = dest / MARK
    if exe.exists() and mark.is_file() and mark.read_text().strip() == t["sha256"]:
        return exe
    f = download(name, home, dry)
    if dry:
        return exe
    say(f"installing {name} into {dest}")
    with tempfile.TemporaryDirectory(dir=home / "downloads") as tmpd:
        tmp = Path(tmpd)
        if t["archive"] == "zip":
            with zipfile.ZipFile(f) as z:
                _safe_members(z.namelist(), tmp)
                z.extractall(tmp)
        elif t["archive"] == "7z":
            listing = subprocess.run([_bsdtar(), "-tf", str(f)], capture_output=True, text=True, check=True)
            _safe_members(listing.stdout.splitlines(), tmp)
            subprocess.run([_bsdtar(), "-xf", str(f), "-C", str(tmp)], check=True)
        elif t["archive"] == "file":
            shutil.copy2(f, tmp / f.name)
        else:
            raise SystemExit(f"{name}: unknown archive type {t['archive']}")
        src = tmp
        for _ in range(int(t.get("strip", 0))):
            kids = [k for k in src.iterdir()]
            if len(kids) != 1 or not kids[0].is_dir():
                raise SystemExit(f"{name}: cannot strip one level ({len(kids)} entries)")
            src = kids[0]
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    if not exe.exists():
        raise SystemExit(f"{name}: {t['bin']} not found after extraction")
    mark.write_text(t["sha256"])
    return exe


def _bsdtar() -> str:
    p = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tar.exe"
    if not p.is_file():
        raise SystemExit("Windows tar.exe (bsdtar, reads .7z) not found")
    return str(p)


def pixi_env(home: Path) -> dict:
    return dict(os.environ, PIXI_HOME=str(home / "pixi"), PIXI_CACHE_DIR=str(home / "cache" / "pixi"),
                RATTLER_CACHE_DIR=str(home / "cache" / "rattler"), UV_CACHE_DIR=str(home / "cache" / "uv"))


def install_envs(pixi: Path, home: Path, envs, dry: bool) -> dict:
    cfg = REPO / ".pixi" / "config.toml"
    if not dry:
        cfg.parent.mkdir(exist_ok=True)
        cfg.write_text(f'detached-environments = "{(home / "envs").as_posix()}"\n', encoding="utf-8")
    for e in envs:
        say(f"environment {e} (pixi install --locked)")
        if not dry:
            r = subprocess.run([str(pixi), "install", "--locked", "-e", e], cwd=REPO, env=pixi_env(home))
            if r.returncode:
                raise SystemExit(f"pixi install failed for {e}")
    if dry:
        return {}
    info = json.loads(subprocess.run([str(pixi), "info", "--json"], cwd=REPO, env=pixi_env(home),
                                     capture_output=True, text=True, check=True).stdout)
    return {i["name"]: str(Path(i["prefix"])) for i in info.get("environments_info", [])
            if i["name"] in envs and i.get("prefix") and Path(i["prefix"]).is_dir()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--home", type=Path, default=None, help="workshop root (default: MCPFC_HOME / previous)")
    ap.add_argument("--packs", default="socle", help="comma list, or 'tout' for every pack")
    ap.add_argument("--register", action="store_true", help="register the MCP servers on this FreeCAD")
    ap.add_argument("--skip-validate", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    home = (a.home or default_home()).resolve()
    packs = list(PACKS) if a.packs.strip() == "tout" else ["socle", *[p.strip() for p in a.packs.split(",")]]
    packs = [p for p in dict.fromkeys(packs) if not p.startswith("_")]
    unknown = [p for p in packs if p not in PACKS]
    if unknown:
        raise SystemExit(f"unknown pack(s) {unknown}; known: {[k for k in PACKS if not k.startswith('_')]}")
    envs = list(dict.fromkeys(e for p in packs for e in PACKS[p]["envs"]))
    tools = list(dict.fromkeys(t for p in packs for t in PACKS[p]["tools"]))
    say(f"home {home} | packs {', '.join(packs)} | envs {', '.join(envs)}")
    home.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(home).free / 2**30
    if free < 40:
        say(f"WARNING only {free:.0f} GB free on {home.anchor}: the full workshop needs ~40 GB")
    pixi = install_tool("pixi", home, a.dry_run)
    tool_paths = {t: str(install_tool(t, home, a.dry_run)) for t in tools if t != "pixi"}
    env_paths = install_envs(pixi, home, envs, a.dry_run)
    if a.dry_run:
        say("dry run: nothing changed")
        return 0
    old = {}
    try:
        old = json.loads((home / "atelier.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    cfg = {"home": str(home), "packs": sorted(set(old.get("packs", [])) | set(packs)),
           "envs": {**old.get("envs", {}), **env_paths},
           "tools": {**old.get("tools", {}), **tool_paths, "pixi": str(pixi)}}
    if "freecad" in cfg["tools"]:
        cfg["tools"]["freecadcmd"] = cfg["tools"].pop("freecad")
    (home / "atelier.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    POINTER.parent.mkdir(parents=True, exist_ok=True)
    POINTER.write_text(str(home), encoding="utf-8")
    say(f"configuration written: {home / 'atelier.json'}")
    if not a.skip_validate:
        sys.path.insert(0, str(REPO / "server"))
        os.environ["MCPFC_HOME"] = str(home)
        from atelier import runner
        bad = []
        for name, st in sorted(runner.status()["adapters"].items()):
            if st.get("state") == "env missing":
                continue
            v = runner.validate(name, st["env"])
            say(f"reference cases {name}: {v.get('verdict')} {v.get('summary', '')}")
            if v.get("verdict") != "OK":
                bad.append(name)
        if bad:
            say(f"NOT validated (unusable until fixed): {', '.join(bad)}")
    if a.register and "freecadcmd" in cfg["tools"]:
        say("registering the MCP servers on the workshop's FreeCAD")
        r = subprocess.run([sys.executable, str(HERE / "bootstrap.py"), "--freecad-bin",
                            cfg["tools"]["freecadcmd"], "--skip-tests"])
        if r.returncode:
            raise SystemExit("bootstrap.py failed")
    say("done. In Claude Code: tools_status()")
    return 0


if __name__ == "__main__":
    sys.exit(main())
