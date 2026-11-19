"""The study loop (FEUILLE_DE_ROUTE_ATELIER.md §3.4, §7.3): from requirements to a real, tested object.

    design ──simulate×N──► all criteria OK? ──no──► correct (new iteration) ──┐
       ▲                          │yes                                        │
       │                          ▼                                           │
       │                    ready_to_build ──(the human builds it)──► measure │
       │                                                                │     │
       │            real test within tolerance AND close to the model? ─┤     │
       │                         │no                                    │yes  │
       └──── calibrate (model ← measurements) ◄──┘                 validated  │
       └──────────────────────────────────────────────────────────────────────┘

"Perfect" is a state the data must reach, not a claim: `validated` only when every simulation
criterion is OK, every real measurement is inside its acceptance limits, AND the model predicts
the measurements within the agreed tolerance. Anything else names the next action.

Storage: <project>/studies/<name>/study.json (+ journal.jsonl, one line per event). Plain JSON in
the project folder: versioned with the project, readable by a human.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")
STATES = ("design", "ready_to_build", "built", "calibrate", "validated")


def _dir(project_dir: Path, name: str) -> Path:
    if not _NAME.fullmatch(name or ""):
        raise ValueError("study name: letters, digits, _ and - (max 40)")
    return project_dir / "studies" / name


def load(project_dir: Path, name: str) -> Dict[str, Any]:
    try:
        return json.loads((_dir(project_dir, name) / "study.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"no study {name!r}: study(action='start', name=...) first") from None


def _save(project_dir: Path, s: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> None:
    d = _dir(project_dir, s["name"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "study.json.tmp"
    tmp.write_text(json.dumps(s, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(d / "study.json")
    if event is not None:
        event = dict(event, t=time.strftime("%Y-%m-%d %H:%M:%S"), iteration=s["iteration"])
        with open(d / "journal.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def exists(project_dir: Path, name: str) -> bool:
    return (_dir(project_dir, name) / "study.json").is_file()


def start(project_dir: Path, name: str, goal: str, criteria: Optional[Dict[str, Any]] = None,
          hypotheses: Optional[List[Any]] = None, params: Optional[Dict[str, Any]] = None,
          acceptance: Optional[Dict[str, Any]] = None, agreement: float = 0.10) -> Dict[str, Any]:
    """criteria: {kind: {value_key: {"min"|"max": x}}} applied to every simulate of that kind.
    acceptance: {quantity: {"min"|"max": x}} limits for the REAL test.
    agreement: max relative gap between model and measurement (0.10 = 10 %)."""
    if exists(project_dir, name):
        raise ValueError(f"study {name!r} exists: use action='status' (or another name)")
    s = {"name": name, "goal": goal, "state": "design", "iteration": 1,
         "criteria": criteria or {}, "acceptance": acceptance or {}, "agreement": float(agreement),
         "hypotheses": [], "params": params or {}, "results": {}, "measurements": {},
         "calibration": {}, "history": [], "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    for h in hypotheses or []:
        s["hypotheses"].append(_hyp(h, 1))
    _save(project_dir, s, {"event": "start", "goal": goal})
    return s


def _hyp(h: Any, iteration: int) -> Dict[str, Any]:
    if isinstance(h, str):
        h = {"text": h}
    if not isinstance(h, dict) or not h.get("text"):
        raise ValueError("a hypothesis is a text or {text, source}")
    return {"text": str(h["text"]), "source": str(h.get("source", "UNSOURCED")), "since": iteration}


def add_hypothesis(project_dir: Path, name: str, h: Any) -> Dict[str, Any]:
    s = load(project_dir, name)
    s["hypotheses"].append(_hyp(h, s["iteration"]))
    _save(project_dir, s, {"event": "hypothesis", **s["hypotheses"][-1]})
    return s


def limits_for(project_dir: Path, name: str, kind: str) -> Dict[str, Any]:
    try:
        return dict(load(project_dir, name).get("criteria", {}).get(kind, {}))
    except ValueError:
        return {}


def record_result(project_dir: Path, name: str, kind: str, res: Dict[str, Any],
                  label: str = "", params: Optional[Dict[str, Any]] = None) -> None:
    """Called by simulate: the latest result of each kind (or label) in this iteration. The inputs
    are kept so that `calibrate` can re-run the same case with one model parameter changed."""
    if not exists(project_dir, name):
        return
    s = load(project_dir, name)
    key = label or kind
    geom = Path(res["work"]) / "geometry.step" if res.get("work") else None
    s["results"][key] = {"kind": kind, "verdict": res.get("verdict"), "summary": res.get("summary", "")[:400],
                         "values": {k: v for k, v in (res.get("values") or {}).items()
                                    if isinstance(v, (int, float, str))},
                         "inputs_hash": res.get("inputs_hash"), "iteration": s["iteration"],
                         "params": params or {},
                         "geometry": str(geom) if geom is not None and geom.is_file() else None}
    _advance(s)
    _save(project_dir, s, {"event": "simulate", "key": key, "verdict": res.get("verdict"),
                           "summary": res.get("summary", "")[:300]})


def _advance(s: Dict[str, Any]) -> None:
    """design → ready_to_build when every criterion's kind has an OK result of this iteration."""
    if s["state"] not in ("design", "ready_to_build"):
        return
    cur = {k: r for k, r in s["results"].items() if r.get("iteration") == s["iteration"]}
    needed = set(s["criteria"]) or set(cur)
    done = {r["kind"] for r in cur.values() if r["verdict"] == "OK"} | {k for k, r in cur.items() if r["verdict"] == "OK"}
    bad = [k for k, r in cur.items() if r["verdict"] != "OK"]
    s["state"] = "ready_to_build" if needed and needed <= done and not bad else "design"


def new_iteration(project_dir: Path, name: str, change: str,
                  params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A correction: what changed and why. Results of the previous iteration stay in history."""
    if not change.strip():
        raise ValueError("say what changed (the correction) and why")
    s = load(project_dir, name)
    # deep copies: the next iteration updates s["params"] in place, and a shared reference made
    # the history show the NEW parameters under the old iteration
    snap = json.loads(json.dumps({"results": s["results"], "params": s["params"],
                                  "measurements": s["measurements"]}, default=str))
    s["history"].append({"iteration": s["iteration"], "state": s["state"], **snap})
    s["iteration"] += 1
    s["params"].update(params or {})
    s["results"] = {}
    if s["state"] in ("ready_to_build", "built", "calibrate"):
        s["state"] = "design"
    _save(project_dir, s, {"event": "iteration", "change": change, "params": params or {}})
    return s


def mark_built(project_dir: Path, name: str, note: str = "") -> Dict[str, Any]:
    s = load(project_dir, name)
    if s["state"] != "ready_to_build":
        raise ValueError(f"state is {s['state']}: every simulation criterion must be OK first")
    s["state"] = "built"
    _save(project_dir, s, {"event": "built", "note": note})
    return s


def measure(project_dir: Path, name: str, data: Dict[str, Any], source: str,
            predicted: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Real test results. data: {quantity: value}. predicted: {quantity: "kind.value_key"} links a
    measurement to the model's prediction (default: same key in any result of this iteration).
    Returns the comparison and moves the loop to validated or calibrate."""
    if not source.strip():
        raise ValueError("source: how it was measured (instrument, log file, procedure step)")
    s = load(project_dir, name)
    if s["state"] not in ("built", "calibrate", "validated"):
        raise ValueError(f"state is {s['state']}: build the part (mark_built) before measuring")
    predicted = {**s.get("links", {}), **(predicted or {})}   # links set by test_plan
    rows, all_in, all_agree = [], True, True
    for q, v in data.items():
        v = float(v)
        acc = s["acceptance"].get(q, {})
        inside = (("min" not in acc or v >= float(acc["min"])) and ("max" not in acc or v <= float(acc["max"])))
        pred = _prediction(s, q, (predicted or {}).get(q))
        gap = None if pred is None or pred == 0 else (v - pred) / abs(pred)
        agree = gap is not None and abs(gap) <= s["agreement"]
        all_in &= inside if acc else True
        all_agree &= agree
        rows.append({"quantity": q, "measured": v, "predicted": pred, "gap": gap,
                     "acceptance": acc, "inside": inside if acc else None, "agree": agree})
        s["measurements"][q] = {"value": v, "source": source, "iteration": s["iteration"],
                                "link": (predicted or {}).get(q)}
    missing_acc = [q for q in s["acceptance"] if q not in data]
    if all_in and all_agree and not missing_acc:
        s["state"] = "validated"
        nxt = "validated: model and real object agree, every limit met"
    else:
        s["state"] = "calibrate"
        why = []
        if not all_in:
            why.append("a real measurement is outside its acceptance limits → correct the design")
        if not all_agree:
            why.append("model and test disagree beyond the agreed tolerance → calibrate the model")
        if missing_acc:
            why.append(f"not yet measured: {', '.join(missing_acc)}")
        nxt = "; ".join(why)
    _save(project_dir, s, {"event": "measure", "source": source, "rows": rows, "state": s["state"]})
    return {"state": s["state"], "rows": rows, "next": nxt}


def _prediction(s: Dict[str, Any], q: str, link: Optional[str]) -> Optional[float]:
    res = s["results"]
    if link:
        key, _, val = link.partition(".")
        r = res.get(key) or next((r for r in res.values() if r["kind"] == key), None)
        v = (r or {}).get("values", {}).get(val)
        return float(v) if isinstance(v, (int, float)) else None
    for r in res.values():
        v = r.get("values", {}).get(q)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def calibrate(project_dir: Path, name: str, factors: Dict[str, float], note: str) -> Dict[str, Any]:
    """Record model corrections derived from the test (e.g. {"E_eff_MPa": 2900, "friction": 0.12}),
    then open the next iteration with them. The fit itself is done by a `calib_*` simulate kind or
    stated by the AI with its reasoning in `note`."""
    s = load(project_dir, name)
    if s["state"] != "calibrate":
        raise ValueError(f"state is {s['state']}: calibrate follows a measurement that disagreed")
    s["calibration"].update(factors)
    _save(project_dir, s, {"event": "calibrate", "factors": factors, "note": note})
    return new_iteration(project_dir, name, f"calibration: {note}", params=factors)


def _set_path(params: Dict[str, Any], path: str, value: float) -> Dict[str, Any]:
    """Copy of params with params[a][b] = value for path "a.b" (a material given by its name
    becomes {"base": name, b: value})."""
    out = json.loads(json.dumps(params))
    keys = path.split(".")
    cur = out
    for k in keys[:-1]:
        nxt = cur.get(k)
        if isinstance(nxt, str):
            nxt = {"base": nxt}
        elif not isinstance(nxt, dict):
            nxt = {}
        cur[k] = nxt
        cur = nxt
    cur[keys[-1]] = value
    return out


def _get_path(params: Dict[str, Any], path: str):
    cur: Any = params
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _material_default(params: Dict[str, Any], path: str):
    """material.E when the inputs name a material ("steel"): its tabulated value in fem_ccx."""
    import ast
    from . import ADAPTERS
    keys = path.split(".")
    if len(keys) != 2 or keys[0] != "material":
        return None
    mat = params.get("material")
    name = mat if isinstance(mat, str) else (mat.get("base") if isinstance(mat, dict) else None)
    if not name:
        return None
    tree = ast.parse((ADAPTERS / "fem_ccx.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "MATERIALS":
            for k, v in zip(node.value.keys, node.value.values):
                if ast.literal_eval(k) == name and isinstance(v, ast.Call):
                    for kw in v.keywords:
                        if kw.arg == keys[1]:
                            return ast.literal_eval(kw.value)
    return None


def apply_calibration(project_dir: Path, name: str, params: Dict[str, Any]) -> tuple:
    """simulate(..., study=name) runs with the study's calibrated model parameters: (params, notes).
    A dot path applies when its first key is among the params (e.g. material.E)."""
    try:
        cal = load(project_dir, name).get("calibration", {})
    except ValueError:
        return params, []
    notes = []
    for path, val in cal.items():
        if isinstance(val, (int, float)) and path.split(".")[0] in params:
            params = _set_path(params, path, float(val))
            notes.append(f"{path}={val:.6g}")
    return params, notes


def auto_calibrate(project_dir: Path, name: str, quantity: str, param: str, note: str = "",
                   max_runs: int = 8) -> Dict[str, Any]:
    """Fit ONE model parameter so that the model reproduces a measured quantity: re-runs the linked
    simulation (same geometry and inputs) with `param` varied (secant on a log scale) until the gap
    is under a fifth of the agreement tolerance; then calibrate() opens the next iteration with it."""
    from . import runner
    s = load(project_dir, name)
    if s["state"] != "calibrate":
        raise ValueError(f"state is {s['state']}: calibrate follows a measurement that disagreed")
    m = s["measurements"].get(quantity)
    if not m:
        raise ValueError(f"{quantity} was not measured")
    key, _, val_key = (m.get("link") or "").partition(".")
    r = s["results"].get(key) or next((x for x in s["results"].values() if x["kind"] == key), None)
    if not r or not val_key:
        raise ValueError(f"{quantity}: measure it with predicted {{{quantity!r}: 'kind.value'}} first")
    if not r.get("params"):
        raise ValueError("the linked result kept no inputs: simulate it again in this study")
    base = r["params"]
    p0 = _get_path(base, param)
    if p0 is None:
        p0 = _material_default(base, param)
    if not isinstance(p0, (int, float)) or p0 <= 0:
        raise ValueError(f"{param}: no positive current value in the linked inputs")
    target = float(m["value"])
    geom = Path(r["geometry"]) if r.get("geometry") else None
    tol = s["agreement"] / 5

    def model(p: float) -> float:
        res = runner.run(r["kind"], _set_path(base, param, p), project_dir, study=name,
                         geometry=geom, background=False)
        v = (res.get("values") or {}).get(val_key)
        if res.get("verdict") not in ("OK", "FAIL", "UNKNOWN") or not isinstance(v, (int, float)) or v <= 0:
            raise ValueError(f"calibration run failed: {res.get('summary', '')[:200]}")
        return float(v)

    xs = [math.log(p0), math.log(p0) + 0.1]
    fs = [math.log(float(r["values"][val_key]) / target)]
    fs.append(math.log(model(math.exp(xs[1])) / target))
    runs = 1
    while abs(fs[-1]) > tol and runs < max_runs:
        if fs[-1] == fs[-2]:
            raise ValueError(f"{val_key} does not depend on {param}: wrong parameter")
        x = xs[-1] - fs[-1] * (xs[-1] - xs[-2]) / (fs[-1] - fs[-2])
        x = min(max(x, xs[-1] - 1.6), xs[-1] + 1.6)          # at most x5 per step
        xs.append(x)
        fs.append(math.log(model(math.exp(x)) / target))
        runs += 1
    p = math.exp(xs[-1])
    gap = math.exp(fs[-1]) - 1
    if abs(fs[-1]) > tol:
        raise ValueError(f"no fit in {runs} runs ({param}={p:.6g} leaves {gap:+.1%}): the model "
                         "form is probably wrong, not just this parameter")
    calibrate(project_dir, name, {param: p},
              (f"{param} {p0:.6g} -> {p:.6g} fitted in {runs} runs to {quantity}={target:.6g} "
               f"(residual {gap:+.2%}). {note}").strip())
    return {"param": param, "from": p0, "to": p, "runs": runs, "residual": gap}


_SAFETY = {   # kinds or input keys → precautions for the REAL test (FEUILLE_DE_ROUTE §10.2)
    "pressure": "Pressure test: hydrostatic (water), never pneumatic; shield and distance; "
                "relief valve set below the test pressure; nobody in line with closures.",
    "rotation": "Rotating parts: guard in place, hands off, stop at the rated speed first.",
    "marine": "Underwater: SELV only, IP68 connectors, insulation monitor or RCD on any shore "
              "cable; pressure test beyond the maximum depth BEFORE going in the water.",
    "elec": "Electrical: SELV (<= 60 V DC) unless a qualified person is present; fuse near the "
            "battery; Li-ion with a BMS; check polarity before the first power-up.",
    "flight": "Flight/launch: in a permitted area, with the local rules (club, authority), range "
              "clear, the pilot or range officer in charge — never driven by the AI.",
    "load": "Mechanical loading: dead weights or a calibrated actuator, catch the part if it "
            "fails, eye protection, increase the load in steps.",
}


def test_plan(project_dir: Path, name: str, predicted: Optional[Dict[str, str]] = None,
              instruments: Optional[Dict[str, str]] = None) -> Path:
    """The real test as a procedure (test_plan.md) and a sheet to fill in (measurements.csv):
    one row per acceptance quantity, with the model's prediction and the limits; safety notes
    from what the study simulated. `measure {csv: "measurements.csv"}` reads it back."""
    s = load(project_dir, name)
    if s["state"] not in ("ready_to_build", "built", "calibrate", "validated"):
        raise ValueError(f"state is {s['state']}: plan the real test once every criterion is OK")
    if not s["acceptance"]:
        raise ValueError("no real-test acceptance in this study: start it with acceptance {...}")
    links = dict(s.get("links", {}))
    links.update(predicted or {})
    s["links"] = links
    rows = []
    for q, acc in s["acceptance"].items():
        pred = _prediction(s, q, links.get(q))
        rows.append((q, pred, acc.get("min", ""), acc.get("max", ""), (instruments or {}).get(q, "")))
    keys = {r["kind"] for r in s["results"].values()}
    text = json.dumps([r.get("params", {}) for r in s["results"].values()])
    hazards = ["load"]
    if '"pressure"' in text or "pressure_hull" in keys:
        hazards.append("pressure")
    if '"rotation"' in text or "thruster" in keys:
        hazards.append("rotation")
    if keys & {"hydrostatics", "added_mass", "thruster", "pressure_hull", "tether_static"}:
        hazards.append("marine")
    if keys & {"spice", "battery", "wire_sizing", "power_budget", "magnet"}:
        hazards.append("elec")
    if keys & {"rocket_flight", "flight_sim"}:
        hazards.append("flight")
    d = _dir(project_dir, name)
    md = [f"# Real test — study {name} (iteration {s['iteration']})", "", f"Goal: {s['goal']}", "",
          "Performed by a person. The AI prepared this sheet; it never runs the test or drives the rig.",
          "", "## Safety", *[f"- {_SAFETY[h]}" for h in hazards], "",
          "## Hypotheses to check on the real part",
          *[f"- {h['text']} (source: {h['source']})" for h in s["hypotheses"]], "",
          "## Measurements", "", "| quantity | model predicts | accept min | accept max | instrument |",
          "|---|---|---|---|---|"]
    md += [f"| {q} | {'' if p is None else f'{p:.5g}'} | {lo} | {hi} | {ins or 'to fill'} |"
           for q, p, lo, hi, ins in rows]
    md += ["", f"Model/test agreement required: within {s['agreement']:.0%}.",
           "Fill measurements.csv (column `measured`, and `instrument`), then "
           "study measure {csv: \"measurements.csv\"}."]
    (d / "test_plan.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    import csv as _csv
    with open(d / "measurements.csv", "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["quantity", "predicted", "accept_min", "accept_max", "measured", "instrument", "notes"])
        for q, p, lo, hi, ins in rows:
            w.writerow([q, "" if p is None else f"{p:.6g}", lo, hi, "", ins, ""])
    _save(project_dir, s, {"event": "test_plan", "quantities": [r[0] for r in rows], "hazards": hazards})
    return d / "test_plan.md"


def read_sheet(project_dir: Path, name: str, csv_name: str) -> tuple:
    """(data, source) from a filled measurements.csv of the study folder."""
    import csv as _csv
    d = _dir(project_dir, name)
    p = (d / csv_name).resolve()
    if not p.is_relative_to(d.resolve()) or not p.is_file():
        raise ValueError("csv: a file of this study's folder (e.g. measurements.csv)")
    data, src = {}, []
    with open(p, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            v = (row.get("measured") or "").strip()
            if v:
                data[row["quantity"]] = float(v)
                if (row.get("instrument") or "").strip():
                    src.append(f"{row['quantity']}: {row['instrument'].strip()}")
    if not data:
        raise ValueError(f"{csv_name}: no value in the `measured` column yet")
    return data, "; ".join(src) or f"sheet {csv_name}"


def next_action(s: Dict[str, Any]) -> str:
    st = s["state"]
    cur = {k: r for k, r in s["results"].items() if r.get("iteration") == s["iteration"]}
    if st == "design":
        bad = [f"{k} {r['verdict']}" for k, r in cur.items() if r["verdict"] != "OK"]
        todo = [k for k in s["criteria"] if not any(r["kind"] == k for r in cur.values())]
        parts = []
        if bad:
            parts.append(f"fix the design ({', '.join(bad)}), then new_iteration with the change")
        if todo:
            parts.append(f"simulate: {', '.join(todo)}")
        return "; ".join(parts) or "simulate the study's kinds (no result yet)"
    return {"ready_to_build": "every criterion OK: make the fabrication files (export, drawing), "
                              "the human builds it, then action='built'",
            "built": "run the real test, then action='measure' with the values and their source",
            "calibrate": "calibrate {auto: {quantity, param}} fits a model parameter to the test "
                         "(or {factors, note}); or iterate {change} to change the design",
            "validated": "done: action='report' writes the justification file"}[st]


def status(project_dir: Path, name: str) -> Dict[str, Any]:
    s = load(project_dir, name)
    return {"study": name, "goal": s["goal"], "state": s["state"], "iteration": s["iteration"],
            "results": {k: f"{r['verdict']}: {r['summary'][:160]}" for k, r in s["results"].items()},
            "unsourced_hypotheses": [h["text"] for h in s["hypotheses"] if h["source"] == "UNSOURCED"],
            "measurements": {q: m["value"] for q, m in s["measurements"].items()},
            "calibration": s["calibration"], "next": next_action(s)}


def report(project_dir: Path, name: str) -> Path:
    """The justification file: goal, hypotheses with sources, every iteration, tests, gaps."""
    s = load(project_dir, name)
    d = _dir(project_dir, name)
    out = [f"# Study {name}", "", f"Goal: {s['goal']}", f"State: **{s['state']}** (iteration {s['iteration']})", "",
           "## Hypotheses", *[f"- {h['text']} — source: {h['source']} (since iteration {h['since']})"
                               for h in s["hypotheses"]], "", "## Criteria"]
    out += [f"- {k}: {json.dumps(v)}" for k, v in s["criteria"].items()] or ["- none"]
    out += ["", "## Real-test acceptance"] + ([f"- {k}: {json.dumps(v)}" for k, v in s["acceptance"].items()]
                                               or ["- none"])
    for h in s["history"] + [{"iteration": s["iteration"], "state": s["state"], "results": s["results"],
                              "params": s["params"], "measurements": s["measurements"]}]:
        out += ["", f"## Iteration {h['iteration']} ({h['state']})", f"Parameters: `{json.dumps(h['params'])}`"]
        out += [f"- {k}: **{r['verdict']}** — {r['summary']}" for k, r in h["results"].items()]
        out += [f"- measured {q} = {m['value']} ({m['source']})" for q, m in h["measurements"].items()]
    if s["calibration"]:
        out += ["", "## Calibration", f"`{json.dumps(s['calibration'])}`"]
    out += ["", "## Journal", "See journal.jsonl (one line per event).", "",
            "Results are model outputs within the stated hypotheses and the tools' validated range; "
            "they are not a certification."]
    p = d / "report.md"
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return p
