# skill-pogo

**Load when:** POGO, longitudinal coupling of a liquid rocket, structure-propulsion instability,
feedline dynamics, propellant line resonance, pump cavitation compliance, accumulator sizing,
"the launcher oscillates along its axis at a few Hz".

## What POGO is
A closed loop: the vehicle vibrates longitudinally → the acceleration shakes the propellant
column → the pressure oscillation reaches the pump → the pump modulates the chamber pressure →
the thrust oscillation shakes the vehicle again. Below a certain margin it diverges. Titan II,
Saturn V and Ariane all needed an accumulator. NASA's own tool (ML_POGO) is US-release-only and
there is no free equivalent, so this adapter implements the published method.

## Use the tool
`freecad-layers` → **`simulate(kind, target, params)`**, adapter `pogo` (env `sim`). No geometry.
No `limits` → UNKNOWN. The usual criterion: `limits = {"gain_margin": {"min": 2.0},
"min_damping_ratio": {"min": 0.0}}`.

| kind | params | main values |
|---|---|---|
| `feedline` | `density_kg_m3`, `sound_speed_m_s`, `length_m`, `diameter_m` or `area_m2`, `resistance_pa_s_m3_per_m`, `accumulator_compliance_m3_pa`, `both_ends_open`, `frequency_hz` | `inertance_pa_s2_m3`, `compliance_m3_pa`, `organ_pipe_hz`, `helmholtz_hz`, `wave_travel_time_s`, `characteristic_impedance_pa_s_m3` |
| `pogo_stability` | `modes` [{`frequency_hz`, `damping_ratio`, `modal_mass_kg`, `phi_engine`, `phi_tank`}], `feed` {`model` `line`\|`pure_gain`, line geometry, `column_height_m`, `accumulator_compliance_m3_pa`}, `pump` {`gain`, `delay_s`, `cavitation_compliance_m3_pa`}, `engine` {`thrust_n`, `chamber_pressure_pa`, `chamber_time_constant_s`} | `closed_loop_frequencies_hz`, `closed_loop_damping_ratios`, `damping_change_vs_open_loop`, `min_damping_ratio`, `stable`, `gain_margin` (+`_db`), `nyquist_gain_margin`, `phase_margin_deg`; file `pogo_modes.csv` |
| `accumulator` | the same, plus `target_gain_margin`, `max_compliance_m3_pa` | `required_compliance_m3_pa`, `gain_margin_without_accumulator`, `helmholtz_hz_with_accumulator` |

## Where the inputs come from
- `modes`: the **longitudinal** modes of the stage. `fem_modal` (CalculiX) on the vehicle gives
  frequency, modal mass and the mode-shape value at the engine gimbal block (`phi_engine`) and at
  the tank bottom (`phi_tank`), normalised the same way. `damping_ratio` is assumed: 0.005 is the
  usual launcher value, and the answer is sensitive to it — sweep it.
- `pump.gain`: the ratio of discharge to suction pressure perturbation; 1.5 to 3 for a pump
  operating with some cavitation. `cavitation_compliance_m3_pa` is the bubble volume's softness
  at the inducer: it is the term that moves the feed resonance down into the structural band.
- `column_height_m`: the liquid height above the line inlet at this flight point.

## Reading the answer
- **`gain_margin` is the POGO number**: the factor by which the loop gain can be multiplied
  before the least damped mode reaches zero damping. Below about 2 (6 dB) is where programmes fit
  an accumulator. `nyquist_gain_margin` is the classical loop margin and is a different, usually
  much larger number for a lightly damped mode — do not quote it as the POGO margin.
- `damping_change_vs_open_loop` is the honest picture: the loop eats damping long before it goes
  unstable. A mode going from 0.50 % to 0.05 % is already a programme problem.
- If the result says the solver **lost the pole** beyond a gain, read the reported value as a
  LOWER BOUND on the margin, not as the margin.
- An accumulator is sized in **compliance** (m³/Pa). A gas-filled bellows of volume V at pressure
  p gives roughly V/(gamma p). Check its own resonance and its failure mode (gas ingestion).

## Validated on
Degenerate cases with exact closed forms: pure stiffness feedback (closed-loop frequency and
damping), pure negative-damping feedback (damping ratio and a gain margin of exactly c/B), and
line hydraulics against the quarter-wave, half-wave, inertance, compliance and Helmholtz closed
forms. See `REFERENCES` in `server/adapters/pogo.py`.

## Limits to state every time
Linear, small perturbations, **one frozen flight point** — propellant level, thrust and pump speed
all change along the trajectory, so run several points. One line and one engine per run. The pump
is a gain with a delay, not a blade-row model. Combustion is a first-order lag: chug, buzz and
screech are a different family and are NOT covered. Longitudinal only.
