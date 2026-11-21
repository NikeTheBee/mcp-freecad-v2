"""mcp_clients.py writes a readable config for every client, merges instead of replacing, keeps
other servers, backs the previous file up, and refuses to touch an unreadable file.
Works in a throwaway home folder: the user's real configs are never read or written.
Sentinel: MCP_CLIENTS_TEST_OK
"""
import json
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mcp_clients as mc  # noqa: E402


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="mcpfc_clients_"))
    try:
        # an existing Cursor config with another server must keep it
        cur = home / mc.CLIENTS["cursor"][0]
        cur.parent.mkdir(parents=True)
        cur.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
        # an existing Codex config with other settings must keep them
        cod = home / mc.CLIENTS["codex"][0]
        cod.parent.mkdir(parents=True)
        cod.write_text('model = "x"\n[mcp_servers.other]\ncommand = "y"\n', encoding="utf-8")
        for c in mc.CLIENTS:
            mc.write(c, home)
            mc.write(c, home)                       # twice: idempotent
            rel, key, style = mc.CLIENTS[c]
            p = home / rel
            data = tomllib.loads(p.read_text(encoding="utf-8")) if style == "toml" else \
                json.loads(p.read_text(encoding="utf-8"))
            servers = data[key]
            assert set(mc.SERVERS) <= set(servers), (c, servers.keys())
            for n, script in mc.SERVERS.items():
                assert servers[n]["args"] == [str(script)], (c, n)
                assert Path(servers[n]["args"][0]).is_file(), (c, n)
                if style == "typed":
                    assert servers[n]["type"] == "stdio"
            print(f"{c}: {p.relative_to(home)} OK")
        assert "other" in json.loads(cur.read_text(encoding="utf-8"))["mcpServers"]
        t = tomllib.loads(cod.read_text(encoding="utf-8"))
        assert t["model"] == "x" and "other" in t["mcp_servers"]
        assert list(cur.parent.glob("mcp.json.bak-*")), "no backup kept"
        print("other servers and settings kept, backup written OK")
        bad = home / mc.CLIENTS["gemini"][0]
        bad.write_text("{not json", encoding="utf-8")
        try:
            mc.write("gemini", home)
            raise AssertionError("an unreadable config was overwritten")
        except ValueError:
            pass
        assert bad.read_text(encoding="utf-8") == "{not json"
        print("unreadable config left untouched OK")
        json.loads(mc.render("generic", home))
        print("MCP_CLIENTS_TEST_OK")
        return 0
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
