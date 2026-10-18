"""Validate a parametric involute spur-gear generator (missing-domain: train/gears).

Run with:  A:\FreeCAD\bin\freecadcmd.exe install\gear_test.py

Pure FreeCAD Part (no third-party workbench). Generates a true involute spur gear
from module/teeth/pressure-angle, extrudes to a solid, and checks tip diameter and
validity. Prints GEAR_OK on success. The generator (make_spur_gear) is documented
in skill-gear for reuse.
"""
import math

import FreeCAD
import Part
from FreeCAD import Vector


def _involute(rb, r):
    """Point on the involute of base circle rb at radius r (>= rb)."""
    r = max(r, rb)
    a = math.sqrt((r / rb) ** 2 - 1.0)      # tan(pressure angle at r)
    inv = a - math.atan(a)                  # involute roll angle
    return r, inv


def make_spur_gear(doc, module=2.0, teeth=20, width=10.0,
                   pressure_angle=20.0, name="Gear"):
    m, z = float(module), int(teeth)
    alpha = math.radians(pressure_angle)
    r_pitch = m * z / 2.0
    r_base = r_pitch * math.cos(alpha)
    r_add = r_pitch + m                      # addendum (tip)
    r_root = max(r_pitch - 1.25 * m, 0.2)    # dedendum (root)

    # half tooth angle at the base circle: half of circular tooth + involute of alpha
    half_tooth = math.pi / (2 * z) + (math.tan(alpha) - alpha)
    pitch_ang = 2 * math.pi / z
    r_lo = max(r_base, r_root)
    steps = 10

    def P(r, th):
        return Vector(r * math.cos(th), r * math.sin(th), 0)

    # Trace strictly-increasing angle around the gear: per tooth -> gap point,
    # trailing flank (rising), leading flank (falling). Monotonic => no self-cross.
    # Below the base circle the flank drops RADIALLY to the root circle: a straight
    # line from the mid-gap root point to the flank's foot filled the corners of the
    # gap, where the mating tip passes (two meshed gears overlapped by 0.1-0.2 mm3).
    _, inv_lo = _involute(r_base, r_lo)
    foot = half_tooth - inv_lo
    pts = []
    for i in range(z):
        C = i * pitch_ang
        pts.append(P(r_root, C - pitch_ang / 2))                # mid-gap before tooth
        if r_lo > r_root + 1e-9:
            pts.append(P(r_root, C - foot))                     # root, under the flank
        for s in range(steps + 1):                              # trailing flank, base->tip
            r = r_lo + (r_add - r_lo) * s / steps
            _, inv = _involute(r_base, r)
            pts.append(P(r, C - (half_tooth - inv)))
        for s in range(steps + 1):                              # leading flank, tip->base
            r = r_add - (r_add - r_lo) * s / steps
            _, inv = _involute(r_base, r)
            pts.append(P(r, C + (half_tooth - inv)))
        if r_lo > r_root + 1e-9:
            pts.append(P(r_root, C + foot))
    pts.append(pts[0])
    wire = Part.makePolygon(pts)
    face = Part.Face(wire)
    solid = face.extrude(Vector(0, 0, width))
    # Heal micro-defects from the discretised involute outline so the solid is
    # valid (exportable / boolean-safe).
    solid = solid.removeSplitter()
    if not solid.isValid():
        solid.fix(1e-6, 1e-6, 1e-6)

    obj = doc.addObject("Part::Feature", name)
    obj.Shape = solid
    return obj, 2 * r_add


doc = FreeCAD.newDocument("GearTest")
gear, tip_d = make_spur_gear(doc, module=2.0, teeth=20, width=8.0)
doc.recompute()

shp = gear.Shape
bb = shp.BoundBox
print(f"VALID={shp.isValid()} SOLIDS={len(shp.Solids)} "
      f"tip_d={tip_d:.2f} bbox_d={max(bb.XLength, bb.YLength):.2f} Z={bb.ZLength:.2f}")
assert shp.isValid() and len(shp.Solids) == 1, "gear is not a valid solid"
assert abs(max(bb.XLength, bb.YLength) - tip_d) < 0.6, "tip diameter mismatch"
assert abs(bb.ZLength - 8.0) < 1e-6, "wrong gear width"
# the involute itself: tip radius exactly m*z/2 + m, and on the pitch circle the teeth
# fill half of it (tooth thickness = half the circular pitch), whatever m and z
for m, z in ((2.0, 20), (1.0, 12), (3.0, 40)):
    g, tip_d = make_spur_gear(doc, module=m, teeth=z, width=5.0, name=f"G{z}")
    doc.recompute()
    radial = max(math.hypot(v.Point.x, v.Point.y) for v in g.Shape.Vertexes)
    assert abs(radial - tip_d / 2) < 1e-9, (m, z, radial)
    face = [f for f in g.Shape.Faces if abs(f.CenterOfMass.z) < 1e-9][0]
    r_pitch = m * z / 2
    on_pitch = sum(e.Length for e in face.common(Part.makeCircle(r_pitch)).Edges)
    assert abs(on_pitch / (2 * math.pi * r_pitch) - 0.5) < 0.002, (m, z, on_pitch)
print("tip radius exact, teeth fill half the pitch circle (m 1-3, z 12-40): OK")

# a meshed pair at the exact centre distance never overlaps over a whole tooth pitch.
# Gear 1 has a tooth on +X; gear 2, placed on +X, faces it at 180 deg, where it has a
# GAP by itself when z2 is odd: turn it by pi/z2 only when z2 is even ("always turn
# half a tooth" put tooth on tooth for odd z2: 150 mm3 of overlap for 20/31)
for z1, z2 in ((20, 31), (20, 30)):
    a = 2.0 * (z1 + z2) / 2
    g1, _ = make_spur_gear(doc, 2.0, z1, 5.0, name=f"P{z1}_{z2}")
    g2, _ = make_spur_gear(doc, 2.0, z2, 5.0, name=f"W{z1}_{z2}")
    turn = 0.0 if z2 % 2 else 180.0 / z2
    z_axis = FreeCAD.Vector(0, 0, 1)
    for k in range(4):
        th = 360.0 / z1 * k / 4
        g1.Placement = FreeCAD.Placement(Vector(), FreeCAD.Rotation(z_axis, th))
        g2.Placement = FreeCAD.Placement(Vector(a, 0, 0), FreeCAD.Rotation(z_axis, turn - th * z1 / z2))
        doc.recompute()
        common = g1.Shape.common(g2.Shape).Volume
        assert common < 1e-6, (z1, z2, th, common)
print("meshed pairs 20/31 and 20/30: no overlap over a pitch (half-tooth turn only for even z2): OK")
print("GEAR_OK")
