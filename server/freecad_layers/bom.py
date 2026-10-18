"""Bill of materials — extract a parts list from an assembly (GAP G6).

Closes the "conception -> dossier de fabrication" loop: the pince project
shipped a Nomenclature; a driven design should be able to emit one.

What counts as a part (audit 2026-10-11, §2.2 B1):
- a PartDesign::Body is ONE part — its internal features are not parts;
- an object consumed by another shape feature (the Base/Tool of a Cut, the
  source of a Mirror...) is not a part — the result is;
- containers (App::Part, groups, Part::Compound, assemblies) are not parts —
  their children are;
- each App::Link is one more instance of the object it points to.
Identical parts are grouped by measured geometry (volume, area, principal
moments of inertia: the same wherever and however the part is placed) AND
name stem: a box and a cylinder of equal volume are never merged. Limitation: mirror images with the same name stem group
together (equal measurements).

Secure by design: CSV export goes through `safe_out_path` (extension enforced,
no silent clobber). Reading only otherwise — no code execution on labels.
"""
from __future__ import annotations

import csv
import re
from typing import Any, Dict, List, Optional

import FreeCAD as App

from . import safe_out_path, verify

# Grouping objects: their children are the parts.
_CONTAINERS = ("App::Part", "App::DocumentObjectGroup", "App::Origin",
               "Part::Compound", "Assembly::AssemblyObject", "Assembly::JointGroup",
               "Assembly::AssemblyLink")


def _label_stem(label: str) -> str:
    """'Bracket001' / 'Bracket_2' -> 'Bracket' so instances group together."""
    return re.sub(r"[ _]?\d+$", "", label).strip() or label


def _is_container(o) -> bool:
    return o.TypeId in _CONTAINERS or o.TypeId.startswith("Assembly::")


def _is_link(o) -> bool:
    return o.TypeId in ("App::Link", "App::LinkElement")


def _flatten(val) -> List[Any]:
    out = []
    if isinstance(val, (list, tuple)):
        for v in val:
            out += _flatten(v)
    elif val is not None and hasattr(val, "TypeId"):
        out.append(val)
    return out


def _consumers(o) -> List[Any]:
    """Shape features that use `o` as an input through a link property
    (not containers, links, drawings, or mere expression references)."""
    out = []
    for x in o.InList:
        if _is_container(x) or _is_link(x) or not hasattr(x, "Shape"):
            continue
        if x.TypeId.startswith(("TechDraw::", "Spreadsheet::", "Sketcher::", "Fem::")):
            continue                         # a FEM mesh/constraint does not consume the part
        for prop in x.PropertiesList:
            if prop.startswith("_") or prop == "ExpressionEngine":
                continue
            if "Link" not in x.getTypeIdOfProperty(prop):
                continue
            try:
                if o in _flatten(getattr(x, prop)):
                    out.append(x)
                    break
            except Exception:  # noqa: BLE001 — exotic property, ignore
                continue
    return out


def _solid_shape(o):
    shp = getattr(o, "Shape", None)
    # Some objects (e.g. a FEM mesh) have a "Shape" LINK to another object, not a shape.
    if shp is None or not hasattr(shp, "isNull") or shp.isNull() or not shp.Solids:
        return None
    return shp


def _own_size(shp) -> List[float]:
    """Sorted bounding-box size of the part in its OWN frame (placement
    removed): the size of the part, not of its envelope once turned."""
    local = shp.copy()
    local.Placement = App.Placement()
    bb = verify.box(local)
    return sorted(round(x, 2) for x in (bb.XLength, bb.YLength, bb.ZLength))


def _signature(shp) -> tuple:
    """What makes two parts identical, wherever and however they are placed:
    volume, area and the principal moments of inertia of each solid. The
    global bounding box used before changed with orientation, so 4 identical
    screws turned 4 ways came out as 3 different parts."""
    moments = sorted(float(m) for s in shp.Solids for m in s.PrincipalProperties["Moments"])
    return (float(shp.Volume), float(shp.Area), *moments)


def _same_signature(a: tuple, b: tuple, rel: float = 1e-6) -> bool:
    """Signatures equal within `rel`. Rounded keys split identical parts whose
    value sits on a rounding boundary: 40 copies of a 12.5x10.5x2.5 plate
    (328.125 mm3), just turned, read 328.12 or 328.13 -- two rows, 15 + 25."""
    return len(a) == len(b) and all(abs(x - y) <= rel * max(abs(x), abs(y), 1.0)
                                    for x, y in zip(a, b))


def _frame_points(s):
    """The solid's vertices in its principal frame (right-handed), or None when
    that frame is not unique (two equal moments: a turned part, a cube) or the
    part is too big to compare point by point."""
    import numpy as np

    p = s.PrincipalProperties
    m = sorted(p["Moments"])
    if min(m[1] - m[0], m[2] - m[1]) <= 1e-6 * max(abs(m[2]), 1e-12) or len(s.Vertexes) > 2000:
        return None
    a1, a2 = p["FirstAxisOfInertia"], p["SecondAxisOfInertia"]
    axes = np.array([tuple(a1), tuple(a2), tuple(a1.cross(a2))])
    c = s.CenterOfMass
    pts = np.array([(v.Point.x - c.x, v.Point.y - c.y, v.Point.z - c.z) for v in s.Vertexes])
    return pts @ axes.T, list(p["Moments"])        # moments in the axes' order


def _matches(p, q, tol: float) -> bool:
    """Every point of p has a point of q within tol (by rows of 256: a
    2000-vertex part stays within ~12 MB)."""
    import numpy as np

    for i in range(0, len(p), 256):
        d = np.abs(p[i:i + 256, None, :] - q[None, :, :]).max(axis=2)
        if not (d.min(axis=1) < tol).all():
            return False
    return True


def _handedness(a, b) -> Optional[str]:
    """"same" if solid b is solid a turned and moved, "mirror" if it is a's
    mirror image, None if neither or not decidable. Two parts with the same
    volume, area and moments of inertia can still be a left and a right hand:
    not one part ordered twice."""
    import numpy as np

    fa, fb = _frame_points(a), _frame_points(b)
    if fa is None or fb is None:
        return None
    (pa, ma), (pb, mb) = fa, fb
    # pair the axes by moment (each frame lists its axes in its own order)
    ia, ib = np.argsort(ma), np.argsort(mb)
    pa, pb = pa[:, ia], pb[:, ib]
    if len(pa) != len(pb):
        return None
    tol = 1e-4 * max(float(np.abs(pa).max()), 1.0)
    found = None
    for signs in ((1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1),
                  (-1, -1, -1), (-1, 1, 1), (1, -1, 1), (1, 1, -1)):
        q = pa * np.array(signs)
        if _matches(q, pb, tol) and _matches(pb, q, tol):
            # the frames' own handedness enters too: det of the axis pairing
            proper = np.prod(signs) * np.linalg.det(np.eye(3)[ia]) * np.linalg.det(np.eye(3)[ib]) > 0
            if proper:
                return "same"
            found = "mirror"
    return found


def _parts(doc, include_hidden: bool):
    """Yield (instance_object, part_object) for every part instance."""
    for o in doc.Objects:
        if _is_container(o):
            continue
        if not include_hidden and getattr(o, "Visibility", True) is False and not _is_link(o):
            continue
        if _is_link(o):
            tgt = o.getLinkedObject(True)
            if tgt is not None and tgt is not o and _solid_shape(tgt) is not None:
                n = int(getattr(o, "ElementCount", 0) or 0)
                for _ in range(max(n, 1)):
                    yield o, tgt
            continue
        if o.TypeId.startswith(("PartDesign::", "Sketcher::")) and o.TypeId != "PartDesign::Body":
            continue                         # features/sketches live inside a Body
        if o.TypeId.startswith("Fem::") or re.match(r"FEM_.+_Dir\d+$", o.Name):
            continue                         # analysis objects and fem.py's load-direction helpers
        if _solid_shape(o) is None or _consumers(o):
            continue
        linked_only = (getattr(o, "Visibility", True) is False and
                       any(_is_link(x) for x in o.InList))
        if linked_only:
            continue                         # hidden source of App::Link instances
        yield o, o


def collect(doc, include_hidden: bool = True) -> List[Dict[str, Any]]:
    """Grouped parts list. Each row: {item, designation, qty, volume_mm3,
    bbox_mm (sorted), refs}."""
    groups: List[Dict[str, Any]] = []
    by_stem: Dict[str, List[int]] = {}
    for inst, part in _parts(doc, include_hidden):
        shp = _solid_shape(part)
        stem = _label_stem(part.Label)
        sig = _signature(shp)
        same = [i for i in by_stem.get(stem, ()) if _same_signature(groups[i]["_sig"], sig)]
        # same measures can still be a left and a right hand: those are two parts
        g = None
        for i in same:
            other = groups[i]["_shape"]
            h = (_handedness(other.Solids[0], shp.Solids[0])
                 if len(shp.Solids) == 1 and len(other.Solids) == 1 else None)
            if h != "mirror":
                g = groups[i]                  # the same part (or not decidable: as before)
                break
        if g is None:
            by_stem.setdefault(stem, []).append(len(groups))
            groups.append({"designation": stem + (" (mirrored)" if same else ""),
                           "volume_mm3": round(float(shp.Volume), 2),
                           "bbox_mm": _own_size(shp),
                           "qty": 1, "refs": [inst.Name], "_sig": sig, "_shape": shp})
        else:
            g["qty"] += 1
            g["refs"].append(inst.Name)
    return [{"item": i, **{k: v for k, v in g.items() if not k.startswith("_")}}
            for i, g in enumerate(groups, 1)]


def _csv_safe(value: Any) -> Any:
    """Neutralize CSV formula injection: labels come from documents (external
    data); a designation like '=HYPERLINK(...)' must not execute in Excel."""
    s = str(value)
    if s and s.lstrip()[:1] in ("=", "+", "-", "@") or s[:1] in ("\t", "\r"):
        return "'" + s
    return value


def export_csv(rows: List[Dict[str, Any]], path: str,
               overwrite: bool = False) -> str:
    """Write the BOM to a CSV nomenclature (secure output gate)."""
    out = safe_out_path(path, {".csv"}, overwrite=overwrite)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Item", "Designation", "Qty", "Volume_mm3", "Size_mm", "Refs"])
        for r in rows:
            w.writerow([r["item"], _csv_safe(r["designation"]), r["qty"],
                        r["volume_mm3"], "x".join(str(d) for d in r["bbox_mm"]),
                        _csv_safe(";".join(r["refs"]))])
    return str(out)


def bom_for(doc, csv_path: Optional[str] = None,
            overwrite: bool = False) -> Dict[str, Any]:
    """One call: collect the BOM and optionally export CSV. Returns a summary."""
    rows = collect(doc)
    out = export_csv(rows, csv_path, overwrite=overwrite) if csv_path else None
    return {"lines": len(rows),
            "total_parts": sum(r["qty"] for r in rows),
            "rows": rows, "csv": out}


def verdict(summary: Dict[str, Any]) -> str:
    """Token-minimal one-liner."""
    return (f"BOM: {summary['lines']} distinct part(s), "
            f"{summary['total_parts']} total"
            + (f" -> {summary['csv']}" if summary.get("csv") else ""))
