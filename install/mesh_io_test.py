"""The base's mesh / CAD file I/O and measurement handlers against exact answers,
called directly under freecadcmd (no FreeCAD instance, no socket).

Run with:  A:\\FreeCAD\\bin\\freecadcmd.exe install\\mesh_io_test.py
Each case once wrote or reported a wrong file or number (audit 2026-11-13).
MESH_IO_AICOPILOT=<dir> runs it against another copy of the AICopilot addon.
Files go to a fresh folder of the temp dir, removed at the end.
Sentinel: MESH_IO_OK
"""
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADDON = os.environ.get("MESH_IO_AICOPILOT") or str(REPO / "server" / "freecad-mcp" / "AICopilot")
assert os.path.isdir(os.path.join(ADDON, "handlers")), ADDON
sys.path.insert(0, ADDON)

import FreeCAD as App  # noqa: E402
import Mesh  # noqa: E402
import Part  # noqa: E402

V = App.Vector
FAILS = []
TMP = tempfile.mkdtemp(prefix="mesh_io_")


def check(what, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {what}" + (f"  [{detail}]" if detail else ""), flush=True)
    if not ok:
        FAILS.append(what)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


def fresh(name):
    for d in list(App.listDocuments()):
        App.closeDocument(d)
    doc = App.newDocument(name)
    App.setActiveDocument(doc.Name)
    return doc


def box(doc, name, size=10.0):
    b = doc.addObject("Part::Box", name)
    b.Length = b.Width = b.Height = size
    return b


def bbox_of(path):
    """(XMin, YMin, ZMin, XMax, volume) of what a file holds; None when empty."""
    if path.lower().endswith((".stl", ".obj", ".ply", ".3mf", ".off")):
        m = Mesh.Mesh(path)
        if not m.CountFacets:
            return None
        b = m.BoundBox
        return (b.XMin, b.YMin, b.ZMin, b.XMax, m.Volume)
    s = Part.read(path)
    if s.isNull() or not s.Vertexes:
        return None
    b = s.optimalBoundingBox(False, False)
    return (b.XMin, b.YMin, b.ZMin, b.XMax, s.Volume)


def exports_in_document_frame():
    from handlers.mesh_ops import MeshOpsHandler
    h = MeshOpsHandler()

    # a 10 mm box in an App::Part moved by +100 mm in X: the file must hold it
    # at X 100..110 (it was written at 0..10, the container's frame)
    doc = fresh("MeshIoMoved")
    asm = doc.addObject("App::Part", "Asm")
    b = box(doc, "B")
    asm.addObject(b)
    asm.Placement.Base = V(100, 0, 0)
    doc.recompute()
    for ext in (".stl", ".3mf", ".step", ".iges", ".brep"):
        f = os.path.join(TMP, "moved" + ext)
        r = h.export_file({"object_name": "B", "file_path": f})
        got = bbox_of(f) if os.path.exists(f) else None
        check(f"part in a moved App::Part exported where it is ({ext})",
              got is not None and close(got[0], 100, 1e-3) and close(got[3], 110, 1e-3)
              and close(got[4], 1000, 1e-2), f"{r} -> {got}")

    # a mesh feature in the same moved container
    mf = doc.addObject("Mesh::Feature", "MF")
    mf.Mesh = Mesh.Mesh(os.path.join(TMP, "moved.stl"))   # already at 100..110
    mf.Placement.Base = V(0, 0, 50)                        # its own placement too
    asm.addObject(mf)
    doc.recompute()
    for ext in (".stl", ".obj"):
        f = os.path.join(TMP, "movedmesh" + ext)
        r = h.export_mesh({"object_name": "MF", "file_path": f})
        got = bbox_of(f)
        check(f"mesh in a moved App::Part exported where it is ({ext})",
              got is not None and close(got[0], 200, 1e-3) and close(got[2], 50, 1e-3),
              f"{r} -> {got}")

    # a PartDesign feature inside a Body moved by +200 mm in Z
    body = doc.addObject("PartDesign::Body", "Body")
    pb = doc.addObject("PartDesign::AdditiveBox", "PB")
    body.addObject(pb)
    pb.Length = pb.Width = pb.Height = 5
    body.Placement.Base = V(0, 0, 200)
    doc.recompute()
    f = os.path.join(TMP, "pb.step")
    r = h.export_file({"object_name": "PB", "file_path": f})
    got = bbox_of(f)
    check("PartDesign feature in a moved Body exported where it is (STEP)",
          got is not None and close(got[2], 200, 1e-3) and close(got[4], 125, 1e-3),
          f"{r} -> {got}")
    f = os.path.join(TMP, "body.step")
    r = h.export_file({"object_name": "Body", "file_path": f})
    got = bbox_of(f)
    check("Body exported where it is (STEP)",
          got is not None and close(got[2], 200, 1e-3), f"{r} -> {got}")

    # App::Part and App::Link to STEP: an empty file was "Exported"
    doc = fresh("MeshIoKinds")
    asm = doc.addObject("App::Part", "Asm")
    b1, b2 = box(doc, "B1"), box(doc, "B2")
    b2.Placement.Base = V(20, 0, 0)
    asm.addObject(b1)
    asm.addObject(b2)
    asm.Placement.Base = V(0, 100, 0)
    lk = doc.addObject("App::Link", "L")
    lk.LinkedObject = b1
    lk.Placement.Base = V(50, 0, 0)
    doc.recompute()
    for name, ext, y0, x0, vol in (("Asm", ".step", 100, 0, 2000), ("Asm", ".brep", 100, 0, 2000),
                                   ("L", ".step", 0, 50, 1000), ("L", ".iges", 0, 50, 1000)):
        f = os.path.join(TMP, f"{name}{ext}")
        r = h.export_file({"object_name": name, "file_path": f})
        got = bbox_of(f) if os.path.exists(f) else None
        check(f"{doc.getObject(name).TypeId} exported with its geometry ({ext})",
              got is not None and close(got[1], y0, 1e-3) and close(got[0], x0, 1e-3)
              and close(got[4], vol, 1e-2), f"{r} -> {got}")
    empty = doc.addObject("App::Part", "Empty")
    doc.recompute()
    f = os.path.join(TMP, "empty.step")
    r = h.export_file({"object_name": "Empty", "file_path": f})
    check("an empty App::Part is refused, not exported",
          r.startswith("Error") and not os.path.exists(f), r)

    # the plain case is unchanged: a top-level box keeps its own placement
    b3 = box(doc, "B3")
    b3.Placement.Base = V(5, 6, 7)
    doc.recompute()
    for ext in (".step", ".stl"):
        f = os.path.join(TMP, "plain" + ext)
        r = h.export_file({"object_name": "B3", "file_path": f})
        got = bbox_of(f)
        check(f"top-level part exported where it is ({ext})",
              got is not None and close(got[0], 5, 1e-3) and close(got[2], 7, 1e-3), f"{r} -> {got}")
    check("no temporary object left behind",
          not [o.Name for o in doc.Objects if o.Name.startswith("_export_temp")])


def made_from_a_mesh_beside_it():
    import MeshPart
    from handlers.base import BaseHandler
    from handlers.mesh_ops import MeshOpsHandler
    h = MeshOpsHandler()

    # a sphere mesh in an App::Part moved by +100 mm: its solid and its
    # simplified copy were added at the root, shown 100 mm away from it
    doc = fresh("MeshIoBeside")
    asm = doc.addObject("App::Part", "Asm")
    mf = doc.addObject("Mesh::Feature", "MF")
    mf.Mesh = MeshPart.meshFromShape(Shape=Part.makeSphere(10), LinearDeflection=0.05)
    asm.addObject(mf)
    asm.Placement.Base = V(100, 0, 0)
    doc.recompute()
    r = h.mesh_to_solid({"object_name": "MF"})
    so = doc.getObject("MF_Solid")
    x = BaseHandler._world_shape(so).BoundBox.Center.x if so else None
    check("mesh_to_solid: the solid is shown where its mesh is",
          x is not None and close(x, 100, 0.05), f"{r} -> centre x {x}")
    r = h.simplify_mesh({"object_name": "MF", "reduction": 0.5})
    sm = doc.getObject("MF_Simplified")
    try:
        pl = sm.getParentGeoFeatureGroup()
        pg = pl.getGlobalPlacement() if pl else App.Placement()
        x = pg.multVec(sm.Mesh.BoundBox.Center).x
    except Exception as e:
        x = f"<{e}>"
    check("simplify_mesh: the copy is shown where its source is",
          isinstance(x, float) and close(x, 100, 0.05), f"centre x {x}")

    # an inside-out mesh read "Health: OK"
    doc = fresh("MeshIoInfo")
    m = MeshPart.meshFromShape(Shape=Part.makeBox(10, 20, 30), LinearDeflection=0.1)
    m.flipNormals()
    inv = doc.addObject("Mesh::Feature", "Inv")
    inv.Mesh = m
    good = doc.addObject("Mesh::Feature", "Good")
    good.Mesh = MeshPart.meshFromShape(Shape=Part.makeBox(10, 20, 30), LinearDeflection=0.1)
    doc.recompute()
    r = h.get_mesh_info({"object_name": "Inv"})
    check("get_mesh_info: an inside-out mesh is not 'Health: OK'",
          "Health: OK" not in r and "inside-out" in r, r.replace("\n", " | "))
    r = h.get_mesh_info({"object_name": "Good"})
    check("get_mesh_info: an outward closed mesh is OK, with its exact numbers",
          "Health: OK" in r and "Volume: 6000.00" in r and "Area: 2200.00" in r,
          r.replace("\n", " | "))


def faces_and_alignment():
    import math
    from handlers.spatial_ops import SpatialOpsHandler
    sh = SpatialOpsHandler()
    doc = fresh("MeshIoFaces")
    pts = [V(10 * math.cos(math.radians(60 * i)), 10 * math.sin(math.radians(60 * i)), 0)
           for i in range(7)]
    hexo = doc.addObject("Part::Feature", "Hex")
    hexo.Shape = Part.Face(Part.makePolygon(pts)).extrude(V(0, 0, 10))
    b = box(doc, "A")
    doc.recompute()
    # Face1 / Face3 of the prism: normals (0.866, 0.5) and (-0.866, 0.5) -> 120°
    r = sh.face_relationship({"object1": "Hex", "object2": "Hex", "face1": "Face1", "face2": "Face3"})
    check("face_relationship: normals 120° apart read 120°",
          "Angle between normals: 120.00°" in r, r.replace("\n", " | "))
    r = sh.face_relationship({"object1": "A", "object2": "A", "face1": "Face1", "face2": "Face2"})
    check("face_relationship: opposite faces of a box are 180°, parallel, 10 mm apart",
          "Angle between normals: 180.00°" in r and "Parallel: True" in r
          and "Plane distance: 10.0000 mm" in r, r.replace("\n", " | "))

    comp = doc.addObject("Part::Feature", "Comp")
    comp.Shape = Part.makeCompound([Part.makeBox(10, 10, 10), Part.makeBox(10, 10, 10, V(20, 0, 0))])
    doc.recompute()
    r = sh.alignment_check({"object1": "A", "object2": "Comp", "axis": "Z"})
    check("alignment_check works on a compound (centre 15, 5, 5)",
          "Comp center: (15.00, 5.00, 5.00)" in r and "Lateral offset (XY): 10.0000" in r,
          r.replace("\n", " | "))


for case in (exports_in_document_frame, made_from_a_mesh_beside_it, faces_and_alignment):
    try:
        case()
    except Exception:
        traceback.print_exc()
        FAILS.append(f"{case.__name__}: exception")
shutil.rmtree(TMP, ignore_errors=True)

if FAILS:
    print(f"MESH_IO_FAILED ({len(FAILS)}): {FAILS}", flush=True)
else:
    print("MESH_IO_OK", flush=True)
