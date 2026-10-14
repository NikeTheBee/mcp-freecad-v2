"""Launcher + socket auth (replaces autostart_test / auth_test) — bridge venv Python.

On an isolated instance (port 23499, private home):
1. the launcher starts FreeCAD and reports a trusted PID;
2. the upstream auth: no token / wrong token rejected, right token accepted;
3. FreeCAD killed -> the supervisor restarts it at once (audit: 2-minute lockout);
4. a foreign process on the port -> the launcher refuses, and nothing is sent to it
   (audit: the token leaked to a port squatter).
Sentinel: LAUNCHER_OK
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fc_instance import LAUNCHER, VENV_PY, Instance  # noqa: E402


def main() -> int:
    inst = Instance()
    try:
        t0 = time.monotonic()
        pid = inst.start()
        print(f"1. FreeCAD started by the launcher: pid={pid} in {time.monotonic() - t0:.1f}s")

        r = inst.call("execute_python_sync", {"code": "1+1"}, token=None)
        assert r and "error" in r and "result" not in r, r
        r = inst.call("execute_python_sync", {"code": "1+1"}, token="0" * 64)
        assert r and "error" in r and "result" not in r, r
        r = inst.call("execute_python_sync", {"code": "1+1"})
        assert r and r.get("result") == "2", r
        print("2. upstream auth: no/wrong token rejected, right token accepted: OK")
        # FreeCAD does not work in the addon folder: a relative export would land in the code
        import psutil
        cwd = Path(psutil.Process(pid).cwd()).resolve()
        assert cwd == LAUNCHER.parents[2].resolve(), cwd
        print(f"2a. FreeCAD works in the workspace ({cwd.name}), not among the server's code: OK")
        # a second FreeCAD started on the same port (supervisor + ensure() during a restart)
        # loses the port and exits: it must not overwrite the running one's token (it did,
        # and every client was refused "did not prove it is your FreeCAD" until a restart)
        from _fc_instance import BASE, freecadcmd
        tok = inst.token()
        second = subprocess.run([freecadcmd(), str(BASE / "AICopilot" / "headless_server.py")],
                                env=inst.env, capture_output=True, timeout=120)
        assert second.returncode != 0 and inst.token() == tok, (second.returncode, inst.token() == tok)
        r = inst.call("execute_python_sync", {"code": "3+3"})
        assert r and r.get("result") == "6", r
        print("2c. a second FreeCAD that loses the port leaves the token alone: OK")

        # trust rule: the configured exe's folder, or its bin\ (portable build's root stub)
        sys.path.insert(0, str(LAUNCHER.parent))
        import mcp_freecad_launch as launcher
        import psutil
        real = Path(psutil.Process(pid).exe())
        assert launcher.trust_problem(pid, real) is None
        assert launcher.trust_problem(pid, real.parent.parent / real.name) is None   # root stub
        other = launcher.trust_problem(pid, Path(inst.home) / "bin" / real.name)
        assert other and "outside the configured FreeCAD" in other, other
        print("2b. trust: configured folder or its bin\\ accepted, any other folder refused: OK")

        sup = subprocess.Popen([str(VENV_PY), str(LAUNCHER), "--supervise", "60"],
                               env=inst.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
        time.sleep(2)
        inst.kill()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 60:
            if inst.alive():
                break
            time.sleep(0.5)
        took = time.monotonic() - t0
        assert inst.alive(), "FreeCAD was not restarted"
        r = inst.call("execute_python_sync", {"code": "2+2"})
        assert r and r.get("result") == "4", r
        print(f"3. crash -> back and answering in {took:.1f}s (was a 120 s lockout)")
        sup.terminate()
        sup.wait(10)

        # two starts at once (the supervisor and another call's ensure() during a restart):
        # one FreeCAD wins the port, the other quits -- and that caller must still get the
        # winner, not "FreeCAD exited during start"
        inst.kill()
        time.sleep(1)
        both = [subprocess.Popen([str(VENV_PY), str(LAUNCHER), "--check"], env=inst.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(2)]
        infos = [json.loads(p.communicate(timeout=180)[0].strip().splitlines()[-1]) for p in both]
        assert all(i.get("ok") for i in infos) and infos[0]["pid"] == infos[1]["pid"], infos
        r = inst.call("execute_python_sync", {"code": "5+5"})
        assert r and r.get("result") == "10", r
        print("3b. two simultaneous starts: both callers get the one FreeCAD that won: OK")

        inst.kill()
        time.sleep(1)
        fake = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        fake.bind(("127.0.0.1", inst.port))
        fake.listen(5)
        fake.settimeout(0.2)
        r = inst.launcher("--check")
        got = []
        try:
            while True:
                c, _ = fake.accept()
                c.settimeout(0.5)
                try:
                    got.append(c.recv(65536))
                except OSError:
                    got.append(b"")
                c.close()
        except OSError:
            pass
        fake.close()
        info = json.loads(r.stdout.strip().splitlines()[-1])
        print("4.", r.returncode, info)
        assert r.returncode == 3 and info["error"] == "untrusted", (r.returncode, info)
        assert not any(got), f"data was sent to the squatter: {got}"
        print("4. port squatter refused, nothing sent to it: OK")

        # 5. operation logs past retention are pruned (they contain designs)
        import os
        sys.path.insert(0, str(LAUNCHER.parent))
        logs = inst.home / ".freecad-mcp" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        old, new = logs / "operations_20200101.json", logs / "operations_today.json"
        old.write_text("{}")
        new.write_text("{}")
        os.utime(old, (time.time() - 30 * 86400,) * 2)
        r = subprocess.run([str(VENV_PY), "-c",
                            "import mcp_freecad_launch as m; print(m.prune_logs(14))"],
                           env=inst.env, cwd=str(LAUNCHER.parent),
                           capture_output=True, text=True)
        assert r.stdout.strip().endswith("1") and not old.exists() and new.exists(), r
        print("5. old operation logs pruned, recent kept: OK")
    finally:
        inst.stop()
    print("LAUNCHER_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
