# skill-vsp

**Load when:** conceptual aircraft or drone layout, wing/tail sizing, VSPAERO, OpenVSP, "give me
a wing I can put in FreeCAD", stability derivatives of a whole aircraft, lofted surface from a few
numbers, span efficiency, induced drag of a real planform.

## Where it sits among the aero tools
| want | tool |
|---|---|
2-D section polar | `aero airfoil` (NeuralFoil) or XFOIL |
a quick planform number, in milliseconds | `aero wing` (AeroSandbox VLM) |
**a real lofted aircraft + forces + a surface for CAD** | **`vsp`** (this one) |
unsteady aerodynamics for flutter | `aeroelastic` (PanelAero DLM) |
thickness, viscosity, separation, transonic | `cfd` (SU2) |

## Use the tool
`freecad-layers` -> **`simulate(kind, target, params)`**, adapter `vsp` (env `aero`, pack `air`).
Headless: the adapter writes an OpenVSP script, `vspscript.exe` runs it, VSPAERO's CSV comes back.

| kind | params | main values / files |
|---|---|---|
| `vsp_wing` | `span_m` (FULL span), `root_chord_m`, `taper`, `sweep_deg`, `dihedral_deg`, `twist_deg`, `thickness_ratio`, `camber`, `span_panels`, `alpha_start_deg`, `alpha_end_deg`, `alpha_points`, `mach`, `sref_m2`/`bref_m`/`cref_m`, `run_aero`, `export_step`, `export_stl` | `CL`, `CDi`, `CDtot`, `CMy` per alpha, `CL_alpha_per_rad`, `span_efficiency`, `lift_to_drag`, `aspect_ratio`, `stl_span_m`; files `results.csv`, `wing.stp`, `wing.stl`, `wing.vsp3` |
| `vsp_model` | your own model via `params.files = ["aircraft.vsp3"]`, plus `sref_m2`, `bref_m`, `cref_m` and the sweep inputs | the same polar |

`run_aero: false` skips VSPAERO entirely and just builds and exports the geometry - the fast path
when all you want is a surface for FreeCAD.

## Conventions that bite
- `span_m` is the **full** span of the mirrored wing. OpenVSP's own section parameter is the half
  span; the adapter converts, so do not halve it yourself.
- `taper` is tip chord / root chord. Sweep is at the quarter chord.
- Coefficients are referred to the `Sref`/`bref`/`cref` printed in the answer. Give your own if
  you are comparing with a published figure, or the numbers will not line up.
- `CDi` is the computed induced drag. **`CDo` is VSPAERO's own parasite estimate** at the default
  Reynolds number (`Re_millions` in the answer), not a validated drag count - judge `CDi`, treat
  `CDtot` as indicative, and get real drag from `cfd` or from measured data.

## The path to CAD
`export_step: true` then import `wing.stp` into FreeCAD (skill-exchange), and build your own
sketch, spars and ribs on it. A loft is not a structure: OpenVSP gives the outer surface, FreeCAD
and `fem_static` give the part that survives flight. `export_stl` is there for printing and for
the quick check the adapter itself does on the exported span.

## Validated on
The lift slope of a rectangular AR-8 wing against Helmbold's formula (4.60 /rad computed against
4.91 predicted, 6 % low: Helmbold is an approximation and the vortex lattice adds its
discretisation), the induced-drag identity CDi = CL²/(pi AR e) against VSPAERO's own span
efficiency (matches to better than 0.1 %), and the exported STL's span against the wing asked for
(8.000 m for 8 m). See `REFERENCES` in `server/adapters/vsp.py`.

## Limits
Vortex lattice: inviscid, linear, no stall, no separation, no compressibility beyond
Prandtl-Glauert. This build exposes no panel mode. A propeller in the flow, a high angle of
attack or a transonic wing is outside it. The Python API bundled with OpenVSP (built for 3.13) is
never imported - everything goes through files, so a run can be re-done and checked.

## Whole aircraft: `vsp_aircraft`
`fuselage` {length_m, max_diameter_m, exit_diameter_m, nose_fraction, tail_fraction}, `wing` /
`htail` / `vfin` {span_m (a fin's height), root_chord_m, taper, sweep_le_deg (leading edge),
dihedral_deg, thickness_ratio, camber, x_m, z_m}, optional `intake` (closed pod fairing: draw a real
scoop in FreeCAD), `cg_x_m` or `cg_mac_fraction`, the alpha sweep, `run_aero`.

It returns:
- `x_neutral_point_m`, `static_margin_mac` (5 to 15 % for a jet model), `CL_alpha_per_rad`,
  `Cm_alpha_per_rad`, `stl_length_m` / `stl_span_m` / `stl_height_m`;
- `aircraft.stp`, a trimmed B-Rep (SurfaceIntersection, wing-body junction cut, in metres) that
  FreeCAD imports as one shell; plus `aircraft.stl` and `aircraft.vsp3` for the OpenVSP GUI.

**Limits:**
- The fuselage carries no lift in the vortex lattice, so the real neutral point is slightly
  forward: keep a few % of extra margin.
- `lift_to_drag_last` from this solver is not reliable (it has come back negative): judge drag
  elsewhere.

**Validated on:** the exported length and span, zero lift at zero incidence, and moments about
the computed neutral point that do not change with alpha (1e-6).
