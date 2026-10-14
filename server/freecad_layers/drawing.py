"""TechDraw 2D drawings — pages, projected views, dimensions, export (GAP G1).

Closes the workshop-deliverable gap (cahier des charges §4.6; the pince test
required per-part 2D plans): build a dimensioned drawing page from any solid
and export it, fully headless under freecadcmd.

Headless facts (probed on FreeCAD 1.1.1):
  - pages/views/dimensions recompute fine without a GUI;
  - `TechDraw.writeDXFPage` works headless -> DXF is the native export here.
    PDF/SVG page export is GUI-coupled; for PDF, open the .FCStd in the GUI
    and print, or convert the DXF externally.

No silent fallbacks (audit 2026-10-11, §2.2 D1–D3): an unknown view name or
template is an error, the scale is chosen so views fit their slot, and
verdict() FAILs when a view sticks out of the page.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import FreeCAD as App
import TechDraw

from . import safe_out_path, safe_under, verify

# Standard projections: view name -> (Direction, XDirection). An explicit,
# non-parallel XDirection avoids TechDraw's "failed to create projection CS"
# fallback on side views.
DIRECTIONS = {
    "front": ((0, -1, 0), (1, 0, 0)),
    "back": ((0, 1, 0), (-1, 0, 0)),
    "top": ((0, 0, 1), (1, 0, 0)),
    "bottom": ((0, 0, -1), (1, 0, 0)),
    "left": ((-1, 0, 0), (0, -1, 0)),
    "right": ((1, 0, 0), (0, 1, 0)),
    "iso": ((1, -1, 1), (1, 1, 0)),
}

# Normalised drawing scales, largest first (ISO 5455).
STANDARD_SCALES = (10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01, 0.005, 0.002, 0.001)


def _val(q, default: float = 0.0) -> float:
    return float(getattr(q, "Value", q if q is not None else default))


def _template_dir() -> Path:
    return Path(App.getResourceDir()) / "Mod" / "TechDraw" / "Templates"


def _find_template(name: str = "A4_Landscape") -> str:
    """Resolve a template NAME to an SVG inside FreeCAD's template dir.

    Secure by design: the caller's string is a name fragment, never a path —
    the result is re-anchored under the template dir (anti-traversal).
    Unknown names are an error, not a silent A4.
    """
    tdir = _template_dir()
    frag = Path(name).name.lower().replace(".svg", "")
    cands = [p for p in tdir.rglob("*.svg")
             if not {"localized", "titleblocks"} & {x.lower() for x in p.parts}]
    hits = [p for p in cands if frag in p.stem.lower()]
    if not hits:
        sample = sorted(p.stem for p in cands)[:12]
        raise FileNotFoundError(f"no TechDraw template matching {name!r} in {tdir}; "
                                f"e.g. {sample}")
    hits.sort(key=lambda p: (p.stem.lower() != frag,
                             not p.stem.startswith("Default_Template"),
                             len(p.stem), p.stem))
    return str(safe_under(tdir, hits[0].relative_to(tdir)))


def make_page(doc, name: str = "Page", template: str = "A4_Landscape"):
    """Create a DrawPage with an SVG template. Returns the page object."""
    page = doc.addObject("TechDraw::DrawPage", name)
    tmpl = doc.addObject("TechDraw::DrawSVGTemplate", name + "Template")
    tmpl.Template = _find_template(template)
    page.Template = tmpl
    return page


def page_size(page) -> Tuple[float, float]:
    t = page.Template
    w, h = _val(getattr(t, "Width", 0)), _val(getattr(t, "Height", 0))
    return (w, h) if w > 0 and h > 0 else (297.0, 210.0)


def _resolve_direction(direction):
    if isinstance(direction, str):
        if direction not in DIRECTIONS:
            raise ValueError(f"unknown view direction {direction!r}; "
                             f"use one of {sorted(DIRECTIONS)} or a 3D vector")
        return DIRECTIONS[direction]
    return tuple(direction), None


def projected_size(shapes, direction, xdirection=None) -> Tuple[float, float]:
    """Width/height (model mm) of the shapes' bounding box seen along `direction`."""
    d = App.Vector(*direction).normalize()
    x = App.Vector(*(xdirection or (1, 0, 0)))
    x = x - d * x.dot(d)
    if x.Length < 1e-9:                      # xdir parallel to view: pick any normal
        x = App.Vector(0, 0, 1) - d * d.z
        if x.Length < 1e-9:
            x = App.Vector(1, 0, 0) - d * d.x
    x.normalize()
    y = d.cross(x).normalize()
    pts = []
    for s in shapes:
        bb = verify.box(s)
        pts += [App.Vector(px, py, pz) for px in (bb.XMin, bb.XMax)
                for py in (bb.YMin, bb.YMax) for pz in (bb.ZMin, bb.ZMax)]
    if not pts:
        return 0.0, 0.0
    xs, ys = [p.dot(x) for p in pts], [p.dot(y) for p in pts]
    return max(xs) - min(xs), max(ys) - min(ys)


def auto_scale(shapes, views: Sequence[str], slot_w: float, slot_h: float) -> float:
    """Largest standard scale at which every requested view fits its slot."""
    need = 0.0
    for v in views:
        d, xd = _resolve_direction(v)
        w, h = projected_size(shapes, d, xd)
        need = max(need, w / slot_w if slot_w else math.inf, h / slot_h if slot_h else math.inf)
    if need <= 0:
        return 1.0
    for s in STANDARD_SCALES:
        if s * need <= 1.0:
            return s
    return STANDARD_SCALES[-1]


def add_view(page, objects, direction: Union[str, Tuple[float, float, float]] = "front",
             scale: float = 1.0, x: float = 0.0, y: float = 0.0,
             name: str = "View"):
    """Add a projected view of `objects` (object or list) to the page."""
    doc = page.Document
    if not isinstance(objects, (list, tuple)):
        objects = [objects]
    vec, xdir = _resolve_direction(direction)
    view = doc.addObject("TechDraw::DrawViewPart", name)
    page.addView(view)
    view.Source = list(objects)
    view.Direction = App.Vector(*vec)
    if xdir is not None:
        view.XDirection = App.Vector(*xdir)
    view.Scale = scale
    if x or y:
        view.X, view.Y = x, y
    doc.recompute()
    return view


def add_extent_dims(view, horizontal: bool = True, vertical: bool = True) -> List:
    """Overall-size dimensions (the minimum any workshop plan needs).

    Tolerant: TechDraw can throw "Dim extent not found" on views whose
    geometry isn't ready or is edge-degenerate for one direction; a plan with
    views but a missing auto-dim is still a valid deliverable, so each dim is
    best-effort rather than fatal.
    """
    view.Document.recompute()
    dims = []
    for want, direction in ((horizontal, 0), (vertical, 1)):
        if not want:
            continue
        try:
            dims.append(TechDraw.makeExtentDim(view, [], direction))
        except Exception as e:  # noqa: BLE001 — RuntimeError from OCCT
            print(f"extent dim {'H' if direction == 0 else 'V'} skipped "
                  f"on {view.Name}: {e}")
    view.Document.recompute()
    return dims


def add_distance_dim(view, from_pt: Tuple[float, float], to_pt: Tuple[float, float],
                     dim_type: str = "Distance"):
    """Point-to-point dimension. Points are unscaled 2D view coordinates.
    dim_type: Distance | DistanceX | DistanceY."""
    d = TechDraw.makeDistanceDim(view, dim_type,
                                 App.Vector(from_pt[0], from_pt[1], 0),
                                 App.Vector(to_pt[0], to_pt[1], 0))
    view.Document.recompute()
    return d


def export_dxf(page, path: str, overwrite: bool = False) -> str:
    """Export the full page (views + dimensions) to DXF. Headless-safe.

    Secure by design: extension enforced, resolved path, no silent clobber
    (pass overwrite=True to replace an existing file) — see safe_out_path.
    """
    out = safe_out_path(path, {".dxf"}, overwrite=overwrite)
    TechDraw.writeDXFPage(page, str(out))
    if not (out.is_file() and out.stat().st_size > 0):
        raise RuntimeError(f"DXF export produced nothing at {out}")
    return str(out)


def view_svg(view) -> str:
    """Edges of one view as an SVG fragment (quick text-friendly preview)."""
    return TechDraw.viewPartAsSvg(view)


def _slots(w: float, h: float):
    """2x2 layout clear of the bottom-right title block: (x, y, slot_w, slot_h)."""
    sw, sh = w * 0.42, h * 0.36
    return [(w * 0.25, h * 0.70, sw, sh), (w * 0.25, h * 0.30, sw, sh),
            (w * 0.70, h * 0.70, sw, sh), (w * 0.70, h * 0.30, sw, sh * 0.6)]


def sheet_for(obj, dxf_path: Optional[str] = None,
              views: Sequence[str] = ("front", "top", "right"),
              scale: Union[float, str] = "auto", dims: bool = True,
              overwrite: bool = False, template: str = "A4_Landscape") -> Dict[str, Any]:
    """One call: standard multi-view dimensioned sheet for a part.

    Creates a page with up to 4 projections (front top-left, top below it,
    right top-right, 4th bottom-right), overall-extent dimensions on the first
    two axis-aligned views (never on an iso view: a projected length is not a
    size), and optionally exports DXF. scale="auto" picks the largest
    standard scale (1:1, 1:2, 1:5, 2:1...) at which every view fits its slot.
    Returns a compact summary dict (token-minimal).
    """
    _step = verify.body_of(obj)
    if _step is not None:   # a step of a Body is not the part (later features are missing)
        raise ValueError(f"{obj.Name} is a step of {_step.Name}, not the whole part: "
                         f"draw {_step.Name}")
    for v in views:
        _resolve_direction(v)                     # fail before creating anything
    doc = obj.Document
    page = make_page(doc, f"Page_{obj.Name}", template)
    doc.recompute()
    slots = _slots(*page_size(page))
    if scale == "auto":
        scale = auto_scale([obj.Shape], views[:4], slots[0][2], slots[0][3])
    made = []
    for i, vname in enumerate(views[:4]):
        x, y = slots[i][:2]
        made.append(add_view(page, obj, vname, scale=float(scale), x=x, y=y,
                             name=f"{obj.Name}_{vname}"))
    ndims = 0
    if dims:
        # overall sizes only on axis-aligned views: on an iso (or any tilted) view
        # an extent is a projected length, not a size of the part
        ortho = [v for v in made
                 if sum(abs(c) > 1e-9 for c in (v.Direction.x, v.Direction.y, v.Direction.z)) == 1]
        for v in ortho[:2]:
            ndims += len(add_extent_dims(v))
    out = None
    if dxf_path:
        out = export_dxf(page, dxf_path, overwrite=overwrite)
    return {"page": page.Name, "views": [v.Name for v in made], "scale": scale,
            "dims": ndims, "dxf": out, "verdict": verdict(page)}


def _view_fits(view, page_w: float, page_h: float) -> Optional[bool]:
    try:
        shapes = [s.Shape for s in view.Source if hasattr(s, "Shape")]
        w, h = projected_size(shapes, tuple(view.Direction), tuple(view.XDirection))
    except Exception:  # noqa: BLE001
        return None
    sc = _val(view.Scale, 1.0)
    cx, cy = _val(view.X), _val(view.Y)
    return (cx - w * sc / 2 >= 0 and cx + w * sc / 2 <= page_w and
            cy - h * sc / 2 >= 0 and cy + h * sc / 2 <= page_h)


def verdict(page) -> str:
    """One-line page summary: OK / FAIL (empty view, view off the page) /
    UNKNOWN (a view's extent could not be computed)."""
    views = [v for v in page.Views if v.isDerivedFrom("TechDraw::DrawViewPart")]
    dims = [v for v in page.Views if v.isDerivedFrom("TechDraw::DrawViewDimension")]
    w, h = page_size(page)
    issues, unknown = [], []
    if not views:
        issues.append("no view")
    for v in views:
        if not v.Source:
            issues.append(f"{v.Name} has no source")
            continue
        fits = _view_fits(v, w, h)
        if fits is None:
            unknown.append(v.Name)
        elif not fits:
            issues.append(f"{v.Name} exceeds the {w:.0f}x{h:.0f} page at scale {_val(v.Scale):g}")
    status = "FAIL" if issues else "UNKNOWN" if unknown else "OK"
    line = (f"{status} {page.Name}: views={len(views)} dims={len(dims)} "
            f"template={'yes' if page.Template else 'no'}")
    if issues:
        line += " | " + "; ".join(issues)
    if unknown:
        line += " | NOT CHECKED: extent of " + ", ".join(unknown)
    return line
