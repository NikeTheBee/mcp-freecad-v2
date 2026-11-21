# skill-cycle

**Load when:** jet engine cycle, turbojet thrust / TSFC / mass flow, NPSS-type analysis, engine
off-design (another altitude, Mach or thrust on the same hardware).

`simulate("turbojet", params=...)`: adapter `cycle`, env `opt`, NASA pyCycle 4.4.

| params (SI) | default |
|---|---|
| `thrust_N`, `T4_K`, `comp_PR`, `comp_eff`, `turb_eff` | 52489 N, 1316.7 K, 13.5, 0.83, 0.86 |
| `alt_m`, `mach`, `Nmech_rpm`, `burner_dPqP`, `nozzle_Cv` | 0, 1e-6, 8070, 0.03, 0.99 |
| `off_design` [{`alt_m`, `mach`, `thrust_N`}] (up to 8) | none |

**Values:** `W_kg_s`, `FAR`, `OPR`, `Fg_N`, `Fn_N`, `TSFC_g_kN_s`, `TSFC_lbm_lbf_h`, `Tt3_K`,
`Tt4_K`, `turb_PR`, `converged`. Off-design values have the prefix `od<k>_`, plus `Nmech_rpm` and
`Wc_map_ratio`.

**Reading:**
- `converged` 0 means FAIL.
- The maps are pyCycle's generic AXI5 and LPT2269, scaled at design. There are no bleeds, no
  cooling and no power offtake.
- The result is a cycle estimate, not a certified engine.
- A `Wc_map_ratio` far from 1 means the point is far off the map and has been extrapolated.

**Validated on:** pyCycle's simple turbojet regression values (NPSS-checked), design and
off-design, to 1e-5.
