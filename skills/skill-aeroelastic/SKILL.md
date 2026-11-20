# skill-aeroelastic

**Load when:** flutter, flottement, divergence, aeroelasticity, "will the wing/fin shake itself
apart", gust loads, aileron reversal, control-surface effectiveness, whirl flutter, fin flutter of
a rocket, V-g / V-f diagram, Nastran SOL 145 / ZAERO work.

## Use the tool
`freecad-layers` → **`simulate(kind, target, params)`**, adapter `aeroelastic` (env `sim`).
No geometry: every input is a number. `target` is unused except to tie the run to a project.
No `limits` → UNKNOWN ("computed, nothing judged"). Put the criterion in, e.g.
`limits = {"flutter_speed_m_s": {"min": 1.15 * v_dive}}`.

Unsteady aerodynamics come from **PanelAero** (DLR, BSD-3: Doublet Lattice, AICs validated against
MSC.Nastran) or from **Theodorsen strip theory**. The structure is a uniform beam or your own
modal basis — which is how `fem_modal` (CalculiX) results get in.

## Kinds
| kind | params | main values |
|---|---|---|
| `flutter_wing` | `model` `uniform_beam`\|`modal`; `aero` `theodorsen`\|`dlm`; `span_m`, `chord_m`, `a_ea`, `EI_N_m2`, `GJ_N_m2`, `mass_per_length_kg_m`, `S_alpha_kg_m_per_m`, `I_alpha_kg_m2_per_m`, `n_bending_modes`, `n_torsion_modes`, `v_min_m_s`, `v_max_m_s`, `n_speeds`, `rho_kg_m3`, `mach` | `flutter_speed_m_s`, `flutter_frequency_hz`, `flutter_mode`, `flutter_dynamic_pressure_pa`, `divergence_speed_m_s`, `natural_frequencies_hz`, `flutter_found`; file `vg.csv` (V-g / V-f) |
| `divergence` | `span_m`, `chord_m`, `GJ_N_m2`, `e_m`, `cl_alpha_per_rad`, `rho_kg_m3` | `divergence_speed_m_s`, `divergence_dynamic_pressure_pa`, `galerkin_vs_closed_form_rel` |
| `fin_flutter` | `shear_modulus_pa`, `root_chord_m`, `tip_chord_m`, `semi_span_m`, `thickness_m`, `altitude_m` or `pressure_pa`, `design_speed_m_s` | `flutter_velocity_m_s`, `flutter_mach`, `flutter_margin`, `aspect_ratio`, `thickness_ratio` |
| `gust_load` | `wing_loading_n_m2`, `mean_chord_m`, `speed_m_s`, `gust_velocity_m_s`, `cl_alpha_per_rad`, `rho_kg_m3` | `delta_load_factor`, `load_factor_up`/`_down`, `gust_alleviation_factor_Kg`, `mass_ratio_mu_g` |
| `aic_dlm` | `span_m`, `chord_m`, `n_span_panels`, `n_chord_panels`, `mach`, `k`, `xz_symmetry` | `cl_alpha_per_rad`, `helmholtz`… `dlm_k0_vs_vlm_max_rel`, unsteady lift and phase |

## Conventions that bite
- `a_ea` is the elastic axis **in semichords from mid-chord, positive aft**: an axis at 33 % of
  the chord is `a_ea = -0.34`, at mid-chord `0.0`. Getting this wrong moves the flutter speed by
  tens of per cent — it is the single most common error.
- `S_alpha` is the static unbalance per unit span, **positive when the centre of gravity is aft
  of the elastic axis**. Mass ahead of the axis (a leading-edge balance mass) is the classic fix.
- `k` in `aic_dlm` is the CLASSICAL reduced frequency omega/V (1/m), not Nastran's omega c/(2V).
- `g` in the V-g table is the **required structural damping** of the p-k method: negative while
  the mode is stable, zero at the boundary. It is not a damping ratio.

## Reading the answer
- Flutter speed is a **boundary, not a target**: certification practice is 1.15 x dive speed with
  zero structural damping assumed. Report the margin, not just the speed.
- Strip theory (`theodorsen`) ignores tip and sweep effects and is usually **conservative** on a
  straight wing; the 3-D `dlm` option gave +16 % on Goland's wing. Run both and keep the lower.
- No crossing in the sweep means the boundary is **above `v_max_m_s`**: the answer carries
  `flutter_speed_at_least_m_s`, and `flutter_speed_m_s` stays empty (UNKNOWN, never OK).
  Either raise `v_max_m_s` to find the real boundary, or put the criterion on the bound.
- `divergence_speed_m_s` falls out of the same system; if it is below the flutter speed, the wing
  diverges first and the flutter number is academic.
- Fin flutter is an **empirical screening formula** (NACA TN 4197, solid uniform fins): keep a
  margin of 1.5 on speed, and note it scales with sqrt(G/P), so the critical point is low and
  fast, not at apogee. A hollow, built-up or composite fin needs its own stiffness analysis.

## Validated on
Goland's cantilever wing (published 137.2 m/s, 70.7 rad/s; this solver gives 136.8 and
70.0 — 0.3 % and 1 %), through both the beam and the modal input path; the closed-form divergence
of a uniform wing; DLM(k=0) identical to VLM; the Helmbold lift slope; and the algebraic identity
of the fin-flutter and Pratt-Walker gust formulas. See `REFERENCES` in `server/adapters/aeroelastic.py`.

## Limits to state every time
Linear, small perturbations, no separation, no freeplay, no shock motion. The DLM is subsonic:
**not valid transonically**, which is exactly where the flutter boundary dips (the transonic dip).
Modal damping is an assumption. Composite bend-twist coupling is not modelled.
