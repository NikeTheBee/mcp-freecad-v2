# Transform operation handlers for FreeCAD MCP

from typing import Any

import FreeCAD

from .base import BaseHandler


class TransformsHandler(BaseHandler):
    """Handler for transform operations (move, rotate, copy, array)."""

    @staticmethod
    def _attached(obj) -> bool:
        mode = getattr(obj, 'MapMode', None)
        return isinstance(mode, str) and mode != 'Deactivated' and hasattr(obj, 'AttachmentOffset')

    @staticmethod
    def _place_attached(obj, target) -> str:
        """MCP FREECAD v2: an attached object (a sketch on a plane or a face) has its
        Placement recomputed from its attachment, so setting it was undone by the
        recompute: "Moved S by (0, 0, 10)" / "Rotated S by 90°" left it exactly where
        it was. It gets `target` through its AttachmentOffset instead."""
        attachment = obj.Placement.multiply(obj.AttachmentOffset.inverse())
        obj.AttachmentOffset = attachment.inverse().multiply(target)
        return " (through its attachment offset: it stays attached)"

    @staticmethod
    def _not_placed(obj, base=None, rotation=None) -> bool:
        """True when, after the recompute, a real document object is not where it
        was put (FreeCAD computes its placement). False for test mocks."""
        try:
            if not isinstance(obj, FreeCAD.DocumentObject):
                return False
            pl = obj.Placement
            if base is not None and pl.Base.distanceToPoint(base) > 1e-7:
                return True
            probe = FreeCAD.Vector(1, 2, 3)
            return rotation is not None and \
                pl.Rotation.multVec(probe).distanceToPoint(rotation.multVec(probe)) > 1e-7
        except Exception:
            return False

    def move_object(self, args: dict[str, Any]) -> str:
        """Move an object.

        By default moves by a relative offset. Pass relative=False to set
        an absolute position instead.
        """
        import time
        start_time = time.time()

        try:
            object_name = args.get('object_name', '')
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)
            relative = args.get('relative', True)

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return self.log_and_return("move_object", args, error=Exception(err))

            b = obj.Placement.Base
            target = FreeCAD.Vector(b.x + x, b.y + y, b.z + z) if relative else FreeCAD.Vector(x, y, z)
            note = ""
            if self._attached(obj):
                note = self._place_attached(obj, FreeCAD.Placement(target, obj.Placement.Rotation))
            elif relative:
                obj.Placement.Base = FreeCAD.Vector(
                    obj.Placement.Base.x + x,
                    obj.Placement.Base.y + y,
                    obj.Placement.Base.z + z
                )
            else:
                obj.Placement.Base = FreeCAD.Vector(x, y, z)
            result = (f"Moved {object_name} by ({x}, {y}, {z})" if relative
                      else f"Moved {object_name} to ({x}, {y}, {z})") + note

            self.recompute(doc)
            if self._not_placed(obj, base=target):
                p = obj.Placement.Base
                return self.log_and_return("move_object", args, error=Exception(
                    f"{object_name} did NOT move: FreeCAD recomputes its placement (it is at "
                    f"({p.x:g}, {p.y:g}, {p.z:g})); move what it follows instead (its sketch, its Body)"))
            duration = time.time() - start_time
            return self.log_and_return("move_object", args, result=result, duration=duration)

        except Exception as e:
            duration = time.time() - start_time
            return self.log_and_return("move_object", args, error=e, duration=duration)

    def rotate_object(self, args: dict[str, Any]) -> str:
        """Rotate an object around axis."""
        try:
            object_name = args.get('object_name', '')
            axis = args.get('axis', 'z')
            angle = args.get('angle', 90)

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return err

            # Set rotation axis
            axis_vectors = {
                'x': FreeCAD.Vector(1, 0, 0),
                'y': FreeCAD.Vector(0, 1, 0),
                'z': FreeCAD.Vector(0, 0, 1),
            }
            axis_vector = axis_vectors.get(axis.lower())
            if axis_vector is None:
                # No fallback — an unrecognized axis used to silently
                # rotate around Z while the success message still echoed
                # the requested axis string, e.g. axis="q" reporting
                # "around Q-axis" while actually rotating around Z.
                return f"Invalid axis '{axis}': must be 'x', 'y', or 'z'"

            # Rotate object around the GLOBAL axis through its placement point
            # (MCP FREECAD v2): old * new turned an already-rotated object around
            # its OWN axis while the reply named the global one
            rotation = FreeCAD.Rotation(axis_vector, angle)
            target = rotation.multiply(obj.Placement.Rotation)
            note = ""
            if self._attached(obj):
                note = self._place_attached(obj, FreeCAD.Placement(obj.Placement.Base, target))
            else:
                obj.Placement.Rotation = target
            self.recompute(doc)
            if self._not_placed(obj, rotation=target):
                return (f"Error rotating object: {object_name} did NOT turn: FreeCAD recomputes "
                        "its placement; turn what it follows instead (its sketch, its Body)")

            b = obj.Placement.Base
            return (f"Rotated {object_name} by {angle}° around the {axis.upper()}-axis through its "
                    f"placement point ({b.x:g}, {b.y:g}, {b.z:g})" + note)

        except Exception as e:
            return f"Error rotating object: {e}"

    def copy_object(self, args: dict[str, Any]) -> str:
        """Create a copy of an object."""
        try:
            object_name = args.get('object_name', '')
            name = args.get('name', 'Copy')
            x = args.get('x', 0)
            y = args.get('y', 0)
            z = args.get('z', 0)

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return err

            # Create copy. with_dependencies=True so parametric/body-backed
            # objects copy their dependency chain instead of referencing the
            # original's (which silently couples the "copy" to the source).
            copy = doc.copyObject(obj, True)
            copy.Label = name
            copy.Placement.Base = FreeCAD.Vector(
                obj.Placement.Base.x + x,
                obj.Placement.Base.y + y,
                obj.Placement.Base.z + z
            )
            self.recompute(doc)

            return f"Created copy: {copy.Name} at offset ({x}, {y}, {z})"

        except Exception as e:
            return f"Error copying object: {e}"

    def array_object(self, args: dict[str, Any]) -> str:
        """Create linear array of object."""
        try:
            object_name = args.get('object_name', '')
            count = args.get('count', 3)
            spacing_x = args.get('spacing_x', 10)
            spacing_y = args.get('spacing_y', 0)
            spacing_z = args.get('spacing_z', 0)

            doc, obj, err = self.resolve_object(object_name)
            if err:
                return err

            # Create array copies
            copies = []
            for i in range(1, count):  # Start from 1 (original is 0)
                copy = doc.copyObject(obj)
                copy.Label = f"{obj.Label}_Array{i}"
                copy.Placement.Base = FreeCAD.Vector(
                    obj.Placement.Base.x + (spacing_x * i),
                    obj.Placement.Base.y + (spacing_y * i),
                    obj.Placement.Base.z + (spacing_z * i)
                )
                copies.append(copy.Name)

            self.recompute(doc)

            # range(1, count) creates count-1 new copies (the original is
            # instance 0) -- the message previously said "{count} copies",
            # overstating the actual new-object count by one.
            return f"Created array: original + {len(copies)} copies of {object_name} with spacing ({spacing_x}, {spacing_y}, {spacing_z})"

        except Exception as e:
            return f"Error creating array: {e}"
