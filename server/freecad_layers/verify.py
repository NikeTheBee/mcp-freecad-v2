"""Geometric verification — deterministic verdicts (cahier des charges §7.2).

The AI validates its output before continuing and gets a short text verdict
(token-efficient) rather than a screenshot. The *code* decides, never the AI.

Three outcomes, never a silent pass (audit 2026-10-11, §3.4):
  OK       every check ran and none found a problem;
  FAIL     at least one check found a problem;
  UNKNOWN  a check could not run (e.g. too many solids for the pairwise
           interference test) — treat as "not verified", never as OK.

Checks: recompute state of the object and of everything it depends on
(a failed recompute keeps the LAST GOOD shape — that must not pass), shape
validity, presence of a solid, closed shells, inverted/degenerate volume,
OCCT BOP self-intersection check (it RAISES on problems), one-solid rule
for PartDesign, and pairwise interference between the solids of a compound.
Also returns kernel measurements (volume, bounding box, centre of mass,
topology counts) so callers can compare them with the requested design.
"""
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional

import FreeCAD  # available inside FreeCAD / freecadcmd

OK, FAIL, UNKNOWN = "OK", "FAIL", "UNKNOWN"

# Above this many solids the O(n²) interference test is skipped (-> UNKNOWN).
MAX_INTERFERENCE_SOLIDS = 200

_BAD_STATES = ("Invalid", "Touched", "Error")


def _resolve(obj_or_name, doc=None):
    if isinstance(obj_or_name, str):
        doc = doc or FreeCAD.ActiveDocument
        if doc is None:
            raise ValueError("No active document to resolve object by name")
        o = doc.getObject(obj_or_name) or next(
            iter(doc.getObjectsByLabel(obj_or_name)), None)
        if o is None:
            raise ValueError(f"No object named {obj_or_name!r}")
        return o
    return obj_or_name


def _bad_state(obj) -> List[str]:
    return [s for s in (getattr(obj, "State", None) or []) if s in _BAD_STATES]


def _state_issues(obj) -> List[str]:
    """Recompute problems on the object itself and on its dependencies."""
    issues = []
    bad = _bad_state(obj)
    if bad or (hasattr(obj, "isValid") and not obj.isValid()):
        issues.append(f"recompute state {bad or ['invalid']}: the shape is stale "
                      "(last good result, not the current design)")
    for dep in getattr(obj, "OutListRecursive", []) or []:
        dbad = _bad_state(dep)
        if dbad:
            issues.append(f"dependency {dep.Name} is {dbad}")
    return issues


def interference(shapes: Iterable, tol: float = 1e-6,
                 names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Pairwise overlap volume between solids/shapes (bounding-box pre-filter).

    Returns [{a, b, volume}] sorted by decreasing overlap, only pairs whose
    common volume exceeds `tol` mm³. Touching parts (shared faces) do not count.
    """
    shapes = list(shapes)
    names = names or [f"solid{i + 1}" for i in range(len(shapes))]
    hits = []
    for (i, a), (j, b) in combinations(enumerate(shapes), 2):
        if not a.BoundBox.intersect(b.BoundBox):
            continue
        v = float(a.common(b).Volume)
        if v > tol:
            hits.append({"a": names[i], "b": names[j], "volume": round(v, 4)})
    return sorted(hits, key=lambda h: -h["volume"])


def check(obj_or_name, doc=None, min_volume: float = 1e-6,
          expect_solid: bool = True, interference_tol: float = 1e-6) -> Dict[str, Any]:
    """Run every check on an object; return a structured result.

    Keys: status (OK/FAIL/UNKNOWN), ok (status == OK), name, type, valid,
    solids, closed, volume, bbox [x, y, z], center_of_mass, faces, edges,
    vertexes, issues (problems found), unchecked (checks that could not run).
    `expect_solid=False` is for sketches/wires, where "no solid" is expected.
    """
    obj = _resolve(obj_or_name, doc)
    res: Dict[str, Any] = {"name": getattr(obj, "Name", "?"),
                           "type": getattr(obj, "TypeId", "?"),
                           "issues": [], "unchecked": []}
    res["issues"] += _state_issues(obj)

    shp = getattr(obj, "Shape", None)
    if shp is None or shp.isNull():
        res.update(valid=False, solids=0, closed=False, volume=0.0)
        res["issues"].append("no shape / null shape")
        return _finish(res)

    valid = bool(shp.isValid())
    solids = list(shp.Solids)
    volume = float(shp.Volume)
    closed = (all(s.isClosed() for s in solids) if solids else bool(shp.isClosed()))
    bb = shp.BoundBox
    res.update(valid=valid, solids=len(solids), closed=closed, volume=volume,
               bbox=[round(bb.XLength, 4), round(bb.YLength, 4), round(bb.ZLength, 4)],
               faces=len(shp.Faces), edges=len(shp.Edges), vertexes=len(shp.Vertexes))
    if solids:
        total = sum(s.Volume for s in solids) or 1.0
        com = sum((s.CenterOfMass * (s.Volume / total) for s in solids),
                  FreeCAD.Vector())
        res["center_of_mass"] = [round(com.x, 4), round(com.y, 4), round(com.z, 4)]

    if not valid:
        res["issues"].append("shape.isValid() is False")
    if expect_solid and not solids:
        res["issues"].append("no solid (expected a solid body)")
    if any(s.Volume < 0 for s in solids):
        res["issues"].append("inverted solid (negative volume)")
    if solids and volume <= min_volume:
        res["issues"].append(f"degenerate volume ({volume:.3g})")
    if solids and not closed:
        res["issues"].append("solid is not closed (not watertight)")
    if len(solids) > 1 and str(res["type"]).startswith("PartDesign::"):
        res["issues"].append(f"PartDesign result has {len(solids)} solids (must be 1)")

    # OCCT BOP check. It RAISES with the list of problems when the shape is
    # bad and returns None when clean — the old code swallowed the exception,
    # so self-intersections were never reported. Run it per solid: on a
    # compound it also flags solids that merely TOUCH; real overlaps between
    # solids are measured by the interference test below.
    parts = [(f"solid{i + 1} ", s) for i, s in enumerate(solids)] if len(solids) > 1 \
        else [("", shp)]
    for label, s in parts:
        try:
            s.check(True)
        except Exception as e:  # noqa: BLE001 — the message IS the finding
            res["issues"].append(f"{label}topology/self-intersection: "
                                 + " ".join(str(e).split())[:160])

    if len(solids) > 1:
        if len(solids) > MAX_INTERFERENCE_SOLIDS:
            res["unchecked"].append(f"interference ({len(solids)} solids > "
                                    f"{MAX_INTERFERENCE_SOLIDS})")
        else:
            for h in interference(solids, interference_tol):
                res["issues"].append(f"interference {h['a']}/{h['b']}: "
                                     f"{h['volume']:.4g} mm3")
    return _finish(res)


def _finish(res: Dict[str, Any]) -> Dict[str, Any]:
    if res["issues"]:
        res["status"] = FAIL
    elif res["unchecked"]:
        res["status"] = UNKNOWN
    else:
        res["status"] = OK
    res["ok"] = res["status"] == OK
    return res


def verdict(obj_or_name, doc=None, **kw) -> str:
    """One-line, token-minimal verdict string: 'OK|FAIL|UNKNOWN name: ...'."""
    r = check(obj_or_name, doc, **kw)
    base = (f"{r['status']} {r['name']}: valid={r['valid']} solids={r['solids']} "
            f"closed={r['closed']} V={r['volume']:.6g}")
    if r.get("bbox"):
        base += " bbox={}x{}x{}".format(*(f"{x:.4g}" for x in r["bbox"]))
    if r["issues"]:
        base += " | " + "; ".join(r["issues"])
    if r["unchecked"]:
        base += " | NOT CHECKED: " + "; ".join(r["unchecked"])
    return base


def watertight(obj_or_name, doc=None) -> bool:
    """True only if every check passed on a closed solid — gate STL export."""
    r = check(obj_or_name, doc)
    return r["ok"] and r["solids"] >= 1 and r["closed"]


def sketch_check(obj_or_name, doc=None, require_closed: bool = True,
                 require_fully_constrained: bool = False) -> Dict[str, Any]:
    """Sketch health before a Pad/Pocket: conflicting / redundant / malformed
    constraints, solver result, degrees of freedom, open wires.

    Uses `DoF` / `FullyConstrained`, NOT `solve()`'s return code: solve()
    returns 0 for an under-constrained sketch (the base's verify_sketch reads
    that 0 as "fully constrained" — reported upstream).
    Returns {status, issues, advisories, dof, wires_closed, wires_open}.
    """
    sk = _resolve(obj_or_name, doc)
    if getattr(sk, "TypeId", "") != "Sketcher::SketchObject":
        raise ValueError(f"{getattr(sk, 'Name', sk)!r} is not a sketch")
    issues, advisories = [], []
    code = sk.solve()
    if code < 0:
        issues.append(f"solver failed (code {code})")
    for label, ids in (("conflicting", sk.ConflictingConstraints),
                       ("redundant", sk.RedundantConstraints),
                       ("partially redundant", sk.PartiallyRedundantConstraints),
                       ("malformed", sk.MalformedConstraints)):
        if ids:
            issues.append(f"{label} constraints {list(ids)}")
    dof = int(getattr(sk, "DoF", -1))
    if dof > 0:
        (issues if require_fully_constrained else advisories).append(
            f"under-constrained: {dof} degree(s) of freedom")
    wires = sk.Shape.Wires if not sk.Shape.isNull() else []
    closed = sum(1 for w in wires if w.isClosed())
    opened = len(wires) - closed
    if require_closed and opened:
        issues.append(f"{opened} open wire(s): cannot Pad/Pocket")
    if require_closed and not closed:
        issues.append("no closed profile")
    issues += _state_issues(sk)
    return {"status": FAIL if issues else OK, "name": sk.Name, "issues": issues,
            "advisories": advisories, "dof": dof, "wires_closed": closed,
            "wires_open": opened}


def sketch_verdict(obj_or_name, doc=None, **kw) -> str:
    r = sketch_check(obj_or_name, doc, **kw)
    line = (f"{r['status']} sketch {r['name']}: DoF={r['dof']} closed_wires={r['wires_closed']} "
            f"open_wires={r['wires_open']}")
    if r["issues"]:
        line += " | " + "; ".join(r["issues"])
    if r["advisories"]:
        line += " | note: " + "; ".join(r["advisories"])
    return line


def check_assembly(objects: Iterable, tol: float = 1e-6) -> Dict[str, Any]:
    """Interference between distinct parts of an assembly (by object).

    `objects`: document objects (or names in the active document). Returns
    {status, parts, interferences: [{a, b, volume}]}; FAIL if any pair overlaps.
    """
    objs = [_resolve(o) for o in objects]
    shapes = [o.Shape for o in objs]
    hits = interference(shapes, tol, names=[o.Name for o in objs])
    return {"status": FAIL if hits else OK, "parts": len(objs), "interferences": hits}
