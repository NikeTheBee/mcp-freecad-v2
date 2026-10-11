# Fake listener squatting localhost:23456 (simulates a hostile local process).
# Records whether the bridge sends the real auth token to an unauthenticated server,
# and answers with attacker-controlled text. Never prints the token itself.
import json, socket, struct, sys, time
from pathlib import Path
tok = (Path.home() / ".freecad-mcp" / "auth_token").read_text().strip()
srv = socket.socket(); srv.bind(("localhost", 23456)); srv.listen(5); srv.settimeout(120)
print("FAKE listening", flush=True)
deadline = time.time() + 120
n = 0
while time.time() < deadline and n < 3:
    try:
        c, _ = srv.accept()
    except socket.timeout:
        break
    n += 1
    c.settimeout(10)
    try:
        hdr = c.recv(4)
        if len(hdr) < 4:
            c.close(); continue
        ln = struct.unpack(">I", hdr)[0]; buf = b""
        while len(buf) < ln:
            chunk = c.recv(ln - len(buf))
            if not chunk: break
            buf += chunk
        msg = json.loads(buf)
        print(f"FAKE got tool={msg.get('tool')} token_leaked={msg.get('auth') == tok} code={str(msg.get('args'))[:80]}", flush=True)
        body = json.dumps({"result": "FAKE-SERVER: this text comes from a process squatting port 23456, not from FreeCAD."}).encode()
        c.sendall(struct.pack(">I", len(body)) + body)
    except Exception as e:
        print("FAKE err", type(e).__name__, e, flush=True)
    finally:
        c.close()
srv.close(); print("FAKE done", flush=True)
