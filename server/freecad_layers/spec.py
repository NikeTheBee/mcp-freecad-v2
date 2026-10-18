"""Design contract — the user's requirements as automatic, blocking checks.

When the user says "a 60x40x8 plate with one Ø6 hole in the centre, under
200 g", record it once with `require(...)`; `check_all()` then re-measures
the model after every step and any deviation is a FAIL. The code decides
whether the part matches the request — not the AI's reading of it
(audit 2026-10-11 §9, idea from maker-agent).

Specs live in the project state (state.json "specs"), so they survive
sessions and crashes.

Kinds (all lengths mm, volumes mm³, masses g):
  bbox        size=[x, y, z], tol (abs), any_orientation=False
  volume      value+tol | min | max
  mass        density_g_cm3, value+tol | min | max
  solids      count
  holes       count, diameter (+tol), through (bool, optional): holes, not bores --
              a counterbored hole, or one pin hole through both flanges, counts once
  hole_at     diameter, center=[x, y, z] (+tol): one hole whose axis passes there,
              between its ends (null = any value: [x, y, null] for a vertical hole)
  min_wall    min (estimated by analysis.min_wall)
  no_interference  objects=[names]  (+ the target when it is an object)
  valid       (verify.check must be OK)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import analysis, verify
from . import state as _state

OK, FAIL, UNKNOWN = verify.OK, verify.FAIL, verify.UNKNOWN

KINDS = ("bbox", "volume", "mass", "solids", "holes", "hole_at", "min_wall",
         "no_interference", "valid")


def require(target: str, kind: str, note: str = "", **params) -> str:
    """Add a requirement on object `target`; returns its id (e.g. 'S3')."""
    if kind not in KINDS:
        raise ValueError(f"unknown spec kind {kind!r}; use one of {KINDS}")
    _validate(kind, params)
    if kind == "no_interference" and len(set(params["objects"]) | {target}) < 2:
        raise ValueError("spec 'no_interference' needs 2 or more parts: the target and objects=[...]")

    def add(s):
        nums = [int(x["id"][1:]) for x in s["specs"] if str(x.get("id", "")).startswith("S")
                and str(x["id"])[1:].isdigit()]
        sid = f"S{max(nums, default=0) + 1}"
        s["specs"].append({"id": sid, "target": target, "kind": kind, "params": params,
                           "note": note, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return sid
    return _state.update(add)


# Accepted parameters per kind. An unknown key is refused: a typo such as
# {"diam": 6} used to be ignored, leaving a weaker contract than the user's.
_PARAMS = {
    "bbox": {"size", "tol", "any_orientation"},
    "volume": {"value", "tol", "min", "max"},
    "mass": {"density_g_cm3", "value", "tol", "min", "max"},
    "solids": {"count"},
    "holes": {"count", "diameter", "through", "tol"},
    "hole_at": {"diameter", "center", "tol"},
    "min_wall": {"min"},
    "no_interference": {"objects"},
    "valid": set(),
}


def _number(kind: str, key: str, x: Any, low: Optional[float] = None,
            strict: bool = False) -> None:
    ok = isinstance(x, (int, float)) and not isinstance(x, bool) and x == x \
        and abs(x) != float("inf")
    if ok and low is not None:
        ok = x > low if strict else x >= low
    if not ok:
        bound = "" if low is None else f" {'>' if strict else '>='} {low:g}"
        raise ValueError(f"spec {kind!r}: {key} must be a number{bound} (got {x!r})")


def _validate(kind: str, p: Dict[str, Any]) -> None:
    unknown = sorted(set(p) - _PARAMS[kind])
    if unknown:
        raise ValueError(f"spec {kind!r}: unknown parameter(s) {unknown}; "
                         f"accepted: {sorted(_PARAMS[kind]) or 'none'}")
    need = {"bbox": ["size"], "mass": ["density_g_cm3"], "solids": ["count"],
            "hole_at": ["diameter", "center"], "min_wall": ["min"],
            "no_interference": ["objects"]}.get(kind, [])
    missing = [k for k in need if k not in p]
    if missing:
        raise ValueError(f"spec {kind!r} needs {missing}")
    if kind in ("volume", "mass") and not ({"value", "min", "max"} & p.keys()):
        raise ValueError(f"spec {kind!r} needs value(+tol), min or max")
    if "tol" in p:
        _number(kind, "tol", p["tol"], 0)
    for key in ("value", "min", "max"):
        if key in p and kind != "min_wall":
            _number(kind, key, p[key])
    if "min" in p and "max" in p and kind != "min_wall" and p["min"] > p["max"]:
        raise ValueError(f"spec {kind!r}: min {p['min']} > max {p['max']}")
    if kind == "bbox":
        if not isinstance(p["size"], (list, tuple)) or len(p["size"]) != 3:
            raise ValueError("bbox size must be [x, y, z]")
        for i, x in enumerate(p["size"]):
            _number(kind, f"size[{i}]", x, 0, strict=True)
    if kind == "mass":
        _number(kind, "density_g_cm3", p["density_g_cm3"], 0, strict=True)
    if kind == "solids":
        if not isinstance(p["count"], int) or isinstance(p["count"], bool) or p["count"] < 1:
            raise ValueError(f"spec 'solids': count must be an integer >= 1 (got {p['count']!r})")
    if kind == "holes" and "count" in p and (
            not isinstance(p["count"], int) or isinstance(p["count"], bool) or p["count"] < 0):
        raise ValueError(f"spec 'holes': count must be an integer >= 0 (got {p['count']!r})")
    if kind in ("holes", "hole_at") and "diameter" in p:
        _number(kind, "diameter", p["diameter"], 0, strict=True)
    if kind == "holes" and "through" in p and not isinstance(p["through"], bool):
        raise ValueError(f"spec 'holes': through must be true or false (got {p['through']!r})")
    if kind == "hole_at":
        c = p["center"]
        if not isinstance(c, (list, tuple)) or len(c) != 3 or all(x is None for x in c):
            raise ValueError("hole_at center must be [x, y, z] (null for a free coordinate, "
                             "at least one given)")
        for i, x in enumerate(c):
            if x is not None:
                _number(kind, f"center[{i}]", x)
    if kind == "min_wall":
        _number(kind, "min", p["min"], 0, strict=True)
    if kind == "no_interference":
        objs = p["objects"]
        if (not isinstance(objs, (list, tuple)) or not objs
                or not all(isinstance(o, str) and o for o in objs) or len(set(objs)) != len(objs)):
            raise ValueError("no_interference objects must be a list of distinct object names "
                             f"(got {objs!r})")


def remove(spec_id: str) -> None:
    """Delete requirement `spec_id` (e.g. 'S3'); an unknown id is an error."""
    def drop(s):
        kept = [x for x in s["specs"] if x.get("id") != spec_id]
        if len(kept) == len(s["specs"]):
            known = [x.get("id") for x in s["specs"]]
            raise ValueError(f"no requirement {spec_id!r}; recorded: {known or 'none'}")
        s["specs"] = kept
    _state.update(drop)


def list_specs() -> List[Dict[str, Any]]:
    return _state.load().get("specs", [])


def _range_ok(x: float, p: Dict[str, Any], default_tol: float) -> bool:
    if "value" in p:
        return abs(x - p["value"]) <= p.get("tol", default_tol)
    return (x >= p.get("min", -float("inf"))) and (x <= p.get("max", float("inf")))


def _expect_txt(p: Dict[str, Any], default_tol: float) -> str:
    if "value" in p:
        return f"{p['value']}+/-{p.get('tol', default_tol)}"
    return f"[{p.get('min', '-inf')}, {p.get('max', 'inf')}]"


def _material_volume(obj, v: Dict[str, Any]):
    """(volume of material, None) or (None, why there is none). A face read 0 mm3,
    so "mass max 60 g" passed on a sketch face; an open shell read a meaningless
    800 mm3; two parts overlapping by 500 mm3 counted it twice (2000, not 1500)."""
    if not v["solids"]:
        return None, f"{v['name']} has no solid: no volume to measure"
    if not v["closed"]:
        return None, f"{v['name']} is not closed (not watertight): its volume means nothing"
    if v["solids"] > 1 and any(str(i).startswith("interference") for i in v["issues"]):
        shp = obj if not hasattr(obj, "Shape") else verify.global_shape(obj)
        solids = shp.Solids
        try:
            return float(solids[0].multiFuse(solids[1:]).Volume), None
        except Exception:  # noqa: BLE001
            return None, f"{v['name']}: overlapping solids, their union failed"
    return v["volume"], None


def _on_axis(h: Dict[str, Any], c, tol: float) -> bool:
    """True when the hole's axis, between its two ends, passes through the point
    c (None = any value) within tol on each given coordinate. It compared the
    axis MIDPOINT: a D5 hole "at (10, 10, 20)", z being the top face it is
    drilled from, failed on a correct part."""
    m, a, half = h["center"], h["axis"], h["depth"] / 2.0
    lo, hi = -half - tol, half + tol          # position along the axis from the midpoint
    for i in range(3):
        if c[i] is None:
            continue
        if abs(a[i]) < 1e-9:                  # the axis keeps this coordinate
            if abs(m[i] - c[i]) > tol:
                return False
            continue
        s1, s2 = (c[i] - tol - m[i]) / a[i], (c[i] + tol - m[i]) / a[i]
        lo, hi = max(lo, min(s1, s2)), min(hi, max(s1, s2))
        if lo > hi:
            return False
    return True


def _eval(spec: Dict[str, Any], doc=None) -> Dict[str, Any]:
    """Evaluate one spec. One that cannot be evaluated (e.g. saved with bad
    parameters before they were validated) is UNKNOWN for itself — it used to
    crash every later `check` of its target."""
    try:
        return _eval_spec(spec, doc)
    except Exception as e:  # noqa: BLE001 — reported in the result, never silent
        return {"id": spec.get("id"), "kind": spec.get("kind"), "target": spec.get("target"),
                "status": UNKNOWN, "measured": None, "expected": spec.get("params"),
                "message": f"could not evaluate ({type(e).__name__}: {e}); "
                           f"remove it and record it again"[:240]}


def _eval_spec(spec: Dict[str, Any], doc=None) -> Dict[str, Any]:
    kind, p, target = spec["kind"], spec["params"], spec["target"]
    res = {"id": spec["id"], "kind": kind, "target": target}
    try:
        if kind == "no_interference":
            names = list(p["objects"])
            # the target is one of the parts when it is a real object: require("Plate",
            # "no_interference", objects=[pins]) checked the pins against each other only,
            # never against the plate
            try:
                t = verify._resolve(target, doc)
            except ValueError:
                t = None
            if t is not None and t.Name not in names and getattr(t, "Label", None) not in names:
                names.insert(0, t.Name)
            a = verify.check_assembly(names)
            hits = a["interferences"]
            return {**res, "status": FAIL if hits else OK, "measured": hits,
                    "expected": "no overlap between " + ", ".join(names)}
        obj = verify._resolve(target, doc)
    except ValueError as e:
        return {**res, "status": FAIL, "measured": None, "expected": "object exists",
                "message": str(e)}

    v = verify.check(obj)
    if v["status"] == UNKNOWN and kind == "valid":
        return {**res, "status": UNKNOWN, "measured": v["unchecked"], "expected": "OK"}
    if kind == "valid":
        return {**res, "status": OK if v["ok"] else FAIL, "measured": v["issues"],
                "expected": "OK"}
    if not v.get("bbox"):
        return {**res, "status": FAIL, "measured": None, "expected": kind,
                "message": "no shape to measure"}

    if kind == "bbox":
        tol = p.get("tol", 0.01)
        got, want = list(v["bbox"]), list(p["size"])
        if p.get("any_orientation"):
            got, want = sorted(got), sorted(want)
        ok = all(abs(g - w) <= tol for g, w in zip(got, want))
        return {**res, "status": OK if ok else FAIL, "measured": v["bbox"],
                "expected": f"{p['size']}+/-{tol}"}
    if kind in ("volume", "mass"):
        vol, why = _material_volume(obj, v)
        if vol is None:
            return {**res, "status": FAIL, "measured": None, "expected": _expect_txt(
                p, 1e-3 if kind == "volume" else 0.01), "message": why}
    if kind == "volume":
        return {**res, "status": OK if _range_ok(vol, p, 1e-3) else FAIL,
                "measured": round(vol, 3), "expected": _expect_txt(p, 1e-3)}
    if kind == "mass":
        grams = vol / 1000.0 * p["density_g_cm3"]
        return {**res, "status": OK if _range_ok(grams, p, 0.01) else FAIL,
                "measured": round(grams, 3), "expected": _expect_txt(p, 0.01)}
    if kind == "solids":
        return {**res, "status": OK if v["solids"] == p["count"] else FAIL,
                "measured": v["solids"], "expected": p["count"]}
    if kind == "holes":
        hs = analysis.holes(obj)
        tol = p.get("tol", 0.01)
        if "diameter" in p:                 # holes with a bore of that diameter
            hs = [h for h in hs if abs(h["diameter"] - p["diameter"]) <= tol]
            if "through" in p:
                hs = [h for h in hs if h["through"] == bool(p["through"])]
            n = len({h["hole"] for h in hs})
        else:                               # holes: a counterbored hole is ONE hole
            through: Dict[int, bool] = {}
            for h in hs:
                through[h["hole"]] = through.get(h["hole"], False) or h["through"]
            n = sum(1 for t in through.values()
                    if "through" not in p or t == bool(p["through"]))
        want = p.get("count")
        ok = (n == want) if want is not None else n > 0
        return {**res, "status": OK if ok else FAIL, "measured": n,
                "expected": want if want is not None else ">=1"}
    if kind == "hole_at":
        tol = p.get("tol", 0.05)
        c = p["center"]
        same_d = [h for h in analysis.holes(obj) if abs(h["diameter"] - p["diameter"]) <= tol]
        hs = [h for h in same_d if _on_axis(h, c, tol)]
        return {**res, "status": OK if hs else FAIL,
                # where each hole of that diameter is: its axis midpoint, direction, depth
                "measured": [f"mid {h['center']} axis {h['axis']} depth {h['depth']}"
                             for h in same_d],
                "expected": f"D{p['diameter']} with its axis through {c}+/-{tol}"}
    if kind == "min_wall":
        w = analysis.min_wall(obj)
        if w["min_mm"] is None:
            return {**res, "status": UNKNOWN, "measured": None, "expected": f">= {p['min']}"}
        if w["min_mm"] >= p["min"] and w.get("unchecked"):
            # some faces could not be ray-cast: a thinner wall may hide there
            return {**res, "status": UNKNOWN, "measured": w["min_mm"],
                    "expected": f">= {p['min']} (estimate; {w['unchecked']})"}
        return {**res, "status": OK if w["min_mm"] >= p["min"] else FAIL,
                "measured": w["min_mm"], "expected": f">= {p['min']} (estimate)"}
    return {**res, "status": UNKNOWN, "measured": None, "expected": "?"}


def same_target(a: str, b: str, doc=None) -> bool:
    """Do two target strings name the same object? A part is named by its Name OR
    its Label: a requirement recorded on "Plate" (the Label) was skipped by
    check("Body") (its Name), and the reverse -- the verdict said OK while the
    requirement failed. Unresolvable names (not modeled yet) compare as text."""
    if a == b:
        return True
    try:
        oa, ob = verify._resolve(a, doc), verify._resolve(b, doc)
    except ValueError:
        return False
    return oa.Name == ob.Name and oa.Document.Name == ob.Document.Name


def check_all(target: Optional[str] = None, doc=None) -> Dict[str, Any]:
    """Evaluate every spec (or those of `target`). FAIL if any spec fails,
    UNKNOWN if one could not be evaluated, OK only if all pass."""
    results = [_eval(s, doc) for s in list_specs()
               if target is None or s["kind"] == "no_interference"
               or same_target(s["target"], target, doc)]
    statuses = {r["status"] for r in results}
    status = FAIL if FAIL in statuses else UNKNOWN if UNKNOWN in statuses else OK
    return {"status": status, "checked": len(results), "results": results}


def verdict(target: Optional[str] = None, doc=None) -> str:
    """One line: 'OK contract 4/4' or 'FAIL contract 3/4 | S2 bbox Plate: ...'."""
    r = check_all(target, doc)
    if not r["checked"]:
        return "UNKNOWN contract: no requirement recorded (spec.require(...))"
    n_ok = sum(1 for x in r["results"] if x["status"] == OK)
    bad = [x for x in r["results"] if x["status"] != OK]
    line = f"{r['status']} contract {n_ok}/{r['checked']}"
    if bad:
        line += " | " + "; ".join(
            f"{x['id']} {x['kind']} {x['target']}: got {x['measured']} want {x['expected']}"
            + (f" ({x['message']})" if x.get("message") else "") for x in bad)
    return line
