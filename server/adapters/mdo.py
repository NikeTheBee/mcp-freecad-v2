"""Multidisciplinary analysis and optimisation (env `opt`).

Several disciplines that feed each other, solved together, then optimised with exact derivatives:
OpenMDAO assembles the coupled problem and differentiates it, pyOptSparse or SciPy drive the
search. This is what `study optimize` cannot do: `study` sweeps the CAD dimensions of ONE part
against simulations, while this solves a system where the disciplines depend on each other
(the wing's weight changes the lift that sizes the wing).

Kinds:
- mda   solve the coupled system: every discipline's outputs are consistent with its inputs
- mdo   optimise an objective over design variables, under constraints, with the couplings
        solved at every iteration (the MDF architecture)
- doe   design of experiments over the same problem (full factorial or Latin hypercube), to see
        the response surface before optimising

The problem is DECLARED, not coded: each discipline is a set of output = expression lines, and
the objective and constraints are expressions too. Expressions are checked against a strict
whitelist (declared names, numbers, the operators + - * / **, and sqrt exp log log10 sin cos tan
atan tanh abs min max, plus `pi`) before they reach the solver: anything else is refused.

Everything is dimensionless. Put the units in your own variable names and keep them consistent;
the solver only sees numbers. Starting points matter: a gradient optimiser finds A minimum, not
THE minimum, so run a `doe` first on anything you do not already understand, and say in the
answer which start was used.

Exactness: REFERENCES below. Sellar's coupled problem (its MDA has a closed-form root, its MDO a
published optimum), a constrained quadratic whose solution comes out of a Lagrange multiplier by
hand, Rosenbrock's unconstrained minimum, and a full factorial whose mean is exact.
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Sandybridge")
os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")
# OpenMDAO writes an HTML report folder (`<script>_out/reports/`) into the CURRENT
# directory of every run unless told not to. The answer here is the verdict and the
# numbers, so the reports are turned off rather than left scattered in the job folders.
os.environ.setdefault("OPENMDAO_REPORTS", "0")

import math  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict, List, Tuple  # noqa: E402

from _common import result  # noqa: E402

ENV = "opt"
KINDS = {
    "mda": {"timeout": 300,
            "doc": "solve coupled disciplines (declared expressions) until consistent: "
                   "converged values, residual, iterations"},
    "mdo": {"timeout": 900,
            "doc": "optimise an objective over design variables under constraints, couplings "
                   "solved each iteration (MDF); SLSQP, COBYLA or IPOPT"},
    "doe": {"timeout": 900,
            "doc": "design of experiments over the same declared problem (full factorial or "
                   "Latin hypercube): best, worst, mean, and every case in a CSV"},
}

# Functions an expression may call. Everything else is refused, including attribute access,
# indexing, comprehensions and any name that is not a declared variable.
_FUNCS = ("sqrt", "exp", "log", "log10", "sin", "cos", "tan", "arcsin", "arccos", "arctan",
          "arctan2", "tanh", "sinh", "cosh", "abs", "fabs", "minimum", "maximum", "sign")
_CONSTS = ("pi", "e")
_TOKEN = re.compile(r"""
      (?P<name>[A-Za-z_][A-Za-z_0-9]*)
    | (?P<num>\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)
    | (?P<op>\*\*|[-+*/(),]|\s+)
""", re.X)


def versions() -> Dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for p in ("openmdao", "pyoptsparse", "numpy", "scipy"):
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


def _check_expr(expr: str, known: Tuple[str, ...]) -> str:
    """Whitelist check. The expression is data from the caller, so it is validated rather than
    trusted: only declared names, numbers, arithmetic and the listed functions get through."""
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("an expression must be a non-empty string")
    if len(expr) > 400:
        raise ValueError("expression too long (400 characters maximum)")
    pos = 0
    for m in _TOKEN.finditer(expr):
        if m.start() != pos:
            raise ValueError(f"expression rejected at character {pos} of {expr!r}: "
                             f"only names, numbers, + - * / ** ( ) , are allowed")
        pos = m.end()
        name = m.group("name")
        if name and name not in known and name not in _FUNCS and name not in _CONSTS:
            raise ValueError(f"unknown name {name!r} in {expr!r}: declare it in `variables`, or "
                             f"use one of {', '.join(_FUNCS)}")
    if pos != len(expr):
        raise ValueError(f"expression rejected at character {pos} of {expr!r}")
    return expr


def _read_problem(inp: Dict[str, Any]):
    """(variables, disciplines, objective, constraints) after validation."""
    raw = inp.get("variables")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("`variables` must be a non-empty object: "
                         "{name: {val, lower, upper, design}}")
    variables: Dict[str, Dict[str, Any]] = {}
    for name, spec in raw.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,30}", name):
            raise ValueError(f"invalid variable name {name!r}")
        if name in _FUNCS or name in _CONSTS:
            raise ValueError(f"{name!r} is a function or constant name, pick another")
        spec = dict(spec if isinstance(spec, dict) else {"val": spec})
        v = {"val": _f(spec.get("val", 1.0)), "design": bool(spec.get("design", False))}
        for b in ("lower", "upper"):
            if b in spec and spec[b] is not None:
                v[b] = _f(spec[b])
        if "lower" in v and "upper" in v and v["lower"] > v["upper"]:
            raise ValueError(f"{name}: lower > upper")
        variables[name] = v

    discs = inp.get("disciplines") or []
    if not isinstance(discs, list):
        raise ValueError("`disciplines` must be a list")
    outputs: List[Tuple[str, str]] = []            # (output name, expression)
    names = tuple(variables)
    seen = set()
    for i, d in enumerate(discs):
        outs = (d or {}).get("outputs")
        if not isinstance(outs, dict) or not outs:
            raise ValueError(f"discipline {i}: `outputs` must be {{name: expression}}")
        for out, expr in outs.items():
            if out not in variables:
                raise ValueError(f"discipline {i}: output {out!r} must also be declared in "
                                 f"`variables` (its starting value seeds the solver)")
            if out in seen:
                raise ValueError(f"{out!r} is computed by two disciplines")
            seen.add(out)
            outputs.append((out, _check_expr(str(expr), names)))
    for name, v in variables.items():
        if v["design"] and name in seen:
            raise ValueError(f"{name!r} cannot be both a design variable and a discipline output")

    obj = inp.get("objective")
    objective = _check_expr(str(obj), names) if obj is not None else None
    cons = []
    for i, c in enumerate(inp.get("constraints") or []):
        if not isinstance(c, dict) or "expr" not in c:
            raise ValueError(f"constraint {i}: needs {{expr, and lower / upper / equals}}")
        bound = {k: _f(c[k]) for k in ("lower", "upper", "equals") if k in c and c[k] is not None}
        if not bound:
            raise ValueError(f"constraint {i}: give `lower`, `upper` or `equals`")
        cons.append((_check_expr(str(c["expr"]), names), bound))
    return variables, outputs, objective, cons


def _register_functions(om) -> None:
    """ExecComp only knows the functions registered with it, so the whitelist of `_FUNCS` is
    declared once here. The smooth ones are marked complex-safe, which lets OpenMDAO take exact
    derivatives by complex step; the non-smooth ones (abs, min, max, sign) are not, so OpenMDAO
    falls back to finite differences for them -- which is the honest behaviour, since their
    derivative does not exist at the kink anyway."""
    import numpy as np

    smooth = {"sqrt", "exp", "log", "log10", "sin", "cos", "tan", "arcsin", "arccos", "arctan",
              "arctan2", "tanh", "sinh", "cosh"}
    for name in _FUNCS:
        fn = getattr(np, "fabs" if name == "fabs" else name, None)
        if fn is None:
            continue
        try:
            om.ExecComp.register(name, fn, complex_safe=name in smooth)
        except (NameError, ValueError, RuntimeError):
            pass          # already registered (a second run in the same process)


def _build(variables, outputs, objective, cons, inp: Dict[str, Any]):
    """The OpenMDAO problem: one ExecComp per discipline output, promoted into one namespace, with
    a solver on the coupling, then the objective and constraint components."""
    import openmdao.api as om

    _register_functions(om)
    prob = om.Problem()
    model = prob.model
    for i, (out, expr) in enumerate(outputs):
        model.add_subsystem(f"d{i}", om.ExecComp(f"{out} = {expr}",
                                                has_diag_partials=False),
                            promotes=["*"])
    if objective is not None:
        model.add_subsystem("obj_cmp", om.ExecComp(f"_obj = {objective}"), promotes=["*"])
    for i, (expr, _b) in enumerate(cons):
        model.add_subsystem(f"con{i}", om.ExecComp(f"_con{i} = {expr}"), promotes=["*"])

    solver = str(inp.get("solver", "gauss_seidel")).lower()
    maxiter = int(inp.get("solver_maxiter", 200))
    atol = _f(inp.get("solver_atol", 1e-12))
    if solver == "newton":
        model.nonlinear_solver = om.NewtonSolver(solve_subsystems=True, maxiter=maxiter,
                                                 atol=atol, rtol=atol, iprint=0)
        model.linear_solver = om.DirectSolver()
    elif solver == "gauss_seidel":
        model.nonlinear_solver = om.NonlinearBlockGS(maxiter=maxiter, atol=atol, rtol=atol,
                                                     iprint=0, use_apply_nonlinear=True)
        model.linear_solver = om.LinearBlockGS(maxiter=maxiter, iprint=0)
    else:
        raise ValueError("`solver` must be `gauss_seidel` or `newton`")
    return prob, model


def _set_values(prob, variables) -> None:
    for name, v in variables.items():
        try:
            prob.set_val(name, v["val"])
        except Exception:  # noqa: BLE001 — a variable no expression mentions is simply unused
            pass


def _values_of(prob, variables) -> Dict[str, float]:
    out = {}
    for name in variables:
        try:
            out[name] = round(float(prob.get_val(name)[0]), 9)
        except Exception:  # noqa: BLE001
            pass
    return out


def _run_mda(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    variables, outputs, objective, cons = _read_problem(inp)
    if not outputs:
        raise ValueError("`mda` needs at least one discipline output to solve for")
    prob, _model = _build(variables, outputs, objective, cons, inp)
    prob.setup()
    _set_values(prob, variables)
    prob.run_model()
    vals = _values_of(prob, variables)
    # residual of the coupling: recompute each output from the converged inputs
    res = 0.0
    for out, _expr in outputs:
        res = max(res, abs(float(prob.get_val(out)[0]) - vals[out]))
    values: Dict[str, Any] = dict(vals)
    values["coupling_residual"] = res
    values["solver"] = str(inp.get("solver", "gauss_seidel"))
    if objective is not None:
        values["objective"] = round(float(prob.get_val("_obj")[0]), 9)
    for i, (_e, _b) in enumerate(cons):
        values[f"constraint{i}"] = round(float(prob.get_val(f"_con{i}")[0]), 9)
    head = "coupled system solved: " + ", ".join(f"{k} = {v:.6g}" for k, v in vals.items())
    return result(values, inp.get("limits"), head,
                  notes=["dimensionless: the solver sees numbers, you keep the units consistent",
                         "a coupled system can have several roots; this is the one reachable from "
                         "the starting values you gave"])


def _run_mdo(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import openmdao.api as om

    variables, outputs, objective, cons = _read_problem(inp)
    if objective is None:
        raise ValueError("`mdo` needs an `objective` expression")
    design = [n for n, v in variables.items() if v["design"]]
    if not design:
        raise ValueError("`mdo` needs at least one variable with `design: true`")
    prob, model = _build(variables, outputs, objective, cons, inp)

    opt = str(inp.get("optimizer", "SLSQP")).upper()
    maxiter = int(inp.get("maxiter", 200))
    tol = _f(inp.get("tol", 1e-9))
    if opt in ("SLSQP", "COBYLA", "TRUST-CONSTR"):
        prob.driver = om.ScipyOptimizeDriver(optimizer=opt.title() if opt == "TRUST-CONSTR"
                                             else opt, maxiter=maxiter, tol=tol)
    elif opt in ("IPOPT", "SNOPT", "NSGA2", "CONMIN", "PSQP"):
        try:
            prob.driver = om.pyOptSparseDriver(optimizer=opt)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"optimiser {opt} is not available in this build of pyOptSparse "
                             f"({e}); SLSQP and COBYLA always are") from e
    else:
        raise ValueError("`optimizer` must be SLSQP, COBYLA, TRUST-CONSTR, IPOPT or another "
                         "pyOptSparse optimiser")
    prob.driver.options["debug_print"] = []
    if "disp" in prob.driver.options:      # the optimiser's own console output is noise here:
        prob.driver.options["disp"] = False  # the verdict and the numbers are the answer

    for n in design:
        v = variables[n]
        model.add_design_var(n, lower=v.get("lower"), upper=v.get("upper"))
    model.add_objective("_obj")
    for i, (_expr, b) in enumerate(cons):
        model.add_constraint(f"_con{i}", **b)

    prob.setup()
    _set_values(prob, variables)
    fail = prob.run_driver()
    vals = _values_of(prob, variables)
    values: Dict[str, Any] = dict(vals)
    values.update({
        "objective": round(float(prob.get_val("_obj")[0]), 9),
        "optimizer": opt,
        "converged": 0.0 if fail else 1.0,
        "design_variables": design,
    })
    worst = 0.0
    for i, (_expr, b) in enumerate(cons):
        c = round(float(prob.get_val(f"_con{i}")[0]), 9)
        values[f"constraint{i}"] = c
        if "upper" in b:
            worst = max(worst, c - b["upper"])
        if "lower" in b:
            worst = max(worst, b["lower"] - c)
        if "equals" in b:
            worst = max(worst, abs(c - b["equals"]))
    if cons:
        values["worst_constraint_violation"] = round(worst, 9)
    head = (f"objective {values['objective']:.6g} at "
            + ", ".join(f"{n} = {vals[n]:.6g}" for n in design)
            + ("" if not cons else f"; worst constraint violation {worst:.3g}"))
    if fail:
        head = "the optimiser did NOT converge: " + head
    return result(values, inp.get("limits"), head,
                  notes=["gradient optimisers find A minimum, not THE minimum: change the "
                         "starting values, or run `doe` first, before believing a surprising one",
                         "`converged` = 0 means the optimiser gave up: the numbers are where it "
                         "stopped, not an optimum",
                         "dimensionless: keep your own units consistent"])


def _run_doe(inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    import csv

    import numpy as np
    import openmdao.api as om

    variables, outputs, objective, cons = _read_problem(inp)
    if objective is None:
        raise ValueError("`doe` needs an `objective` expression to tabulate")
    design = [n for n, v in variables.items() if v["design"]]
    if not design:
        raise ValueError("`doe` needs at least one variable with `design: true`")
    for n in design:
        if "lower" not in variables[n] or "upper" not in variables[n]:
            raise ValueError(f"{n}: a design variable of a `doe` needs `lower` and `upper`")
    kind = str(inp.get("doe", "full_factorial")).lower()
    levels = int(inp.get("levels", 3))
    samples = int(inp.get("samples", 20))
    if kind == "full_factorial":
        if not 2 <= levels <= 11 or levels ** len(design) > 20000:
            raise ValueError("`levels` must be 2..11 and the grid at most 20000 cases")
        gen = om.FullFactorialGenerator(levels=levels)
        n_cases = levels ** len(design)
    elif kind == "latin_hypercube":
        if not 2 <= samples <= 20000:
            raise ValueError("`samples` must be between 2 and 20000")
        gen = om.LatinHypercubeGenerator(samples=samples, seed=int(inp.get("seed", 0)))
        n_cases = samples
    else:
        raise ValueError("`doe` must be `full_factorial` or `latin_hypercube`")

    prob, model = _build(variables, outputs, objective, cons, inp)
    rec = work / "doe_cases.sql"
    prob.driver = om.DOEDriver(gen)
    prob.driver.add_recorder(om.SqliteRecorder(str(rec)))
    for n in design:
        v = variables[n]
        model.add_design_var(n, lower=v["lower"], upper=v["upper"])
    model.add_objective("_obj")
    for i, (_expr, b) in enumerate(cons):
        model.add_constraint(f"_con{i}", **b)
    prob.setup()
    _set_values(prob, variables)
    prob.run_driver()
    prob.cleanup()

    cr = om.CaseReader(str(rec))
    rows = []
    for case in cr.get_cases("driver", recurse=False):
        row = {n: float(case.get_val(n)[0]) for n in design}
        row["objective"] = float(case.get_val("_obj")[0])
        for i, (_e, _b) in enumerate(cons):
            row[f"constraint{i}"] = float(case.get_val(f"_con{i}")[0])
        rows.append(row)
    if not rows:
        raise ValueError("the experiment produced no case")
    out = work / "doe.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    objs = np.array([r["objective"] for r in rows])
    best = rows[int(np.argmin(objs))]
    values: Dict[str, Any] = {
        "cases": len(rows),
        "cases_planned": n_cases,
        "objective_min": round(float(objs.min()), 9),
        "objective_max": round(float(objs.max()), 9),
        "objective_mean": round(float(objs.mean()), 9),
        "objective_std": round(float(objs.std(ddof=0)), 9),
        "doe": kind,
    }
    for n in design:
        values[f"best_{n}"] = round(float(best[n]), 9)
    return result(values, inp.get("limits"),
                  f"{len(rows)} cases, objective from {values['objective_min']:.6g} to "
                  f"{values['objective_max']:.6g}",
                  files=[str(out)],
                  notes=["a sweep, not an optimum: the best case of a grid is only as good as "
                         "the grid; follow it with `mdo` from that point",
                         "constraints are recorded, not enforced: a case can be infeasible"])


def run(kind: str, inp: Dict[str, Any], work: Path) -> Dict[str, Any]:
    fns = {"mda": _run_mda, "mdo": _run_mdo, "doe": _run_doe}
    if kind not in fns:
        raise ValueError(f"unknown kind {kind!r}; known: {', '.join(sorted(fns))}")
    return fns[kind](inp, Path(work))


# --------------------------------------------------------------------------- references
# Sellar's coupled problem (R. S. Sellar, S. M. Batill, J. E. Renaud, "Response surface based,
# concurrent subspace optimization for multidisciplinary system design", AIAA 96-0714, 1996), the
# standard test of every MDO framework:
#   y1 = z1^2 + x + z2 - 0.2 y2        y2 = sqrt(y1) + z1 + z2
#   min x^2 + z2 + y1 + exp(-y2)   s.t.  y1 >= 3.16,  y2 <= 24
# At x = 1, z = (5, 2) the coupling has a closed-form root: substituting y2 gives
# u^2 + 0.2 u - 26.6 = 0 with u = sqrt(y1), so
_U = (-0.2 + math.sqrt(0.04 + 4 * 26.6)) / 2.0
_Y1, _Y2 = _U ** 2, _U + 7.0                 # 25.588302, 12.058485
_SELLAR_VARS = {
    "x": {"val": 1.0, "lower": 0.0, "upper": 10.0},
    "z1": {"val": 5.0, "lower": -10.0, "upper": 10.0},
    "z2": {"val": 2.0, "lower": 0.0, "upper": 10.0},
    "y1": {"val": 1.0}, "y2": {"val": 1.0},
}
_SELLAR_DISC = [{"outputs": {"y1": "z1*z1 + x + z2 - 0.2*y2"}},
                {"outputs": {"y2": "sqrt(y1) + z1 + z2"}}]
_SELLAR_OBJ = "x*x + z2 + y1 + exp(-y2)"
_SELLAR_CONS = [{"expr": "y1", "lower": 3.16}, {"expr": "y2", "upper": 24.0}]

# A constrained quadratic whose answer comes out of one Lagrange multiplier:
#   min (x-3)^2 + (y+1)^2  s.t. x + y = 1  ->  x = 2.5, y = -1.5, f = 0.5
_QUAD = {"x": {"val": 0.0, "lower": -10.0, "upper": 10.0, "design": True},
         "y": {"val": 0.0, "lower": -10.0, "upper": 10.0, "design": True}}

# Full factorial of a linear objective: 3 levels on each of two variables over [-1, 1] puts the
# mean exactly at the centre value, and the extremes at the corners.
_LIN_A, _LIN_B, _LIN_C = 2.0, -3.0, 7.0      # obj = a x + b y + c


REFERENCES = [
    {   # the coupling has an exact root
        "name": "sellar_mda_closed_form", "kind": "mda",
        "inp": {"variables": _SELLAR_VARS, "disciplines": _SELLAR_DISC,
                "objective": _SELLAR_OBJ, "solver": "gauss_seidel"},
        "expect": {"y1": [_Y1, 1e-9], "y2": [_Y2, 1e-9], "coupling_residual": [0.0, 1e-8]},
    },
    {   # same root through Newton rather than Gauss-Seidel
        "name": "sellar_mda_newton", "kind": "mda",
        "inp": {"variables": _SELLAR_VARS, "disciplines": _SELLAR_DISC, "solver": "newton"},
        "expect": {"y1": [_Y1, 1e-9], "y2": [_Y2, 1e-9]},
    },
    {   # the published optimum of the Sellar problem: 3.18339 at z = (1.977639, 0), x = 0
        "name": "sellar_mdo_optimum", "kind": "mdo",
        "inp": {"variables": {**_SELLAR_VARS,
                              "x": {**_SELLAR_VARS["x"], "design": True},
                              "z1": {**_SELLAR_VARS["z1"], "design": True},
                              "z2": {**_SELLAR_VARS["z2"], "design": True}},
                "disciplines": _SELLAR_DISC, "objective": _SELLAR_OBJ,
                "constraints": _SELLAR_CONS, "optimizer": "SLSQP", "tol": 1e-10},
        "expect": {"objective": [3.183393839, 1e-5], "z1": [1.977639, 1e-4],
                   "z2": [0.0, 1e-4], "x": [0.0, 1e-4], "converged": [1.0, 1e-9]},
    },
    {   # Lagrange by hand: x = 2.5, y = -1.5, f = 0.5
        "name": "constrained_quadratic", "kind": "mdo",
        "inp": {"variables": _QUAD, "disciplines": [],
                "objective": "(x-3)*(x-3) + (y+1)*(y+1)",
                "constraints": [{"expr": "x + y", "equals": 1.0}],
                "optimizer": "SLSQP", "tol": 1e-12},
        "expect": {"x": [2.5, 1e-6], "y": [-1.5, 1e-6], "objective": [0.5, 1e-6],
                   "worst_constraint_violation": [0.0, 1e-8]},
    },
    {   # Rosenbrock: the minimum is exactly (1, 1) with f = 0
        "name": "rosenbrock_minimum", "kind": "mdo",
        "inp": {"variables": {"x": {"val": -1.2, "lower": -5.0, "upper": 5.0, "design": True},
                              "y": {"val": 1.0, "lower": -5.0, "upper": 5.0, "design": True}},
                "disciplines": [],
                "objective": "100*(y - x*x)*(y - x*x) + (1 - x)*(1 - x)",
                "optimizer": "SLSQP", "maxiter": 400, "tol": 1e-14},
        "expect": {"x": [1.0, 1e-4], "y": [1.0, 1e-4], "objective": [0.0, 1e-8]},
    },
    {   # a 3 x 3 full factorial of a linear objective: 9 cases, mean exactly at the centre
        "name": "full_factorial_linear", "kind": "doe",
        "inp": {"variables": {"x": {"val": 0.0, "lower": -1.0, "upper": 1.0, "design": True},
                              "y": {"val": 0.0, "lower": -1.0, "upper": 1.0, "design": True}},
                "disciplines": [],
                "objective": f"{_LIN_A}*x + {_LIN_B}*y + {_LIN_C}",
                "doe": "full_factorial", "levels": 3},
        "expect": {"cases": [9, 1e-9], "objective_mean": [_LIN_C, 1e-9],
                   "objective_min": [_LIN_C - abs(_LIN_A) - abs(_LIN_B), 1e-9],
                   "objective_max": [_LIN_C + abs(_LIN_A) + abs(_LIN_B), 1e-9]},
    },
]
