"""Real printed circuit boards with KiCad 10 (`kicad-cli`, env `sim` for the STEP check with gmsh).

Kinds:
- kicad_drc  design rules check of a .kicad_pcb: violations by type and severity, unconnected
             items, schematic parity when the project has its schematic; FAIL on any error
- kicad_erc  electrical rules check of a .kicad_sch
- kicad_fab  fabrication set: Gerber (every copper / mask / silk / edge layer), Excellon drill,
             pick-and-place, IPC-2581 -- refused unless the DRC is clean (like `export` and `check`)
- kicad_step 3D STEP of the board (+ component models), read back with gmsh: its bounding box is
             the board outline x thickness, ready for FreeCAD (`check_assembly` against the case)

Inputs: `board` / `schematic` = a file of the study folder (project files copied by the server);
the rules are the project's (.kicad_pro next to the board) or KiCad's defaults.

KiCad has no portable build: the owner installs it once (`winget install -e --id KiCad.KiCad`);
this adapter finds `kicad-cli.exe` under Program Files or the per-user install, takes the newest,
and runs it without a shell, in the study folder.

Security: kicad-cli is run on files of the study folder only; outputs stay there. KiCad's
scripting (pcbnew Python plugins, IPC API server) is never started.

Limits, restated in every result: the DRC checks the rules it is given -- the fab's own
capabilities (min track, min drill, annular ring) must be in the project's rules to be checked;
the STEP bounding box checks the outline and thickness, not every component model.

Exactness: REFERENCES below, on a board generated here (30 x 20 mm, 1.6 mm, two tracks of
different nets): 2 mm apart -> no violation; 0.10 mm apart -> a clearance violation (default
clearance 0.2 mm); the fabrication set has the expected layers; the STEP's box is 30 x 20 x 1.6.
"""
from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from _common import result, run_exe

ENV = "sim"
KINDS = {
    "kicad_drc": {"geometry": False, "timeout": 600,
                  "doc": "KiCad DRC of a .kicad_pcb: violations by type, unconnected items; FAIL on error"},
    "kicad_erc": {"geometry": False, "timeout": 600,
                  "doc": "KiCad ERC of a .kicad_sch: violations by type"},
    "kicad_fab": {"geometry": False, "timeout": 900,
                  "doc": "Gerber + drill + placement + IPC-2581 of a DRC-clean board"},
    "kicad_step": {"geometry": False, "timeout": 900,
                   "doc": "STEP of the board read back: bounding box = outline x thickness, for FreeCAD"},
}


def kicad_cli() -> Optional[str]:
    cands: List[str] = []
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramW6432", ""),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            cands += glob.glob(os.path.join(base, "KiCad", "*", "bin", "kicad-cli.exe"))
    if not cands:
        return None

    def ver(p: str):
        m = re.search(r"KiCad[\\/](\d+)\.(\d+)", p)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    return max(cands, key=ver)


def _cli() -> str:
    exe = kicad_cli()
    if not exe:
        raise FileNotFoundError("KiCad is not installed: winget install -e --id KiCad.KiCad "
                                "(then tools_status(refresh=True))")
    return exe


def versions() -> Dict[str, str]:
    exe = kicad_cli()
    if not exe:
        return {"kicad": "missing (not installed)"}
    r = run_exe([exe, "version"], Path.cwd(), 60)
    return {"kicad": (r.stdout or r.stderr).strip()[:40]}


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fn = {"kicad_drc": _drc, "kicad_erc": _erc, "kicad_fab": _fab, "kicad_step": _step}.get(kind)
    if fn is None:
        raise ValueError(f"unknown kind {kind!r}; one of {sorted(KINDS)}")
    return fn(inp, Path(work))


def _file(work: Path, name: Any, ext: str) -> Path:
    p = (work / str(name or "")).resolve()
    if work.resolve() not in p.parents or p.suffix.lower() != ext or not p.is_file():
        raise ValueError(f"{name!r}: a {ext} file inside the study folder")
    return p


def _report(kind: str, src: Path, work: Path, extra: List[str]) -> Dict[str, Any]:
    out = work / f"{kind}_report.json"
    sub = ["pcb", "drc"] if kind == "drc" else ["sch", "erc"]
    r = run_exe([_cli(), *sub, "--format", "json", "--severity-all", *extra, "-o", str(out), str(src)],
                work, 600)
    if not out.is_file():
        raise RuntimeError(f"kicad-cli {' '.join(sub)} wrote no report: {(r.stderr or r.stdout)[-400:]}")
    return json.loads(out.read_text(encoding="utf-8"))


def _count(viol: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_type: Dict[str, int] = {}
    errors = warnings = 0
    for v in viol:
        by_type[v.get("type", "?")] = by_type.get(v.get("type", "?"), 0) + 1
        sev = str(v.get("severity", "error")).lower()
        errors += sev == "error"
        warnings += sev == "warning"
    return {"by_type": by_type, "errors": errors, "warnings": warnings}


def _drc_values(board: Path, work: Path, parity: bool) -> Dict[str, Any]:
    rep = _report("drc", board, work, ["--schematic-parity"] if parity else [])
    viol = list(rep.get("violations") or [])
    unc = list(rep.get("unconnected_items") or [])
    par = list(rep.get("schematic_parity") or [])
    c = _count(viol + par)
    vals: Dict[str, Any] = {"errors": c["errors"], "warnings": c["warnings"], "violations": len(viol),
                            "unconnected": len(unc), "parity_issues": len(par)}
    for t, n in sorted(c["by_type"].items()):
        vals[f"type_{t}"] = n
    return vals


def _drc(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    board = _file(work, inp.get("board"), ".kicad_pcb")
    parity = board.with_suffix(".kicad_sch").is_file() and inp.get("schematic_parity", True)
    vals = _drc_values(board, work, bool(parity))
    limits = inp.get("limits") or {"errors": {"max": 0}, "unconnected": {"max": 0}}
    head = (f"KiCad DRC {board.name}: {vals['errors']} error(s), {vals['warnings']} warning(s), "
            f"{vals['unconnected']} unconnected")
    return result(vals, limits, head, files=["drc_report.json"],
                  notes=[", ".join(k[5:] + f" x{v}" for k, v in vals.items() if k.startswith("type_"))[:300]
                         or "no violation", "rules: the project's .kicad_pro, or KiCad's defaults"])


def _erc(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    sch = _file(work, inp.get("schematic"), ".kicad_sch")
    rep = _report("erc", sch, work, [])
    viol = [v for s in rep.get("sheets") or [] for v in s.get("violations") or []] or list(rep.get("violations") or [])
    c = _count(viol)
    vals: Dict[str, Any] = {"errors": c["errors"], "warnings": c["warnings"], "violations": len(viol)}
    for t, n in sorted(c["by_type"].items()):
        vals[f"type_{t}"] = n
    head = f"KiCad ERC {sch.name}: {vals['errors']} error(s), {vals['warnings']} warning(s)"
    return result(vals, inp.get("limits") or {"errors": {"max": 0}}, head, files=["erc_report.json"])


def _fab(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    board = _file(work, inp.get("board"), ".kicad_pcb")
    drc = _drc_values(board, work, False)
    if drc["errors"] or (drc["unconnected"] and not inp.get("allow_unconnected")):
        res = result(drc, None, f"fabrication refused: DRC has {drc['errors']} error(s), "
                     f"{drc['unconnected']} unconnected -- fix the board first")
        res["verdict"] = "FAIL"
        return res
    out = work / "fab"
    out.mkdir(exist_ok=True)
    cli = _cli()
    steps = [["pcb", "export", "gerbers", "-o", str(out) + os.sep, str(board)],
             ["pcb", "export", "drill", "-o", str(out) + os.sep, str(board)],
             ["pcb", "export", "pos", "--format", "csv", "--units", "mm", "-o", str(out / "placement.csv"), str(board)],
             ["pcb", "export", "ipc2581", "-o", str(out / "board.xml"), str(board)]]
    failed = []
    for a in steps:
        r = run_exe([cli, *a], work, 600)
        if r.returncode != 0:
            failed.append(f"{a[2]}: {(r.stderr or r.stdout).strip()[-200:]}")
    files = sorted(p.name for p in out.iterdir())
    gbr = [f for f in files if f.lower().endswith((".gbr", ".gtl", ".gbl", ".gto", ".gbo", ".gts", ".gbs", ".gm1"))]
    vals = {"gerber_files": len(gbr), "drill_files": len([f for f in files if f.lower().endswith((".drl", ".xln"))]),
            "has_edge_cuts": 1.0 if any("edge" in f.lower() for f in gbr) else 0.0,
            "has_ipc2581": 1.0 if "board.xml" in files else 0.0, "export_failures": len(failed)}
    head = f"KiCad fab {board.name}: {len(gbr)} Gerber, {vals['drill_files']} drill file(s), IPC-2581"
    res = result(vals, inp.get("limits") or {"export_failures": {"max": 0}}, head,
                 files=[f"fab/{f}" for f in files], notes=failed[:4] + ["DRC clean before export"])
    return res


def _step(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    board = _file(work, inp.get("board"), ".kicad_pcb")
    step = work / (board.stem + ".step")
    r = run_exe([_cli(), "pcb", "export", "step", "--subst-models", "--force", "-o", str(step), str(board)],
                work, 900)
    if not step.is_file():
        raise RuntimeError(f"kicad-cli wrote no STEP: {(r.stderr or r.stdout)[-400:]}")
    import gmsh
    gmsh.initialize(["-noenv"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.occ.importShapes(str(step))
        gmsh.model.occ.synchronize()
        x0, y0, z0, x1, y1, z1 = gmsh.model.getBoundingBox(-1, -1)
        n_solids = len(gmsh.model.getEntities(3))
    finally:
        gmsh.finalize()
    vals = {"size_x_mm": round(x1 - x0, 4), "size_y_mm": round(y1 - y0, 4), "size_z_mm": round(z1 - z0, 4),
            "solids": n_solids}
    head = f"KiCad STEP {step.name}: {vals['size_x_mm']} x {vals['size_y_mm']} x {vals['size_z_mm']} mm, {n_solids} solid(s)"
    return result(vals, inp.get("limits"), head, files=[step.name],
                  notes=["import into FreeCAD and run check_assembly against the enclosure"])


def _board_text(gap_mm: float) -> str:
    y2 = 8 + 0.25 + gap_mm
    return f"""(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (38 "B.Mask" user)
    (39 "F.Mask" user)
    (44 "Edge.Cuts" user)
  )
  (setup (pad_to_mask_clearance 0))
  (net 0 "")
  (net 1 "A")
  (net 2 "B")
  (gr_rect (start 0 0) (end 30 20) (stroke (width 0.1) (type default)) (fill none) (layer "Edge.Cuts"))
  (segment (start 5 8) (end 25 8) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 5 {y2:g}) (end 25 {y2:g}) (width 0.25) (layer "F.Cu") (net 2))
)
"""


def _setup_clean(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    (work / "clean.kicad_pcb").write_text(_board_text(2.0), encoding="utf-8")
    return dict(inp, board="clean.kicad_pcb")


def _setup_tight(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    (work / "tight.kicad_pcb").write_text(_board_text(0.10), encoding="utf-8")
    return dict(inp, board="tight.kicad_pcb", limits={})


REFERENCES = [
    {"name": "drc_clean_board", "kind": "kicad_drc", "setup": "_setup_clean", "inp": {},
     "expect": {"errors": [0, 0], "violations": [0, 0]}},
    {"name": "drc_finds_clearance_0p10mm", "kind": "kicad_drc", "setup": "_setup_tight", "inp": {},
     "expect": {"type_clearance": [1, 0]}},
    {"name": "fab_set_of_clean_board", "kind": "kicad_fab", "setup": "_setup_clean", "inp": {},
     "expect": {"export_failures": [0, 0], "has_edge_cuts": [1.0, 0], "has_ipc2581": [1.0, 0]}},
    {"name": "step_box_is_outline_x_thickness", "kind": "kicad_step", "setup": "_setup_clean", "inp": {},
     "expect": {"size_x_mm": [30.0, 2e-3], "size_y_mm": [20.0, 2e-3], "size_z_mm": [1.6, 5e-3]}},
]
