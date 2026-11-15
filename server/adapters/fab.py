"""3D-print preparation with PrusaSlicer's command line (env `sim`, tool `prusaslicer`).

The adapter prepares FILES; the person slices, checks the preview and prints. It never slices and
never writes G-code: the PrusaSlicer argument list is built here from validated parameters only
(no argument list from the caller is ever passed through), and slicing / G-code / SLA actions,
post-processing scripts and option-like strings are refused.

Kinds (models = files of the study folder, copied there by `params.files`: STL, 3MF, OBJ, STEP;
or the geometry handed by the server):
- print_check    PrusaSlicer `--info` of each model: size, volume, manifold, mesh errors, parts;
                 fits-the-bed check against `bed_mm` [x, y] or [x, y, z] (a 90 deg turn about Z
                 allowed).
- print_project  a PrusaSlicer PROJECT 3MF: the models oriented (`rotate_*_deg`), copied
                 (`copies`), laid flat on the bed and arranged inside the bed of the user's own
                 profile (`profile`: an .ini exported from PrusaSlicer, required: the printer is
                 never guessed), the profile embedded so PrusaSlicer opens the project with it.
                 The written file is read back: unzipped (no G-code inside), profile re-read by
                 PrusaSlicer and compared key by key, objects and volume re-measured.

Units: mm, mm3, degrees. A refused request returns UNKNOWN with values {"refused": 1}.
Limits: PrusaSlicer's own mesh repair/statistics; the arrangement is a simple shelf packing of
bounding boxes (no nesting of concave outlines); orientation is the caller's (no automatic
optimisation); printability (walls, overhangs) is `printability` in FreeCAD, not this adapter.
"""
from __future__ import annotations

import math
import re
import struct
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from _common import judge, run_exe, tool

ENV = "sim"
KINDS = {
    "print_check": {"geometry": False, "timeout": 300,
                    "doc": "PrusaSlicer --info: size, volume, manifold, errors; fits the bed (bed_mm)"},
    "print_project": {"geometry": False, "timeout": 900,
                      "doc": "PrusaSlicer project 3MF with the user's profile, arranged; never G-code"},
}
MODEL_EXT = (".stl", ".3mf", ".obj", ".step", ".stp")
_COMMON_KEYS = {"kind", "files", "geometry", "face_info", "limits", "timeout_s"}
_ALLOWED = {
    "print_check": _COMMON_KEYS | {"model", "models", "bed_mm"},
    "print_project": _COMMON_KEYS | {"model", "models", "profile", "copies", "rotate_x_deg",
                                     "rotate_y_deg", "rotate_z_deg", "spacing_mm", "output",
                                     "allow_turn"},
}
# actions and options that slice, write G-code / SLA layers, run scripts or touch other files
_FORBIDDEN_ARGS = {"--export-gcode", "-g", "--gcode", "--export-sla", "--sla", "--slice", "-s",
                   "--delete-after-load", "--single-instance", "--gcodeviewer", "--post-process",
                   "--post_process", "--export-toolpaths"}
_GCODE_WORDS = re.compile(r"gcode|g-code|slice|sla|post[_-]?process|args|argv|cli|command|option",
                          re.I)
_GCODE_EXT = (".gcode", ".bgcode", ".gco", ".g", ".nc", ".sl1", ".sl1s")


def versions() -> Dict[str, str]:
    out = run_exe([tool("prusaslicer"), "--help"], Path.cwd(), 60)
    m = re.search(r"PrusaSlicer-([\d.]+\w*)", out.stdout + out.stderr)
    return {"prusaslicer": m.group(1) if m else "?", "numpy": np.__version__}


class Refused(Exception):
    """A request the adapter will not carry out (reported, nothing written)."""


def _refusal(why: str) -> Dict[str, Any]:
    return {"verdict": "UNKNOWN", "summary": f"refused: {why}", "values": {"refused": 1}, "files": []}


# ------------------------------------------------------------------ PrusaSlicer calls
def _ps(args: List[str], work: Path, timeout: float = 180) -> str:
    for a in args:
        low = a.lower()
        if low in _FORBIDDEN_ARGS or low.startswith(("--post", "--export-gcode", "--slice")):
            raise Refused(f"PrusaSlicer action {a!r} is never run (no slicing, no G-code)")
    data = work / "_prusaslicer"
    data.mkdir(exist_ok=True)
    full = [tool("prusaslicer"), "--datadir", str(data), "--loglevel", "1", *args]
    proc = run_exe(full, work, timeout)
    out = proc.stdout + proc.stderr
    if proc.returncode != 0:
        raise RuntimeError(f"PrusaSlicer failed (code {proc.returncode}): {out[-400:]}")
    return out


def _info(path: Path, work: Path) -> List[Dict[str, Any]]:
    """One dict per object of `--info`."""
    out = _ps(["--info", path.name if path.parent == work else str(path)], work)
    blocks: List[Dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            blocks.append({})
        elif "=" in line and blocks:
            k, v = (s.strip() for s in line.split("=", 1))
            try:
                blocks[-1][k] = float(v)
            except ValueError:
                blocks[-1][k] = v
    if not blocks or "volume" not in blocks[0]:
        raise RuntimeError(f"PrusaSlicer --info gave no statistics for {path.name}")
    return blocks


# ------------------------------------------------------------------ inputs
def _rel_file(work: Path, name: Any, exts: Tuple[str, ...], what: str) -> Path:
    if not isinstance(name, str) or not name:
        raise Refused(f"{what}: a file name of the study folder")
    rel = name.replace("\\", "/")
    if rel.startswith(("/", "-")) or ":" in rel or ".." in rel.split("/"):
        raise Refused(f"{what} {name!r}: a relative file name inside the study folder")
    p = (work / rel).resolve()
    if not p.is_relative_to(work.resolve()):
        raise Refused(f"{what} {name!r} is outside the study folder")
    if p.suffix.lower() not in exts:
        raise Refused(f"{what} {name!r}: extension must be one of {', '.join(exts)}")
    if not p.is_file():
        raise FileNotFoundError(f"{what} {name!r} not found in the study folder (pass it in files)")
    return p


def _scan_3mf(p: Path) -> None:
    """A 3MF given as input must hold neither G-code nor a post-processing script."""
    try:
        z = zipfile.ZipFile(p)
    except zipfile.BadZipFile:
        raise Refused(f"{p.name} is not a valid 3MF (zip)") from None
    with z:
        for n in z.namelist():
            if n.lower().endswith(_GCODE_EXT):
                raise Refused(f"{p.name} contains G-code ({n}): give the model only")
            if n.lower().endswith(".config"):
                txt = z.read(n).decode("utf-8", errors="replace")
                m = re.search(r"^[;\s]*post_process\s*=\s*(\S.*)$", txt, re.M)
                if m and m.group(1).strip():
                    raise Refused(f"{p.name} carries a post-processing script (post_process)")


def _models(inp: Dict[str, Any], work: Path) -> List[Dict[str, Any]]:
    specs = inp.get("models")
    if specs is None and inp.get("model") is not None:
        specs = [inp["model"]]
    if specs is None and inp.get("geometry"):
        specs = [inp["geometry"]]
    if not specs or not isinstance(specs, list):
        raise ValueError("model / models: file name(s) of the study folder (STL, 3MF, OBJ, STEP)")
    out = []
    for s in specs:
        d = dict(s) if isinstance(s, dict) else {"file": s}
        bad = set(d) - {"file", "copies", "rotate_x_deg", "rotate_y_deg", "rotate_z_deg"}
        if bad:
            raise Refused(f"unknown model keys {sorted(bad)}")
        p = _rel_file(work, d.get("file"), MODEL_EXT, "model")
        if p.suffix.lower() == ".3mf":
            _scan_3mf(p)
        d["path"] = p
        out.append(d)
    return out


def _check_params(kind: str, inp: Dict[str, Any]) -> None:
    extra = sorted(set(inp) - _ALLOWED[kind])
    if extra:
        g = [k for k in extra if _GCODE_WORDS.search(k)]
        if g:
            raise Refused(f"{', '.join(g)}: slicing, G-code, scripts and raw command-line "
                          "arguments are never accepted; the person slices in PrusaSlicer")
        raise Refused(f"unknown parameters {extra}; allowed: {sorted(_ALLOWED[kind] - _COMMON_KEYS)}")

    def walk(v):
        if isinstance(v, str):
            if v.strip().startswith("-"):
                raise Refused(f"{v!r} looks like a command-line option: never passed through")
            if v.lower().endswith(_GCODE_EXT):
                raise Refused(f"{v!r}: G-code / sliced output is never written")
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk({k: v for k, v in inp.items() if k not in ("limits", "face_info")})


def _num(v: Any, name: str, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: a number") from None
    if not (math.isfinite(x) and lo <= x <= hi):
        raise ValueError(f"{name}: between {lo:g} and {hi:g}")
    return x


# ------------------------------------------------------------------ verdict
def _verdict(checks: List[Tuple[str, bool]], values: Dict[str, Any], limits, head: str,
             files: List[str], notes: List[str], decisive: bool) -> Dict[str, Any]:
    """Built-in checks (FAIL when one fails) + the caller's limits. `decisive`: the built-in
    checks are enough to say OK (a bed was given); otherwise no limits -> UNKNOWN."""
    values["n_failed_checks"] = sum(1 for _, ok in checks if not ok)
    lines = [f"{n}: {'OK' if ok else 'FAIL'}" for n, ok in checks]
    jv, jl = judge(values, limits) if limits else (None, [])
    if values["n_failed_checks"] or jv == "FAIL":
        verdict = "FAIL"
    elif jv == "UNKNOWN":
        verdict = "UNKNOWN"
    elif decisive or jv == "OK":
        verdict = "OK"
    else:
        verdict = "UNKNOWN"
        notes = [*notes, "no bed_mm nor limits: computed, nothing judged"]
    return {"verdict": verdict, "summary": "; ".join([head, *lines, *jl, *notes]),
            "values": values, "files": files}


def _fits(size: Tuple[float, float, float], bed: List[float], turn: bool = True) -> bool:
    sx, sy, sz = size
    ok_xy = (sx <= bed[0] and sy <= bed[1]) or (turn and sy <= bed[0] and sx <= bed[1])
    return ok_xy and (len(bed) < 3 or sz <= bed[2])


# ------------------------------------------------------------------ print_check
def _print_check(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    models = _models(inp, work)
    bed = inp.get("bed_mm")
    if bed is not None:
        if not isinstance(bed, list) or len(bed) not in (2, 3):
            raise ValueError("bed_mm: [x, y] or [x, y, z] in mm")
        bed = [_num(b, "bed_mm", 1, 5000) for b in bed]
    objs = []
    for m in models:
        for b in _info(m["path"], work):
            b["file"] = m["path"].name
            objs.append(b)
    repair_keys = ("open_edges", "facets_removed", "facets_reversed", "backwards_edges",
                   "degenerate_facets", "edges_fixed", "facets_added")
    values: Dict[str, Any] = {
        "objects": len(objs),
        "volume_mm3": float(sum(o.get("volume", 0.0) for o in objs)),
        "facets": int(sum(o.get("number_of_facets", 0) for o in objs)),
        "parts": int(sum(o.get("number_of_parts", 1) for o in objs)),
        "manifold": int(all(str(o.get("manifold", "")).lower() == "yes" for o in objs)),
        "mesh_errors": int(sum(o.get(k, 0) for o in objs for k in repair_keys
                               if isinstance(o.get(k, 0), float))),
        "open_edges": int(sum(o.get("open_edges", 0) for o in objs)),
    }
    for ax in "xyz":
        values[f"size_{ax}_mm"] = float(max(o.get(f"size_{ax}", 0.0) for o in objs))
    values["per_object"] = [{"file": o["file"], "size_mm": [o.get("size_x"), o.get("size_y"), o.get("size_z")],
                             "volume_mm3": o.get("volume"), "manifold": o.get("manifold")} for o in objs]
    checks = [("manifold (closed, no open edges)", bool(values["manifold"]) and not values["open_edges"])]
    notes = []
    if bed:
        fit = all(_fits((o["size_x"], o["size_y"], o["size_z"]), bed) for o in objs)
        values["fits_bed"] = int(fit)
        checks.append((f"each object fits the bed {' x '.join(f'{b:g}' for b in bed)} mm", fit))
        if len(objs) > 1 and fit:
            placed = _pack([(o["size_x"], o["size_y"]) for o in objs],
                           (0.0, 0.0, bed[0], bed[1]), 6.0, True)
            values["fit_together"] = int(placed is not None)
            notes.append("all objects " + ("fit" if placed else "do NOT fit")
                         + " on one plate (bounding-box packing, 6 mm gaps; advisory)")
    head = (f"print_check: {len(objs)} object(s), {values['size_x_mm']:.4g} x {values['size_y_mm']:.4g}"
            f" x {values['size_z_mm']:.4g} mm, volume {values['volume_mm3']:.6g} mm3, "
            f"manifold {'yes' if values['manifold'] else 'NO'}")
    return _verdict(checks, values, inp.get("limits"), head, [], notes, decisive=bool(bed))


# ------------------------------------------------------------------ profile
def _read_ini(text: str) -> Dict[str, str]:
    cfg: Dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ";")):
            continue
        if s.startswith("["):
            raise Refused("the profile is a config bundle with sections: export ONE profile "
                          "(PrusaSlicer: File > Export > Export Config)")
        if "=" not in s:
            raise ValueError(f"profile: line {s[:60]!r} is not 'key = value'")
        k, v = (x.strip() for x in s.split("=", 1))
        cfg[k] = v
    return cfg


def _check_profile(cfg: Dict[str, str], name: str) -> None:
    for k, v in cfg.items():
        if re.fullmatch(r"post_?process\w*", k, re.I) and v.strip():
            raise Refused(f"{name} sets {k} (a post-processing script): remove it from the profile; "
                          "scripts are never run or embedded")


def _bed_polygon(cfg: Dict[str, str]) -> List[Tuple[float, float]]:
    pts = []
    for pair in cfg.get("bed_shape", "").split(","):
        m = re.fullmatch(r"\s*(-?[\d.]+)x(-?[\d.]+)\s*", pair)
        if m:
            pts.append((float(m.group(1)), float(m.group(2))))
    if len(pts) < 3:
        raise ValueError("the profile has no bed_shape: export the full printer profile")
    return pts


def _inside(poly: List[Tuple[float, float]], x: float, y: float, eps: float = 1e-6) -> bool:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    if len(poly) == 4 and len(set(np.round(xs, 6))) == 2 and len(set(np.round(ys, 6))) == 2:
        return min(xs) - eps <= x <= max(xs) + eps and min(ys) - eps <= y <= max(ys) + eps
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        (xi, yi), (xj, yj) = poly[i], poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


# ------------------------------------------------------------------ meshes (numpy)
def _read_stl(p: Path) -> np.ndarray:
    data = p.read_bytes()
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if len(data) == 84 + 50 * n:
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)),
                                                      ("a", "<u2")]), count=n, offset=84)
            return rec["v"].astype(float)
    nums = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)
    if not nums or len(nums) % 3:
        raise RuntimeError(f"{p.name}: unreadable STL")
    return np.array(nums, dtype=float).reshape(-1, 3, 3)


def _write_stl(p: Path, tri: np.ndarray) -> None:
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    nrm = np.cross(b - a, c - a)
    ln = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = np.divide(nrm, ln, out=np.zeros_like(nrm), where=ln > 0)
    rec = np.zeros(len(tri), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    rec["n"], rec["v"] = nrm, tri
    p.write_bytes(b"mcpfc".ljust(80, b" ") + struct.pack("<I", len(tri)) + rec.tobytes())


def _rot(rx: float, ry: float, rz: float) -> np.ndarray:
    def r(axis, deg):
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        m = np.eye(3)
        i, j = [(1, 2), (2, 0), (0, 1)][axis]
        m[i, i], m[i, j], m[j, i], m[j, j] = c, -s, s, c
        return m
    # X first, then Y, then Z (einsum: no BLAS call for a 3 x 3 product)
    return np.einsum("ij,jk,kl->il", r(2, rz), r(1, ry), r(0, rx), optimize=False)


def _pack(sizes: List[Tuple[float, float]], rect: Tuple[float, float, float, float], gap: float,
          turn: bool) -> Optional[List[Tuple[float, float, bool]]]:
    """Shelf packing of footprints (w, d) in rect (x0, y0, x1, y1): [(x, y, turned)] or None."""
    x0, y0, x1, y1 = rect
    W, D = x1 - x0, y1 - y0
    order = sorted(range(len(sizes)), key=lambda i: -max(sizes[i]))
    place: Dict[int, Tuple[float, float, bool]] = {}
    cx, cy, row = 0.0, 0.0, 0.0
    for i in order:
        w, d = sizes[i]
        turned = False
        if turn and w > W and d <= W:
            w, d, turned = d, w, True
        if cx > 0 and cx + w > W:
            cx, cy, row = 0.0, cy + row + gap, 0.0
        if w > W or cy + d > D:
            return None
        place[i] = (x0 + cx, y0 + cy, turned)
        cx += w + gap
        row = max(row, d)
    return [place[i] for i in range(len(sizes))]


# ------------------------------------------------------------------ print_project
def _print_project(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    if not inp.get("profile"):
        raise Refused("no profile: give the user's PrusaSlicer profile (.ini exported from "
                      "PrusaSlicer) in files and as 'profile'; the printer is never guessed")
    models = _models(inp, work)
    prof = _rel_file(work, inp["profile"], (".ini",), "profile")
    user_cfg = _read_ini(prof.read_text(encoding="utf-8", errors="replace"))
    _check_profile(user_cfg, prof.name)
    out_name = str(inp.get("output", "project.3mf"))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,60}\.3mf", out_name):
        raise Refused("output: a simple file name ending in .3mf (letters, digits, _ -)")
    gap = _num(inp.get("spacing_mm", 6), "spacing_mm", 0, 100)
    turn = bool(inp.get("allow_turn", True))
    # the profile as PrusaSlicer understands it (validated and completed by PrusaSlicer itself)
    norm = work / "_profile_normalized.ini"
    _ps(["--load", prof.name, "--save", norm.name], work)
    cfg = _read_ini(norm.read_text(encoding="utf-8", errors="replace"))
    _check_profile(cfg, "the profile")
    bed = _bed_polygon(cfg)
    zmax = float(cfg.get("max_print_height") or "inf")
    bx = [p[0] for p in bed]
    by = [p[1] for p in bed]
    rect = (min(bx), min(by), max(bx), max(by))
    # every model: converted to STL by PrusaSlicer (repairs, units), oriented, laid flat
    parts_dir = work / "_parts"
    parts_dir.mkdir(exist_ok=True)
    items: List[Tuple[str, np.ndarray]] = []
    for i, m in enumerate(models):
        src = parts_dir / f"src_{i}.stl"
        _ps(["--export-stl", "-o", str(src), str(m["path"])], work)
        tri = _read_stl(src)
        rx = _num(m.get("rotate_x_deg", inp.get("rotate_x_deg", 0)), "rotate_x_deg", -360, 360)
        ry = _num(m.get("rotate_y_deg", inp.get("rotate_y_deg", 0)), "rotate_y_deg", -360, 360)
        rz = _num(m.get("rotate_z_deg", inp.get("rotate_z_deg", 0)), "rotate_z_deg", -360, 360)
        tri = np.einsum("tvj,ij->tvi", tri, _rot(rx, ry, rz), optimize=False)
        tri -= tri.reshape(-1, 3).min(axis=0)
        n = int(_num(m.get("copies", inp.get("copies", 1)), "copies", 1, 100))
        stem = re.sub(r"[^A-Za-z0-9_-]", "_", m["path"].stem)[:40] or "part"
        items += [(f"{stem}_{k + 1}" if n > 1 else stem, tri) for k in range(n)]
    sizes = [tuple(t.reshape(-1, 3).max(axis=0)[:2]) for _, t in items]
    heights = [float(t.reshape(-1, 3)[:, 2].max()) for _, t in items]
    values: Dict[str, Any] = {"refused": 0, "objects": len(items),
                              "bed_x_mm": rect[2] - rect[0], "bed_y_mm": rect[3] - rect[1],
                              "max_height_mm": max(heights)}
    placed = _pack(sizes, rect, gap, turn) if max(heights) <= zmax else None
    if placed is not None:
        xs = [p[0] + (s[1] if p[2] else s[0]) for p, s in zip(placed, sizes)]
        ys = [p[1] + (s[0] if p[2] else s[1]) for p, s in zip(placed, sizes)]
        dx = (rect[2] - max(xs)) / 2          # centre the group on the bed
        dy = (rect[3] - max(ys)) / 2
        placed = [(x + dx, y + dy, t) for x, y, t in placed]
        for (x, y, t), s in zip(placed, sizes):
            w, d = (s[1], s[0]) if t else s
            if not all(_inside(bed, cx, cy) for cx, cy in ((x, y), (x + w, y), (x, y + d), (x + w, y + d))):
                placed = None
                break
    values["fits_bed"] = int(placed is not None)
    if placed is None:
        values["n_failed_checks"] = 1
        why = (f"height {max(heights):.4g} mm > max_print_height {zmax:g} mm" if max(heights) > zmax
               else f"the parts do not fit on the bed {rect[2] - rect[0]:g} x {rect[3] - rect[1]:g} mm "
                    f"with {gap:g} mm gaps")
        return {"verdict": "FAIL", "summary": f"print_project: {why}; no project written "
                f"(fewer copies, another orientation, or split the part)", "values": values, "files": []}
    names = []
    for (name, tri), (x, y, t) in zip(items, placed):
        if t:                                  # 90 deg turn about Z, back into the first quadrant
            tri = np.einsum("tvj,ij->tvi", tri, _rot(0, 0, 90), optimize=False)
            tri -= tri.reshape(-1, 3).min(axis=0)
        tri = tri + np.array([x, y, 0.0])
        f = parts_dir / f"{name}.stl"
        _write_stl(f, tri)
        names.append(str(f))
    out = work / out_name
    out.unlink(missing_ok=True)
    _ps(["--merge", "--dont-arrange", "--export-3mf", "-o", str(out), *names], work, 300)
    if not out.is_file():
        raise RuntimeError("PrusaSlicer wrote no 3MF")
    # embed the profile as PrusaSlicer project settings (Metadata/Slic3r_PE.config)
    body = "; project settings from the user's profile " + prof.name + "\n\n" + "".join(
        f"; {k} = {v}\n" for k, v in cfg.items())
    with zipfile.ZipFile(out, "a", zipfile.ZIP_DEFLATED) as z:
        if "Metadata/Slic3r_PE.config" in z.namelist():
            raise RuntimeError("PrusaSlicer already embedded a config: not overwritten")
        z.writestr("Metadata/Slic3r_PE.config", body)
    # read back: no G-code inside, profile re-read by PrusaSlicer, objects and volume
    gcode = 0
    with zipfile.ZipFile(out) as z:
        for n in z.namelist():
            if n.lower().endswith(_GCODE_EXT):
                gcode += 1
            elif n.lower().endswith((".model", ".xml", ".config", ".rels")):
                txt = z.read(n).decode("utf-8", errors="replace")
                gcode += len(re.findall(r"^\s*G[0-3]\s+[XYZEF]-?\d", txt, re.M))
        members = z.namelist()
    values["gcode_found"] = gcode
    back = work / "_profile_readback.ini"
    back.unlink(missing_ok=True)
    _ps(["--save", back.name, out.name], work)
    cfg_back = _read_ini(back.read_text(encoding="utf-8", errors="replace"))
    mism = sorted(k for k in cfg if cfg_back.get(k) != cfg[k])
    values["profile_keys"] = len(cfg)
    values["profile_mismatch"] = len(mism)
    objs = _info(out, work)
    values["objects_readback"] = len(objs)
    values["volume_mm3"] = float(sum(o.get("volume", 0.0) for o in objs))
    values["manifold"] = int(all(str(o.get("manifold", "")).lower() == "yes" for o in objs))
    values["printer"] = cfg.get("printer_model") or cfg.get("printer_settings_id") or "?"
    checks = [("fits the bed of the profile", True),
              ("no G-code in the project", gcode == 0),
              ("profile read back identical", not mism),
              ("objects read back", len(objs) == len(items)),
              ("manifold", bool(values["manifold"]))]
    head = (f"print_project: {out_name} with {len(items)} object(s) on the bed "
            f"{values['bed_x_mm']:g} x {values['bed_y_mm']:g} mm of profile {prof.name}; "
            f"volume {values['volume_mm3']:.6g} mm3; {len(members)} files inside, no G-code")
    notes = ["open it in PrusaSlicer, slice, check the preview and print yourself: "
             "no G-code is produced here"]
    if mism:
        notes.append("profile keys not read back: " + ", ".join(mism[:8]))
    return _verdict(checks, values, inp.get("limits"), head, [out_name], notes, decisive=True)


# ------------------------------------------------------------------ entry
def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    work = Path(work).resolve()
    try:
        if kind not in _ALLOWED:
            raise ValueError(f"unknown kind {kind!r}")
        _check_params(kind, inp)
        if kind == "print_check":
            return _print_check(inp, work)
        return _print_project(inp, work)
    except Refused as e:
        return _refusal(str(e))


def ref_setup(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    """setup of the reference cases: cubes (binary STL, outward normals) and profile files."""
    make = inp.pop("make")
    for name, a in (make.get("cubes") or {}).items():
        c = np.array([[x, y, z] for x in (0, a) for y in (0, a) for z in (0, a)], dtype=float)
        quads = [(0, 2, 6, 4), (1, 5, 7, 3), (0, 4, 5, 1), (2, 3, 7, 6), (0, 1, 3, 2), (4, 6, 7, 5)]
        tris = []
        centre = c.mean(axis=0)
        for q in quads:
            for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
                p = c[list(t)]
                if np.dot(np.cross(p[1] - p[0], p[2] - p[0]), p.mean(axis=0) - centre) < 0:
                    p = p[[0, 2, 1]]
                tris.append(p)
        _write_stl(work / name, np.array(tris))
    for name, lines in (make.get("ini") or {}).items():
        (work / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return inp


_PROFILE = ["# printer profile of the reference case", "printer_technology = FFF",
            "bed_shape = 0x0,250x0,250x210,0x210", "max_print_height = 210",
            "layer_height = 0.2", "nozzle_diameter = 0.4", "post_process = "]
REFERENCES = [
    {   # 20 mm cube: 8000 mm3, closed
        "name": "cube20_info", "kind": "print_check", "setup": "ref_setup",
        "inp": {"make": {"cubes": {"cube20.stl": 20}}, "model": "cube20.stl", "bed_mm": [250, 210, 210]},
        "expect": {"volume_mm3": [8000, 1e-4], "manifold": [1, 0], "size_x_mm": [20, 1e-6],
                   "fits_bed": [1, 0], "n_failed_checks": [0, 0]},
    },
    {   # a 300 mm cube on a 250 x 210 x 210 bed: FAIL (one failed check: the bed)
        "name": "cube300_exceeds_bed", "kind": "print_check", "setup": "ref_setup",
        "inp": {"make": {"cubes": {"cube300.stl": 300}}, "model": "cube300.stl", "bed_mm": [250, 210, 210]},
        "expect": {"fits_bed": [0, 0], "n_failed_checks": [1, 0], "volume_mm3": [2.7e7, 1e-4]},
    },
    {   # project 3MF: 2 copies arranged, profile embedded and re-read, no G-code inside
        "name": "project_two_cubes", "kind": "print_project", "setup": "ref_setup",
        "inp": {"make": {"cubes": {"cube20.stl": 20}, "ini": {"printer.ini": _PROFILE}},
                "model": "cube20.stl", "copies": 2, "profile": "printer.ini"},
        "expect": {"objects_readback": [2, 0], "volume_mm3": [16000, 1e-4], "gcode_found": [0, 0],
                   "profile_mismatch": [0, 0], "fits_bed": [1, 0], "refused": [0, 0]},
    },
    {   # a post-processing script in the profile: refused, nothing written
        "name": "refuse_post_process", "kind": "print_project", "setup": "ref_setup",
        "inp": {"make": {"cubes": {"cube20.stl": 20},
                         "ini": {"bad.ini": _PROFILE[:-1] + ["post_process = C:\\scripts\\upload.py"]}},
                "model": "cube20.stl", "profile": "bad.ini"},
        "expect": {"refused": [1, 0]},
    },
    {   # a raw argument list asking for G-code: refused
        "name": "refuse_gcode_args", "kind": "print_project", "setup": "ref_setup",
        "inp": {"make": {"cubes": {"cube20.stl": 20}, "ini": {"printer.ini": _PROFILE}},
                "model": "cube20.stl", "profile": "printer.ini", "extra_args": ["--export-gcode"]},
        "expect": {"refused": [1, 0]},
    },
    {   # no profile: refused (the printer is never guessed)
        "name": "refuse_no_profile", "kind": "print_project", "setup": "ref_setup",
        "inp": {"make": {"cubes": {"cube20.stl": 20}}, "model": "cube20.stl"},
        "expect": {"refused": [1, 0]},
    },
]
