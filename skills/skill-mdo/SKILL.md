# skill-mdo

**Load when:** several disciplines depend on each other and must be sized together ("the wing's
weight changes the lift that sizes the wing"), multidisciplinary optimisation, MDO, MDA, coupled
sizing, trade study, design of experiments, Pareto/response surface, "optimise the whole thing,
not one part".

## Not the same tool as `study optimize`
- `study optimize` (skill-atelier) sweeps the **CAD dimensions of one part** against real
  simulations and keeps the best. Use it for a bracket, a housing, a shaft.
- `mdo` solves a **system of disciplines that feed each other**, with exact derivatives, and
  optimises it in one shot. Use it for a vehicle-level sizing loop.

## Use the tool
`freecad-layers` -> **`simulate(kind, target, params)`**, adapter `mdo` (env `opt`).
Everything is **declared**, not coded: each discipline is `output = expression`, and the objective
and constraints are expressions too. Expressions go through a strict whitelist (declared names,
numbers, `+ - * / **`, and sqrt exp log log10 sin cos tan atan tanh abs min max sign, plus `pi`);
anything else is refused, so a typo is an error, never a surprise.

| kind | params | main values |
|---|---|---|
| `mda` | `variables` {name: {val, lower, upper, design}}, `disciplines` [{outputs: {name: expr}}], `solver` `gauss_seidel`\|`newton`, `solver_maxiter`, `solver_atol` | every variable's converged value, `coupling_residual` |
| `mdo` | the same plus `objective`, `constraints` [{expr, lower\|upper\|equals}], `optimizer` (`SLSQP`, `COBYLA`, `TRUST-CONSTR`, or pyOptSparse's `IPOPT`…), `maxiter`, `tol` | `objective`, every variable at the optimum, `constraint<i>`, `worst_constraint_violation`, `converged` |
| `doe` | the same plus `doe` `full_factorial`\|`latin_hypercube`, `levels` or `samples`, `seed` | `objective_min`/`max`/`mean`/`std`, `best_<var>`, `cases`; file `doe.csv` |

Mark a variable `design: true` to let the optimiser move it (it then needs `lower` and `upper`).
A discipline output must also be declared in `variables`: its value seeds the coupling solver.

## How to read the answer
- **`converged: 0` means the optimiser gave up.** The numbers are where it stopped, not an
  optimum. Say so rather than quoting them as a result.
- A gradient optimiser finds **a** minimum. On anything you do not already understand, run `doe`
  first and start `mdo` from the best case; state which starting point you used.
- `coupling_residual` is how consistent the disciplines are. If it is not tiny, the system did not
  really converge and the optimum means nothing.
- A coupled system can have several roots: the one you get is the one reachable from the starting
  values you gave.
- Everything is dimensionless. Keep your own units consistent; the solver only sees numbers.

## Validated on
Sellar's coupled problem, the standard test of every MDO framework: its MDA has a closed-form
root (y1 = 25.588302, y2 = 12.058488 at x = 1, z = (5, 2), matched to 1e-9 by both solvers) and
its MDO a published optimum (3.183394 at z = (1.977639, 0), x = 0, reproduced to 7 digits). Plus
a constrained quadratic solved by hand with a Lagrange multiplier, Rosenbrock's minimum, and a
full factorial whose mean is exact. See `REFERENCES` in `server/adapters/mdo.py`.

## Limits
Expressions only - a discipline that needs a real solver (FEM, CFD) is not plugged in yet: run it
through `simulate`, fit a surrogate or a simple expression to those runs, and declare that. No
integer or categorical variables, no multi-objective Pareto front (optimise one objective with
the others as constraints), no uncertainty propagation.
