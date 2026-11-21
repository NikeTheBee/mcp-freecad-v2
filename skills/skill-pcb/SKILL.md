# skill-pcb

**Load when:** PCB trace width or current, via current, controlled impedance (microstrip /
stripline, USB, Ethernet, RF 50 ohm), netlist or schematic sanity check (ERC), wiring harness
(connectors, cables, BOM, voltage drop).

Every kind below is called as `simulate(kind, params=...)`: adapter `pcb`, env `elec`.

| kind | params | main values |
|---|---|---|
| `trace_width` | `current_A` and/or `width_mm`, `temp_rise_C` (10), `copper_oz` (1 = 35 um), `layer` external/internal, `length_mm`, `ambient_C`, `voltage_V` | `min_width_mm`, `capacity_A`, `R_ohm`, `drop_V`, `loss_W`, `drop_pct` |
| `via` | `drill_mm`, `plating_um` (25), `length_mm` (1.6), `temp_rise_C`, `current_A` | `capacity_A`, `R_ohm`, `vias_needed` |
| `impedance` | `line` microstrip (`height_mm`) or stripline (`spacing_mm`, ground to ground), `er`, then `width_mm` (analysis) or `z0_ohm` (synthesis) | `width_mm`, `z0_ohm`, `eps_eff`, `delay_ps_per_mm` |
| `erc` | `parts` {REF: {pin: type}}, `nets` {name: ["REF.pin", ...]}, `power_flags` [nets fed from off-board] | `errors`, `warnings`, erc_report.txt. Pin types: in, out, bidir, tristate, passive, power_in, power_out, open_collector, open_emitter, nc |
| `harness` | `harness` (WireViz YAML or dict), `currents_A` {"W1": A or "W1:2": A}, `voltage_V`, `temperature_C` | `wires`, `wire_length_total_m`, `max_drop_V`, `max_drop_pct`, harness.svg/html/tsv |

**Rules:**
- `erc` defaults to the limit errors = 0. Each error is quoted in the summary.
- A wrong pin type gives a wrong verdict. Take pin types from the datasheet.
- `harness` refuses `image`, `template` and `tweak`, because they read files from disk.
- A wire's current rating comes from `elec wire_sizing`.
- IPC-2221 is conservative, and the result says when it is extrapolating. Impedance formulas are
  closed forms, accurate to a few %; the fab's stack-up calculator has the final word.
- For a real board (ERC/DRC, Gerber, STEP), KiCad (`kicad-cli`) is the next step, lot R2 in the
  roadmap.

**Validated on:**
- Pozar, Examples 3.5 and 3.7;
- IPC-2221 at 1 A, 10 degC, 1 oz (0.30 mm outer, 0.78 mm inner);
- rho L / A for copper (trace and via);
- an ERC with 3 planted faults, and a clean one;
- a WireViz harness with its voltage drop.
