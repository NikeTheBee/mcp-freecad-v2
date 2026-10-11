"""`freecad-layers` MCP server end to end — bridge venv Python, isolated instance.

Replays the audit's "plate with a centred Ø6 through-hole" scenario through
the new tools: the contract FAILs while the hole is missing, pd_hole puts a
real PartDesign hole inside the Body, then everything is OK; plus checkpoint,
analysis, printability, BOM, drawing, record_step + autosave, and the tool
schema budget. Sentinel: LAYERS_MCP_OK
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fc_instance import REPO, VENV_PY, Instance  # noqa: E402

SERVER = REPO / "server" / "layers_mcp" / "freecad_layers_server.py"


def _t(r) -> str:
    return "\n".join(getattr(c, "text", "") or "" for c in getattr(r, "content", []) or [])


async def run(inst: Instance, project: str, out: str) -> None:
    env = dict(inst.env, MCP_FREECAD_PROJECT_DIR=project)
    params = StdioServerParameters(command=str(VENV_PY), args=[str(SERVER)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            size = len(json.dumps([t.model_dump(exclude_none=True) for t in tools]))
            print(f"{len(tools)} tools, schema {size} chars (~{size // 4} tokens)")
            assert size < 16000, size

            async def call(_tool, **kw):
                txt = _t(await s.call_tool(_tool, kw))
                print(f"-> {_tool}: {txt[:300]}")
                return txt

            assert "Project: Plate" in await call("project_init", name="Plate",
                                                  intent="60x40x8, one D6 through hole centred")
            # model the plate with the base's own tools path (setup only)
            inst.call("execute_python_sync", {"code": (
                "import FreeCAD\n"
                "d = FreeCAD.newDocument('Plate')\n"
                "b = d.addObject('PartDesign::Body', 'Body')\n"
                "p = b.newObject('PartDesign::AdditiveBox', 'Pad')\n"
                "p.Length, p.Width, p.Height = 60, 40, 8\n"
                "p.Placement.Base = FreeCAD.Vector(-30, -20, 0)\n"
                "d.recompute()\n'ok'")})
            await call("require", target="Body", kind="bbox", params={"size": [60, 40, 8]})
            await call("require", target="Body", kind="holes",
                       params={"count": 1, "diameter": 6, "through": True})
            txt = await call("check", target="Body")
            assert "OK Body" in txt and "FAIL contract" in txt, txt     # valid but WRONG
            assert (await call("checkpoint", action="save", name="no hole")).endswith(".FCStd")

            txt = await call("pd_hole", body="Body", diameter=6, x=0, y=0)
            assert txt.startswith("OK Body"), txt
            txt = await call("check", target="Body")
            assert "OK contract 2/2" in txt, txt
            txt = await call("analyze", target="Body")
            assert "D6.0 @ [0.0, 0.0, 4.0]" in txt, txt
            txt = await call("printability", target="Body")
            assert '"status": "OK"' in txt, txt
            txt = await call("bill_of_materials")
            assert "1 distinct part(s), 1 total" in txt, txt
            txt = await call("drawing_sheet", target="Body",
                             dxf_path=str(Path(out) / "plate.dxf"))
            assert '"verdict": "OK' in txt and Path(out, "plate.dxf").is_file(), txt
            txt = await call("record_step", feature="Hole", ftype="PartDesign::SubtractiveCylinder",
                             params={"d": 6}, decision="through hole via pd_hole")
            assert "autosaved" in txt, txt
            txt = await call("recover", action="list")
            assert "Plate" in txt, txt
            txt = await call("checkpoint", action="restore", name="no hole")
            assert "restored no hole" in txt, txt
            txt = await call("check", target="Body")
            assert "FAIL contract" in txt, txt                         # rolled back: hole gone
            txt = await call("project_status")
            assert "Design contract" in txt and "Recovery" in txt, txt
            txt = await call("check", target="Nope")
            assert txt.startswith("ERROR"), txt
            inst.call("execute_python_sync", {"code": (
                "import FreeCAD, Part\n"
                "s = FreeCAD.ActiveDocument.addObject('Sketcher::SketchObject', 'Open')\n"
                "s.addGeometry(Part.LineSegment(FreeCAD.Vector(0,0,0), FreeCAD.Vector(5,0,0)))\n"
                "FreeCAD.ActiveDocument.recompute()\n'ok'")})
            txt = await call("check", target="Open")
            assert txt.startswith("FAIL sketch Open") and "open wire" in txt, txt


def main() -> int:
    inst = Instance()
    project = tempfile.mkdtemp(prefix="mcpfc_proj_")
    out = tempfile.mkdtemp(prefix="mcpfc_out_")
    try:
        inst.start()
        asyncio.run(run(inst, project, out))
    finally:
        inst.stop()
        import shutil
        shutil.rmtree(project, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)
    print("LAYERS_MCP_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
