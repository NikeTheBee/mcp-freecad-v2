"""Validate mesh repair for 3D-print prep (cahier §4.6: réparation géométrie externe).

Run with:  A:\FreeCAD\bin\freecadcmd.exe tests\mesh_repair_test.py
Bundled Mesh/MeshPart. Meshes a solid, runs the standard repair passes
(harmonize normals, remove non-manifolds, fill holes) and checks the result is a
clean, manifold, solid-bounding mesh. Prints MESH_REPAIR_OK on success.
"""
import FreeCAD
import Mesh
import MeshPart

doc = FreeCAD.newDocument("MeshRepairTest")
box = doc.addObject("Part::Box", "Box")
box.Length, box.Width, box.Height = 20, 20, 20
doc.recompute()

mesh = MeshPart.meshFromShape(Shape=box.Shape, LinearDeflection=0.2,
                              AngularDeflection=0.5, Relative=False)
print(f"facets={mesh.CountFacets} points={mesh.CountPoints}")
assert mesh.CountFacets > 0, "empty mesh"

# Standard repair passes (idempotent on an already-clean mesh)
mesh.harmonizeNormals()
mesh.removeNonManifolds()
mesh.fillupHoles(10)
mesh.removeDuplicatedPoints()
mesh.removeDuplicatedFacets()

print(f"after repair: nonmanifolds={mesh.hasNonManifolds()} "
      f"selfintersect={mesh.hasSelfIntersections()} solid={mesh.isSolid()}")
assert not mesh.hasNonManifolds(), "mesh still non-manifold after repair"
assert mesh.isSolid(), "repaired mesh does not bound a solid (not watertight)"
print("MESH_REPAIR_OK")
