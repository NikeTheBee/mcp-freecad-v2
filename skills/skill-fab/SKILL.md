# skill-fab

**Load when:** preparing a 3D print beyond the export: "does it fit my printer", mesh statistics,
a PrusaSlicer project, arranging several parts or copies on the bed, using the user's own profile.

## The rule
**These tools prepare files; the person slices, machines and prints.** Nothing here produces
G-code, slices, sends anything to a printer or a machine, or runs a post-processing script (`skill-cam`, roadmap §10.2). Reading the person's own G-code to check it
is allowed; writing it is not.

## Use the tool
First `printability` then `export` (3MF/STL, `skill-print3d`): the geometry checks live there.
Then `freecad-layers` -> **`simulate(kind, params)`** with the adapter `fab` (env `sim`, tool
PrusaSlicer 2.9.7 console). Model files come from the project with `params.files` (STL, 3MF,
OBJ, STEP). Units: mm, mm³, degrees.

| kind | key params | main outputs |
|---|---|---|
| `print_check` | `model` or `models` [..]; `bed_mm` [x, y] or [x, y, z] | `size_x/y/z_mm`, `volume_mm3`, `manifold`, `open_edges`, `mesh_errors`, `parts`, `objects`, `fits_bed` (a 90° turn about Z allowed), `fit_together` (advisory), `per_object` |
| `print_project` | `models` (names, or {`file`, `copies`, `rotate_x_deg`, `rotate_y_deg`, `rotate_z_deg`}); `profile` (**required**: the user's .ini from PrusaSlicer, File > Export > Export Config); `copies`; `rotate_*_deg`; `spacing_mm` (6); `allow_turn` (true); `output` (`project.3mf`) | the project 3MF; `objects_readback`, `volume_mm3`, `gcode_found` (0), `profile_mismatch` (0), `fits_bed`, `printer` |

```
simulate("print_check", params={"files": ["out/bracket.3mf"], "model": "out/bracket.3mf",
         "bed_mm": [250, 210, 210]})
simulate("print_project", params={"files": ["out/bracket.stl", "profiles/mk4_petg.ini"],
         "models": [{"file": "out/bracket.stl", "copies": 4, "rotate_x_deg": 90}],
         "profile": "profiles/mk4_petg.ini", "output": "brackets.3mf"})
```
Then tell the person: open the 3MF in PrusaSlicer, check the profile, slice, inspect the preview
(supports, first layer, time) and print it themselves.

## Verdicts and refusals
- `print_check`: FAIL when a mesh is open / not manifold or an object does not fit `bed_mm`;
  without `bed_mm` (and no `limits`) -> UNKNOWN.
- `print_project`: OK only when the parts fit the profile's bed and height, the 3MF holds no
  G-code (unzipped and searched), the profile re-read by PrusaSlicer equals the one given, and
  every object is read back. Parts that do not fit -> FAIL and **no file**.
- **Refused** (UNKNOWN, `refused: 1`, nothing written): no profile (the printer is never guessed);
  a profile with a non-empty `post_process` (or a config bundle with sections); any parameter
  asking to slice or for G-code / SLA output, any raw argument list, any value that looks like a
  command-line option, an output name that is not `*.3mf`, a file outside the study folder, an
  input 3MF that carries G-code or a script. The argument list is always built by the adapter.
  Do not try to work around a refusal: explain it to the person.

## Limits (say them)
- Statistics and repairs are PrusaSlicer's (`--info`). Wall thickness and overhangs are
  `printability`'s job, not this one.
- Arrangement: shelf packing of bounding boxes with gaps, centred on the bed (no nesting of
  concave outlines); a 90° turn about Z when it helps. Orientation is the caller's choice
  (no automatic optimisation): pick the flattest stable face down.
- The profile's own custom start/end G-code templates stay as the person wrote them; they are
  only carried in the project, never executed here.

## Exactness (reference cases, run before first use)
20 mm cube STL written by the adapter: `--info` volume 8000 mm³, manifold, fits a 250 × 210 × 210
bed; a 300 mm cube on that bed: FAIL; a project with two cubes: written, 2 objects and
16000 mm³ read back, profile identical, no G-code inside; a profile with `post_process`, a request
with `--export-gcode` arguments, and a request without a profile: refused.
