# skill-dynamics

**Load when:** robot/vehicle dynamics: "will the motor be strong enough", joint torques, a model
moving under gravity/torques/PD, controller tuning (step, margins, LQR), standing balance / ZMP,
a wheel on sand or soil (sinkage, drawbar pull, max slope).

## Use the tool
`freecad-layers` -> **`simulate(kind=..., params={...})`**, adapter `dynamics` (env `robot`:
MuJoCo 4, Pinocchio, python-control). No `target`: these kinds read a model file or numbers.
Units SI: m, kg, s, N, N m, rad (keys ending `_deg` are degrees), W, Pa.

**Model** (multibody, inverse_dynamics, balance), one of:
- `model_xml`: the MJCF or URDF text inline (+ `model_format` `mjcf|urdf`). Preferred: it is
  part of the cache key, so an edited model is re-run.
- `model`: a path relative to the study run folder. Every mesh/include it references must resolve
  inside that folder, else refused. URDF `package://` paths are read as the mesh's base name.
- `builtin:pendulum | builtin:free_fall | builtin:two_link` (+ `model_params`), for checks.
- URDF from FreeCAD: see `skill-robotics-ros` (inertials via `urdf_aug`; mm -> m).

| kind | params | key values |
|---|---|---|
| `multibody` | `duration`, `timestep`, `integrator` (euler/rk4/implicit/implicitfast), `q0`/`qd0` {joint: v}, `qpos0` [nq], `torques` {joint: N m}, `pd` {targets, kp, kd, max_torque}, `ctrl` {actuator: v}, `track_joint`, `track_body`, `video` (+ `video_format` gif/mp4, `fps`, `width`, `height`) | `max_joint_torque_Nm`, `peak_torque_Nm`{}, `max_joint_speed`, `final_q`{}, `contact_normal_max_N`, `energy_drift_rel`, `period_s`, `body_z_m` |
| `inverse_dynamics` | URDF, `g`, `trajectory`: `{q: [..]}` static, `{q: [[..]..], dt}` sampled (finite differences, or give `qd`, `qdd`), `{type: sine, amplitude, freq_hz, phase, offset, duration, dt}` | `peak_tau_<j>_Nm`, `rms_tau_<j>_Nm`, `peak_power_<j>_W`, `peak_total_power_W`, `max_abs_diff_Nm` (Pinocchio vs MuJoCo) |
| `control` | `tf` {num, den} or `ss` {A, B, C, D}, `dt` (discrete), `pid` {kp, ki, kd, N}, `loop` open/closed, `lqr` {Q, R} | `stable`, `rise_time_s`, `overshoot_pct`, `settling_time_s` (2 %), `gain_margin(_dB)`, `phase_margin_deg`, `lqr_K_i_j`, `lqr_cl_max_pole_real` |
| `balance` | `com` [x,y,z] or a model + `q`/`qpos`, `com_acc`, `com_vel`, `contacts` [[x,y]..] (else the model's contacts), `ground_z`, `g` | `zmp_x_m`, `zmp_y_m`, `zmp_margin_m` (> 0 inside), `com_margin_m`, `capture_margin_m`, `support_area_m2` |
| `terramech` | `soil` (name or {n, kc N/m^(n+1), kphi N/m^(n+2), c Pa, phi_deg, source}), `load_N` per wheel, `wheel_diameter_m`, `wheel_width_m`, `slip`, `K_m` (shear modulus, with its source), `n_wheels` | `sinkage_m`, `motion_resistance_N`, `thrust_N`, `drawbar_pull_N`, `max_slope_deg`, `sinkage_quad_m` |

Built-in soils (Wong, *Theory of Ground Vehicles*, Table 2.3, transcribed: check them against
your edition): `dry_sand_lll`, `sandy_loam_lll_15pct`, `clayey_soil_thailand_38pct`,
`heavy_clay_wes_25pct`, `lete_sand`. The table gives no `K`: ask the user for it and its source.

## Verdict
Pass the criteria in `params.limits`, e.g. `{"max_joint_torque_Nm": {"max": 12}}`,
`{"zmp_margin_m": {"min": 0.02}}`, `{"phase_margin_deg": {"min": 45}}`,
`{"max_slope_deg": {"min": 15}}`. With no criteria the verdict is UNKNOWN (computed, not judged).

## Read the results honestly
- Torques are at the joint: rigid links, no gearbox, friction or motor losses. Size the actuator
  with continuous torque ≥ RMS and peak torque ≥ peak, plus a margin taken from the datasheet.
- `energy_drift_rel` is a conservation check only for a passive, frictionless model with no
  contacts. When torques, damping or contacts act, the tool says so.
- MuJoCo contacts are soft: forces depend on `solref`/`solimp` and the timestep. Halve the
  timestep and compare before trusting a contact force.
- A diverged simulation (NaN, bad acceleration) is reported as UNKNOWN. Reduce the timestep, or
  check the masses and inertias.
- In `inverse_dynamics`, Pinocchio needs a fixed-base URDF with 1-dof joints. An MJCF uses MuJoCo
  alone. Damping, armature and limits are zeroed for the cross-check.
- In `balance` from a model, bodies welded to the world are not in the CoM. Use a free-floating
  model for a legged robot. The cart-table ZMP assumes flat ground and rigid feet, and neglects
  the angular momentum about the CoM.
- `terramech` uses the classical Bekker/Janosi model: rigid wheel, single pass, no bulldozing, no
  lugs. Outside sinkage < 0.3 D it warns. The gap between `sinkage_m` and `sinkage_quad_m` is
  the error of Bekker's series step. Soil without a source: treat every number as UNKNOWN.
- Video is offscreen. If headless GL fails, the video is skipped with a note and the numbers still
  stand.
- Simulation only: never connect to a real robot (roadmap §10.2).

## Exactness (reference cases, run before the first use)
- Pendulum period, small amplitude: 2π√(L/g).
- Pendulum period at 2 rad: 4√(L/g)·K(sin²θ0/2).
- Free fall: position and speed.
- Static RNEA torques of a 2-link arm: hand-computed gravity torques.
- Pinocchio vs MuJoCo on a moving arm: agree to 1e-6 N m.
- 2nd-order step: overshoot and peak time.
- Double-integrator LQR gain.
- Gain and phase margins of 2/(s+1)³.
- ZMP: static case and cart-table case.
- CoM of the 2-link arm.
- Bekker sinkage at n = 1 and n = 0.5: exact integrals.
