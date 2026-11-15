"""Workshop end to end — bridge venv Python, isolated FreeCAD (port 23499), real pixi envs.

A FreeCAD part goes through the whole loop with the MCP tools:
tools_status -> study start (criteria + real-test acceptance) -> simulate fem_static on the part
(FaceN and bbox-side faces, checked geometry exported as STEP) -> answer matches the beam formula
-> cached on repeat -> a FAIL under a heavier load -> iterate -> ready_to_build -> built ->
measure (disagreement -> calibrate -> next iteration) -> measure (agreement) -> validated -> report.
Also: a background job polled to completion, a kind that needs geometry refused without one,
an unknown kind refused.

Needs the workshop installed (MCPFC_HOME or ~/.freecad-mcp/atelier_home). Otherwise prints
ATELIER_SKIP. Sentinel: ATELIER_OK
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fc_instance import REPO, VENV_PY, Instance  # noqa: E402

SERVER = REPO / "server" / "layers_mcp" / "freecad_layers_server.py"


def _home() -> str | None:
    h = os.environ.get("MCPFC_HOME")
    if h:
        return h
    try:
        return (Path.home() / ".freecad-mcp" / "atelier_home").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _t(r) -> str:
    return "\n".join(getattr(c, "text", "") or "" for c in getattr(r, "content", []) or [])


def _val(txt: str, key: str) -> float:
    for line in txt.splitlines():
        if line.startswith("values: "):
            for kv in line[8:].split(", "):
                k, _, v = kv.partition("=")
                if k == key:
                    return float(v)
    raise AssertionError(f"{key} not in {txt}")


async def run(inst: Instance, project: str, home: str) -> None:
    env = dict(inst.env, MCP_FREECAD_PROJECT_DIR=project, MCPFC_HOME=home)
    params = StdioServerParameters(command=str(VENV_PY), args=[str(SERVER)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            async def call(_tool, **kw):
                txt = _t(await s.call_tool(_tool, kw))
                print(f"-> {_tool}: {txt[:400]}")
                return txt

            txt = await call("tools_status")
            assert "fem_ccx [sim]" in txt and "fem_static" in txt, txt
            assert "Project: Beam" in await call("project_init", name="Beam", intent="cantilever")
            # 200 x 20 x 20 steel beam, clamped at x=0, 1 kN down at the free end
            r = inst.call("execute_python_sync", {"code": (
                "import FreeCAD\n"
                "d = FreeCAD.newDocument('Beam')\n"
                "b = d.addObject('PartDesign::Body', 'Body')\n"
                "p = b.newObject('PartDesign::AdditiveBox', 'Box')\n"
                "p.Length, p.Width, p.Height = 200, 20, 20\n"
                "d.recompute()\n"
                "[i + 1 for i, f in enumerate(b.Shape.Faces) if abs(f.CenterOfMass.x - 200) < 1e-6][0]")})
            xmax_face = f"Face{int(r['result'])}"

            txt = await call("study", action="start", name="beam", args={
                "goal": "cantilever holds 1 kN with safety factor >= 1.5",
                # sigma = M/W = 1000*200/(20*20^2/6) = 150 MPa: S235 gives 1.57
                "criteria": {"fem_static": {"safety_factor": {"min": 1.5}}},
                "hypotheses": [{"text": "S235 steel, E 210 GPa", "source": "EN 10025-2"},
                               "load is static"],
                "acceptance": {"tip_deflection_mm": {"max": 1.5}}, "agreement": 0.1})
            st = json.loads(txt)
            assert st["state"] == "design" and st["unsourced_hypotheses"] == ["load is static"], st

            load = {"fixed": ["xmin"], "material": "steel", "mesh_size": 5,
                    "loads": [{"type": "force", "faces": [xmax_face], "N": [0, 0, -1000]}]}
            txt = await call("simulate", kind="fem_static", target="Body", params=load, study="beam")
            assert txt.startswith("OK fem_static"), txt
            uz = _val(txt, "max_abs_uz_mm")
            assert abs(uz - 0.96) / 0.96 < 0.03, uz            # Timoshenko: 0.9597 mm
            assert "8/8 reference cases match" in txt, txt
            txt = await call("simulate", kind="fem_static", target="Body", params=load, study="beam")
            assert "cached" in txt and abs(_val(txt, "max_abs_uz_mm") - uz) < 1e-12, txt
            st = json.loads(await call("study", action="status", name="beam"))
            assert st["state"] == "ready_to_build", st

            # 20 kN: stress too high -> FAIL -> the loop stays in design
            heavy = dict(load, loads=[{"type": "force", "faces": ["xmax"], "N": [0, 0, -20000]}])
            txt = await call("simulate", kind="fem_static", target="Body", params=heavy, study="beam")
            assert txt.startswith("FAIL") and "safety_factor" in txt, txt
            st = json.loads(await call("study", action="status", name="beam"))
            assert st["state"] == "design" and "fix the design" in st["next"], st
            txt = await call("study", action="built", name="beam")
            assert txt.startswith("ERROR") and "must be OK first" in txt, txt
            # correction: back to the 1 kN case (new iteration)
            await call("study", action="iterate", name="beam",
                       args={"change": "load case corrected to the real 1 kN", "params": {"F_N": 1000}})
            await call("simulate", kind="fem_static", target="Body", params=load, study="beam")
            assert json.loads(await call("study", action="status", name="beam"))["state"] == "ready_to_build"
            await call("study", action="built", name="beam", args={"note": "machined, S235"})

            # the real test: procedure + sheet from the study (prediction, limits, safety)
            txt = await call("study", action="test_plan", name="beam", args={
                "predicted": {"tip_deflection_mm": "fem_static.max_abs_uz_mm"},
                "instruments": {"tip_deflection_mm": "dial gauge 0.01 mm"}})
            plan = Path(txt.split("test plan written: ")[1].split(" (+")[0])
            body = plan.read_text(encoding="utf-8")
            assert "dead weights" in body and "never runs the test" in body and "1.5" in body, body
            sheet = plan.parent / "measurements.csv"
            rows = sheet.read_text(encoding="utf-8").splitlines()
            assert rows[1].startswith("tip_deflection_mm,0.95"), rows
            # the human fills it in: 15 % softer than the model
            rows[1] = rows[1].replace(",,dial gauge", f",{uz * 1.15:.6g},dial gauge")
            sheet.write_text("\n".join(rows) + "\n", encoding="utf-8")
            txt = await call("study", action="measure", name="beam", args={"csv": "measurements.csv"})
            assert txt.startswith("CALIBRATE") and "+15.0%" in txt and "calibrate" in txt, txt
            # the loop fits the model itself: E re-simulated until the model gives the measurement
            txt = await call("study", action="calibrate", name="beam", args={
                "auto": {"quantity": "tip_deflection_mm", "param": "material.E"},
                "note": "printed/annealed part softer than handbook steel?"})
            assert txt.startswith("CALIBRATED material.E 210000 -> "), txt
            e_fit = float(txt.split(" -> ")[1].split()[0])
            assert abs(e_fit - 210000 / 1.15) / (210000 / 1.15) < 0.02, e_fit   # deflection ~ 1/E
            st = json.loads(txt.split("\n", 1)[1])
            assert st["iteration"] == 3 and st["state"] == "design", st
            # the same inputs as before: the study applies the calibrated E by itself
            txt = await call("simulate", kind="fem_static", target="Body", params=load, study="beam")
            assert "[calibrated material.E=" in txt, txt
            uz2 = _val(txt, "max_abs_uz_mm")
            assert abs(uz2 - uz * 1.15) / (uz * 1.15) < 0.02, (uz2, uz)
            await call("study", action="built", name="beam", args={"note": "same part"})
            txt = await call("study", action="measure", name="beam", args={
                "data": {"tip_deflection_mm": uz * 1.15}, "source": "dial gauge, 1 kN dead weight",
                "predicted": {"tip_deflection_mm": "fem_static.max_abs_uz_mm"}})
            assert txt.startswith("VALIDATED") and "in limits" in txt, (txt, uz2)
            txt = await call("study", action="report", name="beam")
            rep = Path(txt.split("report written: ")[1].strip())
            body = rep.read_text(encoding="utf-8")
            assert "Iteration 3" in body and "dial gauge" in body and "EN 10025-2" in body, body[:800]
            assert len((rep.parent / "journal.jsonl").read_text(encoding="utf-8").splitlines()) >= 12

            # background job, polled
            txt = await call("simulate", kind="fem_modal", target="Body", background=True,
                             params={"fixed": ["xmin"], "material": "steel", "mesh_size": 5,
                                     "n_modes": 2, "limits": {"f1_Hz": {"min": 300}}})
            assert txt.startswith("RUNNING job "), txt
            jid = txt.split()[2].rstrip(":")
            for _ in range(240):
                txt = await call("job", id=jid)
                if not txt.startswith("{"):
                    break
                await asyncio.sleep(1)
            assert txt.startswith("OK modal") and abs(_val(txt, "f1_Hz") - 417.8) / 417.8 < 0.03, txt

            # automatic correction: the lightest beam height (spreadsheet-driven) with SF >= 2
            r = inst.call("execute_python_sync", {"code": (
                "import FreeCAD\n"
                "d = FreeCAD.newDocument('Opt')\n"
                "sh = d.addObject('Spreadsheet::Sheet', 'Params')\n"
                "sh.set('A1', '12 mm')\nsh.setAlias('A1', 'thick')\n"
                "b = d.addObject('PartDesign::Body', 'Body')\n"
                "p = b.newObject('PartDesign::AdditiveBox', 'Box')\n"
                "p.Length, p.Width = 200, 20\n"
                "p.setExpression('Height', 'Params.thick')\n"
                "d.recompute()\nFreeCAD.setActiveDocument('Opt')\n'ok'")})
            assert r.get("result") == "'ok'", r
            txt = await call("study", action="start", name="lightest", args={
                "goal": "lightest 200 mm cantilever holding 1 kN, SF >= 2",
                "criteria": {"fem_static": {"safety_factor": {"min": 2}}}})
            txt = await call("study", action="optimize", name="lightest", args={
                "kind": "fem_static", "target": "Body", "objective": "mass_kg", "sense": "min",
                "vars": [{"cell": "Params.thick", "lo": 12, "hi": 36}], "max_runs": 8,
                "params": {"fixed": ["xmin"], "material": "steel", "mesh_size": 5,
                           "loads": [{"type": "force", "faces": ["xmax"], "N": [0, 0, -1000]}]}})
            assert txt.startswith("OK best of 8 runs") and "Params.thick 12 mm -> " in txt, txt
            t_best = float(txt.split("Params.thick 12 mm -> ")[1].split(")")[0])
            # beam theory gives 22.6 mm with the peak at the clamp; the FEM judges the peak away
            # from the supports (a little lower), so slightly less: 20..23.5 mm
            assert 20 <= t_best <= 23.5 and _val(txt, "safety_factor") >= 2, (t_best, txt)
            r = inst.call("execute_python_sync", {"code": (
                "import FreeCAD\nFreeCAD.getDocument('Opt').getObject('Body').Shape.BoundBox.ZLength")})
            assert abs(float(r["result"]) - t_best) < 1e-6, r          # applied to the part
            st = json.loads(await call("study", action="status", name="lightest"))
            assert st["iteration"] == 2 and st["state"] == "ready_to_build", st

            # purchased parts beside the made one: named by their standard, 'buy' in the BOM
            for x in (20, 60):
                txt = await call("std_part", kind="iso4762", size="M6", length=20, at=[x, 10, 0], axis="-z")
                assert txt.startswith("OK ") and "ISO 4762 M6x20" in txt and "clearance hole D6.6" in txt, txt
            txt = await call("bill_of_materials")
            rows = json.loads(txt)["rows"]
            scr = [r for r in rows if r["designation"] == "ISO 4762 M6x20"]
            assert scr and scr[0]["qty"] == 2 and scr[0]["source"] == "buy", rows
            assert any(r["source"] == "make" for r in rows), rows

            # project files handed to an adapter: copied into the study, their BYTES in the cache key
            logs = Path(project) / "projects" / "Beam" / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "strain.csv").write_text("s\n-2\n1\n-3\n5\n-1\n3\n-4\n4\n-2\n", encoding="utf-8")
            fat = {"files": ["logs/strain.csv"], "csv": "logs/strain.csv",
                   "sn": {"power": {"C": 1.0, "m": 1.0}}, "limits": {"damage": {"max": 30}}}
            txt = await call("simulate", kind="fatigue", params=fat)
            assert txt.startswith("OK fatigue: 4 cycles") and _val(txt, "damage") == 23, txt
            (logs / "strain.csv").write_text("s\n-2\n1\n-3\n5\n-1\n3\n-4\n4\n-9\n", encoding="utf-8")
            txt = await call("simulate", kind="fatigue", params=fat)
            assert "cached" not in txt and _val(txt, "max_range") == 14, txt   # 5 - (-9)
            txt = await call("simulate", kind="fatigue", params=dict(fat, files=["../outside.csv"]))
            assert txt.startswith("ERROR") and "relative to the project" in txt, txt

            txt = await call("simulate", kind="fem_static", params=load)
            assert txt.startswith("ERROR") and "needs target" in txt, txt
            txt = await call("simulate", kind="nope", params={})
            assert txt.startswith("ERROR") and "unknown kind" in txt, txt


def main() -> int:
    sys.stdout.reconfigure(errors="replace")      # cp1252 consoles: arrows in the verdicts
    home = _home()
    if not home or not (Path(home) / "atelier.json").is_file():
        print("ATELIER_SKIP: workshop not installed")
        return 0
    inst = Instance()
    inst.start()
    project = tempfile.mkdtemp(prefix="mcpfc_atelier_")
    try:
        asyncio.run(run(inst, project, home))
    finally:
        inst.stop()                     # FreeCAD works IN the project folder: stop it first
        shutil.rmtree(project, ignore_errors=True)
    print("ATELIER_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
