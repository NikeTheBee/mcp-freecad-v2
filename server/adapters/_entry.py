"""Entry point of every adapter, run INSIDE a pixi environment:

    <env>/python.exe -I server/adapters/_entry.py <adapter> <input.json> <output.json>
    <env>/python.exe -I server/adapters/_entry.py <adapter> --reference <output.json>
    <env>/python.exe -I server/adapters/_entry.py <adapter> --versions <output.json>

- `adapter` is a module name of this folder (checked against a strict pattern, never a path).
- No network: socket connections are refused unless they go to the loopback interface
  (SITL/Gazebo use 127.0.0.1). This guards the Python libraries; native code is held by the
  adapter calling only known executables with argument lists (no shell).
- The output is always a JSON object with a verdict (OK / FAIL / UNKNOWN); an exception becomes
  UNKNOWN with its message, never a stack trace for the AI to read.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
_NAME = re.compile(r"[a-z][a-z0-9_]{0,40}")


def _guard_network() -> None:
    def _loopback(address) -> bool:
        host = address[0] if isinstance(address, tuple) else address
        if host in ("localhost",):
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address):
            raise PermissionError(f"network disabled in adapters (tried {address!r})")
        return real_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address):
            raise PermissionError(f"network disabled in adapters (tried {address!r})")
        return real_connect_ex(self, address)

    real_gai = socket.getaddrinfo

    def getaddrinfo(host, *a, **kw):
        if host not in (None, "localhost") and not _loopback((host,)):
            raise PermissionError(f"network disabled in adapters (lookup of {host!r})")
        return real_gai(host, *a, **kw)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo


def main(argv) -> int:
    if len(argv) != 4 or not _NAME.fullmatch(argv[1]) or argv[1].startswith("_"):
        print("usage: _entry.py <adapter> <input.json|--reference|--versions> <output.json>")
        return 2
    _guard_network()
    sys.path.insert(0, str(HERE))
    out_path = Path(argv[3])
    t0 = time.time()
    try:
        import importlib
        mod = importlib.import_module(argv[1])
        from _common import run_references
        if argv[2] == "--reference":
            result = run_references(mod, out_path.parent)
        elif argv[2] == "--versions":
            result = {"verdict": "OK", "versions": mod.versions()}
        else:
            inp = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
            work = Path(argv[2]).resolve().parent
            result = mod.run(inp["kind"], inp, work)
            result.setdefault("verdict", "UNKNOWN")
    except Exception as e:  # noqa: BLE001 - reported, not raised
        tb = traceback.extract_tb(e.__traceback__)
        where = f"{Path(tb[-1].filename).name}:{tb[-1].lineno}" if tb else ""
        result = {"verdict": "UNKNOWN", "summary": f"{type(e).__name__}: {e}"[:600],
                  "where": where}
    result["elapsed_s"] = round(time.time() - t0, 2)
    out_path.write_text(json.dumps(result, ensure_ascii=False, default=str, indent=1),
                        encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
