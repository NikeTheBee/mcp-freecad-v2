# Mesh Operations Handler for FreeCAD MCP
#
# Provides mesh import/export, mesh-to-solid conversion, validation,
# simplification, and generic CAD file I/O (STL, OBJ, STEP, IGES, BREP).

import contextlib
import os
import time
from typing import Any

import FreeCAD

from .base import NO_ACTIVE_DOCUMENT_ERROR, BaseHandler


_MAX_HOLE_EDGES = 100   # auto_repair fills holes up to this many rim edges


def _signed_volume(mesh) -> float:
    """Signed volume of a mesh: < 0 when its normals point inward.

    MCP FREECAD v2: Mesh.Volume is the ABSOLUTE value (an inside-out 10 mm cube
    reads +1000, its signed volume is -1000), so it cannot tell inside-out.
    """
    points, facets = mesh.Topology
    try:
        import numpy as np
        p = np.array([(q[0], q[1], q[2]) for q in points], dtype=float).reshape(-1, 3)
        f = np.array(facets, dtype=int).reshape(-1, 3)
        a, b, c = p[f[:, 0]], p[f[:, 1]], p[f[:, 2]]
        return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
    except ImportError:
        v = 0.0
        for i, j, k in facets:
            a, b, c = points[i], points[j], points[k]
            v += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                  + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6.0
        return v


def _orientation_issue(mesh) -> str:
    """'' when the facets are oriented consistently and outward."""
    if mesh.hasNonUniformOrientedFacets():
        return (f"{mesh.countNonUniformOrientedFacets()} facet(s) oriented against their "
                "neighbours (flipped normals)")
    if mesh.isSolid() and _signed_volume(mesh) < 0:
        return "inside-out (all normals point inward)"
    return ""


def _container_placement(obj):
    """Global placement of the App::Part / Body holding obj; None when there is
    none, it is the identity, or obj is not a real document object."""
    try:
        if not isinstance(obj, FreeCAD.DocumentObject):
            return None
        parent = obj.getParentGeoFeatureGroup()
        if parent is None:
            return None
        pg = parent.getGlobalPlacement()
        return None if pg.isIdentity() else pg
    except Exception:
        return None


def _place_beside(new_obj, src) -> None:
    """Show new_obj (made from src's Mesh, in src's container frame) where src is:
    in the same App::Part, else with the container's placement applied."""
    try:
        if not isinstance(src, FreeCAD.DocumentObject):
            return
        parent = src.getParentGeoFeatureGroup()
        if parent is None:
            return
        if parent.isDerivedFrom("App::Part"):
            parent.addObject(new_obj)
            return
        new_obj.Placement = parent.getGlobalPlacement().multiply(new_obj.Placement)
    except Exception:
        pass


def _is_part_feature(obj) -> bool:
    try:
        return bool(obj.isDerivedFrom("Part::Feature"))
    except Exception:
        return True


def _empty_shape(shape) -> bool:
    """True for a null shape or one with no vertex at all (mock-tolerant)."""
    try:
        if shape.isNull() is True:
            return True
        verts = shape.Vertexes
        return isinstance(verts, (list, tuple)) and not verts
    except Exception:
        return False


class MeshOpsHandler(BaseHandler):
    """Handler for mesh and generic file I/O operations.

    Bridges the gap between mesh-based workflows (STL terrain files, 3D scans)
    and FreeCAD's Part/CAM pipeline. Supports:
    - Mesh import/export (STL, OBJ, PLY, OFF, AMF, 3MF)
    - CAD import/export (STEP, IGES, BREP)
    - Mesh-to-solid conversion for CAM compatibility
    - Mesh validation and repair
    - Mesh simplification (decimation)
    """

    _ALLOWED_OPERATIONS = frozenset({
        "import_mesh", "export_mesh", "mesh_to_solid", "get_mesh_info",
        "import_file", "export_file", "validate_mesh", "simplify_mesh",
    })

    MESH_FORMATS = {'.stl', '.obj', '.ply', '.off', '.amf', '.3mf'}
    CAD_FORMATS = {'.step', '.stp', '.iges', '.igs', '.brep', '.brp'}

    def import_mesh(self, args: dict[str, Any]) -> str:
        """Import a mesh file (STL, OBJ, PLY, OFF, AMF, 3MF).

        Args:
            file_path: Path to mesh file (required)
            name: Object name (optional, derived from filename)

        Returns:
            Object name, vertex/face counts, bounding box
        """
        start_time = time.time()
        try:
            import Mesh

            file_path = args.get('file_path', '')
            if not file_path:
                return self.log_and_return("import_mesh", args,
                    error=Exception("file_path parameter required"),
                    duration=time.time() - start_time)

            path_err = self._validate_file_path(file_path)
            if path_err:
                return self.log_and_return("import_mesh", args,
                    error=Exception(path_err), duration=time.time() - start_time)

            if not os.path.exists(file_path):
                return self.log_and_return("import_mesh", args,
                    error=Exception(f"File not found: {file_path}"),
                    duration=time.time() - start_time)

            ext = os.path.splitext(file_path)[1].lower()
            if ext not in self.MESH_FORMATS:
                return self.log_and_return("import_mesh", args,
                    error=Exception(f"Unsupported mesh format '{ext}'. Supported: {', '.join(sorted(self.MESH_FORMATS))}"),
                    duration=time.time() - start_time)

            doc = self.get_document()
            if not doc:
                return self.log_and_return("import_mesh", args,
                    error=Exception(NO_ACTIVE_DOCUMENT_ERROR),
                    duration=time.time() - start_time)

            # Derive name from filename if not provided
            name = args.get('name', '')
            if not name:
                name = os.path.splitext(os.path.basename(file_path))[0]
                # Sanitize for FreeCAD naming (alphanumeric + underscore)
                name = ''.join(c if c.isalnum() or c == '_' else '_' for c in name)
                if name and name[0].isdigit():
                    name = 'Mesh_' + name

            mesh_data = Mesh.Mesh(file_path)
            mesh_obj = doc.addObject("Mesh::Feature", name)
            mesh_obj.Mesh = mesh_data
            doc.recompute()

            bb = mesh_data.BoundBox
            result = (f"Imported mesh '{mesh_obj.Name}' from {os.path.basename(file_path)}\n"
                      f"  Points: {mesh_data.CountPoints}\n"
                      f"  Facets: {mesh_data.CountFacets}\n"
                      f"  Bounding box: {bb.XLength:.2f} x {bb.YLength:.2f} x {bb.ZLength:.2f} mm")

            return self.log_and_return("import_mesh", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("import_mesh", args, error=e, duration=time.time() - start_time)

    def export_mesh(self, args: dict[str, Any]) -> str:
        """Export an object to mesh format (STL, OBJ, PLY, OFF, AMF, 3MF).

        Handles both Mesh::Feature and Part::Feature objects. Part objects
        are tessellated before export.

        Args:
            object_name: Object to export (required)
            file_path: Output file path (required)
            linear_deflection: Tessellation accuracy for Part objects (default 0.1)
            angular_deflection: Angular tessellation accuracy for Part objects

        Returns:
            Success message with file path and size
        """
        start_time = time.time()
        try:
            import Mesh

            object_name = args.get('object_name', '')
            file_path = args.get('file_path', '')

            if not object_name:
                return self.log_and_return("export_mesh", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)
            if not file_path:
                return self.log_and_return("export_mesh", args,
                    error=Exception("file_path parameter required"),
                    duration=time.time() - start_time)

            path_err = self._validate_file_path(file_path)
            if path_err:
                return self.log_and_return("export_mesh", args,
                    error=Exception(path_err), duration=time.time() - start_time)

            ext = os.path.splitext(file_path)[1].lower()
            if ext not in self.MESH_FORMATS:
                return self.log_and_return("export_mesh", args,
                    error=Exception(f"Unsupported mesh format '{ext}'. Supported: {', '.join(sorted(self.MESH_FORMATS))}"),
                    duration=time.time() - start_time)

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return self.log_and_return("export_mesh", args,
                    error=Exception(err), duration=time.time() - start_time)

            # Ensure output directory exists
            out_dir = os.path.dirname(file_path)
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir, exist_ok=True)

            # MCP FREECAD v2: obj.Shape / obj.Mesh are in the frame of the App::Part
            # or Body holding the object: a 10 mm box in an App::Part moved by
            # 100 mm was written at X 0..10 instead of 100..110 (STL, and STEP/IGES/
            # BREP below). The file now holds the part where the document shows it.
            container = _container_placement(obj)
            if hasattr(obj, 'Mesh'):
                if container is None:
                    Mesh.export([obj], file_path)
                else:
                    placed = obj.Mesh.copy()
                    placed.Placement = container.multiply(placed.Placement)
                    placed.write(file_path)
            elif hasattr(obj, 'Shape'):
                # Part object — tessellate first
                import MeshPart
                linear = args.get('linear_deflection', 0.1)
                angular = args.get('angular_deflection')

                mesh_params = {"Shape": self._world_shape(obj), "LinearDeflection": linear}
                if angular is not None:
                    mesh_params["AngularDeflection"] = angular

                mesh_data = MeshPart.meshFromShape(**mesh_params)
                # Create temporary mesh object for export
                temp_obj = doc.addObject("Mesh::Feature", "_export_temp")
                temp_obj.Mesh = mesh_data
                try:
                    Mesh.export([temp_obj], file_path)
                finally:
                    # Previously only removed on the success path -- an
                    # exception during export (bad path, tessellation edge
                    # case) left this permanently orphaned in the document,
                    # surviving future saves. Clean it up regardless of
                    # outcome, matching assembly_ops.py's consistent
                    # temp-object cleanup pattern.
                    doc.removeObject(temp_obj.Name)
            else:
                return self.log_and_return("export_mesh", args,
                    error=Exception(f"Object '{object_name}' has no Mesh or Shape to export"),
                    duration=time.time() - start_time)

            file_size = os.path.getsize(file_path)
            size_str = f"{file_size / 1024:.1f} KB" if file_size < 1024 * 1024 else f"{file_size / (1024 * 1024):.1f} MB"
            result = f"Exported '{object_name}' to {file_path} ({size_str})"

            return self.log_and_return("export_mesh", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("export_mesh", args, error=e, duration=time.time() - start_time)

    def mesh_to_solid(self, args: dict[str, Any]) -> str:
        """Convert a mesh object to a Part solid for CAM compatibility.

        This is the critical bridge between mesh-based workflows (terrain STL files,
        3D scans) and FreeCAD's Part/CAM pipeline.

        Args:
            object_name: Mesh object to convert (required)
            tolerance: Sewing tolerance (default 0.1)
            name: Name for the resulting solid (optional)

        Returns:
            Solid name, volume, bounding box
        """
        start_time = time.time()
        try:
            import Part

            object_name = args.get('object_name', '')
            if not object_name:
                return self.log_and_return("mesh_to_solid", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return self.log_and_return("mesh_to_solid", args,
                    error=Exception(err), duration=time.time() - start_time)

            if not hasattr(obj, 'Mesh'):
                return self.log_and_return("mesh_to_solid", args,
                    error=Exception(f"Object '{object_name}' is not a mesh object"),
                    duration=time.time() - start_time)

            tolerance = args.get('tolerance', 0.1)
            name = args.get('name', f"{object_name}_Solid")

            mesh = obj.Mesh
            topology = mesh.Topology  # ([points], [facets])

            # Build shape from mesh triangles
            shape = Part.Shape()
            shape.makeShapeFromMesh(topology, tolerance)

            # Try to make a solid
            solid = None
            warning = ""
            # Not critical if this fails
            with contextlib.suppress(Exception):
                shape = shape.removeSplitter()

            def _closed_solid(s):
                # MCP FREECAD v2: makeSolid does not refuse an open mesh -- one
                # missing triangle gave an INVALID "solid" of 5478 mm3 for a
                # 6000 mm3 box, replied "Converted mesh to solid".
                try:
                    return (bool(s.isValid()) and bool(s.Solids)
                            and all(sh.isClosed() for sh in s.Shells))
                except Exception:
                    return True  # not a real shape (unit-test stubs): not judged

            closed = True
            try:
                solid = Part.makeSolid(shape)
                if not _closed_solid(solid):
                    raise ValueError("open or invalid solid")
            except Exception:
                # Fallback: try sewing first, then solid
                try:
                    shell = shape.Shells[0] if shape.Shells else shape
                    shell.sewShape()
                    solid = Part.makeSolid(shell)
                    if not _closed_solid(solid):
                        raise ValueError("open or invalid solid")
                    warning = " (required sewing step)"
                except Exception:
                    # Last resort: keep as shell
                    solid = shape
                    closed = False
                    warning = (" (WARNING: the mesh is not closed: kept as a shell, NOT a "
                               "solid -- it has no volume; repair it first with "
                               "validate_mesh auto_repair=true)")

            solid_obj = doc.addObject("Part::Feature", name)
            solid_obj.Shape = solid
            # MCP FREECAD v2: the solid of a mesh in an App::Part moved by 100 mm
            # was added at the document root: shown 100 mm away from its mesh.
            _place_beside(solid_obj, obj)
            doc.recompute()

            bb = solid.BoundBox
            vol_str = "\n  Volume: none (not a closed solid)" if not closed else ""
            try:
                if closed:
                    vol = solid.Volume
                    vol_str = f"\n  Volume: {vol:.2f} mm^3"
            except Exception:
                pass

            result = (f"Converted mesh '{object_name}' to solid '{solid_obj.Name}'{warning}\n"
                      f"  Bounding box: {bb.XLength:.2f} x {bb.YLength:.2f} x {bb.ZLength:.2f} mm{vol_str}")

            return self.log_and_return("mesh_to_solid", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("mesh_to_solid", args, error=e, duration=time.time() - start_time)

    def get_mesh_info(self, args: dict[str, Any]) -> str:
        """Get detailed information about a mesh object.

        Args:
            object_name: Mesh object to inspect (required)

        Returns:
            Mesh statistics: vertex/face counts, area, volume, topology health
        """
        start_time = time.time()
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return self.log_and_return("get_mesh_info", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return self.log_and_return("get_mesh_info", args,
                    error=Exception(err), duration=time.time() - start_time)

            if not hasattr(obj, 'Mesh'):
                return self.log_and_return("get_mesh_info", args,
                    error=Exception(f"Object '{object_name}' is not a mesh object"),
                    duration=time.time() - start_time)

            mesh = obj.Mesh
            bb = mesh.BoundBox
            # MCP FREECAD v2: box and centre where the document shows the mesh -- in an
            # App::Part moved +100 mm they were given 100 mm off (the container's frame)
            pg = _container_placement(obj)
            if pg is not None:
                placed = mesh.copy()
                placed.Placement = pg.multiply(placed.Placement)
                bb = placed.BoundBox

            result = f"Mesh info for '{object_name}':\n"
            result += f"  Points: {mesh.CountPoints}\n"
            result += f"  Facets: {mesh.CountFacets}\n"
            result += f"  Area: {mesh.Area:.2f} mm^2\n"
            # an open mesh has no inside: its "volume" is not one (MCP FREECAD v2)
            open_note = "" if mesh.isSolid() else " (open mesh: not a real volume)"
            result += f"  Volume: {mesh.Volume:.2f} mm^3{open_note}\n"
            result += f"  Bounding box: {bb.XLength:.2f} x {bb.YLength:.2f} x {bb.ZLength:.2f} mm\n"
            result += f"  Center: ({bb.Center.x:.2f}, {bb.Center.y:.2f}, {bb.Center.z:.2f})\n"

            # Topology checks
            has_non_manifold = mesh.hasNonManifolds()
            has_self_intersect = mesh.hasSelfIntersections()
            is_solid = mesh.isSolid()

            result += f"  Is manifold: {not has_non_manifold}\n"
            result += f"  Is watertight: {is_solid}\n"
            result += f"  Has self-intersections: {has_self_intersect}\n"
            # MCP FREECAD v2: an inside-out cube (signed volume -1000 mm3) or one
            # with a flipped facet read "Health: OK" -- validate_mesh flags both.
            orientation = _orientation_issue(mesh)
            if orientation:
                result += f"  Orientation: {orientation}\n"

            if has_non_manifold or has_self_intersect or not is_solid or orientation:
                result += "  Health: ISSUES DETECTED — consider running validate_mesh with auto_repair=true"
            else:
                result += "  Health: OK"

            return self.log_and_return("get_mesh_info", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("get_mesh_info", args, error=e, duration=time.time() - start_time)

    def import_file(self, args: dict[str, Any]) -> str:
        """Import a file with automatic format detection.

        Supports mesh formats (STL, OBJ, PLY, OFF, AMF, 3MF),
        CAD formats (STEP, IGES, BREP), and FreeCAD files (FCStd).

        Args:
            file_path: Path to file (required)
            name: Object name (optional)

        Returns:
            Import result with object info
        """
        start_time = time.time()
        try:
            file_path = args.get('file_path', '')
            if not file_path:
                return self.log_and_return("import_file", args,
                    error=Exception("file_path parameter required"),
                    duration=time.time() - start_time)

            path_err = self._validate_file_path(file_path)
            if path_err:
                return self.log_and_return("import_file", args,
                    error=Exception(path_err), duration=time.time() - start_time)

            if not os.path.exists(file_path):
                return self.log_and_return("import_file", args,
                    error=Exception(f"File not found: {file_path}"),
                    duration=time.time() - start_time)

            ext = os.path.splitext(file_path)[1].lower()

            # Mesh formats — delegate to import_mesh
            if ext in self.MESH_FORMATS:
                return self.import_mesh(args)

            # CAD formats
            if ext in self.CAD_FORMATS:
                import Part

                doc = self.get_document()
                if not doc:
                    return self.log_and_return("import_file", args,
                        error=Exception(NO_ACTIVE_DOCUMENT_ERROR),
                        duration=time.time() - start_time)

                objects_before = {o.Name for o in doc.Objects}
                # MCP FREECAD v2: Part.insert flattened a STEP assembly and dropped its
                # names ("Left bracket"/"Right pin" became "asm", "asm001"). STEP goes
                # through Import.insert (names and App::Part structure kept); IGES stays
                # on Part.insert (Import.insert gave an empty shape) and BREP too (Import
                # does not read it).
                if ext in ('.step', '.stp'):
                    try:
                        import Import
                        Import.insert(file_path, doc.Name)
                    except Exception:
                        Part.insert(file_path, doc.Name)
                else:
                    Part.insert(file_path, doc.Name)
                doc.recompute()
                skip = ("App::Origin", "App::Line", "App::Plane", "App::Point")
                new_objs = [o for o in doc.Objects
                            if o.Name not in objects_before and o.TypeId not in skip]
                # `name` was documented but ignored: it labels the single top-level import
                inner = {c.Name for o in new_objs for c in (getattr(o, 'Group', None) or [])}
                tops = [o for o in new_objs if o.Name not in inner]
                name = args.get('name')
                if name and len(tops) == 1:
                    tops[0].Label = name
                if new_objs:
                    result = f"Imported {os.path.basename(file_path)} — {len(new_objs)} object(s):\n"
                    for obj in new_objs:
                        label = f" ({obj.Label})" if obj.Label != obj.Name else ""
                        shape = getattr(obj, 'Shape', None)
                        if shape is not None and not shape.isNull() and shape.Solids:
                            # where the document shows it: inside an imported App::Part too
                            bb = self._exact_bbox(self._world_shape(obj))
                            result += (f"  {obj.Name}{label}: {bb.XLength:.2f} x {bb.YLength:.2f} x "
                                       f"{bb.ZLength:.2f} mm at ({bb.XMin:.2f}, {bb.YMin:.2f}, {bb.ZMin:.2f})\n")
                        else:
                            result += f"  {obj.Name}{label} ({obj.TypeId})\n"
                    if name and len(tops) != 1:
                        result += f"  (name '{name}' not applied: {len(tops)} top-level objects)\n"
                else:
                    result = f"Imported {os.path.basename(file_path)} (no new objects detected — file may be empty)"

                return self.log_and_return("import_file", args, result=result, duration=time.time() - start_time)

            # FreeCAD native format
            if ext == '.fcstd':
                doc = FreeCAD.openDocument(file_path)
                result = f"Opened FreeCAD document '{doc.Name}' with {len(doc.Objects)} object(s)"
                return self.log_and_return("import_file", args, result=result, duration=time.time() - start_time)

            # Unknown format
            all_formats = sorted(self.MESH_FORMATS | self.CAD_FORMATS | {'.fcstd'})
            return self.log_and_return("import_file", args,
                error=Exception(f"Unsupported format '{ext}'. Supported: {', '.join(all_formats)}"),
                duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("import_file", args, error=e, duration=time.time() - start_time)

    def export_file(self, args: dict[str, Any]) -> str:
        """Export an object with automatic format detection.

        Supports mesh formats (STL, OBJ, PLY, OFF, AMF, 3MF) and
        CAD formats (STEP, IGES, BREP).

        Args:
            object_name: Object to export (required)
            file_path: Output file path (required)
            linear_deflection: Tessellation accuracy for mesh export (default 0.1)

        Returns:
            Export result with file path and size
        """
        start_time = time.time()
        try:
            object_name = args.get('object_name', '')
            file_path = args.get('file_path', '')

            if not object_name:
                return self.log_and_return("export_file", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)
            if not file_path:
                return self.log_and_return("export_file", args,
                    error=Exception("file_path parameter required"),
                    duration=time.time() - start_time)

            path_err = self._validate_file_path(file_path)
            if path_err:
                return self.log_and_return("export_file", args,
                    error=Exception(path_err), duration=time.time() - start_time)

            ext = os.path.splitext(file_path)[1].lower()

            # Mesh formats — delegate to export_mesh
            if ext in self.MESH_FORMATS:
                return self.export_mesh(args)

            # CAD formats
            if ext in self.CAD_FORMATS:
                import Part

                doc, obj, err = self.resolve_object(object_name)
                if err:
                    return self.log_and_return("export_file", args,
                        error=Exception(err), duration=time.time() - start_time)

                if not hasattr(obj, 'Shape'):
                    return self.log_and_return("export_file", args,
                        error=Exception(f"Object '{object_name}' has no Shape — cannot export to CAD format. Try mesh format instead."),
                        duration=time.time() - start_time)

                # Ensure output directory exists
                out_dir = os.path.dirname(file_path)
                if out_dir and not os.path.exists(out_dir):
                    os.makedirs(out_dir, exist_ok=True)

                # MCP FREECAD v2: Part.export writes the object's local Shape and
                # nothing at all for an App::Part or an App::Link: a STEP of an
                # assembly or of a link (1.6 KB, no solid) was "Exported". Such
                # objects, and parts inside a moved container, are written through
                # a temporary feature holding their shape in the document frame.
                shape = self._world_shape(obj)
                if _empty_shape(shape):
                    return self.log_and_return("export_file", args,
                        error=Exception(f"Object '{object_name}' has an empty shape: "
                                        "nothing to export"),
                        duration=time.time() - start_time)
                if _container_placement(obj) is None and _is_part_feature(obj):
                    Part.export([obj], file_path)
                else:
                    temp_obj = doc.addObject("Part::Feature", "_export_temp")
                    try:
                        temp_obj.Shape = shape
                        temp_obj.Label = getattr(obj, 'Label', object_name)
                        Part.export([temp_obj], file_path)
                    finally:
                        doc.removeObject(temp_obj.Name)

                file_size = os.path.getsize(file_path)
                size_str = f"{file_size / 1024:.1f} KB" if file_size < 1024 * 1024 else f"{file_size / (1024 * 1024):.1f} MB"
                result = f"Exported '{object_name}' to {file_path} ({size_str})"

                return self.log_and_return("export_file", args, result=result, duration=time.time() - start_time)

            # Unknown format
            all_formats = sorted(self.MESH_FORMATS | self.CAD_FORMATS)
            return self.log_and_return("export_file", args,
                error=Exception(f"Unsupported export format '{ext}'. Supported: {', '.join(all_formats)}"),
                duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("export_file", args, error=e, duration=time.time() - start_time)

    def validate_mesh(self, args: dict[str, Any]) -> str:
        """Validate mesh topology and optionally auto-repair issues.

        Checks for non-manifold edges, self-intersections, degenerate facets,
        duplicate points/facets, and normal orientation.

        Args:
            object_name: Mesh object to validate (required)
            auto_repair: If true, attempt to fix issues (default false)

        Returns:
            Validation report with issue list and repair results
        """
        start_time = time.time()
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return self.log_and_return("validate_mesh", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return self.log_and_return("validate_mesh", args,
                    error=Exception(err), duration=time.time() - start_time)

            if not hasattr(obj, 'Mesh'):
                return self.log_and_return("validate_mesh", args,
                    error=Exception(f"Object '{object_name}' is not a mesh object"),
                    duration=time.time() - start_time)

            auto_repair = args.get('auto_repair', False)
            mesh = obj.Mesh

            # Collect issues
            issues = []
            if mesh.hasNonManifolds():
                issues.append("non-manifold edges")
            if mesh.hasSelfIntersections():
                issues.append("self-intersections")
            if not mesh.isSolid():
                issues.append("not watertight (open boundary)")
            if mesh.hasInvalidPoints():
                issues.append("invalid/NaN points")
            # MCP FREECAD v2: the docstring promised a normal-orientation check that
            # was never made: a cube with one flipped facet, or turned inside out,
            # "passed all validation checks" (slicers then see holes or a void).
            orientation = _orientation_issue(mesh)
            if orientation:
                issues.append(orientation)
            had_self_intersections = "self-intersections" in issues

            # Check for duplicates
            orig_points = mesh.CountPoints
            orig_facets = mesh.CountFacets

            if not issues:
                result = f"Mesh '{object_name}' passed all validation checks.\n"
                result += f"  Points: {orig_points}, Facets: {orig_facets}\n"
                result += "  Status: VALID"
                return self.log_and_return("validate_mesh", args, result=result, duration=time.time() - start_time)

            result = f"Mesh '{object_name}' has {len(issues)} issue(s):\n"
            for issue in issues:
                result += f"  - {issue}\n"

            if auto_repair:
                result += "\nAttempting repairs:\n"

                # Work on a copy to preserve original
                mesh_copy = mesh.copy()
                repairs_done = []
                # Each bare `except Exception: pass` below made a repair
                # call that actually RAISED indistinguishable from one that
                # ran fine and simply found nothing to fix — both produced
                # no entry in repairs_done. Track failures explicitly so
                # the caller can tell "no such issue" from "repair failed".
                repairs_failed = []

                try:
                    mesh_copy.removeDuplicatedPoints()
                    if mesh_copy.CountPoints < orig_points:
                        repairs_done.append(f"removed {orig_points - mesh_copy.CountPoints} duplicate points")
                except Exception as e:
                    repairs_failed.append(f"removeDuplicatedPoints: {e}")

                try:
                    before = mesh_copy.CountFacets
                    mesh_copy.removeDuplicatedFacets()
                    if mesh_copy.CountFacets < before:
                        repairs_done.append(f"removed {before - mesh_copy.CountFacets} duplicate facets")
                except Exception as e:
                    repairs_failed.append(f"removeDuplicatedFacets: {e}")

                try:
                    before = mesh_copy.CountFacets
                    mesh_copy.fixDegenerations()
                    if mesh_copy.CountFacets != before:
                        repairs_done.append("fixed degenerate facets")
                except Exception as e:
                    repairs_failed.append(f"fixDegenerations: {e}")

                try:
                    mesh_copy.fixSelfIntersections()
                    # MCP FREECAD v2: said "fixed" also when there were none to fix
                    if had_self_intersections and not mesh_copy.hasSelfIntersections():
                        repairs_done.append("fixed self-intersections")
                except Exception as e:
                    repairs_failed.append(f"fixSelfIntersections: {e}")

                try:
                    mesh_copy.harmonizeNormals()
                except Exception as e:
                    repairs_failed.append(f"harmonizeNormals: {e}")

                try:
                    was_solid = mesh_copy.isSolid()
                    # MCP FREECAD v2: called with no argument it raised in FreeCAD 1.1
                    # ("takes at least 1 argument"): no hole was ever filled. The
                    # argument is the largest hole filled, in edges along its rim.
                    mesh_copy.fillupHoles(_MAX_HOLE_EDGES)
                    if not was_solid and mesh_copy.isSolid():
                        repairs_done.append("filled holes (now watertight)")
                except Exception as e:
                    repairs_failed.append(f"fillupHoles: {e}")

                try:
                    # a closed mesh can tell inside-out: turn it outward
                    if mesh_copy.isSolid() and _signed_volume(mesh_copy) < 0:
                        mesh_copy.flipNormals()
                    if orientation and not _orientation_issue(mesh_copy):
                        repairs_done.append("fixed facet orientation (normals point outward)")
                except Exception as e:
                    repairs_failed.append(f"flipNormals: {e}")

                # Apply repaired mesh
                obj.Mesh = mesh_copy
                doc.recompute()

                if repairs_done:
                    for repair in repairs_done:
                        result += f"  + {repair}\n"
                else:
                    result += "  (no automatic repairs succeeded)\n"

                if repairs_failed:
                    result += "\n  Repairs that raised an error (not applied):\n"
                    for failure in repairs_failed:
                        result += f"    ! {failure}\n"

                # Re-check
                remaining = []
                if mesh_copy.hasNonManifolds():
                    remaining.append("non-manifold edges")
                if mesh_copy.hasSelfIntersections():
                    remaining.append("self-intersections")
                if not mesh_copy.isSolid():
                    remaining.append("not watertight")
                left = _orientation_issue(mesh_copy)
                if left:
                    remaining.append(left)

                if remaining:
                    result += f"\n  Remaining issues: {', '.join(remaining)}"
                else:
                    result += "\n  All issues resolved!"

                result += f"\n  Final mesh: {mesh_copy.CountPoints} points, {mesh_copy.CountFacets} facets"
            else:
                result += "\nRun with auto_repair=true to attempt automatic fixes."

            return self.log_and_return("validate_mesh", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("validate_mesh", args, error=e, duration=time.time() - start_time)

    def simplify_mesh(self, args: dict[str, Any]) -> str:
        """Simplify a mesh by reducing face count (decimation).

        Useful for large terrain meshes that need to be converted to solids
        for CAM processing. Specify either target_count or reduction.

        Args:
            object_name: Mesh object to simplify (required)
            target_count: Absolute target number of faces
            reduction: Reduction ratio 0-1 (e.g., 0.5 = reduce to 50%)
            name: Name for simplified mesh (optional, default: original_Simplified)

        Returns:
            New mesh name with before/after face counts
        """
        start_time = time.time()
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return self.log_and_return("simplify_mesh", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return self.log_and_return("simplify_mesh", args,
                    error=Exception(err), duration=time.time() - start_time)

            if not hasattr(obj, 'Mesh'):
                return self.log_and_return("simplify_mesh", args,
                    error=Exception(f"Object '{object_name}' is not a mesh object"),
                    duration=time.time() - start_time)

            mesh = obj.Mesh
            original_count = mesh.CountFacets

            target_count = args.get('target_count')
            reduction = args.get('reduction')

            if target_count is None and reduction is None:
                return self.log_and_return("simplify_mesh", args,
                    error=Exception("Provide either target_count (absolute face count) or reduction (0-1 ratio)"),
                    duration=time.time() - start_time)

            if target_count is not None:
                target = int(target_count)
            else:
                reduction = float(reduction)
                if not 0.0 < reduction < 1.0:
                    return self.log_and_return("simplify_mesh", args,
                        error=Exception("reduction must be between 0 and 1 (exclusive)"),
                        duration=time.time() - start_time)
                target = int(original_count * reduction)

            if target >= original_count:
                return self.log_and_return("simplify_mesh", args,
                    error=Exception(f"Target count ({target}) must be less than current count ({original_count})"),
                    duration=time.time() - start_time)

            if target < 4:
                return self.log_and_return("simplify_mesh", args,
                    error=Exception(f"Target count ({target}) is too small — minimum is 4 faces"),
                    duration=time.time() - start_time)

            name = args.get('name', f"{object_name}_Simplified")

            # Work on a copy
            mesh_copy = mesh.copy()
            mesh_copy.decimate(target)

            simplified_obj = doc.addObject("Mesh::Feature", name)
            simplified_obj.Mesh = mesh_copy
            _place_beside(simplified_obj, obj)   # beside its source (MCP FREECAD v2)
            doc.recompute()

            actual_count = mesh_copy.CountFacets
            pct = (1.0 - actual_count / original_count) * 100

            result = (f"Simplified mesh '{object_name}' → '{simplified_obj.Name}'\n"
                      f"  Before: {original_count} facets\n"
                      f"  After:  {actual_count} facets ({pct:.1f}% reduction)\n"
                      f"  Points: {mesh_copy.CountPoints}")

            return self.log_and_return("simplify_mesh", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("simplify_mesh", args, error=e, duration=time.time() - start_time)
