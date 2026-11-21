# skill-traj

**Load when:** trajectory optimisation, optimal control, ascent/landing/transfer profile,
minimum time or fuel, "quel profil de poussée", NASA OTIS/POST-type work.

`simulate("optimal_control", "", params)`, adapter `traj` (env `opt`, NASA dymos 1.16 on OpenMDAO).
Declared, not coded: `states` {name: rate expression}, `controls` {name: {lower, upper, guess,
smooth}}, `params` {name: number}, `initial`/`final` {state: value}, `objective` `time` |
`min:<state>` | `max:<state>`, `duration_min/max/guess`, `segments`, `order`. Expressions use the
`mdo` whitelist. Answers: `final_time`, `<state>_final`, `<control>_min/max`, `converged`;
file `trajectory.csv`.

**The guess decides the optimum.** A constant pitch guess sends the brachistochrone to a 4.7 s
local optimum instead of 1.80 s; give controls a `[start, end]` ramp and report which guess was
used. `converged: 0` = not an answer. One phase (no staging events yet). Units are yours.

**Validated on:** the brachistochrone against the exact cycloid (1.801604 vs 1.801603 s) and the
minimum-time bang-bang double integrator (2.0010 vs exactly 2). pyCycle (NASA engine cycles) is
installed in the same env; it has no adapter yet.
