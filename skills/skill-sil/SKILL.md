# skill-sil

**Load when:** firmware in the loop / software-in-the-loop, "does my STM32 code really balance
the robot", control law running on the MCU against the physics, deadline misses, a robot model
(cart-pole, two-wheeled balancing robot / gyropode) or your own MJCF to check before tuning.

Both kinds are called as `simulate(kind, params=...)`: adapter `sil`, env `robot` (MuJoCo +
Renode 1.17 + Arm GNU).

| kind | params | main values |
|---|---|---|
| `robot_model` | `robot` {`template`: cart_pole / balance_bot, `params`} or {`mjcf`: file in the study folder} | `divergence_rate_1_s` vs `divergence_rate_closed_form`, `time_to_double_s`, sensors, actuators, the MJCF written |
| `sil_run` | `robot`, `inputs` [sensor names, at most 16], `outputs` [actuators, at most 8], `control_hz`, `duration_s`, `initial` {joint: value}, `push` {t_s, joint, dv}, `sensor_noise` {name: sigma}, `quantization` {name: lsb}, `sources` {"main.c": ...} / `files`, `defines`, `opt`, `reference_gains` {actuator: [k per input]} | `deadline_misses`, `final_abs_<in>`, `max_abs_<in>`, `max_dev_vs_reference`, `flash_bytes`, `ram_static_bytes`, sil_trajectory.csv |

**Template parameters:**
- cart_pole: `cart_mass_kg`, `pole_mass_kg`, `pole_com_m`, `pole_inertia_kgm2`, `max_force_N`.
- balance_bot: `body_mass_kg`, `body_com_m`, `body_inertia_kgm2`, `wheel_mass_kg`,
  `wheel_radius_m`, `wheel_inertia_kgm2`, `max_torque_Nm`.

**Firmware contract:**
- Include `"mcpfc_io.h"`.
- Loop on `last = mcpfc_wait(last);`, read `mcpfc_io.in[i]`, write `mcpfc_io.out[j]`, then
  `mcpfc_io.seq_out = last;`.
- The command is applied for the next period: one period of delay, as on the board.
- If `seq_out` was not updated in time, that period is a deadline miss.

**Rules:**
- `max_dev_vs_reference` of about 1e-6 means the firmware implements the designed law
  (float32 vs float64).
- Renode is instruction-accurate, not cycle-accurate: timing margins are indicative.
- Renode only talks over 127.0.0.1 and its sockets are audited. The firmware never leaves the
  emulator: flashing the board is a human action.

**Validated on:**
- the divergence rate of both templates against the closed form
  sqrt(m g l a / (a c - b^2));
- an LQR law compiled for STM32 that balances the cart-pole in Renode, matching the Python law
  to 1e-8.
