# skill-atelier

**Load when:** the user describes a whole project in natural language ("design me an underwater
thruster", "a 2 kg quadcopter frame", "a rover for sand", "a pressure housing for 100 m", "a
rocket for a G motor", "a humanoid leg") and expects it designed, simulated, corrected and ready
to build in one go. This skill is the method; the domain skills hold the details.

## The pipeline (do it in this order, without asking again what is already said)
1. **Understand -> requirements.** From the request, write down what was said, with numbers.
   Ask ONE grouped question only for what is missing AND changes the design (load, depth,
   voltage, mass budget, printer bed...). Everything else becomes a hypothesis with a source or
   "UNSOURCED" (it is listed until checked).
2. **Project.** `project_status`, then `project_init(name, intent)`.
3. **Decompose.** List the subsystems (structure, sealing, propulsion, power, electronics,
   control, payload) and, for each, the physics that can make it fail. Pick the kinds:

   | Failure / question | kind (skill) |
   |---|---|
   | breaks, bends, too heavy | `fem_static` (skill-sim) |
   | resonates, vibrates | `fem_modal` |
   | buckles, collapses under pressure | `fem_buckling`, `pressure_hull` (skill-marine) |
   | overheats, dilates | `fem_thermal`, `fem_thermomech`, `sat_thermal` (skill-space) |
   | fails after cycles | `fatigue` |
   | floats wrong, rolls over | `hydrostatics`, `added_mass` (skill-marine) |
   | not enough thrust / efficiency | `thruster` (skill-marine), `airfoil`, `wing` (skill-aero-sim) |
   | leaks | `oring_gland` (skill-marine) |
   | flight, trajectory, stability | `rocket_flight`, `nozzle`, `combustion` (skill-aero-sim), `sitl_*` (skill-sitl) |
   | flow, drag, pressure drop | `cfd_su2`, `flow_elmer` (skill-cfd), `pde_diffusion` |
   | mechanism, robot, balance, motors | `multibody`, `inverse_dynamics`, `control`, `balance` (skill-dynamics) |
   | wheels on soil, digging, ground | `terramech` (skill-dynamics), `ogs_steady`, `consolidation` (skill-geo) |
   | circuit, battery, wiring, magnets | `spice`, `battery`, `wire_sizing`, `power_budget`, `magnet` (skill-elec) |
   | orbit, delta-v | `orbit`, `delta_v` (skill-space) |
   | parts fit together | `tolstack`, `check_assembly` |
   | vehicle in water / ROS | `gz_underwater`, `convert_check` (skill-gazebo), ROS 2 env (skill-robotics-ros) |
   | printable, fits the bed | `printability`, `print_check`, `print_project` (skill-fab) |
   | firmware computes right, fits the flash | `firmware_sim` (skill-firmware) |
   | made with standard screws / bearings | `std_part`; `analyze(dfm=...)` for machining / moulding |
4. **Contract and study.** `require(...)` for every geometric requirement (bbox, holes,
   mass, wall...). `study start` with `criteria` per kind (`safety_factor >= 2`, `f1_Hz >= 2×`
   the excitation, `net_buoyancy_N` in a band, `autonomy_h >= x`...), the hypotheses with their
   sources, the real-test `acceptance` and `agreement`.
5. **Model parametrically.** Every dimension that may change lives in a spreadsheet cell
   (skill-partdesign); parts and assembly with the freecad tools; `check` after every step,
   `record_step` when OK, `checkpoint` before a risky one.
6. **Simulate all criteria** (`simulate(..., study=name)`). FAIL -> correct:
   - a dimension -> `study optimize` on its cell (the lightest / smallest that passes), or
   - the concept (material, shape, architecture) -> change it, `study iterate {change}`.
   Repeat until the study says `ready_to_build`. Never stop at the first FAIL, never call a
   FAIL or UNKNOWN "good enough".
   Before trusting a stress result:
   - re-run it with `mesh_size` halved: a peak that keeps rising sits on a sharp re-entrant
     corner (a singularity), and the fix is a fillet in the design, not a finer mesh;
   - keep a margin for what the mesh has not settled, written as a hypothesis;
   - when two models bracket reality (clamped vs simply supported plate, bonded vs sliding
     contact), size on the worse one and say so.
   Before `ready_to_build`, run `analyze(target, dfm={...})` on every part. A closed internal
   cavity, a sharp inside corner or an unreachable face is a design change, not a note: for
   example, split a sealed shell into a tube and end caps with seal glands, then check the glands
   (`oring_gland`) and their tolerance stack (`tolstack`).
7. **Fabrication files.** `export` (STEP / 3MF / STL, read back and compared), `drawing_sheet`
   (DXF), `bill_of_materials`, `print_project` (PrusaSlicer project, no G-code), KiCad / WireViz
   outputs for electronics. Machining: STEP + drawing for the human's CAM, never G-code.
8. **Real test plan.** `study test_plan` -> procedure (with the safety notes) + `measurements.csv`.
   The human builds and tests; then `study built`, `study measure {csv}`. Disagreement ->
   `study calibrate {auto}` -> simulate again -> until `VALIDATED`. `study report` at the end.

## Answering the user
One short report per milestone, not per tool call:
- what was understood (requirements, hypotheses still unsourced);
- the design (key dimensions, mass, materials, parts list);
- every criterion with its number and verdict;
- what was corrected and why (iteration log);
- the files ready for fabrication;
- the test to run and what the model predicts.

Pictures (`render_views`) only at the design milestone or on request.

## Hard limits (they are requirements, not advice)
- No command to a real vehicle, robot, printer or machine. No flashing a real board. No purchase.
- No machine G-code written by the AI. Reading the human's G-code to check it is fine.
- Mains or high voltage (> 50 V AC, > 120 V DC): no design on that side, "qualified person
  required". Li-ion: BMS + fuse, datasheet limits only. Underwater: SELV, IP68, pressure test
  first. Solid rocket propellant formulation and anything weapon-like: refused.
- Never "safe" or "certified": "the model predicts X under hypotheses H; test it".

## `study` arguments
| action | args |
|---|---|
| `start` | `goal`, `criteria` {kind: {value: {min\|max}}}, `hypotheses` [text \| {text, source}], `params`, `acceptance` {quantity: {min\|max}} (real test), `agreement` (0.1) |
| `status` | (none) |
| `hypothesis` | `text`, `source` |
| `iterate` | `change`, `params` |
| `built` | `note` |
| `test_plan` | `predicted` {quantity: "kind.value"}, `instruments` |
| `measure` | `data`, `source`, `predicted`, or `csv` |
| `calibrate` | `auto` {quantity, param, e.g. "material.E"}, or `factors`, `note` |
| `optimize` | `kind`, `target`, `params`, `vars` [{cell: "Sheet.alias", lo, hi}], `objective`, `sense`, `constraints`, `max_runs`; the best design is applied |
| `report` | (none) |

`simulate(..., study=name)` feeds the study, with the calibration applied.
