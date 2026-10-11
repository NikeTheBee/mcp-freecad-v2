import asyncio, json, os, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
SERVER = Path.home() / ".freecad-mcp" / "freecad_mcp_server.py"
async def main():
    env = dict(os.environ); env["FREECAD_MCP_AUTOSTART"] = "0"
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            tools = (await s.list_tools()).tools
            blob = json.dumps([t.model_dump(exclude_none=True) for t in tools])
            instr = init.instructions or ""
            print(f"TOOLS={len(tools)} schema_chars={len(blob)} ~tokens={len(blob)//4} server_instructions_chars={len(instr)}")
            big = sorted(((len(json.dumps(t.model_dump(exclude_none=True))), t.name) for t in tools), reverse=True)[:6]
            print("largest:", big)
asyncio.run(main())
