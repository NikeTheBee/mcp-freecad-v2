"""Structural and thermal FEM with Gmsh + CalculiX, outside FreeCAD (env `sim`).

Kinds (geometry = a STEP/BREP of the solid, in mm, written by the server from FreeCAD):
- fem_static     force, pressure, gravity, rotation, uniform temperature change (dilatation)
- fem_modal      natural frequencies
- fem_buckling   linear buckling factors (load multiplier); real shells need a knock-down factor
- fem_thermal    steady conduction with fixed temperatures, heat flux, convection
- fem_thermomech steady coupled temperature-displacement: thermal stresses and dilatation

Units inside: mm, N, MPa, t/mm^3, s, mW (consistent set). Inputs in common units:
material {E MPa, nu, rho kg/m^3, alpha 1/K, k W/(m K), cp J/(kg K), yield MPa}; pressure MPa;
heat flux W/m^2; film h W/(m^2 K); temperatures degC; gravity m/s^2; rotation rpm.

Faces: "xmin".."zmax" (every planar face on that side of the bounding box) or "FaceN" (FreeCAD
numbering; the server sends each FaceN's centre and area, matched here to Gmsh's surfaces).

Mesh: 10-node tetrahedra (Gmsh order 2). Exactness: REFERENCES below, closed-form answers.
Limits: linear elastic, small displacements; buckling is linear (eigenvalue).
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from _common import exe, geometry_path, result, run_exe

ENV = "sim"
KINDS = {
    "fem_static": {"geometry": True, "timeout": 1800,
                   "doc": "stress/displacement: fixed faces, forces, pressure, gravity, rotation, delta_T"},
    "fem_modal": {"geometry": True, "timeout": 1800, "doc": "natural frequencies (n_modes)"},
    "fem_buckling": {"geometry": True, "timeout": 1800, "doc": "linear buckling load factors"},
    "fem_thermal": {"geometry": True, "timeout": 1800,
                    "doc": "steady conduction: temperatures, heat flux, convection (film)"},
    "fem_thermomech": {"geometry": True, "timeout": 1800,
                       "doc": "steady thermal + structural coupled: thermal stress, dilatation"},
}
MAX_NODES = 400_000
_SIDES = {"xmin": (0, False), "xmax": (0, True), "ymin": (1, False), "ymax": (1, True),
          "zmin": (2, False), "zmax": (2, True)}
# CalculiX/Abaqus C3D10 faces by corner index (0-based): F1 123, F2 142, F3 243, F4 341
_TET_FACES = ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0))
MATERIALS = {
    "steel":     dict(E=210000, nu=0.30, rho=7850, alpha=1.2e-5, k=50, cp=470, yield_=235),
    "stainless": dict(E=193000, nu=0.29, rho=8000, alpha=1.7e-5, k=16, cp=500, yield_=205),
    "aluminium": dict(E=69000, nu=0.33, rho=2700, alpha=2.3e-5, k=167, cp=896, yield_=240),
    "titanium":  dict(E=114000, nu=0.34, rho=4430, alpha=8.6e-6, k=6.7, cp=526, yield_=880),
    "copper":    dict(E=117000, nu=0.34, rho=8940, alpha=1.7e-5, k=390, cp=385, yield_=70),
    "pla":       dict(E=3500, nu=0.36, rho=1240, alpha=6.8e-5, k=0.13, cp=1800, yield_=50),
    "petg":      dict(E=2100, nu=0.40, rho=1270, alpha=6.0e-5, k=0.2, cp=1200, yield_=45),
    "abs":       dict(E=2200, nu=0.35, rho=1040, alpha=9.0e-5, k=0.17, cp=1400, yield_=40),
    "pom":       dict(E=2900, nu=0.35, rho=1410, alpha=1.1e-4, k=0.31, cp=1470, yield_=65),
}


def versions() -> Dict[str, str]:
    import gmsh
    out = run_exe([exe("ccx"), "-v"], Path.cwd(), 30)
    m = re.search(r"[Vv]ersion\s+([\d.]+)", out.stdout + out.stderr)
    return {"calculix": m.group(1) if m else "?", "gmsh": gmsh.__version__}


# ------------------------------------------------------------------ material
def _material(spec) -> Dict[str, float]:
    if isinstance(spec, str):
        key = spec.lower().replace("aluminum", "aluminium")
        if key not in MATERIALS:
            raise ValueError(f"unknown material {spec!r}; one of {sorted(MATERIALS)} or a dict")
        m = dict(MATERIALS[key], name=key)
    elif isinstance(spec, dict):
        base = MATERIALS.get(str(spec.get("base", "")).lower(), {})
        m = dict(base, **{k: v for k, v in spec.items() if k != "base"})
        if "yield" in m:
            m["yield_"] = m.pop("yield")
        m.setdefault("name", "custom")
        for k in ("E", "nu"):
            if k not in m:
                raise ValueError("a custom material needs E (MPa) and nu")
    else:
        raise ValueError("material: a name or a dict {E, nu, rho, alpha, k, cp, yield}")
    return m


# ------------------------------------------------------------------ mesh
def _mesh(geom: Path, size: float, face_info: Dict[str, Any]):
    import gmsh
    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 8)
        gmsh.model.add("m")
        gmsh.model.occ.importShapes(str(geom))
        gmsh.model.occ.synchronize()
        vols = gmsh.model.getEntities(3)
        if not vols:
            raise ValueError("the geometry holds no solid")
        if len(vols) > 1:
            gmsh.model.occ.fragment(vols[:1], vols[1:])
            gmsh.model.occ.synchronize()
        xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(-1, -1)
        bbox = (xmin, ymin, zmin, xmax, ymax, zmax)
        if size <= 0:
            size = max(min(xmax - xmin, ymax - ymin, zmax - zmin) / 4.0,
                       max(xmax - xmin, ymax - ymin, zmax - zmin) / 60.0)
        gmsh.option.setNumber("Mesh.MeshSizeMax", size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", size / 10)
        gmsh.option.setNumber("Mesh.ElementOrder", 2)
        gmsh.option.setNumber("Mesh.HighOrderOptimize", 1)
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)
        gmsh.model.mesh.generate(3)
        tags, coords, _ = gmsh.model.mesh.getNodes()
        xyz = coords.reshape(-1, 3)
        if len(tags) > MAX_NODES:
            raise ValueError(f"{len(tags)} nodes > {MAX_NODES}: pass a larger mesh_size")
        etypes, _, enodes = gmsh.model.mesh.getElements(3)
        tets = None
        for t, n in zip(etypes, enodes):
            if t == 11:                                  # 10-node tetrahedron
                tets = n.reshape(-1, 10)
        if tets is None:
            raise RuntimeError("Gmsh produced no 10-node tetrahedra")
        tets = tets[:, [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]]  # Gmsh -> CalculiX/VTK node order
        surfaces = {}
        for _, s in gmsh.model.getEntities(2):
            com = gmsh.model.occ.getCenterOfMass(2, s)
            area = gmsh.model.occ.getMass(2, s)
            sb = gmsh.model.getBoundingBox(2, s)
            ntags, _, _ = gmsh.model.mesh.getNodes(2, s, includeBoundary=True)
            et, _, en = gmsh.model.mesh.getElements(2, s)
            tris = [n.reshape(-1, 6) for t, n in zip(et, en) if t == 9]
            surfaces[s] = {"com": com, "area": area, "bbox": sb, "nodes": ntags,
                           "tris": tris[0] if tris else np.zeros((0, 6), dtype=np.uint64)}
        return tags, xyz, tets, surfaces, bbox, size
    finally:
        gmsh.finalize()


def _select(spec: str, surfaces, bbox, face_info) -> List[int]:
    diag = math.dist(bbox[:3], bbox[3:])
    tol = 1e-4 * diag + 1e-6
    s = spec.strip()
    if s.lower() in _SIDES:
        axis, is_max = _SIDES[s.lower()]
        plane = bbox[3 + axis] if is_max else bbox[axis]
        hit = [t for t, d in surfaces.items()
               if abs(d["bbox"][axis] - plane) < tol and abs(d["bbox"][3 + axis] - plane) < tol]
    elif re.fullmatch(r"Face\d+", s):
        info = face_info.get(s)
        if not info:
            raise ValueError(f"{s}: no centre/area sent for it (the server resolves FaceN)")
        hit = [t for t, d in surfaces.items()
               if math.dist(d["com"], info["com"]) < 1e-3 * diag
               and abs(d["area"] - info["area"]) < 1e-3 * max(info["area"], 1e-9)]
    else:
        raise ValueError(f"face {spec!r}: FaceN or one of {sorted(_SIDES)}")
    if not hit:
        raise ValueError(f"face {spec!r} matches no surface of the meshed solid")
    return hit


def _faces_of(sel: List[int], surfaces, face_map) -> List[Tuple[int, int, float]]:
    """(element id, CalculiX face number 1-4, triangle area) for each boundary triangle."""
    out = []
    for s in sel:
        for tri in surfaces[s]["tris"]:
            key = frozenset(int(x) for x in tri[:3])
            if key not in face_map:
                raise RuntimeError("a boundary triangle has no tetrahedron (mesh not conforming)")
            out.append(face_map[key])
    return out


# ------------------------------------------------------------------ CalculiX deck
def _deck(kind: str, inp: Dict[str, Any], tags, xyz, tets, surfaces, bbox, face_info, mat):
    nid = {int(t): i for i, t in enumerate(tags)}
    lines = ["*HEADING", f"mcpfc {kind}", "*NODE, NSET=NALL"]
    lines += [f"{int(t)},{x:.9g},{y:.9g},{z:.9g}" for t, (x, y, z) in zip(tags, xyz)]
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    lines += [f"{i + 1}," + ",".join(str(int(n)) for n in e) for i, e in enumerate(tets)]
    face_map: Dict[frozenset, Tuple[int, int]] = {}
    for i, e in enumerate(tets):
        for f, (a, b, c) in enumerate(_TET_FACES):
            face_map[frozenset((int(e[a]), int(e[b]), int(e[c])))] = (i + 1, f + 1)
    thermal = kind in ("fem_thermal", "fem_thermomech")
    structural = kind != "fem_thermal"

    def nodes_of(spec_list) -> List[int]:
        ns = set()
        for spec in spec_list:
            for s in _select(spec, surfaces, bbox, face_info):
                ns.update(int(n) for n in surfaces[s]["nodes"])
        return sorted(ns)

    fixed_nodes: List[int] = []
    if structural:
        fixed_nodes = nodes_of(inp.get("fixed") or [])
        if not fixed_nodes:
            raise ValueError("fixed: at least one face (else the part flies away)")
        lines += ["*NSET, NSET=NFIX"] + _chunks(fixed_nodes)
    # material
    lines += ["*MATERIAL, NAME=M", "*ELASTIC", f"{mat['E']},{mat['nu']}"]
    if mat.get("rho"):
        lines += ["*DENSITY", f"{float(mat['rho']) * 1e-12:.6e}"]
    t0 = float(inp.get("initial_T", inp.get("ref_T", 20.0)))
    if mat.get("alpha"):
        lines += [f"*EXPANSION, ZERO={t0}", f"{float(mat['alpha']):.6e}"]
    if thermal:
        if not mat.get("k"):
            raise ValueError("thermal analysis needs the material conductivity k (W/(m K))")
        lines += ["*CONDUCTIVITY", f"{float(mat['k'])}"]
        lines += ["*SPECIFIC HEAT", f"{float(mat.get('cp', 1000)) * 1e6:.6e}"]
    lines += ["*SOLID SECTION, ELSET=EALL, MATERIAL=M"]
    if structural:
        lines += ["*BOUNDARY", "NFIX,1,3"]
    if thermal or inp.get("delta_T"):
        lines += ["*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"NALL,{t0}"]
    # step
    prestress = kind == "fem_modal" and bool(inp.get("rotation"))
    if prestress:
        # spinning part: a geometrically nonlinear static step under the centrifugal load (stress
        # stiffening and spin softening), then the frequencies about that state (rotating frame)
        lines += ["*STEP, NLGEOM", "*STATIC"]
        lines += _loads(inp, surfaces, bbox, face_info, face_map, tets, xyz, nid)
        lines += ["*END STEP", "*STEP, PERTURBATION"]
    else:
        lines.append("*STEP")
    if kind == "fem_modal":
        lines += ["*FREQUENCY", str(int(inp.get("n_modes", 6)))]
    elif kind == "fem_buckling":
        lines += ["*BUCKLE", str(int(inp.get("n_modes", 4)))]
    elif kind == "fem_thermal":
        lines += ["*HEAT TRANSFER, STEADY STATE", "1.,1."]
    elif kind == "fem_thermomech":
        lines += ["*COUPLED TEMPERATURE-DISPLACEMENT, STEADY STATE", "1.,1."]
    else:
        lines += ["*STATIC"]
    if kind != "fem_modal" and structural:
        lines += _loads(inp, surfaces, bbox, face_info, face_map, tets, xyz, nid)
    if thermal:
        lines += _thermal_bcs(inp, surfaces, bbox, face_info, face_map, nodes_of)
    if inp.get("delta_T") and kind == "fem_static":
        lines += ["*TEMPERATURE", f"NALL,{t0 + float(inp['delta_T'])}"]
    out_vars = []
    if structural:
        out_vars.append("U")
    if thermal:
        out_vars.append("NT")
    lines += ["*NODE FILE", ",".join(out_vars)]
    if structural and kind not in ("fem_modal", "fem_buckling"):
        lines += ["*EL FILE", "S"]
    lines += ["*END STEP"]
    return "\n".join(lines) + "\n", fixed_nodes


def _chunks(ids: List[int], n: int = 12) -> List[str]:
    return [",".join(str(i) for i in ids[k:k + n]) + "," for k in range(0, len(ids), n)]


def _loads(inp, surfaces, bbox, face_info, face_map, tets, xyz, nid) -> List[str]:
    lines: List[str] = []
    cload: Dict[Tuple[int, int], float] = {}
    dload: List[str] = []
    for ld in inp.get("loads") or []:
        typ = ld.get("type", "force")
        sel = [s for spec in ld["faces"] for s in _select(spec, surfaces, bbox, face_info)]
        if typ == "pressure":
            p = float(ld["MPa"])
            for e, f in _faces_of(sel, surfaces, face_map):
                dload.append(f"{e},P{f},{p:.9g}")
        elif typ == "force":
            vec = np.asarray(ld["N"], dtype=float)
            # consistent load of a uniform traction on 6-node triangles: midside nodes take
            # 1/3 of each triangle's share, corners 0
            tris = [t for s in sel for t in surfaces[s]["tris"]]
            areas = []
            for t in tris:
                a, b, c = (xyz[nid[int(t[i])]] for i in range(3))
                areas.append(0.5 * np.linalg.norm(np.cross(b - a, c - a)))
            total = sum(areas)
            for t, a in zip(tris, areas):
                for m in t[3:]:
                    for d in range(3):
                        if vec[d]:
                            key = (int(m), d + 1)
                            cload[key] = cload.get(key, 0.0) + vec[d] * a / total / 3.0
        else:
            raise ValueError(f"load type {typ!r}: force or pressure")
    if inp.get("gravity"):
        g = np.asarray(inp["gravity"], dtype=float)
        mag = float(np.linalg.norm(g))
        if mag:
            d = g / mag
            dload.append(f"EALL,GRAV,{mag * 1000:.9g},{d[0]:.9g},{d[1]:.9g},{d[2]:.9g}")
    if inp.get("rotation"):
        r = inp["rotation"]
        w = float(r["rpm"]) * 2 * math.pi / 60
        p = r.get("point", [0, 0, 0])
        a = np.asarray(r.get("axis", [0, 0, 1]), dtype=float)
        a = a / np.linalg.norm(a)
        dload.append(f"EALL,CENTRIF,{w * w:.9g},{p[0]},{p[1]},{p[2]},{a[0]},{a[1]},{a[2]}")
    if cload:
        lines.append("*CLOAD")
        lines += [f"{n},{d},{v:.9g}" for (n, d), v in sorted(cload.items())]
    if dload:
        lines += ["*DLOAD"] + dload
    return lines


def _thermal_bcs(inp, surfaces, bbox, face_info, face_map, nodes_of) -> List[str]:
    lines: List[str] = []
    temps = inp.get("temperatures") or []
    if temps:
        lines.append("*BOUNDARY")
        for t in temps:
            for n in nodes_of(t["faces"]):
                lines.append(f"{n},11,11,{float(t['C'])}")
    flux = []
    for q in inp.get("heat_flux") or []:                         # W/m^2 into the part
        sel = [s for spec in q["faces"] for s in _select(spec, surfaces, bbox, face_info)]
        flux += [f"{e},S{f},{float(q['W_m2']) * 1e-3:.9g}" for e, f in _faces_of(sel, surfaces, face_map)]
    if flux:
        lines += ["*DFLUX"] + flux
    film = []
    for h in inp.get("convection") or []:
        sel = [s for spec in h["faces"] for s in _select(spec, surfaces, bbox, face_info)]
        film += [f"{e},F{f},{float(h['T_inf_C'])},{float(h['h_W_m2K']) * 1e-3:.9g}"
                 for e, f in _faces_of(sel, surfaces, face_map)]
    if film:
        lines += ["*FILM"] + film
    if not (temps or flux or film):
        raise ValueError("thermal: give temperatures, heat_flux and/or convection")
    if not temps and not film:
        raise ValueError("thermal: a heat flux alone has no steady state; add a temperature or convection")
    return lines


# ------------------------------------------------------------------ results
def _frd_blocks(path: Path) -> Dict[str, List[Dict[int, List[float]]]]:
    """name -> list (one per step/mode) of {node: values}."""
    blocks: Dict[str, List[Dict[int, List[float]]]] = {}
    cur = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(" -4"):
                name = line[5:13].strip()
                cur = {}
                blocks.setdefault(name, []).append(cur)
            elif line.startswith(" -1") and cur is not None:
                node = int(line[3:13])
                body = line[13:].rstrip("\n")
                cur[node] = [float(body[i:i + 12]) for i in range(0, len(body) - 11, 12)]
            elif line.startswith(" -3"):
                cur = None
    return blocks


def _dat_table(path: Path, title: str) -> List[float]:
    text = path.read_text(encoding="utf-8", errors="replace")
    i = text.find(title)
    if i < 0:
        return []
    vals = []
    for line in text[i:].splitlines()[1:]:
        parts = line.split()
        if parts and parts[0].isdigit() and len(parts) >= 2:
            try:
                vals.append([float(p) for p in parts])
            except ValueError:
                break
        elif vals and not parts:
            break
    return vals


def _vm(s) -> np.ndarray:
    s = np.asarray(s)
    sx, sy, sz, txy, tyz, tzx = (s[:, i] for i in range(6))
    return np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2)
                   + 3 * (txy ** 2 + tyz ** 2 + tzx ** 2))


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    from scipy.spatial import cKDTree
    geom = geometry_path(inp, work)
    mat = _material(inp.get("material", "steel"))
    face_info = inp.get("face_info") or {}
    tags, xyz, tets, surfaces, bbox, size = _mesh(geom, float(inp.get("mesh_size", 0) or 0), face_info)
    deck, fixed = _deck(kind, inp, tags, xyz, tets, surfaces, bbox, face_info, mat)
    (work / "job.inp").write_text(deck, encoding="utf-8")
    proc = run_exe([exe("ccx"), "-i", "job"], work, float(inp.get("timeout_s", 1700)),
                   env_extra={"OMP_NUM_THREADS": "8", "CCX_NPROC_EQUATION_SOLVER": "8"})
    frd, dat = work / "job.frd", work / "job.dat"
    if proc.returncode != 0 or not frd.is_file():
        tail = (proc.stdout + proc.stderr)[-500:]
        raise RuntimeError(f"CalculiX failed (code {proc.returncode}): {tail}")
    if "*ERROR" in proc.stdout:
        raise RuntimeError("CalculiX: " + proc.stdout[proc.stdout.find("*ERROR"):][:400])
    blocks = _frd_blocks(frd)
    nid = {int(t): i for i, t in enumerate(tags)}
    values: Dict[str, Any] = {"nodes": int(len(tags)), "elements": int(len(tets)),
                              "mesh_size_mm": round(size, 4)}
    # volume and mass from the mesh (corner tets: exact for straight-sided elements)
    c = xyz[np.vectorize(nid.get)(tets[:, :4])]
    vol = float(np.abs(np.einsum("ij,ij->i", c[:, 1] - c[:, 0],
                                 np.cross(c[:, 2] - c[:, 0], c[:, 3] - c[:, 0]))).sum() / 6)
    values["volume_mm3"] = round(vol, 3)
    if mat.get("rho"):
        values["mass_kg"] = round(vol * float(mat["rho"]) * 1e-9, 6)
    notes = []
    if kind == "fem_modal":
        rows = _dat_table(dat, "E I G E N V A L U E   O U T P U T")
        freqs = [r[3] for r in rows if len(r) >= 4]
        if not freqs:
            raise RuntimeError("no eigenfrequency in CalculiX output")
        values["freqs_Hz"] = [round(f, 4) for f in freqs]
        values["f1_Hz"] = freqs[0]
        if len(freqs) > 1:
            values["f2_Hz"] = freqs[1]
        head = f"modal: f1 = {freqs[0]:.2f} Hz; " + ", ".join(f"{f:.1f}" for f in freqs[:6]) + " Hz"
        if inp.get("rotation"):
            rpm = float(inp["rotation"]["rpm"])
            values["prestress_rpm"] = rpm
            values["rotation_Hz"] = rpm / 60.0
            head += f" spinning at {rpm:g} rpm (rotating frame, centrifugal prestress)"
            notes.append("frequencies in the rotating frame; compare them with the engine orders "
                         "k x rpm/60 (Campbell)")
    elif kind == "fem_buckling":
        rows = _dat_table(dat, "B U C K L I N G   F A C T O R   O U T P U T")
        facs = [r[1] for r in rows if len(r) >= 2]
        if not facs:
            raise RuntimeError("no buckling factor in CalculiX output")
        values["buckling_factors"] = [round(f, 5) for f in facs]
        values["buckling_factor"] = facs[0]
        kd = float(inp.get("knockdown", 1.0))
        values["buckling_factor_knocked_down"] = facs[0] * kd
        head = f"buckling: first factor {facs[0]:.4g} × applied loads"
        notes.append("linear eigenvalue buckling: thin shells need a knock-down (e.g. NASA SP-8007)"
                     + (f", applied {kd}" if kd != 1 else ", none applied"))
    else:
        head_parts = []
        if "DISP" in blocks:
            u = np.array(list(blocks["DISP"][-1].values()))[:, :3]
            mag = np.linalg.norm(u, axis=1)
            values["max_disp_mm"] = float(mag.max())
            for d, ax in enumerate("xyz"):
                values[f"max_abs_u{ax}_mm"] = float(np.abs(u[:, d]).max())
            head_parts.append(f"max displacement {mag.max():.4g} mm")
        if "STRESS" in blocks:
            sd = blocks["STRESS"][-1]
            ids = np.array(list(sd.keys()))
            vm = _vm(np.array(list(sd.values()))[:, :6])
            values["max_vm_MPa"] = float(vm.max())
            away = vm
            if fixed:
                tree = cKDTree(xyz[[nid[n] for n in fixed]])
                dist, _ = tree.query(xyz[[nid[i] for i in ids]])
                mask = dist > 2 * size
                if mask.any():
                    away = vm[mask]
            values["max_vm_away_MPa"] = float(away.max())
            if mat.get("yield_"):
                values["safety_factor"] = float(mat["yield_"]) / max(float(away.max()), 1e-12)
            head_parts.append(f"von Mises max {vm.max():.4g} MPa (away from supports {away.max():.4g})")
            notes.append("peak at a fixed face is usually a mesh singularity: safety factor uses the "
                         "peak away from the supports")
        if "NDTEMP" in blocks:
            t = np.array([v[0] for v in blocks["NDTEMP"][-1].values()])
            values["T_max_C"], values["T_min_C"] = float(t.max()), float(t.min())
            head_parts.append(f"temperature {t.min():.4g}..{t.max():.4g} °C")
            for spec in inp.get("report_faces") or []:
                ns = {int(n) for s in _select(spec, surfaces, bbox, face_info) for n in surfaces[s]["nodes"]}
                tt = [blocks["NDTEMP"][-1][n][0] for n in ns if n in blocks["NDTEMP"][-1]]
                values[f"T_{spec}_C"] = float(np.mean(tt))
        head = f"{kind}: " + ", ".join(head_parts)
    files = ["job.inp", "job.frd"]
    try:
        files.append(_vtu(work, tags, xyz, tets, blocks))
    except Exception as e:  # noqa: BLE001 - the numbers stand without the picture
        notes.append(f"no VTU ({type(e).__name__})")
    return result(values, inp.get("limits"), head, files, notes)


def _vtu(work: Path, tags, xyz, tets, blocks) -> str:
    import pyvista as pv
    nid = {int(t): i for i, t in enumerate(tags)}
    conn = np.vectorize(nid.get)(tets)
    cells = np.hstack([np.full((len(conn), 1), 10), conn]).ravel()
    grid = pv.UnstructuredGrid(cells, np.full(len(conn), 24, dtype=np.uint8), xyz)
    for name, key, ncomp in (("DISP", "displacement_mm", 3), ("NDTEMP", "temperature_C", 1)):
        if name in blocks:
            arr = np.zeros((len(tags), ncomp))
            for n, v in blocks[name][-1].items():
                if n in nid:
                    arr[nid[n]] = v[:ncomp]
            grid.point_data[key] = arr if ncomp > 1 else arr[:, 0]
    if "STRESS" in blocks:
        arr = np.zeros((len(tags), 6))
        for n, v in blocks["STRESS"][-1].items():
            if n in nid:
                arr[nid[n]] = v[:6]
        grid.point_data["von_mises_MPa"] = _vm(arr)
    grid.save(str(work / "result.vtu"))
    return "result.vtu"


# ------------------------------------------------------------------ reference cases (§9)
def box_geometry(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """setup: a box L x b x h (mm) written as STEP, for the reference cases."""
    import gmsh
    L, b, h = inp.pop("box")
    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("ref")
        gmsh.model.occ.addBox(0, 0, 0, L, b, h)
        gmsh.model.occ.synchronize()
        gmsh.write(str(work / "geometry.step"))
    finally:
        gmsh.finalize()
    inp["geometry"] = "geometry.step"
    return inp


_E, _NU = 210000.0, 0.3
_G = _E / (2 * (1 + _NU))
_K = 10 * (1 + _NU) / (12 + 11 * _NU)          # Timoshenko shear coefficient, rectangle
_I20 = 20 * 20 ** 3 / 12
# clamped-free 300 x 10 x 10 mm steel beam, first bending mode (Euler-Bernoulli)
_F0_300 = 1.87510407 ** 2 / (2 * math.pi) * math.sqrt(_E * 1e6 * (0.01 ** 4 / 12) / (7850 * 1e-4 * 0.3 ** 4))
_STEEL = {"E": _E, "nu": _NU, "rho": 7850, "alpha": 1.2e-5, "k": 50, "cp": 470, "yield": 235}
REFERENCES = [
    {   # tip force: Timoshenko beam (bending + shear)
        "name": "cantilever_tip_force", "kind": "fem_static", "setup": "box_geometry",
        "inp": {"box": [200, 20, 20], "material": _STEEL, "fixed": ["xmin"], "mesh_size": 5,
                "loads": [{"type": "force", "faces": ["xmax"], "N": [0, 0, -1000]}]},
        "expect": {"max_abs_uz_mm": [1000 * 200 ** 3 / (3 * _E * _I20) + 1000 * 200 / (_K * _G * 400), 0.03]},
    },
    {   # uniform pressure on the top face: w = p*b
        "name": "cantilever_pressure", "kind": "fem_static", "setup": "box_geometry",
        "inp": {"box": [200, 20, 20], "material": _STEEL, "fixed": ["xmin"], "mesh_size": 5,
                "loads": [{"type": "pressure", "faces": ["zmax"], "MPa": 1.0}]},
        "expect": {"max_abs_uz_mm": [20 * 200 ** 4 / (8 * _E * _I20) + 20 * 200 ** 2 / (2 * _K * _G * 400), 0.03]},
    },
    {   # self weight
        "name": "cantilever_gravity", "kind": "fem_static", "setup": "box_geometry",
        "inp": {"box": [200, 20, 20], "material": _STEEL, "fixed": ["xmin"], "mesh_size": 5,
                "gravity": [0, 0, -9.81]},
        "expect": {"max_abs_uz_mm": [(7850e-12 * 9810 * 400) * 200 ** 4 / (8 * _E * _I20)
                                     + (7850e-12 * 9810 * 400) * 200 ** 2 / (2 * _K * _G * 400), 0.03]},
    },
    {   # free dilatation of a bar clamped at one end: dL = alpha L dT
        "name": "bar_dilatation", "kind": "fem_static", "setup": "box_geometry",
        "inp": {"box": [200, 10, 10], "material": _STEEL, "fixed": ["xmin"], "mesh_size": 3,
                "delta_T": 100, "initial_T": 20},
        "expect": {"max_abs_ux_mm": [1.2e-5 * 200 * 100, 0.02]},
    },
    {   # Euler-Bernoulli first bending mode, clamped-free (1.8751^2)
        "name": "cantilever_modal", "kind": "fem_modal", "setup": "box_geometry",
        "inp": {"box": [200, 20, 20], "material": _STEEL, "fixed": ["xmin"], "mesh_size": 5, "n_modes": 4},
        "expect": {"f1_Hz": [1.87510407 ** 2 / (2 * math.pi)
                             * math.sqrt(_E * 1e6 * (0.02 ** 4 / 12) / (7850 * 4e-4 * 0.2 ** 4)), 0.03]},
    },
    {   # rotating uniform cantilever, root on the axis (Southwell): flap f^2 = f0^2 + 1.193 W^2,
        # lead-lag (in the plane of rotation) f^2 = f0^2 + (1.193 - 1) W^2; W = 100 Hz (6000 rpm)
        "name": "rotating_cantilever_modal", "kind": "fem_modal", "setup": "box_geometry",
        "inp": {"box": [300, 10, 10], "material": _STEEL, "fixed": ["xmin"], "mesh_size": 2.5,
                "n_modes": 4, "rotation": {"rpm": 6000, "point": [0, 0, 0], "axis": [0, 0, 1]}},
        "expect": {"f1_Hz": [math.sqrt(_F0_300 ** 2 + 0.193 * 100.0 ** 2), 0.03],
                   "f2_Hz": [math.sqrt(_F0_300 ** 2 + 1.193 * 100.0 ** 2), 0.03]},
    },
    {   # Euler column clamped-free under unit axial compression: P = pi^2 E I / (4 L^2)
        "name": "euler_column", "kind": "fem_buckling", "setup": "box_geometry",
        "inp": {"box": [500, 10, 10], "material": _STEEL, "fixed": ["xmin"], "mesh_size": 2.5,
                "loads": [{"type": "force", "faces": ["xmax"], "N": [-1000, 0, 0]}], "n_modes": 2},
        "expect": {"buckling_factor": [math.pi ** 2 * _E * (10 ** 4 / 12) / (4 * 500 ** 2) / 1000, 0.03]},
    },
    {   # 1-D conduction + convection in series: q = dT / (L/k + 1/h)
        "name": "bar_conduction_convection", "kind": "fem_thermal", "setup": "box_geometry",
        "inp": {"box": [100, 10, 10], "material": _STEEL, "mesh_size": 3,
                "temperatures": [{"faces": ["xmin"], "C": 100}],
                "convection": [{"faces": ["xmax"], "h_W_m2K": 500, "T_inf_C": 0}],
                "report_faces": ["xmax"]},
        "expect": {"T_xmax_C": [100 - (100 / (0.1 / 50 + 1 / 500)) * 0.1 / 50, 0.01]},
    },
    {   # thermomechanical: 100 degC at one end, 0 at the other, mean dilatation alpha L 50
        "name": "bar_thermomech", "kind": "fem_thermomech", "setup": "box_geometry",
        "inp": {"box": [100, 10, 10], "material": _STEEL, "fixed": ["xmin"], "mesh_size": 3,
                "initial_T": 0,
                "temperatures": [{"faces": ["xmin"], "C": 100}, {"faces": ["xmax"], "C": 0}]},
        "expect": {"max_abs_ux_mm": [1.2e-5 * 100 * 50, 0.03]},
    },
]
