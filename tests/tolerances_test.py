"""Validate functional fits / clearances (cahier §4.4: tolérances, ajustements, jeux).

Run with:  A:\FreeCAD\bin\freecadcmd.exe tests\tolerances_test.py

Provides a small fit helper (recommended diametral clearance/interference per fit
class) and proves a shaft mates into a hole with the intended functional gap.
For exact ISO 286 limits use deviation tables; this gives practical clearances.
Prints TOLERANCES_OK on success.
"""
import FreeCAD
import Part

# Recommended diametral allowance (mm) by fit class (practical, size <= ~50mm).
# Positive = clearance (hole larger), negative = interference (press fit).
FITS = {
    "loose":        (0.20, 0.50),   # sliding / loose running
    "clearance":    (0.05, 0.20),   # normal running fit
    "close":        (0.01, 0.06),   # location, easy assembly
    "transition":   (-0.02, 0.02),  # snug, may need light press
    "press":        (-0.06, -0.02),  # interference / press fit
}


def hole_for_shaft(shaft_d: float, fit: str = "clearance") -> float:
    """Return a hole diameter giving the mid clearance of `fit` for a shaft."""
    lo, hi = FITS[fit]
    return shaft_d + (lo + hi) / 2.0


def diametral_clearance(hole_d: float, shaft_d: float) -> float:
    return hole_d - shaft_d


doc = FreeCAD.newDocument("TolTest")
shaft_d = 10.0
hole_d = hole_for_shaft(shaft_d, "clearance")
gap = diametral_clearance(hole_d, shaft_d)
print(f"shaft Ø{shaft_d} -> hole Ø{hole_d:.3f}  (diametral clearance {gap:.3f} mm)")
assert 0.05 <= gap <= 0.20, "clearance out of class"

# Build a pin in a plate hole and confirm they don't interfere (clearance fit)
plate = doc.addObject("Part::Box", "Plate"); plate.Length, plate.Width, plate.Height = 30, 30, 5
hole = Part.makeCylinder(hole_d / 2, 5)
hole.translate(FreeCAD.Vector(15, 15, 0))
plate_holed = plate.Shape.cut(hole)
pin = Part.makeCylinder(shaft_d / 2, 5)
pin.translate(FreeCAD.Vector(15, 15, 0))
common = plate_holed.common(pin)
print(f"interference volume = {common.Volume:.4f} (must be ~0 for clearance fit)")
assert common.Volume < 1e-6, "pin interferes with hole - not a clearance fit"

# Press fit must show interference
press_hole = hole_for_shaft(shaft_d, "press")
assert diametral_clearance(press_hole, shaft_d) < 0, "press fit should be negative"
print("TOLERANCES_OK")
