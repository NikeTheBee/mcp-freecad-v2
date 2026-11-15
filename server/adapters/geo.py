"""Soil and rock: groundwater flow, heat conduction, consolidation with OpenGeoSys 6 (env `geo`).

Kinds (no CAD geometry: the adapter builds a structured block mesh itself):
- ogs_steady     steady Darcy flow (head) or heat conduction (temperature) in a 1-D/2-D/3-D block
                 with layers of different conductivity; Dirichlet and Neumann sides; OGS
                 SteadyStateDiffusion. Outputs: field extremes, probe values, flow/heat through
                 each loaded side.
- consolidation  1-D Terzaghi consolidation of a clay layer: the excess pore pressure equation
                 du/dt = cv d2u/dz2 solved by OGS (HeatConduction process, transient), cross-checked
                 against the series solution; degree of consolidation and settlement vs time.
- borehole       closed forms around a well or a borehole (pure Python): Thiem (steady confined),
                 Theis (transient confined), steady radial conduction, Kelvin line source.

Units: SI. Lengths m; hydraulic conductivity K m/s; head m; thermal conductivity k W/(m K);
temperature degC; specific flux on a side m/s (= m3/s per m2) or W/m2, positive INTO the domain;
flows through a side in m3/s or W (2-D: times `thickness_m`, 1-D: times `area_m2`, default 1).
Consolidation: cv m2/s, mv 1/kPa, load kPa, settlement mm.

Sides: "xmin".."zmax" of the block. A side with no condition is impervious / adiabatic.
Exactness: REFERENCES below (linear head between two fixed heads, two layers in series, fixed
flux, Terzaghi U(Tv) from published tables, Theis/Kelvin well function from tables).
Limits: linear, isotropic or diagonal-anisotropic layers parallel to the block faces; saturated
steady flow (no free surface, no unsaturated zone); consolidation is Terzaghi's 1-D uncoupled
theory (small strain, constant cv and mv, no creep), not a full Biot hydro-mechanical model.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from _common import exe, result, run_exe

ENV = "geo"
KINDS = {
    "ogs_steady": {"geometry": False, "timeout": 900,
                   "doc": "steady groundwater head or heat conduction in a layered block (OpenGeoSys)"},
    "consolidation": {"geometry": False, "timeout": 600,
                      "doc": "1-D Terzaghi consolidation: degree U and settlement vs time (OGS + series)"},
    "borehole": {"geometry": False, "timeout": 60,
                 "doc": "well/borehole closed forms: Thiem, Theis, radial conduction, line source"},
}
MAX_NODES = 250_000
_AXES = "xyz"
_SIDES = {f"{a}{e}": (i, e == "max") for i, a in enumerate(_AXES) for e in ("min", "max")}
_PHYS = {"groundwater": {"field": "head", "unit": "m", "flow_unit": "m3/s"},
         "heat": {"field": "T", "unit": "C", "flow_unit": "W"}}


def versions() -> Dict[str, str]:
    import scipy
    out = run_exe([exe("ogs"), "--version"], Path.cwd(), 60)
    m = re.search(r"version:\s*([\w.]+)", out.stdout + out.stderr)
    return {"opengeosys": m.group(1) if m else "?", "numpy": np.__version__, "scipy": scipy.__version__}


# ------------------------------------------------------------------ VTU (ASCII, numpy only)
_VTK_VERTEX, _VTK_LINE, _VTK_QUAD, _VTK_HEX = 1, 3, 9, 12
# OGS requires it in every medium; no property used here depends on temperature
_REF_T = ("<property><name>reference_temperature</name><type>Constant</type>"
          "<value>293.15</value></property>")


def _write_vtu(path: Path, pts: np.ndarray, cells: np.ndarray, ctype: int,
               cell_int: Optional[Dict[str, np.ndarray]] = None) -> None:
    n_c, per = cells.shape
    p3 = np.zeros((len(pts), 3))
    p3[:, :pts.shape[1]] = pts
    lines = ['<?xml version="1.0"?>',
             '<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">',
             "<UnstructuredGrid>", f'<Piece NumberOfPoints="{len(p3)}" NumberOfCells="{n_c}">',
             "<Points>", '<DataArray type="Float64" NumberOfComponents="3" format="ascii">',
             "\n".join(" ".join(f"{v:.15g}" for v in row) for row in p3),
             "</DataArray>", "</Points>", "<Cells>",
             '<DataArray type="Int64" Name="connectivity" format="ascii">',
             "\n".join(" ".join(str(int(v)) for v in row) for row in cells), "</DataArray>",
             '<DataArray type="Int64" Name="offsets" format="ascii">',
             " ".join(str(per * (i + 1)) for i in range(n_c)), "</DataArray>",
             '<DataArray type="UInt8" Name="types" format="ascii">',
             " ".join([str(ctype)] * n_c), "</DataArray>", "</Cells>"]
    if cell_int:
        lines.append("<CellData>")
        for name, arr in cell_int.items():
            lines += [f'<DataArray type="Int32" Name="{name}" format="ascii">',
                      " ".join(str(int(v)) for v in arr), "</DataArray>"]
        lines.append("</CellData>")
    lines += ["</Piece>", "</UnstructuredGrid>", "</VTKFile>"]
    path.write_text("\n".join(lines), encoding="utf-8")


def _read_point_array(path: Path, name: str) -> np.ndarray:
    """A PointData array of an ASCII VTU written by OGS (data_mode Ascii)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    pd = text[text.find("<PointData"):text.find("</PointData>")]
    m = re.search(r'<DataArray[^>]*Name="%s"[^>]*>(.*?)</DataArray>' % re.escape(name), pd, re.S)
    if not m:
        raise RuntimeError(f"OGS output {path.name} has no point array {name!r}")
    return np.array(m.group(1).split(), dtype=float)


# ------------------------------------------------------------------ structured block
def _axis_nodes(length: float, n: int, cuts: List[float]) -> np.ndarray:
    """Node coordinates on [0, length] with nodes on every layer interface."""
    edges = [0.0, *cuts, length]
    out = [0.0]
    for a, b in zip(edges[:-1], edges[1:]):
        k = max(2, int(round(n * (b - a) / length)))
        out += list(np.linspace(a, b, k + 1)[1:])
    return np.array(out)


def _block(inp: Dict[str, Any]):
    """coords per axis, layer cuts along the layer axis, material id per cell, conductivities."""
    size = [float(v) for v in (inp.get("size_m") or [])]
    layers = inp.get("layers") or []
    dim = len(size)
    if dim not in (1, 2, 3):
        raise ValueError("size_m: [Lx] (1-D), [Lx, Ly] (2-D) or [Lx, Ly, Lz] (3-D), in m")
    if any(not (v > 0) for v in size):
        raise ValueError("size_m: every length must be > 0")
    ax = _AXES.index(str(inp.get("layer_axis", _AXES[dim - 1])))
    if ax >= dim:
        raise ValueError(f"layer_axis {_AXES[ax]!r} does not exist in a {dim}-D block")
    if layers:
        th = [float(l["thickness_m"]) for l in layers]
        if any(t <= 0 for t in th):
            raise ValueError("layers: thickness_m must be > 0")
        if abs(sum(th) - size[ax]) > 1e-9 * size[ax]:
            raise ValueError(f"layers: thicknesses sum to {sum(th):g} m but size_m along "
                             f"{_AXES[ax]} is {size[ax]:g} m")
        conds = [layer["conductivity"] for layer in layers]
    else:
        th = [size[ax]]
        if "conductivity" not in inp:
            raise ValueError("conductivity (K m/s or k W/(m K)) or layers[] is required")
        conds = [inp["conductivity"]]
    kk = []
    for c in conds:
        v = [float(x) for x in (c if isinstance(c, (list, tuple)) else [c])]
        if len(v) not in (1, dim) or any(not (x > 0) for x in v):
            raise ValueError(f"conductivity {c!r}: a positive number or {dim} values (diagonal)")
        kk.append(v * dim if len(v) == 1 else v)
    default_cells = {1: [100], 2: [60, 30], 3: [30, 12, 8]}[dim]
    cells = [int(c) for c in (inp.get("cells") or default_cells)]
    if len(cells) != dim or any(c < 1 for c in cells):
        raise ValueError(f"cells: {dim} positive integers")
    cuts = list(np.cumsum(th)[:-1])
    coords = [_axis_nodes(size[d], cells[d], cuts if d == ax else []) for d in range(dim)]
    n_nodes = int(np.prod([len(c) for c in coords]))
    if n_nodes > MAX_NODES:
        raise ValueError(f"{n_nodes} nodes > {MAX_NODES}: fewer cells")
    centers = 0.5 * (coords[ax][1:] + coords[ax][:-1])
    mat_axis = np.searchsorted(np.array(cuts), centers, side="right")
    return dim, size, coords, ax, mat_axis, np.array(kk, dtype=float), th


def _mesh_arrays(dim: int, coords: List[np.ndarray]):
    """Points (x fastest) and cells in VTK order."""
    n = [len(c) for c in coords] + [1] * (3 - dim)
    grids = np.meshgrid(*[coords[d] if d < dim else np.zeros(1) for d in range(3)], indexing="ij")
    pts = np.stack([g.transpose(2, 1, 0).ravel() for g in grids[:dim]], axis=1)

    def nid(i, j, k):
        return i + n[0] * (j + n[1] * k)

    I, J, K = np.meshgrid(np.arange(max(n[0] - 1, 1)), np.arange(max(n[1] - 1, 1)),
                          np.arange(max(n[2] - 1, 1)), indexing="ij")
    I, J, K = (a.transpose(2, 1, 0).ravel() for a in (I, J, K))
    if dim == 1:
        J = K = np.zeros_like(I)
        cells = np.stack([nid(I, 0, 0), nid(I + 1, 0, 0)], 1)
        ijk = np.stack([I], 1)
    elif dim == 2:
        K = np.zeros_like(I)
        cells = np.stack([nid(I, J, 0), nid(I + 1, J, 0), nid(I + 1, J + 1, 0), nid(I, J + 1, 0)], 1)
        ijk = np.stack([I, J], 1)
    else:
        cells = np.stack([nid(I, J, K), nid(I + 1, J, K), nid(I + 1, J + 1, K), nid(I, J + 1, K),
                          nid(I, J, K + 1), nid(I + 1, J, K + 1), nid(I + 1, J + 1, K + 1),
                          nid(I, J + 1, K + 1)], 1)
        ijk = np.stack([I, J, K], 1)
    return pts, cells, ijk, n


def _side_mesh(dim: int, pts: np.ndarray, n: List[int], side: str):
    """(node ids, cells in local numbering, cell type) of one side of the block."""
    d, top = _SIDES[side]
    if d >= dim:
        raise ValueError(f"side {side!r} does not exist in a {dim}-D block")
    idx = np.arange(len(pts)).reshape(n[2], n[1], n[0])           # [k, j, i]
    sl = [slice(None)] * 3
    sl[2 - d] = (n[d] - 1) if top else 0
    face = idx[tuple(sl)]                                         # 2-D array of node ids
    if dim == 1:
        return face.ravel(), np.zeros((1, 1), dtype=int), _VTK_VERTEX
    ids = face.ravel()
    loc = np.arange(ids.size).reshape(face.shape)
    if dim == 2:
        line = loc.ravel()
        return ids, np.stack([line[:-1], line[1:]], 1), _VTK_LINE
    a, b = loc.shape
    q = np.stack([loc[:-1, :-1].ravel(), loc[:-1, 1:].ravel(), loc[1:, 1:].ravel(),
                  loc[1:, :-1].ravel()], 1)
    return ids, q, _VTK_QUAD


# ------------------------------------------------------------------ conservative side flows
def _stiffness(dim: int, coords: List[np.ndarray], ijk: np.ndarray, cells: np.ndarray,
               kcell: np.ndarray, n_nodes: int):
    """Global matrix of -div(K grad u) for the multilinear block elements (exact integration)."""
    from scipy.sparse import coo_matrix
    S = np.array([[1.0, -1.0], [-1.0, 1.0]])
    M = np.array([[1 / 3, 1 / 6], [1 / 6, 1 / 3]])
    h = np.stack([np.diff(coords[d])[ijk[:, d]] for d in range(dim)], 1)   # (ne, dim)
    # local node order of the kron product: bits (x, y, z) with x slowest
    kron_to_vtk = {1: [0, 1], 2: [0, 3, 1, 2], 3: [0, 4, 3, 7, 1, 5, 2, 6]}[dim]
    ne = len(cells)
    nl = 2 ** dim
    Ke = np.zeros((ne, nl, nl))
    for d in range(dim):
        mats = []
        for e in range(dim):
            mats.append((S[None] / h[:, e, None, None]) if e == d else (M[None] * h[:, e, None, None]))
        A = mats[0]
        for B in mats[1:]:
            A = np.einsum("nab,ncd->nacbd", A, B).reshape(ne, A.shape[1] * 2, A.shape[2] * 2)
        Ke += kcell[:, d, None, None] * A
    g = cells[:, kron_to_vtk]
    rows = np.repeat(g, nl, axis=1).ravel()
    cols = np.tile(g, (1, nl)).ravel()
    return coo_matrix((Ke.ravel(), (rows, cols)), shape=(n_nodes, n_nodes)).tocsr()


def _side_area(dim: int, size: List[float], side: str, depth: float) -> float:
    d, _ = _SIDES[side]
    if dim == 1:
        return depth
    if dim == 2:
        return size[1 - d] * depth
    return float(np.prod([size[e] for e in range(3) if e != d]))


# ------------------------------------------------------------------ project file
def _prj_steady(meshes: List[str], kk: np.ndarray, field: str, dirichlet, neumann) -> str:
    media = "\n".join(
        f'<medium id="{i}"><properties><property><name>diffusion</name><type>Constant</type>'
        f'<value>{" ".join(f"{v:.12g}" for v in (k if len(set(k)) > 1 else k[:1]))}</value>'
        f"</property>{_REF_T}</properties></medium>" for i, k in enumerate(kk))
    params = ['<parameter><name>u0</name><type>Constant</type><value>0</value></parameter>']
    bcs = []
    for i, bc in enumerate(dirichlet):
        params.append(f'<parameter><name>d{i}</name><type>Constant</type><value>{float(bc["value"]):.15g}</value></parameter>')
        bcs.append(f'<boundary_condition><mesh>bc_{bc["side"]}</mesh><type>Dirichlet</type><parameter>d{i}</parameter></boundary_condition>')
    for i, bc in enumerate(neumann):
        params.append(f'<parameter><name>n{i}</name><type>Constant</type><value>{float(bc["flux"]):.15g}</value></parameter>')
        bcs.append(f'<boundary_condition><mesh>bc_{bc["side"]}</mesh><type>Neumann</type><parameter>n{i}</parameter></boundary_condition>')
    return f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<OpenGeoSysProject>
 <meshes>{"".join(f"<mesh>{m}</mesh>" for m in meshes)}</meshes>
 <processes><process><name>SSD</name><type>STEADY_STATE_DIFFUSION</type><integration_order>2</integration_order>
  <process_variables><process_variable>{field}</process_variable></process_variables>
 </process></processes>
 <media>{media}</media>
 <time_loop>
  <processes><process ref="SSD">
   <nonlinear_solver>picard</nonlinear_solver>
   <convergence_criterion><type>DeltaX</type><norm_type>NORM2</norm_type><abstol>1e-12</abstol></convergence_criterion>
   <time_discretization><type>BackwardEuler</type></time_discretization>
   <time_stepping><type>SingleStep</type></time_stepping>
  </process></processes>
  <output><type>VTK</type><prefix>out</prefix><data_mode>Ascii</data_mode><compress_output>false</compress_output>
   <timesteps><pair><repeat>1</repeat><each_steps>1</each_steps></pair></timesteps>
   <variables><variable>{field}</variable></variables>
  </output>
 </time_loop>
 <parameters>{"".join(params)}</parameters>
 <process_variables><process_variable><name>{field}</name><components>1</components><order>1</order>
  <initial_condition>u0</initial_condition>
  <boundary_conditions>{"".join(bcs)}</boundary_conditions>
 </process_variable></process_variables>
 <nonlinear_solvers><nonlinear_solver><name>picard</name><type>Picard</type><max_iter>4</max_iter>
  <linear_solver>ls</linear_solver></nonlinear_solver></nonlinear_solvers>
 <linear_solvers><linear_solver><name>ls</name><eigen><solver_type>SparseLU</solver_type><scaling>true</scaling></eigen></linear_solver></linear_solvers>
</OpenGeoSysProject>
"""


def _ogs(work: Path, prj: str, timeout: float) -> None:
    (work / "model.prj").write_text(prj, encoding="utf-8")
    proc = run_exe([exe("ogs"), "-o", str(work.resolve()), "model.prj"], work, timeout)
    log = proc.stdout + proc.stderr
    (work / "ogs.log").write_text(log, encoding="utf-8")
    if proc.returncode != 0 or "critical:" in log.lower():
        bad = [l for l in log.splitlines() if "error" in l.lower() or "critical" in l.lower()]
        raise RuntimeError(f"OpenGeoSys failed (code {proc.returncode}): " + " | ".join(bad[-4:])[:500])


def _identify(work: Path, bulk: str, subs: List[str], tol: float) -> None:
    proc = run_exe([exe("identifySubdomains"), "-m", bulk, "-s", f"{tol:.6g}", "-o", "bc_", "-f",
                    "--", *subs], work, 300)
    if proc.returncode != 0:
        raise RuntimeError("identifySubdomains failed: " + (proc.stdout + proc.stderr)[-400:])


def _last_output(work: Path) -> Path:
    outs = sorted(work.glob("out_ts_*_t_*.vtu"), key=lambda p: int(re.search(r"_ts_(\d+)_", p.name).group(1)))
    if not outs:
        raise RuntimeError("OpenGeoSys wrote no result file")
    return outs[-1]


# ------------------------------------------------------------------ ogs_steady
def _steady(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    phys = str(inp.get("physics", "groundwater"))
    if phys not in _PHYS:
        raise ValueError(f"physics: one of {sorted(_PHYS)}")
    P = _PHYS[phys]
    dim, size, coords, ax, mat_axis, kk, th = _block(inp)
    pts, cells, ijk, n = _mesh_arrays(dim, coords)
    mat = mat_axis[ijk[:, ax]]
    dirichlet = list(inp.get("dirichlet") or [])
    neumann = list(inp.get("neumann") or [])
    if not dirichlet:
        raise ValueError("dirichlet: at least one side with a fixed head/temperature")
    used = [b["side"] for b in dirichlet + neumann]
    if len(set(used)) != len(used):
        raise ValueError("one condition per side")
    for s in used:
        if s not in _SIDES or _SIDES[s][0] >= dim:
            raise ValueError(f"side {s!r}: one of {[k for k, v in _SIDES.items() if v[0] < dim]}")
    depth = float(inp.get("area_m2" if dim == 1 else "thickness_m", 1.0)) if dim < 3 else 1.0
    if not depth > 0:
        raise ValueError("area_m2 / thickness_m must be > 0")
    ctype = {1: _VTK_LINE, 2: _VTK_QUAD, 3: _VTK_HEX}[dim]
    _write_vtu(work / "domain.vtu", pts, cells, ctype, {"MaterialIDs": mat})
    side_nodes = {}
    for s in used:
        ids, sc, st = _side_mesh(dim, pts, n, s)
        side_nodes[s] = ids
        _write_vtu(work / f"{s}.vtu", pts[ids], sc, st)
    tol = 1e-6 * max(size)
    _identify(work, "domain.vtu", [f"{s}.vtu" for s in used], tol)
    field = "u"
    prj = _prj_steady(["domain.vtu", *[f"bc_{s}.vtu" for s in used]], kk, field, dirichlet, neumann)
    _ogs(work, prj, float(inp.get("timeout_s", 800)))
    u = _read_point_array(_last_output(work), field)
    if len(u) != len(pts) or not np.all(np.isfinite(u)):
        raise RuntimeError("OpenGeoSys result does not match the mesh")
    # conservative flows through the sides: residual of the discrete balance (reaction)
    A = _stiffness(dim, coords, ijk, cells, kk[mat], len(pts))
    R = A @ u
    f = np.zeros(len(pts))
    values: Dict[str, Any] = {"nodes": int(len(pts)), "elements": int(len(cells))}
    fu, un = P["field"], P["unit"]
    values[f"{fu}_max_{un}"], values[f"{fu}_min_{un}"] = float(u.max()), float(u.min())
    for bc in neumann:
        s = bc["side"]
        q_in = float(bc["flux"]) * _side_area(dim, size, s, depth)
        values[f"flow_in_{s}"] = q_in
    # nodal Neumann loads (to remove from the residual at shared corner nodes)
    for bc in neumann:
        d, top = _SIDES[bc["side"]]
        ids = side_nodes[bc["side"]]
        if dim == 1:
            f[ids] += float(bc["flux"])
        else:
            others = [e for e in range(dim) if e != d]
            w = np.ones(len(ids))
            sub = np.unravel_index(np.arange(len(ids)), tuple(len(coords[e]) for e in reversed(others)))
            for e, idxs in zip(reversed(others), sub):
                c = coords[e]
                lw = np.zeros(len(c))
                lw[:-1] += np.diff(c) / 2
                lw[1:] += np.diff(c) / 2
                w = w * lw[idxs]
            f[ids] += float(bc["flux"]) * w
    react = R - f
    dir_sides = [b["side"] for b in dirichlet]
    owner = {}
    for s in dir_sides:
        for i in side_nodes[s]:
            owner.setdefault(int(i), []).append(s)
    shared = False
    for s in dir_sides:
        tot = 0.0
        for i in side_nodes[s]:
            o = owner[int(i)]
            shared |= len(o) > 1
            tot += react[i] / len(o)
        mult = depth if dim < 3 else 1.0
        values[f"flow_in_{s}"] = float(tot * mult)
    # Neumann residual check at its nodes is zero by construction; global balance
    total = sum(values[f"flow_in_{b['side']}"] for b in dirichlet + neumann)
    scale = max(1e-300, max(abs(values[f"flow_in_{b['side']}"]) for b in dirichlet + neumann))
    values["balance_error_rel"] = abs(total) / scale if scale > 1e-300 else 0.0
    # probes (multilinear interpolation on the grid)
    from scipy.interpolate import RegularGridInterpolator
    grid = u.reshape([len(c) for c in reversed(coords)]).transpose(list(range(dim))[::-1])
    interp = RegularGridInterpolator(tuple(coords), grid)
    for i, p in enumerate(inp.get("probes") or []):
        pp = [float(v) for v in p][:dim]
        if len(pp) != dim:
            raise ValueError(f"probe {p!r}: {dim} coordinates in m")
        values[f"probe_{i}"] = float(interp([pp])[0])
    values["equivalent_conductivity_series"] = float(sum(th) / sum(t / k[ax] for t, k in zip(th, kk)))
    flows = ", ".join(f"{s} {values[f'flow_in_{s}']:.4g}" for s in used)
    head = (f"ogs_steady {phys} {dim}-D ({len(pts)} nodes, {len(kk)} layer(s)): {fu} "
            f"{u.min():.4g}..{u.max():.4g} {un}; flow in ({P['flow_unit']}): {flows}")
    notes = [f"balance error {values['balance_error_rel']:.2e}"]
    if shared:
        notes.append("nodes shared by two fixed sides: their flow is split half and half")
    if dim < 3:
        notes.append(f"flows are per {'area_m2' if dim == 1 else 'thickness_m'} = {depth:g}")
    return result(values, inp.get("limits"), head, ["domain.vtu", _last_output(work).name, "model.prj"], notes)


# ------------------------------------------------------------------ consolidation
_TV_LAST = 3.0


def terzaghi_U(tv: float, terms: int = 400) -> float:
    """Average degree of consolidation, Terzaghi series: U = 1 - sum 2/M^2 exp(-M^2 Tv)."""
    if tv <= 0:
        return 0.0
    m = np.arange(terms)
    M = np.pi * (2 * m + 1) / 2
    return float(1 - np.sum(2 / M ** 2 * np.exp(-M ** 2 * tv)))


def _consolidation(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    H = float(inp["thickness_m"])
    drainage = str(inp.get("drainage", "top"))
    if drainage not in ("top", "both"):
        raise ValueError("drainage: 'top' (one drained face) or 'both'")
    hdr = H if drainage == "top" else H / 2
    cv = float(inp["cv_m2_s"])
    mv = float(inp["mv_1_per_kPa"])
    load = float(inp["load_kPa"])
    times = [float(t) for t in (inp.get("times_s") or [])]
    if not (H > 0 and cv > 0 and mv > 0) or not times or any(t <= 0 for t in times):
        raise ValueError("thickness_m, cv_m2_s, mv_1_per_kPa > 0 and times_s (list of t > 0, s)")
    tvs = [cv * t / hdr ** 2 for t in times]
    ne = int(inp.get("cells", 200))
    # dimensionless problem on the drainage path: z' in [0, 1], drained at z'=0, Tv as time
    z = np.linspace(0.0, 1.0, ne + 1)
    _write_vtu(work / "column.vtu", z[:, None], np.stack([np.arange(ne), np.arange(1, ne + 1)], 1),
               _VTK_LINE, {"MaterialIDs": np.zeros(ne, dtype=int)})
    _write_vtu(work / "drained.vtu", np.array([[0.0]]), np.zeros((1, 1), dtype=int), _VTK_VERTEX)
    _identify(work, "column.vtu", ["drained.vtu"], 1e-9)
    t_end = max(tvs)
    grid = (set(np.geomspace(1e-7, t_end, 1200).tolist()) | set(np.linspace(0, t_end, 801)[1:].tolist())
            | set(tvs))
    steps = sorted(t for t in grid if t <= t_end)
    dts, t_prev = [], 0.0
    for t in steps:
        if t - t_prev > 1e-15:
            dts.append(t - t_prev)
            t_prev = t
    pairs = "".join(f"<pair><repeat>1</repeat><delta_t>{d:.17g}</delta_t></pair>" for d in dts)
    out_times = " ".join(f"{t:.17g}" for t in sorted(set(tvs)))
    prj = f"""<?xml version="1.0" encoding="ISO-8859-1"?>
<OpenGeoSysProject>
 <meshes><mesh>column.vtu</mesh><mesh>bc_drained.vtu</mesh></meshes>
 <processes><process><name>HC</name><type>HEAT_CONDUCTION</type><integration_order>2</integration_order>
  <process_variables><process_variable>u</process_variable></process_variables>
 </process></processes>
 <media><medium id="0"><properties>
  <property><name>thermal_conductivity</name><type>Constant</type><value>1</value></property>
  <property><name>specific_heat_capacity</name><type>Constant</type><value>1</value></property>
  <property><name>density</name><type>Constant</type><value>1</value></property>
  {_REF_T}
 </properties></medium></media>
 <time_loop>
  <processes><process ref="HC">
   <nonlinear_solver>picard</nonlinear_solver>
   <convergence_criterion><type>DeltaX</type><norm_type>NORM2</norm_type><abstol>1e-12</abstol></convergence_criterion>
   <time_discretization><type>BackwardEuler</type></time_discretization>
   <time_stepping><type>FixedTimeStepping</type><t_initial>0</t_initial><t_end>{t_end:.17g}</t_end>
    <timesteps>{pairs}</timesteps></time_stepping>
  </process></processes>
  <output><type>VTK</type><prefix>out</prefix><data_mode>Ascii</data_mode><compress_output>false</compress_output>
   <timesteps><pair><repeat>1</repeat><each_steps>100000000</each_steps></pair></timesteps>
   <fixed_output_times>{out_times}</fixed_output_times>
   <variables><variable>u</variable></variables>
  </output>
 </time_loop>
 <parameters>
  <parameter><name>u0</name><type>Constant</type><value>1</value></parameter>
  <parameter><name>zero</name><type>Constant</type><value>0</value></parameter>
 </parameters>
 <process_variables><process_variable><name>u</name><components>1</components><order>1</order>
  <initial_condition>u0</initial_condition>
  <boundary_conditions><boundary_condition><mesh>bc_drained</mesh><type>Dirichlet</type><parameter>zero</parameter></boundary_condition></boundary_conditions>
 </process_variable></process_variables>
 <nonlinear_solvers><nonlinear_solver><name>picard</name><type>Picard</type><max_iter>4</max_iter>
  <linear_solver>ls</linear_solver></nonlinear_solver></nonlinear_solvers>
 <linear_solvers><linear_solver><name>ls</name><eigen><solver_type>SparseLU</solver_type><scaling>true</scaling></eigen></linear_solver></linear_solvers>
</OpenGeoSysProject>
"""
    _ogs(work, prj, float(inp.get("timeout_s", 500)))
    outs = {}
    pvd = (work / "out.pvd").read_text(encoding="utf-8") if (work / "out.pvd").is_file() else ""
    for ts, fname in re.findall(r'timestep="([^"]+)"[^>]*file="([^"]+)"', pvd):
        if re.fullmatch(r"out_ts_\d+_t_[0-9.]+\.vtu", fname) and (work / fname).is_file():
            outs[float(ts)] = work / fname
    values: Dict[str, Any] = {"drainage_path_m": hdr, "final_settlement_mm": mv * load * H * 1000}
    worst = 0.0
    for i, (t, tv) in enumerate(zip(times, tvs)):
        key = min(outs, key=lambda x: abs(x - tv)) if outs else None
        if key is None or abs(key - tv) > 1e-6 * max(tv, 1e-12):
            raise RuntimeError(f"OpenGeoSys wrote no result at Tv = {tv:g}")
        u = _read_point_array(outs[key], "u")
        U = 1.0 - float(np.trapezoid(u, z))
        Us = terzaghi_U(tv)
        worst = max(worst, abs(U - Us))
        values[f"Tv_{i}"] = tv
        values[f"U_{i}"] = U
        values[f"U_series_{i}"] = Us
        values[f"settlement_mm_{i}"] = U * values["final_settlement_mm"]
    values["fe_vs_series_max_dU"] = worst
    values["t50_s"] = 0.197 * hdr ** 2 / cv
    values["t90_s"] = 0.848 * hdr ** 2 / cv
    last = len(times) - 1
    head = (f"consolidation (Terzaghi 1-D, H {H:g} m, drainage {drainage}): U = "
            + ", ".join(f"{values[f'U_{i}']:.3f} at {t:g} s" for i, t in enumerate(times))
            + f"; settlement {values[f'settlement_mm_{last}']:.4g} mm of {values['final_settlement_mm']:.4g} mm")
    notes = [f"OGS vs series max |dU| {worst:.4f}",
             f"t50 {values['t50_s']:.4g} s, t90 {values['t90_s']:.4g} s (Tv 0.197 / 0.848)"]
    res = result(values, inp.get("limits"), head, ["model.prj"], notes)
    if worst > 0.02:
        res["verdict"] = "UNKNOWN"
        res["summary"] += "; OGS and the series disagree by more than 0.02: not converged (more cells)"
    return res


# ------------------------------------------------------------------ borehole (closed forms)
def _borehole(inp: Dict[str, Any]) -> Dict[str, Any]:
    from scipy.special import exp1
    mode = str(inp.get("mode", ""))
    v: Dict[str, Any] = {}
    if mode == "thiem":
        K, b = float(inp["K_m_s"]), float(inp["aquifer_thickness_m"])
        rw, R = float(inp["well_radius_m"]), float(inp["radius_of_influence_m"])
        if not (K > 0 and b > 0 and 0 < rw < R):
            raise ValueError("thiem: K_m_s, aquifer_thickness_m > 0, 0 < well_radius_m < radius_of_influence_m")
        T = K * b
        if "pumping_m3_s" in inp:
            Q = float(inp["pumping_m3_s"])
        else:
            Q = 2 * math.pi * T * float(inp["drawdown_m"]) / math.log(R / rw)
        v["pumping_m3_s"] = Q
        v["drawdown_well_m"] = Q / (2 * math.pi * T) * math.log(R / rw)
        for i, r in enumerate(inp.get("probe_radii_m") or []):
            r = float(r)
            v[f"drawdown_{i}_m"] = Q / (2 * math.pi * T) * math.log(R / r) if rw <= r <= R else 0.0
        head = f"Thiem (steady, confined): Q {Q:.4g} m3/s, drawdown at the well {v['drawdown_well_m']:.4g} m"
    elif mode == "theis":
        T = float(inp["K_m_s"]) * float(inp["aquifer_thickness_m"])
        S, Q = float(inp["storativity"]), float(inp["pumping_m3_s"])
        r, t = float(inp["radius_m"]), float(inp["time_s"])
        uu = r * r * S / (4 * T * t)
        v["u"] = uu
        v["W_u"] = float(exp1(uu))
        v["drawdown_m"] = Q / (4 * math.pi * T) * v["W_u"]
        head = f"Theis (transient, confined): u {uu:.4g}, W(u) {v['W_u']:.5g}, drawdown {v['drawdown_m']:.4g} m"
    elif mode == "radial_heat":
        k = float(inp["k_W_mK"])
        r1, r2 = float(inp["r_inner_m"]), float(inp["r_outer_m"])
        T1, T2 = float(inp["T_inner_C"]), float(inp["T_outer_C"])
        L = float(inp.get("length_m", 1.0))
        if not (k > 0 and 0 < r1 < r2):
            raise ValueError("radial_heat: k_W_mK > 0, 0 < r_inner_m < r_outer_m")
        q = 2 * math.pi * k * (T1 - T2) / math.log(r2 / r1)
        v["heat_W_per_m"], v["heat_W"] = q, q * L
        head = f"steady radial conduction: {q:.4g} W/m, {q * L:.4g} W over {L:g} m"
    elif mode == "line_source":
        q, k = float(inp["q_W_per_m"]), float(inp["k_W_mK"])
        alpha = float(inp["diffusivity_m2_s"])
        r, t = float(inp["radius_m"]), float(inp["time_s"])
        uu = r * r / (4 * alpha * t)
        v["u"] = uu
        v["E1_u"] = float(exp1(uu))
        v["delta_T_K"] = q / (4 * math.pi * k) * v["E1_u"]
        head = f"Kelvin line source: dT {v['delta_T_K']:.4g} K at r {r:g} m after {t:g} s"
    else:
        raise ValueError("mode: thiem | theis | radial_heat | line_source")
    return result(v, inp.get("limits"), head, [], ["closed form: homogeneous, isotropic, infinite medium"])


# ------------------------------------------------------------------ entry
def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    if kind == "ogs_steady":
        return _steady(inp, work)
    if kind == "consolidation":
        return _consolidation(inp, work)
    if kind == "borehole":
        return _borehole(inp)
    raise ValueError(f"unknown kind {kind!r}")


# Keq of layers in series: L / sum(Li/Ki); interface head from the common specific discharge
_Q2 = (10 / (4 / 1e-4 + 6 / 1e-5)) * 10 / 10            # specific discharge, m/s
REFERENCES = [
    {   # 1-D, two fixed heads: linear head, Q = K A dh / L
        "name": "darcy_1d_two_heads", "kind": "ogs_steady",
        "inp": {"size_m": [10], "conductivity": 1e-4, "cells": [50], "area_m2": 1,
                "dirichlet": [{"side": "xmin", "value": 10}, {"side": "xmax", "value": 5}],
                "probes": [[2.5]]},
        "expect": {"flow_in_xmin": [1e-4 * 1 * 5 / 10, 1e-6], "flow_in_xmax": [-5e-5, 1e-6],
                   "probe_0": [8.75, 1e-6]},
    },
    {   # 3-D, two layers in series along x: K_eq = L / (L1/K1 + L2/K2)
        "name": "darcy_3d_two_layers_series", "kind": "ogs_steady",
        "inp": {"size_m": [10, 2, 1], "layer_axis": "x", "cells": [20, 4, 2],
                "layers": [{"thickness_m": 4, "conductivity": 1e-4},
                           {"thickness_m": 6, "conductivity": 1e-5}],
                "dirichlet": [{"side": "xmin", "value": 10}, {"side": "xmax", "value": 0}],
                "probes": [[4, 1, 0.5]]},
        "expect": {"flow_in_xmin": [_Q2 * 2 * 1, 1e-6], "probe_0": [10 - _Q2 * 4 / 1e-4, 1e-6],
                   "equivalent_conductivity_series": [1.5625e-5, 1e-9]},
    },
    {   # 2-D heat: fixed flux q at xmin, fixed T at xmax: T(xmin) = T1 + q L / k
        "name": "heat_2d_fixed_flux", "kind": "ogs_steady",
        "inp": {"physics": "heat", "size_m": [1, 0.5], "conductivity": 2.0, "cells": [20, 10],
                "thickness_m": 1,
                "neumann": [{"side": "xmin", "flux": 50}],
                "dirichlet": [{"side": "xmax", "value": 10}]},
        "expect": {"T_max_C": [10 + 50 * 1 / 2, 1e-6], "flow_in_xmax": [-50 * 0.5, 1e-6]},
    },
    {   # Terzaghi: published U(Tv) table, U = 50 % at Tv = 0.197, 90 % at Tv = 0.848
        "name": "terzaghi_U50_U90", "kind": "consolidation",
        "inp": {"thickness_m": 1.0, "drainage": "top", "cv_m2_s": 1e-7, "mv_1_per_kPa": 1e-3,
                "load_kPa": 100, "times_s": [0.197e7, 0.848e7]},
        "expect": {"U_0": [0.50, 0.01], "U_1": [0.90, 0.01], "settlement_mm_1": [90.0, 0.01],
                   "final_settlement_mm": [100.0, 1e-9]},
    },
    {   # Theis well function table: W(0.01) = 4.0379; Kelvin line source with E1(1) = 0.2194
        "name": "theis_W_table", "kind": "borehole",
        "inp": {"mode": "theis", "K_m_s": 1e-4, "aquifer_thickness_m": 10, "storativity": 1e-4,
                "pumping_m3_s": 0.01, "radius_m": 20, "time_s": 1e-4 * 400 / (4 * 1e-3 * 0.01)},
        "expect": {"W_u": [4.0379, 1e-4], "drawdown_m": [0.01 / (4 * math.pi * 1e-3) * 4.0379, 1e-4]},
    },
    {
        "name": "line_source_E1", "kind": "borehole",
        "inp": {"mode": "line_source", "q_W_per_m": 40, "k_W_mK": 2.0, "diffusivity_m2_s": 1e-6,
                "radius_m": 0.1, "time_s": 0.01 / (4 * 1e-6)},
        "expect": {"E1_u": [0.21938, 1e-4], "delta_T_K": [40 / (4 * math.pi * 2.0) * 0.21938, 1e-4]},
    },
    {   # Thiem with R / rw = e: Q = 2 pi K b s
        "name": "thiem_ln_e", "kind": "borehole",
        "inp": {"mode": "thiem", "K_m_s": 1e-4, "aquifer_thickness_m": 10, "well_radius_m": 0.1,
                "radius_of_influence_m": 0.1 * math.e, "drawdown_m": 2.0},
        "expect": {"pumping_m3_s": [2 * math.pi * 1e-3 * 2.0, 1e-9]},
    },
]
