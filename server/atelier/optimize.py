"""Automatic design correction: vary design parameters until the criteria hold, best objective.

Typical question: "the lightest wall thickness that keeps the safety factor >= 2", "the smallest
battery that gives 2 h", "the fin span that gives a static margin of 1.5 calibers".

search(evaluate, variables, objective, sense, constraints, max_runs):
- evaluate(x: dict) -> dict of values (or None when the design is broken: infeasible);
- variables: [{"name", "lo", "hi"}] (1 to 3);
- constraints: {value: {"min"|"max": x}} - the same form as simulate limits;
- objective: a value to minimise or maximise, or None = only satisfy the constraints, choosing
  the variable closest to `lo` (e.g. the thinnest that holds).

1 variable: a coarse grid, then the feasibility boundary is bisected (where the optimum of a
monotone problem sits) or, without a boundary next to the best point, a golden-section step in
its neighbourhood. 2-3 variables: coordinate sweeps of the 1-variable search. Every evaluation is
kept (the table of runs is the proof). Pure Python: the callback does the FreeCAD and solver work.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional

GOLD = (math.sqrt(5) - 1) / 2


def margins(values: Optional[Dict[str, Any]], constraints: Dict[str, Dict[str, float]]) -> Optional[float]:
    """Smallest relative margin over the constraints (>= 0: all hold); None: not evaluable."""
    if values is None:
        return None
    worst = math.inf
    for key, lim in constraints.items():
        v = values.get(key)
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        if "min" in lim:
            a = float(lim["min"])
            worst = min(worst, (v - a) / max(abs(a), 1e-12))
        if "max" in lim:
            b = float(lim["max"])
            worst = min(worst, (b - v) / max(abs(b), 1e-12))
    return worst if worst != math.inf else 0.0


class Search:
    def __init__(self, evaluate: Callable[[Dict[str, float]], Optional[Dict[str, Any]]],
                 variables: List[Dict[str, Any]], objective: Optional[str], sense: str,
                 constraints: Dict[str, Dict[str, float]], max_runs: int):
        if not 1 <= len(variables) <= 3:
            raise ValueError("optimize: 1 to 3 variables")
        for v in variables:
            if not float(v["lo"]) < float(v["hi"]):
                raise ValueError(f"variable {v.get('name')}: lo must be < hi")
        if sense not in ("min", "max"):
            raise ValueError("sense: min or max")
        if not constraints and not objective:
            raise ValueError("optimize needs constraints and/or an objective")
        self.evaluate, self.vars = evaluate, variables
        self.objective, self.sense, self.constraints = objective, sense, constraints
        self.max_runs = int(max_runs)
        self.runs: List[Dict[str, Any]] = []

    # score: feasible designs first, then the objective (or closeness to lo)
    def _score(self, run) -> tuple:
        if not run["feasible"]:
            m = run["margin"]
            return (1, -(m if m is not None else -math.inf))
        if self.objective:
            v = run["values"].get(self.objective)
            if not isinstance(v, (int, float)):
                return (1, math.inf)
            return (0, v if self.sense == "min" else -v)
        v0 = self.vars[0]
        return (0, (run["x"][v0["name"]] - float(v0["lo"])) / (float(v0["hi"]) - float(v0["lo"])))

    def run_at(self, x: Dict[str, float]) -> Dict[str, Any]:
        for r in self.runs:                                   # never twice the same design
            if all(abs(r["x"][k] - x[k]) <= 1e-12 * max(1.0, abs(x[k])) for k in x):
                return r
        if len(self.runs) >= self.max_runs:
            raise StopIteration
        vals = self.evaluate(dict(x))
        m = margins(vals, self.constraints)
        r = {"n": len(self.runs) + 1, "x": dict(x), "values": vals or {},
             "margin": m, "feasible": m is not None and m >= 0}
        self.runs.append(r)
        return r

    def best(self) -> Optional[Dict[str, Any]]:
        return min(self.runs, key=self._score) if self.runs else None

    def _line(self, base: Dict[str, float], var: Dict[str, Any], budget: int) -> None:
        name, lo, hi = var["name"], float(var["lo"]), float(var["hi"])
        n0 = max(3, min(5, budget // 2 + 1))
        grid = [lo + (hi - lo) * i / (n0 - 1) for i in range(n0)]
        pts = [self.run_at(dict(base, **{name: g})) for g in grid]
        stop = len(self.runs) + max(0, budget - n0)
        while len(self.runs) < stop:
            line = sorted((r for r in self.runs if all(abs(r["x"][k] - base[k]) < 1e-12 or k == name
                                                       for k in base)), key=lambda r: r["x"][name])
            bi = min(range(len(line)), key=lambda i: self._score(line[i]))
            b = line[bi]
            nb = [line[j] for j in (bi - 1, bi + 1) if 0 <= j < len(line)]
            edge = [r for r in nb if r["feasible"] != b["feasible"]]
            if edge:                                          # bisect the feasibility boundary
                o = edge[0]
                x = (b["x"][name] + o["x"][name]) / 2
            elif len(nb) == 2:                                # golden step toward the better side
                lo_r, hi_r = nb
                side = lo_r if self._score(lo_r) < self._score(hi_r) else hi_r
                x = b["x"][name] + (1 - GOLD) * (side["x"][name] - b["x"][name])
            else:
                o = nb[0]
                x = (b["x"][name] + o["x"][name]) / 2
            if any(abs(r["x"][name] - x) <= 1e-9 * max(1.0, abs(x)) for r in line):
                break                                         # converged to the resolution
            self.run_at(dict(base, **{name: x}))
        del pts

    def solve(self) -> Dict[str, Any]:
        x0 = {v["name"]: float(v.get("start", (float(v["lo"]) + float(v["hi"])) / 2)) for v in self.vars}
        try:
            if len(self.vars) == 1:
                self._line(x0, self.vars[0], self.max_runs)
            else:
                # coordinate sweeps, each line refined (>= 2 steps past its grid), until the
                # budget is spent or a whole sweep finds nothing new
                per = max(5, self.max_runs // (2 * len(self.vars)))
                while len(self.runs) < self.max_runs:
                    before = len(self.runs)
                    for v in self.vars:
                        b = self.best()
                        base = dict(b["x"]) if b else x0
                        self._line(base, v, per)
                    if len(self.runs) == before:
                        break
        except StopIteration:
            pass
        b = self.best()
        return {"best": b, "runs": self.runs, "feasible": bool(b and b["feasible"])}


def search(evaluate, variables, objective=None, sense="min", constraints=None, max_runs=12):
    return Search(evaluate, variables, objective, sense, constraints or {}, max_runs).solve()
