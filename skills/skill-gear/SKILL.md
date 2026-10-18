# skill-gear

**Load when:** gears, gear trains, transmissions — involute spur gears for mechanical/train designs.

## What this is
A self-contained **involute spur-gear generator** in pure `Part` — no third-party workbench, no install.
Parametric on module, teeth, width, pressure angle. Produces a valid, exportable solid.

## Use
The reusable function is `make_spur_gear(doc, module, teeth, width, pressure_angle, name)` in
`install/gear_test.py`. Import it (add `install/` to `sys.path`) or inline the algorithm:
```python
gear, tip_d = make_spur_gear(doc, module=2.0, teeth=20, width=8.0, pressure_angle=20.0)
doc.recompute()
```

## How it works (for adapting)
- Pitch r = `m·z/2`; base r = `r·cos(α)`; tip r = `r + m`; root r = `r − 1.25·m`.
- Tooth outline traced at **strictly increasing angle** (mid-gap → trailing flank rising → leading
  flank falling) so the wire never self-intersects. Flank angle at radius r is
  `half_tooth − inv(r)` where `inv(r)=√((r/r_base)²−1) − atan(√(...))` and
  `half_tooth = π/(2z) + (tan α − α)`.
- `Part.makePolygon → Face → extrude(width)`, then `removeSplitter()`/`fix()` to heal discretisation
  micro-defects → a **valid** solid (the discretised involute otherwise trips OCCT validity).

## Gear trains
Mesh two gears: centre distance = `(m·z1 + m·z2)/2`; ratio = `z2/z1`. Position the second gear with a
`Placement` (see `skill-assembly`) on the +X axis of the first. The generator puts a TOOTH on +X, so
gear 2 faces gear 1 at 180°: it already shows a gap there when **z2 is odd** (no turn); when **z2 is
even**, turn it half a tooth (`180/z2` degrees). Turning it for an odd z2 puts tooth on tooth
(150 mm³ of overlap for 20/31). Turning gear 1 by θ turns gear 2 by `−θ·z1/z2`.
At the exact centre distance the pair meshes without overlap over a whole pitch (`gear_test.py`).
A real pair needs running clearance: add ~0.05·m to 0.1·m to the centre distance (printed gears
usually more, ~0.2–0.3 mm). Then `require(..., "no_interference", objects=[...])` must be OK.

## Verify
`verify.verdict` (valid solid), tip diameter ≈ `m·(z+2)`, width = extrude length.
Runnable example: `install/gear_test.py`.
