# skill-marine

**Load when:** boat, buoy, ROV/AUV, hull, flotation, ballast, stability, "will it float",
propeller/thruster, cavitation, pressure housing/hull, depth rating, O-ring seal.

## Use the tool
`freecad-layers` -> **`simulate(kind, target, params)`** with the adapter `marine` (env `sim`).
Every answer is OK / FAIL / UNKNOWN + numbers. No `limits` -> UNKNOWN ("computed, nothing judged"),
except `oring_gland` (Parker ranges built in) and `pressure_hull` with `safety_factor`.

Frame of the CAD: **x forward, y port (transverse), z up**, mm. The target must be the closed
**outer envelope** of the hull (a filled solid; sealed inner voids are counted as displaced water).

| kind | target | key params | main outputs |
|---|---|---|---|
| `hydrostatics` | hull solid | `mass_kg` (solves the waterline) or `waterline_z_mm`, or `submerged: true`; `cg_mm [x,y,z]`; `rho_kg_m3` (1025 sea, 1000 fresh) | `draft_mm`, `displacement_kg`, `buoyancy_N`, `CB_mm`, `reserve_buoyancy_N/_pct`, `BM_T_mm`, `BM_L_mm`, `GM_T_mm`, `GM_L_mm`, `static_heel_deg`; submerged: `net_buoyancy_N` (+ floats up), `BG_mm` (+ = CB above CG, stable) |
| `added_mass` | hull solid | `fluid: "infinite"` (default, deep vehicle) or `"free_surface"` + `waterline_z_mm` + `periods_s` / `omegas_rad_s` (`"inf"`, 0 allowed); `cg_mm` (rotation centre, default volume centroid); `panels` (3000, whole surface before clipping; 200..8000) | `added_mass_6x6`, `damping_6x6` (SI, surge..yaw), `A11_kg`..`A66_kg_m2`, `B33_kg_s` |
| `thruster` | none | `rpm`, `speed_of_advance_m_s` (or `J`), `diameter_mm`, `P_D`, `Ae_A0`, `Z`; `shaft_depth_m` for cavitation (`keller_K` 0.2, `vapour_pressure_Pa`) | `thrust_N`, `torque_Nm`, `power_W`, `eta0`, `KT`, `KQ`, `AeA0_min_keller`, `cavitation_margin` (>= 1) |
| `pressure_hull` | none | `shape` cylinder/sphere, `outer_diameter_mm`, `thickness_mm`, `length_mm` (cylinder: between frames/end caps), `depth_m` or `pressure_MPa`, `material` (name or `{E_MPa, nu, yield_MPa}`), `safety_factor`, `knockdown` (cylinder 0.75 default; sphere: none, must be given) | `collapse_pressure_MPa`, `mode` (yield/buckling), `safety_factor`, `collapse_depth_m`, `rated_depth_m`, `buckling_classical_MPa`, `buckling_lobes`, `lame_vm_inner_MPa` |
| `oring_gland` | none | `cs_mm`, `gland_depth_mm` (compression gap), `groove_width_mm`, `id_mm`, `seat_diameter_mm`, `service` static/face/dynamic | `squeeze_pct`, `squeeze_mm`, `gland_fill_pct`, `stretch_pct`, `cs_effective_mm` |

Examples:
```
simulate("hydrostatics", target="Hull", params={"mass_kg": 12.4, "cg_mm": [310, 0, 85],
         "limits": {"GM_T_mm": {"min": 20}, "reserve_buoyancy_pct": {"min": 50}}})
simulate("hydrostatics", target="ROV", params={"submerged": true, "mass_kg": 9.8,
         "cg_mm": [0, 0, -40], "limits": {"net_buoyancy_N": {"min": 0.5, "max": 3}, "BG_mm": {"min": 15}}})
simulate("thruster", params={"rpm": 3000, "speed_of_advance_m_s": 0.5, "diameter_mm": 90,
         "P_D": 1.0, "Ae_A0": 0.55, "Z": 4, "shaft_depth_m": 1, "limits": {"thrust_N": {"min": 30}}})
simulate("pressure_hull", params={"shape": "cylinder", "outer_diameter_mm": 110, "thickness_mm": 5,
         "length_mm": 300, "material": "aluminium_6061_t6", "depth_m": 100, "safety_factor": 2})
```

## Read the results honestly
- **Hydrostatics** is exact for the triangulated surface; `mesh_volume_error_pct` says how far
  the mesh is from the CAD volume (> 5 % is refused). Stability is **initial** (small angles),
  no free-surface effect of liquids on board, no large-angle GZ curve. Heel/trim are small-angle
  estimates from the CG-CB offset. A sinking hull says `SINKS` with the shortfall.
- **CG is the user's**: from the BOM masses (`bill_of_materials`) or a measurement, never guessed.
- **Added mass**: linear potential flow, constant panels (a few % error at ~3000 panels,
  converging from above). "infinite" = no free surface: damping is zero and the result does not
  depend on frequency (right for a vehicle several lengths deep). Viscous drag is NOT included:
  ROV damping needs drag coefficients (tests or CFD) on top.
- **Thruster**: Wageningen B open-water polynomials (Rn 2e6, no scale correction), uniform inflow,
  open propeller (no duct, no hull wake). Outside the series (Z 2..7, P/D 0.5..1.4, Ae/A0 range of
  each Z, J beyond zero thrust) -> UNKNOWN. A hobby thruster's real blades are not B-series: use it
  for sizing, then a bollard test. Cavitation = Keller blade-area criterion only (Burrill not
  implemented).
- **Pressure hull**: hand formulas: shell between rigid frames/end caps; frames, penetrations,
  end-cap joints, fatigue, creep of plastics and inelastic buckling are NOT checked. Spheres need
  a `knockdown` (imperfections cut the classical value by a large factor) or the verdict is
  UNKNOWN. Acrylic/PC hulls: use the maker's data and a large safety factor.
- **O-ring**: nominal dimensions; run the tolerance extremes too. Ranges are Parker ORD 5700's
  recommendations (static/face squeeze max 30 %, dynamic ~16 %, min 0.18 mm; fill 60..85 %;
  stretch <= 5 %). No swell or extrusion-gap check.
- Underwater rules (§10.2): very low voltage only, IP68 connectors, insulation monitor on any shore
  cable, **pressure-test above the maximum depth before any launch**. Never say "safe".

## Exactness (reference cases, run before first use)
Floating box (draft, KB, BM_T, BM_L, GM_T, reserve: exact), floating vertical cylinder through the
mesh (draft 0.5 %, BM 1 %), submerged sphere (Archimedes, BG), sphere added mass = ½ρV in infinite
fluid (3 %), floating hemisphere heave at infinite frequency = 0.5·ρ·(2/3)πa³ (3 %), Kuiper's
B4-55 example (KT 0.1823, KQ 0.03124, η0 0.6509), long tube -> Bresse, Zoelly sphere × knockdown,
Lamé thick cylinder, O-ring squeeze/fill/stretch (Parker Fig. 3-3 formula).
