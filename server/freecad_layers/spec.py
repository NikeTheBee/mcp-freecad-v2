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
  holes       count, diameter (+tol), through (bool, optional)
  hole_at     diameter, center=[x, y, z] (+tol): one hole there
  min_wall    min (estimated by analysis.min_wall)
  no_interference  objects=[names]  (target ignored)
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

    def add(s):
        nums = [int(x["id"][1:]) for x in s["specs"] if str(x.get("id", "")).startswith("S")
                and str(x["id"])[1:].isdigit()]
        sid = f"S{max(nums, default=0) + 1}"
        s["specs"].append({"id": sid, "target": target, "kind": kind, "params": params,
                           "note": note, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return sid
    return _state.update(add)


def _validate(kind: str, p: Dict[str, Any]) -> None:
    need = {"bbox": ["size"], "mass": ["density_g_cm3"], "solids": ["count"],
            "hole_at": ["diameter", "center"], "min_wall": ["min"],
            "no_interference": ["objects"]}.get(kind, [])
    missing = [k for k in need if k not in p]
    if missing:
        raise ValueError(f"spec {kind!r} needs {missing}")
    if kind in ("volume", "mass") and not ({"value", "min", "max"} & p.keys()):
        raise ValueError(f"spec {kind!r} needs value(+tol), min or max")
    if kind == "bbox" and len(p["size"]) != 3:
        raise ValueError("bbox size must be [x, y, z]")


def remove(spec_id: str) -> None:
    _state.update(lambda s: s.__setitem__(
        "specs", [x for x in s["specs"] if x.get("id") != spec_id]))


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


def _eval(spec: Dict[str, Any], doc=None) -> Dict[str, Any]:
    kind, p, target = spec["kind"], spec["params"], spec["target"]
    res = {"id": spec["id"], "kind": kind, "target": target}
    try:
        if kind == "no_interference":
            a = verify.check_assembly(p["objects"])
            hits = a["interferences"]
            return {**res, "status": FAIL if hits else OK, "measured": hits,
                    "expected": "no overlap"}
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
    if kind == "volume":
        return {**res, "status": OK if _range_ok(v["volume"], p, 1e-3) else FAIL,
                "measured": round(v["volume"], 3), "expected": _expect_txt(p, 1e-3)}
    if kind == "mass":
        grams = v["volume"] / 1000.0 * p["density_g_cm3"]
        return {**res, "status": OK if _range_ok(grams, p, 0.01) else FAIL,
                "measured": round(grams, 3), "expected": _expect_txt(p, 0.01)}
    if kind == "solids":
        return {**res, "status": OK if v["solids"] == p["count"] else FAIL,
                "measured": v["solids"], "expected": p["count"]}
    if kind == "holes":
        hs = analysis.holes(obj)
        tol = p.get("tol", 0.01)
        if "diameter" in p:
            hs = [h for h in hs if abs(h["diameter"] - p["diameter"]) <= tol]
        if "through" in p:
            hs = [h for h in hs if h["through"] == bool(p["through"])]
        want = p.get("count")
        ok = (len(hs) == want) if want is not None else bool(hs)
        return {**res, "status": OK if ok else FAIL, "measured": len(hs),
                "expected": want if want is not None else ">=1"}
    if kind == "hole_at":
        tol = p.get("tol", 0.05)
        c = p["center"]
        hs = [h for h in analysis.holes(obj)
              if abs(h["diameter"] - p["diameter"]) <= tol
              and all(abs(h["center"][i] - c[i]) <= tol
                      for i in range(3) if c[i] is not None)]
        return {**res, "status": OK if hs else FAIL,
                "measured": [h["center"] for h in analysis.holes(obj)
                             if abs(h["diameter"] - p["diameter"]) <= tol],
                "expected": f"D{p['diameter']} at {c}+/-{tol}"}
    if kind == "min_wall":
        w = analysis.min_wall(obj)
        if w["min_mm"] is None:
            return {**res, "status": UNKNOWN, "measured": None, "expected": f">= {p['min']}"}
        return {**res, "status": OK if w["min_mm"] >= p["min"] else FAIL,
                "measured": w["min_mm"], "expected": f">= {p['min']} (estimate)"}
    return {**res, "status": UNKNOWN, "measured": None, "expected": "?"}


def check_all(target: Optional[str] = None, doc=None) -> Dict[str, Any]:
    """Evaluate every spec (or those of `target`). FAIL if any spec fails,
    UNKNOWN if one could not be evaluated, OK only if all pass."""
    results = [_eval(s, doc) for s in list_specs()
               if target is None or s["target"] == target or s["kind"] == "no_interference"]
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
