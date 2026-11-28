# skill-aero-sim

**Load when:** airfoil or wing aerodynamics, rocket nozzle sizing, propellant performance (c*, Isp,
flame temperature), a rocket's flight (apogee, rail exit speed, stability), properties of a fluid.
The FreeCAD workbenches stay in `skill-rocket` (Barrowman, geometry) and `skill-drone` (AirPlaneDesign).

## Use the tool, not hand-written scripts
Workshop `simulate(kind=..., params=...)`, adapter `server/adapters/aero.py` (pixi env `aero`).
No geometry: every input is a number. Offline (no weather, no download). Pass `limits`
(`{"apogee_m": {"min": 500}}`) or the verdict is UNKNOWN ("computed, nothing judged").

| kind | what | key params (units in the name) | main values |
|---|---|---|---|
| `airfoil` | NeuralFoil polar | `airfoil` ("naca2412", UIUC name) or `coordinates`, `Re`, `mach`, `alpha_range` [lo, hi, step] or `alpha_deg`, `n_crit` (9) | `CL_max`, `alpha_CL_max_deg`, `LD_max`, `CD_min`, `lift_slope_per_rad`, `alpha_L0_deg`, `min_confidence`, `polar` |
| `wing` | VLM (default) or `method="aerobuildup"` | `span_m`, `root_chord_m`, `tip_chord_m`, `sweep_le_deg`, `dihedral_deg`, `twist_tip_deg`, `planform` (trapezoid/elliptic), `airfoil`, `alpha_deg` list, `velocity_m_s` | `CL`, `CDi`, `span_efficiency`, `CL_alpha_per_rad`, `aspect_ratio` |
| `isentropic` | exact ratios | `gamma` + one of `mach`, `area_ratio` (+`branch`), `p_ratio`, `T_ratio` | `mach`, `A_Astar`, `p_p0`, `T_T0`, `prandtl_meyer_deg` |
| `nozzle` | ideal nozzle, constant gamma | `gamma`, `pc_bar`, `Tc_K`, `molar_mass_g_mol`, `area_ratio` (or none = optimum for `pa_bar`), `pa_bar` (1.01325), `throat_diameter_mm` | `exit_mach`, `pe_bar`, `c_star_m_s`, `CF`, `CF_vac`, `Isp_s`, `Isp_vac_s`, `thrust_N` |
| `combustion` | Cantera equilibrium + expansion | `fuel`, `oxidizer` (name or {species: moles}), `of_ratio` or `phi`, `pc_bar`, `area_ratio` and/or `pa_bar`, `mechanism` (nasa / gri30 / h2o2), `h_fuel_kJ_mol`, `h_ox_kJ_mol`, or `reactants` [{name, moles or mass, T_K, h_kJ_mol}] | `T_chamber_K`, `M_chamber_g_mol`, `gamma_s_equilibrium`, `c_star_eq_m_s`, `Isp_vac_eq_s`, `Isp_eq_s`, and the `_frozen` values |
| `rocket_flight` | RocketPy 6-DOF to apogee | `thrust_curve` [[t, N]...], `propellant_mass_kg`, `radius_m`, `dry_mass_kg` (without motor), `cg_m`, `inertia_kg_m2`, `cd` or `drag_curve` [[mach, cd]...], `nose` {length_m, kind, tip_position_m}, `fins` {n, root_chord_m, tip_chord_m, span_m, position_m}, `rail_length_m`, `inclination_deg` (85) | `apogee_m` (above ground), `max_speed_m_s`, `max_acceleration_g`, `rail_exit_velocity_m_s`, `static_margin_rail_exit_cal` |
| `fluid_props` | CoolProp | `fluid` (Water, Nitrogen, NitrousOxide...), `T_C` or `T_K`, `p_bar` | `density_kg_m3`, `viscosity_Pa_s`, `cp_J_kgK`, `p_sat_bar`, `T_sat_K` |
| `control_surface` | Glauert thin-airfoil theory (closed-form flap loading) | `flap_chord_ratio` (c_f/c), `chord_m` (local mean), `flap_span_m`, `speed_m_s` (top speed), `deflection_deg` (20), `alpha_deg` (5, local), `horn_to_arm_ratio`, `servo_stall_torque_kgcm`, `viscous_factor` (1) | `tau`, `Cl_delta_per_rad`, `Ch_alpha_per_rad`, `Ch_delta_per_rad`, `hinge_moment_Nm`, `servo_torque_kgcm`, `servo_margin` |

Rocket positions are in m **from the tail** (nozzle exit = 0). Nose `tip_position_m` = rocket length.
Fin `position_m` = leading edge of the root chord.

## Read the results honestly
- `airfoil`: NeuralFoil approximates XFOIL. Trust it where `min_confidence` > 0.7. Post-stall and very
  low Re (< 1e5) are weak. `CL_max` at the end of the alpha range is not a stall peak.
- `wing` VLM is inviscid and linear: no profile drag, no stall. Add section drag from `airfoil`,
  or use `aerobuildup`.
- `nozzle` and `combustion` are ideal, 1-D and isentropic. Real Isp is about 90-97 % of these values
  (divergence, friction, heat loss, combustion efficiency). `pe < 0.4 pa` means the flow separates
  in the nozzle and the computed CF is not reached.
- `combustion` is gas phase only (no soot, no condensed water). Shifting (equilibrium) bounds Isp
  from above and frozen from below; the truth is in between. Reactants are gases at `T_K` unless you
  give `h_kJ_mol`. For LOX, LH2 or LCH4, take the liquid enthalpy from NASA CEA, otherwise Isp
  comes out about 1-2 % high. Use `mechanism="nasa"` above 3500 K (GRI-Mech data stops there).
- `rocket_flight` uses a standard atmosphere with no wind. Without `inertia_kg_m2` it estimates the
  inertia (from CAD is better). A static margin under 1 caliber or a rail exit speed under about 15 m/s
  is flagged. These are rules of thumb, not your requirement: put the user's limits in `limits`.
- Never call a design "safe". A pressure vessel or chamber needs `fem_static` and a recorded
  safety factor `require`. Real firing tests need protections and distance (roadmap §10.2).

## Out of scope (refused)
Solid propellant formulation. Reactants are limited to gases and (L) liquids of C, H, O, N, Ar and He,
so no metals and no perchlorate or nitrate salts. Any weapon use. No G-code, no commands to real
hardware.

## Exactness (reference cases, run before the first use)
| Case | Source | Tolerance |
|---|---|---|
| A/A* = 1.6875 at M 2, gamma 1.4 (and the inverse); nu(2) = 26.38 deg | NACA 1135 | 1e-6 |
| gamma 5/3, eps 3: Me 3, pe/pc 1/32, CF_vac 1.5, c* | exact | 1e-6 |
| NACA 0012 lift slope 2 pi /rad (got 1.0096 x 2 pi), alpha_L0 = 0 | thin-airfoil theory, Abbott & von Doenhoff | 5 % |
| Elliptic wing AR 8: e = 1 (got 1.008); CL_alpha by Helmbold (got -2.2 %) | Prandtl, Helmbold | 2 % / 3 % |
| CH4/air flame T 2226 K (nasa and gri30) | Turns, Table B.1 | 1 % |
| H2/O2 flame T 3080 K (got 3077) | Glassman, Table 1.1 | 1 % |
| Argon through the Cantera expansion: c*, CF_vac 1.5, Isp_vac | exact | 0.2 % |
| RocketPy, vertical flight, no drag, constant thrust: apogee, v at burnout, a max | closed form | 0.5 % |
| Water at 25 degC: 997.05 kg/m3, 890.0 uPa s; p_sat at 100 degC 1.01418 bar | IAPWS-95/2008 | 1e-4 |

### Control surfaces and servos
`control_surface` sizes a servo: worst case = top speed, full deflection and the largest local angle
of attack adding up. Thin-airfoil hinge moments are an upper bound (the boundary layer unloads the
trailing edge): keep `viscous_factor` 1 unless a sourced test says otherwise, and ask a servo
margin of 2. A horn / servo-arm ratio r divides the torque by r and multiplies the servo travel by r.
Validated on Glauert's closed form (tau at c_f/c 0.25 = 1/3 + sqrt(3)/(2 pi)) and on the full-chord
flap, which is the plate's leading-edge moment (-pi/2 per rad).
