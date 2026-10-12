"""Isolated FreeCAD test instance — tests never touch the live :23456 one.

Dedicated port (23499) and a private USERPROFILE/HOME, so the instance's
rotating auth token (~/.freecad-mcp/windows_auth_token) and the launcher logs
live in a temp dir. Started through the real launcher (--check), stopped by
PID (never `taskkill /IM`, which would kill the user's other FreeCAD runs).

Run with the bridge venv Python (needs psutil): see VENV_PY.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = REPO / "server" / "freecad-mcp"
LAUNCHER = REPO / "server" / "launcher" / "mcp_freecad_launch.py"
_VENV = Path.home() / ".freecad-mcp" / "venv"
VENV_PY = _VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
TEST_PORT = 23499

sys.path.insert(0, str(BASE))
from mcp_bridge_framing import receive_message, send_message, verify_listener  # noqa: E402


def freecadcmd() -> str:
    return (os.environ.get("FREECAD_MCP_FREECAD_BIN") or shutil.which("freecadcmd")
            or shutil.which("FreeCADCmd") or r"A:\FreeCAD\bin\freecadcmd.exe")


class Instance:
    def __init__(self, port: int = TEST_PORT):
        self.port = port
        self.home = Path(tempfile.mkdtemp(prefix="mcpfc_home_"))
        self.env = dict(os.environ, USERPROFILE=str(self.home), HOME=str(self.home),
                        FREECAD_MCP_PORT=str(port), FREECAD_MCP_FREECAD_BIN=freecadcmd())
        self.pid = None

    def launcher(self, *args, timeout: float = 150) -> subprocess.CompletedProcess:
        return subprocess.run([str(VENV_PY), str(LAUNCHER), *args], env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def start(self) -> int:
        r = self.launcher("--check")
        info = json.loads(r.stdout.strip().splitlines()[-1])
        if not info.get("ok"):
            raise RuntimeError(f"launcher --check failed: {info} {r.stderr[-500:]}")
        self.pid = info["pid"]
        return self.pid

    def token(self) -> str:
        return (self.home / ".freecad-mcp" / "windows_auth_token").read_text().strip()

    def call(self, tool: str, args: dict, token="auto", timeout: float = 60.0):
        payload = {"tool": tool, "args": args}
        if token == "auto":
            payload["token"] = self.token()
        elif token is not None:
            payload["token"] = token
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("127.0.0.1", self.port))
        try:
            if token == "auto":   # real clients check the listener before sending the token
                refusal = verify_listener(s, payload["token"])
                if refusal:
                    return {"error": f"Not sent: {refusal}", "not_sent": True}
            send_message(s, json.dumps(payload))
            resp = receive_message(s, timeout=timeout)
            return json.loads(resp) if resp else None
        finally:
            s.close()

    def alive(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    def kill(self) -> None:
        """Kill whatever FreeCAD currently serves the test port (by PID)."""
        import psutil
        for c in psutil.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == self.port:
                try:
                    psutil.Process(c.pid).kill()
                    psutil.Process(c.pid).wait(10)
                except psutil.Error:
                    pass

    def stop(self) -> None:
        self.kill()
        shutil.rmtree(self.home, ignore_errors=True)
