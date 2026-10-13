#!/usr/bin/env python3
"""One-command installer for MCP FREECAD (idempotent; Windows-first, POSIX-aware).

What it does:
  1. Finds FreeCAD (freecadcmd) and its REAL version-specific user Mod dir.
  2. Checks the base server: a modified copy of `blwfish/freecad-mcp` (v8.3.0) is part of
     this repository (server/freecad-mcp, git subtree, LGPL — see its
     CHANGES-NikeTheBee.md). Nothing is downloaded for it.
  3. Creates a dedicated venv (~/.freecad-mcp/venv) with pinned dependencies:
     nothing is installed into your system Python.
  4. Installs the AICopilot addon into the versioned Mod dir (for GUI use).
  5. Registers two MCP servers: `freecad` (supervised launcher + base) and
     `freecad-layers` (verification, contract, checkpoints, BOM, drawings...)
     with Claude Code and/or Claude Desktop.
  6. Optionally clones the workbench grafts, each pinned to a commit.
  7. Runs the test suite.

Every step either succeeds or stops the install with a clear message
(audit 2026-10-11, §3.7). Claude Desktop's config is backed up and written
atomically; an unreadable config is never overwritten.

Usage:
    python install/bootstrap.py [--with-grafts] [--client {auto,code,desktop,both,none}]
                                [--freecad-bin PATH] [--python PATH] [--skip-tests] [--dry-run]
    python install/bootstrap.py --update-base [--base-ref TAG] [--dry-run]
        Merges upstream blwfish/freecad-mcp changes into server/freecad-mcp
        (git subtree pull --squash; our changes are kept, conflicts are shown by git).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE_DIR = REPO / "server" / "freecad-mcp"
LAUNCHER = REPO / "server" / "launcher" / "mcp_freecad_launch.py"
LAYERS_SERVER = REPO / "server" / "layers_mcp" / "freecad_layers_server.py"
# MCP servers registered with the client(s): base tools + the project's layers.
SERVERS = {"freecad": LAUNCHER, "freecad-layers": LAYERS_SERVER}

# Base server: vendored as a git subtree (server/freecad-mcp). Upstream, for --update-base.
BASE_REPO_URL = "https://github.com/blwfish/freecad-mcp"
BASE_BRANCH = "main"
BASE_PREFIX = "server/freecad-mcp"

# Bridge venv dependencies (match the pinned base's pyproject + the launcher).
VENV_DEPS = [
    "mcp>=2.2.0,<3",
    "mcp-events>=0.1.0,<1",
    "mcp-agent-notes @ git+https://github.com/blwfish/mcp-agent-notes.git"
    "@dd83adf8bb97755f91dbfcf857dbaeb987883f8f",
    "psutil>=6,<8",
]

# Grafts: (url, autoload into Mod?, pinned commit)
GRAFTS = {
    "Rocket": ("https://github.com/davesrocketshop/Rocket", True,
               "9e5ca4781e75d77e7494cab3a03743c0c98767b3"),
    "AirPlaneDesign": ("https://github.com/FredsFactory/FreeCAD_AirPlaneDesign", False,
                       "e3a5124ae5f0a071e3f392363788956634f5dc2b"),
    # CROSS robotics: dir name must be the namespace `freecad.cross` (see skill-robotics-ros).
    "freecad.cross": ("https://github.com/galou/freecad.cross", False,
                      "d30a7048a039a30b6e1fb9f36cdf50458c6cdf4c"),
}
HOME = Path.home()
BRIDGE_DIR = HOME / ".freecad-mcp"
VENV = BRIDGE_DIR / "venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


class InstallError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def run(cmd, check: bool = True, **kw) -> subprocess.CompletedProcess:
    log("$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], **kw)
    if check and r.returncode != 0:
        raise InstallError(f"command failed ({r.returncode}): {' '.join(map(str, cmd))}")
    return r


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------
def find_freecadcmd(explicit: str | None) -> Path:
    """explicit arg -> env -> PATH -> common per-OS install paths."""
    cands = [explicit, os.environ.get("FREECAD_MCP_FREECAD_BIN")]
    cands += [shutil.which(n) for n in ("freecadcmd", "FreeCADCmd")]
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        cands += [str(p / "bin" / "freecadcmd.exe")
                  for p in sorted(Path(pf).glob("FreeCAD*"), reverse=True)]
    elif sys.platform == "darwin":
        cands.append("/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd")
    else:
        cands += ["/usr/bin/freecadcmd", "/usr/local/bin/freecadcmd"]
    for c in cands:
        if c and Path(c).is_file():
            return Path(c).resolve()
    raise InstallError("FreeCAD not found. Pass --freecad-bin PATH or set "
                       "FREECAD_MCP_FREECAD_BIN to freecadcmd(.exe).")


def freecad_user_mod_dir(freecadcmd: Path) -> Path:
    """FreeCAD's REAL version-specific user Mod dir (e.g. ...\\FreeCAD\\v1-1\\Mod)."""
    code = "import FreeCAD;print('USERDIR=' + FreeCAD.getUserAppDataDir())"
    p = run([freecadcmd, "-c", code], capture_output=True, text=True, timeout=120)
    line = next((ln for ln in (p.stdout or "").splitlines() if ln.startswith("USERDIR=")), "")
    if not line:
        raise InstallError(f"could not resolve FreeCAD user dir:\n{p.stdout}\n{p.stderr}")
    moddir = Path(line[len("USERDIR="):].strip()) / "Mod"
    moddir.mkdir(parents=True, exist_ok=True)
    return moddir


# --------------------------------------------------------------------------
# Install steps
# --------------------------------------------------------------------------
def checkout_pinned(url: str, dest: Path, sha: str, dry: bool) -> None:
    """Clone or update `dest` to exactly `sha`. Refuses to touch local edits."""
    if dry:
        log(f"DRY: {url} @ {sha[:10]} -> {dest}")
        return
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--quiet", url, dest])
    dirty = run(["git", "-C", dest, "status", "--porcelain", "--untracked-files=no"],
                capture_output=True, text=True).stdout.strip()
    if dirty:
        raise InstallError(f"{dest} has local modifications; commit/stash them or delete "
                           f"the folder, then re-run:\n{dirty}")
    have = run(["git", "-C", dest, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if have != sha:
        if run(["git", "-C", dest, "cat-file", "-e", f"{sha}^{{commit}}"],
               check=False, capture_output=True).returncode != 0:
            run(["git", "-C", dest, "fetch", "--quiet", "origin", sha], check=False)
            run(["git", "-C", dest, "fetch", "--quiet", "--unshallow"], check=False,
                capture_output=True)
            run(["git", "-C", dest, "fetch", "--quiet", "origin"])
        run(["git", "-C", dest, "checkout", "--quiet", sha])
    log(f"{dest.name} pinned at {sha[:10]}")


def check_base() -> None:
    """The base is part of this repository; refuse a partial checkout."""
    needed = ["freecad_mcp_server.py", "mcp_bridge_framing.py", "AICopilot/headless_server.py",
              "AICopilot/freecad_mcp_handler.py"]
    missing = [n for n in needed if not (BASE_DIR / n).is_file()]
    if missing:
        raise InstallError(f"{BASE_DIR} is incomplete (missing {missing}); re-clone the "
                           "repository — the base server ships inside it")
    log(f"base server: {BASE_DIR} (vendored, see CHANGES-NikeTheBee.md)")


def update_base(dry: bool, ref: str = BASE_BRANCH) -> None:
    """Merge upstream blwfish/freecad-mcp into the vendored copy (keeps our changes)."""
    dirty = run(["git", "-C", REPO, "status", "--porcelain", "--untracked-files=no"],
                capture_output=True, text=True).stdout.strip()
    if dirty:
        raise InstallError(f"commit or stash your changes first:\n{dirty}")
    cmd = ["git", "-C", REPO, "subtree", "pull", f"--prefix={BASE_PREFIX}",
           BASE_REPO_URL, ref, "--squash", "-m",
           f"Update {BASE_PREFIX} to upstream {ref} ({BASE_REPO_URL})"]
    if dry:
        log("DRY: " + " ".join(map(str, cmd)))
        return
    if run(cmd, check=False).returncode != 0:
        raise InstallError("upstream merge stopped (conflicts?): fix the files git lists, "
                           "`git add` them, `git commit`, then run install/run_all_tests.py")
    log("base updated. Now: python install/run_all_tests.py, and note the upstream "
        f"version in {BASE_PREFIX}/CHANGES-NikeTheBee.md")


def copy_tree(src: Path, dst: Path, dry: bool) -> None:
    log(f"copy {src} -> {dst}")
    if dry:
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))


def make_venv(python: Path, dry: bool) -> None:
    """Dedicated venv for the bridge + launcher; uses uv when available."""
    if dry:
        log(f"DRY: venv {VENV} with {VENV_DEPS}")
        return
    uv = shutil.which("uv")
    if not VENV_PY.exists():
        if uv:
            run([uv, "venv", "--quiet", "--python", python, VENV])
        else:
            run([python, "-m", "venv", VENV])
    if uv:
        run([uv, "pip", "install", "--quiet", "--python", VENV_PY, *VENV_DEPS])
    else:
        run([VENV_PY, "-m", "pip", "install", "--quiet", "--upgrade", *VENV_DEPS])


def retire_legacy(moddir: Path, dry: bool) -> None:
    """Move aside what older installs left behind (patched bridge copies, the
    old token, an AICopilot in the non-versioned Mod dir FreeCAD 1.x ignores)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    legacy = BRIDGE_DIR / f"legacy-{stamp}"
    old = [BRIDGE_DIR / n for n in ("freecad_mcp_server.py", "mcp_bridge_framing.py",
                                    "freecad_autostart.py", "mcp_auth.py", "auth_token",
                                    "AICopilot")]
    old = [p for p in old if p.exists()]
    plain_mod = moddir.parent.parent / "Mod" / "AICopilot"   # %APPDATA%\FreeCAD\Mod
    if plain_mod.exists() and plain_mod.parent != moddir:
        old.append(plain_mod)
    for p in old:
        log(f"retire legacy {p} -> {legacy}")
        if dry:
            continue
        legacy.mkdir(parents=True, exist_ok=True)
        dest = legacy / (p.name if p.parent == BRIDGE_DIR else f"Mod_{p.name}")
        try:
            shutil.move(str(p), str(dest))
        except OSError as e:   # e.g. a FreeCAD still running from that folder
            log(f"WARNING: could not move {p} ({e}); close FreeCAD and re-run, "
                "or delete it by hand — FreeCAD 1.x does not load it anyway")
        if p.is_dir() and not any(p.iterdir()):
            try:
                p.rmdir()
            except OSError:
                pass


def stop_stale_headless(dry: bool) -> None:
    """Stop headless FreeCAD servers started from an old AICopilot copy: they
    speak the old auth protocol and would hold the port."""
    if not VENV_PY.exists():
        return
    ours = str((BASE_DIR / "AICopilot" / "headless_server.py").resolve())
    code = (
        "import os, sys, psutil\n"
        "ours = os.path.normcase(sys.argv[1]); dry = sys.argv[2] == '1'\n"
        "for p in psutil.process_iter(['cmdline']):\n"
        "    args = [os.path.normcase(a) for a in (p.info.get('cmdline') or [])]\n"
        "    if any(a.endswith('headless_server.py') for a in args) and ours not in args:\n"
        "        print(f'stopping stale headless FreeCAD pid={p.pid}', flush=True)\n"
        "        dry or p.kill()\n"
    )
    run([VENV_PY, "-c", code, ours, "1" if dry else "0"], check=False)


# --------------------------------------------------------------------------
# Client registration
# --------------------------------------------------------------------------
def register_claude_code(freecadcmd: Path, dry: bool) -> bool:
    claude = shutil.which("claude")
    if not claude:
        return False
    for name, script in SERVERS.items():
        if dry:
            log(f"DRY: would register `{name}` with Claude Code")
            continue
        subprocess.run([claude, "mcp", "remove", "-s", "user", name],
                       capture_output=True, text=True)
        run([claude, "mcp", "add", "-s", "user", name,
             "-e", f"FREECAD_MCP_FREECAD_BIN={freecadcmd}",
             "--", VENV_PY, script], capture_output=True, text=True)
        log(f"registered `{name}` with Claude Code (user scope)")
    return True


def claude_desktop_config_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return HOME / ".config" / "Claude" / "claude_desktop_config.json"


def register_claude_desktop(freecadcmd: Path, dry: bool) -> bool:
    cfg = claude_desktop_config_path()
    if not cfg.parent.exists():
        return False  # Claude Desktop not installed
    data = {}
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            raise InstallError(f"{cfg} is not valid JSON ({e}); fix or remove it — "
                               "it was NOT modified") from e
        if not isinstance(data, dict):
            raise InstallError(f"{cfg} does not contain a JSON object; NOT modified")
    for name, script in SERVERS.items():
        data.setdefault("mcpServers", {})[name] = {
            "command": str(VENV_PY),
            "args": [str(script)],
            "env": {"FREECAD_MCP_FREECAD_BIN": str(freecadcmd)},
        }
    if dry:
        log(f"DRY: would update Claude Desktop config at {cfg}")
        return True
    if cfg.exists():
        shutil.copy2(cfg, cfg.with_name(cfg.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    tmp = cfg.with_name(cfg.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, cfg)
    log(f"registered `freecad` with Claude Desktop ({cfg}, previous config backed up)")
    return True


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-grafts", action="store_true")
    ap.add_argument("--client", choices=["auto", "code", "desktop", "both", "none"],
                    default="auto")
    ap.add_argument("--freecad-bin")
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter used to create the venv (3.10+)")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--base-ref", default=BASE_BRANCH,
                    help="upstream branch or release tag for --update-base (e.g. v8.3.0)")
    ap.add_argument("--update-base", action="store_true",
                    help="merge upstream blwfish/freecad-mcp changes, then stop")
    args = ap.parse_args()
    dry = args.dry_run

    if args.update_base:
        try:
            update_base(dry, args.base_ref)
        except InstallError as e:
            log(f"STOPPED: {e}")
            return 1
        return 0

    try:
        freecadcmd = find_freecadcmd(args.freecad_bin)
        log(f"FreeCAD: {freecadcmd}")
        moddir = freecad_user_mod_dir(freecadcmd)
        log(f"User Mod dir: {moddir}")

        check_base()
        make_venv(Path(args.python), dry)
        stop_stale_headless(dry)          # first: a running server locks its folder
        retire_legacy(moddir, dry)
        copy_tree(BASE_DIR / "AICopilot", moddir / "AICopilot", dry)   # GUI use

        if args.with_grafts:
            for name, (url, autoload, sha) in GRAFTS.items():
                dest = REPO / "addons" / name
                checkout_pinned(url, dest, sha, dry)
                if autoload:
                    copy_tree(dest, moddir / name, dry)
                    log(f"activated graft {name} (auto-load)")

        registered = []
        if args.client in ("auto", "code", "both") and register_claude_code(freecadcmd, dry):
            registered.append("Claude Code")
        if args.client in ("desktop", "both") or (args.client == "auto" and not registered):
            if register_claude_desktop(freecadcmd, dry):
                registered.append("Claude Desktop")
        log(f"Registered clients: {registered or 'none (use --client)'}")
    except InstallError as e:
        log(f"STOPPED: {e}")
        return 1

    if not dry and not args.skip_tests:
        log("Running test suite...")
        rc = subprocess.run([str(VENV_PY), str(REPO / "install" / "run_all_tests.py")],
                            env=dict(os.environ, FREECAD_MCP_FREECAD_BIN=str(freecadcmd))
                            ).returncode
        if rc != 0:
            log("Install finished but some tests FAILED (see above).")
            return rc

    log("DONE. Open a NEW client session and ask in natural language, e.g. "
        "'create a 10x20x30mm box and give me its volume'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
