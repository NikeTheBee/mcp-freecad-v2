# skill-foam

**Load when:** OpenFOAM, Fluent-type CFD, free surface / VOF, sloshing, cavitation, rotating zone
(MRF), six-DOF body in flow, turbulence model other than SU2's, "run this OpenFOAM case".

`freecad-layers` → **`simulate(kind, target, params)`**, adapter `foam` (env `sim`, pack
`fluides-plus`: blueCFD-Core 2024-1 = OpenFOAM 12 native Windows, serial).

| kind | params | main values |
|---|---|---|
| `foam_couette` | `height_m`, `wall_velocity_m_s`, `nu_m2_s`, `cells_across` | `profile_max_error_rel`, `u_mid_m_s` |
| `foam_channel` | `height_m`, `mean_velocity_m_s`, `nu_m2_s`, `length_to_height`, `cells_across`, `cells_along` | `u_max_over_mean`, `dpdx_kinematic`, `dpdx_error_rel`, `profile_max_error_rel`, `reynolds` |
| `foam_case` | `solver` (module: `incompressibleFluid`, `incompressibleVoF`, `multiphaseEuler`…), `timeout_s`; the case via `params.files` (keep `system/`, `constant/`, `0/`) | `refused`, `last_time`, `fields_written`, `residuals`, `max_final_residual` |

**Security:** a supplied case is scanned first. `#codeStream`, `#calc`, `coded*`, `libs (...)`,
`systemCall`, or an `#include` from outside the case → **refused, nothing runs** (OpenFOAM can
compile and execute C++ from a dictionary, and blueCFD ships the compiler). Write cases without
them; if one is truly needed, a human reads it.

**Reading:** pressure is kinematic (p/ρ). No `limits` → UNKNOWN. For `foam_case`, judge
`max_final_residual` (above ~1e-3 usually means not converged) and say the physics/mesh are the
case's own. Serial only. Fields open in ParaView (env `post`).

**Validated on:** Couette's exact linear profile, Poiseuille's exact parabola and
dp/dx = −12 ν U / h², and the refusal of a case hiding `#codeStream`.
