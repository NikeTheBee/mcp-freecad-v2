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
                  label: str = "") -> None:
    """Called by simulate: the latest result of each kind (or label) in this iteration."""
    if not exists(project_dir, name):
        return
    s = load(project_dir, name)
    key = label or kind
    s["results"][key] = {"kind": kind, "verdict": res.get("verdict"), "summary": res.get("summary", "")[:400],
                         "values": {k: v for k, v in (res.get("values") or {}).items()
                                    if isinstance(v, (int, float, str))},
                         "inputs_hash": res.get("inputs_hash"), "iteration": s["iteration"]}
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
    s["history"].append({"iteration": s["iteration"], "state": s["state"], "results": s["results"],
                         "params": s["params"], "measurements": s["measurements"]})
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
        s["measurements"][q] = {"value": v, "source": source, "iteration": s["iteration"]}
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
            "calibrate": "action='calibrate' with the corrected model parameters (or new_iteration "
                         "to change the design), then simulate again",
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
