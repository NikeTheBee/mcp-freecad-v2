# skill-structure

**Load when:** carbon or glass composite, lay-up, stacking sequence, ply, laminate, ABD, Tsai-Wu,
"quelle épaisseur de carbone", warping after cure; or a crack: damage tolerance, crack growth,
da/dN, Paris law, inspection interval, "elle est fissurée, combien de cycles reste-t-il".

## Where it sits
| question | tool |
|---|---|
"will it hold, once?" | `fem_static` (CalculiX) |
"how many cycles until a crack **starts**?" | `fatigue` (rainflow + Miner, S-N) |
**"there IS a crack: how long until it is critical?"** | **`structure crack_growth`** |
"what does my carbon lay-up actually do?" | **`structure laminate`** |
buckling of a shell or a hull | `marine pressure_hull`, `fem_buckling` |

## Use the tool
`freecad-layers` → **`simulate(kind, target, params)`**, adapter `structure` (env `sim`).
No geometry. Stresses in MPa; laminate lengths in mm; crack sizes in **m** so that K comes out in
MPa·√m, the unit every published da/dN table uses.

### `laminate`
`stack` (a list of angles) + `material` + `ply_thickness_mm`, with `symmetric: true` to mirror
it; or `plies` written out one by one. `material`: `E1_MPa`, `E2_MPa`, `nu12`, `G12_MPa`, and for
margins `Xt_MPa`, `Xc_MPa`, `Yt_MPa`, `Yc_MPa`, `S_MPa`. Optional `load`:
`Nx_N_mm`, `Ny_N_mm`, `Nxy_N_mm` (force per unit width) and `Mx_N`, `My_N`, `Mxy_N`.

Answers: `A`/`B`/`D`, `Ex_MPa`, `Ey_MPa`, `Gxy_MPa`, `nu_xy`, `thickness_mm`,
`areal_mass_kg_m2` (give `density_kg_m3`), and with a load: the stress in **every** ply in its own
axes plus `max_stress_index` and `tsai_wu_index`, with their margins.

Two flags worth reading before anything else:
- **`symmetric: 0`** → B ≠ 0: the plate will **warp when it cures** and bend when you pull it.
  Almost always a mistake unless you wanted it.
- **`balanced: 0`** → A16/A26 ≠ 0: in-plane shear couples to stretching, so a tensile load twists
  the part. Pair every +θ with a −θ.

### `crack_growth`
`delta_sigma_MPa` (the stress RANGE of one cycle), `C`, `m`, `Kc_MPa_sqrt_m`, `a_initial_m`, and
optionally `R`, `a_final_m`, `law` (`paris` or `forman`), `delta_K_threshold_MPa_sqrt_m`,
`beta` (`centre_crack_infinite_plate`, `centre_crack_finite_width` + `width_m`,
`edge_crack_single`, `surface_crack_semicircular`), `inspection_life_fraction`, and
**`thickness_m`** — give it whenever there is a wall.

**Leak before break.** With `thickness_m`, the calculation stops when the crack reaches the far
face: it leaks. Without it, a surface crack in a 3 mm wall is happily reported as growing to a
"critical" 2.4 m, which is arithmetic, not engineering. `leak_before_break: 1` means it leaks
first (usually the benign outcome, and the one inspection relies on); `0` with a wall given means
K reaches Kc inside the wall — it breaks.

Answers: `cycles_to_failure`, `critical_crack_m`, `delta_K_initial/final`, `Kmax_final`,
`inspection_crack_m` (the size an inspection has to catch to leave that fraction of life), and
`grows: 0` when ΔK is at or below the threshold — in which case there is **no** life to report
and the answer says so instead of inventing a number.

## What to say when you report
- **A negative Tsai-Wu index is normal** and means comfortably safe (it happens in
  compression). It is reported as it is, not clamped, so a criterion on it still gets judged.
- **First-ply failure.** An index above 1 means one ply has failed, not that the laminate broke.
  For a real allowable, carry a margin: a lay-up's measured strengths scatter by 10 % and more.
- **`C` and `m` are the whole answer.** They belong to that material, that orientation and that
  environment. A generic pair is the largest error in the result, larger than anything the
  integration does. Say which pair was used.
- **The geometry factor too.** The answer prints the β used and its validity range; quote it.
- An inspection interval is **half** of the computed life at most, and that is a convention, not
  a calculation.
- Feed `Ex_MPa` and `nu_xy` into `fem_static` only where the laminate works in its plane.

## Validated on
Closed forms, not opinions: a single isotropic ply reproduces its own A, D, Ex, Ey, ν and G
exactly (and B = 0); a symmetric balanced 8-ply stack gives B = 0 and A16 = A26 = 0 exactly,
while a single 45° ply is correctly reported as not balanced; a 0° ply pulled to exactly its
fibre strength gives **exactly 1** in both criteria; and the Paris integration matches its
analytic antiderivative, including when it runs all the way to the critical crack it found
(Kmax = Kc there) when the threshold stops it dead, and when a wall is reached first (leak before break, the life being the closed-form integral up to the wall). See `REFERENCES` in
`server/adapters/structure.py`.

## Limits
CLT is thin-plate: in-plane and bending only, no transverse shear (wrong for a thick short
laminate), no interlaminar stress, no delamination, no free-edge effect, no moisture or
temperature ageing. Crack growth is constant-amplitude, long-crack, linear elastic: no overload
retardation, no short-crack regime, no corrosion, no mixed mode.
