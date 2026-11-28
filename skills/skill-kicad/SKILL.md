# skill-kicad

**Load when:** a real PCB in KiCad: DRC / ERC, Gerber / drill / placement / IPC-2581 for the
fab, the board's STEP for the enclosure in FreeCAD.

Every kind below is called as `simulate(kind, params=...)`: adapter `kicad`, env `sim`. It needs
KiCad 10, installed once by the owner: `winget install -e --id KiCad.KiCad`. Until then
`tools_status` shows the adapter as not validated, and nothing runs.

| kind | params | main values |
|---|---|---|
| `kicad_drc` | `board` (.kicad_pcb in the study folder), `schematic_parity` | `errors`, `warnings`, `unconnected`, `type_<rule>`, `clearance_actual_min_mm` (worst gap measured), drc_report.json. Default limits: errors 0, unconnected 0 |
| `kicad_erc` | `schematic` (.kicad_sch) | `errors`, `warnings`, `type_<rule>` |
| `kicad_fab` | `board`, `allow_unconnected` | `gerber_files`, `drill_files`, `has_edge_cuts`, `has_ipc2581`, fab/. Refused unless the DRC is clean |
| `kicad_step` | `board` | `size_x_mm`, `size_y_mm`, `size_z_mm`, `solids`, board.step |

**Chain:**
1. `pcb` sizes the traces and impedances and checks the netlist.
2. KiCad draws the board.
3. `kicad_drc`, then `kicad_fab`.
4. `kicad_step`, then into FreeCAD, then `check_assembly` (the board fits the case).

**Rules:**
- The fab's capabilities (min track, min drill, annular ring) must be in the project's rules
  (.kicad_pro) to be checked.
- KiCad's Python plugins and its IPC server are never started.
- Third-party KiCad MCP servers are not used: they run code and write outside the project.
- A board written as text (a generator script) with its own footprints: put them in a project
  library (`<name>.pretty` + `fp-lib-table` + `.kicad_pro` next to the board) and pass every file
  in `files`, else the DRC warns `lib_footprint_issues` on each footprint.
- KiCad 10 warns `track_dangling` on any track end that touches nothing: end tracks on a pad.

**Validated on:** a generated 30 x 20 mm board:
- DRC is clean with 2 mm between the tracks;
- DRC finds the clearance violation with 0.10 mm between them, and measures it at 0.10 mm (the
  number of violations listed for one gap changes from run to run: judge the gap, not the count);
- the full fabrication set is exported;
- the STEP box is 30 x 20 x 1.51 mm: KiCad 10 exports the board body as the dielectric alone
  (default stack-up: 1.6 mm minus 2 x 0.035 copper and 2 x 0.01 mask). Add the copper and the
  mask when checking a tight fit.
