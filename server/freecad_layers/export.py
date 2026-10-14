"""Verified export: STL / 3MF for printing, STEP / BREP / IGES for other CAD.

A file leaves only for a part that `check` passes (geometry AND the user's
requirements): the code decides, not the AI. The file is then READ BACK and
compared with the solid it came from (volume, size and position, closed and
consistently oriented mesh, solid count). A file that does not match is never
handed over: it is written under a temporary name and only renamed once it
passed, so a failed export leaves no file behind and never replaces a good one.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import FreeCAD

from . import gate, project_dir, safe_out_path, verify

MESH = (".stl", ".3mf")
EXACT = (".step", ".stp", ".brep", ".brp")
SURFACES = (".iges", ".igs")
FORMATS = MESH + EXACT + SURFACES

# Triangles: at most 0.2 rad between neighbours (a D4 hole is off by 0.01 mm at
# most) and a chord error of 1e-4 of the part's diagonal, kept between 0.005 and
# 0.05 mm: finer than FDM and resin printers. 0.1 rad doubled the triangles and
# the meshing time (43 s -> 109 s on a plate with 1,600 holes) for nothing printable.
ANGULAR_DEFLECTION = 0.2
MESH_VOLUME_TOL = 0.01      # relative: a valid solid's mesh stays far below
EXACT_VOLUME_TOL = 1e-6     # relative: STEP/BREP keep the exact geometry
# the self-intersection test takes 3 s on 800,000 triangles; above this, said, not run
MAX_SELF_INTERSECTION_FACETS = 2_000_000


def deflection_for(bb) -> float:
    return min(0.05, max(0.005, bb.DiagonalLength * 1e-4))


def _ext(path: Optional[str], fmt: str) -> str:
    if path:
        ext = Path(path).suffix.lower()
    else:
        ext = "." + str(fmt or "").lower().lstrip(".")
    if ext not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(e[1:] for e in FORMATS)} (got {ext!r})")
    return ext


def _bbox(bb) -> List[float]:
    return [round(bb.XLength, 4), round(bb.YLength, 4), round(bb.ZLength, 4)]


def _box_problem(got, want, tol: float) -> Optional[str]:
    """Size AND position: a part shifted on export is not the same part."""
    pairs = (("XMin", got.XMin, want.XMin), ("YMin", got.YMin, want.YMin), ("ZMin", got.ZMin, want.ZMin),
             ("XMax", got.XMax, want.XMax), ("YMax", got.YMax, want.YMax), ("ZMax", got.ZMax, want.ZMax))
    off = [(k, a - b) for k, a, b in pairs if abs(a - b) > tol]
    if off:
        return "bounding box differs from the solid: " + ", ".join(f"{k} {d:+.4g} mm" for k, d in off)
    return None


def _write_mesh(shp, tmp: Path, defl: float) -> None:
    import MeshPart
    m = MeshPart.meshFromShape(Shape=shp, LinearDeflection=defl,
                               AngularDeflection=ANGULAR_DEFLECTION, Relative=False)
    m.write(str(tmp))


def _verify_mesh(tmp: Path, shp, defl: float) -> Dict[str, Any]:
    import Mesh
    m = Mesh.Mesh(str(tmp))
    problems, unchecked = [], []
    n = int(m.CountFacets)
    if n == 0:
        return {"problems": ["the file holds no triangle"], "unchecked": [], "facets": 0}
    if not m.isSolid():
        problems.append("mesh is not closed (not watertight)")
    if m.hasNonManifolds():
        problems.append("non-manifold edges")
    if m.hasNonUniformOrientedFacets():
        problems.append("inconsistently oriented triangles")
    if n <= MAX_SELF_INTERSECTION_FACETS:
        if m.hasSelfIntersections():
            problems.append("self-intersecting triangles")
    else:
        unchecked.append(f"self-intersection ({n} triangles > {MAX_SELF_INTERSECTION_FACETS})")
    v, want = float(m.Volume), float(shp.Volume)
    if v <= 0:
        problems.append(f"volume {v:.6g} mm3: triangles face inwards")
    elif abs(v - want) > MESH_VOLUME_TOL * want:
        problems.append(f"volume {v:.6g} mm3 vs solid {want:.6g} ({100 * (v - want) / want:+.3g} %)")
    box = _box_problem(m.BoundBox, shp.BoundBox, 2 * defl + 1e-3)
    if box:
        problems.append(box)
    return {"problems": problems, "unchecked": unchecked, "facets": n, "volume": v,
            "bbox": _bbox(m.BoundBox)}


def _write_exact(shp, tmp: Path, ext: str) -> None:
    if ext in (".step", ".stp"):
        shp.exportStep(str(tmp))
    elif ext in (".brep", ".brp"):
        shp.exportBrep(str(tmp))
    else:
        shp.exportIges(str(tmp))


def _verify_exact(tmp: Path, shp, ext: str) -> Dict[str, Any]:
    import Part
    s = Part.read(str(tmp))
    problems = []
    if s.isNull() or not s.Faces:
        return {"problems": ["the file holds no geometry"], "unchecked": []}
    box = _box_problem(s.BoundBox, shp.BoundBox, 1e-4)
    if box:
        problems.append(box)
    out: Dict[str, Any] = {"problems": problems, "unchecked": [], "bbox": _bbox(s.BoundBox)}
    if ext in SURFACES:       # IGES carries the faces, not the solid: compare those
        a, want = float(s.Area), float(shp.Area)
        if abs(a - want) > EXACT_VOLUME_TOL * want + 1e-9:
            problems.append(f"surface area {a:.6g} mm2 vs part {want:.6g}")
        if len(s.Faces) != len(shp.Faces):
            problems.append(f"{len(s.Faces)} faces vs {len(shp.Faces)} in the part")
        out.update(faces=len(s.Faces), area=a)
        return out
    if len(s.Solids) != len(shp.Solids):
        problems.append(f"{len(s.Solids)} solid(s) in the file vs {len(shp.Solids)} in the part")
    v, want = float(s.Volume), float(shp.Volume)
    if abs(v - want) > EXACT_VOLUME_TOL * want + 1e-9:
        problems.append(f"volume {v:.9g} mm3 vs solid {want:.9g}")
    if not s.isValid():
        problems.append("the shape read back is not valid")
    out.update(solids=len(s.Solids), volume=v)
    return out


def export(name, path: Optional[str] = None, fmt: str = "stl",
           overwrite: bool = False) -> Dict[str, Any]:
    """Gate on `check`, write, read back, compare. Raises ValueError on refusal."""
    obj = verify._resolve(name)
    if path and not Path(path).suffix:          # "bracket" + format stl -> bracket.stl
        path = f"{path}.{str(fmt or '').lower().lstrip('.')}"
    ext = _ext(path, fmt)
    if obj.TypeId == "Sketcher::SketchObject":
        raise ValueError(f"{obj.Name} is a sketch: export the solid made from it")
    # the name as given: a requirement may be recorded under the label
    g = gate.check_target(name if isinstance(name, str) else obj.Name, autosave=False)
    if g["status"] != verify.OK:
        raise ValueError(f"not exported: check says {g['status']} (an export needs OK)\n{g['text']}")
    shp = obj.Shape
    out = safe_out_path(path or project_dir() / "exports" / f"{obj.Name}{ext}", FORMATS,
                        overwrite=overwrite)
    # the final name in a private folder (one per call: async jobs run beside
    # inline ones): Mesh.write and Part pick the format from the extension, and
    # a STEP header records the file name
    tmpdir = Path(tempfile.mkdtemp(prefix=".export-", dir=out.parent))
    tmp = tmpdir / out.name
    defl = deflection_for(shp.BoundBox)
    try:
        if ext in MESH:
            _write_mesh(shp, tmp, defl)
            r = _verify_mesh(tmp, shp, defl)
        else:
            _write_exact(shp, tmp, ext)
            r = _verify_exact(tmp, shp, ext)
        if r["problems"]:
            raise ValueError(f"not exported: the {ext[1:].upper()} file read back does not match "
                             f"{obj.Name}: " + "; ".join(r["problems"]))
        os.replace(tmp, out)
    except BaseException:
        if not overwrite:               # the empty name reserved by safe_out_path
            out.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    r.update(status=verify.UNKNOWN if r["unchecked"] else verify.OK, path=str(out),
             format=ext[1:].upper(), name=obj.Name, bytes=out.stat().st_size,
             solid_volume=float(shp.Volume), solid_bbox=_bbox(shp.BoundBox))
    if ext in MESH:
        r["deflection_mm"] = defl
    if ext in SURFACES:
        r["advisory"] = "IGES carries the surfaces, not a solid: prefer STEP for another CAD"
    return r


def summary(r: Dict[str, Any]) -> str:
    """One line: what was written, and how it compares with the solid."""
    box = "x".join(f"{x:.4g}" for x in r["bbox"])
    if "facets" in r:
        dv = 100 * (r["volume"] - r["solid_volume"]) / r["solid_volume"]
        what = (f"{r['facets']} triangles (chord <= {r['deflection_mm']:.3g} mm), closed, "
                f"volume {r['volume']:.6g} mm3 ({dv:+.3f} % vs the solid)")
    elif "faces" in r:
        what = f"{r['faces']} faces, area {r['area']:.6g} mm2 (same as the part)"
    else:
        what = f"{r['solids']} solid(s), volume {r['volume']:.6g} mm3 (same as the part)"
    line = f"{r['status']} {r['format']} {r['name']} -> {r['path']}: {what}, bbox {box} mm"
    if r["unchecked"]:
        line += " | NOT CHECKED: " + "; ".join(r["unchecked"])
    if r.get("advisory"):
        line += " | " + r["advisory"]
    return line
