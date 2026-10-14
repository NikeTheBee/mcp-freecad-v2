# Part workbench operation handlers for FreeCAD MCP

from typing import Any

import FreeCAD

from .base import (
    NO_ACTIVE_DOCUMENT_ERROR,
    OCCT_CONFUSION_TOLERANCE_DEFAULT,
    BaseHandler,
    axis_vector_or_none,
)


class PartOpsHandler(BaseHandler):
    """Handler for Part workbench operations (extrude, revolve, mirror, section, scale)."""

    @staticmethod
    def _profile(shape):
        """The whole profile as planar face(s): every closed wire, inner loops
        cut out, in any order (MCP FREECAD v2, 2026-10-14). Extruding or
        revolving Wires[0] alone dropped a plate's holes -- or made a cylinder
        of the plate when a circle came first. None: nothing closed."""
        import Part
        if shape.Wires:
            closed = [w for w in shape.Wires if w.isClosed()]
            return Part.makeFace(closed, "Part::FaceMakerBullseye") if closed else None
        return shape if shape.Faces else None

    @staticmethod
    def _plane_normal(obj, profile):
        """Unit normal of the profile's plane: the sketch's own, else its face's."""
        try:
            return obj.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        except Exception:
            faces = list(getattr(profile, "Faces", []) or [])
            return faces[0].normalAt(0, 0) if faces else FreeCAD.Vector(0, 0, 1)

    def extrude(self, args: dict[str, Any]) -> str:
        """Extrude a sketch or wire profile."""
        try:
            profile_sketch = args.get('profile_sketch', '')
            height = args.get('height', 10)
            # Normalize case — 'X'/'Y'/'Z' must not silently fall through to Z
            # (revolve already lowercases; extrude didn't). Omitted: along the
            # sketch's own normal (a fixed 'z' lay IN the plane of an XZ sketch).
            given = args.get('direction')
            direction = str(given).lower() if given is not None else None

            doc, sketch, err = self.resolve_object(profile_sketch, attr='Shape', noun='Sketch')
            if err:
                return err

            shape = sketch.Shape
            profile = self._profile(shape)
            if profile is None:
                return (f"Sketch {profile_sketch} has no valid wires or faces to extrude"
                        if not shape.Wires else f"Sketch {profile_sketch} has no closed profile to extrude")
            normal = self._plane_normal(sketch, profile)

            # Determine extrusion vector
            axes = {'x': FreeCAD.Vector(1, 0, 0), 'y': FreeCAD.Vector(0, 1, 0), 'z': FreeCAD.Vector(0, 0, 1)}
            if direction is None:
                unit = normal
                direction = next(("-" + k if float(a.dot(normal)) < 0 else k
                                  for k, a in axes.items() if abs(abs(float(a.dot(normal))) - 1.0) < 1e-9),
                                 "the sketch's normal")
            elif direction in axes:
                unit = axes[direction]
            else:
                return f"Invalid direction {direction!r}; use 'x', 'y', or 'z'"
            if abs(float(unit.dot(normal))) < 1e-9:
                return (f"Invalid direction {direction!r}: it lies in the plane of {profile_sketch}, "
                        "the extrusion would have no volume; omit it to extrude along the sketch's normal")
            vec = FreeCAD.Vector(unit.x * height, unit.y * height, unit.z * height)
            extruded = profile.extrude(vec)

            extrude_obj = doc.addObject("Part::Feature", f"{profile_sketch}_extruded")
            extrude_obj.Shape = extruded
            self.recompute(doc)

            return f"Extruded {profile_sketch} by {height}mm in {direction} direction"

        except Exception as e:
            return f"Error extruding profile: {e}"

    def revolve(self, args: dict[str, Any]) -> str:
        """Revolve a sketch profile around an axis."""
        try:
            profile_sketch = args.get('profile_sketch', '')
            angle = args.get('angle', 360)
            axis = args.get('axis', 'z')

            doc, sketch, err = self.resolve_object(profile_sketch, attr='Shape', noun='Sketch')
            if err:
                return err

            # Define revolution axis -- no fallback: an unrecognized axis
            # used to silently revolve around Z while the success message
            # still echoed the requested axis string.
            axis_vec = axis_vector_or_none(axis)
            if axis_vec is None:
                return f"Invalid axis '{axis}': must be 'x', 'y', or 'z'"

            shape = sketch.Shape
            import Part

            pos = FreeCAD.Vector(0, 0, 0)
            if hasattr(sketch, 'Placement'):
                pos = sketch.Placement.Base

            profile = self._profile(shape)
            if profile is None:
                return f"Sketch {profile_sketch} has no valid wires or faces to revolve"
            revolved = profile.revolve(pos, axis_vec, angle)

            revolve_obj = doc.addObject("Part::Feature", f"{profile_sketch}_revolved")
            revolve_obj.Shape = revolved
            self.recompute(doc)

            return f"Revolved {profile_sketch} by {angle}° around {axis} axis"

        except Exception as e:
            return f"Error revolving profile: {e}"

    def mirror_object(self, args: dict[str, Any]) -> str:
        """Mirror object across a plane."""
        try:
            object_name = args.get('object_name', '')
            plane = args.get('plane', 'YZ')
            name = args.get('name', '')

            doc, obj, err = self.resolve_object(object_name, attr='Shape')
            if err:
                return err

            # Set mirror plane normal and origin
            if plane == "YZ":
                normal = FreeCAD.Vector(1, 0, 0)
                mirror_point = FreeCAD.Vector(0, 0, 0)
            elif plane == "XZ":
                normal = FreeCAD.Vector(0, 1, 0)
                mirror_point = FreeCAD.Vector(0, 0, 0)
            elif plane == "XY":
                normal = FreeCAD.Vector(0, 0, 1)
                mirror_point = FreeCAD.Vector(0, 0, 0)
            else:
                return f"Invalid plane '{plane}'. Valid options: XY, XZ, YZ"

            mirrored_shape = obj.Shape.mirror(mirror_point, normal)

            if name:
                mirrored_obj = doc.addObject("Part::Feature", name)
            else:
                mirrored_obj = doc.addObject("Part::Feature", f"{object_name}_mirrored")
            mirrored_obj.Shape = mirrored_shape

            self.recompute(doc)
            return f"Mirrored {object_name} across {plane} plane at (0,0,0)"

        except Exception as e:
            return f"Error mirroring object: {e}"

    def scale_object(self, args: dict[str, Any]) -> str:
        """Scale object by modifying its dimensions directly."""
        try:
            object_name = args.get('object_name', '')
            scale_factor = args.get('scale_factor', 1.5)
            # Guard non-positive factors: <=0 inverts/zeroes the non-parametric
            # transformGeometry path (parametric paths get clamped by FreeCAD).
            if scale_factor <= 0:
                return f"scale_factor must be > 0 (got {scale_factor})"

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return err

            # Check if this is a parametric object (Box, Cylinder, etc.)
            if hasattr(obj, 'Length') and hasattr(obj, 'Width') and hasattr(obj, 'Height'):
                # Box object
                old_dims = f"{obj.Length.Value}x{obj.Width.Value}x{obj.Height.Value}"
                obj.Length = obj.Length.Value * scale_factor
                obj.Width = obj.Width.Value * scale_factor
                obj.Height = obj.Height.Value * scale_factor
                new_dims = f"{obj.Length.Value}x{obj.Width.Value}x{obj.Height.Value}"
                self.recompute(doc)
                return f"Scaled {object_name} by factor {scale_factor} ({old_dims}mm → {new_dims}mm)"

            elif hasattr(obj, 'Radius') and hasattr(obj, 'Height'):
                # Cylinder/Cone object
                old_dims = f"R{obj.Radius.Value}xH{obj.Height.Value}"
                obj.Radius = obj.Radius.Value * scale_factor
                obj.Height = obj.Height.Value * scale_factor
                if hasattr(obj, 'Radius2'):
                    obj.Radius2 = obj.Radius2.Value * scale_factor
                new_dims = f"R{obj.Radius.Value}xH{obj.Height.Value}"
                self.recompute(doc)
                return f"Scaled {object_name} by factor {scale_factor} ({old_dims}mm → {new_dims}mm)"

            elif hasattr(obj, 'Radius'):
                # Sphere object
                old_radius = obj.Radius.Value
                obj.Radius = obj.Radius.Value * scale_factor
                self.recompute(doc)
                return f"Scaled {object_name} by factor {scale_factor} (R{old_radius}mm → R{obj.Radius.Value}mm)"

            else:
                # Non-parametric object - create scaled copy
                if hasattr(obj, 'Shape'):
                    matrix = FreeCAD.Matrix()
                    matrix.scale(scale_factor, scale_factor, scale_factor)
                    scaled_shape = obj.Shape.transformGeometry(matrix)
                    scaled_obj = doc.addObject("Part::Feature", f"{object_name}_scaled")
                    scaled_obj.Shape = scaled_shape
                    self.recompute(doc)
                    return f"Created scaled copy: {scaled_obj.Name} (factor {scale_factor})"
                else:
                    return f"Cannot scale {object_name} - not a parametric object"

        except Exception as e:
            return f"Error scaling object: {e}"

    def section(self, args: dict[str, Any]) -> str:
        """Create section of object - placeholder."""
        try:
            object_name = args.get('object_name', '')
            plane = args.get('plane', 'XY')
            offset = args.get('offset', 0)

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return err
            if not hasattr(obj, 'Shape'):
                return f"Object {object_name} is not a shape object"

            import Part

            # MCP FREECAD v2 (2026-10-14): Shape.slice at the plane, exact and
            # unbounded. A fixed 1000 x 1000 mm plane centred on the origin cut
            # a part beyond +-500 mm partly or not at all, replied as "Created".
            normals = {'XY': (0, 0, 1), 'XZ': (0, 1, 0), 'YZ': (1, 0, 0)}
            if plane not in normals:
                return f"Invalid plane '{plane}'. Valid options: XY, XZ, YZ"
            wires = obj.Shape.slice(FreeCAD.Vector(*normals[plane]), float(offset))
            if not wires:
                bb = self._exact_bbox(obj.Shape)
                axis, lo, hi = {'XY': ('z', bb.ZMin, bb.ZMax), 'XZ': ('y', bb.YMin, bb.YMax),
                                'YZ': ('x', bb.XMin, bb.XMax)}[plane]
                return (f"The {plane} plane at {offset} mm misses {object_name} "
                        f"(it spans {axis} {lo:g}..{hi:g} mm): no section")
            section_shape = Part.makeCompound(wires)
            area = ""
            try:
                closed = [w for w in wires if w.isClosed()]
                if closed:
                    area = (f", area {Part.makeFace(closed, 'Part::FaceMakerBullseye').Area:.2f} mm2 "
                            f"({len(closed)} closed outline(s))")
            except Exception:
                area = ""
            section_obj = doc.addObject("Part::Feature", f"{object_name}_section")
            section_obj.Shape = section_shape

            self.recompute(doc)

            return f"Created section of {object_name} at {plane} plane, offset {offset}mm{area}"

        except Exception as e:
            return f"Error creating section: {e}"

    def loft(self, args: dict[str, Any]) -> str:
        """Loft between multiple sketches to create complex shapes."""
        try:
            sketches = args.get('sketches', [])
            ruled = args.get('ruled', False)
            closed = args.get('closed', True)
            name = args.get('name', 'Loft')

            if len(sketches) < 2:
                return "Need at least 2 sketches for lofting"

            doc = self.get_document()
            if not doc:
                return NO_ACTIVE_DOCUMENT_ERROR

            sketch_objs = []
            for sketch_name in sketches:
                _, sketch, err = self.resolve_object(sketch_name, doc, noun='Sketch')
                if err:
                    return err
                sketch_objs.append(sketch)

            loft = doc.addObject("Part::Loft", name)
            loft.Sections = sketch_objs
            loft.Solid = closed
            loft.Ruled = ruled

            self.recompute(doc)

            err = self._check_feature_state(loft, "Loft")
            if err:
                return err

            return f"Created loft: {loft.Name} through {len(sketches)} profiles"

        except Exception as e:
            return f"Error creating loft: {e}"

    def sweep(self, args: dict[str, Any]) -> str:
        """Sweep a profile sketch along a path sketch."""
        try:
            profile_sketch = args.get('profile_sketch', '')
            path_sketch = args.get('path_sketch', '')
            solid = args.get('solid', True)
            name = args.get('name', 'Sweep')

            doc = self.get_document()
            if not doc:
                return NO_ACTIVE_DOCUMENT_ERROR

            _, profile, err = self.resolve_object(profile_sketch, doc, noun='Profile sketch')
            if err:
                return err

            _, path, err = self.resolve_object(path_sketch, doc, noun='Path sketch')
            if err:
                return err

            sweep = doc.addObject("Part::Sweep", name)
            sweep.Sections = [profile]
            sweep.Spine = path
            sweep.Solid = solid

            self.recompute(doc)

            err = self._check_feature_state(sweep, "Sweep")
            if err:
                return err

            return f"Created sweep: {sweep.Name} with profile {profile_sketch} along path {path_sketch}"

        except Exception as e:
            return f"Error creating sweep: {e}"

    def compound(self, args: dict[str, Any]) -> str:
        """Create a compound from multiple objects (non-boolean grouping)."""
        try:
            objects = args.get('objects', [])
            name = args.get('name', 'Compound')

            if len(objects) < 2:
                return "Need at least 2 objects for compound"

            doc = self.get_document()
            if not doc:
                return NO_ACTIVE_DOCUMENT_ERROR

            import Part

            shapes = []
            for obj_name in objects:
                _, obj, err = self.resolve_object(obj_name, doc, attr='Shape')
                if err:
                    return err
                shapes.append(obj.Shape)

            compound_shape = Part.makeCompound(shapes)
            compound_obj = doc.addObject("Part::Feature", name)
            compound_obj.Shape = compound_shape

            self.recompute(doc)

            return f"Created compound: {compound_obj.Name} from {len(objects)} objects"

        except Exception as e:
            return f"Error creating compound: {e}"

    def shape_string(self, args: dict[str, Any]) -> str:
        """Create a Part ShapeString — parametric text as a wire compound.

        Uses Part.makeWireString() to produce a compound of closed wires from
        a font and string. The result is a static Part::Feature (not parametric
        after creation), but the wires are clean and ready for Pad / Pocket.

        For a Draft-managed ShapeString use draft_operations shape_string instead.
        """
        try:
            string = args.get('string', 'Text')
            font_file = args.get('font_file', '')
            size = float(args.get('size', 10.0))
            tracking = float(args.get('tracking', 0.0))
            x = float(args.get('x', 0))
            y = float(args.get('y', 0))
            z = float(args.get('z', 0))
            name = args.get('name', 'ShapeString')

            doc = self.get_document()
            if not doc:
                return NO_ACTIVE_DOCUMENT_ERROR

            font = self.find_font(font_file)
            if not font:
                return (
                    "Error: no font file found. "
                    "Specify font_file with a path to a .ttf font, e.g. "
                    "/System/Library/Fonts/Supplemental/Arial.ttf"
                )

            import Part

            # makeWireString returns [[Wire, ...], ...] — one list of wires per character
            char_wires = Part.makeWireString(string, font, size, tracking)
            flat = [w for char in char_wires for w in char]
            if not flat:
                return f"Error: no wires generated for string '{string}' — check font path and string content"

            compound = Part.makeCompound(flat)
            ss_obj = doc.addObject("Part::Feature", name)
            ss_obj.Label = name
            ss_obj.Shape = compound
            ss_obj.Placement.Base = FreeCAD.Vector(x, y, z)

            self.recompute(doc)

            import os
            return (
                f"Created Part ShapeString '{string}' ({ss_obj.Name}) at ({x},{y},{z}), "
                f"size={size}mm, font={os.path.basename(font)}, "
                f"chars={len(char_wires)}, wires={len(flat)}. "
                f"Ready to Pad or use as Pocket profile."
            )

        except Exception as e:
            return f"Error creating Part ShapeString: {e}"

    def check_geometry(self, args: dict[str, Any]) -> str:
        """Check geometry validity of an object (BRep check)."""
        try:
            object_name = args.get('object_name', '')
            run_bop_check = args.get('run_bop_check', False)

            doc, obj, err = self.resolve_object(object_name, attr='Shape')
            if err:
                return err

            shape = obj.Shape
            results = []

            # Basic validity check
            is_valid = shape.isValid()
            results.append(f"Valid: {is_valid}")

            # Check if closed (watertight)
            if hasattr(shape, 'isClosed'):
                results.append(f"Closed (watertight): {shape.isClosed()}")

            # Check for solids
            results.append(f"Solids: {len(shape.Solids)}")
            results.append(f"Shells: {len(shape.Shells)}")
            results.append(f"Faces: {len(shape.Faces)}")
            results.append(f"Edges: {len(shape.Edges)}")
            results.append(f"Vertices: {len(shape.Vertexes)}")

            # Check for degenerate edges. References base.py's
            # OCCT_CONFUSION_TOLERANCE_DEFAULT as the single source of
            # truth for this numeral instead of independently hardcoding
            # 1e-7 here.
            degen_edges = 0
            for edge in shape.Edges:
                if edge.Length < OCCT_CONFUSION_TOLERANCE_DEFAULT:
                    degen_edges += 1
            if degen_edges > 0:
                results.append(f"Degenerate edges (near-zero length): {degen_edges}")

            # Check for self-intersection (expensive)
            if run_bop_check:
                try:
                    shape.check(True)  # Raises exception if invalid
                    results.append("BOP check: Passed")
                except Exception as e:
                    results.append(f"BOP check: Failed - {e}")

            # Volume sanity check
            if shape.Solids:
                vol = shape.Volume
                if vol < 0:
                    results.append(f"Warning: Negative volume ({vol:.2f}) - normals may be inverted")
                elif vol == 0:
                    results.append("Warning: Zero volume")
                else:
                    results.append(f"Volume: {vol:.2f} mm³")

            return f"Geometry check for {object_name}:\n  " + "\n  ".join(results)

        except Exception as e:
            return f"Error checking geometry: {e}"
