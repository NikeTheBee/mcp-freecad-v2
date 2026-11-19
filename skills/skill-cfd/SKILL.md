# skill-cfd

**Load when:** flow through a duct, nozzle, manifold or channel (pressure drop, flow rate, velocity),
flow around a body or an airfoil (lift, drag, pressure), supersonic flow and shocks, a fluid heated
inside a channel. Fast airfoil polars stay in `skill-aero-sim` (NeuralFoil), and structural strength
in `skill-fem`.

## Use the tool, not hand-written scripts
Workshop `simulate(kind=..., target=..., params=...)`, adapter `server/adapters/cfd.py` (pixi env
`sim`; SU2 8.6 and Elmer from the `fluides` pack). Pass `limits` (`{"dp_Pa": {"max": 500}}`).
Without limits the verdict is UNKNOWN ("computed, nothing judged").

| kind | what | geometry |
|---|---|---|
| `cfd_su2` | SU2 on the solid. `domain="internal"`: the solid IS the fluid (duct air or water volume), needs `inlet` and `outlet` faces. `domain="external"`: the solid sits in a box (`farfield_upstream/downstream/side` in body lengths: 5/10/5) | `target` (STEP, mm) |
| `cfd_su2_shape` | SU2 on a built-in 2-D shape: `shape={"type": "channel"\|"ramp"\|"airfoil", ...}` | none |
| `flow_elmer` | Elmer, incompressible laminar flow inside the solid, plus heat if asked | `target` |
| `flow_elmer_shape` | Elmer on the built-in 2-D `channel` | none |

**Lengths are in mm** (geometry, shapes, probes, `ref_length_mm`, `ref_area_mm2`; `scale`=0.001
converts them to m). Everything else is SI: Pa, m/s, kg/m3, Pa s, K for SU2 temperatures, degC
for Elmer temperatures, W/m3, W/m2.

### Common params
- `solver` (SU2): `EULER`, `NAVIER_STOKES` (laminar), `RANS` (Spalart-Allmaras), `INC_EULER`,
  `INC_NAVIER_STOKES`, `INC_RANS`.
- Compressible: `mach`, `aoa_deg`, `sideslip_deg`, `p_inf_Pa` (101325), `T_inf_K` (288.15),
  `reynolds` (on `ref_length_mm`, needed for the viscous solvers), `gamma`, `R_J_kgK`.
  For an internal inlet, give `inlet_total_pressure_Pa`, `inlet_total_temperature_K` and
  `outlet_pressure_Pa`.
- Incompressible (SU2 `INC_*` and Elmer): `velocity_m_s` (freestream or mean inlet speed, along the
  inward normal), `fluid` = `"air"`, `"water"` or `{density_kg_m3, viscosity_Pa_s, conductivity_W_mK,
  cp_J_kgK}`. `outlet_pressure_Pa` (default 0): pressures are gauge.
- Faces (STEP): `inlet`, `outlet`, `symmetry`, `farfield`, `slip` = lists of `"FaceN"` or
  `xmin..zmax`. Every other face is a no-slip wall.
- Shapes:
  - `channel`: `length_mm`, `height_mm`, `cells_x`, `cells_y`. Markers `inlet`, `outlet`,
    `wall_bottom`, `wall_top`.
  - `ramp` (wedge): `angle_deg`, `upstream_mm`, `ramp_length_mm`, `height_mm`, `cells_y`. Markers
    `inlet`, `top`, `outlet`, `wall`, `ramp`.
  - `airfoil`: `naca` ("2412") or `coordinates` [[x, y], ...] (TE, upper, LE, lower), `chord_mm`,
    `farfield_chords` (30), `surface_cell_mm`. For RANS also `first_cell_mm`, `bl_ratio`,
    `bl_thickness_mm`. Markers `airfoil`, `farfield`.
- `roles` = `{marker: wall|slip|inlet|outlet|farfield|symmetry}` overrides a shape's defaults.
- `probes` = [[x, y(, z)], ...] in mm. Two probes also give `dp_probes_Pa` and, for incompressible
  flow, `fRe_probes`.
- Numerics: `mesh_size` (mm, STEP), `residual_orders` (4), `max_iter` (4000), `cfl`, `scheme`
  (`ROE` with a limiter for shocks, the default; `JST` for smooth subsonic flow), `threads` (8).
- Elmer heat: `heat_source_W_m3`, `temperatures` {marker: degC}, `heat_flux_W_m2` {marker: W/m2},
  `inlet_temperature_C`; `inlet_profile` = `"parabolic"` (built-in channel only); `nonlinear_tol`
  (1e-7).

### Main values
`CL`, `CD`, `CM` (SU2), `p_max_Pa`, `p_min_Pa`, `p_wall_max_Pa`, `p_wall_min_Pa`, `Cp_wall_max/min`,
`p_mean_<marker>_Pa`, `p_median_<marker>_Pa`, `p_ratio_median_<marker>` (p / p_inf, compressible),
`u_max_m_s`, `mach_max`, `mdot_kg_s` (per metre of span in 2-D), `mass_imbalance`, `dp_Pa` (mean
inlet minus mean outlet static pressure), `u_mean_<marker>_m_s`, `u_max_to_mean_<marker>`,
`Dh_<marker>_mm`, `fRe_probes` (Darcy f x Re on Dh: 96 between plates, 64 in a pipe, 56.9 in a square
duct when laminar and developed), probe `p/u/T_probeN`, `T_max_C`/`T_min_C` (Elmer),
`converged`, `residual_drop_orders`, `residual_drops`, `iterations`. Files: `flow.vtu` (or Elmer's
`result_t0001.vtu`), `surface_flow.csv`, `history.csv`, the case file and the solver log.

## Convergence (read it before any number)
- SU2: **every** rms residual must fall `residual_orders` from its peak. Otherwise the summary says
  `NOT CONVERGED` and the verdict is UNKNOWN, even if the limits pass. Fixes, in this order: raise
  `max_iter`, lower `cfl`, use `scheme="JST"` for subsonic flow, refine or smooth the mesh.
- Elmer: the nonlinear relative change must fall below `nonlinear_tol`.
- `mass_imbalance` over 5 % (inlet vs outlet) also makes the verdict UNKNOWN: the mesh is too coarse
  at those faces, or the run is not converged.
- Mesh sensitivity is not automatic. For a number that matters, run again with a finer
  `mesh_size` or more cells. If the value moves more than a few %, it is not mesh-converged; say so.

## Read the results honestly
- A vertex-based code (SU2) puts the inlet corner nodes on the no-slip wall, so the flow rate it
  carries is slightly lower than U x A (about 1/n of the cells across). Use the measured `mdot_kg_s`
  and `u_mean_*`, not the requested velocity.
- Developed-flow values (`fRe_probes`, `u_max_to_mean_outlet`) mean something only after the entrance
  length, about 0.05 Re Dh for laminar flow (thermal: x Pr). Put the probes past it.
- Pressure extremes at sharp corners (a ramp corner, a trailing edge) are numerical spikes. Use the
  medians and means for the physics. Inviscid (Euler) drag of a subsonic body should be zero; what
  you see (the coarse sphere test gives CD 0.14) measures the mesh error, not real drag.
- RANS-SA on the built-in airfoil mesh: lift is close (NACA 0012, Re 6e6, alpha 10 deg: CL 1.087 vs
  about 1.09 in the NASA TMR) but drag is too high (0.021 vs 0.0122), and the residuals stall at
  about 2 orders, so the verdict stays UNKNOWN. For drag, use a dedicated structured C-mesh, or
  NeuralFoil/XFOIL (`skill-aero-sim`).
- Elmer's inlet acts as cold inflow when `inlet_temperature_C` is not given. Temperatures develop
  along the channel over about U h^2 / (alpha pi^2).
- Steady flow only: no vortex shedding, unsteady loads, free surface, cavitation or combustion.
  Laminar unless RANS. 3-D runs are capped at 1.5 M cells (SU2) and 300 k nodes (Elmer); there is
  no boundary-layer mesh on STEP geometry, so wall shear and heat transfer on a real part are
  approximate.

## Exactness (reference cases, run before the first use)
| Case | Source | Got | Tolerance |
|---|---|---|---|
| SU2 INC laminar, plane Poiseuille (uniform inlet, Re_h 10): u_max/u_mean, f Re | White, Viscous Fluid Flow §3-3 | 1.5015, 96.09 (exact 1.5, 96) | 1 % |
| SU2 Euler, Mach 2 over a 10 deg wedge: p2/p1 on the ramp | theta-beta-M + normal shock, NACA Report 1135 | 1.7052 (exact 1.7066) | 1 % |
| Elmer flow + heat, same channel, heat source, walls 0 degC: u_max/u_mean, f Re, centre T | White; Incropera eq. 3.42 | 1.4979, 96.03, 49.92 degC (exact 50) | 1 % |

Each case must also report `converged = 1`.
