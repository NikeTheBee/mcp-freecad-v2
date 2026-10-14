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
for PartDesign, every feature of a Body in its shape (none after the Tip, none
suppressed), and pairwise interference between the solids of a compound.
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

# Last shapes given to the OCCT check, with its outcome. The analysis can take
# 30 s on a heavy part and printability/analyze re-check a part `check` just
# passed. A hit needs the SAME OCCT data (isEqual: same TShape, placement and
# orientation): any edit or recompute makes a new TShape. Holding the shape
# here keeps its TShape alive, so its address cannot be reused by another.
_SHAPE_CHECKS: List[tuple] = []
_SHAPE_CHECKS_SIZE = 4


def _occt_check(s) -> Optional[str]:
    """shape.check(True): None when clean, else the problems it raised."""
    # tolerances can be changed in place (setTolerance/fixTolerance): part of the key
    tols = (s.getTolerance(1), s.getTolerance(0))
    for cached, cached_tols, err in _SHAPE_CHECKS:
        if cached_tols == tols and cached.isEqual(s):
            return err
    try:
        s.check(True)
        err = None
    except Exception as e:  # noqa: BLE001 — the message IS the finding
        err = " ".join(str(e).split())[:160]
    _SHAPE_CHECKS.append((s, tols, err))
    del _SHAPE_CHECKS[:-_SHAPE_CHECKS_SIZE]
    return err


def box(shp):
    """The part's TRUE bounding box. Shape.BoundBox bounds the control points or
    the triangulation of curved faces: a 30x20 loft measured 37.3x26.8, a tilted
    torus 8 % too big, a revolved B-spline SMALLER than the part (so a ray or an
    interference pre-filter could skip a face). AddOptimal measures the surfaces
    themselves, and is as fast (0.003 s on a plate with 1,600 holes)."""
    try:
        bb = shp.optimalBoundingBox(False, False)
        if bb.isValid():
            return bb
    except Exception:  # noqa: BLE001 — never fail a measurement on the better box
        pass
    return shp.BoundBox


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


def body_of(obj):
    """The PartDesign Body a feature belongs to (None for a Body or a free object)."""
    if getattr(obj, "TypeId", "") == "PartDesign::Body" or not obj.TypeId.startswith("PartDesign::"):
        return None
    for o in obj.InList:
        if o.TypeId == "PartDesign::Body" and obj in o.Group:
            return o
    return None


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


def _body_issues(obj) -> List[str]:
    """A Body's shape is its Tip's: a feature after the Tip, or suppressed, is
    NOT in the part, while the Body still checks as a valid solid."""
    if getattr(obj, "TypeId", "") != "PartDesign::Body":
        return []
    feats = [o for o in obj.Group if o.isDerivedFrom("PartDesign::Feature")]
    if not feats:
        return []
    tip = obj.Tip
    if tip is None:
        return ["the Body has no Tip: none of its features is in the part"]
    issues = []
    if tip in feats:
        i = feats.index(tip)
        if feats[i + 1:]:
            issues.append(f"{', '.join(f.Name for f in feats[i + 1:])} after the Tip ({tip.Name}): "
                          "not in the part (set the Body's Tip to the last feature, or delete them)")
        feats = feats[:i + 1]
    off = [f.Name for f in feats if getattr(f, "Suppressed", False)]
    if off:
        issues.append(f"{', '.join(off)} suppressed: not in the part")
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
    boxes = [box(s) for s in shapes]      # exact, and once each
    for (i, a), (j, b) in combinations(enumerate(shapes), 2):
        if not boxes[i].intersect(boxes[j]):
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
    bare = not hasattr(obj, "Shape") and hasattr(obj, "Solids")   # a TopoShape, not an object
    res: Dict[str, Any] = {"name": getattr(obj, "Name", "shape" if bare else "?"),
                           "type": getattr(obj, "TypeId", "?"),
                           "issues": [], "unchecked": []}
    if not bare:
        res["issues"] += _state_issues(obj)
        res["issues"] += _body_issues(obj)

    shp = obj if bare else getattr(obj, "Shape", None)
    if shp is None or shp.isNull():
        res.update(valid=False, solids=0, closed=False, volume=0.0)
        res["issues"].append("no shape / null shape")
        return _finish(res)

    solids = list(shp.Solids)
    # OCCT BOP check. It RAISES with the list of problems when the shape is
    # bad and returns None when clean — the old code swallowed the exception,
    # so self-intersections were never reported. Run it per solid: on a
    # compound it also flags solids that merely TOUCH; real overlaps between
    # solids are measured by the interference test below.
    parts = [(f"solid{i + 1} ", s) for i, s in enumerate(solids)] if len(solids) > 1 \
        else [("", shp)]
    bop_issues = []
    for label, s in parts:
        err = _occt_check(s)
        if err is not None:
            bop_issues.append(f"{label}topology/self-intersection: {err}")
    # check(True) starts with the same BRepCheck analysis as isValid() and
    # raises when it fails, so a clean pass proves validity: isValid() (as
    # slow as that analysis, 15 s on a 1600-hole plate) only runs when needed
    # — after a failure, or when the shape holds more than its solids.
    solids_only = len(solids) <= 1 or all(
        sum(len(getattr(s, k)) for s in solids) == len(getattr(shp, k))
        for k in ("Faces", "Edges", "Vertexes"))
    valid = True if (not bop_issues and solids_only) else bool(shp.isValid())
    volume = float(shp.Volume)
    closed = (all(s.isClosed() for s in solids) if solids else bool(shp.isClosed()))
    bb = box(shp)
    res.update(valid=valid, solids=len(solids), closed=closed, volume=volume,
               # an empty shape has no box (it printed "-infx-infx-inf")
               bbox=[round(bb.XLength, 4), round(bb.YLength, 4), round(bb.ZLength, 4)]
               if bb.isValid() else None,
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

    res["issues"] += bop_issues

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


def _profile_problem(loops) -> Optional[str]:
    """Why the closed loops cannot make the face a Pad/Pocket would, or None.
    Closed loops can still cross themselves (a figure-8) or each other (two
    overlapping squares, a hole circle cutting the outline): the sketch read
    OK and the Pad failed or made a broken solid. Built as PartDesign builds
    it (FaceMakerBullseye), then the OCCT check of that face."""
    import Part
    for i, w in enumerate(loops, 1):
        try:
            one = Part.makeFace([w], "Part::FaceMakerBullseye")
        except Exception:  # noqa: BLE001
            one = None
        # a figure-8 makes two regions (the Pad, two solids); a folded loop, none
        if one is None or len(one.Faces) != 1 or abs(one.Area) <= 1e-9:
            return f"loop {i} crosses itself (a figure-8 or folded outline)"
    try:
        face = Part.makeFace(loops, "Part::FaceMakerBullseye")
    except Exception as e:  # noqa: BLE001 — the reason IS the finding
        return ("the loops cross each other, no face can be made: "
                + " ".join(str(e).split())[:120])
    if face.isNull() or not face.Faces or abs(face.Area) <= 1e-9:
        return "the loops overlap each other: no area left to pad"
    err = _occt_check(face)
    if err is not None or not face.isValid():
        return f"a loop crosses itself or another: {err or 'invalid face'}"
    return None


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
    if require_closed and closed and not opened:
        problem = _profile_problem([w for w in wires if w.isClosed()])
        if problem:
            issues.append(problem)
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
    names = [o.Name for o in objs]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:   # a part listed twice "overlaps itself" by its whole volume
        raise ValueError(f"object(s) listed more than once: {dup}")
    if len(objs) < 2:   # nothing to compare: an OK here would verify nothing
        raise ValueError(f"an interference check needs 2 or more parts (got {names})")
    for o in objs:
        body = body_of(o)
        if body is not None:   # a step overlaps its own Body by its whole volume
            raise ValueError(f"{o.Name} is a step of {body.Name}, not the whole part: "
                             f"list {body.Name}")
        s = getattr(o, "Shape", None)
        if s is None or s.isNull():
            raise ValueError(f"{o.Name} has no shape: an interference check needs solid parts")
        if not s.Solids:
            # a sketch overlaps nothing (silent OK); a shell's "overlap volume" is not one
            raise ValueError(f"{o.Name} is a {s.ShapeType}, not a solid: an interference "
                             f"check needs solid parts")
    shapes = [o.Shape for o in objs]
    hits = interference(shapes, tol, names=[o.Name for o in objs])
    return {"status": FAIL if hits else OK, "parts": len(objs), "interferences": hits}
