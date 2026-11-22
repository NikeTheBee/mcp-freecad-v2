"""A user's whole job through the real tools, base and layers together (isolated FreeCAD).

"A 80 x 50 x 6 plate, four D5 holes 6 mm from the edges, R3 rounds on the
vertical edges, ready to print": sketch, pad, requirements, pd_hole x4, fillet
by edge index on the Body, check (contract 6/6), printability, record_step,
export 3MF (read back), parts list, DXF plan. The volume must be exact:
80*50*6 - 4*pi*2.5^2*6 - 4*(9 - 9*pi/4)*6. Each piece is tested elsewhere;
this one catches what breaks BETWEEN them (edge numbering after holes, a
dress-up landing outside the Body...). Sentinel: SCENARIO_OK
"""
import asyncio
import json
import math
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from _fc_instance import REPO, VENV_PY, Instance  # noqa: E402

SERVER = REPO / "server" / "layers_mcp" / "freecad_layers_server.py"


def _t(r):
    return "\n".join(getattr(c, "text", "") or "" for c in getattr(r, "content", []) or [])


def op(inst, tool, args, wait=120.0):
    r = inst.call(tool, args)
    job_id = (r.get("job_id") or r.get("id")) if isinstance(r, dict) else None
    t0 = time.monotonic()
    while isinstance(r, dict) and r.get("status") in ("submitted", "running", "queued"):
        assert time.monotonic() - t0 < wait
        time.sleep(0.2)
        r = inst.call("poll_job", {"job_id": job_id})
    return r.get("result", r) if isinstance(r, dict) else r


async def run(inst, project):
    env = dict(inst.env, MCP_FREECAD_PROJECT_DIR=project)
    params = StdioServerParameters(command=str(VENV_PY), args=[str(SERVER)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            async def call(tool, **kw):
                txt = _t(await s.call_tool(tool, kw))
                print(f"L {tool}: {txt[:400]}")
                return txt

            def base(tool, **kw):
                r = op(inst, tool, kw)
                print(f"B {tool} {kw.get('operation', '')}: {str(r)[:300]}")
                return r

            await call("project_init", name="Plaque", intent="80x50x6, 4 trous D5 a 6 mm des bords, conges R3")
            base("view_control", operation="create_document", name="Plaque")
            # the AI builds a Body, a sketch and a pad with the base tools
            inst.call("execute_python_sync", {"code": (
                "import FreeCAD\nd = FreeCAD.ActiveDocument\n"
                "b = d.addObject('PartDesign::Body', 'Body')\nd.recompute()\n'ok'")})
            base("sketch_operations", operation="create_sketch", name="Profil", plane="XY")
            base("sketch_operations", operation="add_rectangle", sketch_name="Profil", x=0, y=0, width=80, height=50)
            base("sketch_operations", operation="verify_sketch", sketch_name="Profil")
            base("partdesign_operations", operation="pad", sketch_name="Profil", length=6)
            await call("require", target="Body", kind="bbox", params={"size": [80, 50, 6]})
            await call("require", target="Body", kind="holes", params={"count": 4, "diameter": 5, "through": True})
            for x, y in ((6, 6), (74, 6), (6, 44), (74, 44)):
                await call("require", target="Body", kind="hole_at", params={"diameter": 5, "center": [x, y, None]})
            txt = await call("check", target="Body")
            assert "FAIL contract" in txt
            for x, y in ((6, 6), (74, 6), (6, 44), (74, 44)):
                txt = await call("pd_hole", body="Body", diameter=5, x=x, y=y)
                assert txt.startswith("OK"), txt
            # the four outer vertical edges (what list_faces / count_elements gives the AI)
            r = inst.call("execute_python_sync", {"code": (
                "import FreeCAD\ns = FreeCAD.ActiveDocument.getObject('Body').Shape\n"
                "[i + 1 for i, e in enumerate(s.Edges) if e.Curve.TypeId == 'Part::GeomLine' "
                "and abs(e.BoundBox.ZLength - 6) < 1e-9 and e.BoundBox.XLength < 1e-9 and e.BoundBox.YLength < 1e-9 "
                "and (e.BoundBox.XMin in (0, 80)) and (e.BoundBox.YMin in (0, 50))]")})
            edges = json.loads(r["result"].replace("'", '"'))
            print("vertical outer edges:", edges)
            base("partdesign_operations", operation="fillet", object_name="Body", radius=3, edges=edges)
            txt = await call("check", target="Body")
            v = float(inst.call("execute_python_sync", {"code": (
                "import FreeCAD\nFreeCAD.ActiveDocument.getObject('Body').Shape.Volume")})["result"])
            want = 80 * 50 * 6 - 4 * math.pi * 2.5 ** 2 * 6 - 4 * (9 - math.pi * 9 / 4) * 6
            print(f"VOLUME {v:.4f} want {want:.4f} diff {v - want:+.6f}")
            assert abs(v - want) < 1e-6 * want
            assert "OK contract" in txt, txt
            await call("printability", target="Body")
            await call("record_step", feature="Fillet", ftype="PartDesign::Fillet", params={"r": 3})
            txt = await call("export", target="Body", format="3mf")
            assert txt.startswith("OK 3MF"), txt
            await call("bill_of_materials")
            await call("drawing_sheet", target="Body", dxf_path="plan.dxf")
            print("SCENARIO_OK")


def main():
    inst = Instance()
    project = tempfile.mkdtemp(prefix="mcpfc_scn_")
    try:
        inst.start()
        asyncio.run(run(inst, project))
    finally:
        inst.stop()
        shutil.rmtree(project, ignore_errors=True)


if __name__ == "__main__":
    main()
