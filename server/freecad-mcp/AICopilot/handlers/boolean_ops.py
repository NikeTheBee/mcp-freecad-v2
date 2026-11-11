# Boolean operation handlers for FreeCAD MCP

from typing import Any

import FreeCAD

from .base import NO_ACTIVE_DOCUMENT_ERROR, BaseHandler



def _volume(obj):
    """The object's volume when it is a real number, else None (unit-test mocks)."""
    try:
        v = obj.Shape.Volume
    except Exception:
        return None
    return float(v) if isinstance(v, (int, float)) else None


def _solid_count(obj):
    """Number of solids in the object's shape, else None (unit-test mocks)."""
    try:
        solids = obj.Shape.Solids
    except Exception:
        return None
    return len(solids) if isinstance(solids, (list, tuple)) else None


def _no_material(result, sources) -> bool:
    """MCP FREECAD v2: True when a boolean left nothing. OCCT does not return a
    null shape for that but an EMPTY compound: a cut whose tool swallows the base
    and a common of disjoint (or merely touching) boxes passed the isNull() guard,
    hid the sources and were replied "Created ..., volume 0 mm3"."""
    shape = getattr(result, 'Shape', None)
    if not shape or shape.isNull():
        return True
    n = _solid_count(result)
    if n is None or n > 0:
        return False
    # no solid left: nothing, unless the operands had none either (faces, wires)
    return any((_solid_count(o) or 0) > 0 for o in sources)


def _pieces(result) -> str:
    """', in N separate solids' when the result is not one piece (MCP FREECAD v2):
    a fuse of parts that do not touch was replied like one fused part."""
    n = _solid_count(result)
    return f", in {n} separate solids (pieces that do not touch are not joined)" if n and n > 1 else ""


def _frame_target(objs):
    """(container, error) for a boolean of `objs` (MCP FREECAD v2).

    A Part boolean reads each operand's shape in the frame of the container that
    holds it (App::Part, Body...), not in the document's: a common of a box in an
    App::Part moved by 100 mm and a free box it overlaps by 500 mm3 came out EMPTY,
    and the cut of one by the other "removed NOTHING". The result is therefore made
    inside the operands' shared container; operands in containers placed
    differently are refused. (None, None) when the root of the document is right,
    or when that cannot be known (unit-test mocks)."""
    parents = []
    for o in objs:
        try:
            if not isinstance(o, FreeCAD.DocumentObject):
                return None, None
            parents.append(o.getParentGeoFeatureGroup())
        except Exception:
            return None, None

    names = {getattr(p, 'Name', None) for p in parents}
    shared = parents[0] if len(names) == 1 else None
    if shared is not None and shared.isDerivedFrom('App::Part'):    # a Body refuses Part features
        return shared, None
    if all(p is None or p.getGlobalPlacement().isIdentity() for p in parents):
        return None, None

    def where(o, p):
        if p is None:
            return f"{o.Name} at the document root"
        b = p.getGlobalPlacement().Base
        return f"{o.Name} in {p.Name} (placed at ({b.x:g}, {b.y:g}, {b.z:g}))"
    return None, ("Cannot make this boolean: " + ", ".join(where(o, p) for o, p in zip(objs, parents))
                  + ". A Part boolean reads each shape in its own container's frame, so the result "
                  "would be wrong. Put the operands in the same container (or use the Body / "
                  "container itself as the operand).")


class BooleanOpsHandler(BaseHandler):
    """Handler for boolean operations (fuse, cut, common)."""

    def fuse_objects(self, args: dict[str, Any]) -> str:
        """Fuse (union) multiple objects together."""
        try:
            objects = args.get('objects', [])
            name = args.get('name', 'Fusion')

            if len(objects) < 2:
                return "Need at least 2 objects to fuse"

            doc = self.get_document()
            if not doc:
                return NO_ACTIVE_DOCUMENT_ERROR

            # Get object references
            objs = []
            for obj_name in objects:
                _, obj, err = self.resolve_object(obj_name, doc)
                if err:
                    return err
                objs.append(obj)

            container, err = _frame_target(objs)
            if err:
                return err

            # Safety: warn on high complexity, save before risky op
            warning = self.check_complexity(objs)
            if warning:
                FreeCAD.Console.PrintWarning(f"[MCP] {warning}\n")
            self.save_before_risky_op(doc)

            # Create fusion and hide sources
            fusion = doc.addObject("Part::MultiFuse", name)
            fusion.Label = name
            fusion.Shapes = objs
            if container is not None:
                container.addObject(fusion)
            self.recompute(doc)
            # Verify the boolean produced a real shape BEFORE hiding the sources —
            # OCCT can yield a null shape (non-manifold/coincident geometry) without
            # raising, which would otherwise leave the user with a hidden, empty result.
            if _no_material(fusion, objs):
                doc.removeObject(fusion.Name)
                return ("Fusion produced an empty/invalid shape — sources left visible, "
                        "result not kept (check for non-manifold or coincident geometry)")
            for obj in objs:
                obj.Visibility = False

            v = _volume(fusion)
            return (f"Created fusion: {fusion.Name} from {len(objects)} objects"
                    + (f" (inside {container.Name}, their container)" if container is not None else "")
                    + (f", volume {v:.6g} mm³" if v is not None else "") + _pieces(fusion))

        except Exception as e:
            return f"Error fusing objects: {e}"

    def cut_objects(self, args: dict[str, Any]) -> str:
        """Cut (subtract) tools from base object."""
        try:
            base = args.get('base', '')
            tools = args.get('tools', [])
            name = args.get('name', 'Cut')

            if not base or not tools:
                return "Need base object and tool objects"

            doc = self.get_document()
            if not doc:
                return NO_ACTIVE_DOCUMENT_ERROR

            # Get object references
            _, base_obj, err = self.resolve_object(base, doc, noun='Base object')
            if err:
                return err

            tool_objs = []
            for tool_name in tools:
                _, tool_obj, err = self.resolve_object(tool_name, doc, noun='Tool object')
                if err:
                    return err
                tool_objs.append(tool_obj)

            container, err = _frame_target([base_obj] + tool_objs)
            if err:
                return err

            # Safety: warn on high complexity, save before risky op.
            # fuse_objects/common_objects both do this; cut_objects had
            # been missing it -- an inconsistent safety gate for arguably
            # the boolean most likely to be run with many tool objects.
            warning = self.check_complexity([base_obj] + tool_objs)
            if warning:
                FreeCAD.Console.PrintWarning(f"[MCP] {warning}\n")
            self.save_before_risky_op(doc)

            # Create cut and hide sources. Part::Cut.Tool is a single reference
            # (assigning a list raises "Type must be App.DocumentObject or None,
            # not list"), so fuse multiple tools into one before cutting.
            cut = doc.addObject("Part::Cut", name)
            cut.Label = name
            cut.Base = base_obj
            made = [cut]
            if len(tool_objs) == 1:
                cut.Tool = tool_objs[0]
            else:
                fusion = doc.addObject("Part::MultiFuse", f"{name}_Tools")
                fusion.Shapes = tool_objs
                cut.Tool = fusion
                made.append(fusion)
            if container is not None:
                for o in made:
                    container.addObject(o)
            self.recompute(doc)
            if _no_material(cut, [base_obj]):
                for o in made:
                    doc.removeObject(o.Name)
                return ("Cut produced an empty/invalid shape — sources left visible, result not kept "
                        "(the tools may fully consume the base, or geometry is degenerate)")
            base_obj.Visibility = False
            for obj in tool_objs:
                obj.Visibility = False

            # MCP FREECAD v2: say what the cut removed -- a tool placed beside the
            # base removed nothing and was replied "Created cut"
            inside = f" (inside {container.Name}, their container)" if container is not None else ""
            v0, v1 = _volume(base_obj), _volume(cut)
            if v0 is None or v1 is None:
                return f"Created cut: {cut.Name}{inside} from {base} minus {len(tools)} tools"
            if v0 - v1 <= 1e-9 * max(v0, 1.0):
                return (f"Created cut: {cut.Name}{inside}, but it removed NOTHING: the tool(s) do not "
                        f"overlap {base} (check their placement)")
            return (f"Created cut: {cut.Name}{inside} from {base} minus {len(tools)} tools: "
                    f"removed {v0 - v1:.6g} mm³, volume {v1:.6g} mm³" + _pieces(cut))

        except Exception as e:
            return f"Error cutting objects: {e}"

    def common_objects(self, args: dict[str, Any]) -> str:
        """Find intersection of multiple objects."""
        try:
            objects = args.get('objects', [])
            name = args.get('name', 'Common')

            if len(objects) < 2:
                return "Need at least 2 objects for intersection"

            doc = self.get_document()
            if not doc:
                return NO_ACTIVE_DOCUMENT_ERROR

            # Get object references
            objs = []
            for obj_name in objects:
                _, obj, err = self.resolve_object(obj_name, doc)
                if err:
                    return err
                objs.append(obj)

            container, err = _frame_target(objs)
            if err:
                return err

            # Safety: warn on high complexity, save before risky op
            warning = self.check_complexity(objs)
            if warning:
                FreeCAD.Console.PrintWarning(f"[MCP] {warning}\n")
            self.save_before_risky_op(doc)

            # Create common and hide sources
            common = doc.addObject("Part::MultiCommon", name)
            common.Label = name
            common.Shapes = objs
            if container is not None:
                container.addObject(common)
            self.recompute(doc)
            # An empty intersection is a legitimate geometric answer (no overlap),
            # but hiding the sources and reporting success would hide that fact.
            if _no_material(common, objs):
                doc.removeObject(common.Name)
                return ("Intersection is empty — the objects do not overlap (or only touch); "
                        "sources left visible, result not kept")
            for obj in objs:
                obj.Visibility = False

            v = _volume(common)
            return (f"Created intersection: {common.Name} from {len(objects)} objects"
                    + (f" (inside {container.Name}, their container)" if container is not None else "")
                    + (f", volume {v:.6g} mm³" if v is not None else ""))

        except Exception as e:
            return f"Error finding intersection: {e}"
