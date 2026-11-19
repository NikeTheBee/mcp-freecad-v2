"""Computational fluid dynamics with SU2 and Elmer on Gmsh meshes (env `sim`, tools `su2`, `elmer`).

Kinds:
- cfd_su2          SU2 on the study geometry (STEP, a solid): the solid IS the fluid
                   (`domain="internal"`: duct, nozzle, manifold) or an obstacle in a box
                   (`domain="external"`: body in a stream). 3-D, tetrahedra.
- cfd_su2_shape    SU2 on a built-in 2-D shape: `channel`, `ramp` (wedge), `airfoil`
                   (NACA 4-digit or coordinates). Structured quads where the shape allows.
- flow_elmer       Elmer FlowSolve (+ HeatSolve) on the study geometry (internal flow, 3-D).
- flow_elmer_shape Elmer on the built-in 2-D `channel`.

Solvers (`solver`): EULER, NAVIER_STOKES (laminar), RANS (Spalart-Allmaras), INC_EULER,
INC_NAVIER_STOKES, INC_RANS. Elmer: incompressible laminar Navier-Stokes, optional heat.

Units: geometry and every length param in mm (`scale`, default 0.001, converts to m before
solving); SU2 and Elmer solve in SI: m, s, kg, Pa (pressures are static, gauge for the
incompressible solvers: outlet = `outlet_pressure_Pa`, default 0), K for SU2 temperatures, degC for
Elmer temperatures, velocity m/s, density kg/m^3, dynamic viscosity Pa s, conductivity W/(m K),
heat capacity J/(kg K), volumetric heat source W/m^3, heat flux W/m^2, mass flow kg/s (per metre
of span in 2-D), angles deg. Coefficients CL, CD, CM are SU2's, on `ref_area_mm2` (a length in
2-D: span 1) and `ref_length_mm`.

Faces of a STEP geometry: "xmin".."zmax" (every planar face on that side of the bounding box) or
"FaceN" (FreeCAD numbering; the server sends each FaceN's centre and area). Built-in shapes name
their boundaries (markers): channel {inlet, outlet, wall_bottom, wall_top}; ramp {inlet, top,
outlet, wall, ramp}; airfoil {airfoil, farfield}.

Convergence: a SU2 run is converged when EVERY rms residual dropped by `residual_orders`
(default 4) from its peak; an Elmer run when the nonlinear relative change fell below
`nonlinear_tol` (default 1e-7). A run that did not converge, or whose inlet and outlet mass flows
differ by more than 5 %, is UNKNOWN whatever the limits say.

Exactness: REFERENCES below (plane Poiseuille flow, supersonic wedge, Poiseuille flow with a heat
source), closed-form answers.
"""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from _common import geometry_path, result, run_exe, tool

ENV = "sim"
KINDS = {
    "cfd_su2": {"geometry": True, "timeout": 3600,
                "doc": "SU2 CFD on the solid (internal duct flow or external body in a stream): "
                       "Euler/laminar/RANS-SA, compressible or incompressible; CL CD CM, pressures, mass flow"},
    "cfd_su2_shape": {"geometry": False, "timeout": 3600,
                      "doc": "SU2 CFD on a built-in 2-D shape: channel, ramp (wedge), airfoil (NACA or coordinates)"},
    "flow_elmer": {"geometry": True, "timeout": 3600,
                   "doc": "Elmer incompressible laminar flow (+ heat) inside the solid: velocity, pressure drop, temperature"},
    "flow_elmer_shape": {"geometry": False, "timeout": 1800,
                         "doc": "Elmer incompressible laminar flow (+ heat) in a built-in 2-D channel"},
}
MAX_CELLS = 1_500_000
MAX_ELMER_NODES = 300_000
MAX_IMBALANCE = 0.05          # inlet/outlet mass flow mismatch above this -> UNKNOWN
SOLVERS = ("EULER", "NAVIER_STOKES", "RANS", "INC_EULER", "INC_NAVIER_STOKES", "INC_RANS")
ROLES = ("wall", "slip", "inlet", "outlet", "farfield", "symmetry")
FLUIDS = {   # 20 degC, 1 atm
    "air": dict(rho=1.204, mu=1.825e-5, k=0.02514, cp=1006.0),
    "water": dict(rho=998.2, mu=1.002e-3, k=0.598, cp=4182.0),
}
_SIDES = {"xmin": (0, False), "xmax": (0, True), "ymin": (1, False), "ymax": (1, True),
          "zmin": (2, False), "zmax": (2, True)}
# Gmsh element type -> (VTK/SU2 type, nodes)
_GMSH2VTK = {1: (3, 2), 2: (5, 3), 3: (9, 4), 4: (10, 4), 5: (12, 8), 6: (13, 6), 7: (14, 5)}


def versions() -> Dict[str, str]:
    import gmsh
    out: Dict[str, str] = {"gmsh": gmsh.__version__}
    try:
        p = run_exe([tool("su2"), "-h"], Path.cwd(), 60)
        m = re.search(r"SU2 v([\d.]+)", p.stdout + p.stderr)
        out["su2"] = m.group(1) if m else "?"
    except (OSError, FileNotFoundError) as e:
        out["su2"] = f"missing ({e})"
    try:
        p = run_exe([tool("elmer")], Path.cwd(), 60)
        m = re.search(r"Version:\s*([\d.]+)", p.stdout + p.stderr)
        out["elmer"] = m.group(1) if m else "?"
    except (OSError, FileNotFoundError) as e:
        out["elmer"] = f"missing ({e})"
    return out


# ------------------------------------------------------------------ fluid
def _fluid(inp: Dict[str, Any]) -> Dict[str, float]:
    spec = inp.get("fluid", "air")
    if isinstance(spec, str):
        if spec.lower() not in FLUIDS:
            raise ValueError(f"fluid {spec!r}: one of {sorted(FLUIDS)} or a dict")
        return dict(FLUIDS[spec.lower()])
    if isinstance(spec, dict):
        base = dict(FLUIDS.get(str(spec.get("base", "air")).lower(), FLUIDS["air"]))
        alias = {"density_kg_m3": "rho", "viscosity_Pa_s": "mu", "conductivity_W_mK": "k",
                 "cp_J_kgK": "cp"}
        for k, v in spec.items():
            if k != "base":
                base[alias.get(k, k)] = float(v)
        return base
    raise ValueError("fluid: 'air', 'water' or {density_kg_m3, viscosity_Pa_s, conductivity_W_mK, cp_J_kgK}")


# ------------------------------------------------------------------ geometry (Gmsh)
def _gmsh_init():
    import gmsh
    gmsh.initialize(["-nopopup"])
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.option.setNumber("General.NumThreads", 8)
    gmsh.model.add("cfd")
    return gmsh


def _transfinite_block(gmsh, corners, nx: int, ny: int, grading_y: float = 1.0):
    """A 4-sided structured block from 4 occ points (counter-clockwise, bottom edge first).
    Returns (surface, [bottom, right, top, left] curve tags)."""
    occ = gmsh.model.occ
    c = [occ.addLine(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    loop = occ.addCurveLoop(c)
    s = occ.addPlaneSurface([loop])
    return s, c, (nx, ny, grading_y)


def _shape_channel(gmsh, sh: Dict[str, Any]) -> Dict[str, Any]:
    L, h = float(sh.get("length_mm", 1000)), float(sh.get("height_mm", 100))
    ny = int(sh.get("cells_y", 40))
    nx = int(sh.get("cells_x", max(10, round(ny * L / h / 2))))
    occ = gmsh.model.occ
    p = [occ.addPoint(0, 0, 0), occ.addPoint(L, 0, 0), occ.addPoint(L, h, 0), occ.addPoint(0, h, 0)]
    s, c, _ = _transfinite_block(gmsh, p, nx, ny)
    occ.synchronize()
    m = gmsh.model.mesh
    for t, n in ((c[0], nx), (c[2], nx), (c[1], ny), (c[3], ny)):
        m.setTransfiniteCurve(t, n + 1)
    m.setTransfiniteSurface(s, cornerTags=p)
    m.setRecombine(2, s)
    return {"dim": 2, "surfaces": [s],
            "markers": {"inlet": [c[3]], "outlet": [c[1]], "wall_bottom": [c[0]], "wall_top": [c[2]]},
            "roles": {"inlet": "inlet", "outlet": "outlet", "wall_bottom": "wall", "wall_top": "wall"}}


def _shape_ramp(gmsh, sh: Dict[str, Any]) -> Dict[str, Any]:
    """Flat plate then a compression ramp at angle_deg: an attached oblique shock from the corner."""
    a = float(sh.get("upstream_mm", 500))
    Lr = float(sh.get("ramp_length_mm", 1000))
    H = float(sh.get("height_mm", 1200))
    th = math.radians(float(sh.get("angle_deg", 10)))
    ny = int(sh.get("cells_y", 80))
    nxa = int(sh.get("cells_upstream", max(8, round(ny * a / H))))
    nxr = int(sh.get("cells_ramp", max(16, round(1.5 * ny * Lr / H))))
    ye = Lr * math.tan(th)
    if ye >= 0.8 * H:
        raise ValueError("ramp: height_mm too small for this angle and ramp length")
    occ = gmsh.model.occ
    p0, p1 = occ.addPoint(-a, 0, 0), occ.addPoint(0, 0, 0)
    p2, p3 = occ.addPoint(Lr, ye, 0), occ.addPoint(Lr, H, 0)
    p4, p5 = occ.addPoint(0, H, 0), occ.addPoint(-a, H, 0)
    lw, lr = occ.addLine(p0, p1), occ.addLine(p1, p2)
    lo, lt2 = occ.addLine(p2, p3), occ.addLine(p3, p4)
    lt1, li = occ.addLine(p4, p5), occ.addLine(p5, p0)
    lm = occ.addLine(p1, p4)
    sa = occ.addPlaneSurface([occ.addCurveLoop([lw, lm, lt1, li])])
    sb = occ.addPlaneSurface([occ.addCurveLoop([lr, lo, lt2, -lm])])
    occ.synchronize()
    m = gmsh.model.mesh
    for t, n in ((lw, nxa), (lt1, nxa), (lr, nxr), (lt2, nxr), (li, ny), (lm, ny), (lo, ny)):
        m.setTransfiniteCurve(t, n + 1)
    m.setTransfiniteSurface(sa, cornerTags=[p0, p1, p4, p5])
    m.setTransfiniteSurface(sb, cornerTags=[p1, p2, p3, p4])
    m.setRecombine(2, sa)
    m.setRecombine(2, sb)
    return {"dim": 2, "surfaces": [sa, sb],
            "markers": {"inlet": [li], "top": [lt1, lt2], "outlet": [lo], "wall": [lw], "ramp": [lr]},
            "roles": {"inlet": "farfield", "top": "farfield", "outlet": "farfield",
                      "wall": "wall", "ramp": "wall"}}


def _naca4(code: str, n: int = 120) -> np.ndarray:
    """NACA 4-digit, closed trailing edge (a4 = -0.1036), cosine spacing; TE -> upper -> LE -> lower -> TE."""
    m, p, t = int(code[0]) / 100, int(code[1]) / 10, int(code[2:4]) / 100
    beta = np.linspace(0, math.pi, n)
    x = 0.5 * (1 - np.cos(beta))
    yt = 5 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3 - 0.1036 * x ** 4)
    if m > 0 and p > 0:
        yc = np.where(x < p, m / p ** 2 * (2 * p * x - x ** 2), m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x ** 2))
        dy = np.where(x < p, 2 * m / p ** 2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
    else:
        yc, dy = np.zeros_like(x), np.zeros_like(x)
    th = np.arctan(dy)
    xu, yu = x - yt * np.sin(th), yc + yt * np.cos(th)
    xl, yl = x + yt * np.sin(th), yc - yt * np.cos(th)
    upper = np.column_stack([xu, yu])[::-1]           # TE -> LE
    lower = np.column_stack([xl, yl])[1:]             # LE -> TE
    return np.vstack([upper, lower])


def _shape_airfoil(gmsh, sh: Dict[str, Any], viscous: bool) -> Dict[str, Any]:
    chord = float(sh.get("chord_mm", 1000))
    if sh.get("coordinates"):
        xy = np.asarray(sh["coordinates"], dtype=float)
        span = xy[:, 0].max() - xy[:, 0].min()
        xy = (xy - [xy[:, 0].min(), 0]) / span          # normalised to unit chord
    else:
        code = str(sh.get("naca", "0012")).lower().replace("naca", "")
        if not re.fullmatch(r"\d{4}", code):
            raise ValueError("airfoil: naca '0012' style (4 digits) or coordinates [[x, y], ...]")
        xy = _naca4(code, int(sh.get("points_per_side", 120)))
    if np.allclose(xy[0], xy[-1]):
        xy = xy[:-1]
    xy = xy * chord
    R = float(sh.get("farfield_chords", 30)) * chord
    h0 = float(sh.get("surface_cell_mm", chord / 200))
    occ = gmsh.model.occ
    ile = int(np.argmin(xy[:, 0]))
    pts = [occ.addPoint(x, y, 0) for x, y in xy]
    up = occ.addSpline(pts[:ile + 1])
    lo = occ.addSpline(pts[ile:] + [pts[0]])
    far = occ.addCircle(chord / 4, 0, 0, R)
    s = occ.addPlaneSurface([occ.addCurveLoop([far]), occ.addCurveLoop([up, lo])])
    occ.synchronize()
    f = gmsh.model.mesh.field
    d = f.add("Distance")
    f.setNumbers(d, "CurvesList", [up, lo])
    f.setNumber(d, "Sampling", 400)
    th = f.add("Threshold")
    f.setNumber(th, "InField", d)
    f.setNumber(th, "SizeMin", h0)
    f.setNumber(th, "SizeMax", R / 12)
    f.setNumber(th, "DistMin", 0.02 * chord)
    f.setNumber(th, "DistMax", R / 2)
    f.setAsBackgroundMesh(th)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    if viscous:
        bl = f.add("BoundaryLayer")
        f.setNumbers(bl, "CurvesList", [up, lo])
        f.setNumber(bl, "Size", float(sh.get("first_cell_mm", chord * 2e-5)))
        f.setNumber(bl, "Ratio", float(sh.get("bl_ratio", 1.15)))
        f.setNumber(bl, "Thickness", float(sh.get("bl_thickness_mm", chord * 0.04)))
        f.setNumber(bl, "Quads", 1)
        f.setNumbers(bl, "FanPointsList", [pts[0]])
        f.setAsBoundaryLayer(bl)
    return {"dim": 2, "surfaces": [s], "markers": {"airfoil": [up, lo], "farfield": [far]},
            "roles": {"airfoil": "wall", "farfield": "farfield"},
            "ref": {"length_mm": chord, "area_mm2": chord, "origin_mm": [chord / 4, 0, 0]}}


def _select_surfaces(gmsh, spec: str, bbox, face_info) -> List[int]:
    diag = math.dist(bbox[:3], bbox[3:])
    tol = 1e-4 * diag + 1e-6
    s = str(spec).strip()
    surfs = [t for _, t in gmsh.model.getEntities(2)]
    if s.lower() in _SIDES:
        axis, is_max = _SIDES[s.lower()]
        plane = bbox[3 + axis] if is_max else bbox[axis]
        hit = []
        for t in surfs:
            b = gmsh.model.getBoundingBox(2, t)
            if abs(b[axis] - plane) < tol and abs(b[3 + axis] - plane) < tol:
                hit.append(t)
    elif re.fullmatch(r"Face\d+", s):
        info = face_info.get(s)
        if not info:
            raise ValueError(f"{s}: no centre/area sent for it (the server resolves FaceN)")
        hit = [t for t in surfs
               if math.dist(gmsh.model.occ.getCenterOfMass(2, t), info["com"]) < 1e-3 * diag
               and abs(gmsh.model.occ.getMass(2, t) - info["area"]) < 1e-3 * max(info["area"], 1e-9)]
    else:
        raise ValueError(f"face {spec!r}: FaceN or one of {sorted(_SIDES)}")
    if not hit:
        raise ValueError(f"face {spec!r} matches no surface of the solid")
    return hit


def _step_domain(gmsh, geom: Path, inp: Dict[str, Any]) -> Dict[str, Any]:
    occ = gmsh.model.occ
    occ.importShapes(str(geom))
    occ.synchronize()
    vols = gmsh.model.getEntities(3)
    if not vols:
        raise ValueError("the geometry holds no solid")
    if len(vols) > 1:
        occ.fuse(vols[:1], vols[1:])
        occ.synchronize()
    bbox = gmsh.model.getBoundingBox(-1, -1)
    ext = [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]]
    domain = str(inp.get("domain", "internal")).lower()
    face_info = inp.get("face_info") or {}
    if domain == "external":
        Lb = max(ext)
        up, down, side = (float(inp.get(k, d)) for k, d in
                          (("farfield_upstream", 5), ("farfield_downstream", 10), ("farfield_side", 5)))
        box = occ.addBox(bbox[0] - up * Lb, bbox[1] - side * Lb, bbox[2] - side * Lb,
                         ext[0] + (up + down) * Lb, ext[1] + 2 * side * Lb, ext[2] + 2 * side * Lb)
        body = gmsh.model.getEntities(3)
        body = [e for e in body if e[1] != box]
        out, _ = occ.cut([(3, box)], body)
        occ.synchronize()
        bb = gmsh.model.getBoundingBox(-1, -1)
        diag = math.dist(bb[:3], bb[3:])
        far, wall = [], []
        for _, t in gmsh.model.getEntities(2):
            b = gmsh.model.getBoundingBox(2, t)
            on_box = any(abs(b[a] - bb[a]) < 1e-6 * diag and abs(b[a + 3] - bb[a]) < 1e-6 * diag
                         or abs(b[a] - bb[a + 3]) < 1e-6 * diag and abs(b[a + 3] - bb[a + 3]) < 1e-6 * diag
                         for a in range(3))
            (far if on_box else wall).append(t)
        size_body = float(inp.get("mesh_size", 0) or 0) or Lb / 25
        _size_field(gmsh, wall, size_body, Lb * max(up, side) / 3, Lb)
        return {"dim": 3, "markers": {"body": wall, "farfield": far},
                "roles": {"body": "wall", "farfield": "farfield"},
                "ref": {"length_mm": Lb, "area_mm2": ext[1] * ext[2], "origin_mm": list(bbox[:3])}}
    if domain != "internal":
        raise ValueError("domain: internal (the solid is the fluid) or external (flow around it)")
    used: Dict[int, str] = {}
    markers: Dict[str, List[int]] = {}
    for role in ("inlet", "outlet", "symmetry", "farfield", "slip"):
        specs = inp.get(role) or []
        if isinstance(specs, str):
            specs = [specs]
        for spec in specs:
            for t in _select_surfaces(gmsh, spec, bbox, face_info):
                if t in used:
                    raise ValueError(f"face {spec!r} is both {used[t]} and {role}")
                used[t] = role
                markers.setdefault(role, []).append(t)
    if "inlet" not in markers or "outlet" not in markers:
        raise ValueError("internal flow: give inlet=[faces] and outlet=[faces]")
    markers["wall"] = [t for _, t in gmsh.model.getEntities(2) if t not in used]
    size = float(inp.get("mesh_size", 0) or 0)
    if size <= 0:
        size = max(min(e for e in ext if e > 0) / 8, max(ext) / 60)
    gmsh.option.setNumber("Mesh.MeshSizeMax", size)
    gmsh.option.setNumber("Mesh.MeshSizeMin", size / 5)
    return {"dim": 3, "markers": markers, "roles": {k: ("wall" if k == "wall" else k) for k in markers},
            "mesh_size_mm": size}


def _size_field(gmsh, surfaces, h0, hmax, Lb):
    f = gmsh.model.mesh.field
    d = f.add("Distance")
    f.setNumbers(d, "SurfacesList", surfaces)
    th = f.add("Threshold")
    f.setNumber(th, "InField", d)
    f.setNumber(th, "SizeMin", h0)
    f.setNumber(th, "SizeMax", hmax)
    f.setNumber(th, "DistMin", 0.05 * Lb)
    f.setNumber(th, "DistMax", 4 * Lb)
    f.setAsBackgroundMesh(th)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)


def _build(kind: str, inp: Dict[str, Any], work: Path, viscous: bool):
    """Build and mesh the domain; returns (gmsh-free mesh dict, info). Gmsh stays initialised
    until the caller has written what it needs (finalised by the caller)."""
    gmsh = _gmsh_init()
    if kind.endswith("_shape"):
        sh = dict(inp.get("shape") or {})
        typ = str(sh.get("type", "")).lower()
        if typ == "channel":
            info = _shape_channel(gmsh, sh)
        elif typ in ("ramp", "wedge"):
            info = _shape_ramp(gmsh, sh)
        elif typ == "airfoil":
            if kind.startswith("flow_elmer"):
                raise ValueError("flow_elmer_shape: channel only")
            info = _shape_airfoil(gmsh, sh, viscous)
        else:
            raise ValueError("shape.type: channel, ramp (wedge) or airfoil")
    else:
        info = _step_domain(gmsh, geometry_path(inp, work), inp)
    dim = info["dim"]
    m = gmsh.model
    for i, (name, tags) in enumerate(info["markers"].items()):
        m.addPhysicalGroup(dim - 1, tags, tag=i + 1, name=name)
    vols = [t for _, t in m.getEntities(dim)]
    m.addPhysicalGroup(dim, vols, tag=len(info["markers"]) + 1, name="fluid")
    if kind.startswith("flow_elmer"):
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
    m.mesh.generate(dim)
    return gmsh, info


def _extract(gmsh, info: Dict[str, Any], scale: float) -> Dict[str, Any]:
    dim = info["dim"]
    tags, coords, _ = gmsh.model.mesh.getNodes()
    order = np.argsort(tags)
    tags, xyz = tags[order], coords.reshape(-1, 3)[order] * scale
    lut = np.zeros(int(tags.max()) + 1, dtype=np.int64)
    lut[tags.astype(np.int64)] = np.arange(len(tags))

    def elems(d: int, ents: List[int]):
        out: Dict[int, List[np.ndarray]] = {}
        for e in ents:
            ets, _, ens = gmsh.model.mesh.getElements(d, e)
            for t, n in zip(ets, ens):
                if int(t) not in _GMSH2VTK:
                    raise RuntimeError(f"unsupported Gmsh element type {t}")
                vt, nn = _GMSH2VTK[int(t)]
                out.setdefault(vt, []).append(lut[n.astype(np.int64)].reshape(-1, nn))
        return [(vt, np.vstack(a)) for vt, a in out.items()]

    cells = elems(dim, [t for _, t in gmsh.model.getEntities(dim)])
    markers = {name: elems(dim - 1, tg) for name, tg in info["markers"].items()}
    used = np.unique(np.concatenate([c.ravel() for _, c in cells]))
    if len(used) != len(tags):                       # drop orphan nodes (geometry points...)
        remap = -np.ones(len(tags), dtype=np.int64)
        remap[used] = np.arange(len(used))
        xyz = xyz[used]
        cells = [(t, remap[c]) for t, c in cells]
        markers = {k: [(t, remap[c]) for t, c in v] for k, v in markers.items()}
    ncell = sum(len(c) for _, c in cells)
    if ncell > MAX_CELLS:
        raise ValueError(f"{ncell} cells > {MAX_CELLS}: coarser mesh_size / fewer cells")
    if any(not v for v in markers.values()):
        empty = [k for k, v in markers.items() if not v]
        raise RuntimeError(f"no boundary elements on {empty}")
    return {"dim": dim, "xyz": xyz, "cells": cells, "markers": markers, "ncell": ncell}


def _facets(mesh, name: str) -> Tuple[np.ndarray, np.ndarray, List[np.ndarray]]:
    """(area or length in m^2|m, unit normal, node lists) of each boundary facet of a marker.
    The normal points out of the fluid."""
    xyz = mesh["xyz"]
    areas, normals, nodes = [], [], []
    for vt, conn in mesh["markers"][name]:
        p = xyz[conn]
        if vt == 3:
            d = p[:, 1] - p[:, 0]
            n = np.column_stack([d[:, 1], -d[:, 0], np.zeros(len(d))])
            a = np.linalg.norm(d, axis=1)
        else:
            if vt == 5:
                n = 0.5 * np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
            else:
                n = 0.5 * np.cross(p[:, 2] - p[:, 0], p[:, 3] - p[:, 1])
            a = np.linalg.norm(n, axis=1)
        nn = n / np.maximum(np.linalg.norm(n, axis=1), 1e-300)[:, None]
        areas.append(a)
        normals.append(nn)
        nodes += list(conn)
    a, nn = np.concatenate(areas), np.concatenate(normals)
    # orient outward: the adjacent cell centroid lies on the inner side
    inner = _inner_points(mesh, nodes)
    fc = np.array([xyz[c].mean(axis=0) for c in nodes])
    flip = np.einsum("ij,ij->i", inner - fc, nn) > 0
    nn[flip] *= -1
    return a, nn, nodes


def _inner_points(mesh, facets: List[np.ndarray]) -> np.ndarray:
    """Centroid of the cell owning each boundary facet."""
    key_cell: Dict[frozenset, int] = {}
    cents = []
    want = {frozenset(int(x) for x in f) for f in facets}
    k = 0
    for vt, conn in mesh["cells"]:
        faces = _cell_faces(vt)
        for c in conn:
            for f in faces:
                key = frozenset(int(c[i]) for i in f)
                if key in want and key not in key_cell:
                    key_cell[key] = k
                    cents.append(mesh["xyz"][c].mean(axis=0))
                    k += 1
    return np.array([cents[key_cell[frozenset(int(x) for x in f)]] for f in facets])


def _cell_faces(vt: int):
    return {5: ((0, 1), (1, 2), (2, 0)), 9: ((0, 1), (1, 2), (2, 3), (3, 0)),
            10: ((0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3)),
            12: ((0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)),
            13: ((0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)),
            14: ((0, 1, 2, 3), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4))}[vt]


def _write_su2(mesh, path: Path) -> None:
    dim = mesh["dim"]
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(f"NDIME= {dim}\nNELEM= {mesh['ncell']}\n")
        k = 0
        for vt, conn in mesh["cells"]:
            for c in conn:
                f.write(f"{vt} " + " ".join(map(str, c)) + f" {k}\n")
                k += 1
        xyz = mesh["xyz"]
        f.write(f"NPOIN= {len(xyz)}\n")
        fmt = "{:.12e} {:.12e} {}\n" if dim == 2 else "{:.12e} {:.12e} {:.12e} {}\n"
        for i, p in enumerate(xyz):
            f.write(fmt.format(*p[:dim], i))
        f.write(f"NMARK= {len(mesh['markers'])}\n")
        for name, blocks in mesh["markers"].items():
            n = sum(len(c) for _, c in blocks)
            f.write(f"MARKER_TAG= {name}\nMARKER_ELEMS= {n}\n")
            for vt, conn in blocks:
                for c in conn:
                    f.write(f"{vt} " + " ".join(map(str, c)) + "\n")


# ------------------------------------------------------------------ SU2
def _su2_config(inp, mesh, roles, fluid, ref, solver) -> Tuple[str, Dict[str, Any]]:
    dim = mesh["dim"]
    inc = solver.startswith("INC_")
    viscous = solver not in ("EULER", "INC_EULER")
    scale = float(inp.get("scale", 0.001))
    aoa = float(inp.get("aoa_deg", 0.0))
    beta = float(inp.get("sideslip_deg", 0.0))
    cfg: Dict[str, Any] = {
        "SOLVER": {"RANS": "RANS", "INC_RANS": "INC_RANS"}.get(solver, solver),
        "MATH_PROBLEM": "DIRECT", "RESTART_SOL": "NO",
        "MESH_FILENAME": "mesh.su2", "MESH_FORMAT": "SU2",
        "REF_LENGTH": ref["length_mm"] * scale,
        "REF_AREA": ref["area_mm2"] * (scale if dim == 2 else scale ** 2),
        "REF_ORIGIN_MOMENT_X": ref["origin_mm"][0] * scale,
        "REF_ORIGIN_MOMENT_Y": ref["origin_mm"][1] * scale,
        "REF_ORIGIN_MOMENT_Z": ref["origin_mm"][2] * scale,
        "AOA": aoa, "SIDESLIP_ANGLE": beta,
    }
    if solver in ("RANS", "INC_RANS"):
        cfg["KIND_TURB_MODEL"] = "SA"
    info: Dict[str, Any] = {}
    names = {r: [k for k, v in roles.items() if v == r] for r in ROLES}
    walls = names["wall"] if viscous else names["wall"] + names["slip"]
    slip = names["slip"] if viscous else names["wall"] + names["slip"]
    # stream direction (external) and inlet directions (internal)
    a, b = math.radians(aoa), math.radians(beta)
    stream = ((math.cos(a), math.sin(a), 0.0) if dim == 2
              else (math.cos(a) * math.cos(b), math.sin(b), math.sin(a) * math.cos(b)))
    inlets = []
    for name in names["inlet"]:
        ar, nn, _ = _facets(mesh, name)
        n_in = -(nn * ar[:, None]).sum(axis=0)
        n_in /= np.linalg.norm(n_in)
        inlets.append((name, n_in))
    if inc:
        rho, mu = fluid["rho"], fluid["mu"]
        V = float(inp.get("velocity_m_s", 0) or 0)
        if V <= 0:
            raise ValueError("incompressible solver: velocity_m_s (freestream or inlet speed) > 0")
        vinit = np.asarray(inlets[0][1] if inlets and not names["farfield"] else stream) * V
        T = float(inp.get("temperature_K", 293.15))
        cfg.update({"INC_NONDIM": "DIMENSIONAL", "FLUID_MODEL": "CONSTANT_DENSITY",
                    "INC_DENSITY_MODEL": "CONSTANT", "INC_DENSITY_INIT": rho,
                    "INC_VELOCITY_INIT": "( " + ", ".join(f"{v:.9g}" for v in vinit) + " )",
                    "INC_TEMPERATURE_INIT": T, "INC_ENERGY_EQUATION": "NO",
                    "VISCOSITY_MODEL": "CONSTANT_VISCOSITY", "MU_CONSTANT": mu,
                    "CONV_NUM_METHOD_FLOW": "FDS", "MUSCL_FLOW": "YES", "SLOPE_LIMITER_FLOW": "NONE"})
        if inlets:
            cfg["INC_INLET_TYPE"] = " ".join("VELOCITY_INLET" for _ in inlets)
            cfg["MARKER_INLET"] = "( " + ", ".join(
                f"{n}, {T}, {V:.9g}, {d[0]:.9g}, {d[1]:.9g}, {d[2]:.9g}" for n, d in inlets) + " )"
        if names["outlet"]:
            cfg["INC_OUTLET_TYPE"] = " ".join("PRESSURE_OUTLET" for _ in names["outlet"])
        info.update(rho=rho, mu=mu, V=V, q=0.5 * rho * V * V, p_ref=float(inp.get("outlet_pressure_Pa", 0.0)))
        if inp.get("reynolds"):
            raise ValueError("incompressible solver: give velocity_m_s and fluid, not reynolds")
    else:
        M = float(inp.get("mach", 0) or 0)
        p_inf = float(inp.get("p_inf_Pa", 101325.0))
        T_inf = float(inp.get("T_inf_K", 288.15))
        gamma, Rg = float(inp.get("gamma", 1.4)), float(inp.get("R_J_kgK", 287.058))
        if M <= 0 and not inlets:
            raise ValueError("compressible solver: mach > 0 (freestream), or an inlet")
        M = M if M > 0 else 0.1
        cfg.update({"MACH_NUMBER": M, "FREESTREAM_PRESSURE": p_inf, "FREESTREAM_TEMPERATURE": T_inf,
                    "GAMMA_VALUE": gamma, "GAS_CONSTANT": Rg, "FLUID_MODEL": "STANDARD_AIR"
                    if gamma == 1.4 and abs(Rg - 287.058) < 1e-3 else "IDEAL_GAS",
                    "REF_DIMENSIONALIZATION": "DIMENSIONAL",
                    "ENTROPY_FIX_COEFF": 0.0})
        scheme = str(inp.get("scheme", "ROE")).upper()
        if scheme == "JST":                   # central + artificial dissipation: smooth subsonic flow
            cfg.update({"CONV_NUM_METHOD_FLOW": "JST", "JST_SENSOR_COEFF": "( 0.5, 0.02 )"})
        elif scheme == "ROE":                 # upwind + limiter: shocks
            cfg.update({"CONV_NUM_METHOD_FLOW": "ROE", "MUSCL_FLOW": "YES",
                        "SLOPE_LIMITER_FLOW": "VENKATAKRISHNAN",
                        "VENKAT_LIMITER_COEFF": float(inp.get("venkat_coeff", 0.05))})
        else:
            raise ValueError("scheme: ROE (shocks, default) or JST (smooth subsonic)")
        if viscous:
            Re = float(inp.get("reynolds", 0) or 0)
            if Re <= 0:
                raise ValueError("viscous compressible solver: reynolds (on ref_length) > 0")
            cfg.update({"REYNOLDS_NUMBER": Re, "REYNOLDS_LENGTH": cfg["REF_LENGTH"],
                        "INIT_OPTION": "REYNOLDS", "VISCOSITY_MODEL": "SUTHERLAND",
                        "FREESTREAM_OPTION": "TEMPERATURE_FS"})
        else:
            cfg["INIT_OPTION"] = "TD_CONDITIONS"
        a_inf = math.sqrt(gamma * Rg * T_inf)
        rho_inf = p_inf / (Rg * T_inf)
        info.update(p_inf=p_inf, rho=rho_inf, V=M * a_inf, q=0.5 * gamma * p_inf * M * M, p_ref=p_inf)
        if inlets:
            Pt = float(inp.get("inlet_total_pressure_Pa", 0) or 0)
            Tt = float(inp.get("inlet_total_temperature_K", 0) or 0)
            if Pt <= 0 or Tt <= 0:
                raise ValueError("compressible inlet: inlet_total_pressure_Pa and inlet_total_temperature_K")
            cfg["INLET_TYPE"] = "TOTAL_CONDITIONS"
            cfg["MARKER_INLET"] = "( " + ", ".join(
                f"{n}, {Tt}, {Pt}, {d[0]:.9g}, {d[1]:.9g}, {d[2]:.9g}" for n, d in inlets) + " )"
    if names["outlet"]:
        po = float(inp.get("outlet_pressure_Pa", 0.0 if inc else info["p_inf"]))
        cfg["MARKER_OUTLET"] = "( " + ", ".join(f"{n}, {po}" for n in names["outlet"]) + " )"
    if walls:
        if viscous:
            if inp.get("wall_temperature_K") and not inc:
                cfg["MARKER_ISOTHERMAL"] = "( " + ", ".join(
                    f"{n}, {float(inp['wall_temperature_K'])}" for n in walls) + " )"
            else:
                cfg["MARKER_HEATFLUX"] = "( " + ", ".join(f"{n}, 0.0" for n in walls) + " )"
        else:
            cfg["MARKER_EULER"] = "( " + ", ".join(walls) + " )"
    if viscous and slip:
        cfg["MARKER_EULER"] = "( " + ", ".join(slip) + " )"
    if names["farfield"]:
        cfg["MARKER_FAR"] = "( " + ", ".join(names["farfield"]) + " )"
    if names["symmetry"]:
        cfg["MARKER_SYM"] = "( " + ", ".join(names["symmetry"]) + " )"
    surf = walls + slip if (walls or slip) else list(roles)[:1]
    surf = list(dict.fromkeys(surf))
    cfg["MARKER_PLOTTING"] = "( " + ", ".join(surf) + " )"
    cfg["MARKER_MONITORING"] = "( " + ", ".join(surf) + " )"
    if solver in ("RANS", "INC_RANS"):
        cfg.update({"CONV_NUM_METHOD_TURB": "SCALAR_UPWIND", "MUSCL_TURB": "NO",
                    "TIME_DISCRE_TURB": "EULER_IMPLICIT"})
    orders = float(inp.get("residual_orders", 4))
    cfl = float(inp.get("cfl", 100.0 if inc else 5.0))
    cfl_max = float(inp.get("cfl_max", cfl * (100 if inc else 20)))
    cfg.update({
        "NUM_METHOD_GRAD": "WEIGHTED_LEAST_SQUARES" if viscous else "GREEN_GAUSS",
        "TIME_DISCRE_FLOW": "EULER_IMPLICIT", "CFL_NUMBER": cfl, "CFL_ADAPT": "YES",
        "CFL_ADAPT_PARAM": f"( 0.5, 1.5, {cfl / 10:.6g}, {cfl_max:.6g} )",
        "LINEAR_SOLVER": "FGMRES", "LINEAR_SOLVER_PREC": "ILU",
        "LINEAR_SOLVER_ERROR": float(inp.get("linear_error", 1e-6 if inc else 1e-4)),
        "LINEAR_SOLVER_ITER": int(inp.get("linear_iter", 50 if inc else 20)), "MGLEVEL": 0,
        "ITER": int(inp.get("max_iter", 4000)),
        # every equation must fall by the requested orders (the verdict re-checks it from history)
        "CONV_FIELD": "( " + ", ".join(
            ["REL_RMS_PRESSURE"] + [f"REL_RMS_VELOCITY-{c}" for c in "XYZ"[:dim]] if inc else
            ["REL_RMS_DENSITY"] + [f"REL_RMS_MOMENTUM-{c}" for c in "XYZ"[:dim]] + ["REL_RMS_ENERGY"])
            + (", REL_RMS_NU_TILDE" if solver in ("RANS", "INC_RANS") else "") + " )",
        # SU2 measures from an early iteration, the verdict from each residual's peak (stricter):
        # half an order of margin so SU2 does not stop just short of the verdict's test
        "CONV_RESIDUAL_MINVAL": -(orders + 0.5), "CONV_STARTITER": 10,
        "OUTPUT_FILES": "( RESTART_ASCII, PARAVIEW, SURFACE_CSV )",
        "VOLUME_OUTPUT": "( COORDINATES, SOLUTION, PRIMITIVE )",
        "HISTORY_OUTPUT": "( ITER, RMS_RES, AERO_COEFF )",
        "SCREEN_OUTPUT": "( INNER_ITER, RMS_RES, LIFT, DRAG )", "SCREEN_WRT_FREQ_INNER": 100,
        "WRT_RESTART_COMPACT": "NO",
        "CONV_FILENAME": "history", "RESTART_FILENAME": "restart_flow",
        "VOLUME_FILENAME": "flow", "SURFACE_FILENAME": "surface_flow", "TABULAR_FORMAT": "CSV",
        "OUTPUT_WRT_FREQ": 100000,
    })
    info.update(orders=orders, inc=inc, viscous=viscous)
    text = "% mcpfc cfd_su2\n" + "".join(f"{k}= {v}\n" for k, v in cfg.items())
    return text, info


def _read_csv(path: Path) -> Tuple[List[str], np.ndarray]:
    with open(path, encoding="utf-8", errors="replace") as f:
        rows = list(csv.reader(f))
    head = [h.strip().strip('"').strip() for h in rows[0]]
    data = []
    for r in rows[1:]:
        try:
            data.append([float(x) for x in r])
        except ValueError:
            continue
    return head, np.array(data, dtype=float).reshape(-1, len(head))


def _residual_drop(head: List[str], hist: np.ndarray) -> Tuple[float, Dict[str, float]]:
    """Orders of magnitude each rms residual fell from its peak to its last value (log10 data)."""
    drops = {}
    for i, h in enumerate(head):
        if h.startswith("rms[") and len(hist):
            col = hist[:, i]
            if not np.all(np.isfinite(col)):
                drops[h] = float("nan")
                continue
            drops[h] = float(col.max() - col[-1])
    worst = min(drops.values()) if drops else float("nan")
    return worst, drops


def _map_nodes(mesh_xyz: np.ndarray, pts: np.ndarray, dim: int) -> np.ndarray:
    from scipy.spatial import cKDTree
    tree = cKDTree(pts[:, :dim])
    d, j = tree.query(mesh_xyz[:, :dim])
    span = float(np.ptp(mesh_xyz[:, :dim], axis=0).max())
    if d.max() > 1e-6 * span:
        raise RuntimeError("solver output nodes do not match the mesh")
    return j


def _post_fields(mesh, xyz, fields: Dict[str, np.ndarray], roles, info, inp, values, notes):
    """Values common to SU2 and Elmer from nodal p, velocity (and rho)."""
    dim = mesh["dim"]
    j = _map_nodes(mesh["xyz"], xyz, dim)
    p = fields["p"][j]
    vel = fields["u"][j]
    rho = fields["rho"][j] if "rho" in fields else np.full(len(p), info["rho"])
    speed = np.linalg.norm(vel, axis=1)
    values["u_max_m_s"] = float(speed.max())
    values["p_max_Pa"], values["p_min_Pa"] = float(p.max()), float(p.min())
    if "mach" in fields:
        values["mach_max"] = float(fields["mach"][j].max())
    if "T" in fields:
        T = fields["T"][j]
        values["T_max"], values["T_min"] = float(T.max()), float(T.min())
    walls = []
    hyd: Dict[str, Tuple[float, float]] = {}
    for name, role in roles.items():
        ar, nn, nodes = _facets(mesh, name)
        pf = np.array([p[c].mean() for c in nodes])
        pm = float((pf * ar).sum() / ar.sum())
        values[f"p_mean_{name}_Pa"] = pm
        if role in ("wall", "slip"):
            walls.append(np.unique(np.concatenate(nodes)))
            values[f"p_median_{name}_Pa"] = float(np.median(pf))
            if info.get("p_inf"):
                values[f"p_ratio_median_{name}"] = float(np.median(pf)) / info["p_inf"]
                values[f"p_ratio_max_{name}"] = float(pf.max()) / info["p_inf"]
        if role in ("inlet", "outlet", "farfield"):
            mf = np.array([(rho[c, None] * vel[c]).mean(axis=0) for c in nodes])
            mdot = float((np.einsum("ij,ij->i", mf, nn) * ar).sum())
            values[f"mdot_{name}_kg_s"] = mdot
            values[f"u_max_{name}_m_s"] = float(speed[np.unique(np.concatenate(nodes))].max())
            if role == "farfield":
                continue
            area = float(ar.sum())
            rho_m = float((np.array([rho[c].mean() for c in nodes]) * ar).sum() / area)
            u_mean = abs(mdot) / (rho_m * area)
            values[f"u_mean_{name}_m_s"] = u_mean
            if u_mean > 0:
                values[f"u_max_to_mean_{name}"] = values[f"u_max_{name}_m_s"] / u_mean
            # hydraulic diameter 4 A / P (2-D: plates, span 1 -> 2 h)
            per = 2.0 if dim == 2 else _perimeter(mesh, nodes)
            if per > 0:
                dh = 4 * area / per
                values[f"Dh_{name}_mm"] = dh / float(inp.get("scale", 0.001))
                hyd.setdefault(role, (u_mean, dh))
    if walls:
        wn = np.unique(np.concatenate(walls))
        values["p_wall_max_Pa"], values["p_wall_min_Pa"] = float(p[wn].max()), float(p[wn].min())
        if info.get("q"):
            pr = info.get("p_ref", 0.0)
            values["Cp_wall_max"] = (values["p_wall_max_Pa"] - pr) / info["q"]
            values["Cp_wall_min"] = (values["p_wall_min_Pa"] - pr) / info["q"]
    ins = [n for n, r in roles.items() if r == "inlet"]
    outs = [n for n, r in roles.items() if r == "outlet"]
    if ins and outs:
        def amean(names):
            tot, aa = 0.0, 0.0
            for n in names:
                ar, _, nodes = _facets(mesh, n)
                tot += float((np.array([p[c].mean() for c in nodes]) * ar).sum())
                aa += float(ar.sum())
            return tot / aa
        values["dp_Pa"] = amean(ins) - amean(outs)
        m_in = -sum(values[f"mdot_{n}_kg_s"] for n in ins)
        m_out = sum(values[f"mdot_{n}_kg_s"] for n in outs)
        values["mdot_kg_s"] = m_in
        values["mass_imbalance"] = abs(m_in - m_out) / max(abs(m_in), 1e-300)
        if values["mass_imbalance"] > 0.01:
            notes.append(f"mass imbalance {values['mass_imbalance']:.2%} (mesh too coarse at the inlet/outlet or "
                         f"not converged{'; > 5 % -> UNKNOWN' if values['mass_imbalance'] > MAX_IMBALANCE else ''})")
    probes = inp.get("probes") or []
    if probes:
        scale = float(inp.get("scale", 0.001))
        pp = np.zeros((len(probes), 3))
        for i, q in enumerate(probes):
            pp[i, :len(q)] = np.asarray(q, dtype=float) * scale
        extra = [fields["T"][j]] if "T" in fields else []
        got = _probe(mesh, np.column_stack([p, vel, *extra]), pp)
        for i in range(len(probes)):
            values[f"p_probe{i}_Pa"] = float(got[i, 0])
            values[f"u_probe{i}_m_s"] = float(np.linalg.norm(got[i, 1:4]))
            if extra:
                values[f"T_probe{i}"] = float(got[i, 4])
        pv_p = got[:, 0]
        if len(probes) >= 2:
            values["dp_probes_Pa"] = float(pv_p[0] - pv_p[1])
            dx = float(np.linalg.norm(pp[1] - pp[0]))
            u_dh = hyd.get("outlet") or hyd.get("inlet")
            if info.get("mu") and u_dh and u_dh[0] > 0 and dx > 0:
                # Darcy friction factor x Reynolds on the hydraulic diameter, from the measured
                # mean speed: 96 for laminar flow between plates, 64 in a round pipe
                u_m, dh = u_dh
                values["fRe_probes"] = 2 * (values["dp_probes_Pa"] / dx) * dh ** 2 / (info["mu"] * u_m)


def _perimeter(mesh, nodes: List[np.ndarray]) -> float:
    """Length of the outer edges of a set of boundary facets (edges used by one facet only)."""
    count: Dict[Tuple[int, int], int] = {}
    for f in nodes:
        k = len(f)
        for i in range(k):
            e = tuple(sorted((int(f[i]), int(f[(i + 1) % k]))))
            count[e] = count.get(e, 0) + 1
    xyz = mesh["xyz"]
    return float(sum(np.linalg.norm(xyz[a] - xyz[b]) for (a, b), n in count.items() if n == 1))


def _probe(mesh, data, pts):
    """Linear interpolation of nodal data (Delaunay of the nodes, nearest node as fallback)."""
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
    dim = mesh["dim"]
    X = mesh["xyz"][:, :dim]
    lin = LinearNDInterpolator(X, data)(pts[:, :dim])
    near = NearestNDInterpolator(X, data)(pts[:, :dim])
    bad = ~np.all(np.isfinite(lin), axis=1)
    lin[bad] = near[bad]
    return lin


def _run_su2(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    solver = str(inp.get("solver", "EULER")).upper()
    if solver not in SOLVERS:
        raise ValueError(f"solver {solver!r}: one of {SOLVERS}")
    viscous = solver not in ("EULER", "INC_EULER")
    scale = float(inp.get("scale", 0.001))
    gmsh, info = _build(kind, inp, work, viscous)
    try:
        mesh = _extract(gmsh, info, scale)
    finally:
        gmsh.finalize()
    roles = dict(info["roles"])
    for k, v in (inp.get("roles") or {}).items():
        if k not in roles or v not in ROLES:
            raise ValueError(f"roles: marker {k!r} -> one of {ROLES} (markers: {sorted(roles)})")
        roles[k] = v
    ref = dict(info.get("ref") or {"length_mm": 1000.0, "area_mm2": 1e6, "origin_mm": [0, 0, 0]})
    if inp.get("ref_length_mm"):
        ref["length_mm"] = float(inp["ref_length_mm"])
    if inp.get("ref_area_mm2"):
        ref["area_mm2"] = float(inp["ref_area_mm2"])
    if inp.get("moment_origin_mm"):
        ref["origin_mm"] = list(inp["moment_origin_mm"]) + [0] * (3 - len(inp["moment_origin_mm"]))
    _write_su2(mesh, work / "mesh.su2")
    fluid = _fluid(inp)
    text, sinfo = _su2_config(inp, mesh, roles, fluid, ref, solver)
    (work / "case.cfg").write_text(text, encoding="ascii")
    threads = str(int(inp.get("threads", 8)))
    proc = run_exe([tool("su2"), "-t", threads, "case.cfg"], work, float(inp.get("timeout_s", 3500)),
                   env_extra={"OMP_NUM_THREADS": threads})
    (work / "su2.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    hist_p = work / "history.csv"
    rest_p = work / "restart_flow.csv"
    if not hist_p.is_file() or not rest_p.is_file():
        tail = (proc.stdout + proc.stderr)[-600:]
        raise RuntimeError(f"SU2 failed (code {proc.returncode}): {tail}")
    head, hist = _read_csv(hist_p)
    worst, drops = _residual_drop(head, hist)
    values: Dict[str, Any] = {"solver": solver, "cells": mesh["ncell"], "nodes": int(len(mesh["xyz"])),
                              "iterations": int(len(hist)), "residual_drop_orders": worst}
    converged = bool(np.isfinite(worst) and worst >= sinfo["orders"] - 1e-9)
    values["converged"] = int(converged)
    last = hist[-1] if len(hist) else []
    for key, col in (("CL", "CL"), ("CD", "CD"), ("CM", "CMz" if mesh["dim"] == 2 else "CMy"),
                     ("CSF", "CSF")):
        if col in head and len(hist):
            values[key] = float(last[head.index(col)])
    rh, rd = _read_csv(rest_p)
    col = {h: i for i, h in enumerate(rh)}
    dim = mesh["dim"]
    xyz = rd[:, [col["x"], col["y"]] + ([col["z"]] if dim == 3 else [])]
    fields: Dict[str, np.ndarray] = {"p": rd[:, col["Pressure"]]}
    comps = "xyz"[:dim]
    if all(f"Velocity_{c}" in col for c in comps):
        fields["u"] = rd[:, [col[f"Velocity_{c}"] for c in comps]]
    else:
        rho = rd[:, col["Density"]]
        fields["u"] = rd[:, [col[f"Momentum_{c}"] for c in comps]] / rho[:, None]
    if "Density" in col and not sinfo["inc"]:
        fields["rho"] = rd[:, col["Density"]]
    if "Mach" in col:
        fields["mach"] = rd[:, col["Mach"]]
    if fields["u"].shape[1] == 2:
        fields["u"] = np.column_stack([fields["u"], np.zeros(len(fields["u"]))])
    notes: List[str] = []
    _post_fields(mesh, xyz, fields, roles, sinfo, inp, values, notes)
    if not converged:
        notes.insert(0, f"NOT CONVERGED: residuals fell {worst:.2f} orders < {sinfo['orders']:g} "
                        f"requested in {len(hist)} iterations (raise max_iter, lower cfl, refine) -> UNKNOWN")
    head_txt = (f"SU2 {solver} {mesh['dim']}-D, {mesh['ncell']} cells, {len(hist)} it, residuals "
                f"-{worst:.1f} orders")
    for k in ("CL", "CD", "CM"):
        if k in values:
            head_txt += f", {k} {values[k]:.4g}"
    if "dp_Pa" in values:
        head_txt += f", dp {values['dp_Pa']:.4g} Pa, mdot {values['mdot_kg_s']:.4g} kg/s"
    head_txt += f", p {values['p_min_Pa']:.5g}..{values['p_max_Pa']:.5g} Pa, |u| max {values['u_max_m_s']:.4g} m/s"
    values["residual_drops"] = {k: round(v, 2) for k, v in drops.items()}
    files = ["case.cfg", "mesh.su2", "history.csv", "su2.log"]
    files += [f for f in ("flow.vtu", "surface_flow.csv", "surface_flow.vtu") if (work / f).is_file()]
    res = result(values, inp.get("limits"), head_txt, files, notes)
    if not converged or values.get("mass_imbalance", 0.0) > MAX_IMBALANCE:
        res["verdict"] = "UNKNOWN"
    return res


# ------------------------------------------------------------------ Elmer
def _write_msh2(gmsh, path: Path) -> None:
    gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
    gmsh.option.setNumber("Mesh.SaveAll", 0)
    gmsh.write(str(path))


def _sif_vec(v) -> str:
    return " ".join(f"{x:.12g}" for x in v)


def _run_elmer(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    scale = float(inp.get("scale", 0.001))
    gmsh, info = _build(kind, inp, work, True)
    try:
        mesh = _extract(gmsh, info, scale)
        _write_msh2(gmsh, work / "mesh.msh")
    finally:
        gmsh.finalize()
    if len(mesh["xyz"]) > MAX_ELMER_NODES:
        raise ValueError(f"{len(mesh['xyz'])} nodes > {MAX_ELMER_NODES}: coarser mesh_size")
    roles = dict(info["roles"])
    for k, v in (inp.get("roles") or {}).items():
        if k not in roles or v not in ("wall", "slip", "inlet", "outlet", "symmetry"):
            raise ValueError(f"roles: marker {k!r} -> wall|slip|inlet|outlet|symmetry")
        roles[k] = v
    for k, v in roles.items():
        if v == "farfield":
            raise ValueError("flow_elmer: internal flow only (no farfield): set roles")
    eg = run_exe([tool("elmer", "ElmerGrid.exe"), "14", "2", "mesh.msh", "-out", "elmesh"], work, 600)
    if not (work / "elmesh" / "mesh.header").is_file():
        raise RuntimeError("ElmerGrid failed: " + (eg.stdout + eg.stderr)[-400:])
    fluid = _fluid(inp)
    dim = mesh["dim"]
    V = float(inp.get("velocity_m_s", 0) or 0)
    if V <= 0:
        raise ValueError("flow_elmer: velocity_m_s (mean inlet speed) > 0")
    profile = str(inp.get("inlet_profile", "uniform")).lower()
    if profile not in ("uniform", "parabolic"):
        raise ValueError("inlet_profile: uniform or parabolic")
    if profile == "parabolic" and not (kind == "flow_elmer_shape"
                                       and str((inp.get("shape") or {}).get("type")) == "channel"):
        raise ValueError("inlet_profile parabolic: built-in channel only")
    heat = bool(inp.get("temperatures") or inp.get("heat_source_W_m3") or inp.get("heat_flux_W_m2"))
    tol = float(inp.get("nonlinear_tol", 1e-7))
    names = list(info["markers"])
    bid = {n: i + 1 for i, n in enumerate(names)}
    nvel = dim
    lin = ("  Linear System Solver = Direct\n  Linear System Direct Method = UMFPack\n"
           if len(mesh["xyz"]) * (dim + 1) < 200_000 else
           "  Linear System Solver = Iterative\n  Linear System Iterative Method = BiCGStabl\n"
           "  BiCGStabl Polynomial Degree = 4\n  Linear System Preconditioning = ILU1\n"
           "  Linear System Max Iterations = 2000\n  Linear System Convergence Tolerance = 1e-10\n")
    s = ["Header", "  CHECK KEYWORDS Warn", '  Mesh DB "." "elmesh"', "End",
         "Simulation", "  Max Output Level = 4", "  Coordinate System = Cartesian",
         f"  Coordinate Scaling = {scale}", "  Simulation Type = Steady state",
         "  Steady State Max Iterations = 1", "  Output Intervals(1) = 1", "End",
         "Constants", "End",
         "Body 1", f"  Target Bodies(1) = {len(names) + 1}", "  Equation = 1", "  Material = 1",
         "  Initial Condition = 1"]
    if inp.get("heat_source_W_m3"):
        s.append("  Body Force = 1")
    s += ["End", "Initial Condition 1", *[f"  Velocity {i + 1} = 0" for i in range(nvel)],
          "  Pressure = 0"]
    if heat:
        s.append(f"  Temperature = {float(inp.get('initial_temperature_C', 20.0))}")
    s += ["End", "Equation 1", f"  Active Solvers({3 if heat else 2}) = {'1 2 3' if heat else '1 2'}"]
    if heat:
        s.append("  Convection = Computed")
    s += ["End",
          "Solver 1", "  Equation = Navier-Stokes", '  Procedure = "FlowSolve" "FlowSolver"',
          f"  Variable = Flow Solution[Velocity:{nvel} Pressure:1]",
          "  Stabilization Method = Stabilized",
          "  Nonlinear System Max Iterations = 60",
          f"  Nonlinear System Convergence Tolerance = {tol:g}",
          "  Nonlinear System Newton After Iterations = 4",
          "  Nonlinear System Newton After Tolerance = 1e-3",
          "  Nonlinear System Relaxation Factor = 1.0", lin.rstrip("\n"),
          "  Steady State Convergence Tolerance = 1e-8", "End"]
    nxt = 2
    if heat:
        s += ["Solver 2", "  Equation = Heat Equation", '  Procedure = "HeatSolve" "HeatSolver"',
              "  Variable = Temperature", "  Stabilize = True",
              "  Nonlinear System Max Iterations = 3",
              f"  Nonlinear System Convergence Tolerance = {tol:g}", lin.rstrip("\n"),
              "  Steady State Convergence Tolerance = 1e-8", "End"]
        nxt = 3
    s += [f"Solver {nxt}", "  Exec Solver = After All", "  Equation = ResultOutput",
          '  Procedure = "ResultOutputSolve" "ResultOutputSolver"', "  Output File Name = result",
          "  Vtu Format = True", "  Ascii Output = True", "  Save Geometry Ids = True", "End",
          "Material 1", f"  Density = {fluid['rho']:.12g}", f"  Viscosity = {fluid['mu']:.12g}",
          f"  Heat Conductivity = {fluid['k']:.12g}", f"  Heat Capacity = {fluid['cp']:.12g}", "End"]
    if inp.get("heat_source_W_m3"):
        s += ["Body Force 1", f"  Heat Source = {float(inp['heat_source_W_m3']) / fluid['rho']:.12g}", "End"]
    temps = dict(inp.get("temperatures") or {})
    fluxes = dict(inp.get("heat_flux_W_m2") or {})
    for k in list(temps) + list(fluxes):
        if k not in bid:
            raise ValueError(f"temperatures/heat_flux_W_m2: unknown marker {k!r} (markers: {names})")
    for i, n in enumerate(names):
        role = roles[n]
        s += [f"Boundary Condition {i + 1}", f"  Target Boundaries(1) = {bid[n]}", f"  Name = \"{n}\""]
        if role == "inlet":
            ar, nn, _ = _facets(mesh, n)
            d = -(nn * ar[:, None]).sum(axis=0)
            d /= np.linalg.norm(d)
            if profile == "parabolic":       # fully developed plane-channel inflow, mean V
                hm = float(inp["shape"].get("height_mm", 100)) * scale
                s += ["  Velocity 1 = Variable Coordinate 2",
                      f'    Real MATC "6*{V:.12g}*tx*({hm:.12g}-tx)/({hm:.12g}*{hm:.12g})"',
                      "  Velocity 2 = 0"]
            else:
                s += [f"  Velocity {c + 1} = {V * d[c]:.12g}" for c in range(nvel)]
            if heat and "inlet_temperature_C" in inp:
                s.append(f"  Temperature = {float(inp['inlet_temperature_C'])}")
        elif role == "wall":
            s += [f"  Velocity {c + 1} = 0" for c in range(nvel)]
        elif role in ("slip", "symmetry"):
            s += ["  Normal-Tangential Velocity = True", "  Velocity 1 = 0"]
        elif role == "outlet" and inp.get("outlet_pressure_Pa"):
            s.append(f"  External Pressure = {float(inp['outlet_pressure_Pa'])}")
        if n in temps:
            s.append(f"  Temperature = {float(temps[n])}")
        if n in fluxes:
            s.append(f"  Heat Flux = {float(fluxes[n])}")
        s.append("End")
    (work / "case.sif").write_text("\n".join(s) + "\n", encoding="ascii")
    (work / "ELMERSOLVER_STARTINFO").write_text("case.sif\n1\n", encoding="ascii")
    proc = run_exe([tool("elmer"), "case.sif"], work, float(inp.get("timeout_s", 3500)),
                   env_extra={"ELMER_HOME": str(Path(tool("elmer")).parent.parent),
                              "OMP_NUM_THREADS": "1"})
    log = proc.stdout + proc.stderr
    (work / "elmer.log").write_text(log, encoding="utf-8")
    vtus = sorted(work.glob("result*.vtu")) or sorted((work / "elmesh").glob("result*.vtu"))
    if proc.returncode != 0 or not vtus or "ERROR::" in log:
        i = log.find("ERROR::")
        raise RuntimeError(f"ElmerSolver failed (code {proc.returncode}): "
                           + (log[i:i + 400] if i >= 0 else log[-400:]))
    # convergence: last nonlinear relative change of each solver
    relc: Dict[str, float] = {}
    for m in re.finditer(r"ComputeChange:\s*NS \(ITER=\s*(\d+)\)\s*\(NRM,RELC\):\s*\(\s*([-\dEe.+]+)\s+([-\dEe.+]+)"
                         r"\s*\)\s*:: (.+)", log):
        relc[m.group(4).strip()] = float(m.group(3))
    import pyvista as pv
    g = pv.read(str(vtus[-1]))
    keys = {k.lower(): k for k in g.point_data.keys()}
    vel = np.asarray(g.point_data[keys["velocity"]])
    if vel.ndim == 1:
        vel = vel[:, None]
    if vel.shape[1] < 3:
        vel = np.column_stack([vel, np.zeros((len(vel), 3 - vel.shape[1]))])
    fields = {"p": np.asarray(g.point_data[keys["pressure"]]), "u": vel}
    if heat and "temperature" in keys:
        fields["T"] = np.asarray(g.point_data[keys["temperature"]])
    worst = max(relc.values()) if relc else float("nan")
    converged = bool(relc) and worst <= tol * 1.0001
    values: Dict[str, Any] = {"nodes": int(len(mesh["xyz"])), "cells": mesh["ncell"],
                              "relative_change": worst, "converged": int(converged)}
    notes: List[str] = []
    _post_fields(mesh, np.asarray(g.points), fields, roles,
                 {"rho": fluid["rho"], "mu": fluid["mu"], "V": V, "q": 0.5 * fluid["rho"] * V * V, "p_ref": 0.0},
                 inp, values, notes)
    if "T_max" in values:
        values["T_max_C"], values["T_min_C"] = values.pop("T_max"), values.pop("T_min")
    for k in [k for k in values if re.fullmatch(r"T_probe\d+", k)]:
        values[k + "_C"] = values.pop(k)
    if not converged:
        notes.insert(0, f"NOT CONVERGED: nonlinear change {worst:.2e} > {tol:g} -> UNKNOWN")
    head_txt = (f"Elmer flow{' + heat' if heat else ''} {dim}-D, {len(mesh['xyz'])} nodes, "
                f"|u| max {values['u_max_m_s']:.4g} m/s")
    if "dp_Pa" in values:
        head_txt += f", dp {values['dp_Pa']:.4g} Pa"
    if "T_max_C" in values:
        head_txt += f", T {values['T_min_C']:.4g}..{values['T_max_C']:.4g} degC"
    files = ["case.sif", "elmer.log", str(vtus[-1].relative_to(work))]
    res = result(values, inp.get("limits"), head_txt, files, notes)
    if not converged or values.get("mass_imbalance", 0.0) > MAX_IMBALANCE:
        res["verdict"] = "UNKNOWN"
    return res


# ------------------------------------------------------------------ entry
def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    if kind in ("cfd_su2", "cfd_su2_shape"):
        return _run_su2(kind, inp, work)
    if kind in ("flow_elmer", "flow_elmer_shape"):
        return _run_elmer(kind, inp, work)
    raise ValueError(f"unknown kind {kind!r}")


# ------------------------------------------------------------------ reference cases (§9)
def oblique_shock(M1: float, theta_deg: float, gamma: float = 1.4) -> Tuple[float, float]:
    """(beta_deg, p2/p1) of the weak attached oblique shock: theta-beta-M relation and normal-shock
    pressure ratio on M1 sin(beta), NACA Report 1135 (Ames Research Staff, 1953)."""
    th = math.radians(theta_deg)

    def f(b):
        return (2 / math.tan(b) * (M1 ** 2 * math.sin(b) ** 2 - 1)
                / (M1 ** 2 * (gamma + math.cos(2 * b)) + 2) - math.tan(th))

    lo, hi = math.asin(1 / M1) + 1e-9, math.radians(64.0)    # weak branch below beta_max
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    b = 0.5 * (lo + hi)
    Mn = M1 * math.sin(b)
    return math.degrees(b), 1 + 2 * gamma / (gamma + 1) * (Mn ** 2 - 1)


_H, _L, _U, _MU = 100.0, 2000.0, 1.0, 0.01          # mm, mm, m/s, Pa s (rho 1: Re_h = 10)
_Q, _K = 2.0e5, 5.0                                 # W/m^3, W/(m K)
_POIS_FLUID = {"density_kg_m3": 1.0, "viscosity_Pa_s": _MU, "conductivity_W_mK": _K, "cp_J_kgK": 1000.0}
# Plane Poiseuille flow (White, Viscous Fluid Flow, 3rd ed., section 3-3.3): fully developed
# u(y) = 6 U y (h - y) / h^2, so u_max = 1.5 U_mean and dp/dx = 12 mu U / h^2, i.e. Darcy f Re = 96
# on the hydraulic diameter 2 h. Both are checked against the mean speed the solver actually
# carries (mass flow / (rho h)): a vertex-based code puts the inlet corner nodes on the no-slip
# wall, so its flow rate is a little under U h. Uniform inlet, Re_h = 10: the entrance length
# (~1 h, Durst et al., J. Fluids Eng. 127, 2005) ends long before the probes at x = 5 h and 15 h.
_POIS = {"shape": {"type": "channel", "length_mm": _L, "height_mm": _H},
         "fluid": _POIS_FLUID, "velocity_m_s": _U, "probes": [[500, 50], [1500, 50]]}
REFERENCES = [
    {   # SU2 incompressible laminar, plane Poiseuille flow (see above)
        "name": "poiseuille_su2", "kind": "cfd_su2_shape",
        "inp": dict(_POIS, shape=dict(_POIS["shape"], cells_y=32, cells_x=320),
                    solver="INC_NAVIER_STOKES", residual_orders=6, max_iter=4000),
        "expect": {"converged": [1, 0], "u_max_to_mean_outlet": [1.5, 0.01], "fRe_probes": [96.0, 0.01]},
    },
    {   # Mach 2 over a 10 deg wedge, inviscid: attached oblique shock, beta = 39.31 deg,
        # p2/p1 = 1.7066 (theta-beta-M relation and the normal-shock pressure ratio on M1 sin(beta),
        # NACA Report 1135, Ames Research Staff, 1953; computed by oblique_shock()). Checked on the
        # median wall pressure of the ramp: the uniform plateau behind the shock.
        "name": "wedge_mach2_su2", "kind": "cfd_su2_shape",
        "inp": {"shape": {"type": "ramp", "angle_deg": 10, "upstream_mm": 400, "ramp_length_mm": 1000,
                          "height_mm": 1100, "cells_y": 80},
                "solver": "EULER", "mach": 2.0, "p_inf_Pa": 101325, "T_inf_K": 288.15,
                "residual_orders": 4, "max_iter": 3000},
        "expect": {"converged": [1, 0], "p_ratio_median_ramp": [oblique_shock(2.0, 10.0)[1], 0.01]},
    },
    {   # Elmer: the same channel (uniform inlet) with a uniform heat source q and both walls at
        # 0 degC. Once thermally developed, T(y) = q y (h - y) / (2 k) whatever the flow (it is
        # parallel to the walls), centre T = q h^2 / (8 k) = 50 degC (plane wall with generation,
        # Incropera, Fundamentals of Heat and Mass Transfer, eq. 3.42). Thermal decay length
        # ~ U h^2 / (alpha pi^2) = 0.2 m here: the probe at x = 1.5 m is 7 lengths downstream.
        "name": "poiseuille_heat_elmer", "kind": "flow_elmer_shape",
        "inp": dict(_POIS, shape=dict(_POIS["shape"], cells_y=24, cells_x=240),
                    heat_source_W_m3=_Q, temperatures={"wall_bottom": 0.0, "wall_top": 0.0}),
        "expect": {"converged": [1, 0], "u_max_to_mean_outlet": [1.5, 0.01], "fRe_probes": [96.0, 0.01],
                   "T_probe1_C": [_Q * (_H * 1e-3) ** 2 / (8 * _K), 0.01]},
    },
]
