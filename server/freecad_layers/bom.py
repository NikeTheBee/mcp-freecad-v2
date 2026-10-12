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
Identical parts are grouped by measured geometry (volume, area, sorted
bounding-box dimensions) AND name stem: a box and a cylinder of equal volume
are never merged. Limitation: mirror images with the same name stem group
together (equal measurements).

Secure by design: CSV export goes through `safe_out_path` (extension enforced,
no silent clobber). Reading only otherwise — no code execution on labels.
"""
from __future__ import annotations

import csv
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from . import safe_out_path

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


def _signature(shp) -> tuple:
    bb = shp.BoundBox
    dims = sorted(round(x, 2) for x in (bb.XLength, bb.YLength, bb.ZLength))
    return (round(float(shp.Volume), 2), round(float(shp.Area), 2), *dims)


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
    groups: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
    for inst, part in _parts(doc, include_hidden):
        shp = _solid_shape(part)
        stem = _label_stem(part.Label)
        key = (stem, _signature(shp))
        g = groups.get(key)
        if g is None:
            groups[key] = {"designation": stem,
                           "volume_mm3": round(float(shp.Volume), 2),
                           "bbox_mm": list(key[1][2:]),
                           "qty": 1, "refs": [inst.Name]}
        else:
            g["qty"] += 1
            g["refs"].append(inst.Name)
    return [{"item": i, **g} for i, g in enumerate(groups.values(), 1)]


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
