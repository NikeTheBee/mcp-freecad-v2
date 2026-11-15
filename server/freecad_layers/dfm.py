"""Design for manufacturing checks (FEUILLE_DE_ROUTE_ATELIER.md §7.1): measured, per process.

- cnc (3-axis milling along `axis`, end mill of diameter `tool_d`):
  * sharp concave edges parallel to the tool axis: a round tool cannot cut them (it leaves a
    radius = tool_d / 2): FAIL, with where they are;
  * concave cylindrical faces (internal fillets) parallel to the axis with a radius under the
    tool's: FAIL;
  * faces facing away from the tool (normal . axis < 0, not vertical): need a second set-up
    or a 5-axis machine: WARN, with how many.
- mold (pull direction `axis`, minimum draft `draft_deg`): planar side faces whose draft is
  under the minimum: FAIL with the faces.

Convexity of an edge: for the faces F1, F2 meeting there with outward normals n1, n2 and d1 the
direction from the edge into F1 (in its plane), the edge is concave when d1 . n2 > 0 (F1 rises
along the outside of F2). Sheet metal (bend radius, hole-to-edge) needs the SheetMetal
workbench: not checked here. 3D printing: `printability`.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

import FreeCAD as App
import Part

from . import verify

_AX = {"x": App.Vector(1, 0, 0), "y": App.Vector(0, 1, 0), "z": App.Vector(0, 0, 1),
       "-x": App.Vector(-1, 0, 0), "-y": App.Vector(0, -1, 0), "-z": App.Vector(0, 0, -1)}


def _normal(face, p):
    """Outward normal of the face at p (normalAt follows the face's orientation)."""
    u, v = face.Surface.parameter(p)
    return face.normalAt(u, v)


def _into_face(face, edge, p, t, n, eps):
    d = n.cross(t)
    d.normalize()
    for cand in (d, -d):
        q = p + cand * eps
        if face.distToShape(Part.Vertex(q))[0] < eps * 0.05:
            return cand
    return None


def _concave(shape, edge) -> bool | None:
    faces = shape.ancestorsOfType(edge, Part.Face)
    if len(faces) != 2:
        return None
    f1, f2 = faces
    p = edge.valueAt((edge.FirstParameter + edge.LastParameter) / 2)
    t = edge.tangentAt((edge.FirstParameter + edge.LastParameter) / 2)
    n1, n2 = _normal(f1, p), _normal(f2, p)
    if abs(n1.dot(n2)) > 1 - 1e-9:
        return False                                    # tangent faces: no corner
    eps = max(edge.Length * 1e-3, 1e-4)
    d1 = _into_face(f1, edge, p, t, n1, eps)
    if d1 is None:
        return None
    return d1.dot(n2) > 1e-9


def _flat_normal(f):
    """Outward normal of a face that is geometrically flat (any surface type: a loft makes flat
    B-spline faces), else None."""
    if not isinstance(f.Surface, Part.Plane):
        try:
            if f.findPlane() is None:
                return None
        except Exception:  # noqa: BLE001
            return None
    u0, u1, v0, v1 = f.ParameterRange
    return f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)


def check(target, process: str = "cnc", axis: str = "z", tool_d: float = 6.0,
          draft_deg: float = 1.0) -> Dict[str, Any]:
    o = verify._resolve(target)
    shp = verify.global_shape(o)
    if shp is None or not shp.Solids:
        raise ValueError(f"{o.Name} has no solid")
    if axis not in _AX:
        raise ValueError(f"axis: one of {', '.join(_AX)}")
    a = _AX[axis]
    issues: List[str] = []
    warns: List[str] = []
    if process == "cnc":
        r_tool = tool_d / 2
        sharp = []
        for e in shp.Edges:
            if not isinstance(e.Curve, Part.Line):
                continue
            t = e.tangentAt(e.FirstParameter)
            if abs(abs(t.dot(a)) - 1) > 1e-6:
                continue
            if _concave(shp, e):
                c = e.CenterOfMass
                sharp.append(f"({c.x:.4g}, {c.y:.4g}, {c.z:.4g})")
        if sharp:
            issues.append(f"{len(sharp)} sharp inside corner(s) along {axis}: a D{tool_d:g} mill leaves "
                          f"R{r_tool:g} there; add a fillet >= R{r_tool:g} (at {', '.join(sharp[:6])})")
        small = []
        for i, f in enumerate(shp.Faces, 1):
            s = f.Surface
            if isinstance(s, Part.Cylinder) and abs(abs(s.Axis.dot(a)) - 1) < 1e-6 and s.Radius < r_tool - 1e-9:
                u0, u1, v0, v1 = f.ParameterRange
                p = f.valueAt((u0 + u1) / 2, (v0 + v1) / 2)
                n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
                radial = p - s.Center - s.Axis * (p - s.Center).dot(s.Axis)
                if radial.Length > 0 and n.dot(radial) < 0:       # normal toward the axis: concave
                    small.append(f"Face{i} R{s.Radius:g}")
        if small:
            issues.append(f"internal radius under the tool's R{r_tool:g}: {', '.join(small[:8])}")
        ns = [(i, _flat_normal(f)) for i, f in enumerate(shp.Faces, 1)]
        under = [i for i, n in ns if n is not None and n.dot(a) < -1e-6 and abs(n.dot(a)) < 1 - 1e-9]
        down = [i for i, n in ns if n is not None and n.dot(a) < -1 + 1e-9]
        if under:
            warns.append(f"{len(under)} face(s) lean away from the tool ({', '.join(f'Face{i}' for i in under[:6])}): "
                         "second set-up or 5-axis")
        if len(down) > 1:
            warns.append(f"{len(down)} faces facing -{axis.lstrip('-')}: they need a flip (second set-up)")
    elif process == "mold":
        low = []
        for i, f in enumerate(shp.Faces, 1):
            n = _flat_normal(f)
            if n is None:
                continue
            draft = 90 - math.degrees(math.acos(min(1.0, abs(n.dot(a)))))
            if draft < draft_deg - 1e-9 and abs(n.dot(a)) < 1 - 1e-9:
                low.append(f"Face{i} {draft:.2f} deg")
        if low:
            issues.append(f"{len(low)} side face(s) under {draft_deg:g} deg of draft for a pull along "
                          f"{axis}: {', '.join(low[:8])}")
    else:
        raise ValueError("process: cnc | mold (3D printing: printability)")
    status = "FAIL" if issues else ("WARN" if warns else "OK")
    return {"status": status, "issues": issues, "warnings": warns, "process": process}


def summary(r: Dict[str, Any]) -> str:
    return (f"{r['status']} DFM {r['process']}" + "".join(f" | {x}" for x in r["issues"] + r["warnings"])
            + ("" if r["issues"] or r["warnings"] else ": no issue found by these checks"))
