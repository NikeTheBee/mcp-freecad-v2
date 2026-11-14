"""Custom steady PDEs with NGSolve (env `pde`): any scalar field obeying
    -div(k grad u) + c u = f     in the solid
    u = g on Dirichlet faces;  k du/dn = q on flux faces;  k du/dn = h (u_inf - u) on Robin faces
which covers steady heat conduction, electrostatics (u = potential, k = permittivity), DC current
flow (k = conductivity), groundwater/Darcy pressure, potential flow, membrane deflection...

Units are the caller's, consistent: geometry in mm (as exported by FreeCAD) unless `scale` says
otherwise (scale = 0.001 converts mm to m before solving). Faces: "xmin".."zmax" (planar faces on
that side of the bounding box) or "FaceN" (FreeCAD numbering, matched by centre and area sent by
the server). Probes: points in the same units as the geometry.

Exactness: second-order elements reproduce quadratic solutions exactly (REFERENCES).
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List

from _common import geometry_path, result

ENV = "pde"
KINDS = {
    "pde_diffusion": {"geometry": True, "timeout": 1200,
                      "doc": "steady -div(k grad u) + c u = f: heat, electrostatics, DC current, Darcy..."},
}
_SIDES = {"xmin": (0, False), "xmax": (0, True), "ymin": (1, False), "ymax": (1, True),
          "zmin": (2, False), "zmax": (2, True)}
MAX_DOFS = 3_000_000


def versions() -> Dict[str, str]:
    import ngsolve
    import netgen
    return {"ngsolve": ngsolve.__version__, "netgen": getattr(netgen, "__version__", "?")}


def _name_faces(shape, specs: List[str], face_info: Dict[str, Any]) -> None:
    faces = list(shape.faces)
    lo, hi = shape.bounding_box
    diag = math.dist(tuple(lo), tuple(hi))
    tol = 1e-6 * diag + 1e-9
    for spec in specs:
        s = spec.strip()
        hit = []
        if s.lower() in _SIDES:
            ax, is_max = _SIDES[s.lower()]
            plane = tuple(hi)[ax] if is_max else tuple(lo)[ax]
            for f in faces:
                a, b = f.bounding_box
                if abs(tuple(a)[ax] - plane) < tol and abs(tuple(b)[ax] - plane) < tol:
                    hit.append(f)
        elif re.fullmatch(r"Face\d+", s):
            info = face_info.get(s)
            if not info:
                raise ValueError(f"{s}: no centre/area sent for it (the server resolves FaceN)")
            for f in faces:
                c = tuple(f.center)
                if math.dist(c, info["com"]) < 1e-3 * diag and abs(f.mass - info["area"]) < 1e-3 * info["area"]:
                    hit.append(f)
        else:
            raise ValueError(f"face {spec!r}: FaceN or one of {sorted(_SIDES)}")
        if not hit:
            raise ValueError(f"face {spec!r} matches no face of the solid")
        for f in hit:
            f.name = s


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    from netgen.occ import OCCGeometry
    import ngsolve as ng
    geom = geometry_path(inp, work)
    shape = OCCGeometry(str(geom)).shape
    scale = float(inp.get("scale", 1.0))
    if scale != 1.0:
        shape = shape.Scale((0, 0, 0), scale)
    dirichlet = inp.get("dirichlet") or {}
    flux = inp.get("flux") or {}
    robin = inp.get("robin") or {}
    specs = list(dict.fromkeys([*dirichlet, *flux, *robin, *(inp.get("report_flux") or [])]))
    _name_faces(shape, specs, {k: {"com": [c * scale for c in v["com"]], "area": v["area"] * scale ** 2}
                               for k, v in (inp.get("face_info") or {}).items()})
    lo, hi = shape.bounding_box
    size = float(inp.get("mesh_size", 0) or 0) or max(min(hi[i] - lo[i] for i in range(3)) / 3,
                                                       max(hi[i] - lo[i] for i in range(3)) / 25)
    mesh = ng.Mesh(OCCGeometry(shape).GenerateMesh(maxh=size))
    order = int(inp.get("order", 2))
    fes = ng.H1(mesh, order=order, dirichlet="|".join(dirichlet) or "")
    if fes.ndof > MAX_DOFS:
        raise ValueError(f"{fes.ndof} unknowns > {MAX_DOFS}: pass a larger mesh_size")
    u, v = fes.TnT()
    k = ng.CoefficientFunction(float(inp.get("k", 1.0)))
    c = float(inp.get("c", 0.0))
    a = ng.BilinearForm(fes, symmetric=True)
    a += k * ng.grad(u) * ng.grad(v) * ng.dx
    if c:
        a += c * u * v * ng.dx
    f = ng.LinearForm(fes)
    if inp.get("source"):
        f += float(inp["source"]) * v * ng.dx
    for face, q in flux.items():
        f += float(q) * v * ng.ds(face)
    for face, (h, uinf) in robin.items():
        a += float(h) * u * v * ng.ds(face)
        f += float(h) * float(uinf) * v * ng.ds(face)
    a.Assemble()
    f.Assemble()
    g = ng.GridFunction(fes)
    if dirichlet:   # ONE Set: each Set call resets the dofs outside its own region
        g.Set(mesh.BoundaryCF({f: float(v) for f, v in dirichlet.items()}, default=0), ng.BND)
    r = f.vec.CreateVector()
    r.data = f.vec - a.mat * g.vec
    g.vec.data += a.mat.Inverse(fes.FreeDofs(), inverse="sparsecholesky") * r
    vals = list(g.vec)[:mesh.nv]          # H1: the first dofs are the vertex values
    values: Dict[str, Any] = {"dofs": fes.ndof, "elements": mesh.ne, "mesh_size": size,
                              "u_min": float(min(vals)), "u_max": float(max(vals))}
    for i, p in enumerate(inp.get("probes") or []):
        pt = [x * scale for x in p]
        values[f"u_probe{i}"] = float(g(mesh(*pt)))
    n = ng.specialcf.normal(3)
    for face in inp.get("report_flux") or []:
        # outward flux of -k grad u through the face (heat leaving the solid is positive)
        q = ng.Integrate(-k * ng.BoundaryFromVolumeCF(ng.grad(g)) * n * ng.ds(face), mesh)
        values[f"flux_out_{face}"] = float(q)
    head = f"pde_diffusion: u {values['u_min']:.5g}..{values['u_max']:.5g} ({fes.ndof} dofs, order {order})"
    files = []
    try:
        vtk = ng.VTKOutput(mesh, coefs=[g], names=["u"], filename=str(work / "result"), subdivision=0)
        vtk.Do()
        files.append("result.vtu")
    except Exception:  # noqa: BLE001
        pass
    return result(values, inp.get("limits"), head, files)


# ------------------------------------------------------------------ reference cases
def cube(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    from netgen.occ import Box, Pnt
    Box(Pnt(0, 0, 0), Pnt(1, 1, 1)).WriteStep(str(work / "geometry.step"))
    inp["geometry"] = "geometry.step"
    return inp


REFERENCES = [
    {   # manufactured solution u = x(1-x): -u'' = 2, u(0) = u(1) = 0, max 1/4 at x = 1/2
        "name": "quadratic_exact", "kind": "pde_diffusion", "setup": "cube",
        "inp": {"k": 1, "source": 2, "dirichlet": {"xmin": 0, "xmax": 0}, "probes": [[0.5, 0.5, 0.5]],
                "mesh_size": 0.3},
        "expect": {"u_probe0": [0.25, 1e-6], "u_max": [0.25, 1e-6]},
    },
    {   # linear conduction, k = 2, u: 0 -> 1 over L = 1, area 1: flux out of xmin = -k A du/dx... = 2
        "name": "linear_flux", "kind": "pde_diffusion", "setup": "cube",
        "inp": {"k": 2, "dirichlet": {"xmin": 1, "xmax": 0}, "report_flux": ["xmax"], "mesh_size": 0.3},
        "expect": {"flux_out_xmax": [2.0, 1e-6]},
    },
    {   # conduction + convection in series: T(1) = 100 - q L/k, q = 100 / (L/k + 1/h), k=1, h=10
        "name": "robin_series", "kind": "pde_diffusion", "setup": "cube",
        "inp": {"k": 1, "dirichlet": {"xmin": 100}, "robin": {"xmax": [10, 0]},
                "probes": [[1.0, 0.5, 0.5]], "mesh_size": 0.3},
        "expect": {"u_probe0": [100 - (100 / 1.1), 1e-6]},
    },
]
