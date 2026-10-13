# skill-fem

**Load when:** structural analysis, stress, displacement, "will this part hold", FEM, CalculiX.

## Use the tool, not hand-written FEM code
`freecad-layers` → **`fem_static`**. It meshes with Gmsh (2nd-order tets), solves with CalculiX
(both ship with FreeCAD) and returns measured values plus an OK/FAIL/UNKNOWN verdict:

```
fem_static(target="Bracket", fixed=["xmin"],
           loads=[{"faces": "xmax", "force_N": 200, "direction": [0, 0, -1]}],
           material="aluminium", min_safety_factor=2.0, max_displacement_mm=0.5)
-> OK FEM static (aluminium, 23295 nodes, mesh 2.0 mm, 7.4 s): max displacement 0.303 mm at [...];
   von Mises 76.5 MPa away from supports at [...] (peak 76.5 MPa); safety factor 3.14
```

- **Faces:** `"FaceN"`, or a side of the part's bounding box: `xmin|xmax|ymin|ymax|zmin|zmax`.
  A side means every planar face lying on it. Use `measurement_operations list_faces` for
  anything else.
- **Loads:** total force in N per entry, spread over the faces; `direction` is any vector.
- **Materials:**
  - steel S235, stainless 304, aluminium 6061-T6, pla, petg, abs;
  - or a dict `{"E": MPa, "nu": ..., "rho": kg/m3, "yield": MPa}`.
  - The yield values are typical minimums.
- **Verdict:**
  - FAIL when the safety factor is below `min_safety_factor` (default 2), or when the displacement
    is over `max_displacement_mm`.
  - UNKNOWN when the part is not a valid solid, or meshing or solving failed. Never treat UNKNOWN
    as OK.
- **Ask the user** for supports, loads and material before solving. Don't invent them. Record
  them with `record_step`.

## Read the results honestly
- The safety factor uses von Mises stress **away from the supports**, more than one element from a
  fixed face.
- The peak right at an ideal fixed face is usually a mesh singularity; the tool reports it and says
  so. If that region matters, model the real fixture: bolts, a contact face.
- `refine=True` solves again on a 1.6× finer mesh and reports how much the results moved. More
  than 5 % on displacement or 10 % on stress means the result is not converged: refine further,
  or pass `mesh_size_mm`. A size too small for the 150,000-node limit is refused at once with the
  smallest size that fits; past that limit, a finer local study is out of scope (say so).
- Scope is linear elastic with small displacements. It does **not** cover buckling (slender parts
  in compression), plasticity, contact, fatigue or vibration. Say so when the question needs one
  of these.
- 3D-printed parts are weaker across layers (Z) than the bulk values; orient the part or keep a
  bigger margin.

## Exactness (why you can trust it)
`install/fem_test.py` checks CalculiX against closed-form results:

| Case | Error |
|---|---|
| Cantilever deflection, F·L³/3EI, steel | 0.3 % |
| Same cantilever, aluminium | 0.1 % |
| Bar in tension, elongation | 0.25 % |
| Bar in tension, stress | 4 % |

It also checks the load direction, and the traps: limits that must FAIL, bad inputs that must be
refused, a non-solid that must be UNKNOWN.

## Under the hood (only if the tool can't express it)
`server/freecad_layers/fem.py` uses `ObjectsFem` (`makeSolverCalculiXCcxTools`,
`makeConstraintFixed/Force`, `makeMeshGmsh`), `femmesh.gmshtools.GmshTools`, and
`femtools.ccxtools.FemToolsCcx`. Results land in `FEM_<part>_Analysis`. A rerun replaces the
previous one. Other loads (pressure, gravity) or solvers (Elmer, Z88): extend `fem.py` with a test.
Don't hand-roll a one-off script.
