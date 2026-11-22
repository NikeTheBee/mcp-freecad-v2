"""Token budget of the whole MCP surface, as ANY client pays it at session start: the tool
schemas and the server instructions of both servers (`freecad` through its launcher on an
isolated test instance, `freecad-layers` directly). Prints a table; fails above the budget.
Run with the bridge venv Python. Sentinel: TOOL_BUDGET_OK
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fc_instance import LAUNCHER, VENV_PY, Instance  # noqa: E402

LAYERS = Path(__file__).resolve().parent.parent / "server" / "layers_mcp" / "freecad_layers_server.py"
BUDGET_TOKENS = int(sys.argv[1]) if len(sys.argv) > 1 else 15500


async def measure(cmd, args, env):
    async with stdio_client(StdioServerParameters(command=cmd, args=args, env=env)) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            tools = (await s.list_tools()).tools
            per = sorted(((len(json.dumps(t.model_dump(exclude_none=True))), t.name) for t in tools), reverse=True)
            return len(tools), sum(p for p, _ in per), len(init.instructions or ""), per


def main() -> int:
    inst = Instance()
    inst.start()
    try:
        rows = [("freecad",) + asyncio.run(measure(str(VENV_PY), [str(LAUNCHER)], inst.env)),
                ("freecad-layers",) + asyncio.run(measure(str(VENV_PY), [str(LAYERS)], inst.env))]
    finally:
        inst.stop()
    total = 0
    for name, n, chars, instr, per in rows:
        total += chars + instr
        print(f"{name:15s} {n:3d} tools  schema {chars:6d} chars  instructions {instr:5d}  ~{(chars + instr) // 4} tokens")
        print("   largest: " + ", ".join(f"{t} {c}" for c, t in per[:8]))
    print(f"TOTAL ~{total // 4} tokens at session start (budget {BUDGET_TOKENS})")
    if total // 4 > BUDGET_TOKENS:
        print("!! over budget")
        return 1
    print("TOOL_BUDGET_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
