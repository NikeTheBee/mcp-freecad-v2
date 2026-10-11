# Socket-level robustness/stress tests against the user's own local FreeCAD MCP server.
import json, socket, struct, sys, threading, time, statistics
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".freecad-mcp"))
from mcp_bridge_framing import send_message, receive_message
import mcp_auth

HOST, PORT = "localhost", 23456
def R(tag, msg): print(f"RESULT[{tag}] {msg}", flush=True)

def call(payload, timeout=15.0, auth=True):
    if auth: payload = mcp_auth.attach(dict(payload))
    s = socket.socket(); s.settimeout(timeout); s.connect((HOST, PORT))
    try:
        send_message(s, json.dumps(payload))
        r = receive_message(s, timeout=timeout)
        return json.loads(r) if r else None
    finally:
        s.close()

def alive():
    try:
        r = call({"tool": "test_echo", "args": {"message": "ping"}}, timeout=10)
        return bool(r) and "error" not in r
    except Exception as e:
        return False

R("baseline alive", alive())

# 1. Latency baseline
lat = []
for i in range(50):
    t = time.perf_counter(); call({"tool": "test_echo", "args": {"message": str(i)}}); lat.append((time.perf_counter()-t)*1000)
lat.sort()
R("latency test_echo x50", f"p50={statistics.median(lat):.1f}ms p95={lat[int(0.95*len(lat))-1]:.1f}ms max={lat[-1]:.1f}ms")

# 2. Slowloris: idle connection that never sends -> does it block others?
idle = socket.socket(); idle.connect((HOST, PORT))
t = time.perf_counter()
try:
    ok = call({"tool": "test_echo", "args": {"message": "while-idle"}}, timeout=8)
    R("slowloris (1 idle conn)", f"other client served in {(time.perf_counter()-t)*1000:.0f}ms -> {bool(ok)}")
except Exception as e:
    R("slowloris (1 idle conn)", f"OTHER CLIENT BLOCKED: {type(e).__name__} after {(time.perf_counter()-t):.1f}s")
idles = []
for i in range(60):
    try:
        x = socket.socket(); x.settimeout(2); x.connect((HOST, PORT)); idles.append(x)
    except Exception as e:
        R("slowloris many", f"connect #{i} failed: {type(e).__name__}"); break
t = time.perf_counter()
try:
    ok = call({"tool": "test_echo", "args": {"message": "while-60-idle"}}, timeout=8)
    R("slowloris (60 idle conns)", f"served in {(time.perf_counter()-t)*1000:.0f}ms -> {bool(ok)}")
except Exception as e:
    R("slowloris (60 idle conns)", f"BLOCKED/REFUSED: {type(e).__name__} after {(time.perf_counter()-t):.1f}s")
for x in idles + [idle]: x.close()
time.sleep(1)
R("alive after slowloris", alive())

# 3. Malformed frames
def raw(data, wait=3):
    s = socket.socket(); s.settimeout(wait); s.connect((HOST, PORT))
    try:
        s.sendall(data)
        try: return s.recv(4096)
        except Exception as e: return f"<{type(e).__name__}>"
    finally: s.close()
R("oversize length 0xFFFFFFFF", repr(raw(struct.pack(">I", 0xFFFFFFFF) + b"x"))[:120])
R("HTTP request bytes", repr(raw(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"))[:120])
R("bad json", repr(raw(struct.pack(">I", 5) + b"{oops"))[:160])
R("non-utf8", repr(raw(struct.pack(">I", 4) + b"\xff\xfe\xfd\xfc"))[:160])
R("json array not object", repr(raw(struct.pack(">I", 2) + b"[]"))[:160])
R("alive after malformed", alive())

# 4. Unauthenticated flood
t = time.perf_counter(); rej = 0
for i in range(300):
    try:
        r = call({"tool": "execute_python", "args": {"code": "1"}}, timeout=5, auth=False)
        if r and "unauthorized" in str(r.get("error", "")): rej += 1
    except Exception: pass
R("unauth flood x300", f"{rej}/300 rejected in {time.perf_counter()-t:.1f}s; alive={alive()}")

# 5. Concurrency: 20 threads each appending to a shared list inside FreeCAD
call({"tool": "execute_python", "args": {"code": "import FreeCAD\nFreeCAD.__audit_counter = []\n'reset'"}})
errors = []; done = [0]
def worker(k):
    for j in range(10):
        try:
            r = call({"tool": "execute_python", "args": {"code": f"import FreeCAD\nFreeCAD.__audit_counter.append({k*100+j})\nlen(FreeCAD.__audit_counter)"}}, timeout=60)
            if not r or "error" in r: errors.append(str(r)[:120])
            else: done[0] += 1
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
t = time.perf_counter()
ths = [threading.Thread(target=worker, args=(k,)) for k in range(20)]
[th.start() for th in ths]; [th.join() for th in ths]
r = call({"tool": "execute_python", "args": {"code": "import FreeCAD\n(len(FreeCAD.__audit_counter), len(set(FreeCAD.__audit_counter)))"}})
R("concurrency 20x10 execute_python", f"ok={done[0]}/200 errors={len(errors)} final={r.get('result') if r else r} in {time.perf_counter()-t:.1f}s; sample_err={errors[:2]}")
R("alive at end", alive())
