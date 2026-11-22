# skill-elec

**Load when:** circuit simulation (SPICE), battery discharge, coil or magnet field/force, wire
gauge / voltage drop / fuse, power budget / autonomy of a battery-powered device.

## Use the workshop tool, not hand-written code
`simulate(kind=..., params=...)` runs `server/adapters/elec.py` in the pixi env `elec`
(ngspice 41, PyBaMM, Magpylib). Each result has a verdict (OK / FAIL / UNKNOWN), a short summary
and `values`. Put the user's criteria in `params.limits` = `{"value_key": {"max": x} | {"min": x}}`.
No criteria -> UNKNOWN ("computed, nothing judged"). Never treat UNKNOWN as OK.

| kind | what | main params |
|---|---|---|
| `spice` | ngspice batch run, sandboxed | `netlist` (text, no title line), `analysis` (".tran 1u 5m" or a list: one run each), `meas` (".meas ..." lines), `measure` {"v(out)": "max"}, `print` [let-vector names] |
| `battery` | PyBaMM constant-current discharge | `model` SPM/SPMe/DFN (default SPMe) or `compare` [..], `parameter_set` (Chen2020...), `c_rate` or `current_A`, `cutoff_V` (default: the set's), `thermal` lumped/isothermal, `ambient_C`, `initial_soc` (1) |
| `magnet` | Magpylib B field, force, torque | `sources` [{type loop/solenoid/cuboid/cylinder/sphere, name, position_m, axis or rotvec_deg, current_A, turns, length_m, radius_m/diameter_m, dimension_m, height_m, polarization_T or Br_T}], `points_m`, `force_on` [names], `meshing` |
| `wire_sizing` | resistance, drop, loss, section, fuse | `length_m` (one way), `round_trip` (true), `area_mm2` or `awg`, `current_A`, `voltage_V`, `system` dc/ac, `temperature_C`, `material` copper/aluminium, `conductor` ideal/iec60228_class2/class5, `max_drop_pct`, `fuse_A`, `ampacity_A`+`ampacity_source` or `installation` {method A1/B1/C, ambient_C, grouping_factor} |
| `power_budget` | battery current, C-rate, autonomy | `battery` {capacity_Ah, voltage_V, voltage_min_V, max_continuous_C, max_peak_C, usable_fraction}, `rails` [{name, voltage_V, efficiency, loads [{avg_A, peak_A, duty, rms_A}]}], `required_autonomy_h` |

Units: SI (SPICE suffixes accepted in SPICE), wire section in mm², temperatures in °C.

## Reading the values
- **spice:** `.op` gives every vector (`v(out)`, `i(v1)`: a source current is negative when it
  delivers). `.meas` results come back under their name. `measure` keys are `"<expr>:<op>"`, ops
  `max min pp avg rms` (optional window `avg@8m:10m`), `final initial argmax argmin at=X cross=L
  value`; exprs `v(a)`, `v(a,b)`, `i(src)`, `vdb()`, `vm()`, `db()`, `mag()`, `phase_deg()`,
  `real()`, `imag()`. A complex AC vector without a function is measured on its magnitude.
  With several analyses a key that occurs in more than one gets a prefix: `tran.v(out):max`.
- **Sandbox:** `.include`/`.lib`/`file=` only inside the study folder; `.control` lines limited
  to safe nutmeg commands (no shell, cd, source, codemodel, plot, `>` redirection, backquotes;
  `write`/`wrdata` only into the study folder); `.osdi` and unknown directives refused.
- **battery:** `capacity_Ah`, `energy_Wh`, `capacity_vs_nominal`, `final_T_C`/`max_T_C` (lumped);
  `compare` adds `capacity_Ah_<model>` and `capacity_rel_diff`. A cell model from published
  parameters, not a datasheet.
- **magnet:** `Bx_T_k`, `By_T_k`, `Bz_T_k`, `B_T_k` for point k (first 50), `B_T` all, `B_max_T`;
  `Fx_N_<name>`… `F_N_<name>`, `T_Nm_<name>`. Raise `meshing` and compare to judge force
  convergence. No soft iron, no demagnetisation. No default magnet grade: ask for Br.
- **wire_sizing:** `R_ohm`, `drop_V`, `drop_pct`, `loss_W`, `min_area_mm2`,
  `suggested_area_mm2` / `suggested_awg` (drop only, not current rating), `ampacity_A`,
  `derated_current_ratio` (1.25·I/Iz ≤ 1), `fuse_to_ampacity` (In/Iz ≤ 1), `current_to_fuse`.
  Resistivity: IEC 60028 (Cu 1/58 Ω·mm²/m, α 0.00393). Ampacity only from the caller's datasheet
  or IEC 60364-5-52 Table B.52.2 (Cu, PVC, 2 loaded, A1/B1/C, 1.5-35 mm²); otherwise UNKNOWN.
- **power_budget:** `battery_mean_A`, `battery_peak_A` (all peaks together, at `voltage_min_V`),
  `battery_rms_bound_A` (upper bound), `c_rate_*`, `autonomy_h`, `autonomy_margin`,
  `c_rate_cont_ratio` / `c_rate_peak_ratio` ≤ 1 vs the datasheet (absent -> UNKNOWN).

## Electrical safety rules (§10.2, applied as requirements)
- > 50 V AC or > 120 V DC: "qualified person required", verdict never OK; no mains-side design,
  certified supplies only. > 60 V DC: insulation, interlock, hardware e-stop.
- Every branch has a fuse rated **below** the wire's sourced rating, ≥ 25 % derating margin, voltage
  drop checked. Precharge above ~24 V, reverse-polarity protection.
- Lithium: BMS + fuse near the battery, limits only from the cell datasheet, UN38.3 for transport.
- Never say "safe" or "certified". A value with no source is UNKNOWN and goes to a human.
- Never drive real instruments or boards (pyvisa is for bench tests the human runs).

## Exactness
11 reference cases run before the kinds can be used: RC −3 dB at 1/(2πRC) and −45°, divider
`.op`, RC step at τ (63.2 %), ideal buck mean = D·Vin, PyBaMM SPM vs DFN at C/10 (0.02 % apart,
5.13 Ah vs 5.0 Ah nominal LG M50), solenoid centre μ0NI/√(L²+4R²), cylinder magnet on axis,
coaxial loops force (elliptic integrals), wire drop ρL/A at 20 and 70 °C, power budget arithmetic.
