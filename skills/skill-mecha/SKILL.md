# skill-mecha

**Load when:** IMU or gyro noise (Allan variance, datasheet ARW / bias instability), ADC
resolution or ENOB, sensor fusion / Kalman filter tuning, DC motor or gearmotor sizing (speed,
torque, current, efficiency, heating, step response).

Every kind below is called as `simulate(kind, params=...)`: adapter `mecha`, env `sim`.

| kind | params | main values |
|---|---|---|
| `allan` | simulated: `N`, `B`, `K`, `rate_hz`, `duration_s`, `seed`; or recorded: `data_file` (CSV in the study folder), `column`, `rate_hz` | `N_est`, `B_est`, `tau_at_min_s`, `K_est`, allan.csv |
| `adc` | `bits`, `vref_V`, `rate_hz`, `f_in_hz`, `amplitude_V`, `noise_rms_V`, `jitter_rms_s`, `points` | `SINAD_dB`, `SNR_dB`, `ENOB`, `ideal_SNR_dB`, `clipped` |
| `kalman` | `dt_s`, `accel_noise` (sigma of acceleration), `meas_noise` (sigma of position), `steps` | `alpha`, `beta`, `pos_sigma`, `vel_sigma`, Monte-Carlo `*_mc`, `mc_vs_theory_rel` |
| `edf` | `fan_diameter_m`, `hub_diameter_m`, `exit_area_ratio` (0.9), `fan_efficiency` (0.55 printed to 0.65 good), `shaft_power_W` or `static_thrust_N`, `intake` {area_m2, duct_area_m2, duct_length_m, k_lip, k_bends, friction_factor}, `mass_kg`, `cd0_area_m2`, `speeds_m_s` | `static_thrust_N` / `_kgf`, `exit_velocity_m_s`, `mass_flow_kg_s`, `thrust_vs_speed_N`, `thrust_to_weight`, `top_speed_kmh`, `intake_K_total`, `thrust_loss_vs_ideal_inlet`, `duct_velocity_m_s` |
| `dc_motor` | `voltage_V`, `R_ohm`, `L_H`, `Kt_Nm_per_A` or `Kv_rpm_per_V`, `J_kgm2`, `b_Nms`, `friction_Nm`, `gear_ratio`, `gear_eff`, `load_torque_Nm` (output side), `max_current_A`, `Rth_K_per_W`, `ambient_C`, `max_winding_C` | `speed_rpm`, `output_speed_rpm`, `current_A`, `efficiency`, `stall_torque_Nm`, `tau_mech_s`, `winding_temp_C`, `step_t63_s`, motor_step.csv |

**Chain:**
1. `allan` on the real sensor gives its noise.
2. That noise goes into `kalman`.
3. `dc_motor` sizes the actuator.
4. `dynamics control` closes the loop.
5. `ai` learns a policy when no model is good enough.

**Limits:**
- The motor model is linear: no saturation, no cogging.
- The ADC is ideal: no INL or DNL.
- An Allan record shorter than about 10 tau under-reads the bias instability B.

Nothing here touches real hardware.

**Ducted fan (`edf`):** always give the `intake`. Without it the thrust assumes a perfect inlet; a narrow duct (a capture area below about 110 % of the fan area) costs 10 to 15 % of thrust.

**Validated on:**
- white-noise Allan deviation N/sqrt(tau) (IEEE 952);
- an ideal 12-bit quantiser at 74.0 dB;
- Kalata's alpha-beta filter at lambda = 1 (0.75 / 0.5);
- the DC motor's closed forms and its geared operating point;
- the ducted fan's static thrust T = (rho A_e)^(1/3) (2 eta P)^(2/3), its power-thrust inverse, and the energy balance with intake losses.
