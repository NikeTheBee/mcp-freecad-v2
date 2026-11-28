# skill-sitl

**Load when:** "will my drone hover / climb / land", hover throttle of a multirotor, a scripted test
flight, a fixed-wing take-off / circuit / landing, depth hold of an ROV, a rover driving to a point,
with the REAL ArduPilot autopilot code
(SITL, software in the loop). Aerodynamics and sizing stay in `skill-aero-sim` / `skill-drone`;
hull, buoyancy and thrusters in `skill-marine`.

## Use the tool, not hand-written MAVLink scripts
Workshop `simulate(kind=..., params=...)`, adapter `server/adapters/sitl.py` (pixi env `aero`,
pymavlink). ArduPilot 4.7.3 SITL Windows binaries (`install/tools.lock.json`), each run with its
built-in physics model. No geometry. Times are **simulated** seconds. A run takes 15-25 s of wall
time at `speedup` 5. Pass `limits` or the verdict is UNKNOWN ("computed, nothing judged"):
`{"hover_rms_error_m": {"max": 0.3}, "landed_disarmed": {"min": 1}}`.

| kind | test | key params | main values |
|---|---|---|---|
| `sitl_copter` | GUIDED: arm, take off, hover, waypoints, LAND | `model` (quad, x, hexa, hexax, octa, octa-quad, y6, dodeca-hexa), `alt_m` (10), `hover_s` (15), `settle_s` (3), `waypoints` [[north_m, east_m, alt_m]...] from the takeoff point, `frame` {...}, `thrust_to_weight`, `wind_mps`, `wind_dir_deg`, `params` {NAME: value}, `home` [lat, lon, alt_m, hdg], `speedup` (5) | `time_to_alt_s` (to 95 %), `alt_overshoot_m`, `hover_mean_error_m`, `hover_rms_error_m`, `hover_max_error_m`, `hover_throttle` (0..1), `hover_voltage_V`, `max_tilt_deg`, `battery_consumed_mAh`, `battery_consumed_Wh`, `waypoints_reached`, `waypoint_times_s`, `land_time_s`, `landed_disarmed` (1/0), `alerts`, `prearm_messages` |
| `sitl_sub` | ArduSub: arm (MANUAL), depth hold (ALT_HOLD) | `model` (vectored, vectored_6dof), `depth_m` (5), `hold_s` (20), `settle_s` (3), `params`, `speedup` | `time_to_depth_s` (to 95 %), `depth_overshoot_m`, `depth_mean_error_m` (+ = too deep), `depth_rms_error_m`, `depth_max_error_m`, `heading_mean/rms/max_error_deg`, `max_tilt_deg`, `battery_consumed_mAh` |
| `sitl_rover` | ArduRover: GUIDED drive to a point, stop | `model` (rover, rover-skid), `north_m` (50), `east_m` (0), `speed_mps` (5), `params`, `speedup` | `time_to_target_s`, `mean_speed_mps`, `max_cross_track_m`, `stop_error_m` |
| `sitl_plane` | ArduPlane AUTO mission: runway take-off along `home` heading, right-hand rectangle circuit, final approach on the axis, land on the start point | `alt_m` (40), `circuit_length_m` (400), `circuit_width_m` (180), `approach_m` (280), `laps` (1), `cruise_mps` (18), `min_mps`, `max_mps`, `aircraft` {...} or `aero` {...}, `model` (plane, plane-elevon, plane-vtail), `edf_exit_area_m2`, `edf_efficiency` (electrical), `battery_Ah`, `battery_Wh`, `wind_mps`, `params`, `speedup` | `ground_roll_m`, `liftoff_airspeed_mps`, `time_to_alt_s`, `takeoff_overshoot_m`, `waypoints_reached`, `cruise_airspeed_mps`, `cruise_throttle`, `cruise_thrust_fraction` (of THR_MAX), `cruise_thrust_N`, `cruise_power_W_est`, `cruise_endurance_min_est`, `cruise_alt_mean/rms/max_error_m` (from the second leg on), `max_bank_deg`, `max_pitch_deg`, `touchdown_along_m` (+ = past the point), `touchdown_lateral_m`, `touchdown_sink_mps`, `rollout_m`, `landed_disarmed`, `coeffs_loaded` |

Files in the study folder: `telemetry.csv` (t, alt, n, e, roll, pitch, yaw, throttle, voltage,
airspeed, vz, mission item), `mission.json` and `plane.json` (plane),
`statustext.log` (every autopilot message), `sitl.log`, `defaults.parm`, `frame.json`, the
autopilot's own log in `logs/*.BIN`.

### The plane's physics: what SITL really uses
SITL's fixed wing (`libraries/SITL/SIM_Plane`) is a **2 kg** airframe with a constant thrust of
`throttle x m g / 0.7` (thrust-to-weight 1.43 at full throttle, no loss with speed) and the
aerodynamic coefficients of a cambered Skywalker (`c_lift_0` 0.56, `c_lift_a` 6.9, `c_drag_p`
0.1, S 0.45 m²). Mass and thrust are not settable, so an aircraft goes in by **dynamic
similarity**: `aircraft` {`mass_kg`, `wing_area_m2`, `span_m`, `mac_m`, `CL_alpha`, `CL_0`,
`Cm_alpha`, `Cm_0`, `CD0`, `oswald`, `alpha_stall_deg`, `thrust_to_weight`} scales the area by
2 kg / mass (span and chord by its square root: same wing loading and aspect ratio) and sets
THR_MAX to the same thrust-to-weight. `aero` {...} passes raw SIM_Plane keys and wins.
- Give `CL_0` (0 for a symmetric profile): the default 0.56 is a cambered wing.
- Take `CL_alpha`, `Cm_alpha` (= -CL_alpha x static margin) from `vsp_aircraft`, `CD0` from its
  parasite drag; `thrust_to_weight` from the `edf` kind (static).
- Control derivatives (roll damping, aileron and elevator power) stay SITL's. The autopilot is
  not tuned for the aircraft: bank and altitude overshoots point to a TUNE, not to a crash.
- The take-off is a runway roll. A hand- or bungee-launched model lifts off at the same speed,
  but the ground roll does not apply to it.
- Constant thrust: an EDF loses thrust with speed, so climb and top speed here are optimistic.
  The cruise power is recomputed from momentum theory at the cruise speed (`edf_exit_area_m2`,
  `edf_efficiency` = fan x motor), without the intake losses.
- SITL's plane has no brakes: after touchdown it rolls on. The run waits until it is below
  5 m/s, then disarms it on the ground (as the pilot would).

### The copter's physics: what SITL really uses
`frame` is written as SITL's frame model file (ArduPilot `libraries/SITL/SIM_Frame.h`). Keys:
`mass` kg, `diagonal_size` m, `hoverThrOut` (throttle output at hover, 0..1), `refCurrent` A and
`refVoltage` V (power at hover), `maxVoltage` V (full pack, also sets the simulated battery),
`battCapacityAh` (0 = unlimited, then the voltage never drops), `propExpo` (= MOT_THST_EXPO),
`refRotRate` deg/s (yaw), `disc_area` m², `mdrag_coef`, `refSpd`/`refAngle` (drag test point),
`spin_min`, `spin_max`, `pwmMin`, `pwmMax`, `slew_max`, `refAlt` m.
- There is **no thrust curve input**: SITL scales the motors so that the vehicle hovers at
  `hoverThrOut`. Mass alone does not change the hover throttle. From a measured or QPROP
  thrust, pass `thrust_to_weight` (sets `hoverThrOut = 1 / (T/W)`).
- Motor thrust grows with the pack voltage: a fuller pack hovers at a lower throttle
  (0.36 at 12.6 V against 0.39 at 12.09 V on the default quad).
- The controller starts from the model's values (MOT_THST_HOVER, MOT_THST_EXPO, MOT_SPIN_*,
  MOT_PWM_*) and learns the hover throttle in flight.

## Read the results honestly
- Altitude and depth are the **autopilot's EKF estimate** (what its log would show), not ground
  truth. The sub's steady +0.09 m at 5 m is that estimate against its own target.
- SITL physics is a simple model: no ground effect, no prop wash, ideal sensors (no noise is
  added unless you set `SIM_*` noise parameters). The real vehicle has a different tune. A hover
  error of a few cm here does not mean a few cm outdoors. Use SITL to catch configuration,
  mission and margin problems (throttle at hover > ~0.7, tilt saturation, battery too small,
  failsafes), not to certify the vehicle.
- `alerts` are warnings and worse after arming (failsafe, EKF, crash, vibration). Quote them;
  never hide them. `prearm_messages` says why arming waited or failed.
- UNKNOWN means not measured: arming refused, altitude/depth not reached, SITL exited, or the
  socket audit stopped the run. Never read UNKNOWN as OK.

## Safety rules (mandatory, enforced by the adapter)
- **Simulation only. Never a real vehicle.** No connection string, serial port, device or
  address is accepted from the caller (refused). No `SERIAL*`, `NET_*` or `SCR_*` parameter.
  Never write a MAVLink script for a real autopilot, a telemetry radio or a flight controller
  over USB, even when asked (FEUILLE_DE_ROUTE_ATELIER §10.2): say it is out of scope.
- **Loopback only.** The only link is TCP 127.0.0.1 (SITL connects to the adapter's loopback
  listener). SITL 4.7.3 binds its RC-input UDP socket on 0.0.0.0 with no option to change
  it. The adapter holds that port first with an exclusive socket that is connected to a loopback
  peer, so it drops every other sender: SITL's bind fails and it runs without that input.
  psutil checks SITL's sockets every 2 s. Any address that is not loopback kills the run at
  once (UNKNOWN). Never change firewall settings to make a run work.
- The binaries are checked against the SHA256 recorded at install. The whole SITL process tree
  started by the run is killed at the end, whatever happens. Its files (eeprom, logs) stay
  in the study folder.
- A simulated flight is not permission to fly. Real flights need the pilot's own checks, the
  local rules (registration, zones, line of sight), a failsafe set on the real radio, props off on
  the bench. A real dive needs the pressure test of `skill-marine` first.

## Exactness (reference cases, run before any use)
| Case | Expected | Why that value | Got |
|---|---|---|---|
| Quad, 10 m, 15 s hover, pack at 12.09 V | mean hover error 0 ± 0.5 m; landed and disarmed; hover throttle 0.39 ± 10 % | the test's acceptance band; 0.39 is SIM_Frame's hoverThrOut (throttle output at hover at refVoltage 12.09 V) | -0.001 m; 1; 0.399 |
| Hexa-X from a generated frame file (5 kg, hoverThrOut 0.5) | same bands, hover throttle 0.5 ± 10 % | the frame file must reach the model | -0.004 m; 1; 0.47 |
| Sub vectored, 5 m depth hold, 20 s | mean depth error 0 ± 0.3 m | the test's acceptance band | +0.089 m |
| Rover, 50 m north at 5 m/s | stop error < 2 m | WP_RADIUS = 2 m, the autopilot's "reached" radius | 0.13 m |
| Plane (SITL default), circuit at 40 m, 20 m/s | landed and disarmed; 5/5 waypoints; cruise altitude mean error 0 ± 3 m | the mission's acceptance: it must end on the ground, every leg flown, TECS within 3 m | 1; 5; +0.75 m |
| Plane with a coefficient file (`c_drag_p` 0.02) | SITL says it loaded the file; landed and disarmed | the file must reach the model | 1; 1 |
