"""`freecad-layers` MCP server end to end — bridge venv Python, isolated instance.

Replays the audit's "plate with a centred Ø6 through-hole" scenario through
the new tools: the contract FAILs while the hole is missing, pd_hole puts a
real PartDesign hole inside the Body, then everything is OK; plus checkpoint,
analysis, printability, BOM, drawing, record_step + autosave, and the tool
schema budget. Sentinel: LAYERS_MCP_OK
"""
import asyncio
import json
import os
import sys
import tempfile
import time
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
            # FreeCAD works in the selected project: a relative export lands there
            here = Path(project) / "projects" / "Plate"
            r = inst.call("execute_python_sync", {"code": (
                f"import os\nos.path.samefile(os.getcwd(), {str(here)!r})")})
            assert r.get("result") == "True", (r, here)
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
            txt = await call("export", target="Body")
            assert txt.startswith("ERROR") and "not exported: check says FAIL" in txt, txt
            assert not (Path(project) / "projects" / "Plate" / "exports" / "Body.stl").exists()
            assert (await call("checkpoint", action="save", name="no hole")).endswith(".FCStd")

            txt = await call("pd_hole", body="Body", diameter=6, x=0, y=0)
            assert txt.startswith("OK Body"), txt
            txt = await call("check", target="Body")
            assert "OK contract 2/2" in txt, txt
            txt = await call("analyze", target="Body")
            assert "D6.0 @ [0.0, 0.0, 4.0]" in txt, txt
            txt = await call("printability", target="Body")
            assert '"status": "OK"' in txt, txt
            txt = await call("export", target="Body", format="3mf")
            assert txt.startswith("OK 3MF Body -> ") and "closed" in txt and "bbox 60x40x8" in txt, txt
            assert (Path(project) / "projects" / "Plate" / "exports" / "Body.3mf").is_file(), txt
            txt = await call("export", target="Body", path="plate.step")
            assert txt.startswith("OK STEP Body") and "same as the part" in txt, txt
            txt = await call("fem_static", target="Body", fixed=["xmin"], material="aluminium",
                             loads=[{"faces": "xmax", "force_N": 500, "direction": [0, 0, -1]}])
            assert txt.startswith("OK FEM static (aluminium") and "safety factor" in txt, txt
            txt = await call("fem_static", target="Body", fixed=["xmin"], material="pla",
                             loads=[{"faces": "xmax", "force_N": 500, "direction": [0, 0, -1]}],
                             max_displacement_mm=0.01)
            assert txt.startswith("FAIL FEM static") and "max displacement" in txt, txt
            res = await s.call_tool("render_views", {"targets": ["Body"], "views": ["top", "iso"]})
            kinds = [c.type for c in res.content]
            print("-> render_views:", kinds, _t(res)[:120])
            assert kinds == ["text", "image"] and res.content[1].mime_type == "image/png", kinds
            assert len(res.content[1].data) > 10000, len(res.content[1].data)
            txt = await call("bill_of_materials")
            assert "1 distinct part(s), 1 total" in txt, txt
            txt = await call("drawing_sheet", target="Body",
                             dxf_path=str(Path(out) / "plate.dxf"))
            assert '"verdict": "OK' in txt and Path(out, "plate.dxf").is_file(), txt
            txt = await call("record_step", feature="Hole", ftype="PartDesign::SubtractiveCylinder",
                             params={"d": 6}, decision="through hole via pd_hole")
            assert "autosaved" in txt, txt
            txt = await call("recipe", action="save", name="plate-hole",
                             description="plate with a centred through hole")
            assert txt.startswith("saved recipe plate-hole") and "OK contract 2/2" in txt, txt
            assert "plate-hole (plate with a centred through hole)" in await call("recipe", action="list")
            txt = await call("recipe", action="show", name="plate-hole")
            assert "1. Hole [PartDesign::SubtractiveCylinder]" in txt and "holes on Body" in txt, txt
            txt = await call("recover", action="list")
            assert "Plate" in txt, txt
            txt = await call("checkpoint", action="restore", name="no hole")
            assert "restored no hole" in txt, txt
            txt = await call("check", target="Body")
            assert "FAIL contract" in txt, txt                         # rolled back: hole gone
            txt = await call("recipe", action="save", name="no-hole")
            assert txt.startswith("ERROR") and "not verified" in txt, txt  # never keep a failing design
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

            # a second project keeps its own memory; the first is intact after switching back
            txt = await call("project_init", name="Gear", intent="spur gear")
            assert "Project: Gear" in txt and "Design contract" not in txt, txt
            txt = await call("project_init", name="Plate")
            assert txt.startswith("ERROR") and "already exists" in txt, txt
            txt = await call("project_switch", name="Plate")
            assert "Project: Plate" in txt and "Projects: default, Gear, Plate*" in txt, txt
            # documents stay open across a switch and checks use the active one: say which
            assert "Open documents: " in txt and "(active)" in txt, txt
            assert "Design contract" in await call("project_status")

            # FreeCAD outlives sessions: an updated layer file is reloaded without a restart
            # (a marker on the module object, not id(): a freed address can be reused)
            inst.call("execute_python_sync", {"code": (
                "import sys\nsys.modules['freecad_layers']._test_marker = 1\n'ok'")})

            def marked():
                return inst.call("execute_python_sync", {"code": (
                    "import sys\nhasattr(sys.modules['freecad_layers'], '_test_marker')")})["result"]
            assert "Project: Plate" in await call("project_status")
            assert marked() == "True"                     # unchanged files: no reload
            f = REPO / "server" / "freecad_layers" / "render.py"
            st = f.stat()
            os.utime(f, (st.st_atime, st.st_mtime + 2))
            try:
                assert "Project: Plate" in await call("project_status")
                assert marked() == "False", "layers not reloaded after a file changed"
            finally:
                os.utime(f, (st.st_atime, st.st_mtime))
            print("layers reloaded after an update, without restarting FreeCAD: OK")

            # the user changes a requirement: replaced in one step, removable, never stuck
            txt = await call("require", target="Body", kind="bbox", params={"size": [70, 40, 8]},
                             replaces="S1")
            assert "(replaces S1)" in txt, txt
            new_id = txt.split()[0]
            assert "FAIL contract" in await call("check", target="Body")   # the part is 60 long
            txt = await call("require", target="Body", kind="bbox", params={"size": [60, 40, 8]},
                             replaces=new_id)
            txt = await call("check", target="Body")   # (the hole was rolled back above)
            assert "FAIL contract 1/2 | S2 holes" in txt and "bbox" not in txt.split("contract")[1], txt
            txt = await call("require", target="Body", kind="remove", replaces="S99")
            assert txt.startswith("ERROR no requirement 'S99'"), txt     # no wrapper prefix
            txt = await call("require", target="Body", kind="holes", params={"diam": 6})
            assert txt.startswith("ERROR") and "unknown parameter" in txt, txt
            # a requirement set on a step of the Body is checked by nobody: said at once
            txt = await call("require", target="Pad", kind="valid")
            assert "note: Pad is a step of Body; a requirement on the part goes on Body" in txt, txt
            await call("require", target="Pad", kind="remove", replaces=txt.split()[0])
            # a hole that cuts nothing / would break the part is not kept
            vol = await call("check", target="Body")
            txt = await call("pd_hole", body="Body", diameter=3, x=1e9, y=0)
            assert txt.startswith("ERROR no material at (1e+09, 0)"), txt
            txt = await call("pd_hole", body="Body", diameter=500, x=0, y=0)
            assert txt.startswith("FAIL pd_hole: the part would be broken"), txt
            assert (await call("check", target="Body")).splitlines()[0] == vol.splitlines()[0]
            # nonsense requests are refused with the reason
            for tool, kw, why in (("check_assembly", {"objects": ["Body", "Body"]}, "more than once"),
                                  ("check_assembly", {"objects": ["Body"]}, "2 or more"),
                                  ("render_views", {"targets": []}, "at least one"),
                                  ("printability", {"target": "Body", "bed_mm": [-1, 5]}, "bed_mm"),
                                  ("recover", {"action": "bogus"}, "list or open"),
                                  # a step of the Body is not the part (later features missing)
                                  ("printability", {"target": "Pad"}, "is a step of Body"),
                                  ("fem_static", {"target": "Pad", "fixed": ["xmin"],
                                                  "loads": [{"faces": "xmax", "force_N": 1}]},
                                   "is a step of Body"),
                                  ("drawing_sheet", {"target": "Pad"}, "is a step of Body"),
                                  ("export", {"target": "Pad"}, "is a step of Body"),
                                  ("record_step", {"feature": "", "ftype": ""}, "must name")):
                txt = await call(tool, **kw)
                assert txt.startswith("ERROR") and why in txt, (tool, txt)
            print("requirements replaced/removed; useless holes not kept; nonsense refused: OK")
            # pd_hole in a MOVED Body: positions are the Body's own (the global bounding box
            # used to put the cylinder 50 mm too high, beside the part)
            inst.call("execute_python_sync", {"code": (
                "import FreeCAD\n"
                "d = FreeCAD.ActiveDocument\n"
                "b = d.addObject('PartDesign::Body', 'Moved')\n"
                "p = b.newObject('PartDesign::AdditiveBox', 'MovedBox')\n"
                "p.Length = p.Width = p.Height = 10\n"
                "b.Placement.Base = FreeCAD.Vector(100, 0, 50)\n"
                "d.recompute()\n'ok'")})
            txt = await call("pd_hole", body="Moved", diameter=4, x=5, y=5)
            assert txt.startswith("OK Moved"), txt
            txt = await call("analyze", target="Moved")
            assert "D4.0 @ [105.0, 5.0, 55.0]" in txt, txt
            print("pd_hole in a moved Body lands where asked, through the part: OK")
            # a stepped part (top at z 15 on one half, 5 on the other): a blind hole starts
            # at the surface under it, not at the highest point (it cut nothing / too short)
            inst.call("execute_python_sync", {"code": (
                "import FreeCAD\n"
                "d = FreeCAD.ActiveDocument\n"
                "b = d.addObject('PartDesign::Body', 'Step')\n"
                "p = b.newObject('PartDesign::AdditiveBox', 'Base')\n"
                "p.Length, p.Width, p.Height = 40, 20, 5\n"
                "q = b.newObject('PartDesign::AdditiveBox', 'Riser')\n"
                "q.Length, q.Width, q.Height = 20, 20, 10\n"
                "q.Placement.Base = FreeCAD.Vector(0, 0, 5)\n"
                "d.recompute()\n'ok'")})
            txt = await call("pd_hole", body="Step", diameter=3, x=30, y=10, through=False, depth=3)
            assert txt.startswith("OK Step") and "3 mm deep from z 5" in txt, txt
            txt = await call("pd_hole", body="Step", diameter=3, x=10, y=10, through=False, depth=4)
            assert txt.startswith("OK Step") and "4 mm deep from z 15" in txt, txt
            txt = await call("pd_hole", body="Step", diameter=2, x=35, y=5, through=False, depth=7)
            assert txt.startswith("FAIL pd_hole: a 7 mm deep hole goes right through"), txt
            print("pd_hole on a stepped part: blind holes start at the surface under them: OK")

            # headless FreeCAD runs a call to the end whatever its timeout: what limits a
            # heavy check is our socket wait (hence 300 s for the analysis tools)
            t0 = time.time()
            r = inst.call("execute_python_sync", {"code": "import time\ntime.sleep(2)\n'slept'",
                                                  "timeout": 1})
            assert r.get("result") == "'slept'" and time.time() - t0 >= 2, r

            # LAST (FreeCAD dies here): a crash while a layers call waits is reported as a
            # crash with the way back, not as "still computing"
            import threading
            busy = threading.Thread(target=lambda: inst.call(
                "execute_python_sync", {"code": "import time\ntime.sleep(20)\n'x'"}, timeout=30))
            busy.start()                                   # holds FreeCAD for 20 s
            await asyncio.sleep(1.0)
            pending = asyncio.ensure_future(call("check", target="Body"))   # waits behind it
            await asyncio.sleep(2.0)
            inst.kill()                                    # the crash
            txt = await pending
            busy.join()
            assert txt.startswith("ERROR FreeCAD stopped during this call") and "recover(" in txt, txt
            print("FreeCAD crash during a call: reported as a crash, with recover: OK")
            # ...and the way back works: the next call starts FreeCAD again, recover opens
            # the newest autosave, and the part checks out
            txt = await call("recover", action="open", doc="Plate")
            assert txt == "opened Plate", txt          # its own name, not the copy's file name
            txt = await call("check", target="Body")
            # the LATEST work: the no-hole rollback (60x40x8 full = 19200), autosaved when it
            # was restored - not the abandoned state before it (18973.8, with the hole)
            assert txt.startswith("OK Body") and "V=19200 " in txt, txt
            print("after the crash: FreeCAD back on its own, recover reopened the work: OK")
    # long calls are explained to the person waiting, not as raw bridge errors
    sys.path.insert(0, str(SERVER.parent))
    import freecad_layers_server as srv
    msg = srv._explain("Operation timeout (300s) - GUI thread may be busy. Use "
                       "execute_python_async for long operations.", 300.0)
    assert "still working on it" in msg and "300 s" in msg, msg
    assert "still busy" in srv._explain("GUI thread is busy with a previous operation. "
                                        "Wait for it to complete or use execute_python_async.", 30.0)
    assert "within 315 s" in srv._explain("no response from FreeCAD", 300.0)
    assert srv._explain("No object named 'Nope'", 30.0) == "No object named 'Nope'"
    assert srv._explain("Python execution error: no project 'x'", 30.0) == "no project 'x'"
    # no answer + the FreeCAD that took the call is gone = a crash, not a long computation
    import subprocess
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    assert srv._gone(dead.pid)
    serving = srv.launcher.port_owners
    srv.launcher.port_owners = lambda port: {os.getpid()}     # "still serves its port"
    try:
        assert not srv._gone(os.getpid())
    finally:
        srv.launcher.port_owners = serving
    assert "recover(action='open'" in srv._STOPPED.format(pid=dead.pid)
    # the value is the last QUOTED line: CalculiX prints come before it, and the base
    # appends a "Tip: ..." line after it when the code calls .cut() / makeBox()...
    reply = ("Calculix: solving\n[FreeCAD Console]\nsome warning\n" + repr(json.dumps({"V": 6}))
             + "\nTip: this code calls cut directly -- a dedicated tool already covers this")
    assert srv._decode(reply) == {"V": 6}, srv._decode(reply)
    assert srv._decode(repr(json.dumps([1, "a"]))) == [1, "a"]
    print("long calls: headless runs them to the end; timeouts explained plainly: OK")


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
