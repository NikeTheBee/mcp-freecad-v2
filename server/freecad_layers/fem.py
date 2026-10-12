"""Static structural analysis (FEM) with CalculiX: measured, not guessed.

static() builds a FreeCAD FEM analysis on one solid, then:
- meshes it with Gmsh (2nd-order tetrahedra);
- solves a linear static case with CalculiX (both ship with FreeCAD);
- returns measurements: max displacement, max von Mises stress overall and
  away from the supports, and the safety factor against the material's yield;
- gives an OK / FAIL / UNKNOWN verdict against the limits the caller sets.

Faces are named "Face3", or by a side of the part's bounding box: "xmin",
"xmax", "ymin", "ymax", "zmin" or "zmax" (every planar face lying on that side).
Loads are forces in newtons with a direction vector.

Exactness: a cantilever matches delta = F*L^3 / (3*E*I) within 1 %
(install/fem_test.py).

Scope: linear elastic, small displacements. NOT for buckling, plasticity,
contact or fatigue. The peak stress at a fixed face is usually a mesh
singularity, so the safety factor uses the peak away from the supports, and
both values are reported. `refine=True` solves again on a finer mesh and
reports how much the results moved (convergence).
"""
from __future__ import annotations

import math
import re
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence

import FreeCAD as App
import Part

from . import verify

# E and yield in MPa, rho in kg/m^3. Yield values are typical minimums; printed
# plastics are weaker across layers (Z) than these bulk figures.
MATERIALS: Dict[str, Dict[str, float]] = {
    "steel":     {"E": 210000, "nu": 0.30, "rho": 7850, "yield": 235},   # S235
    "stainless": {"E": 193000, "nu": 0.29, "rho": 8000, "yield": 205},   # 304
    "aluminium": {"E": 69000,  "nu": 0.33, "rho": 2700, "yield": 240},   # 6061-T6 (conservative)
    "pla":       {"E": 3500,   "nu": 0.36, "rho": 1240, "yield": 50},
    "petg":      {"E": 2100,   "nu": 0.40, "rho": 1270, "yield": 45},
    "abs":       {"E": 2200,   "nu": 0.35, "rho": 1040, "yield": 40},
}
MAX_NODES = 150_000
_SIDES = {"xmin": (0, False), "xmax": (0, True), "ymin": (1, False),
          "ymax": (1, True), "zmin": (2, False), "zmax": (2, True)}
_AXES = (App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1))


def _material(material) -> Dict[str, Any]:
    if isinstance(material, dict):
        m = {k: float(material[k]) for k in ("E", "nu") if k in material}
        if set(m) != {"E", "nu"}:
            raise ValueError("custom material needs E (MPa) and nu; optional rho, yield")
        m["rho"] = float(material.get("rho", 0) or 0)
        m["yield"] = float(material["yield"]) if material.get("yield") else None
        m["name"] = str(material.get("name", "custom"))
        return m
    key = str(material).lower().replace("aluminum", "aluminium")
    if key not in MATERIALS:
        raise ValueError(f"unknown material {material!r}; one of {sorted(MATERIALS)} "
                         "or a dict {E, nu, rho, yield}")
    return dict(MATERIALS[key], name=key)


def faces(shape, spec: str) -> List[str]:
    """Face names for "Face3" or a bounding-box side ("xmin" ... "zmax")."""
    spec = spec.strip()
    if re.fullmatch(r"Face\d+", spec):
        if int(spec[4:]) > len(shape.Faces):
            raise ValueError(f"{spec} does not exist ({len(shape.Faces)} faces)")
        return [spec]
    if spec.lower() not in _SIDES:
        raise ValueError(f"face spec {spec!r}: use FaceN or one of {sorted(_SIDES)}")
    axis, is_max = _SIDES[spec.lower()]
    bb = shape.BoundBox
    plane = (bb.XMax, bb.YMax, bb.ZMax)[axis] if is_max else (bb.XMin, bb.YMin, bb.ZMin)[axis]
    tol = max(bb.DiagonalLength * 1e-6, 1e-7)
    out = []
    for i, f in enumerate(shape.Faces, 1):
        if not isinstance(f.Surface, Part.Plane):
            continue
        n = f.normalAt(*_mid_uv(f))
        if abs(abs(n.dot(_AXES[axis])) - 1.0) > 1e-6:
            continue
        if abs(f.CenterOfMass[axis] - plane) <= tol:
            out.append(f"Face{i}")
    if not out:
        raise ValueError(f"no planar face lies on the {spec} side of the part")
    return out


def _mid_uv(face):
    u0, u1, v0, v1 = face.ParameterRange
    return (u0 + u1) / 2.0, (v0 + v1) / 2.0


def _remove_previous(doc, prefix: str) -> None:
    for o in [o for o in doc.Objects if o.Name.startswith(prefix)]:
        try:
            doc.removeObject(o.Name)
        except Exception:
            pass


def _direction_helper(doc, name: str, direction: Sequence[float]):
    v = App.Vector(*map(float, direction))
    if v.Length < 1e-12:
        raise ValueError("load direction must be a non-zero vector")
    v.normalize()
    line = doc.addObject("Part::Feature", name)
    line.Shape = Part.LineSegment(App.Vector(0, 0, 0), v * 10.0).toShape()
    if hasattr(line, "Visibility"):
        line.Visibility = False
    return line, v


def _solve_once(doc, target, fixed_specs, loads, mat, mesh_size: float, prefix: str):
    import ObjectsFem
    from femmesh.gmshtools import GmshTools
    from femtools import ccxtools

    _remove_previous(doc, prefix)
    shape = target.Shape
    analysis = ObjectsFem.makeAnalysis(doc, prefix + "Analysis")
    solver = ObjectsFem.makeSolverCalculiXCcxTools(doc, prefix + "CalculiX")
    solver.AnalysisType = "static"
    solver.GeometricalNonlinearity = "linear"
    material = ObjectsFem.makeMaterialSolid(doc, prefix + "Material")
    md = material.Material
    md.update({"Name": mat["name"], "YoungsModulus": f"{mat['E']} MPa",
               "PoissonRatio": str(mat["nu"]), "Density": f"{mat['rho'] or 1} kg/m^3"})
    material.Material = md
    members = [solver, material]

    fixed_faces: List[str] = []
    for spec in fixed_specs:
        fixed_faces += faces(shape, spec)
    fixed = ObjectsFem.makeConstraintFixed(doc, prefix + "Fixed")
    fixed.References = [(target, sorted(set(fixed_faces), key=lambda s: int(s[4:])))]
    members.append(fixed)

    for i, load in enumerate(loads, 1):
        names: List[str] = []
        for spec in ([load["faces"]] if isinstance(load["faces"], str) else load["faces"]):
            names += faces(shape, spec)
        force = ObjectsFem.makeConstraintForce(doc, f"{prefix}Force{i}")
        force.References = [(target, sorted(set(names), key=lambda s: int(s[4:])))]
        force.Force = f"{float(load['force_N'])} N"
        line, want = _direction_helper(doc, f"{prefix}Dir{i}", load.get("direction", (0, 0, -1)))
        force.Direction = (line, ["Edge1"])
        force.Reversed = False
        doc.recompute()
        got = App.Vector(force.DirectionVector)
        if got.Length > 0 and got.normalize().dot(want) < 0:
            force.Reversed = True
            doc.recompute()
        members.append(force)

    mesh = ObjectsFem.makeMeshGmsh(doc, prefix + "Mesh")
    mesh.Shape = target
    mesh.CharacteristicLengthMax = float(mesh_size)
    mesh.ElementOrder = "2nd"
    members.append(mesh)
    for o in members:
        analysis.addObject(o)
    doc.recompute()

    err = GmshTools(mesh).create_mesh()
    nodes = mesh.FemMesh.NodeCount
    if err or nodes == 0:
        return None, {"error": f"meshing failed: {err or 'no nodes'}"}
    if nodes > MAX_NODES:
        return None, {"error": f"mesh too large ({nodes} nodes > {MAX_NODES}); "
                               f"pass a larger mesh_size_mm than {mesh_size:g}"}

    work = tempfile.mkdtemp(prefix="mcpfc_ccx_")
    try:
        fea = ccxtools.FemToolsCcx(analysis, solver)
        fea.update_objects()
        fea.setup_working_dir(work)
        fea.setup_ccx()
        problem = fea.check_prerequisites()
        if problem:
            return None, {"error": f"CalculiX prerequisites: {problem.strip()}"}
        fea.purge_results()
        fea.write_inp_file()
        fea.ccx_run()
        fea.load_results()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    res = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemResultObject")]
    if not res or not res[0].vonMises:
        return None, {"error": "CalculiX produced no results (see the FreeCAD report)"}
    r = res[0]

    # Stress away from the supports: drop nodes within one element of a fixed face.
    pts = r.Mesh.FemMesh.Nodes
    boxes = []
    for name in fixed.References[0][1]:
        bb = shape.getElement(name).BoundBox
        bb.enlarge(mesh_size)
        boxes.append(bb)
    vm_away, at_away = 0.0, None
    vm_max, at_max = 0.0, None
    for nid, vm in zip(r.NodeNumbers, r.vonMises):
        p = pts[nid]
        if vm > vm_max:
            vm_max, at_max = vm, p
        if vm > vm_away and not any(b.isInside(p) for b in boxes):
            vm_away, at_away = vm, p
    i_d = max(range(len(r.DisplacementLengths)), key=r.DisplacementLengths.__getitem__)
    d_at = pts[r.NodeNumbers[i_d]]
    return r, {
        "max_disp_mm": round(r.DisplacementLengths[i_d], 5),
        "max_disp_at": [round(c, 2) for c in d_at],
        "vm_max_MPa": round(vm_max, 3),
        "vm_max_at": [round(c, 2) for c in at_max] if at_max else None,
        "vm_away_from_supports_MPa": round(vm_away, 3),
        "vm_away_at": [round(c, 2) for c in at_away] if at_away else None,
        "nodes": nodes,
        "elements": mesh.FemMesh.VolumeCount,
        "mesh_size_mm": round(float(mesh_size), 4),
        "fixed_faces": fixed.References[0][1],
    }


def default_mesh_size(shape) -> float:
    bb = shape.BoundBox
    dims = sorted(d for d in (bb.XLength, bb.YLength, bb.ZLength) if d > 1e-9)
    return max(dims[0] / 4.0, bb.DiagonalLength / 80.0)


def static(target, fixed: Sequence[str], loads: Sequence[Dict[str, Any]],
           material="steel", mesh_size_mm: Optional[float] = None,
           min_safety_factor: float = 2.0, max_displacement_mm: Optional[float] = None,
           refine: bool = False) -> Dict[str, Any]:
    """Linear static FEM of one solid. Returns {status, issues, advisories, measures}.

    fixed: face specs held in place, e.g. ["xmin"].
    loads: [{"faces": "xmax" | [...], "force_N": 100, "direction": [0, 0, -1]}]
           (total force spread over the faces).
    material: a name in MATERIALS or {"E": MPa, "nu": ..., "rho": kg/m3, "yield": MPa}.
    """
    t0 = time.time()
    issues: List[str] = []
    advisories: List[str] = []
    obj = verify._resolve(target)
    doc = obj.Document
    chk = verify.check(obj)
    if chk["status"] != verify.OK or not chk.get("closed"):
        return {"status": verify.UNKNOWN, "measures": {},
                "issues": [f"not a valid closed solid: {chk['issues']}"], "advisories": []}
    if not fixed:
        raise ValueError("at least one fixed face is needed (an unsupported part has no static solution)")
    if not loads:
        raise ValueError("at least one load is needed")
    mat = _material(material)
    size = float(mesh_size_mm or default_mesh_size(obj.Shape))
    prefix = f"FEM_{obj.Name}_"
    _, m = _solve_once(doc, obj, list(fixed), list(loads), mat, size, prefix)
    if "error" in m:
        return {"status": verify.UNKNOWN, "measures": {"material": mat["name"]},
                "issues": [m["error"]], "advisories": []}
    m["material"] = mat["name"]
    m["yield_MPa"] = mat["yield"]

    if refine:
        _, m2 = _solve_once(doc, obj, list(fixed), list(loads), mat, size / 1.6, prefix)
        if "error" in m2:
            advisories.append(f"refined solve failed ({m2['error']}); convergence unknown")
        else:
            dd = abs(m2["max_disp_mm"] - m["max_disp_mm"]) / max(m2["max_disp_mm"], 1e-12)
            ds = (abs(m2["vm_away_from_supports_MPa"] - m["vm_away_from_supports_MPa"])
                  / max(m2["vm_away_from_supports_MPa"], 1e-12))
            m2["convergence"] = {"disp_change_pct": round(dd * 100, 2),
                                 "stress_change_pct": round(ds * 100, 2),
                                 "coarse_mesh_mm": m["mesh_size_mm"]}
            m2["material"], m2["yield_MPa"] = m["material"], m["yield_MPa"]
            m = m2
            if dd > 0.05 or ds > 0.10:
                advisories.append("not converged: results moved "
                                  f"{m['convergence']['disp_change_pct']} % (displacement) / "
                                  f"{m['convergence']['stress_change_pct']} % (stress) on a finer mesh")

    vm_ref = m["vm_away_from_supports_MPa"]
    if mat["yield"]:
        sf = mat["yield"] / vm_ref if vm_ref > 0 else math.inf
        m["safety_factor"] = round(sf, 2) if sf != math.inf else None
        if sf < min_safety_factor:
            issues.append(f"safety factor {sf:.2f} < {min_safety_factor} "
                          f"(von Mises {vm_ref} MPa vs yield {mat['yield']} MPa, {mat['name']})")
    else:
        advisories.append("no yield strength given: safety factor not computed")
    if max_displacement_mm is not None and m["max_disp_mm"] > max_displacement_mm:
        issues.append(f"max displacement {m['max_disp_mm']} mm > {max_displacement_mm} mm")
    if m["vm_max_MPa"] > 1.5 * vm_ref:
        advisories.append(f"peak {m['vm_max_MPa']} MPa sits at a support: usually a mesh "
                          "singularity of the ideal fixed face; model the real fixture if it matters")
    if mat["name"] in ("pla", "petg", "abs"):
        advisories.append("3D-printed part: layers are weaker across Z than these bulk values")
    m["solve_s"] = round(time.time() - t0, 1)
    return {"status": verify.FAIL if issues else verify.OK, "issues": issues,
            "advisories": advisories, "measures": m}


def summary(result: Dict[str, Any]) -> str:
    """One compact paragraph for the AI."""
    m = result.get("measures", {})
    head = f"{result['status']} FEM static"
    if "max_disp_mm" not in m:
        return f"{head}: " + "; ".join(result.get("issues", []))
    txt = (f"{head} ({m.get('material')}, {m['nodes']} nodes, mesh {m['mesh_size_mm']} mm, "
           f"{m.get('solve_s')} s): max displacement {m['max_disp_mm']} mm at {m['max_disp_at']}; "
           f"von Mises {m['vm_away_from_supports_MPa']} MPa away from supports at {m['vm_away_at']} "
           f"(peak {m['vm_max_MPa']} MPa); safety factor {m.get('safety_factor')}")
    if "convergence" in m:
        c = m["convergence"]
        txt += f"; refined: disp {c['disp_change_pct']} %, stress {c['stress_change_pct']} %"
    if result.get("issues"):
        txt += " | FAIL: " + "; ".join(result["issues"])
    if result.get("advisories"):
        txt += " | note: " + "; ".join(result["advisories"])
    return txt
