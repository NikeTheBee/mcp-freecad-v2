# skill-space

**Load when:** a small satellite or CubeSat: orbit (period, J2 drift, sun-synchronous, eclipse,
ground-track repeat), orbit propagation and lifetime (drag decay), manoeuvre delta-v and propellant,
steady thermal balance (hot/cold temperatures).

## Use the tool, not hand-written scripts
Workshop `simulate(kind=..., params=...)`, adapter `server/adapters/space.py` (pixi env `space`).
No geometry: every input is a number. Offline (no space-weather or Orekit data download). Pass
`limits` (`{"T_hot_C": {"max": 60}}`) or the verdict is UNKNOWN ("computed, nothing judged").

| kind | what | key params (units in the name) | main values |
|---|---|---|---|
| `orbit` | closed-form orbit + optional propagation | `alt_km` (circular) or `a_km` + `e` or `perigee_alt_km` + `apogee_alt_km`; `i_deg` or `sun_synchronous: true`; `raan_deg`, `argp_deg`, `ta_deg`; `beta_deg` (0 = worst eclipse), `sun_phase_deg`; `repeat_max_days` (30), `repeat_tol_km` (10), `repeat: {revs, days}` | `period_s`, `v_perigee_km_s`, `v_apogee_km_s`, `raan_dot_deg_day`, `argp_dot_deg_day`, `sso_inclination_deg`, `eclipse_fraction`, `eclipse_max_min`, `revs_per_day`, `repeat_revs`/`repeat_days`/`repeat_shift_km`, `repeat_solved_alt_km` |
| `orbit` + `propagate` | GMAT (default) or Orekit numerical run | `duration_s` / `duration_days` / `duration_periods`; `gravity` point_mass / j2; `drag` JacchiaRoberts / MSISE90 / NRLMSISE00 / Exponential (GMAT only) with `epoch_utc` ('01 Jan 2026 00:00:00.000'), `cd` (2.2), `area_m2`, `mass_kg`, `f107` (150), `f107a`, `kp` (3); `engine` gmat / orekit / both; `reentry_alt_km` (120) | `final_state_km_km_s`, `final_alt_km`, `final_sma_km`, `mean_alt_start_km`/`_end_km`, `alt_decay_km`, `sma_decay_km_day`, `raan_drift_num_deg_day`, `reentered`, `lifetime_days`, `start_end_distance_m`, `gmat_orekit_diff_m` |
| `delta_v` | impulsive transfers, rocket equation | `hohmann: {from_alt_km | r1_km, to_alt_km | r2_km, plane_change_deg}`, `bi_elliptic: {..., via_alt_km | rb_km}`, `plane_change: {deg, alt_km | v_km_s}`, `extra_dv_m_s: {name: m/s}`, `dv_m_s` (overrides the sum), `margin` (0.1 = 10 %), `propellant: {isp_s, dry_kg}` or `{isp_s, wet_kg, propellant_kg}` | `hohmann_dv_total_km_s`, `hohmann_dv_total_combined_km_s`, `hohmann_tof_h`, `bielliptic_dv_total_km_s`, `plane_change_dv_km_s`, `dv_budget_m_s`, `propellant_kg`, `wet_kg`, `dv_available_m_s` |
| `sat_thermal` | steady radiative balance | `shape` sphere (`diameter_m`) or box (`box_m` [x, y, z] or `cubesat` 1U..12U, -z nadir); `alpha`, `eps`, `faces: {"+x": {alpha, eps}}`; `P_hot_W`, `P_cold_W` (or `internal_W`); `alt_km` (none = deep space); `sun_dir` (body frame); `hot`/`cold` `{solar_W_m2, albedo, earth_ir_W_m2}`; `cold_case` eclipse / sunlit; `beta_deg`, `beta_hot_deg` or `eclipse_fraction`; `multi_node`, `coupling_W_K` (0.5 per face) | `T_hot_C`, `T_cold_C`, `T_avg_hot_C`, `T_avg_cold_C`, `Q_in_hot_W`, multi-node `T_<face>_<case>_C`, `T_internal_hot_C`, `T_face_max_hot_C`, `T_face_min_cold_C` |

Default environments: hot 1414 W/m2, albedo 0.35, Earth IR 258 W/m2; cold 1322 W/m2, albedo 0.25,
IR 216 W/m2 (typical design values, Gilmore 2002 ch. 2). Use the mission's values when known.
Budget sum: Hohmann (combined plane change if given) or else bi-elliptic, plus `plane_change`,
plus `extra_dv_m_s`, times (1 + `margin`).

## Read the results honestly
- J2 rates are first order and take the input elements as mean elements. A GMAT run of the same
  orbit drifts about 0.4 % differently (osculating vs mean elements): normal, not a bug.
- `gmat_orekit_diff_m` (engine both) grows by tens of metres per day with J2: GMAT applies J2 in
  the Earth frame of date, Orekit (no data files) in EME2000. Trust it for point mass only.
- Drag decay depends on the solar flux by a factor 2-10: constant `f107`/`kp` gives one scenario,
  not a forecast. Give a range (e.g. 70 and 200) when the user asks for a lifetime. Always set
  `epoch_utc` with drag (diurnal bulge, Sun position).
- Below about 100 km the atmosphere models stop. The run then ends as `reentered`.
- The shadow is a cylinder (no penumbra). The eclipse of an eccentric orbit depends on
  `sun_phase_deg`; 0 puts the shadow at apogee, which is the longest eclipse.
- Thermal is steady state with no thermal mass. The sunlit and eclipse temperatures bound the real
  swing; the orbit average is the limit for a heavy, well-coupled satellite. Albedo is taken at the
  sub-solar point, so the hot case is conservative. Multi-node faces radiate only to space (no
  face-to-face exchange). Batteries need a transient analysis (Elmer or an RC network) before
  sign-off; say so.
- Ground-track repeat uses nodal days and first-order J2; `repeat_shift_km` is the equator miss per
  cycle.

## Out of scope (refused or not modelled)
Attitude dynamics and control (Basilisk, not installed), radiation dose (SpacePy/IRBEM), SRP and
third bodies in the propagation, finite burns, launch-vehicle ascent. Orekit runs without its data
files: no UTC, no Earth frames, no drag.

## Exactness (reference cases, run before the first use)
| Case | Source | Tolerance |
|---|---|---|
| LEO period 400 km, 2 pi sqrt(a^3/mu) | Curtis ch. 2 | 1e-12 |
| J2 RAAN drift 400 km 51.6 deg: -5.0023 deg/day | Vallado eq. 9-41 | 1e-12 |
| Sun-synchronous inclination 700 km: 98.188 deg | Wertz SMAD (98.19) | 5e-4 |
| Eclipse fraction 400 km beta 0 / 700 km beta 50 (numerical vs closed form) | cylindrical shadow | 1e-6 |
| Hohmann 300 km to GEO: 3.8926 km/s | Curtis ch. 6 | 1e-12 |
| Hohmann 191.34 km to GEO: 3.935224 km/s, 5.256713 h | Vallado Example 6-1 | 1e-6 |
| Bi-elliptic via 503873 km: 3.904057 km/s, 593.92 h | Vallado Example 6-2 | 1e-6 / 1e-5 |
| Plane change 2 v sin(di/2); rocket equation | closed form | 1e-12 |
| GMAT two-body, one period: back to start (got 0.02 m) | closed orbit | 1 m |
| Orekit two-body, one period (got 3e-7 m) | closed orbit | 1 m |
| Sphere in sunlight T = (alpha S / (4 eps sigma))^(1/4) | Gilmore ch. 2 | 1e-12 |
| Box, multi-node with tight coupling, sun on one face | closed form | 1e-6 |
