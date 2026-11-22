# skill-ai

**Load when:** a neural network for piloting, control or decisions in a robot / drone /
mechatronic system; learning a model from data (surrogate, sensor calibration, classifier);
reinforcement learning of a controller; checking an ONNX model for an embedded target.

Every kind below is called as `simulate(kind, params=...)`: adapter `ai`, env `ai` (pack `ia`:
PyTorch CPU, Gymnasium, Stable-Baselines3, ONNX Runtime).

| kind | params | main values |
|---|---|---|
| `nn_fit` | `data_file` (CSV in the study folder) + `input_columns` / `output_columns`, OR `function` {out: expr} + `inputs` {x: [lo, hi]} + `samples`; `task` regression/classification, `hidden` [32, 32], `activation` tanh/relu, `epochs`, `lr`, `batch`, `seed`, `target_flash_kB`, `target_ram_kB` | `val_rmse`, `val_max_abs_err` / `val_accuracy`, `params`, `macs_per_inference`, `size_int8_kB`, `flash_ratio_*`, `onnx_vs_torch_max_abs`, `latency_us_pc`, model.onnx |
| `rl_train` | `env` (CartPole-v1, Acrobot-v1, MountainCar(-Continuous), Pendulum-v1, InvertedPendulum-v5, InvertedDoublePendulum-v5, Reacher-v5, Hopper-v5, HalfCheetah-v5, Walker2d-v5, Swimmer-v5, Ant-v5), `algo` PPO/SAC/A2C/TD3, `timesteps`, `seed`, `n_envs`, `net_arch`, `hyperparams` (whitelisted), `eval_episodes` | `mean_return`, `std_return`, `solved` (at or above the env's threshold), `onnx_action_agreement`, `params`, policy.onnx |
| `onnx_check` | `file` (.onnx in the study folder), `test_input` | `opset`, `params`, `size_kB`, `non_mcu_operators`, `latency_us_pc`, `output_0` |

**Your own robot:** set `env` to "custom" and give:
- `robot` {`template`: cart_pole / balance_bot, `params`} or {`mjcf`};
- `observe` [sensor names];
- `act` [actuators];
- `reward` (an expression over the sensor names, `u0..`, `t`), e.g.
  "1 - 0.02*x**2 - 0.01*x_rate**2";
- `terminate` (> 0 ends the episode), e.g. "maximum(abs(pitch) - 0.2, abs(x) - 2.4)";
- `control_hz`, `episode_s`, `success_return`;
- `randomize` {`mass_pct`, `sensor_noise` {name: sigma}, `initial` {joint: [lo, hi]}}.

The ONNX policy outputs actions in [-1, 1]; the command is ctrl = lo + (a + 1)/2 (hi - lo).
To check the firmware port, run the same law in `sil`.

**Device:** `device` is cpu, gpu or both; the default is the workshop's compute profile.
- `gpu` and `both` train the network on CUDA. They need the `ai-gpu` environment (pack `ia-gpu`,
  installed when the hardware scan finds an NVIDIA driver >= 570).
- In RL, the physics always steps on the CPU.
- Export, ONNX checks and latency always run on the CPU.
- `device_used` and `gpu` say what actually ran. A small MLP is often faster on the CPU; the GPU
  pays off on large networks and big batches.

**Rules:**
- Models are exported as ONNX only. `.pt`, `.pth`, `.pkl` and SB3 `.zip` files are refused,
  because they are pickles that run code when loaded.
- An ONNX file with external data or a custom operator domain is refused.
- A declared `function` uses the `mdo` whitelist, never `eval`.
- A policy is a simulation result: it never drives real hardware from here. On the real robot,
  start with a bench test and limits.
- Report the seed. Validation numbers are measured on the same distribution as the training
  data, so the sim-to-real gap is open until a bench test.
- `onnx_vs_torch_max_abs` must be about 1e-6, and `onnx_action_agreement` must be 1.

**Validated on:**
- sin(x) fitted to < 0.01;
- a circle classified at 97 % or better;
- CartPole-v1 solved by PPO, with the ONNX policy taking the same actions;
- the custom cart-pole template solved by PPO with ±10 % mass randomisation;
- a hand-built ONNX Gemm+ReLU graph with exact parameter count and output.
