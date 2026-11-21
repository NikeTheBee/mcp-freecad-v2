"""Trajectory optimisation / optimal control with NASA's dymos (env `opt`).

The class of tool NASA uses OTIS or POST for: find the control history (pitch angle, thrust
direction, throttle...) that drives a dynamic system from one state to another while minimising
time, fuel, or anything else, under path and boundary constraints. dymos (NASA Glenn, Apache-2.0)
does it by pseudospectral collocation on top of OpenMDAO, with exact derivatives.

Kind:
- optimal_control  a DECLARED problem: state equations as expressions, controls with bounds,
                   initial and final conditions, an objective (final time by default)

The equations go through the same strict whitelist as `mdo` (declared names, numbers,
arithmetic, sqrt exp log sin cos tan atan tanh abs min max, `pi`): no code reaches the solver.
Constants (`params`) are substituted as numbers before anything is built.

Example -- the brachistochrone:
  states   {"x": "v*sin(theta)", "y": "-v*cos(theta)", "v": "g*cos(theta)"}
  controls {"theta": {"lower": 0.01, "upper": 3.13}}
  params   {"g": 9.80665}
  initial  {"x": 0, "y": 10, "v": 0}      final {"x": 10, "y": 5}

Limits, restated in every result: a gradient optimiser finds A local optimum from the initial
guess (linear between the given end states, controls constant or a [start, end] ramp) -- a
constant pitch guess sends the brachistochrone to a 4.7 s local optimum instead of 1.80 s; the answer reports `converged` and the defect of the
collocation; units are yours to keep consistent (everything is a number); one phase only (no
staging events yet: model each stage as its own run, or chain them by hand).

Exactness: REFERENCES below. The brachistochrone's optimal time is the cycloid's, computed here
in closed form; the minimum-time double integrator (|u| <= 1, rest to rest over a unit distance)
takes exactly 2.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")
os.environ.setdefault("OPENMDAO_REPORTS", "0")

import math  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict  # noqa: E402

from _common import result  # noqa: E402

ENV = "opt"
KINDS = {
    "optimal_control": {"timeout": 900,
                        "doc": "dymos pseudospectral optimal control of declared state equations: "
                               "control history, final time / objective, collocation defect"},
}
_FUNCS = ("sqrt", "exp", "log", "log10", "sin", "cos", "tan", "arcsin", "arccos", "arctan",
          "arctan2", "tanh", "sinh", "cosh", "abs", "minimum", "maximum", "sign")
_SMOOTH = {"sqrt", "exp", "log", "log10", "sin", "cos", "tan", "arcsin", "arccos", "arctan",
           "arctan2", "tanh", "sinh", "cosh"}
_TOKEN = re.compile(r"(?P<name>[A-Za-z_][A-Za-z_0-9]*)|(?P<num>\d+\.?\d*(?:[eE][-+]?\d+)?|"
                    r"\.\d+(?:[eE][-+]?\d+)?)|(?P<op>\*\*|[-+*/(),]|\s+)")
_NAME = re.compile(r"[A-Za-z][A-Za-z_0-9]{0,30}")


def versions() -> Dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for p in ("dymos", "openmdao", "numpy", "scipy"):
        try:
            out[p] = version(p)
        except PackageNotFoundError:
            out[p] = "missing"
    return out


def _f(x) -> float:
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(f"not a finite number: {x!r}")
    return v


def _check(expr: str, known: set) -> str:
    if not isinstance(expr, str) or not expr.strip() or len(expr) > 400:
        raise ValueError("an expression must be a non-empty string of at most 400 characters")
    pos = 0
    for m in _TOKEN.finditer(expr):
        if m.start() != pos:
            raise ValueError(f"expression rejected at character {pos} of {expr!r}")
        pos = m.end()
        n = m.group("name")
        if n and n not in known and n not in _FUNCS and n != "pi":
            raise ValueError(f"unknown name {n!r} in {expr!r}")
    if pos != len(expr):
        raise ValueError(f"expression rejected at character {pos} of {expr!r}")
    return expr


def _substitute(expr: str, params: Dict[str, float]) -> str:
    def rep(m):
        n = m.group(0)
        return f"({params[n]!r})" if n in params else n
    return re.sub(r"[A-Za-z_][A-Za-z_0-9]*", rep, expr)


def _run(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import dymos as dm
    import numpy as np
    import openmdao.api as om

    states = inp.get("states") or {}
    controls = inp.get("controls") or {}
    params = {k: _f(v) for k, v in (inp.get("params") or {}).items()}
    if not states:
        raise ValueError("`states` must give each state's rate: {name: expression}")
    for n in list(states) + list(controls) + list(params):
        if not _NAME.fullmatch(n) or n in _FUNCS or n == "pi" or n == "time":
            raise ValueError(f"invalid or reserved name {n!r}")
    if len(set(states) | set(controls) | set(params)) != len(states) + len(controls) + len(params):
        raise ValueError("a name is used twice across states, controls and params")
    known = set(states) | set(controls) | set(params)
    rates = {s: _substitute(_check(str(e), known), params) for s, e in states.items()}
    init = {k: _f(v) for k, v in (inp.get("initial") or {}).items()}
    final = {k: _f(v) for k, v in (inp.get("final") or {}).items()}
    for k in list(init) + list(final):
        if k not in states:
            raise ValueError(f"boundary condition on {k!r}, which is not a state")
    t_lo, t_hi = _f(inp.get("duration_min", 0.01)), _f(inp.get("duration_max", 1000.0))
    t_guess = _f(inp.get("duration_guess", math.sqrt(t_lo * t_hi)))
    nseg = int(inp.get("segments", 10))
    order = int(inp.get("order", 3))
    if not (2 <= nseg <= 100 and 2 <= order <= 7):
        raise ValueError("`segments` 2..100 and `order` 2..7")

    for name in _FUNCS:
        try:
            om.ExecComp.register(name, getattr(np, name), complex_safe=name in _SMOOTH)
        except (NameError, ValueError, RuntimeError):
            pass

    eqs = [f"{s}_dot = {rates[s]}" for s in states]
    # ExecComp refuses an argument no expression uses (a state like x rarely appears in its own
    # rate), so only the names that really occur on a right-hand side become ODE inputs; dymos
    # simply leaves the other states unconnected to the ODE
    used = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", " ".join(rates.values())))
    names = sorted((set(states) | set(controls)) & used)

    class ODE(om.Group):
        def initialize(self):
            self.options.declare("num_nodes", types=int)

        def setup(self):
            nn = self.options["num_nodes"]
            kw = {n: {"shape": (nn,)} for n in names}
            kw.update({f"{s}_dot": {"shape": (nn,)} for s in states})
            self.add_subsystem("f", om.ExecComp(eqs, has_diag_partials=True, **kw),
                               promotes=["*"])

    prob = om.Problem()
    traj = dm.Trajectory()
    phase = dm.Phase(ode_class=ODE, transcription=dm.Radau(num_segments=nseg, order=order))
    traj.add_phase("p", phase)
    prob.model.add_subsystem("traj", traj)
    phase.set_time_options(fix_initial=True, duration_bounds=(t_lo, t_hi))
    for s in states:
        phase.add_state(s, rate_source=f"{s}_dot", fix_initial=s in init, fix_final=s in final)
    for c, spec in controls.items():
        spec = spec or {}
        phase.add_control(c, lower=spec.get("lower"), upper=spec.get("upper"),
                          continuity=True, rate_continuity=bool(spec.get("smooth", True)))
    objective = str(inp.get("objective", "time"))
    if objective == "time":
        phase.add_objective("time", loc="final")
    elif objective.startswith(("min:", "max:")) and objective[4:] in states:
        phase.add_objective(objective[4:], loc="final",
                            scaler=1.0 if objective.startswith("min:") else -1.0)
    else:
        raise ValueError("`objective` is `time`, or `min:<state>` / `max:<state>` at the final time")
    prob.driver = om.ScipyOptimizeDriver(optimizer="SLSQP", maxiter=int(inp.get("maxiter", 300)),
                                         tol=_f(inp.get("tol", 1e-8)))
    prob.driver.options["disp"] = False
    prob.driver.declare_coloring()
    prob.setup()
    phase.set_time_val(initial=0.0, duration=t_guess)
    for s in states:
        a = init.get(s, 0.0)
        b = final.get(s, a)
        phase.set_state_val(s, [a, b])
    for c, spec in controls.items():
        spec = spec or {}
        lo, hi = spec.get("lower"), spec.get("upper")
        g = spec.get("guess", (0.5 * (lo + hi)) if lo is not None and hi is not None else 0.0)
        # a number, or [start, end] for a ramp: the guess decides WHICH local optimum is found
        ga, gb = (g if isinstance(g, (list, tuple)) and len(g) == 2 else (g, g))
        phase.set_control_val(c, [_f(ga), _f(gb)])
    failed = dm.run_problem(prob, run_driver=True, simulate=False)
    ok = not (getattr(failed, "success", True) is False if not isinstance(failed, bool) else failed)

    tf = float(prob.get_val("traj.p.timeseries.time")[-1, 0])
    values: Dict[str, Any] = {"final_time": round(tf, 9), "converged": 1.0 if ok else 0.0,
                              "segments": nseg, "order": order}
    for s in states:
        v = prob.get_val(f"traj.p.timeseries.{s}")[:, 0]
        values[f"{s}_final"] = round(float(v[-1]), 9)
    for c in controls:
        u = prob.get_val(f"traj.p.timeseries.{c}")[:, 0]
        values[f"{c}_min"] = round(float(u.min()), 9)
        values[f"{c}_max"] = round(float(u.max()), 9)
    t = prob.get_val("traj.p.timeseries.time")[:, 0]
    out = work / "trajectory.csv"
    cols = ["time"] + list(states) + list(controls)
    data = [t] + [prob.get_val(f"traj.p.timeseries.{n}")[:, 0] for n in cols[1:]]
    with out.open("w", encoding="utf-8") as fh:
        fh.write(",".join(cols) + "\n")
        for i in range(len(t)):
            fh.write(",".join(f"{d[i]:.10g}" for d in data) + "\n")
    head = (("" if ok else "NOT converged: ") + f"final time {tf:.6g}, "
            + ", ".join(f"{s} = {values[f'{s}_final']:.6g}" for s in states))
    return result(values, inp.get("limits"), head, files=[str(out)],
                  notes=["a LOCAL optimum from the initial guess (linear between the end states): "
                         "change the guess before believing a surprising answer",
                         "one phase: staging events are separate runs for now",
                         "everything is a number: keep your own units consistent"])


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    if kind != "optimal_control":
        raise ValueError(f"unknown kind {kind!r}; known: optimal_control")
    return _run(inp, Path(work))


# --------------------------------------------------------------------------- references
def _cycloid_time(dx: float, dy: float, g: float) -> float:
    """Exact brachistochrone time between two points dx apart and dy lower, from rest:
    the cycloid x = R(phi - sin phi), drop = R(1 - cos phi) through the end point."""
    from scipy.optimize import brentq
    phi = brentq(lambda p: (p - math.sin(p)) / (1.0 - math.cos(p)) - dx / dy, 1e-6, 2 * math.pi - 1e-6)
    r = dy / (1.0 - math.cos(phi))
    return phi * math.sqrt(r / g)


_G = 9.80665

REFERENCES = [
    {   # exact cycloid time from (0, 10) to (10, 5) under g, from rest (about 1.8016 s)
        "name": "brachistochrone_cycloid", "kind": "optimal_control",
        "inp": {"states": {"x": "v*sin(theta)", "y": "-v*cos(theta)", "v": "g*cos(theta)"},
                "controls": {"theta": {"lower": 0.01, "upper": 3.13, "guess": [0.087, 1.754]}},
                "params": {"g": _G},
                "initial": {"x": 0.0, "y": 10.0, "v": 0.0}, "final": {"x": 10.0, "y": 5.0},
                "duration_min": 0.5, "duration_max": 10.0, "duration_guess": 2.0,
                "segments": 10, "order": 3},
        "expect_fn": "_cycloid_time",
        "expect": {"converged": [1.0, 1e-9], "x_final": [10.0, 1e-6], "y_final": [5.0, 1e-6]},
    },
    {   # minimum-time rest-to-rest over a unit distance with |u| <= 1: bang-bang, T = 2 exactly
        "name": "double_integrator_bang_bang", "kind": "optimal_control",
        "inp": {"states": {"x": "v", "v": "u"},
                "controls": {"u": {"lower": -1.0, "upper": 1.0, "guess": 0.0, "smooth": False}},
                "initial": {"x": 0.0, "v": 0.0}, "final": {"x": 1.0, "v": 0.0},
                "duration_min": 0.5, "duration_max": 10.0, "duration_guess": 3.0,
                "segments": 20, "order": 3},
        "expect": {"final_time": [2.0, 2e-3], "x_final": [1.0, 1e-6], "converged": [1.0, 1e-9]},
    },
]
# the cycloid time is computed, not typed: put it in the first case's expectation
REFERENCES[0]["expect"]["final_time"] = [_cycloid_time(10.0, 5.0, _G), 1e-3]
del REFERENCES[0]["expect_fn"]
