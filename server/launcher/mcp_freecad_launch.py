#!/usr/bin/env python3
"""Supervised launcher for the FreeCAD MCP bridge (Windows TCP mode).

Registered as the MCP server command instead of the bare upstream bridge.
It replaces the old string patches applied to third-party code
(audit 2026-10-11, §3.8) — nothing is patched at run time; the base is the
vendored server/freecad-mcp (its own changes: CHANGES-NikeTheBee.md):

1. Make sure a headless FreeCAD (AICopilot headless_server.py) listens on the
   port, starting one if needed.
2. Refuse to go on if the port is held by anything else than a FreeCAD
   binary from the configured install, owned by the current user: the bridge
   would otherwise hand the auth token and every command to that process
   (port squatting, audit §2.3).
3. Run the upstream bridge with stdin/stdout passed through (MCP stdio).
4. Supervise: if FreeCAD dies it is restarted at once (backoff 0/2/4/8/16/30 s
   on repeated failures); if a foreign process takes the port, the bridge is
   stopped (fail closed).

Usage:
  python mcp_freecad_launch.py                 # normal: FreeCAD + bridge (MCP stdio)
  python mcp_freecad_launch.py --check         # ensure FreeCAD, print JSON status, exit
  python mcp_freecad_launch.py --supervise 30  # supervise only, for N seconds (tests)

Env: FREECAD_MCP_FREECAD_BIN (freecadcmd path), FREECAD_MCP_PORT (default 23456),
     MCP_FREECAD_BASE (upstream checkout, default <repo>/server/freecad-mcp).
Requires psutil (installed in the bridge venv by install/bootstrap.py).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parents[2]
BASE = Path(os.environ.get("MCP_FREECAD_BASE") or REPO / "server" / "freecad-mcp")
BRIDGE = BASE / "freecad_mcp_server.py"
HEADLESS = BASE / "AICopilot" / "headless_server.py"
PORT = int(os.environ.get("FREECAD_MCP_PORT", "23456"))
HOST = "127.0.0.1"
FREECAD_NAMES = {"freecadcmd.exe", "freecad.exe", "freecadcmd", "freecad"}
BACKOFF_S = (0, 2, 4, 8, 16, 30)
START_TIMEOUT_S = float(os.environ.get("FREECAD_MCP_AUTOSTART_WAIT", "90"))
LOST_RACE_WAIT_S = 30.0   # a FreeCAD that lost the port: wait this long for the winner


class Untrusted(RuntimeError):
    """The port is held by a process that is not our FreeCAD."""


def _log_path() -> Path:
    d = Path.home() / ".freecad-mcp" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "launcher.log"
    if p.exists() and p.stat().st_size > 1_000_000:
        p.replace(p.with_suffix(".log.1"))
    return p


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [launcher pid={os.getpid()} port={PORT}] {msg}"
    with _log_path().open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, file=sys.stderr, flush=True)   # stderr only: stdout is MCP


def prune_logs(days: float | None = None) -> int:
    """Delete the base's operation logs older than N days (they hold every
    command and result in clear text). FREECAD_MCP_LOG_RETENTION_DAYS, default
    14; 0 keeps everything. Returns the number of files removed."""
    if days is None:
        days = float(os.environ.get("FREECAD_MCP_LOG_RETENTION_DAYS", "14"))
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for f in (Path.home() / ".freecad-mcp" / "logs").glob("operations_*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def find_freecadcmd() -> Path | None:
    cands = [os.environ.get("FREECAD_MCP_FREECAD_BIN"),
             shutil.which("freecadcmd"), shutil.which("FreeCADCmd")]
    for c in cands:
        if c and Path(c).is_file():
            return Path(c).resolve()
    return None


def port_owners(port: int = PORT) -> set:
    """PIDs of every process listening on <port> (IPv4 or IPv6, any address):
    a squatter on 0.0.0.0 or ::1 counts as much as one on 127.0.0.1."""
    return {c.pid for c in psutil.net_connections(kind="inet")
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port}


def port_owner(port: int = PORT, freecadcmd: Path | None = None) -> int | None:
    """The single trusted listener's PID, None if nobody listens.
    Raises Untrusted if any listener on the port is not our FreeCAD."""
    owners = port_owners(port)
    if not owners:
        return None
    if freecadcmd is not None:
        for pid in owners:
            why = trust_problem(pid, freecadcmd)
            if why:
                raise Untrusted(why)
    return min(owners)


def trust_problem(pid: int, freecadcmd: Path) -> str | None:
    """None if `pid` is a FreeCAD binary from the configured install run by
    the current user; otherwise a human-readable reason."""
    try:
        p = psutil.Process(pid)
        exe = Path(p.exe()).resolve()
        user = p.username()
    except (psutil.Error, OSError) as e:
        return f"cannot inspect pid {pid}: {e}"
    if exe.name.lower() not in FREECAD_NAMES:
        return f"pid {pid} is {exe.name}, not FreeCAD"
    # The configured exe may be the portable build's root stub, which starts the
    # real <root>\bin\freecadcmd.exe: accept the configured folder and its bin\.
    allowed = {os.path.normcase(str(d)) for d in (freecadcmd.parent, freecadcmd.parent / "bin")}
    if os.path.normcase(str(exe.parent)) not in allowed:
        return f"pid {pid} runs {exe}, outside the configured FreeCAD ({freecadcmd.parent})"
    me = psutil.Process().username()
    if user != me:
        return f"pid {pid} belongs to {user}, not {me}"
    return None


def _alive(timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((HOST, PORT)) == 0


def spawn(freecadcmd: Path) -> subprocess.Popen:
    flags = 0
    if sys.platform == "win32":   # survive the launcher; no console window
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    logf = _log_path().with_name("freecad_headless.log").open("ab")
    # MANAGED: this FreeCAD is started again by us when it exits (supervisor, or
    # the next ensure()), so the base's restart_freecad may restart it by exiting.
    env = dict(os.environ, FREECAD_MCP_PORT=str(PORT), MCP_FREECAD_MANAGED="1")
    # working folder = the workspace, not the addon: a relative file name given
    # to FreeCAD (exportStl("part.stl")) must not land among the server's code
    work = Path(os.environ.get("MCP_FREECAD_PROJECT_DIR") or REPO)
    proc = subprocess.Popen([str(freecadcmd), str(HEADLESS)],
                            cwd=str(work if work.is_dir() else HEADLESS.parent),
                            stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                            creationflags=flags, close_fds=True, env=env)
    log(f"started FreeCAD pid={proc.pid}")
    return proc


def ensure(freecadcmd: Path) -> int:
    """Return the PID of a trusted FreeCAD listening on PORT (start one if
    needed). Raises Untrusted / RuntimeError."""
    owner = port_owner(PORT, freecadcmd)
    if owner is not None:
        return owner
    if not HEADLESS.is_file():
        raise RuntimeError(f"AICopilot headless server not found: {HEADLESS} "
                           "(run install/bootstrap.py)")
    proc = spawn(freecadcmd)
    deadline = time.monotonic() + START_TIMEOUT_S
    exited_at = None
    while time.monotonic() < deadline:
        owner = port_owner(PORT, freecadcmd)
        if owner is not None:
            return owner
        if proc.poll() is not None:
            # ours quit: it may have lost the port to a FreeCAD started at the same
            # moment (the supervisor's, another call's ensure()), which then serves;
            # give that one time to open the port before calling it a failure
            exited_at = exited_at or time.monotonic()
            if time.monotonic() - exited_at > LOST_RACE_WAIT_S:
                raise RuntimeError(f"FreeCAD exited during start (code {proc.returncode}); "
                                   f"see {_log_path().with_name('freecad_headless.log')}")
        time.sleep(0.25)
    raise RuntimeError(f"FreeCAD did not open port {PORT} within {START_TIMEOUT_S:.0f}s")


class Supervisor(threading.Thread):
    """Restart FreeCAD when it dies; stop the bridge if the port is hijacked."""

    def __init__(self, freecadcmd: Path, on_untrusted, interval: float = 1.0):
        super().__init__(daemon=True)
        self.freecadcmd, self.on_untrusted, self.interval = freecadcmd, on_untrusted, interval
        self.stop_evt = threading.Event()
        self.restarts = 0

    def run(self) -> None:
        failures = 0
        while not self.stop_evt.wait(self.interval):
            try:
                owner = port_owner(PORT, self.freecadcmd)
            except Untrusted as e:
                log(f"SECURITY: port {PORT} taken by an untrusted process ({e}); "
                    "stopping the bridge")
                self.on_untrusted(str(e))
                return
            if owner is not None:
                failures = 0
                continue
            delay = BACKOFF_S[min(failures, len(BACKOFF_S) - 1)]
            log(f"FreeCAD is down; restarting in {delay}s")
            if self.stop_evt.wait(delay):
                return
            try:
                ensure(self.freecadcmd)
                self.restarts += 1
                failures = 0
                log("FreeCAD restarted")
            except Untrusted as e:
                log(f"SECURITY: {e}; stopping the bridge")
                self.on_untrusted(str(e))
                return
            except RuntimeError as e:
                failures += 1
                log(f"restart failed ({failures}): {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="ensure FreeCAD, print status, exit")
    ap.add_argument("--supervise", type=float, metavar="SECONDS",
                    help="supervise only (no bridge) for SECONDS")
    args = ap.parse_args(argv)

    freecadcmd = find_freecadcmd()
    if freecadcmd is None:
        log("ERROR: freecadcmd not found (set FREECAD_MCP_FREECAD_BIN)")
        return 2
    try:
        pid = ensure(freecadcmd)
    except Untrusted as e:
        log(f"SECURITY: refusing to start: port {PORT} is held by an untrusted process "
            f"({e}). No token or command was sent.")
        if args.check:
            print(json.dumps({"ok": False, "error": "untrusted", "detail": str(e)}))
        return 3
    except RuntimeError as e:
        log(f"ERROR: {e}")
        if args.check:
            print(json.dumps({"ok": False, "error": "start", "detail": str(e)}))
        return 4

    if args.check:
        print(json.dumps({"ok": True, "pid": pid, "port": PORT}))
        return 0

    if args.supervise is not None:
        sup = Supervisor(freecadcmd, on_untrusted=lambda why: None)
        sup.start()
        time.sleep(args.supervise)
        sup.stop_evt.set()
        print(json.dumps({"ok": True, "restarts": sup.restarts}))
        return 0

    n = prune_logs()
    if n:
        log(f"pruned {n} operation log file(s) past retention")
    env = dict(os.environ, FREECAD_MCP_FREECAD_BIN=str(freecadcmd),
               FREECAD_MCP_PORT=str(PORT))
    bridge = subprocess.Popen([sys.executable, str(BRIDGE)], env=env, cwd=str(BASE))
    stopped = {"why": None}

    def kill_bridge(why: str) -> None:
        stopped["why"] = why
        bridge.terminate()

    sup = Supervisor(freecadcmd, on_untrusted=kill_bridge)
    sup.start()
    try:
        code = bridge.wait()
    except KeyboardInterrupt:
        bridge.terminate()
        code = bridge.wait()
    sup.stop_evt.set()
    return 3 if stopped["why"] else code


if __name__ == "__main__":
    raise SystemExit(main())
