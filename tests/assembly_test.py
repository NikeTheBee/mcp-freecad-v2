"""Validate multi-part assembly structure headless (missing-domain: assemblies).

Run with:  A:\FreeCAD\bin\freecadcmd.exe tests\assembly_test.py

Uses the native Assembly workbench (FreeCAD 1.1). Builds an assembly container with
two positioned parts (placement-based assembly). Joint/constraint *solving* is
GUI/solver-coupled and documented in skill-assembly; structure + placement works
headless. Prints ASSEMBLY_OK on success.
"""
import FreeCAD
from FreeCAD import Vector, Placement, Rotation
import Assembly  # noqa: F401  (registers the Assembly type)

doc = FreeCAD.newDocument("AssemblyTest")
asm = doc.addObject("Assembly::AssemblyObject", "Assembly")

base = doc.addObject("Part::Box", "Base")
base.Length, base.Width, base.Height = 60, 60, 10
lid = doc.addObject("Part::Box", "Lid")
lid.Length, lid.Width, lid.Height = 60, 60, 10
# stack the lid on top of the base (placement-based assembly)
lid.Placement = Placement(Vector(0, 0, 10), Rotation())

for part in (base, lid):
    asm.addObject(part)
doc.recompute()

children = [o.Name for o in asm.Group] if hasattr(asm, "Group") else [o.Name for o in asm.OutList]
print("ASSEMBLY_CHILDREN:", children)
assert "Base" in children and "Lid" in children, "parts not in assembly"
assert lid.Placement.Base.z == 10, "lid not positioned"
# combined bounding box spans both stacked parts (0..20 in Z)
import Part
# obj.Shape already bakes in the object Placement, so combine directly
comp = Part.makeCompound([base.Shape, lid.Shape])
print("STACK_HEIGHT_Z:", comp.BoundBox.ZLength)
assert abs(comp.BoundBox.ZLength - 20) < 1e-6, "stack height wrong"
print("ASSEMBLY_OK")
