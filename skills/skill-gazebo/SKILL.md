# skill-gazebo

**Load when:** an underwater vehicle in Gazebo (ROV/AUV sinks, rises, holds depth, terminal
velocity, thruster speed, attitude drift), or "does my URDF survive the conversion to SDF /
MuJoCo" (masses, inertias, joints, fixed links that disappear).

## Use the tool
`freecad-layers` → **`simulate(kind=..., params={...})`**, adapter `gazebo` (env `ros`: Gazebo
Jetty, gz-sim 10, sdformat 15). No `target`: these kinds read numbers or a model file.
Units SI: m, kg, s, N, kg m², rad (keys ending `_deg` are degrees). Depth is positive downwards,
`vz` positive **up**. Vectors of 6 are `[surge, sway, heave, roll, pitch, yaw]`.

| kind | params | key values |
|---|---|---|
| `gz_underwater` | `hull` {`shape` box `size` [x,y,z] \| cylinder `radius` `length` `axis` x/y/z \| sphere `radius`} or `model` (SDF/URDF relative to the study folder, + `link`); `mass` or `net_buoyancy_N` (default: neutral); `inertia` [ixx,iyy,izz(,ixy,ixz,iyz)] (default: solid hull); `com` [x,y,z]; `fluid_density` (1000); `gravity` (9.81); `fluid` (false = no water: free fall); `damping` {`linear` [6] N s/m, `quadratic` [6] N s²/m²}, positive; `added_mass` [6], a 6×6, or the `marine` added_mass output (positive); `current` [3] m/s; `thrusters` [{`pos`, `axis`, `thrust_N`, `diameter`}]; `initial_depth`, `initial_rpy_deg`, `duration`, `dt` (0.001), `record_hz` (100) | `final_depth_m`, `depth_change_m`, `max_depth_drift_m`, `vertical_velocity_m_s`, `vz_terminal_m_s`, `terminal_speed_m_s`, `terminal_reached`, `t63_s`, `predicted_terminal_speed_m_s`, `net_buoyancy_N`, `horizontal_speed_m_s`, `horizontal_drift_m`, `max_attitude_drift_deg`, `final_roll/pitch/yaw_deg`, `gz_sockets`; file `pose.csv` |
| `convert_check` | `urdf` (path relative to the study folder) or `urdf_xml` or `builtin:two_link`; `sdf` (optional: an SDF of yours to compare, else `gz sdf -p` makes it); `mjcf` (true: also load in MuJoCo, env `robot`); `tol` (1e-6, relative) | `total_mass_rel_err`, `mass_rel_err_max`, `inertia_rel_err_max`, `com_err_max_m`, `sdf_roundtrip_rel_err`, `dropped_links`, `moving_joints_urdf/sdf`, `sdf_valid`, `mjcf_*` (incl. `mjcf_dropped_links`) |

```
simulate("gz_underwater", params={"hull": {"shape": "cylinder", "radius": 0.1, "length": 1.0, "axis": "x"},
         "net_buoyancy_N": -5, "com": [0, 0, -0.02], "initial_rpy_deg": [10, 0, 0],
         "damping": {"linear": [10, 30, 30, 2, 2, 2], "quadratic": [5, 40, 40, 1, 1, 1]},
         "added_mass": [3, 30, 30, 0.1, 1, 1], "duration": 15,
         "limits": {"max_depth_drift_m": {"max": 5}}})
-> OK depth 10 -> 11.88 m, vz -0.1404 m/s (terminal; predicted 0.14039), attitude drift 10.1 deg
   (rights itself), gz sockets loopback only
```

- **Added mass from the marine pack:** pass its `added_mass` result (`added_mass_6x6` or
  `A11_kg..A66_kg_m2`). It goes into the SDF `<fluid_added_mass>` of the link (about the link's
  inertial frame: give `cg_mm` there = the vehicle's CoM). Damping is not computed by Capytaine at
  zero speed: take it from tests, Fossen-type tables or CFD, and say where it came from.
- **Thrusters:** a fixed command in N along `axis` (vehicle frame), re-sent every 0.05 s. The
  propeller spin gives a small reaction roll: it shows up in the attitude drift.
- **Verdict:** `gz_underwater` is judged on `params.limits` (no criteria → UNKNOWN).
  `convert_check` is OK when every mass/inertia agrees within `tol`, FAIL otherwise
  (or when a moving joint is missing / changed type, or the SDF is not valid).

## Read the results honestly
- The fluid is uniform and **unbounded**: no free surface, no waves, no seabed. A positive body rises
  for ever; a surfacing or wave case needs another tool (say so).
- Buoyancy comes from the **collision** volume; the hydrodynamics are Fossen-type lumped
  coefficients: the answer is only as good as the damping you give.
- `terminal_reached` false → lengthen `duration` before quoting a terminal velocity.
- The integrator is semi-implicit (error ~ dt/T): the free fall is 0.05 % long at dt = 1 ms.
- `convert_check`: sdformat **lumps fixed links** into their parent (listed in `dropped_links`,
  composite inertia checked exactly). MuJoCo fuses them too, **silently**, and a fixed-base root
  link vanishes into the world (its mass is not simulated: `mjcf_total_mass_kg` shows it). A URDF
  link without `<inertial>` becomes 1 kg / identity in SDF: flagged as a problem.

## Exactness (why you can trust it)
Reference cases, re-run whenever the adapter or the environment changes:

| Case | Expected | Got |
|---|---|---|
| Neutral body holds its depth, 10 s | drift < 1 cm | 0.0 m |
| Linear damping, F = 98.1 N, c = 100 | v = F/c = 0.981 m/s | 0.98100 |
| Same + added mass 60 kg | t63 = (m+mₐ)/c = 1.50 s | 1.498 s |
| Quadratic damping, k = 100 | v = √(F/k) = 0.9905 m/s | 0.99045 |
| No fluid, free fall 2 s | g t²/2 = 19.62 m | 19.630 |
| Thruster 25 N, surge c = 50 | u = T/c = 0.500 m/s | 0.4998 |
| 2-link URDF + fixed sensor → SDF | masses/inertias exact, 1 link lumped | 0 / 1e-15, 1 |

## Network safety (what gz binds)
gz-transport normally discovers peers by UDP multicast on every interface. The adapter runs gz
with `GZ_IP=127.0.0.1`, a unique `GZ_PARTITION`, the **zenoh** backend listening on
`tcp/127.0.0.1` only with multicast scouting off, and parks the legacy discovery on ports Windows
refuses to bind (excluded port ranges), since that discovery always binds `0.0.0.0`. psutil samples
every socket of the gz process tree during the run: any non-loopback address → gz killed, UNKNOWN.
If no unbindable port exists on the machine, gz is not started (UNKNOWN, with the reason). Remote
model URIs (http, Fuel) and files outside the study folder are refused. Never change the firewall.

## Under the hood
`server/adapters/gazebo.py`: writes `gz/world.sdf`, runs `gz-sim-main` (what `gz sim` dispatches
to) `-s -r --iterations N --record-path gz/log --record-topic /model/vehicle/pose`, reads
`gz/log/state.tlog` (SQLite, `gz.msgs.Pose_V`). `convert_check` calls `gz-sdformat-sdf -p / -k`.
The ZeroMQ backend of gz-transport aborts on this machine (wepoll "Bad file descriptor"): keep zenoh.
