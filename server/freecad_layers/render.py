"""Headless shaded views of parts: front, top, right and isometric in one PNG.

FreeCAD's 3D viewer needs the GUI. This module renders without it:
- the shape is tessellated (OCCT);
- each view is an orthographic projection;
- faces are flat-shaded and painted back to front with matplotlib (Agg).
That gives a picture of what the part looks like, good enough to catch
"right numbers, wrong part". Each view shows the bounding-box size along its
two axes. The picture is not a measurement: `check`/`analyze` remain the
source of truth.

Use it on request, or at a milestone of a complex part or assembly (CLAUDE.md:
no screenshots by default).
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Sequence

import FreeCAD as App

from . import project_dir, safe_out_path, verify

# view -> (camera forward, up). Screen right = forward x up.
_VIEWS = {
    "front": ((0, 1, 0), (0, 0, 1)),     # from -Y: X right, Z up
    "top":   ((0, 0, -1), (0, 1, 0)),    # from +Z: X right, Y up
    "right": ((-1, 0, 0), (0, 0, 1)),    # from +X: Y right, Z up
    "iso":   ((-1, 1, -1), (0, 0, 1)),   # from (+1, -1, +1), like FreeCAD's isometric
}
_AXIS_NAMES = {(1, 0, 0): "X", (0, 1, 0): "Y", (0, 0, 1): "Z"}
_COLORS = [(0.55, 0.68, 0.85), (0.90, 0.62, 0.35), (0.55, 0.80, 0.55), (0.85, 0.55, 0.65),
           (0.75, 0.70, 0.45), (0.60, 0.60, 0.85)]
KEEP_RENDERS = 50   # pictures named by render_views itself; a path you give is never removed


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _frame(view: str):
    fwd, up = _VIEWS[view]
    fwd = _unit(fwd)
    right = _unit(_cross(fwd, up))
    up = _unit(_cross(right, fwd))
    return fwd, right, up


def _mesh(shape, tol: float):
    # MeshPart with a 0.5 rad angular step: on parts with many small round
    # features it is 4-5x faster than shape.tessellate(tol) (1600-hole plate:
    # 8.7 s vs 41 s, 173k vs 582k triangles) for the same picture. Triangles
    # are outward-oriented like tessellate's (back faces are culled below).
    import MeshPart
    m = MeshPart.meshFromShape(Shape=shape, LinearDeflection=tol, AngularDeflection=0.5)
    pts, tris = m.Topology
    P = [(p.x, p.y, p.z) for p in pts]
    return P, tris


def views(targets, path: Optional[str] = None, which: Sequence[str] = ("front", "top", "right", "iso"),
          size_px: int = 1000, overwrite: bool = False) -> Dict[str, Any]:
    """Render `targets` (one name/object or a list) to a PNG grid.

    Returns {path, views, triangles, bbox}. `path` defaults to
    <project>/renders/<names>_<time>.png (gated by safe_out_path).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    objs = [verify._resolve(t) for t in (targets if isinstance(targets, (list, tuple)) else [targets])]
    if not objs:
        raise ValueError("targets must name at least one object to render")
    shapes = []
    for o in objs:
        shp = getattr(o, "Shape", None)
        if shp is None or not hasattr(shp, "isNull") or shp.isNull() or not shp.Faces:
            raise ValueError(f"{o.Name} has no faces to render")
        shapes.append(shp)
    bad = [v for v in which if v not in _VIEWS]
    if bad:
        raise ValueError(f"unknown view(s) {bad}; use {sorted(_VIEWS)}")

    bb = verify.box(shapes[0])
    for s in shapes[1:]:
        bb.add(verify.box(s))
    tol = max(bb.DiagonalLength / 400.0, 1e-3)
    meshes = [_mesh(s, tol) for s in shapes]
    n_tris = sum(len(t) for _, t in meshes)

    names = "_".join(o.Name for o in objs)[:60]
    auto = path is None
    if auto:
        # two renders in the same second used to collide ("exists"): number them
        stem = project_dir() / "renders" / f"{names}_{time.strftime('%Y%m%d-%H%M%S')}"
        for seq in range(100):
            try:
                out = safe_out_path(f"{stem}{f'-{seq:02d}' if seq else ''}.png", {".png"})
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError(f"{stem}*.png: 100 renders in one second")
    else:
        out = safe_out_path(path, {".png"}, overwrite=overwrite)

    cols = 2 if len(which) > 1 else 1
    rows = math.ceil(len(which) / cols)
    inch = size_px / 100.0
    fig, axes = plt.subplots(rows, cols, figsize=(inch, inch * rows / cols), dpi=100,
                             squeeze=False)
    # Triangles as numpy arrays (one pass per part): corners A, B, C, unit normals
    # and a base colour per triangle -- the per-view work below is vectorised.
    import numpy as np
    tri_a, tri_b, tri_c, tri_n, tri_col = [], [], [], [], []
    for i, (P, tris) in enumerate(meshes):
        if not tris:
            continue
        Pa = np.asarray(P, dtype=float)
        T = np.asarray(tris, dtype=int).reshape(-1, 3)
        a, b, c = Pa[T[:, 0]], Pa[T[:, 1]], Pa[T[:, 2]]
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n, axis=1)
        keep = ln > 1e-15                     # drop degenerate slivers
        tri_a.append(a[keep]); tri_b.append(b[keep]); tri_c.append(c[keep])
        tri_n.append(n[keep] / ln[keep, None])
        tri_col.append(np.tile(_COLORS[i % len(_COLORS)], (int(keep.sum()), 1)))
    A, B, C = (np.concatenate(x) if x else np.zeros((0, 3)) for x in (tri_a, tri_b, tri_c))
    N = np.concatenate(tri_n) if tri_n else np.zeros((0, 3))
    COL = np.concatenate(tri_col) if tri_col else np.zeros((0, 3))

    for k, view in enumerate(which):
        ax = axes[k // cols][k % cols]
        fwd, right, up = _frame(view)
        # key light from the viewer's upper left: top, front and side faces read apart
        cam_light = _unit(tuple(-0.5 * f + 0.8 * u - 0.3 * r for f, u, r in zip(fwd, up, right)))
        f, r_, u_, lt = (np.asarray(v, dtype=float) for v in (fwd, right, up, cam_light))
        front = N @ f <= 0                     # back faces of closed solids are hidden
        a, b, c, n = A[front], B[front], C[front], N[front]
        shade = 0.45 + 0.55 * np.clip(n @ lt, 0.0, None)
        colors = np.clip(COL[front] * shade[:, None], 0.0, 1.0)
        verts = np.stack([np.stack([p @ r_, p @ u_], axis=1) for p in (a, b, c)], axis=1)
        order = np.argsort(-(((a + b + c) / 3.0) @ f), kind="stable")   # far first
        pc = PolyCollection(verts[order], facecolors=colors[order],
                            edgecolors=colors[order], linewidths=0.8)  # no seams
        ax.add_collection(pc)
        corners = [(x, y, z) for x in (bb.XMin, bb.XMax) for y in (bb.YMin, bb.YMax)
                   for z in (bb.ZMin, bb.ZMax)]
        us = [_dot(c, right) for c in corners]
        vs = [_dot(c, up) for c in corners]
        pad = 0.06 * max(max(us) - min(us), max(vs) - min(vs), 1e-6)
        ax.set_xlim(min(us) - pad, max(us) + pad)
        ax.set_ylim(min(vs) - pad, max(vs) + pad)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        rname = _AXIS_NAMES.get(tuple(round(c) for c in right))
        uname = _AXIS_NAMES.get(tuple(round(c) for c in up))
        if rname and uname:
            size = {"X": bb.XLength, "Y": bb.YLength, "Z": bb.ZLength}
            ax.set_title(f"{view}: {rname} {size[rname]:.4g} x {uname} {size[uname]:.4g} mm",
                         fontsize=10)
        else:
            ax.set_title(f"{view}", fontsize=10)
    for k in range(len(which), rows * cols):
        axes[k // cols][k % cols].axis("off")
    fig.suptitle(", ".join(o.Label for o in objs)
                 + f"  (bbox {bb.XLength:.4g} x {bb.YLength:.4g} x {bb.ZLength:.4g} mm)", fontsize=11)
    fig.tight_layout()
    fig.savefig(str(out))
    plt.close(fig)
    if auto:   # renders/ grew by one picture per call, forever: keep the newest
        old = sorted(out.parent.glob("*_????????-??????*.png"), key=lambda f: f.stat().st_mtime_ns)
        for f in old[:-KEEP_RENDERS]:
            try:
                f.unlink()
            except OSError:
                pass
    return {"path": str(out), "views": list(which), "triangles": n_tris,
            "bbox": [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]}
