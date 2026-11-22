"""The workshop through a bare MCP client, as Cursor, VS Code, Codex, Gemini CLI or LM Studio see it:
no harness-specific feature. The layers server starts over stdio, lists its tools, serves the
skills as a tool, as resources (skill://...) and as a prompt, and refuses a skill name that is
not one of its folders. Does not need FreeCAD running. Sentinel: UNIVERSAL_MCP_OK
"""
import asyncio
import json
import sys
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:                      # a job without the bridge venv (CI core job)
    print("UNIVERSAL_MCP_SKIP the mcp package is not installed here: run with the bridge venv")
    sys.exit(0)

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "server" / "layers_mcp" / "freecad_layers_server.py"


def _t(r) -> str:
    return "\n".join(getattr(c, "text", "") or "" for c in getattr(r, "content", []) or [])


async def main() -> None:
    # the server runs with the bridge venv, as every registered client starts it
    venv = Path.home() / ".freecad-mcp" / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    params = StdioServerParameters(command=str(venv if venv.is_file() else sys.executable), args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            for need in ("skill", "simulate", "check", "tools_status", "study"):
                assert need in tools, need
            size = len(json.dumps([t.model_dump(exclude_none=True) for t in tools.values()]))
            assert size < 18000, size
            print(f"{len(tools)} tools, schema {size} chars OK")
            idx = _t(await s.call_tool("skill", {}))
            assert "skill-pcb" in idx and "skill-ai" in idx, idx[:200]
            pcb = _t(await s.call_tool("skill", {"name": "skill-pcb"}))
            assert pcb.startswith("# skill-pcb"), pcb[:80]
            bad = await s.call_tool("skill", {"name": "../SECURITY"})
            assert getattr(bad, "is_error", False) or "unknown skill" in _t(bad), _t(bad)
            print("skill tool: index, one skill, path refused OK")
            res = await s.read_resource("skill://skill-mecha")
            assert "skill-mecha" in res.contents[0].text
            res = await s.read_resource("skill://index")
            assert "skill-cycle" in res.contents[0].text
            templates = (await s.list_resource_templates()).resource_templates
            assert any("skill://" in t.uri_template for t in templates)
            print("resources skill://index and skill://<name> OK")
            pr = await s.get_prompt("engineering_workflow", {})
            txt = pr.messages[0].content.text
            assert "Golden rules" in txt and "skill-pcb" in txt
            print("prompt engineering_workflow OK")
    print("UNIVERSAL_MCP_OK")


if __name__ == "__main__":
    asyncio.run(main())
