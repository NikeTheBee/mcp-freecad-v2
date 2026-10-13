"""MCP-protocol loop test (Part A) — the full client -> launcher -> bridge -> FreeCAD path.

Drives the registered entry point (server/launcher/mcp_freecad_launch.py) as a
real MCP client over stdio, on an isolated FreeCAD (test port, private home):
list tools, create a document and a box, read the volume back.
Run with the bridge venv Python (mcp 2.x + psutil). Sentinel: MCP_LOOP_OK
"""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fc_instance import LAUNCHER, VENV_PY, Instance  # noqa: E402


def _text(result) -> str:
    """Flatten a CallToolResult's content blocks to text."""
    return "\n".join(getattr(c, "text", "") or "" for c in getattr(result, "content", []) or [])


async def run(inst: Instance) -> int:
    params = StdioServerParameters(command=str(VENV_PY), args=[str(LAUNCHER)], env=inst.env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            print("TOOLS:", len(tools), sorted(tools)[:6], "...")
            for needed in ("view_control", "part_operations", "measurement_operations"):
                assert needed in tools, f"missing MCP tool {needed}"

            r = await session.call_tool("view_control",
                                        {"operation": "create_document", "name": "McpLoop"})
            print("create_document:", _text(r)[:120])
            r = await session.call_tool("part_operations",
                                        {"operation": "box", "length": 10, "width": 20,
                                         "height": 30, "name": "LoopBox"})
            print("box:", _text(r)[:120])
            r = await session.call_tool("measurement_operations",
                                        {"operation": "get_volume", "object_name": "LoopBox"})
            vol = _text(r)
            print("volume:", vol[:120])
            assert "6000" in vol, f"unexpected volume: {vol}"

            # a long operation (40 s, past the bridge's 35 s answer wait) returns its
            # result: headless jobs used to run inline on the submitting socket, so
            # the bridge reported a crash ("FreeCAD process not found") mid-computation
            r = await session.call_tool("execute_python",
                                        {"code": "import time\ntime.sleep(40)\n'slept 40'"})
            txt = _text(r)
            print("40 s operation:", txt[:120])
            assert "slept 40" in txt and "Crash" not in txt, txt

            # LAST (FreeCAD dies here): killed mid-operation, it is restarted by the
            # supervisor within seconds; the new one does not know the job, which used
            # to come back as "Unknown job_id ... Already retrieved" (read as success)
            pending = asyncio.ensure_future(session.call_tool(
                "execute_python", {"code": "import time\ntime.sleep(20)\n'never'"}))
            await asyncio.sleep(4)
            inst.kill()
            txt = _text(await pending)
            print("killed mid-operation:", txt[:160])
            assert "FreeCAD stopped while" in txt and "lost" in txt, txt
    return 0


def main() -> int:
    inst = Instance()
    try:
        rc = asyncio.run(run(inst))
    finally:
        inst.stop()
    if rc == 0:
        print("MCP_LOOP_OK")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
