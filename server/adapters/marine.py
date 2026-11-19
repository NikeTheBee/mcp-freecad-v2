"""Marine and underwater engineering: hydrostatics, added mass, propellers, pressure hulls, O-rings
(env `sim`: numpy, scipy, gmsh, capytaine 3).

Kinds (geometry = a STEP/BREP of the hull/vehicle in mm, CAD frame: x forward, y port, z up):
- hydrostatics   floating (equilibrium waterline for a mass, or a given waterline) or submerged:
                 volume, buoyancy, centre of buoyancy, draft, reserve buoyancy, BM/GM (floating),
                 BG and net buoyancy (submerged), small static heel/trim from CG-CB offsets.
- added_mass     Capytaine 3 radiation problem: 6x6 added mass and radiation damping, SI.
                 fluid "infinite" (default: no free surface, frequency independent, zero damping)
                 or "free_surface" (deep water, waterline at waterline_z_mm, given frequencies).
- thruster       Wageningen B-series open water (Oosterveld & van Oossanen 1975 polynomials,
                 Rn = 2e6): J, KT, KQ, thrust, torque, power, efficiency; Keller (1966) minimum
                 blade-area ratio against cavitation when shaft_depth_m is given.
- pressure_hull  external pressure on a cylinder (von Mises / Windenburg-Trilling lobar buckling,
                 NASA SP-8007 knock-down) or a sphere (Zoelly classical buckling x knock-down),
                 Lame thick-wall stresses, yield; safety factor and collapse depth.
- oring_gland    squeeze, gland fill, stretch (Parker O-Ring Handbook ORD 5700 ch. 3 ranges).

Units: inputs as named (suffix _mm, _m, _kg, _MPa, _Pa, _m_s, _rpm...); rho_kg_m3 default 1025
(seawater); g = 9.80665 m/s^2. Outputs in SI (N, m^3, kg, W, N m) and mm for positions/lengths.
Limits: hydrostatics are exact for the triangulated surface (a curved hull is approximated by its
mesh: mesh_volume_error_pct says how much); stability is initial (small angle, no free-surface
correction); propeller polynomials are open water, uniform inflow, Rn 2e6 (no Reynolds correction);
pressure hull checks are hand formulas: shell between rigid frames/end caps, no general instability
of frames, no fatigue, no penetrations; elastic buckling and yield are not combined (min of both).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from _common import geometry_path, result

ENV = "sim"
KINDS = {
    "hydrostatics": {"geometry": True, "timeout": 600,
                     "doc": "buoyancy, CB, equilibrium draft, reserve, GM_T/GM_L or BG (submerged): mass_kg, cg_mm"},
    "added_mass": {"geometry": True, "timeout": 1800,
                   "doc": "Capytaine 6x6 added mass/damping, infinite fluid or free surface (periods_s)"},
    "thruster": {"geometry": False, "timeout": 120,
                 "doc": "Wageningen B open water: rpm, Va, D, P/D, Ae/A0, Z -> T, Q, P, eta; Keller cavitation"},
    "pressure_hull": {"geometry": False, "timeout": 120,
                      "doc": "cylinder/sphere under external pressure: buckling, Lame, yield, SF, collapse depth"},
    "oring_gland": {"geometry": False, "timeout": 60,
                    "doc": "O-ring squeeze, gland fill, stretch vs Parker ORD 5700 ranges"},
}
G = 9.80665
P_ATM = 101325.0


def versions() -> Dict[str, str]:
    import gmsh
    import scipy
    out = {"gmsh": gmsh.__version__, "numpy": np.__version__, "scipy": scipy.__version__}
    try:
        import capytaine
        out["capytaine"] = capytaine.__version__
    except Exception as e:  # noqa: BLE001
        out["capytaine"] = f"missing ({type(e).__name__})"
    return out


def _f(inp: Dict[str, Any], key: str, default=None) -> Optional[float]:
    v = inp.get(key, default)
    return None if v is None else float(v)


def _r(x: float, n: int = 6) -> float:
    return float(f"{x:.{n}g}") if math.isfinite(x) else x


# ------------------------------------------------------------------ surface mesh (gmsh)
def _surface(geom: Path, size: float = 0.0, curvature: int = 48, target_panels: int = 0):
    """Closed outer surface of the solid(s) as outward triangles (mm).

    Returns xyz (N,3), tris (M,3), info {cad_volume_mm3, cad_com_mm, bbox, inner_voids, size}.
    Inner shells (sealed voids) are left out: the outer envelope is what displaces water."""
    import gmsh
    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("hull")
        gmsh.model.occ.importShapes(str(geom))
        gmsh.model.occ.synchronize()
        vols = gmsh.model.getEntities(3)
        if not vols:
            raise ValueError("the geometry holds no solid (a closed volume is needed)")
        if len(vols) > 1:
            gmsh.model.occ.fuse(vols[:1], vols[1:])
            gmsh.model.occ.synchronize()
            vols = gmsh.model.getEntities(3)
        cad_v, com = 0.0, np.zeros(3)
        for _, v in vols:
            m = gmsh.model.occ.getMass(3, v)
            cad_v += m
            com += m * np.array(gmsh.model.occ.getCenterOfMass(3, v))
        com /= max(cad_v, 1e-30)
        bbox = gmsh.model.getBoundingBox(-1, -1)
        diag = math.dist(bbox[:3], bbox[3:])
        # the surface loops of each solid; which one is the OUTER shell is decided after meshing,
        # by the volume each loop encloses: the CAD bounding boxes of curved faces (tori of
        # fillets) are loose, and an inner cavity with fillets looked bigger than its hull
        solid_loops: List[List[List[int]]] = []
        voids = 0
        for _, v in vols:
            _, loops = gmsh.model.occ.getSurfaceLoops(v)
            loops = [[abs(int(x)) for x in l] for l in loops]
            voids += len(loops) - 1
            solid_loops.append(loops)
        if size <= 0:
            size = diag / 40
        if target_panels:
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
        else:
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", curvature)
        for attempt in range(3):
            gmsh.option.setNumber("Mesh.MeshSizeMax", size)
            gmsh.option.setNumber("Mesh.MeshSizeMin", size / 20)
            gmsh.model.mesh.clear()
            gmsh.model.mesh.generate(2)
            tags, coords, _ = gmsh.model.mesh.getNodes()
            xyz = coords.reshape(-1, 3)
            idx = {int(t): i for i, t in enumerate(tags)}
            def loop_tris(loop):
                parts = []
                for sf in loop:
                    et, _, en = gmsh.model.mesh.getElements(2, sf)
                    for t, n in zip(et, en):
                        if t == 2:
                            parts.append(np.vectorize(idx.get)(np.asarray(n, dtype=np.int64).reshape(-1, 3)))
                return _orient(parts, len(xyz), xyz)

            def enclosed(t):
                if not len(t):
                    return 0.0
                a, b, c = xyz[t[:, 0]], xyz[t[:, 1]], xyz[t[:, 2]]
                return abs(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0)

            shells = []
            for loops in solid_loops:
                cands = [loop_tris(l) for l in loops]
                shells.append(max(cands, key=enclosed))
            tris = np.concatenate(shells) if shells else np.zeros((0, 3), dtype=np.int64)
            if (not target_panels or attempt == 2 or not len(tris)
                    or 0.6 * target_panels <= len(tris) <= 1.3 * target_panels):
                break
            area = float(np.linalg.norm(np.cross(xyz[tris[:, 1]] - xyz[tris[:, 0]],
                                                 xyz[tris[:, 2]] - xyz[tris[:, 0]]), axis=1).sum() / 2)
            size = math.sqrt(area / (0.433 * target_panels))
        if len(tris) < 4:
            raise ValueError("surface mesh failed (no triangle on the outer shell)")
        if len(tris) > 400_000:
            raise ValueError(f"surface mesh too fine ({len(tris)} triangles): pass a larger mesh_size_mm")
    finally:
        gmsh.finalize()
    return xyz, tris, {"cad_volume_mm3": cad_v, "cad_com_mm": com, "bbox": bbox,
                       "inner_voids": voids, "size": size}


def _orient(per_surface: List[np.ndarray], n_nodes: int, xyz: np.ndarray) -> np.ndarray:
    """One consistent outward orientation: each CAD face's triangles are consistent among
    themselves (gmsh); faces are matched across their shared mesh edges (a shared edge must run
    in opposite directions), then the whole shell is flipped if its volume is negative."""
    if not per_surface:
        return np.zeros((0, 3), dtype=np.int64)
    k = len(per_surface)
    keys, sids, dirs = [], [], []
    for i, tri in enumerate(per_surface):
        e = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
        lo, hi = e.min(1), e.max(1)
        keys.append(lo * n_nodes + hi)
        sids.append(np.full(len(e), i))
        dirs.append(e[:, 0] < e[:, 1])
    key, sid, dr = np.concatenate(keys), np.concatenate(sids), np.concatenate(dirs)
    order = np.argsort(key, kind="stable")
    key, sid, dr = key[order], sid[order], dr[order]
    same = np.nonzero((key[1:] == key[:-1]) & (sid[1:] != sid[:-1]))[0]
    adj: Dict[int, Dict[int, int]] = {i: {} for i in range(k)}
    for j in same:
        a, b = int(sid[j]), int(sid[j + 1])
        rel = -1 if dr[j] == dr[j + 1] else 1          # same direction -> opposite orientation
        adj[a].setdefault(b, rel)
        adj[b].setdefault(a, rel)
    sign = [0] * k
    out = []
    for start in range(k):                              # one closed shell per component
        if sign[start]:
            continue
        sign[start] = 1
        comp, stack = [start], [start]
        while stack:
            a = stack.pop()
            for b, rel in adj[a].items():
                if not sign[b]:
                    sign[b] = sign[a] * rel
                    comp.append(b)
                    stack.append(b)
        tris = np.vstack([per_surface[i] if sign[i] > 0 else per_surface[i][:, ::-1] for i in comp])
        c = xyz[tris]
        vol = np.einsum("ij,ij->i", c[:, 0], np.cross(c[:, 1], c[:, 2])).sum() / 6
        out.append(tris if vol >= 0 else tris[:, ::-1])
    return np.vstack(out)


# ------------------------------------------------------------------ exact integrals below a plane
def _clip_below(P: np.ndarray, zw: float) -> np.ndarray:
    """Triangles (K,3,3) clipped to z <= zw, orientation kept."""
    below = P[:, :, 2] <= zw
    nb = below.sum(1)
    out = [P[nb == 3]]
    for case in (1, 2):
        sel = P[nb == case]
        if not len(sel):
            continue
        b = below[nb == case]
        odd = np.argmax(b if case == 1 else ~b, axis=1)      # the vertex alone on its side
        order = (odd[:, None] + np.arange(3)[None, :]) % 3
        R = np.take_along_axis(sel, order[:, :, None], axis=1)
        a, p, q = R[:, 0], R[:, 1], R[:, 2]

        def cut(u, v):
            t = (zw - u[:, 2]) / (v[:, 2] - u[:, 2])
            return u + t[:, None] * (v - u)
        ab, ac = cut(a, p), cut(a, q)
        if case == 1:                                       # a below: one small triangle
            out.append(np.stack([a, ab, ac], 1))
        else:                                               # a above: quad ab, p, q, ac
            out.append(np.stack([ab, p, q], 1))
            out.append(np.stack([ab, q, ac], 1))
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def _props(P_all: np.ndarray, zw: float) -> Dict[str, float]:
    """Volume, centroid and waterplane of the part below z = zw (mm).

    Divergence theorem on the clipped hull only: with F = (0,0,z-zw) times x, y or 1, the
    waterplane cap contributes nothing; waterplane integrals are minus the hull's n_z integrals.
    Edge-midpoint quadrature: exact for the quadratic integrands used."""
    P = _clip_below(P_all, zw)
    if not len(P):
        return {"V": 0.0}
    Az = 0.5 * np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])[:, 2]
    M = np.stack([(P[:, 0] + P[:, 1]) / 2, (P[:, 1] + P[:, 2]) / 2, (P[:, 2] + P[:, 0]) / 2], 1)
    x, y, h = M[:, :, 0], M[:, :, 1], M[:, :, 2] - zw
    V = float((Az * h.mean(1)).sum())
    out = {"V": V}
    if V > 0:
        out["xB"] = float((Az * (x * h).mean(1)).sum()) / V
        out["yB"] = float((Az * (y * h).mean(1)).sum()) / V
        out["zB"] = zw + float((Az * (h * h / 2).mean(1)).sum()) / V
    Awp = -float(Az.sum())
    out["Awp"] = Awp
    if Awp > 1e-9 * max(V, 1.0) ** (2 / 3):
        xf = -float((Az * x.mean(1)).sum()) / Awp
        yf = -float((Az * y.mean(1)).sum()) / Awp
        Ixx = -float((Az * (y * y).mean(1)).sum()) - Awp * yf ** 2   # about the x axis (roll)
        Iyy = -float((Az * (x * x).mean(1)).sum()) - Awp * xf ** 2   # about the y axis (pitch)
        out.update(xF=xf, yF=yf, I_T=Ixx, I_L=Iyy)
    return out


def _hydrostatics(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    from scipy.optimize import brentq
    geom = geometry_path(inp, work)
    rho = _f(inp, "rho_kg_m3", 1025.0)
    mass = _f(inp, "mass_kg")
    cg = inp.get("cg_mm")
    cg = None if cg is None else [float(c) for c in cg]
    xyz, tris, info = _surface(geom, float(inp.get("mesh_size_mm", 0) or 0),
                               int(inp.get("curvature_points", 48)))
    P_all = xyz[tris]
    zmin, zmax = float(P_all[:, :, 2].min()), float(P_all[:, :, 2].max())
    full = _props(P_all, zmax + 1.0)
    Vtot = full["V"]
    notes: List[str] = []
    values: Dict[str, Any] = {"triangles": int(len(tris)), "mesh_size_mm": _r(info["size"], 4),
                              "envelope_volume_m3": Vtot * 1e-9, "inner_voids": info["inner_voids"]}
    if Vtot <= 0:
        raise ValueError("the surface encloses no volume (open or inverted shell)")
    if info["inner_voids"] == 0:
        err = (Vtot - info["cad_volume_mm3"]) / info["cad_volume_mm3"] * 100
        values["mesh_volume_error_pct"] = _r(err, 3)
        if abs(err) > 5:
            raise ValueError(f"surface mesh volume differs from the CAD volume by {err:.1f} %: "
                             "the solid is not closed or is badly oriented")
    else:
        notes.append(f"{info['inner_voids']} inner void(s) taken as sealed (displaced water)")
    w2kg = rho * 1e-9                                   # mm^3 -> kg of water
    submerged = bool(inp.get("submerged"))
    if submerged:
        if info["inner_voids"] == 0:                    # exact CAD mass properties
            V, cb = info["cad_volume_mm3"], info["cad_com_mm"]
        else:
            V, cb = Vtot, np.array([full["xB"], full["yB"], full["zB"]])
        B = rho * G * V * 1e-9
        values.update(displaced_volume_m3=V * 1e-9, buoyancy_N=B, displacement_kg=V * w2kg,
                      CB_mm=[_r(c) for c in cb])
        head = f"submerged: V = {V * 1e-9:.5g} m^3, buoyancy {B:.4g} N"
        if mass is not None:
            nb = B - mass * G
            values.update(net_buoyancy_N=nb, net_buoyancy_kg=nb / G)
            head += f", net {nb:+.4g} N ({'floats up' if nb > 0 else 'sinks'})"
        if cg is not None:
            bg = float(cb[2] - cg[2])
            values["BG_mm"] = bg
            head += f", BG {bg:.4g} mm ({'stable: CB above CG' if bg > 0 else 'UNSTABLE: CB not above CG'})"
            if bg > 0:
                values["static_heel_deg"] = math.degrees(math.atan2(cg[1] - cb[1], bg))
                values["static_trim_deg"] = math.degrees(math.atan2(cb[0] - cg[0], bg))
        else:
            notes.append("no cg_mm: BG not computed")
        return result(values, inp.get("limits"), head, [], notes)
    # floating
    if inp.get("waterline_z_mm") is not None:
        zw = float(inp["waterline_z_mm"])
        if mass is not None:
            notes.append("waterline_z_mm given: mass_kg only used for the imbalance")
    elif mass is not None:
        if mass <= 0:
            raise ValueError("mass_kg must be positive")
        if mass >= Vtot * w2kg:
            values.update(sinks=True, max_displacement_kg=Vtot * w2kg,
                          reserve_buoyancy_N=(Vtot * w2kg - mass) * G)
            head = (f"SINKS: mass {mass:.4g} kg > envelope displacement {Vtot * w2kg:.4g} kg "
                    f"({(Vtot * w2kg - mass) * G:.4g} N short)")
            return result(values, inp.get("limits"), head, [], notes)
        zw = brentq(lambda z: _props(P_all, z)["V"] * w2kg - mass, zmin, zmax,
                    xtol=1e-9 * (zmax - zmin), rtol=1e-12, maxiter=200)
    else:
        raise ValueError("hydrostatics needs mass_kg (equilibrium), waterline_z_mm, or submerged: true")
    p = _props(P_all, zw)
    V = p["V"]
    disp = V * w2kg
    values.update(waterline_z_mm=zw, draft_mm=zw - zmin, freeboard_mm=zmax - zw,
                  displaced_volume_m3=V * 1e-9, displacement_kg=disp, buoyancy_N=disp * G,
                  CB_mm=[_r(p.get("xB", math.nan)), _r(p.get("yB", math.nan)), _r(p.get("zB", math.nan))],
                  KB_mm=p.get("zB", math.nan) - zmin,
                  reserve_buoyancy_N=(Vtot - V) * w2kg * G,
                  reserve_buoyancy_pct=(Vtot - V) / V * 100 if V > 0 else math.nan,
                  waterplane_area_m2=p.get("Awp", 0.0) * 1e-6)
    if mass is not None and inp.get("waterline_z_mm") is not None:
        values["vertical_imbalance_N"] = (disp - mass) * G
    head = (f"floating: draft {zw - zmin:.4g} mm (waterline z = {zw:.4g} mm), displacement "
            f"{disp:.4g} kg, freeboard {zmax - zw:.4g} mm, reserve {values['reserve_buoyancy_pct']:.3g} %")
    if "I_T" in p and V > 0:
        bmt, bml = p["I_T"] / V, p["I_L"] / V
        values.update(BM_T_mm=bmt, BM_L_mm=bml, LCF_mm=[_r(p["xF"]), _r(p["yF"])])
        if cg is not None:
            gmt = p["zB"] + bmt - cg[2]
            gml = p["zB"] + bml - cg[2]
            values.update(GM_T_mm=gmt, GM_L_mm=gml, KG_mm=cg[2] - zmin)
            head += f"; GM_T {gmt:.4g} mm, GM_L {gml:.4g} mm"
            if gmt <= 0:
                head += " (UNSTABLE in roll)"
            else:
                values["static_heel_deg"] = math.degrees(math.atan2(cg[1] - p["yB"], gmt))
            if gml > 0:
                values["static_trim_deg"] = math.degrees(math.atan2(cg[0] - p["xB"], gml))
            notes.append("initial stability (small angles), no free-surface correction of tanks; "
                         "heel/trim = small-angle estimate from CG-CB offset")
        else:
            notes.append("no cg_mm: GM not computed (KB and BM given)")
    return result(values, inp.get("limits"), head, [], notes)


# ------------------------------------------------------------------ added mass (Capytaine)
_DOFS = ("Surge", "Sway", "Heave", "Roll", "Pitch", "Yaw")


def _added_mass(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import capytaine as cpt
    cpt.set_logging("ERROR")
    geom = geometry_path(inp, work)
    rho = _f(inp, "rho_kg_m3", 1025.0)
    fluid = str(inp.get("fluid", "infinite"))
    if fluid not in ("infinite", "free_surface"):
        raise ValueError("fluid: 'infinite' (no free surface) or 'free_surface'")
    panels = int(inp.get("panels", 3000))
    if not 200 <= panels <= 8000:
        raise ValueError("panels: 200..8000 (memory grows as panels^2)")
    xyz, tris, info = _surface(geom, float(inp.get("mesh_size_mm", 0) or 0), target_panels=panels)
    zw = float(inp.get("waterline_z_mm", 0.0)) if fluid == "free_surface" else 0.0
    shift = np.array([0.0, 0.0, zw])
    verts = (xyz - shift) * 1e-3
    cg = inp.get("cg_mm")
    rc = (np.array([float(c) for c in cg]) if cg is not None else info["cad_com_mm"])
    rc_m = (rc - shift) * 1e-3
    mesh = cpt.Mesh(vertices=verts, faces=tris)
    notes: List[str] = []
    if fluid == "infinite":
        omegas: List[float] = [1.0]
        free_surface = math.inf
        body = cpt.FloatingBody(mesh=mesh, dofs=cpt.rigid_body_dofs(rotation_center=tuple(rc_m)),
                                center_of_mass=tuple(rc_m))
        notes.append("infinite fluid (no free surface): added mass independent of frequency, "
                     "zero radiation damping; valid for a body deep below the surface")
    else:
        free_surface = 0.0
        if inp.get("omegas_rad_s") is not None:
            omegas = [math.inf if str(w).lower() in ("inf", "infinity") else float(w)
                      for w in inp["omegas_rad_s"]]
        elif inp.get("periods_s") is not None:
            omegas = [2 * math.pi / float(T) for T in inp["periods_s"]]
        else:
            raise ValueError("fluid free_surface needs periods_s or omegas_rad_s ('inf' and 0 allowed)")
        finite = [w for w in omegas if 0 < w < math.inf]
        lid = None
        if inp.get("lid", True) and finite and verts[:, 2].max() > 0 > verts[:, 2].min():
            lid = mesh.generate_lid(z=-1e-3 * (verts[:, 2].max() - verts[:, 2].min()))
        body = cpt.FloatingBody(mesh=mesh, lid_mesh=lid,
                                dofs=cpt.rigid_body_dofs(rotation_center=tuple(rc_m)),
                                center_of_mass=tuple(rc_m)).immersed_part()
        notes.append("deep water, linear potential flow" + (", lid against irregular frequencies"
                                                           if lid is not None else ""))
    solver = cpt.BEMSolver()
    A_all, B_all = [], []
    for w in omegas:
        A = np.zeros((6, 6))
        B = np.zeros((6, 6))
        for j, d in enumerate(_DOFS):
            prob = cpt.RadiationProblem(body=body, free_surface=free_surface, water_depth=math.inf,
                                        radiating_dof=d, omega=w, rho=rho, g=G)
            res = solver.solve(prob, keep_details=False)
            for i, di in enumerate(_DOFS):
                A[i, j] = res.added_masses[di]
                B[i, j] = res.radiation_dampings[di]
        A_all.append(A)
        B_all.append(B)
    A0, B0 = A_all[0], B_all[0]
    vol = float(body.mesh.volume)
    values: Dict[str, Any] = {
        "panels": int(body.mesh.nb_faces), "wetted_volume_m3": vol,
        "rotation_center_mm": [_r(c) for c in rc],
        "omegas_rad_s": [w if math.isfinite(w) else "inf" for w in omegas],
        "added_mass_6x6": [np.round(A, 6).tolist() for A in A_all],
        "damping_6x6": [np.round(B, 6).tolist() for B in B_all],
    }
    names = ["A11_kg", "A22_kg", "A33_kg", "A44_kg_m2", "A55_kg_m2", "A66_kg_m2"]
    for k, n in enumerate(names):
        values[n] = float(A0[k, k])
    values["B33_kg_s"] = float(B0[2, 2])
    disp = rho * vol
    for k, ax in enumerate("xyz"):
        values[f"A{k + 1}{k + 1}_over_displaced"] = float(A0[k, k] / disp) if disp > 0 else math.nan
    head = (f"added mass ({fluid}, {values['panels']} panels): A11 {A0[0, 0]:.4g}, A22 {A0[1, 1]:.4g}, "
            f"A33 {A0[2, 2]:.4g} kg; A44 {A0[3, 3]:.4g}, A55 {A0[4, 4]:.4g}, A66 {A0[5, 5]:.4g} kg m^2"
            f" (first frequency); displaced {disp:.4g} kg")
    notes.append("matrices in SI (kg, kg m, kg m^2) about rotation_center_mm, order surge..yaw; "
                 "constant-panel BEM: a few % discretisation error at ~3000 panels")
    return result(values, inp.get("limits"), head, [], notes)


# ------------------------------------------------------------------ Wageningen B-series
# Oosterveld, M.W.C. & van Oossanen, P. (1975) "Further computer-analyzed data of the Wageningen
# B-screw series", International Shipbuilding Progress 22(251), Table 1 (Rn = 2e6):
#   KT, KQ = sum C * J^s * (P/D)^t * (Ae/A0)^u * Z^v      rows: (C, s, t, u, v)
_KT = (
    (0.00880496, 0, 0, 0, 0), (-0.204554, 1, 0, 0, 0), (0.166351, 0, 1, 0, 0),
    (0.158114, 0, 2, 0, 0), (-0.147581, 2, 0, 1, 0), (-0.481497, 1, 1, 1, 0),
    (0.415437, 0, 2, 1, 0), (0.0144043, 0, 0, 0, 1), (-0.0530054, 2, 0, 0, 1),
    (0.0143481, 0, 1, 0, 1), (0.0606826, 1, 1, 0, 1), (-0.0125894, 0, 0, 1, 1),
    (0.0109689, 1, 0, 1, 1), (-0.133698, 0, 3, 0, 0), (0.00638407, 0, 6, 0, 0),
    (-0.00132718, 2, 6, 0, 0), (0.168496, 3, 0, 1, 0), (-0.0507214, 0, 0, 2, 0),
    (0.0854559, 2, 0, 2, 0), (-0.0504475, 3, 0, 2, 0), (0.010465, 1, 6, 2, 0),
    (-0.00648272, 2, 6, 2, 0), (-0.00841728, 0, 3, 0, 1), (0.0168424, 1, 3, 0, 1),
    (-0.00102296, 3, 3, 0, 1), (-0.0317791, 0, 3, 1, 1), (0.018604, 1, 0, 2, 1),
    (-0.00410798, 0, 2, 2, 1), (-0.000606848, 0, 0, 0, 2), (-0.0049819, 1, 0, 0, 2),
    (0.0025983, 2, 0, 0, 2), (-0.000560528, 3, 0, 0, 2), (-0.00163652, 1, 2, 0, 2),
    (-0.000328787, 1, 6, 0, 2), (0.000116502, 2, 6, 0, 2), (0.000690904, 0, 0, 1, 2),
    (0.00421749, 0, 3, 1, 2), (0.0000565229, 3, 6, 1, 2), (-0.00146564, 0, 3, 2, 2),
)
_KQ = (
    (0.00379368, 0, 0, 0, 0), (0.00886523, 2, 0, 0, 0), (-0.032241, 1, 1, 0, 0),
    (0.00344778, 0, 2, 0, 0), (-0.0408811, 0, 1, 1, 0), (-0.108009, 1, 1, 1, 0),
    (-0.0885381, 2, 1, 1, 0), (0.188561, 0, 2, 1, 0), (-0.00370871, 1, 0, 0, 1),
    (0.00513696, 0, 1, 0, 1), (0.0209449, 1, 1, 0, 1), (0.00474319, 2, 1, 0, 1),
    (-0.00723408, 2, 0, 1, 1), (0.00438388, 1, 1, 1, 1), (-0.0269403, 0, 2, 1, 1),
    (0.0558082, 3, 0, 1, 0), (0.0161886, 0, 3, 1, 0), (0.00318086, 1, 3, 1, 0),
    (0.015896, 0, 0, 2, 0), (0.0471729, 1, 0, 2, 0), (0.0196283, 3, 0, 2, 0),
    (-0.0502782, 0, 1, 2, 0), (-0.030055, 3, 1, 2, 0), (0.0417122, 2, 2, 2, 0),
    (-0.0397722, 0, 3, 2, 0), (-0.00350024, 0, 6, 2, 0), (-0.0106854, 3, 0, 0, 1),
    (0.00110903, 3, 3, 0, 1), (-0.000313912, 0, 6, 0, 1), (0.0035985, 3, 0, 1, 1),
    (-0.00142121, 0, 6, 1, 1), (-0.00383637, 1, 0, 2, 1), (0.0126803, 0, 2, 2, 1),
    (-0.00318278, 2, 3, 2, 1), (0.00334268, 0, 6, 2, 1), (-0.00183491, 1, 1, 0, 2),
    (0.000112451, 3, 2, 0, 2), (-0.0000297228, 3, 6, 0, 2), (0.000269551, 1, 0, 1, 2),
    (0.00083265, 2, 0, 1, 2), (0.00155334, 0, 2, 1, 2), (0.000302683, 0, 6, 1, 2),
    (-0.0001843, 0, 0, 2, 2), (-0.000425399, 0, 3, 2, 2), (0.0000869243, 3, 3, 2, 2),
    (-0.0004659, 0, 6, 2, 2), (0.0000554194, 1, 6, 2, 2),
)
# blade-area ratios tested in the series, per blade number (Kuiper 1992, The Wageningen Propeller
# Series, MARIN publication 92-001); P/D 0.5..1.4 for all
_BSERIES_AE = {2: (0.30, 0.30), 3: (0.35, 0.80), 4: (0.40, 1.00), 5: (0.45, 1.05),
               6: (0.50, 0.80), 7: (0.55, 0.85)}


def _poly(table, J, PD, AE, Z) -> float:
    return float(sum(c * J ** s * PD ** t * AE ** u * Z ** v for c, s, t, u, v in table))


def _thruster(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    from scipy.optimize import brentq
    rho = _f(inp, "rho_kg_m3", 1025.0)
    D = float(inp["diameter_mm"]) * 1e-3
    PD, AE, Z = float(inp["P_D"]), float(inp["Ae_A0"]), int(inp["Z"])
    rpm = float(inp["rpm"])
    if D <= 0 or rpm <= 0:
        raise ValueError("diameter_mm and rpm must be positive")
    n = rpm / 60.0
    if inp.get("J") is not None:
        J = float(inp["J"])
        Va = J * n * D
    else:
        Va = float(inp.get("speed_of_advance_m_s", 0.0))
        J = Va / (n * D)
    notes: List[str] = []
    out_of_range = []
    if not 0.5 <= PD <= 1.4:
        out_of_range.append(f"P/D {PD} not in 0.5..1.4")
    if Z not in _BSERIES_AE:
        out_of_range.append(f"Z {Z} not in 2..7")
    else:
        lo, hi = _BSERIES_AE[Z]
        if not lo - 1e-9 <= AE <= hi + 1e-9:
            out_of_range.append(f"Ae/A0 {AE} outside the B{Z} series ({lo}..{hi})")
    KT, KQ = _poly(_KT, J, PD, AE, Z), _poly(_KQ, J, PD, AE, Z)
    try:
        J0 = brentq(lambda j: _poly(_KT, j, PD, AE, Z), 0.0, 2.0)
    except ValueError:
        J0 = math.nan
    if J < 0 or (math.isfinite(J0) and J > J0):
        out_of_range.append(f"J {J:.4g} outside 0..{J0:.4g} (KT > 0 range)")
    T = KT * rho * n ** 2 * D ** 4
    Q = KQ * rho * n ** 2 * D ** 5
    P = 2 * math.pi * n * Q
    eta = J * KT / (2 * math.pi * KQ) if KQ > 0 else math.nan
    values: Dict[str, Any] = {"J": J, "KT": KT, "KQ": KQ, "eta0": eta, "thrust_N": T, "torque_Nm": Q,
                              "power_W": P, "speed_of_advance_m_s": Va, "J_zero_thrust": J0,
                              "tip_speed_m_s": math.pi * n * D}
    head = (f"B{Z}-{round(AE * 100)} P/D {PD:g}, D {D * 1e3:g} mm, {rpm:g} rpm, J {J:.4g}: "
            f"KT {KT:.4f}, KQ {KQ:.5f}, thrust {T:.4g} N, torque {Q:.4g} N m, power {P:.4g} W, "
            f"eta0 {eta:.3f}")
    if inp.get("shaft_depth_m") is not None:
        h = float(inp["shaft_depth_m"])
        pv = _f(inp, "vapour_pressure_Pa", 1704.0)        # fresh water 15 degC
        K = _f(inp, "keller_K", 0.2)
        p0 = _f(inp, "p_atm_Pa", P_ATM) + rho * G * h
        # Keller (1966), as given in Carlton, Marine Propellers and Propulsion, ch. 12:
        #   (Ae/A0)min = (1.3 + 0.3 Z) T / ((p0 - pv) D^2) + K
        ae_min = (1.3 + 0.3 * Z) * max(T, 0.0) / ((p0 - pv) * D ** 2) + K
        vr2 = Va ** 2 + (0.7 * math.pi * n * D) ** 2
        values.update(AeA0_min_keller=ae_min, cavitation_margin=AE / ae_min,
                      sigma_07R=(p0 - pv) / (0.5 * rho * vr2))
        head += (f"; Keller min Ae/A0 {ae_min:.3f} (K {K:g}) -> margin {AE / ae_min:.3g} "
                 f"({'OK' if AE >= ae_min else 'cavitation risk'})")
        notes.append("cavitation: Keller blade-area criterion only (Burrill chart not implemented)")
    else:
        notes.append("cavitation not checked: pass shaft_depth_m")
    notes.append("open water, uniform inflow, Rn 2e6 (no Reynolds/scale correction), no duct, "
                 "no hull wake or thrust deduction")
    res = result(values, inp.get("limits"), head, [], notes)
    if out_of_range:
        res["verdict"] = "UNKNOWN"
        res["summary"] = "OUTSIDE the B-series (extrapolation): " + "; ".join(out_of_range) + "; " + res["summary"]
    return res


# ------------------------------------------------------------------ pressure hull
_HULL_MATERIALS = {   # typical minimum values; pass your own {E_MPa, nu, yield_MPa} for design
    "aluminium_6061_t6": dict(E_MPa=68900, nu=0.33, yield_MPa=240),
    "aluminium_7075_t6": dict(E_MPa=71700, nu=0.33, yield_MPa=430),
    "steel_s355": dict(E_MPa=210000, nu=0.30, yield_MPa=355),
    "stainless_316": dict(E_MPa=193000, nu=0.29, yield_MPa=205),
    "titanium_gr5": dict(E_MPa=113800, nu=0.34, yield_MPa=830),
}


def _von_mises_cyl(E, nu, R, t, L) -> Tuple[float, int]:
    """von Mises (1929) lobar buckling of a cylinder between rigid supports under hydrostatic
    pressure (end load included), as given by Windenburg & Trilling (1934) Trans. ASME 56:
    p = (E t/R)/(n^2-1+lam^2/2) [1/(n^2/lam^2+1)^2 + t^2 (n^2-1+lam^2)^2/(12 R^2 (1-nu^2))],
    lam = pi R / L, minimised over the integer lobe number n >= 2."""
    lam = math.pi * R / L
    best = (math.inf, 2)
    for n in range(2, 60):
        p = (E * t / R) / (n * n - 1 + 0.5 * lam * lam) * (
            1 / (n * n / (lam * lam) + 1) ** 2
            + t * t * (n * n - 1 + lam * lam) ** 2 / (12 * R * R * (1 - nu * nu)))
        if p < best[0]:
            best = (p, n)
    return best


def _pressure_hull(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    shape = str(inp.get("shape", "cylinder"))
    if shape not in ("cylinder", "sphere"):
        raise ValueError("shape: 'cylinder' or 'sphere'")
    mat = inp.get("material", {})
    if isinstance(mat, str):
        if mat not in _HULL_MATERIALS:
            raise ValueError(f"material {mat!r}: one of {', '.join(_HULL_MATERIALS)} or a dict")
        mat = _HULL_MATERIALS[mat]
    E, nu, Sy = float(mat["E_MPa"]), float(mat.get("nu", 0.3)), _f(mat, "yield_MPa")
    rho = _f(inp, "rho_kg_m3", 1025.0)
    if inp.get("pressure_MPa") is not None:
        p = float(inp["pressure_MPa"])
    elif inp.get("depth_m") is not None:
        p = rho * G * float(inp["depth_m"]) * 1e-6         # gauge: 1 atm inside
    else:
        raise ValueError("pressure_hull needs depth_m or pressure_MPa (external, gauge)")
    Do, t = float(inp["outer_diameter_mm"]), float(inp["thickness_mm"])
    if not 0 < 2 * t < Do:
        raise ValueError("thickness_mm must be > 0 and < outer_diameter_mm / 2")
    b, a = Do / 2, Do / 2 - t
    R = (a + b) / 2
    values: Dict[str, Any] = {"pressure_MPa": p, "R_mean_mm": R, "R_over_t": R / t}
    notes: List[str] = []
    if shape == "cylinder":
        L = float(inp["length_mm"])                      # between frames / end caps
        values["membrane_hoop_MPa"] = -p * R / t
        k = b * b / (b * b - a * a)
        values["lame_hoop_inner_MPa"] = -2 * p * k        # Lame, external pressure, inner wall
        values["lame_hoop_outer_MPa"] = -p * (b * b + a * a) / (b * b - a * a)
        values["lame_vm_inner_MPa"] = math.sqrt(3) * p * k  # closed ends: s_z = -p k
        p_yield = Sy / (math.sqrt(3) * k) if Sy else math.nan
        p_cl, nl = _von_mises_cyl(E, nu, R, t, L)
        Dm = 2 * R
        # Windenburg & Trilling (1934) short-cylinder approximation (cross-check, n >= 3 range)
        den = L / Dm - 0.45 * math.sqrt(t / Dm)
        p_wt = 2.42 * E * (t / Dm) ** 2.5 / ((1 - nu * nu) ** 0.75 * den) if den > 0 else math.nan
        # NASA SP-8007 (rev. 2020, external pressure, unstiffened isotropic cylinders of
        # moderate length): correlation factor 0.75 on the classical buckling pressure
        kd = _f(inp, "knockdown", 0.75)
        values.update(buckling_classical_MPa=p_cl, buckling_lobes=nl, buckling_wt_MPa=p_wt,
                      knockdown=kd, buckling_design_MPa=kd * p_cl,
                      bresse_long_MPa=2 * E / (1 - nu * nu) * (t / Dm) ** 3)
        notes.append("shell between rigid frames/end caps (length_mm); frames' general instability "
                     "and interframe yield (Pmy) not checked")
    else:
        values["membrane_stress_MPa"] = -p * R / (2 * t)
        k3 = b ** 3 / (b ** 3 - a ** 3)
        values["lame_hoop_inner_MPa"] = -1.5 * p * k3      # Lame sphere, inner wall
        values["lame_vm_inner_MPa"] = 1.5 * p * k3         # s_r = 0, s_t = s_p
        p_yield = Sy / (1.5 * k3) if Sy else math.nan
        # Zoelly (1915) classical buckling of a complete sphere
        p_cl = 2 * E * (t / R) ** 2 / math.sqrt(3 * (1 - nu * nu))
        kd = _f(inp, "knockdown")
        values.update(buckling_classical_MPa=p_cl)
        if kd is None:
            values["buckling_design_MPa"] = math.nan
            notes.append("no knockdown given: real spheres buckle far below Zoelly (imperfections; "
                         "NASA SP-8032) -> design buckling and safety factor UNKNOWN")
        else:
            values.update(knockdown=kd, buckling_design_MPa=kd * p_cl)
    values["yield_collapse_MPa"] = p_yield
    pb = values["buckling_design_MPa"]
    pc = min(p_yield, pb) if math.isfinite(p_yield) and math.isfinite(pb) else math.nan
    values["collapse_pressure_MPa"] = pc
    values["mode"] = ("yield" if p_yield <= pb else "buckling") if math.isfinite(pc) else "unknown"
    values["safety_factor"] = pc / p if p > 0 and math.isfinite(pc) else math.nan
    values["collapse_depth_m"] = pc * 1e6 / (rho * G) if math.isfinite(pc) else math.nan
    sf = _f(inp, "safety_factor")
    limits = dict(inp.get("limits") or {})
    if sf is not None:
        limits.setdefault("safety_factor", {"min": sf})
        values["rated_depth_m"] = values["collapse_depth_m"] / sf
    head = (f"{shape} D {Do:g} x t {t:g} mm at {p:.4g} MPa: collapse {pc:.4g} MPa ({values['mode']}), "
            f"SF {values['safety_factor']:.3g}, collapse depth {values['collapse_depth_m']:.4g} m; "
            f"Lame von Mises inner {values['lame_vm_inner_MPa']:.4g} MPa")
    notes.append("elastic buckling and yield taken separately (min): no inelastic interaction; "
                 "real test above max depth required (§10.2)")
    return result(values, limits or None, head, [], notes)


# ------------------------------------------------------------------ O-ring gland
# Parker O-Ring Handbook ORD 5700 (2018), §3.3 & §3.5 stretch <= 5 %; §3.6 squeeze max ~16 %
# dynamic, 30 % static/face, minimum 0.007 in (0.18 mm); §3.7 gland fill 60..85 % (75 % optimum).
_ORING_LIMITS = {
    "static": {"squeeze_pct": {"max": 30.0}, "squeeze_mm": {"min": 0.178},
               "gland_fill_pct": {"min": 60.0, "max": 85.0}, "stretch_pct": {"max": 5.0}},
    "face": {"squeeze_pct": {"max": 30.0}, "squeeze_mm": {"min": 0.178},
             "gland_fill_pct": {"min": 60.0, "max": 85.0}, "stretch_pct": {"max": 5.0}},
    "dynamic": {"squeeze_pct": {"max": 16.0}, "squeeze_mm": {"min": 0.178},
                "gland_fill_pct": {"min": 60.0, "max": 85.0}, "stretch_pct": {"max": 5.0}},
}


def _cs_reduction_pct(stretch_pct: float) -> float:
    """Parker ORD 5700 Fig. 3-3 empirical (observed) cross-section reduction from ID stretch."""
    X = stretch_pct
    if X <= 0:
        return 0.0
    if X <= 3:
        return -0.005 + 1.19 * X - 0.19 * X ** 2 - 0.001 * X ** 3 + 0.008 * X ** 4
    return 0.56 + 0.59 * X - 0.0046 * X ** 2


def _oring(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    service = str(inp.get("service", "static"))
    if service not in _ORING_LIMITS:
        raise ValueError("service: static (radial), face or dynamic")
    cs = float(inp["cs_mm"])
    depth = float(inp["gland_depth_mm"])            # compression gap: groove depth + clearance
    width = float(inp["groove_width_mm"])
    ID = _f(inp, "id_mm")
    seat = _f(inp, "seat_diameter_mm")              # diameter the ring ID is fitted on (groove root)
    stretch = 0.0
    if ID and seat:
        stretch = max((seat - ID) / ID * 100, 0.0)
    red = _cs_reduction_pct(stretch)
    cs_eff = cs * (1 - red / 100)
    sq_mm = cs_eff - depth
    values: Dict[str, Any] = {
        "stretch_pct": stretch, "cs_reduction_pct": red, "cs_effective_mm": cs_eff,
        "squeeze_mm": sq_mm, "squeeze_pct": sq_mm / cs_eff * 100,
        "gland_fill_pct": math.pi * cs_eff ** 2 / 4 / (depth * width) * 100,
    }
    if ID and seat and seat < ID:
        values["id_compression_pct"] = (ID - seat) / ID * 100
    limits = {k: dict(v) for k, v in _ORING_LIMITS[service].items()}
    limits.update(inp.get("limits") or {})
    head = (f"O-ring {cs:g} mm ({service}): squeeze {values['squeeze_pct']:.1f} % ({sq_mm:.3f} mm), "
            f"gland fill {values['gland_fill_pct']:.1f} %, stretch {stretch:.2f} %")
    notes = ["acceptance = Parker ORD 5700 §3.5-3.7 recommendations (dynamic: small sections "
             "may take 25 %); nominal dimensions: also run the tolerance extremes",
             "no swell, thermal expansion or extrusion-gap (back-up ring) check"]
    return result(values, limits, head, [], notes)


# ------------------------------------------------------------------ dispatcher
def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fn = {"hydrostatics": _hydrostatics, "added_mass": _added_mass, "thruster": _thruster,
          "pressure_hull": _pressure_hull, "oring_gland": _oring}.get(kind)
    if fn is None:
        raise ValueError(f"unknown kind {kind!r}")
    return fn(inp, work)


# ------------------------------------------------------------------ reference cases (§9)
def shape_geometry(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """setup: a box [x0,y0,z0,L,B,H], sphere [xc,yc,zc,R] or vertical cylinder [xc,yc,z0,R,H]
    (mm) written as STEP, for the reference cases."""
    import gmsh
    spec = inp.pop("shape_mm")
    gmsh.initialize(["-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("ref")
        if "box" in spec:
            gmsh.model.occ.addBox(*spec["box"])
        elif "sphere" in spec:
            gmsh.model.occ.addSphere(*spec["sphere"])
        elif "hollow_cylinder" in spec:
            # sealed housing: outer R x H, cavity of radius Ri between end caps `cap` thick,
            # its edges filleted rf (curved faces with loose CAD bounding boxes)
            R, H, Ri, cap, rf = spec["hollow_cylinder"]
            occ = gmsh.model.occ
            # cavity = a half-profile with rounded corners (XZ plane) revolved about Z: its
            # fillets are tori, as in a CAD fillet
            z0, z1 = cap, H - cap
            pts = [occ.addPoint(0, 0, z0), occ.addPoint(Ri - rf, 0, z0), occ.addPoint(Ri - rf, 0, z0 + rf),
                   occ.addPoint(Ri, 0, z0 + rf), occ.addPoint(Ri, 0, z1 - rf), occ.addPoint(Ri - rf, 0, z1 - rf),
                   occ.addPoint(Ri - rf, 0, z1), occ.addPoint(0, 0, z1)]
            cl = [occ.addLine(pts[0], pts[1]), occ.addCircleArc(pts[1], pts[2], pts[3]),
                  occ.addLine(pts[3], pts[4]), occ.addCircleArc(pts[4], pts[5], pts[6]),
                  occ.addLine(pts[6], pts[7]), occ.addLine(pts[7], pts[0])]
            surf = occ.addPlaneSurface([occ.addCurveLoop(cl)])
            cav = [e for e in occ.revolve([(2, surf)], 0, 0, 0, 0, 0, 1, 2 * math.pi) if e[0] == 3]
            outer = occ.addCylinder(0, 0, 0, 0, 0, H, R)
            occ.cut([(3, outer)], cav)
        else:
            xc, yc, z0, R, H = spec["cylinder"]
            gmsh.model.occ.addCylinder(xc, yc, z0, 0, 0, H, R)
        gmsh.model.occ.synchronize()
        gmsh.write(str(work / "geometry.step"))
    finally:
        gmsh.finalize()
    inp["geometry"] = "geometry.step"
    return inp


_RHO = 1025.0
# floating box 1000 x 400 x 300 mm, 100 kg: T = m/(rho L B); KB = T/2; BM_T = B^2/(12 T);
# BM_L = L^2/(12 T) (wall-sided body, exact)
_T_BOX = 100 / (_RHO * 1.0 * 0.4) * 1e3
# floating vertical cylinder R 200 mm, 30 kg: T = m/(rho pi R^2); BM = R^2/(4 T)
_T_CYL = 30 / (_RHO * math.pi * 0.2 ** 2) * 1e3
_V_SPH = 4 / 3 * math.pi * 0.1 ** 3
_E_ST, _NU_ST = 210000.0, 0.3
_E_AL, _NU_AL = 70000.0, 0.33
REFERENCES = [
    {   # wall-sided box: closed-form hydrostatics
        "name": "floating_box", "kind": "hydrostatics", "setup": "shape_geometry",
        "inp": {"shape_mm": {"box": [0, 0, 0, 1000, 400, 300]}, "mass_kg": 100,
                "rho_kg_m3": _RHO, "cg_mm": [500, 200, 100]},
        "expect": {"draft_mm": [_T_BOX, 1e-6], "KB_mm": [_T_BOX / 2, 1e-6],
                   "BM_T_mm": [400 ** 2 / (12 * _T_BOX), 1e-6],
                   "BM_L_mm": [1000 ** 2 / (12 * _T_BOX), 1e-6],
                   "GM_T_mm": [_T_BOX / 2 + 400 ** 2 / (12 * _T_BOX) - 100, 1e-6],
                   "reserve_buoyancy_N": [(1.0 * 0.4 * 0.3 * _RHO - 100) * G, 1e-6]},
    },
    {   # curved hull through the mesh path: vertical cylinder, T = m/(rho pi R^2), BM = R^2/(4T)
        "name": "floating_vertical_cylinder", "kind": "hydrostatics", "setup": "shape_geometry",
        "inp": {"shape_mm": {"cylinder": [0, 0, 0, 200, 500]}, "mass_kg": 30, "rho_kg_m3": _RHO,
                "cg_mm": [0, 0, 150]},
        "expect": {"draft_mm": [_T_CYL, 0.005], "BM_T_mm": [200 ** 2 / (4 * _T_CYL), 0.01],
                   "BM_L_mm": [200 ** 2 / (4 * _T_CYL), 0.01]},
    },
    {   # Archimedes: submerged sphere R 100 mm, B = rho g 4/3 pi R^3; BG = zB - zG
        "name": "submerged_sphere", "kind": "hydrostatics", "setup": "shape_geometry",
        "inp": {"shape_mm": {"sphere": [0, 0, 0, 100]}, "submerged": True, "mass_kg": 3.5,
                "rho_kg_m3": _RHO, "cg_mm": [0, 0, -20]},
        "expect": {"buoyancy_N": [_RHO * G * _V_SPH, 1e-4],
                   "net_buoyancy_N": [_RHO * G * _V_SPH - 3.5 * G, 1e-3], "BG_mm": [20.0, 1e-3]},
    },
    {   # sealed hollow housing with filleted inner edges: it displaces its OUTER envelope,
        # V = pi R^2 H (the cavity is sealed); faceting of the curved hull within 0.5 %
        "name": "sealed_hollow_housing", "kind": "hydrostatics", "setup": "shape_geometry",
        "inp": {"shape_mm": {"hollow_cylinder": [50, 200, 47.3, 4.45, 3]}, "submerged": True,
                "mass_kg": 1.0, "rho_kg_m3": _RHO, "cg_mm": [0, 0, 100]},
        "expect": {"envelope_volume_m3": [math.pi * 0.05 ** 2 * 0.2, 5e-3],
                   "buoyancy_N": [_RHO * G * math.pi * 0.05 ** 2 * 0.2, 5e-3]},
    },
    {   # potential flow: sphere in unbounded fluid, added mass = 1/2 rho V (Lamb, Hydrodynamics
        # §92); constant-panel BEM converges from above (~2 % at 3000 panels)
        "name": "sphere_added_mass_infinite_fluid", "kind": "added_mass", "setup": "shape_geometry",
        "inp": {"shape_mm": {"sphere": [0, 0, 0, 500]}, "rho_kg_m3": 1000.0, "fluid": "infinite",
                "panels": 3000},
        "expect": {"A11_kg": [0.5 * 1000 * 4 / 3 * math.pi * 0.5 ** 3, 0.03],
                   "A33_kg": [0.5 * 1000 * 4 / 3 * math.pi * 0.5 ** 3, 0.03]},
    },
    {   # floating hemisphere heave at infinite frequency: phi = 0 on z = 0, the odd image is the
        # translating sphere, so A33 = 1/2 of the sphere's = 0.5 rho (2/3) pi a^3 (Hulme 1982,
        # J. Fluid Mech. 121, mu_33(inf) = 0.5)
        "name": "hemisphere_heave_infinite_frequency", "kind": "added_mass", "setup": "shape_geometry",
        "inp": {"shape_mm": {"sphere": [0, 0, 0, 500]}, "rho_kg_m3": 1000.0, "fluid": "free_surface",
                "waterline_z_mm": 0, "omegas_rad_s": ["inf"], "panels": 3000},
        "expect": {"A33_kg": [0.5 * 1000 * 2 / 3 * math.pi * 0.5 ** 3, 0.03]},
    },
    {   # Kuiper (1992) The Wageningen Propeller Series, Example 1, exact solution as reprinted by
        # Helma (2020) J. Mar. Sci. Eng. 8(3) 211, Table A3: B4-55, P/D 1.004, J 0.7007 ->
        # KT 0.1823, KQ 0.03124, eta0 0.6509 (printed to 4 digits). Thrust = KT rho n^2 D^4 from
        # the published KT; Keller (1966) Ae/A0 min with K = 0.2.
        "name": "wageningen_b4_55_kuiper_example", "kind": "thruster",
        "inp": {"Z": 4, "Ae_A0": 0.55, "P_D": 1.004, "J": 0.7007, "rpm": 600, "diameter_mm": 2000,
                "rho_kg_m3": 1025.0, "shaft_depth_m": 5.0, "vapour_pressure_Pa": 1704.0},
        "expect": {"KT": [0.1823, 5e-4], "KQ": [0.03124, 5e-4], "eta0": [0.6509, 5e-4],
                   "thrust_N": [0.1823 * 1025 * 10 ** 2 * 2 ** 4, 5e-4],
                   "AeA0_min_keller": [(1.3 + 0.3 * 4) * 0.1823 * 1025 * 100 * 16
                                       / ((P_ATM + 1025 * G * 5 - 1704) * 4) + 0.2, 1e-3]},
    },
    {   # long tube (L/D = 40): von Mises -> n = 2 Bresse/Bryan 2E/(1-nu^2) (t/D)^3
        "name": "long_cylinder_bresse", "kind": "pressure_hull",
        "inp": {"shape": "cylinder", "outer_diameter_mm": 101, "thickness_mm": 1, "length_mm": 4000,
                "material": {"E_MPa": _E_ST, "nu": _NU_ST, "yield_MPa": 355}, "pressure_MPa": 0.1,
                "knockdown": 1.0},
        "expect": {"buckling_classical_MPa": [2 * _E_ST / (1 - _NU_ST ** 2) * (1 / 100) ** 3, 0.002],
                   "buckling_lobes": [2, 0]},
    },
    {   # Zoelly sphere x knock-down 0.25: p = kd 2E (t/R)^2 / sqrt(3(1-nu^2)), R mean 97.5 mm
        "name": "sphere_zoelly", "kind": "pressure_hull",
        "inp": {"shape": "sphere", "outer_diameter_mm": 200, "thickness_mm": 5, "knockdown": 0.25,
                "material": {"E_MPa": _E_AL, "nu": _NU_AL, "yield_MPa": 240}, "depth_m": 1000,
                "rho_kg_m3": 1025.0},
        "expect": {"buckling_design_MPa": [0.25 * 2 * _E_AL * (5 / 97.5) ** 2
                                           / math.sqrt(3 * (1 - _NU_AL ** 2)), 1e-9],
                   "pressure_MPa": [1025 * G * 1000 / 1e6, 1e-9]},
    },
    {   # Lame thick cylinder b 100, a 70 mm, external 10 MPa: s_theta(a) = -2 p b^2/(b^2-a^2)
        "name": "lame_thick_cylinder", "kind": "pressure_hull",
        "inp": {"shape": "cylinder", "outer_diameter_mm": 200, "thickness_mm": 30, "length_mm": 300,
                "material": {"E_MPa": _E_ST, "nu": _NU_ST, "yield_MPa": 355}, "pressure_MPa": 10},
        "expect": {"lame_hoop_inner_MPa": [-2 * 10 * 100 ** 2 / (100 ** 2 - 70 ** 2), 1e-9],
                   "lame_hoop_outer_MPa": [-10 * (100 ** 2 + 70 ** 2) / (100 ** 2 - 70 ** 2), 1e-9]},
    },
    {   # O-ring 3.53 mm, no stretch, 2.80 x 4.80 mm gland: squeeze (3.53-2.80)/3.53,
        # fill = (pi 3.53^2/4) / (2.80 x 4.80)  (definitions of Parker ORD 5700 §3.6-3.7)
        "name": "oring_no_stretch", "kind": "oring_gland",
        "inp": {"cs_mm": 3.53, "id_mm": 47.22, "seat_diameter_mm": 47.22, "gland_depth_mm": 2.80,
                "groove_width_mm": 4.80, "service": "static"},
        "expect": {"squeeze_pct": [(3.53 - 2.80) / 3.53 * 100, 1e-9],
                   "gland_fill_pct": [math.pi * 3.53 ** 2 / 4 / (2.80 * 4.80) * 100, 1e-9],
                   "stretch_pct": [0.0, 1e-12]},
    },
    {   # 2 % stretch (seat 48.1644 on ID 47.22): Parker Fig. 3-3 observed curve, 0..3 % branch,
        # Y(2) = -0.005 + 2.38 - 0.76 - 0.008 + 0.128 = 1.735 % cross-section reduction
        "name": "oring_stretch_2pct", "kind": "oring_gland",
        "inp": {"cs_mm": 3.53, "id_mm": 47.22, "seat_diameter_mm": 47.22 * 1.02, "gland_depth_mm": 2.80,
                "groove_width_mm": 4.80, "service": "static"},
        "expect": {"stretch_pct": [2.0, 1e-9], "cs_reduction_pct": [1.735, 1e-9],
                   "cs_effective_mm": [3.53 * (1 - 0.01735), 1e-9]},
    },
]
