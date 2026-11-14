# skill-sim

**Load when:** any simulation or virtual test beyond `fem_static`: pressure, gravity, rotation,
vibration modes, buckling, heat, dilatation, thermal stress, fatigue, any steady field (heat,
electrostatics, DC current, Darcy), and the design loop "model → simulate → build → test →
correct" (`study`). Domain packs have their own skills (skill-marine, skill-elec, skill-aero-sim,
skill-dynamics...).

## The four tools (`freecad-layers`)
- `tools_status()` — installed environments and tools, every simulation `kind` and its state:
  `validated` (its reference cases passed for this code and lock), `stale` (rerun them on first
  use, automatically), `fail` (unusable), `env missing` (install command given).
- `simulate(kind, target, params, study)` — runs outside FreeCAD in the tool's own environment.
  `target` = the FreeCAD solid (checked, then exported to STEP) for geometry kinds.
  Answer: `OK|FAIL|UNKNOWN summary`, `values: ...`, `files: ...` (VTU for ParaView), and the
  reference line. Same inputs → `cached`. Longer than ~110 s → `RUNNING job <id>`: poll `job(id)`.
- `job(id, action="status"|"cancel")`.
- `study(action, name, args)` — the loop below.

**Files:** `params.files = ["robot/arm.urdf", "logs/run1.csv"]` (paths relative to the
project folder) are copied into the study folder for the adapter, and their bytes are part of the
cache key (a changed log is recomputed). Paths outside the project are refused.

**Verdict rule:** criteria live in `params.limits = {value: {"min"|"max": x}}` or in the study's
`criteria` for that kind. No criteria → `UNKNOWN` (computed, nothing judged). Never read UNKNOWN as OK.

## Kinds of the common core
| kind | params (besides `limits`) | main values |
|---|---|---|
| `fem_static` | `fixed` [faces], `loads` [{type force, faces, N:[x,y,z]} \| {type pressure, faces, MPa}], `gravity` [m/s²], `rotation` {rpm, axis, point}, `delta_T` + `initial_T` (dilatation), `material`, `mesh_size` mm | `max_disp_mm`, `max_abs_u{x,y,z}_mm`, `max_vm_MPa`, `max_vm_away_MPa`, `safety_factor`, `mass_kg` |
| `fem_modal` | `fixed`, `material` (needs rho), `n_modes` | `f1_Hz`, `freqs_Hz` |
| `fem_buckling` | `fixed`, `loads` (the reference load), `n_modes`, `knockdown` | `buckling_factor` (× loads), `buckling_factor_knocked_down` |
| `fem_thermal` | `temperatures` [{faces, C}], `heat_flux` [{faces, W_m2}], `convection` [{faces, h_W_m2K, T_inf_C}], `report_faces` | `T_max_C`, `T_min_C`, `T_<face>_C` |
| `fem_thermomech` | thermal inputs + `fixed`, `initial_T` (stress-free temperature) | displacements, stresses, temperatures |
| `pde_diffusion` | `k`, `c`, `source`, `dirichlet` {face: u}, `flux` {face: q}, `robin` {face: [h, u_inf]}, `probes` [[x,y,z]], `report_flux` [faces], `scale` | `u_min`, `u_max`, `u_probe<i>`, `flux_out_<face>` |
| `fatigue` | `signal` or `csv`, `scale`, `sn` {basquin {Sf_MPa, b}} \| {power {C, m}}, `endurance_MPa`, `mean_correction` goodman + `Su_MPa`, `repeats` | `cycles`, `damage`, `life_repeats`, `eq_range_at_1e+06` |

Faces: `"FaceN"` (FreeCAD numbering) or a side `xmin|xmax|ymin|ymax|zmin|zmax`. Materials:
steel, stainless, aluminium, titanium, copper, pla, petg, abs, pom, or a dict
`{E MPa, nu, rho kg/m³, alpha 1/K, k W/(m·K), cp J/(kg·K), yield MPa}` (`base` = a name to
start from). Units: mm, N, MPa, °C, W.

## Reading results like an engineer
- The peak stress at a clamp is a mesh singularity: judge on `max_vm_away_MPa` / `safety_factor`.
- Buckling is linear (eigenvalue). Thin shells (pressure hulls, tanks) need a knock-down factor
  from a cited source (NASA SP-8007): pass `knockdown`, and write the source as a hypothesis.
- Converge: repeat with `mesh_size` halved; a value that moves more than a few % is not settled.
- Linear elastic, small displacements only (no plasticity, no contact yet).

## The loop: until the real object matches the model
```
study start {goal, criteria {kind: limits}, hypotheses [{text, source}], acceptance {quantity: limits}, agreement 0.1}
simulate(..., study=name) × N      → FAIL: fix the design, study iterate {change, params}
                                   → all OK: state ready_to_build → export / drawing_sheet
(the human builds and tests it)    → study built
study measure {data {quantity: value}, source, predicted {quantity: "kind.value"}}
   → VALIDATED: measured inside acceptance AND model within `agreement` of the test
   → CALIBRATE: study calibrate {factors {param: value}, note: how fitted} → next iteration
study report                       → report.md (hypotheses, iterations, tests, gaps) + journal.jsonl
```
- Every hypothesis carries its source; `status` lists the unsourced ones: get them sourced or
  tested before building.
- Calibration changes the MODEL (effective modulus, friction, drag coefficient...), the design
  changes the OBJECT: say which one you are doing in `change` / `note`.
- "Validated" means the data agree within the stated hypotheses — never "safe" or "certified".

## Never
- Command a real machine, vehicle, robot or printer; flash a real board; write machine G-code.
- Invent a material property, a datasheet limit or a test value: unsourced → UNKNOWN.
