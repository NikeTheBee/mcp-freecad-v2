"""Drawing: a sketch or a photo becomes CAD geometry (env `sim`).

What a drawing program is for, in the workshop's own terms: draw a shape by hand, or photograph
one, and get a scaled outline that FreeCAD can turn into a sketch and extrude. No image editor is
installed and none is driven through its windows: everything goes through files, so it can be
re-run and checked like every other step.

Kinds:
- trace_image   raster (PNG, JPG, BMP...) -> outlines -> SVG + DXF, at a real-world scale.
                Threshold, marching-squares contours, Douglas-Peucker simplification, and a
                scale either in mm per pixel or from a known length measured on the image.
- sketch_board  writes a self-contained drawing board (one local HTML file, no network, no
                install): draw with the mouse or a tablet, press Save, get an SVG whose
                millimetres are real. That SVG then goes through `trace_svg` or straight into
                FreeCAD.
- trace_svg     reads an SVG back, keeps the closed outlines, rescales and re-exports a clean
                SVG + DXF with the geometry FreeCAD imports best (polylines, no transforms).

Units: SI for the physical world, pixels for the image. `mm_per_pixel` or
(`reference_length_mm`, `reference_pixels`) set the scale. `simplify_mm` is the Douglas-Peucker
tolerance in millimetres (0 keeps every point); `min_area_mm2` drops specks.

What this is not: it does not recognise intent. It gives you the outline you actually drew,
including the wobble of your hand, so simplify, then check the result against a dimension you
know before extruding it. A photograph also needs to be taken square to the object, or the
perspective becomes a scale error that no tolerance will catch.

Exactness: REFERENCES below. Synthetic images whose area and perimeter are known exactly, and a
polyline whose simplification is known by hand.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")

import math  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

import numpy as np  # noqa: E402

from _common import result  # noqa: E402

ENV = "sim"
KINDS = {
    "trace_image": {"timeout": 300,
                    "doc": "photo or scan -> outlines -> scaled SVG + DXF (threshold, contours, "
                           "simplification); scale from mm/pixel or a known length"},
    "sketch_board": {"timeout": 60,
                     "doc": "write a local HTML drawing board (offline, no install) whose Save "
                            "produces an SVG in real millimetres"},
    "trace_svg": {"timeout": 120,
                  "doc": "re-read an SVG, keep the closed outlines, rescale, export a clean "
                         "SVG + DXF for FreeCAD"},
}

Point = Tuple[float, float]


def versions() -> Dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for p in ("numpy", "imageio"):
        try:
            out[p] = version(p)
        except PackageNotFoundError:
            out[p] = "missing"
    return out


def _f(x) -> float:
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(f"not a finite number: {x!r}")
    return v


# --------------------------------------------------------------------------- contour tracing
def contours(mask: np.ndarray) -> List[List[Point]]:
    """Closed contours of a boolean mask by marching squares, in pixel coordinates.

    Each cell of the grid between four pixel centres is classified by which of its corners are
    inside; the 16 cases give 0, 1 or 2 segments through the cell's edge midpoints. The segments
    are then chained end to end into closed loops. The two ambiguous (saddle) cases are resolved
    the same way every time, so the result is deterministic.

    Coordinates are in pixels, x to the right and y DOWN (image convention); the caller flips y.
    """
    m = np.pad(mask.astype(bool), 1, constant_values=False)
    h, w = m.shape
    segs: List[Tuple[Point, Point]] = []
    for j in range(h - 1):
        row0, row1 = m[j], m[j + 1]
        for i in range(w - 1):
            case = (int(row0[i]) << 3) | (int(row0[i + 1]) << 2) | (int(row1[i + 1]) << 1) | int(row1[i])
            if case in (0, 15):
                continue
            top = (i + 0.5, float(j))
            right = (float(i + 1), j + 0.5)
            bottom = (i + 0.5, float(j + 1))
            left = (float(i), j + 0.5)
            for a, b in _CASES[case]:
                segs.append(({"T": top, "R": right, "B": bottom, "L": left}[a],
                             {"T": top, "R": right, "B": bottom, "L": left}[b]))
    return _chain(segs)


# Marching-squares table. Corners, most significant first: top-left, top-right, bottom-right,
# bottom-left. Each entry lists the segments as pairs of edge midpoints (T, R, B, L), oriented so
# that the inside stays on the same side. Cases 5 and 10 are the saddles, always split the same
# way.
_CASES: Dict[int, Tuple[Tuple[str, str], ...]] = {
    1: (("L", "B"),), 2: (("B", "R"),), 3: (("L", "R"),), 4: (("R", "T"),),
    5: (("L", "T"), ("B", "R")), 6: (("B", "T"),), 7: (("L", "T"),), 8: (("T", "L"),),
    9: (("T", "B"),), 10: (("T", "R"), ("B", "L")), 11: (("T", "R"),), 12: (("R", "L"),),
    13: (("R", "B"),), 14: (("B", "L"),),
}


def _key(p: Point) -> Tuple[int, int]:
    return (int(round(p[0] * 2)), int(round(p[1] * 2)))


def _chain(segs: Sequence[Tuple[Point, Point]]) -> List[List[Point]]:
    """Chain segments into closed loops by matching endpoints.

    Endpoints fall on the half-pixel grid, so they match exactly once rounded to halves. An index
    from start point to the segments leaving it keeps the walk linear; segments are consumed as
    they are used, so each one belongs to exactly one loop.
    """
    from collections import defaultdict
    out_of: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for i, (a, _b) in enumerate(segs):
        out_of[_key(a)].append(i)
    used = [False] * len(segs)
    loops: List[List[Point]] = []
    for start in range(len(segs)):
        if used[start]:
            continue
        used[start] = True
        a0, b0 = segs[start]
        loop = [a0, b0]
        cur = b0
        for _ in range(len(segs)):
            nxt = None
            for i in out_of.get(_key(cur), ()):
                if not used[i]:
                    nxt = i
                    break
            if nxt is None:
                break
            used[nxt] = True
            cur = segs[nxt][1]
            if _key(cur) == _key(loop[0]):
                break
            loop.append(cur)
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def simplify(poly: Sequence[Point], tol: float) -> List[Point]:
    """Douglas-Peucker on a CLOSED ring.

    Run on the ring as if it were an open polyline, the first point becomes an arbitrary anchor
    and the corner next to it survives while the opposite one is cut. Splitting the ring at its
    two most distant points first removes that bias: each half is then simplified as an open
    chain, which keeps real corners and drops the half-pixel staircase.
    """
    pts = list(poly)
    if tol <= 0 or len(pts) < 4:
        return pts
    far = max(range(len(pts)), key=lambda i: math.dist(pts[0], pts[i]))
    keep = _dp(pts, 0, far, tol) | _dp(pts, far, len(pts) - 1, tol)
    keep.add(0)
    out = [pts[i] for i in sorted(keep)]
    return out if len(out) >= 3 else pts


def _dp(pts: Sequence[Point], i0: int, i1: int, tol: float) -> set:
    keep = {i0, i1}
    if i1 <= i0 + 1:
        return keep
    (x0, y0), (x1, y1) = pts[i0], pts[i1]
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy)
    worst, wi = -1.0, -1
    for i in range(i0 + 1, i1):
        x, y = pts[i]
        d = (abs(dy * x - dx * y + x1 * y0 - y1 * x0) / n if n > 0
             else math.hypot(x - x0, y - y0))
        if d > worst:
            worst, wi = d, i
    if worst > tol:
        keep |= _dp(pts, i0, wi, tol) | _dp(pts, wi, i1, tol)
    return keep


def polygon_area(poly: Sequence[Point]) -> float:
    """Signed shoelace area; positive counter-clockwise in a y-up frame."""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def polygon_perimeter(poly: Sequence[Point]) -> float:
    n = len(poly)
    return sum(math.dist(poly[i], poly[(i + 1) % n]) for i in range(n))


# --------------------------------------------------------------------------- writers
def write_svg(path: Path, loops: Sequence[Sequence[Point]], w_mm: float, h_mm: float) -> None:
    """One `path` element per outline, in millimetres, y up on the page."""
    parts = [f'<?xml version="1.0" encoding="UTF-8"?>',
             f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_mm:.4f}mm" '
             f'height="{h_mm:.4f}mm" viewBox="0 0 {w_mm:.4f} {h_mm:.4f}">']
    for loop in loops:
        d = "M " + " L ".join(f"{x:.4f},{h_mm - y:.4f}" for x, y in loop) + " Z"
        parts.append(f'  <path d="{d}" fill="none" stroke="#000" stroke-width="0.2"/>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def write_dxf(path: Path, loops: Sequence[Sequence[Point]]) -> None:
    """Minimal DXF R12 with one closed POLYLINE per outline: what FreeCAD and laser cutters
    read without argument."""
    o: List[str] = ["0", "SECTION", "2", "ENTITIES"]
    for loop in loops:
        o += ["0", "POLYLINE", "8", "0", "66", "1", "70", "1",
              "10", "0.0", "20", "0.0", "30", "0.0"]
        for x, y in loop:
            o += ["0", "VERTEX", "8", "0", "10", f"{x:.6f}", "20", f"{y:.6f}", "30", "0.0"]
        o += ["0", "SEQEND", "8", "0"]
    o += ["0", "ENDSEC", "0", "EOF"]
    path.write_text("\n".join(o) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- kinds
def _scale_mm_per_px(inp: Dict[str, Any]) -> float:
    if "mm_per_pixel" in inp:
        v = _f(inp["mm_per_pixel"])
    elif "reference_length_mm" in inp and "reference_pixels" in inp:
        px = _f(inp["reference_pixels"])
        if px <= 0:
            raise ValueError("`reference_pixels` must be > 0")
        v = _f(inp["reference_length_mm"]) / px
    else:
        raise ValueError("give the scale: `mm_per_pixel`, or `reference_length_mm` with "
                         "`reference_pixels` (the same length measured on the image)")
    if v <= 0:
        raise ValueError("the scale must be > 0 mm per pixel")
    return v


def _load_image(inp: Dict[str, Any], work: Path) -> np.ndarray:
    """Grey image as a float array in [0, 1]. `image` is a file in the work folder, or
    `image_array` is the pixel grid itself (used by the reference cases)."""
    if "image_array" in inp:
        a = np.asarray(inp["image_array"], dtype=float)
    else:
        name = str(inp.get("image", ""))
        if not re.fullmatch(r"[A-Za-z0-9_.\- ]{1,120}", name):
            raise ValueError("`image` must be a plain file name inside the job folder")
        src = (work / name)
        if not src.is_file():
            raise ValueError(f"image not found in the job folder: {name}")
        import imageio.v3 as iio
        a = np.asarray(iio.imread(src), dtype=float)
    if a.ndim == 3:
        a = a[..., :3].mean(axis=2) if a.shape[2] >= 3 else a[..., 0]
    if a.ndim != 2 or a.size == 0:
        raise ValueError("the image must be a non-empty 2-D (grey or colour) raster")
    mx = float(a.max())
    return a / mx if mx > 1.0 else a


def _run_trace_image(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    img = _load_image(inp, work)
    mm_px = _scale_mm_per_px(inp)
    thr = _f(inp.get("threshold", 0.5))
    if not 0.0 < thr < 1.0:
        raise ValueError("`threshold` must be strictly between 0 and 1")
    dark_is_ink = bool(inp.get("dark_is_ink", True))
    mask = (img < thr) if dark_is_ink else (img > thr)
    if not mask.any():
        raise ValueError("nothing above the threshold: check `threshold` and `dark_is_ink`")

    h_px, w_px = img.shape
    simp = _f(inp.get("simplify_mm", 0.0))
    min_area = _f(inp.get("min_area_mm2", 0.0))
    raw = contours(mask)

    loops: List[List[Point]] = []
    for c in raw:
        # the mask was padded by one pixel: shift back, scale, and flip y so the drawing is
        # y-up like CAD rather than y-down like an image
        mm = [((x - 1.0) * mm_px, (h_px - (y - 1.0)) * mm_px) for x, y in c]
        mm = simplify(mm, simp)
        if abs(polygon_area(mm)) >= min_area and len(mm) >= 3:
            loops.append(mm)
    if not loops:
        raise ValueError("no outline survived: lower `min_area_mm2` or `simplify_mm`")
    loops.sort(key=lambda p: -abs(polygon_area(p)))

    w_mm, h_mm = w_px * mm_px, h_px * mm_px
    svg, dxf = work / "trace.svg", work / "trace.dxf"
    write_svg(svg, loops, w_mm, h_mm)
    write_dxf(dxf, loops)
    biggest = loops[0]
    xs = [p[0] for p in biggest]
    ys = [p[1] for p in biggest]
    values = {
        "outlines": len(loops),
        "points_total": sum(len(p) for p in loops),
        "mm_per_pixel": mm_px,
        "image_px": [int(w_px), int(h_px)],
        "largest_area_mm2": round(abs(polygon_area(biggest)), 9),
        "largest_perimeter_mm": round(polygon_perimeter(biggest), 9),
        "largest_width_mm": round(max(xs) - min(xs), 9),
        "largest_height_mm": round(max(ys) - min(ys), 9),
        "largest_points": len(biggest),
    }
    return result(values, inp.get("limits"),
                  f"{len(loops)} outline(s); largest {values['largest_width_mm']:.2f} x "
                  f"{values['largest_height_mm']:.2f} mm",
                  files=[str(svg), str(dxf)],
                  notes=["the outline is the edge of the INK, so a pencil line gives two "
                         "outlines (both sides of the stroke): trace a filled shape, or keep "
                         "the outer one",
                         "contours follow the pixel grid: the staircase is real, raise "
                         "`simplify_mm` or draw bigger rather than trusting the last 0.1 mm",
                         "check one known dimension on the result before extruding it",
                         "import into FreeCAD with the SVG or the DXF, then constrain the "
                         "sketch: a traced outline is not a parametric design"])


def _run_trace_svg(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    name = str(inp.get("svg", "drawing.svg"))
    if not re.fullmatch(r"[A-Za-z0-9_.\- ]{1,120}", name):
        raise ValueError("`svg` must be a plain file name inside the job folder")
    src = work / name
    if not src.is_file():
        raise ValueError(f"SVG not found in the job folder: {name}")
    text = src.read_text(encoding="utf-8", errors="replace")
    scale = _f(inp.get("scale", 1.0))
    if scale <= 0:
        raise ValueError("`scale` must be > 0")
    simp = _f(inp.get("simplify_mm", 0.0))
    loops: List[List[Point]] = []
    for d in re.findall(r'\sd\s*=\s*"([^"]+)"', text):
        pts = _parse_path(d)
        if len(pts) >= 3:
            pts = [(x * scale, y * scale) for x, y in pts]
            pts = simplify(pts, simp)
            loops.append(pts)
    if not loops:
        raise ValueError("no usable outline: this reader takes SVG paths made of straight "
                         "segments (M/L/Z, absolute or relative). Flatten curves on export.")
    loops.sort(key=lambda p: -abs(polygon_area(p)))
    out_svg, out_dxf = work / "clean.svg", work / "clean.dxf"
    xs = [p[0] for lp in loops for p in lp]
    ys = [p[1] for lp in loops for p in lp]
    w_mm, h_mm = max(xs) - min(xs), max(ys) - min(ys)
    shifted = [[(x - min(xs), y - min(ys)) for x, y in lp] for lp in loops]
    write_svg(out_svg, shifted, max(w_mm, 1e-6), max(h_mm, 1e-6))
    write_dxf(out_dxf, shifted)
    values = {
        "outlines": len(loops),
        "points_total": sum(len(p) for p in loops),
        "width_mm": round(w_mm, 6),
        "height_mm": round(h_mm, 6),
        "largest_area_mm2": round(abs(polygon_area(loops[0])), 6),
        "largest_perimeter_mm": round(polygon_perimeter(loops[0]), 6),
    }
    return result(values, inp.get("limits"),
                  f"{len(loops)} outline(s), {w_mm:.2f} x {h_mm:.2f} mm",
                  files=[str(out_svg), str(out_dxf)],
                  notes=["straight-segment paths only: curves must be flattened on export",
                         "transforms on groups are ignored, so export without them"])


def _parse_path(d: str) -> List[Point]:
    """M/L/H/V/Z paths with straight segments, absolute or relative. Curves are refused rather
    than silently approximated."""
    toks = re.findall(r"[MmLlHhVvZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?", d)
    pts: List[Point] = []
    cmd = ""
    x = y = 0.0
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in "MmLlHhVvZz":
            cmd = t
            i += 1
            if cmd in "Zz":
                break
            continue
        if cmd in "CcSsQqTtAa":
            raise ValueError("curved SVG paths are not supported: flatten them on export")
        if cmd in "Hh":
            v = float(t)
            x = v if cmd == "H" else x + v
            i += 1
        elif cmd in "Vv":
            v = float(t)
            y = v if cmd == "V" else y + v
            i += 1
        else:
            if i + 1 >= len(toks):
                break
            a, b = float(toks[i]), float(toks[i + 1])
            x, y = (a, b) if cmd in "ML" else (x + a, y + b)
            i += 2
            if cmd == "M":
                cmd = "L"
            elif cmd == "m":
                cmd = "l"
        pts.append((x, y))
    return pts


_BOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sketch board</title>
<style>
 :root{color-scheme:light dark;--bg:#fbfbfa;--fg:#1a1a18;--line:#c9c7c0;--accent:#3a6ea5}
 @media (prefers-color-scheme:dark){:root{--bg:#191917;--fg:#eceae4;--line:#3a3a36}}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.5 ui-sans-serif,system-ui,sans-serif}
 header{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:10px 16px;
  border-bottom:1px solid var(--line)}
 h1{font-size:15px;margin:0 12px 0 0;font-weight:600}
 label{font-size:13px} input[type=number]{width:78px;padding:3px 6px;border:1px solid var(--line);
  border-radius:6px;background:transparent;color:inherit}
 button{padding:6px 12px;border:1px solid var(--line);border-radius:8px;background:transparent;
  color:inherit;cursor:pointer} button.primary{background:var(--accent);color:#fff;border-color:transparent}
 #wrap{padding:16px} canvas{background:#fff;border:1px solid var(--line);border-radius:4px;
  touch-action:none;max-width:100%}
 p.hint{margin:10px 16px;font-size:13px;opacity:.75}
</style></head><body>
<header>
 <h1>Sketch board</h1>
 <label>width <input id="w" type="number" value="200" min="10" max="2000"> mm</label>
 <label>height <input id="h" type="number" value="150" min="10" max="2000"> mm</label>
 <label>pen <input id="pen" type="number" value="1" min="0.1" max="20" step="0.1"> mm</label>
 <button id="undo">Undo</button><button id="clear">Clear</button>
 <button id="save" class="primary">Save SVG</button>
</header>
<div id="wrap"><canvas id="c"></canvas></div>
<p class="hint">Draw with the mouse, a pen or a finger. One stroke = one outline. Save writes an
SVG whose millimetres are the millimetres above: drop it in the job folder and run
<code>trace_svg</code>, or import it straight into FreeCAD.</p>
<script>
const PX=4; // pixels per mm on screen
const c=document.getElementById('c'), ctx=c.getContext('2d');
let strokes=[], cur=null;
function size(){const w=+document.getElementById('w').value, h=+document.getElementById('h').value;
 c.width=w*PX; c.height=h*PX; draw();}
function draw(){ctx.clearRect(0,0,c.width,c.height);ctx.fillStyle='#fff';ctx.fillRect(0,0,c.width,c.height);
 ctx.strokeStyle='#111';ctx.lineCap='round';ctx.lineJoin='round';
 for(const s of strokes){ctx.lineWidth=s.pen*PX;ctx.beginPath();
  s.pts.forEach((p,i)=>i?ctx.lineTo(p[0]*PX,p[1]*PX):ctx.moveTo(p[0]*PX,p[1]*PX));ctx.stroke();}}
function pos(e){const r=c.getBoundingClientRect();
 return [(e.clientX-r.left)*(c.width/r.width)/PX,(e.clientY-r.top)*(c.height/r.height)/PX];}
c.addEventListener('pointerdown',e=>{c.setPointerCapture(e.pointerId);
 cur={pen:+document.getElementById('pen').value,pts:[pos(e)]};strokes.push(cur);draw();});
c.addEventListener('pointermove',e=>{if(!cur)return;const p=pos(e),q=cur.pts[cur.pts.length-1];
 if(Math.hypot(p[0]-q[0],p[1]-q[1])>0.3){cur.pts.push(p);draw();}});
addEventListener('pointerup',()=>{cur=null;});
document.getElementById('undo').onclick=()=>{strokes.pop();draw();};
document.getElementById('clear').onclick=()=>{strokes=[];draw();};
document.getElementById('w').onchange=size; document.getElementById('h').onchange=size;
document.getElementById('save').onclick=()=>{
 const w=+document.getElementById('w').value,h=+document.getElementById('h').value;
 let s=`<?xml version="1.0" encoding="UTF-8"?>\\n<svg xmlns="http://www.w3.org/2000/svg" `+
  `width="${w}mm" height="${h}mm" viewBox="0 0 ${w} ${h}">\\n`;
 for(const st of strokes){if(st.pts.length<2)continue;
  s+=`  <path d="M `+st.pts.map(p=>p[0].toFixed(3)+','+p[1].toFixed(3)).join(' L ')+
     `" fill="none" stroke="#000" stroke-width="${st.pen}"/>\\n`;}
 s+='</svg>\\n';
 const a=document.createElement('a');
 a.href=URL.createObjectURL(new Blob([s],{type:'image/svg+xml'}));a.download='drawing.svg';a.click();};
size();
</script></body></html>
"""


def _run_board(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    out = work / "sketch_board.html"
    out.write_text(_BOARD_HTML, encoding="utf-8")
    # the only URL the page may contain is the SVG namespace, which is an identifier and is
    # never fetched; anything else would mean the board talks to the network
    urls = [u for u in re.findall(r"https?://[^\s\"'<>]+", _BOARD_HTML)
            if not u.startswith("http://www.w3.org/")]
    values = {
        "bytes": out.stat().st_size,
        "has_canvas": 1.0 if "<canvas" in _BOARD_HTML else 0.0,
        "offline": 0.0 if urls else 1.0,
        "external_urls": len(urls),
    }
    return result(values, inp.get("limits"),
                  f"drawing board written ({values['bytes']} bytes): open it in any browser",
                  files=[str(out)],
                  notes=["one local file, no network, nothing installed",
                         "Save writes drawing.svg to your downloads: move it into the job "
                         "folder and run `trace_svg`, or import it into FreeCAD directly",
                         "one stroke is one outline; for a closed shape, finish where you began"])


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fns = {"trace_image": _run_trace_image, "sketch_board": _run_board, "trace_svg": _run_trace_svg}
    if kind not in fns:
        raise ValueError(f"unknown kind {kind!r}; known: {', '.join(sorted(fns))}")
    return fns[kind](inp, Path(work))


# --------------------------------------------------------------------------- references
def _square_image(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """40 x 25 pixel black rectangle in a 100 x 80 white image."""
    a = np.ones((80, 100))
    a[20:45, 30:70] = 0.0          # rows 20..44 (25 px), columns 30..69 (40 px)
    return dict(inp, image_array=a.tolist())


def _disc_image(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """Black disc of radius 30 px centred in a 100 x 100 white image."""
    y, x = np.mgrid[0:100, 0:100]
    a = np.ones((100, 100))
    a[((x - 49.5) ** 2 + (y - 49.5) ** 2) <= 30.0 ** 2] = 0.0
    return dict(inp, image_array=a.tolist())


def _svg_file(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """A 60 x 40 mm rectangle written as a straight-segment SVG path."""
    (work / "drawing.svg").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" '
        'viewBox="0 0 100 100">\n'
        '  <path d="M 10,10 L 70,10 L 70,50 L 10,50 Z" fill="none" stroke="#000"/>\n'
        '</svg>\n', encoding="utf-8")
    return dict(inp, svg="drawing.svg")


# Geometry of the traced outline, countable by hand. Marching squares puts the outline on the
# midpoints BETWEEN an ink pixel centre and its white neighbour, so a block of 40 x 25 ink pixels
# traces a 40 x 25 pixel box exactly -- but each of the four corners is chamfered by half a
# pixel, which removes 4 x 0.125 px2 of area and replaces 1 px of perimeter by sqrt(0.5) px at
# each corner. At 0.5 mm per pixel:
_PX = 0.5
_SQ_W, _SQ_H = 40 * _PX, 25 * _PX
_SQ_AREA = (40 * 25 - 4 * 0.125) * _PX ** 2
_SQ_PERIM = (2 * (40 + 25) - 4 * (1.0 - math.sqrt(0.5))) * _PX
_SQ_PTS = 2 * (40 + 25)          # one vertex per cell edge crossed, deterministic

# The disc of radius 30 px traces a 60 px wide outline; its area matches the circle to a few
# parts in 1e4, while the staircase makes the perimeter a few per cent long.
_DISC_R_MM = 30 * _PX

REFERENCES = [
    {   # exact, unsimplified: size, area, perimeter and point count are all countable
        "name": "rectangle_exact_size", "kind": "trace_image", "setup": "_square_image",
        "inp": {"mm_per_pixel": _PX, "threshold": 0.5, "simplify_mm": 0.0},
        "expect": {"outlines": [1, 1e-9],
                   "largest_width_mm": [_SQ_W, 1e-12],
                   "largest_height_mm": [_SQ_H, 1e-12],
                   "largest_area_mm2": [_SQ_AREA, 1e-9],
                   "largest_perimeter_mm": [_SQ_PERIM, 1e-9],
                   "largest_points": [_SQ_PTS, 1e-9]},
    },
    {   # simplification must keep the four corners and the exact extent, not shave them
        "name": "rectangle_simplified", "kind": "trace_image", "setup": "_square_image",
        "inp": {"mm_per_pixel": _PX, "threshold": 0.5, "simplify_mm": 0.3},
        "expect": {"largest_width_mm": [_SQ_W, 1e-12],
                   "largest_height_mm": [_SQ_H, 1e-12],
                   "largest_points": [4, 0.3],          # 4 or 5 points, never the raw 130
                   # dropping the chamfers cuts one corner, which costs a few per cent of area:
                   # that is the price of simplification and the reason to state the tolerance
                   "largest_area_mm2": [_SQ_AREA, 0.05]},
    },
    {   # the same rectangle with the scale given as a measured reference length instead
        "name": "rectangle_scale_from_reference", "kind": "trace_image", "setup": "_square_image",
        "inp": {"reference_length_mm": 20.0, "reference_pixels": 40.0, "simplify_mm": 0.0},
        "expect": {"mm_per_pixel": [_PX, 1e-12], "largest_width_mm": [_SQ_W, 1e-12]},
    },
    {   # a disc: the traced area against the circle it approximates
        "name": "disc_area_perimeter", "kind": "trace_image", "setup": "_disc_image",
        "inp": {"mm_per_pixel": _PX, "threshold": 0.5, "simplify_mm": 0.0},
        "expect": {"outlines": [1, 1e-9],
                   "largest_area_mm2": [math.pi * _DISC_R_MM ** 2, 0.002],
                   "largest_perimeter_mm": [2 * math.pi * _DISC_R_MM, 0.07],
                   "largest_width_mm": [2 * _DISC_R_MM, 1e-12]},
    },
    {   # an SVG rectangle comes back at its stated size
        "name": "svg_rectangle_roundtrip", "kind": "trace_svg", "setup": "_svg_file",
        "inp": {"scale": 1.0},
        "expect": {"outlines": [1, 1e-9], "width_mm": [60.0, 1e-9], "height_mm": [40.0, 1e-9],
                   "largest_area_mm2": [2400.0, 1e-9]},
    },
    {   # the drawing board is written, is a real canvas page and calls nothing on the network
        "name": "board_is_offline", "kind": "sketch_board", "inp": {},
        "expect": {"has_canvas": [1.0, 1e-9], "offline": [1.0, 1e-9]},
    },
]
