# skill-firmware

**Load when:** microcontroller firmware: control loops, filters, state machines, protocol code,
"does this firmware compute the right thing", code size / RAM on an STM32-class Cortex-M4.

## The tool
`simulate("firmware_sim", params={...})` compiles C with the Arm GNU Toolchain (15.2) for an
STM32F407 (Cortex-M4F, 1 MB flash, 128 KB RAM) and runs it in Renode for `run_s` virtual seconds.
The firmware prints its results on the UART (USART2) with `printf` (newlib-nano, floats included):
one `key=value` per line. Returning from `main` prints `MCPFC_EXIT <code>`; a fault prints
`MCPFC_HARDFAULT`.

```
simulate("firmware_sim", params={
  "sources": {"main.c": "...", "pid.h": "..."},        # or files: ["fw/main.c", "fw/pid.h"]
  "defines": {"KP": "1.5f"}, "opt": "-O2", "run_s": 1,
  "expect": {"y100": [0.99408, 1e-5], "state": 3},      # value alone = exact
  "contains": ["READY"],
  "limits": {"flash_bytes": {"max": 65536}, "exit_code": {"min": 0, "max": 0}}})
-> OK firmware_sim: flash 14 KB, RAM 1 KB static, exited 0 ...; values: y100=..., text_bytes=...
```

## Use it like a unit test of the real target
- Put the algorithm in its own `.c/.h` (no HAL), call it from a `main.c` test harness that
  prints the numbers to check: the same files later build for the real board.
- Fixed-point or float, the emulator runs the real instructions: rounding, overflow, the FPU's
  single precision are those of the chip. Compare with the closed form or a Python reference,
  with a tolerance that says which precision you expect.
- `flash_bytes`, `ram_static_bytes` (data + bss) come from the linker: budget them with `limits`.
- Timing is instruction-accurate, not cycle-accurate: do not read real-time margins from it.

## Limits
- One platform (STM32F4) and the UART; other peripherals are Renode's models (no sensors wired).
- No RTOS supplied (FreeRTOS or Zephyr sources can be passed as files if their licences allow).
- `#include` of absolute or `..` paths is refused; only the given files are compiled.

## Never
- Flash a real board, open a serial port to real hardware, or send firmware to a device: that is
  the human's action, after the tests pass. The emulator is the only target here.
