# skill-geo

**Load when:** groundwater, seepage, Darcy, head, permeability, aquifer, well, pumping, drawdown,
heat in soil or rock, borehole heat exchanger, geothermal, clay settlement, consolidation, Terzaghi.

## Use the tool
`freecad-layers` -> **`simulate(kind, params)`** with the adapter `geo` (env `geo`: OpenGeoSys 6.5
from the `ogs` wheel, numpy, scipy). No CAD target: the adapter builds a structured block mesh.
Every answer is OK / FAIL / UNKNOWN + numbers. No `limits` -> UNKNOWN ("computed, nothing judged").

Units: SI. m, K in m/s, head in m, k in W/(m K), T in °C. Side fluxes are **positive into the
domain**: m/s (m³/s per m²) or W/m². Flows through a side are m³/s or W: 2-D results are per
`thickness_m` (default 1 m), 1-D per `area_m2` (default 1 m²).

| kind | key params | main outputs |
|---|---|---|
| `ogs_steady` | `physics` groundwater/heat; `size_m` [Lx] / [Lx, Ly] / [Lx, Ly, Lz]; `conductivity` (number, or one value per axis = diagonal anisotropy) or `layers` [{`thickness_m`, `conductivity`}] stacked along `layer_axis` (default the last axis; thicknesses must sum to that size); `cells`; `dirichlet` [{`side`, `value`}] (at least one); `neumann` [{`side`, `flux`}]; `probes` [[x, y, z]] | `head_max_m`/`head_min_m` or `T_max_C`/`T_min_C`, `flow_in_<side>` (each loaded side), `probe_<i>`, `balance_error_rel`, `equivalent_conductivity_series` |
| `consolidation` | `thickness_m` (clay layer), `drainage` top/both, `cv_m2_s`, `mv_1_per_kPa`, `load_kPa`, `times_s` [..] | `U_<i>`, `settlement_mm_<i>`, `U_series_<i>`, `final_settlement_mm`, `t50_s`, `t90_s`, `fe_vs_series_max_dU` |
| `borehole` | `mode` thiem (`K_m_s`, `aquifer_thickness_m`, `well_radius_m`, `radius_of_influence_m`, `drawdown_m` or `pumping_m3_s`, `probe_radii_m`) / theis (+ `storativity`, `pumping_m3_s`, `radius_m`, `time_s`) / radial_heat (`k_W_mK`, `r_inner_m`, `r_outer_m`, `T_inner_C`, `T_outer_C`, `length_m`) / line_source (`q_W_per_m`, `k_W_mK`, `diffusivity_m2_s`, `radius_m`, `time_s`) | `pumping_m3_s`, `drawdown_*_m`, `W_u`, `heat_W(_per_m)`, `delta_T_K` |

Sides: `xmin`..`zmax` of the block. A side with no condition is **impervious / adiabatic**.

Examples:
```
simulate("ogs_steady", params={"size_m": [20, 10], "layer_axis": "y", "cells": [40, 20],
         "layers": [{"thickness_m": 4, "conductivity": 1e-6}, {"thickness_m": 6, "conductivity": 1e-4}],
         "dirichlet": [{"side": "xmin", "value": 12}, {"side": "xmax", "value": 10}],
         "limits": {"flow_in_xmin": {"max": 1e-4}}})
simulate("consolidation", params={"thickness_m": 4, "drainage": "both", "cv_m2_s": 3e-8,
         "mv_1_per_kPa": 5e-4, "load_kPa": 50, "times_s": [3.15e7, 3.15e8],
         "limits": {"settlement_mm_0": {"max": 60}}})
simulate("borehole", params={"mode": "line_source", "q_W_per_m": 40, "k_W_mK": 2.0,
         "diffusivity_m2_s": 1e-6, "radius_m": 0.075, "time_s": 2.6e6})
```

## Read the results honestly
- **Ask the user** for the soil/rock data (K or k per layer, cv, mv) and the boundary values: from
  site tests, a geotechnical report or a cited table. Never invent them; a guessed K can be off by
  orders of magnitude. Record them with `record_step`.
- `ogs_steady`: linear, saturated, steady. No free surface (phreatic line), no unsaturated zone,
  no density-driven flow, no advection of heat by groundwater. Layers are parallel to the block faces.
  Flows come from the discrete balance (reaction at fixed sides): exact for the mesh; a node shared
  by two fixed sides is split half and half (said in the summary).
- `consolidation`: Terzaghi 1-D theory (constant cv and mv, small strain, instant load, no creep or
  secondary compression). OGS solves the excess pore-pressure equation; the series solution is the
  cross-check. More than 0.02 apart -> UNKNOWN (refine `cells`, or the time is too early). It is
  **not** a coupled Biot hydro-mechanical model: say so for 2-D/3-D settlement or embankments.
- `borehole`: closed forms, homogeneous isotropic infinite medium. Thiem needs a radius of influence
  (an assumption: say it); Theis assumes a confined aquifer, full penetration, constant rate; the
  line source ignores the borehole's own thermal resistance and is valid for r²/(4αt) small.
- Underground work (drilling, trenches, tunnels) is a real hazard: these numbers are estimates for
  design, never a statement that an excavation or slope is safe. Never say "safe".

## Exactness (reference cases, run before first use)
| Case | Reference | Tolerance |
|---|---|---|
| 1-D, two fixed heads | Q = K A Δh / L, linear head | 1e-6 |
| 3-D, two layers in series | K_eq = L / Σ(Li/Ki), interface head | 1e-6 |
| 2-D heat, fixed flux + fixed T | T(xmin) = T1 + qL/k, outflow = q·area | 1e-6 |
| Terzaghi consolidation | U = 50 % at Tv 0.197, 90 % at Tv 0.848 (published table) | 1 % |
| Theis well function | W(0.01) = 4.0379 (Theis table) | 1e-4 |
| Kelvin line source | E1(1) = 0.21938 (Abramowitz & Stegun) | 1e-4 |
| Thiem | Q = 2πKbs at R/rw = e | exact |
