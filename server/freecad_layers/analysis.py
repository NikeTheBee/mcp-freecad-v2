"""Part analysis — measured facts about a part (holes, walls, overhangs).

Deterministic measurements the AI compares with what the user asked for,
instead of trusting its own reading of the model (audit 2026-10-11 §9:
maker-agent / CADSmith). Used by spec.py (design contract) and for
3D-printability checks.

- holes(): concave, full-turn cylindrical surfaces only. A fillet, a boss or
  the outside of a round bar is NOT a hole (the old CAM layer drilled them).
  A bore stored as a B-spline (a STEP from another CAD, toNurbs) counts too:
  its points and normals are fitted to a cylinder.
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


# a freeform face can be a cylinder in disguise: a STEP from another CAD
_FREEFORM = ("Part::GeomBSplineSurface", "Part::GeomBezierSurface",
             "Part::GeomSurfaceOfExtrusion")


def _fitted_bore(f, tol: float) -> Optional[tuple]:
    """(center, axis, radius, angular span, tolerance) when a freeform face is
    the inside of a cylinder: its normals all square to one axis, its points on
    one circle around it, the normals pointing to that axis. None otherwise."""
    import numpy as np

    u0, u1, v0, v1 = f.ParameterRange
    pts, nrm = [], []
    for i in range(5):
        for j in range(5):
            u, v = u0 + (u1 - u0) * (i + 0.5) / 5, v0 + (v1 - v0) * (j + 0.5) / 5
            n = f.normalAt(u, v)
            if n.Length > 1e-12:
                p = f.valueAt(u, v)
                pts.append((p.x, p.y, p.z))
                nrm.append(tuple(n.normalize()))
    if len(pts) < 9:
        return None
    P, N = np.array(pts), np.array(nrm)
    w, vec = np.linalg.eigh(N.T @ N)
    if w[0] > 1e-6 * len(N) or w[1] < 1e-3 * len(N):
        return None                     # normals not all square to one axis, or a plane
    a = vec[:, 0]
    e1 = np.cross(a, (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0))
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(a, e1)
    x, y = P @ e1, P @ e2
    (d, e, g), *_ = np.linalg.lstsq(np.c_[x, y, np.ones_like(x)], -(x * x + y * y), rcond=None)
    cx, cy = -d / 2.0, -e / 2.0
    r2 = cx * cx + cy * cy - g
    if r2 <= 0:
        return None
    r = math.sqrt(r2)
    fit_tol = max(tol, 2e-3 * r)
    rad = np.c_[x - cx, y - cy]
    dist = np.hypot(rad[:, 0], rad[:, 1])
    if np.max(np.abs(dist - r)) > fit_tol or np.min(dist) < 1e-9:
        return None
    toward = np.einsum("ij,ij->i", np.c_[N @ e1, N @ e2], rad / dist[:, None])
    if np.max(toward) > -0.99:
        return None                     # a boss, or not radial: not a bore
    t = float(np.mean(P @ a))
    center = App.Vector(*(cx * e1 + cy * e2 + t * a))
    axis = App.Vector(*a)
    # angular span: the angles the face reaches (edges + samples); a gap counts
    # as uncovered only when a point in its middle is not on the face
    ring = [(q.x, q.y, q.z) for ed in f.Edges for q in ed.discretize(24)] + pts
    R = np.array(ring) - (cx * e1 + cy * e2)
    ang = np.sort(np.arctan2(R @ e2, R @ e1))
    gaps = np.diff(np.r_[ang, ang[0] + _TWO_PI])
    span = _TWO_PI
    for k in np.nonzero(gaps > 1e-3)[0]:
        mid = ang[k] + gaps[k] / 2.0
        q = App.Vector(*((cx + r * math.cos(mid)) * e1 + (cy + r * math.sin(mid)) * e2 + t * a))
        if not f.isInside(q, max(10 * fit_tol, 1e-3), True):
            span -= float(gaps[k])
    return center, axis, r, span, fit_tol


def _bore(f, tol: float) -> Optional[tuple]:
    """(center, axis, radius, angular span, tolerance) of a face that is the
    inside of a cylinder, None for any other face."""
    s = f.Surface
    if s.TypeId == "Part::GeomCylinder":
        if not _is_concave(f):
            return None
        u0, u1, _v0, _v1 = f.ParameterRange
        return App.Vector(s.Center), App.Vector(s.Axis).normalize(), s.Radius, u1 - u0, tol
    if s.TypeId in _FREEFORM:
        try:
            return _fitted_bore(f, tol)
        except Exception:  # noqa: BLE001 — an unreadable face is simply not a bore
            return None
    return None


def holes(obj_or_shape, tol: float = 1e-4) -> List[Dict[str, Any]]:
    """Cylindrical holes: [{diameter, axis, center, depth, through}], sorted.

    Faces sharing the same axis line and radius are merged (OCCT often splits
    a bore in two half-cylinders) while their stretches along the axis meet: each
    entry is one bore; only bores covering a full turn count.
    `center` is the axis point at the middle of the hole depth. `hole` numbers
    the holes: the bores of one stepped hole (a counterbore) share it.
    Several solids (an assembly): each is measured on its own, so "through"
    means through THAT part (against the whole assembly, a through hole in a
    plate read "blind" as soon as a pin stood in it).
    """
    shp = _shape(obj_or_shape)
    solids = shp.Solids
    if len(solids) > 1:
        out, first = [], 1
        for s in solids:                    # a hole never spans two parts
            hs = _holes_of(s, tol)
            first = _number_holes(hs, s, first)
            out += hs
        return sorted(out, key=lambda h: (h["diameter"], h["center"]))
    out = _holes_of(shp, tol)
    _number_holes(out, solids[0] if solids else None)
    return out


def _number_holes(hs: List[Dict[str, Any]], solid=None, first: int = 1, tol: float = 1e-2) -> int:
    """Give each bore its hole number ("hole"). Bores of one axis line are ONE hole
    when they meet end to end (a counterbore and its bore, a stepped hole) or face
    each other across air only (a pin hole through both flanges of a clevis, a hole
    drilled across a bore): one drill goes through. Material between them (two
    blind holes from opposite sides) keeps them two holes. A plate with 4
    counterbored holes read 8 holes, so a "4 holes" contract failed on the right
    part. Returns the next free number."""
    parent = list(range(len(hs)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    vec = [(App.Vector(*h["center"]), App.Vector(*h["axis"])) for h in hs]
    cells: Dict[tuple, List[int]] = {}
    for i, (c, a) in enumerate(vec):
        foot = c - a * c.dot(a)             # same for +axis and -axis
        key = tuple(math.floor(x) for x in (foot.x, foot.y, foot.z))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in cells.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                        cj, aj = vec[j]
                        if root(i) == root(j) or abs(abs(a.dot(aj)) - 1.0) > 1e-5:
                            continue
                        d = cj - c
                        if (d - a * d.dot(a)).Length > tol:
                            continue
                        if _air_between(c, cj, a, hs[i]["depth"], hs[j]["depth"], solid, tol):
                            parent[root(j)] = root(i)
        cells.setdefault(key, []).append(i)
    ids: Dict[int, int] = {}
    for i, h in enumerate(hs):
        h["hole"] = ids.setdefault(root(i), first + len(ids))
    return first + len(ids)


def _air_between(c1, c2, a, depth1: float, depth2: float, solid, tol: float) -> bool:
    """True when the two coaxial bores (midpoints c1, c2) meet, or when the axis
    between them runs through air only (sampled; no solid to ask: only meeting)."""
    t = (c2 - c1).dot(a)
    gap = abs(t) - (depth1 + depth2) / 2.0
    if gap <= tol:
        return True
    if solid is None:
        return False
    s = 1.0 if t > 0 else -1.0
    start = c1 + a * (s * depth1 / 2.0)
    for k in range(1, 8):
        q = start + a * (s * gap * k / 8.0)
        try:
            if solid.isInside(q, 1e-6, True):
                return False
        except Exception:  # noqa: BLE001 - unreadable: keep them apart
            return False
    return True


def _holes_of(shp, tol: float) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    bores = [(f, b) for f in shp.Faces for b in (_bore(f, tol),) if b is not None]
    # Groups are indexed by the cell of their axis line's foot (its point
    # nearest the origin, the same for +axis and -axis). Two lines that pass
    # the test below (within `tol` of each other, directions within ~1.5 mrad)
    # have feet at most 2*tol + 2*R*1.5e-3 apart (R: distance of the bores
    # from the origin), less than one cell, so the 27 cells around a face
    # hold every candidate: the same test as against all groups, without the
    # n² cost (1600 holes: 2.7 s).
    reach = max((b[0].Length for _f, b in bores), default=0.0)
    btol = max((b[4] for _f, b in bores), default=tol)
    fitted = btol > tol                 # fitted axes: directions within ~4.5 mrad
    cell = max(1.0, 4 * btol + 4 * reach * (4.5e-3 if fitted else 1.5e-3))
    buckets: Dict[tuple, List[Dict[str, Any]]] = {}
    for f, (center, axis, radius, span, ftol) in bores:
        foot = center - axis * center.dot(axis)
        key = tuple(math.floor(c / cell) for c in (foot.x, foot.y, foot.z))
        match = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for g in buckets.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                        if match is not None and g["index"] > match["index"]:
                            continue            # first-created match wins, as before
                        t = max(ftol, g["tol"])
                        same_r = abs(g["radius"] - radius) < t
                        parallel = abs(abs(g["axis"].dot(axis)) - 1.0) < (1e-5 if t > tol else 1e-6)
                        d = center - g["origin"]
                        on_line = (d - g["axis"] * d.dot(g["axis"])).Length < t
                        if same_r and parallel and on_line:
                            match = g
        if match is not None:
            match["faces"].append((f, span))
        else:
            g = {"radius": radius, "axis": axis, "origin": center, "tol": ftol,
                 "faces": [(f, span)], "index": len(groups)}
            groups.append(g)
            buckets.setdefault(key, []).append(g)
    out = []
    bb = verify.box(shp)
    edge_faces = None                   # built once, only if a hole needs it
    for g in _bore_runs(groups):
        if g["span"] < _TWO_PI - 1e-3:
            continue                    # partial arc: concave fillet / slot end
        a, o = g["axis"], g["origin"]
        ts = g["ts"]
        t0, t1 = min(ts), max(ts)
        mid = o + a * ((t0 + t1) / 2.0)
        # through-hole: the bore spans the part's full extent along its axis...
        corners = [App.Vector(x, y, z) for x in (bb.XMin, bb.XMax)
                   for y in (bb.YMin, bb.YMax) for z in (bb.ZMin, bb.ZMax)]
        ext = [(c - o).dot(a) for c in corners]
        through = (t1 - t0) >= (max(ext) - min(ext)) - 1e-3
        if not through:
            # ...or neither end is closed: the base of an L-bracket, a bore under
            # a counterbore, a tilted hole (all read "blind" before)
            if edge_faces is None:
                edge_faces, outer = _edge_faces(shp), {}
            through = not (_closed_end(g["faces"], o, a, g["radius"], t0, -1.0, edge_faces, outer)
                           or _closed_end(g["faces"], o, a, g["radius"], t1, 1.0, edge_faces, outer))
        out.append({"diameter": round(2 * g["radius"], 4),
                    "axis": [round(x, 6) for x in (a.x, a.y, a.z)],
                    "center": [round(x, 4) for x in (mid.x, mid.y, mid.z)],
                    "depth": round(t1 - t0, 4), "through": through})
    return sorted(out, key=lambda h: (h["diameter"], h["center"]))


def _bore_runs(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Split each group of faces on one axis line into bores: runs of faces whose
    stretches along the axis overlap or meet. Coaxial holes of one diameter in two
    flanges read as ONE 24 mm bore across the air between them, and two blind
    holes drilled from opposite sides as one "through" hole across the material."""
    runs = []
    for g in groups:
        a, o, gap = g["axis"], g["origin"], 1e-6 + g["tol"]
        spans = []
        for f, span in g["faces"]:
            ts = _axial_extent([f], o, a)
            if ts:
                spans.append((min(ts), max(ts), f, span, ts))
        spans.sort(key=lambda x: x[0])
        cur = None
        for t0, t1, f, span, ts in spans:
            if cur is not None and t0 <= cur["t1"] + gap:
                cur["t1"] = max(cur["t1"], t1)
                cur["faces"].append(f)
                cur["span"] += span
                cur["ts"] += ts
                continue
            cur = {**g, "t1": t1, "faces": [f], "span": span, "ts": list(ts)}
            runs.append(cur)
    return runs


def _edge_faces(shp) -> Dict[int, List[Any]]:
    """Edge -> the faces bounded by it (an edge shared by two faces is one
    TopoDS edge: same hash whatever its orientation)."""
    out: Dict[int, List[Any]] = {}
    for f in shp.Faces:
        for e in f.Edges:
            out.setdefault(e.hashCode(), []).append(f)
    return out


def _closed_end(faces, o, a, r, t_end, side: float, edge_faces, outer) -> bool:
    """True when the bore is closed at its end `t_end`: a face meeting it there
    reaches INSIDE its cross-section (a flat bottom, a drill point's cone, the
    floor of a counterbore). An open end meets only faces lying outside it (the
    plate's surface around the hole, a countersink's cone).
    A plane neighbour is decided by topology: the end edge in its OUTER wire
    bounds a floor, in an inner wire it is a hole through that face. Any other
    neighbour: is the point just inside the bore's rim on it? (A solid
    classifier on the whole part took minutes on 1,600 holes; a point test on
    a face with 1,520 holes, a minute.)"""
    own = {f.hashCode() for f in faces}
    reach = 1e-6 + 1e-6 * r
    for f in faces:
        for e in f.Edges:
            if e.Curve.TypeId == "Part::GeomLine":
                continue                                    # a seam along the axis
            ts = [(q - o).dot(a) for q in e.discretize(8)]
            if (side < 0 and min(ts) > t_end + reach) or (side > 0 and max(ts) < t_end - reach):
                continue                                    # not at this end
            for nb in edge_faces.get(e.hashCode(), ()):
                k = nb.hashCode()
                if k in own:
                    continue
                try:
                    if k not in outer:          # once per face: Surface is slow on a big one
                        outer[k] = ({x.hashCode() for x in nb.OuterWire.Edges}
                                    if nb.Surface.TypeId == "Part::GeomPlane" else None)
                    if outer[k] is not None:
                        if e.hashCode() in outer[k]:
                            return True
                        continue
                    u0, u1 = e.ParameterRange
                    m = e.valueAt((u0 + u1) / 2.0)
                    w = m - o
                    radial = w - a * w.dot(a)
                    if radial.Length < 1e-9:
                        continue
                    q = m - radial.normalize() * max(1e-3, 1e-2 * r)   # just inside the rim
                    u, v = nb.Surface.parameter(q)
                    if nb.isInside(nb.Surface.value(u, v), 1e-6, True):
                        return True
                except Exception:  # noqa: BLE001 — unreadable neighbour: say closed
                    return True
    return False


def _axial_extent(faces, o, a) -> List[float]:
    """Positions along the axis reached by the bore's edges. End circles square
    to the axis and straight seams are exact at their vertices; any other edge
    (the ellipse of a tilted or slanted end) is sampled."""
    ts = []
    for f in faces:
        for e in f.Edges:
            c = getattr(e, "Curve", None)
            kind = getattr(c, "TypeId", "")
            square = kind == "Part::GeomCircle" and abs(abs(App.Vector(c.Axis).dot(a)) - 1.0) < 1e-9
            pts = ([v.Point for v in e.Vertexes] if square or kind == "Part::GeomLine"
                   else e.discretize(24))
            ts += [(p - o).dot(a) for p in pts]
    return ts


def _face_samples(face, n: int):
    u0, u1, v0, v1 = face.ParameterRange
    for i in range(n):
        for j in range(n):
            u = u0 + (u1 - u0) * (i + 0.5) / n
            v = v0 + (v1 - v0) * (j + 0.5) / n
            p = face.valueAt(u, v)
            if face.isInside(p, 1e-6, True):
                yield p, face.normalAt(u, v)


def _exit_distance(p, d, surfs, faces, lo, hi, limit, eps, tol):
    """Distance from p along unit direction d to the nearest face the ray
    LEAVES the material through, if nearer than `limit` (else `limit`).
    Returns (distance, number of faces whose intersection raised, index of
    that face or None)."""
    import numpy as np

    o = np.array((p.x, p.y, p.z))
    dv = np.array((d.x, d.y, d.z))
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = (lo - o) / dv
        t2 = (hi - o) / dv
    tn, tf = np.fmin(t1, t2), np.fmax(t1, t2)
    for ax in range(3):                       # ray parallel to a slab: in it or never
        if dv[ax] == 0.0:
            inside = (lo[:, ax] <= o[ax]) & (o[ax] <= hi[:, ax])
            tn[:, ax] = np.where(inside, -np.inf, np.inf)
            tf[:, ax] = np.where(inside, np.inf, -np.inf)
    tnear, tfar = tn.max(axis=1), tf.min(axis=1)
    cand = np.nonzero((tnear <= tfar) & (tfar > eps) & (tnear < limit))[0]
    errors, hit = 0, None
    if not len(cand):
        return limit, errors, hit
    line = Part.Line(p, p + d)
    for j in cand[np.argsort(tnear[cand], kind="stable")]:
        if tnear[j] >= limit:
            break                             # bounding boxes sorted: nothing nearer left
        try:
            pts, _curves = line.intersectCS(surfs[j])
            for pt in pts:
                q = App.Vector(pt.X, pt.Y, pt.Z)
                t = (q - p).dot(d)
                if not (eps < t < limit) or not faces[j].isInside(q, tol, True):
                    continue
                u, v = surfs[j].parameter(q)
                if faces[j].normalAt(u, v).dot(d) > 1e-9:   # leaving, not entering or grazing
                    limit, hit = t, int(j)
        except Exception:  # noqa: BLE001 — counted and reported, never silent
            errors += 1
    return limit, errors, hit


def _adjacent_pairs(faces) -> set:
    """(i, j), i < j, for every two faces sharing an edge."""
    by_edge: Dict[int, List[int]] = {}
    for i, f in enumerate(faces):
        for e in f.Edges:
            by_edge.setdefault(e.hashCode(), []).append(i)
    return {(a, b) for fs in by_edge.values() for a in fs for b in fs if a < b}


def _closest_walls(faces, lo, hi, limit: float, tol: float, adjacent: Optional[set] = None):
    """(thickness, point) of the thinnest wall between two faces that do not
    share an edge, closer than `limit`, with the MATERIAL between them (each
    face's outward normal at its closest point faces away from the other);
    (limit, None) if there is none. Boxes first: only the few pairs closer
    than the sampled minimum are measured."""
    import numpy as np

    n = len(faces)
    if adjacent is None:
        adjacent = _adjacent_pairs(faces)
    best, where = limit, None
    for i in range(n - 1):
        gap = np.maximum(0.0, np.maximum(lo[i + 1:] - hi[i], lo[i] - hi[i + 1:]))
        for j in (np.nonzero(np.sqrt((gap * gap).sum(axis=1)) < best)[0] + i + 1):
            if (i, int(j)) in adjacent:
                continue
            fi, fj = faces[i], faces[int(j)]
            try:
                d, pts, _info = fi.distToShape(fj)
                if not (tol < d < best) or not pts:
                    continue
                p1, p2 = pts[0]
                n1 = fi.normalAt(*fi.Surface.parameter(p1))
                n2 = fj.normalAt(*fj.Surface.parameter(p2))
            except Exception:  # noqa: BLE001 — the sampled value stands
                continue
            if (p2 - p1).dot(n1) < 0 and (p1 - p2).dot(n2) < 0:     # material in between
                best, where = d, (p1 + p2) * 0.5
    return best, where


def min_wall(obj_or_shape, per_face: Optional[int] = None, max_samples: int = 2000) -> Dict[str, Any]:
    """Estimated minimum wall thickness by inward ray-casting (sampled).

    Rays start on each face and go along the inward normal. The nearest face
    they LEAVE the solid through gives the thickness there, intersected on
    the exact B-rep surfaces (no mesh: no chord error, and no meshing time,
    which reached 40 s on a 1600-hole plate). It is still an ESTIMATE: thin
    spots between sample points can be missed, so say so to the user.

    Samples are spread over ALL faces (per_face x per_face each, sized from
    max_samples; on huge models every k-th face). Faces are pre-filtered by
    bounding box, nearest first, and only walls thinner than the thinnest
    found so far are searched. Returns
    {min_mm, at, samples, faces_sampled, faces, method[, unchecked]}.
    """
    import numpy as np

    shp = _shape(obj_or_shape)
    solids = shp.Solids
    if not solids:
        return {"min_mm": None, "samples": 0, "method": "no solid"}
    diag = verify.box(shp).DiagonalLength
    eps = max(diag * 1e-7, 1e-7)
    tol = max(diag * 1e-6, 1e-6)
    n_faces = sum(len(s.Faces) for s in solids)
    n = per_face or max(1, min(6, int(math.sqrt(max_samples / max(n_faces, 1)))))
    stride = max(1, math.ceil(n_faces * n * n / max_samples))   # huge models: every k-th face
    best, where, count, sampled, errors, k = math.inf, None, 0, 0, 0, -1
    per_solid = []
    for solid in solids:
        faces = solid.Faces
        surfs = [f.Surface for f in faces]
        bbs = [verify.box(f) for f in faces]     # never too small: no face skipped
        lo = np.array([(b.XMin, b.YMin, b.ZMin) for b in bbs]) - tol
        hi = np.array([(b.XMax, b.YMax, b.ZMax) for b in bbs]) + tol
        adjacent = _adjacent_pairs(faces)
        per_solid.append((faces, lo, hi, adjacent))
        for i, face in enumerate(faces):
            k += 1
            if k % stride:
                continue
            sampled += 1
            for p, nrm in _face_samples(face, n):
                if nrm.Length < 1e-12:
                    continue
                count += 1
                t, err, hit = _exit_distance(p, -nrm, surfs, faces, lo, hi, best, eps, tol)
                errors += err
                # leaving through a face that shares an edge with the start face is the
                # corner those two faces make (it thins to 0 at their edge: holes that
                # cross, a hole into a chamfer), not a wall; walls are between faces
                # that do not meet
                if hit is not None and (min(i, hit), max(i, hit)) in adjacent:
                    continue
                if t < best:
                    best, where = t, p
    # the rays only see walls under their sample points: a hole 0.3 mm from an edge
    # read 0.81 mm. Faces closer than that are measured exactly, two by two.
    for faces, lo, hi, adjacent in per_solid:
        t, p = _closest_walls(faces, lo, hi, best if best < math.inf else diag, tol, adjacent)
        if p is not None and t < best:
            best, where = t, p
    out = {"min_mm": None if best is math.inf else round(best, 4),
           "at": None if where is None else [round(x, 3) for x in (where.x, where.y, where.z)],
           "samples": count, "faces_sampled": sampled, "faces": n_faces,
           "method": f"exact ray-cast ({n}x{n}/face"
                     + (f", every {stride}th face)" if stride > 1 else ", all faces)")
                     + " + closest faces"}
    if errors:
        out["unchecked"] = f"{errors} ray/face intersection(s) failed (those faces were skipped)"
    return out


def overhangs(obj_or_shape, max_angle_deg: float = 45.0,
              build_dir: Sequence[float] = (0, 0, 1)) -> Dict[str, Any]:
    """Downward-facing area steeper than `max_angle_deg` from vertical.

    A face overhangs when its normal is within (90 - max_angle) degrees of
    straight down. Faces lying on the build plate are ignored. Several solids
    (an assembly): each sits on its own plate, as each part is printed (the
    lowest point of the WHOLE assembly made a plate's bottom an "overhang"
    as soon as a pin hung below it). Face names are those of the whole shape.
    """
    shp = _shape(obj_or_shape)
    up = App.Vector(*build_dir).normalize()
    limit = math.cos(math.radians(90.0 - max_angle_deg))
    index = {f.hashCode(): i for i, f in enumerate(shp.Faces)}
    area, faces = 0.0, []
    for part in (shp.Solids if len(shp.Solids) > 1 else [shp]):
        a, names = _overhangs_of(part, up, limit, index)
        area += a
        faces += names
    return {"area_mm2": round(area, 2), "faces": faces, "max_angle_deg": max_angle_deg}


def _overhangs_of(shp, up, limit: float, index: Dict[int, int]):
    # the bed: the part's lowest point. Not its lowest VERTEX: a rod lying down
    # has vertices only on its seam (on top), and everything below read "on the bed"
    bb = verify.box(shp)
    z_min = min(App.Vector(x, y, z).dot(up) for x in (bb.XMin, bb.XMax)
                for y in (bb.YMin, bb.YMax) for z in (bb.ZMin, bb.ZMax))
    area, faces = 0.0, []
    for f in shp.Faces:
        a = _face_overhang(f, up, limit, z_min)
        if a > 1e-9:
            area += a
            faces.append(f"Face{index.get(f.hashCode(), -1) + 1}")
    return area, faces


# a wall exactly at the limit (the 45 deg chamfer printed WITHOUT supports) is
# not an overhang: rounding noise put half of an inverted 45 deg cone in it
_ON_LIMIT = 1e-9


def _face_overhang(f, up, limit: float, z_min: float) -> float:
    """Overhanging area of one face. A plane has one normal: all or nothing
    (nothing when it lies on the bed). A curved face is judged triangle by
    triangle: judged by its middle normal alone, the ceiling of a horizontal
    hole, the underside of a sphere or of a rod lying down all counted 0."""
    import numpy as np

    s = f.Surface
    if s.TypeId == "Part::GeomPlane":
        u0, u1, v0, v1 = f.ParameterRange
        if -f.normalAt((u0 + u1) / 2, (v0 + v1) / 2).dot(up) <= limit + _ON_LIMIT:
            return 0.0
        hs = [v.Point.dot(up) for v in f.Vertexes] or [z_min]
        return 0.0 if max(hs) - z_min < 1e-6 else f.Area     # on the bed: no support
    if s.TypeId == "Part::GeomCylinder" and abs(abs(App.Vector(s.Axis).dot(up)) - 1.0) < 1e-9:
        return 0.0                      # a vertical bore or boss: walls only
    if s.TypeId == "Part::GeomCone" and abs(abs(App.Vector(s.Axis).dot(up)) - 1.0) < 1e-9:
        # one slope all round (a countersink, a 45 deg cone under a part): decided
        # exactly, like a plane -- mesh normals put half of a 45 deg cone over the limit
        u0, u1, v0, v1 = f.ParameterRange
        if -f.normalAt((u0 + u1) / 2, (v0 + v1) / 2).dot(up) <= limit + _ON_LIMIT:
            return 0.0
        return f.Area
    pts, tris = f.tessellate(max(verify.box(f).DiagonalLength * 5e-4, 1e-4))
    if not tris:
        return 0.0
    P = np.array([(p.x, p.y, p.z) for p in pts])
    T = np.array(tris)
    N = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    A = np.linalg.norm(N, axis=1) / 2.0
    keep = A > 1e-15
    if not keep.any():
        return 0.0
    N, A, T = N[keep] / (2.0 * A[keep, None]), A[keep], T[keep]
    # the triangulation's winding against the face's outward normal, once
    k = int(np.argmax(A))
    c = App.Vector(*P[T[k]].mean(axis=0))
    try:
        u, v = s.parameter(c)
        if np.dot(N[k], tuple(f.normalAt(u, v))) < 0:
            N = -N
    except Exception:  # noqa: BLE001 — keep the triangulation's own winding
        pass
    u_ = np.array((up.x, up.y, up.z))
    # vertex normals (area-weighted), then how far each vertex is inside the
    # overhang cone; a triangle across the cone's edge counts the part of its
    # area where that (linear) measure is positive. Whole triangles in or out
    # swung the result by a row of triangles (sphere: -4 %, +0 %, -1 %).
    VN = np.zeros_like(P)
    for j in range(3):
        np.add.at(VN, T[:, j], N * A[:, None])
    VN /= np.maximum(np.linalg.norm(VN, axis=1), 1e-300)[:, None]
    G = (-(VN @ u_) - limit - _ON_LIMIT)[T]
    frac = (G > 0).all(axis=1).astype(float)
    for i in np.nonzero((G > 0).any(axis=1) & ~(G > 0).all(axis=1))[0]:
        g = G[i]
        pos = g > 0
        lone = g[pos][0] if pos.sum() == 1 else g[~pos][0]       # the vertex alone on its side
        rest = g[~pos] if pos.sum() == 1 else g[pos]
        part = lone * lone / ((lone - rest[0]) * (lone - rest[1]))
        frac[i] = part if pos.sum() == 1 else 1.0 - part
    on_bed = (P[T] @ u_).max(axis=1) - z_min < 1e-6
    total = A.sum()
    return float((A * frac)[~on_bed].sum() * (f.Area / total)) if total > 0 else 0.0


def printability(obj_or_name, bed_mm: Sequence[float] = (220, 220, 250),
                 min_wall_mm: float = 0.8, max_overhang_deg: float = 45.0,
                 max_overhang_area_mm2: float = 0.0) -> Dict[str, Any]:
    """FDM printability verdict: watertight solid, fits the bed (any 90° turn
    on the plate), walls >= nozzle-friendly minimum, overhang area. Several
    solids (an assembly) are judged as parts printed one by one; an advisory
    says what printing them together would need.

    Returns {status, issues, advisories, measures}. Overhangs are FAIL only if
    their area exceeds `max_overhang_area_mm2` (supports are often fine).
    """
    if (not isinstance(bed_mm, (list, tuple)) or len(bed_mm) != 3
            or not all(isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0
                       for b in bed_mm)):
        raise ValueError(f"bed_mm must be [x, y, z] in mm, all > 0 (got {bed_mm!r})")
    if not isinstance(min_wall_mm, (int, float)) or min_wall_mm < 0:
        raise ValueError(f"min_wall_mm must be a number >= 0 (got {min_wall_mm!r})")
    if isinstance(obj_or_name, str) or hasattr(obj_or_name, "TypeId"):
        _o = verify._resolve(obj_or_name)
        _step = verify.body_of(_o)
        if _step is not None:   # a step of a Body is not the part (later features are missing)
            raise ValueError(f"{_o.Name} is a step of {_step.Name}, not the whole part: "
                             f"judge {_step.Name}")
    r = verify.check(obj_or_name)
    issues = [f"verify: {i}" for i in r["issues"]]
    advisories: List[str] = []
    measures: Dict[str, Any] = {"bbox": r.get("bbox")}
    if r["status"] != verify.OK or not r["closed"]:
        issues.append("not a watertight valid solid")
    shp = _shape(obj_or_name)
    bx, by, bz = bed_mm

    def fits(bb) -> bool:
        x, y, z = bb.XLength, bb.YLength, bb.ZLength
        return ((x <= bx and y <= by) or (y <= bx and x <= by)) and z <= bz
    solids = shp.Solids
    if len(solids) > 1:
        # an assembly: judged as parts printed one by one (bed size, overhangs),
        # the usual case; print-in-place needs the combined check, said below
        too_big = [f"solid{i + 1}" for i, s in enumerate(solids) if not fits(verify.box(s))]
        if too_big:
            issues.append(f"{', '.join(too_big)} does not fit the {bx}x{by}x{bz} mm build volume")
        advisories.append(
            f"{len(solids)} separate solids, each judged as printed on its own (bed, overhangs); "
            f"printed together (print-in-place) they {'fit' if fits(verify.box(shp)) else 'do NOT fit'}"
            " the bed and the overhangs must be checked with them assembled")
    elif r.get("bbox") and not fits(verify.box(shp)):
        issues.append(f"does not fit the {bx}x{by}x{bz} mm build volume")
    w = min_wall(shp)
    measures["min_wall"] = w
    if w["min_mm"] is not None and w["min_mm"] < min_wall_mm:
        issues.append(f"wall {w['min_mm']} mm < {min_wall_mm} mm at {w['at']} (estimate)")
    elif w.get("unchecked"):
        advisories.append(f"wall thickness only partly checked: {w['unchecked']}")
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

    bore_d: Dict[int, float] = {}           # each hole's narrowest bore
    for h in hs:
        bore_d[h["hole"]] = min(bore_d.get(h["hole"], h["diameter"]), h["diameter"])

    several = len(hs) != len(bore_d)        # some hole has several bores: number them

    def _one(h):
        ax = h["axis"]
        along = "" if abs(abs(ax[2]) - 1.0) < 1e-6 else f" along {ax}"
        kind = "" if h["through"] else f" blind {h['depth']:g} deep"
        if h["diameter"] > bore_d[h["hole"]]:
            kind += f" (counterbore of the D{bore_d[h['hole']]:g})"
        return f"{'#%d ' % h['hole'] if several else ''}D{h['diameter']}{kind}{along} @ {h['center']}"
    hole_txt = ", ".join(_one(h) for h in hs[:10]) or "none"
    if len(hs) > 10:
        hole_txt += f", ... ({len(hs)} bores)"
    n = len(bore_d)
    count = f"{n}" + (f", {len(hs)} bores" if len(hs) != n else "")
    return f"{v}\nholes ({count}): {hole_txt}\nmin wall ~{w['min_mm']} mm ({w['method']})"
