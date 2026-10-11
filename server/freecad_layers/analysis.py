"""Part analysis — measured facts about a part (holes, walls, overhangs).

Deterministic measurements the AI compares with what the user asked for,
instead of trusting its own reading of the model (audit 2026-10-11 §9:
maker-agent / CADSmith). Used by spec.py (design contract) and for
3D-printability checks.

- holes(): concave, full-turn cylindrical surfaces only. A fillet, a boss or
  the outside of a round bar is NOT a hole (the old CAM layer drilled them).
- min_wall(): sampled ray-casting through the solid (an estimate, labelled so).
- overhangs(): downward-facing area beyond a printing angle.
- printability(): one verdict for FDM printing (watertight, bed size, walls,
  overhangs).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

import FreeCAD as App
import Part

from . import verify

_TWO_PI = 2.0 * math.pi


def _shape(obj_or_shape):
    if hasattr(obj_or_shape, "Shape"):
        return obj_or_shape.Shape
    if isinstance(obj_or_shape, str):
        return verify._resolve(obj_or_shape).Shape
    return obj_or_shape


def _is_concave(face) -> Optional[bool]:
    """True if the cylindrical face's material is OUTSIDE the cylinder (a bore)."""
    surf = face.Surface
    u0, u1, v0, v1 = face.ParameterRange
    u, v = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    p = face.valueAt(u, v)
    n = face.normalAt(u, v)            # outward normal of the solid, orientation-aware
    axis = surf.Axis
    c = surf.Center
    w = p - c
    radial = w - axis * w.dot(axis)
    if radial.Length < 1e-9:
        return None
    return n.dot(radial) < 0           # normal points to the axis -> bore


def holes(obj_or_shape, tol: float = 1e-4) -> List[Dict[str, Any]]:
    """Cylindrical holes: [{diameter, axis, center, depth, through}], sorted.

    Faces sharing the same axis line and radius are merged (OCCT often splits
    a bore in two half-cylinders); only groups covering a full turn count.
    `center` is the axis point at the middle of the hole depth.
    """
    shp = _shape(obj_or_shape)
    groups: List[Dict[str, Any]] = []
    for f in shp.Faces:
        if f.Surface.TypeId != "Part::GeomCylinder" or not _is_concave(f):
            continue
        s = f.Surface
        axis = App.Vector(s.Axis).normalize()
        u0, u1, v0, v1 = f.ParameterRange
        for g in groups:
            same_r = abs(g["radius"] - s.Radius) < tol
            parallel = abs(abs(g["axis"].dot(axis)) - 1.0) < 1e-6
            d = s.Center - g["origin"]
            on_line = (d - g["axis"] * d.dot(g["axis"])).Length < tol
            if same_r and parallel and on_line:
                g["span"] += (u1 - u0)
                g["faces"].append(f)
                break
        else:
            groups.append({"radius": s.Radius, "axis": axis, "origin": App.Vector(s.Center),
                           "span": (u1 - u0), "faces": [f]})
    out = []
    bb = shp.BoundBox
    for g in groups:
        if g["span"] < _TWO_PI - 1e-3:
            continue                    # partial arc: concave fillet / slot end
        a, o = g["axis"], g["origin"]
        ts = [(v.Point - o).dot(a) for f in g["faces"] for v in f.Vertexes]
        if not ts:
            continue
        t0, t1 = min(ts), max(ts)
        mid = o + a * ((t0 + t1) / 2.0)
        # through-hole: the bore spans the part's full extent along its axis
        corners = [App.Vector(x, y, z) for x in (bb.XMin, bb.XMax)
                   for y in (bb.YMin, bb.YMax) for z in (bb.ZMin, bb.ZMax)]
        ext = [(c - o).dot(a) for c in corners]
        through = (t1 - t0) >= (max(ext) - min(ext)) - 1e-3
        out.append({"diameter": round(2 * g["radius"], 4),
                    "axis": [round(x, 6) for x in (a.x, a.y, a.z)],
                    "center": [round(x, 4) for x in (mid.x, mid.y, mid.z)],
                    "depth": round(t1 - t0, 4), "through": through})
    return sorted(out, key=lambda h: (h["diameter"], h["center"]))


def _face_samples(face, n: int):
    u0, u1, v0, v1 = face.ParameterRange
    for i in range(n):
        for j in range(n):
            u = u0 + (u1 - u0) * (i + 0.5) / n
            v = v0 + (v1 - v0) * (j + 0.5) / n
            p = face.valueAt(u, v)
            if face.isInside(p, 1e-6, True):
                yield p, face.normalAt(u, v)


def min_wall(obj_or_shape, per_face: int = 3, max_samples: int = 600) -> Dict[str, Any]:
    """Estimated minimum wall thickness by inward ray-casting (sampled).

    Returns {min_mm, at, samples, method}. It is a lower-bound-ish ESTIMATE:
    thin spots between sample points can be missed — say so to the user.
    """
    shp = _shape(obj_or_shape)
    solids = shp.Solids
    if not solids:
        return {"min_mm": None, "samples": 0, "method": "no solid"}
    diag = shp.BoundBox.DiagonalLength
    best, where, count = math.inf, None, 0
    for solid in solids:
        for face in solid.Faces:
            for p, n in _face_samples(face, per_face):
                if count >= max_samples:
                    break
                count += 1
                start = p - n * 1e-4
                seg = Part.LineSegment(start, p - n * diag).toShape()
                inside = solid.common(seg)
                for e in inside.Edges:
                    a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
                    if min((a - start).Length, (b - start).Length) < 1e-2:
                        t = e.Length + 1e-4
                        if t < best:
                            best, where = t, p
    return {"min_mm": None if best is math.inf else round(best, 4),
            "at": None if where is None else [round(x, 3) for x in (where.x, where.y, where.z)],
            "samples": count, "method": f"sampled ray-cast ({per_face}x{per_face}/face)"}


def overhangs(obj_or_shape, max_angle_deg: float = 45.0,
              build_dir: Sequence[float] = (0, 0, 1)) -> Dict[str, Any]:
    """Downward-facing area steeper than `max_angle_deg` from vertical.

    A face overhangs when its normal is within (90 - max_angle) degrees of
    straight down. Faces lying on the build plate are ignored.
    """
    shp = _shape(obj_or_shape)
    up = App.Vector(*build_dir).normalize()
    limit = math.cos(math.radians(90.0 - max_angle_deg))
    z_min = min(v.Point.dot(up) for v in shp.Vertexes) if shp.Vertexes else 0.0
    area, faces = 0.0, []
    for i, f in enumerate(shp.Faces):
        u0, u1, v0, v1 = f.ParameterRange
        n = f.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
        if -n.dot(up) <= limit:
            continue
        lowest = min(v.Point.dot(up) for v in f.Vertexes) if f.Vertexes else z_min
        highest = max(v.Point.dot(up) for v in f.Vertexes) if f.Vertexes else z_min
        if highest - z_min < 1e-6 and lowest - z_min < 1e-6:
            continue                    # on the bed
        area += f.Area
        faces.append(f"Face{i + 1}")
    return {"area_mm2": round(area, 2), "faces": faces, "max_angle_deg": max_angle_deg}


def printability(obj_or_name, bed_mm: Sequence[float] = (220, 220, 250),
                 min_wall_mm: float = 0.8, max_overhang_deg: float = 45.0,
                 max_overhang_area_mm2: float = 0.0) -> Dict[str, Any]:
    """FDM printability verdict: watertight solid, fits the bed (any 90° turn
    on the plate), walls >= nozzle-friendly minimum, overhang area.

    Returns {status, issues, advisories, measures}. Overhangs are FAIL only if
    their area exceeds `max_overhang_area_mm2` (supports are often fine).
    """
    r = verify.check(obj_or_name)
    issues = [f"verify: {i}" for i in r["issues"]]
    advisories: List[str] = []
    measures: Dict[str, Any] = {"bbox": r.get("bbox")}
    if r["status"] != verify.OK or not r["closed"]:
        issues.append("not a watertight valid solid")
    shp = _shape(obj_or_name)
    if r.get("bbox"):
        x, y, z = r["bbox"]
        bx, by, bz = bed_mm
        if not ((x <= bx and y <= by) or (y <= bx and x <= by)) or z > bz:
            issues.append(f"does not fit the {bx}x{by}x{bz} mm build volume")
    w = min_wall(shp)
    measures["min_wall"] = w
    if w["min_mm"] is not None and w["min_mm"] < min_wall_mm:
        issues.append(f"wall {w['min_mm']} mm < {min_wall_mm} mm at {w['at']} (estimate)")
    ov = overhangs(shp, max_overhang_deg)
    measures["overhang_area_mm2"] = ov["area_mm2"]
    if ov["area_mm2"] > max_overhang_area_mm2:
        msg = f"{ov['area_mm2']} mm2 of overhang beyond {max_overhang_deg} deg (supports needed)"
        (issues if max_overhang_area_mm2 > 0 else advisories).append(msg)
    status = verify.FAIL if issues else verify.OK
    return {"status": status, "issues": issues, "advisories": advisories,
            "measures": measures}


def report(obj_or_name) -> str:
    """One compact paragraph: verdict + holes + estimated min wall."""
    v = verify.verdict(obj_or_name)
    hs = holes(obj_or_name)
    w = min_wall(obj_or_name)
    hole_txt = ", ".join(f"D{h['diameter']}{'' if h['through'] else ' blind'} @ {h['center']}"
                         for h in hs[:10]) or "none"
    if len(hs) > 10:
        hole_txt += f", ... ({len(hs)} total)"
    return f"{v}\nholes ({len(hs)}): {hole_txt}\nmin wall ~{w['min_mm']} mm ({w['method']})"
