"""Unit tests for PartOpsHandler.

Covers extrude, revolve, mirror, scale, section, loft, sweep, compound,
shape_string, and check_geometry. The biggest of the part-umbrella
handlers (478 LOC) — all 10 operations exercised by at least one unit
test, with real assertions on dispatch routing, parameter assembly,
and error paths.
"""

import unittest
from unittest.mock import MagicMock, patch

from handlers.part_ops import PartOpsHandler

from tests.unit._freecad_mocks import (
    _Placement,
    _Rotation,
    _Vec,
    assert_error_contains,
    assert_success_contains,
    make_box_object,
    make_cylinder_object,
    make_handler,
    make_mock_doc,
    make_part_object,
    make_sketch,
    make_sphere_object,
    mock_FreeCAD,
    mock_Part,
    reset_mocks,
)


def _make_next_addobject_invalid(doc, type_id):
    """Make the next doc.addObject(type_id, ...) call return an object with
    State=['Invalid'], preserving make_mock_doc's normal _add_object setup
    (appends to doc.Objects, sets Shape defaults) for every other call.
    Used to test the post-recompute Invalid-state check that loft/sweep
    inherit from BaseHandler._check_feature_state (H13 consolidation —
    previously PartDesignOpsHandler-only, now shared)."""
    original = doc.addObject.side_effect

    def _wrapped(t, name=None):
        obj = original(t, name)
        if t == type_id:
            obj.State = ['Invalid']
        return obj

    doc.addObject.side_effect = _wrapped


class TestExtrude(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.extrude({'profile_sketch': 'S', 'height': 10})
        assert_error_contains(self, result, "no active document")

    def test_missing_sketch(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.extrude({'profile_sketch': 'Ghost', 'height': 10})
        assert_error_contains(self, result, "ghost", "not found")

    def test_extrude_z_default_direction(self):
        sketch = make_sketch("RectS", has_wires=True)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.extrude({
            'profile_sketch': 'RectS', 'height': 10,
        })

        assert_success_contains(self, result, "RectS", "10", "z")
        # Result object created with Part::Feature TypeId
        doc.addObject.assert_called()
        last_call = doc.addObject.call_args_list[-1]
        self.assertEqual(last_call.args[0], "Part::Feature")
        self.assertIn("extruded", last_call.args[1])

    def test_extrude_x_direction(self):
        sketch = make_sketch("RectS", has_wires=True)
        sketch.Placement = _Placement(rotation=_Rotation(_Vec(0, 1, 0), 90))   # YZ plane: normal x
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.extrude({
            'profile_sketch': 'RectS', 'height': 5, 'direction': 'x',
        })

        assert_success_contains(self, result, "Extruded RectS by 5mm in x direction")

    def test_extrude_uppercase_direction_not_silently_z(self):
        """'X' must extrude in X — extrude used to be case-sensitive and would
        silently fall through to Z for any non-lowercase value."""
        sketch = make_sketch("RectS", has_wires=True)
        sketch.Placement = _Placement(rotation=_Rotation(_Vec(0, 1, 0), 90))   # YZ plane: normal x
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.extrude({
            'profile_sketch': 'RectS', 'height': 5, 'direction': 'X',
        })
        assert_success_contains(self, result, "in x direction")

    def test_extrude_invalid_direction_errors(self):
        """An unrecognized direction must error, not silently extrude in Z."""
        sketch = make_sketch("RectS", has_wires=True)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.extrude({
            'profile_sketch': 'RectS', 'height': 5, 'direction': 'diagonal',
        })
        self.assertIn("Invalid direction", result)

    def test_extrude_in_the_sketch_plane_is_refused(self):
        """MCP FREECAD v2: 'x' on an XY sketch lies in its plane (no volume)."""
        sketch = make_sketch("RectS", has_wires=True)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.extrude({
            'profile_sketch': 'RectS', 'height': 5, 'direction': 'x',
        })
        assert_error_contains(self, result, "lies in the plane")

    def test_extrude_uses_face_path_when_only_faces(self):
        """When sketch has no Wires but has Faces, fall back to Shape.extrude."""
        sketch = make_sketch("S", has_wires=False, has_faces=True)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.extrude({'profile_sketch': 'S', 'height': 10})

        assert_success_contains(self, result, "S")
        # Shape.extrude was called (face path)
        sketch.Shape.extrude.assert_called()

    def test_extrude_no_wires_or_faces_errors(self):
        sketch = make_sketch("Empty", has_wires=False, has_faces=False)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.extrude({'profile_sketch': 'Empty', 'height': 10})

        assert_error_contains(self, result, "no valid", "wires", "faces")


class TestRevolve(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_revolve_full_circle(self):
        # An XY sketch (the mock's plane) revolves around an axis lying in it:
        # 'y'. This test used 'z' -- the sketch's normal, a zero-volume result.
        sketch = make_sketch("RevS", has_wires=True)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.revolve({
            'profile_sketch': 'RevS', 'angle': 360, 'axis': 'y',
        })

        assert_success_contains(self, result, "RevS", "360", "y", "RevS_revolved")

    def test_revolve_around_the_sketch_normal_is_refused(self):
        """MCP FREECAD v2 (2026-10-30): an XY sketch revolved around z turns
        within its own plane -- a "solid" of volume 0 replied "Revolved ... by
        360°"."""
        sketch = make_sketch("RevS", has_wires=True)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.revolve({
            'profile_sketch': 'RevS', 'angle': 360, 'axis': 'z',
        })

        assert_error_contains(self, result, "invalid axis", "normal")
        doc.addObject.assert_not_called()

    def test_revolve_angle_out_of_range_is_refused(self):
        """720 silently gave a 360° solid; 0 failed with an OCCT message."""
        sketch = make_sketch("RevS", has_wires=True)
        doc = make_mock_doc([sketch])
        mock_FreeCAD.ActiveDocument = doc
        for angle in (0, 720, -400):
            result = self.handler.revolve({
                'profile_sketch': 'RevS', 'angle': angle, 'axis': 'y',
            })
            assert_error_contains(self, result, "invalid angle")
        doc.addObject.assert_not_called()

    def test_missing_sketch(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.revolve({'profile_sketch': 'Nope'})
        assert_error_contains(self, result, "not found")


class TestMirror(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_mirror_yz_plane(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.mirror_object({
            'object_name': 'B', 'plane': 'YZ',
        })

        assert_success_contains(self, result, "B", "YZ")
        # Shape.mirror was called
        box.Shape.mirror.assert_called_once()

    def test_invalid_plane_errors(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.mirror_object({
            'object_name': 'B', 'plane': 'XYZ',
        })
        assert_error_contains(self, result, "invalid plane")

    def test_object_without_shape_errors(self):
        obj = MagicMock()
        obj.Name = "Datum"
        obj.Label = "Datum"
        del obj.Shape
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.mirror_object({
            'object_name': 'Datum', 'plane': 'YZ',
        })
        # Message now comes from resolve_object(attr='Shape') (full-review
        # 2026-09-23, Low finding #68) instead of a hand-written check --
        # wording changed from "not a shape object" to "has no Shape property".
        assert_error_contains(self, result, "no Shape property")


class TestScaleObject(unittest.TestCase):
    """Scale tests check the result string rather than post-state.

    The handler does ``obj.Length = obj.Length.Value * factor`` and
    later reads ``obj.Length.Value`` again to format the new-dims
    string. In real FreeCAD the property setter wraps the float back
    into a Quantity that retains .Value; a plain MagicMock cannot
    fake that descriptor without significant complexity. The result
    string contains both old and new dimensions, so success-text
    assertions exercise the same code path.
    """

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_scale_box_routes_to_box_branch(self):
        """Parametric Box: result reports old → new dimensions."""
        box = make_box_object("B", length=10, width=20, height=30)
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.scale_object({
            'object_name': 'B', 'scale_factor': 2.0,
        })

        # The box branch reads Length.Value once before assignment, so
        # at minimum the old dimensions appear in the result.
        assert_success_contains(self, result, "B", "factor 2",
                                "10.0", "20.0", "30.0")

    def test_scale_cylinder_routes_to_cylinder_branch(self):
        cyl = make_cylinder_object("C", radius=5, height=10)
        doc = make_mock_doc([cyl])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.scale_object({
            'object_name': 'C', 'scale_factor': 3.0,
        })

        assert_success_contains(self, result, "C", "factor 3", "R5", "H10")

    def test_scale_sphere_routes_to_sphere_branch(self):
        sphere = make_sphere_object("S", radius=4)
        doc = make_mock_doc([sphere])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.scale_object({
            'object_name': 'S', 'scale_factor': 0.5,
        })

        assert_success_contains(self, result, "S", "factor 0.5", "R4")

    def test_scale_missing_object(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.scale_object({
            'object_name': 'Ghost', 'scale_factor': 2.0,
        })
        assert_error_contains(self, result, "not found")

    def test_scale_factor_non_positive_rejected(self):
        """scale_factor <= 0 inverts/zeroes geometry on the non-parametric path."""
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.scale_object({
            'object_name': 'X', 'scale_factor': 0,
        })
        self.assertIn("scale_factor must be > 0", result)


class TestSection(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_section_at_xy_plane(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.section({
            'object_name': 'B', 'plane': 'XY', 'offset': 5,
        })

        assert_success_contains(self, result, "B", "XY", "5")

    def test_invalid_plane_errors(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.section({
            'object_name': 'B', 'plane': 'BAD',
        })
        assert_error_contains(self, result, "invalid plane")


class TestLoft(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_loft_two_sketches_creates_part_loft(self):
        s1 = make_sketch("S1")
        s2 = make_sketch("S2")
        doc = make_mock_doc([s1, s2])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.loft({
            'sketches': ['S1', 'S2'], 'name': 'MyLoft',
        })

        assert_success_contains(self, result, "MyLoft", "2 profiles")
        doc.addObject.assert_called_with("Part::Loft", "MyLoft")
        loft = doc.Objects[-1]
        self.assertEqual(list(loft.Sections), [s1, s2])

    def test_loft_needs_at_least_two_sketches(self):
        doc = make_mock_doc([make_sketch("Only")])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.loft({'sketches': ['Only']})
        assert_error_contains(self, result, "at least 2")

    def test_invalid_state_errors_instead_of_false_success(self):
        """recompute() never raises on geometry failure — it marks
        feature.State Invalid instead. Part::Loft shares this FreeCAD
        mechanism with PartDesign features, so it must get the same
        post-recompute check (H13: previously only PartDesignOpsHandler's
        loft_profiles had this; Part::Loft could silently report success
        on a self-intersecting/degenerate loft)."""
        s1, s2 = make_sketch("S1"), make_sketch("S2")
        doc = make_mock_doc([s1, s2])
        mock_FreeCAD.ActiveDocument = doc
        _make_next_addobject_invalid(doc, "Part::Loft")

        result = self.handler.loft({'sketches': ['S1', 'S2'], 'name': 'L'})

        assert_error_contains(self, result, "invalid")


class TestSweep(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_sweep_creates_part_sweep_with_profile_and_path(self):
        profile = make_sketch("Profile")
        path = make_sketch("Path")
        doc = make_mock_doc([profile, path])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.sweep({
            'profile_sketch': 'Profile', 'path_sketch': 'Path',
        })

        assert_success_contains(self, result, "Profile", "Path")
        doc.addObject.assert_called_with("Part::Sweep", "Sweep")
        sweep = doc.Objects[-1]
        self.assertEqual(list(sweep.Sections), [profile])
        self.assertEqual(sweep.Spine, path)

    def test_missing_profile(self):
        doc = make_mock_doc([make_sketch("Path")])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.sweep({
            'profile_sketch': 'NoProfile', 'path_sketch': 'Path',
        })
        assert_error_contains(self, result, "profile", "not found")

    def test_invalid_state_errors_instead_of_false_success(self):
        profile = make_sketch("Profile")
        path = make_sketch("Path")
        doc = make_mock_doc([profile, path])
        mock_FreeCAD.ActiveDocument = doc
        _make_next_addobject_invalid(doc, "Part::Sweep")

        result = self.handler.sweep({
            'profile_sketch': 'Profile', 'path_sketch': 'Path',
        })

        assert_error_contains(self, result, "invalid")


class TestCompound(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_compound_two_boxes(self):
        a = make_box_object("A")
        b = make_box_object("B")
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.compound({
            'objects': ['A', 'B'], 'name': 'Combo',
        })

        assert_success_contains(self, result, "Combo", "2 objects")
        # Part.makeCompound was called with the shapes
        mock_Part.makeCompound.assert_called_once()
        args = mock_Part.makeCompound.call_args
        shapes = args.args[0]
        self.assertEqual(shapes, [a.Shape, b.Shape])

    def test_compound_needs_at_least_two_objects(self):
        a = make_box_object("A")
        doc = make_mock_doc([a])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.compound({'objects': ['A']})
        assert_error_contains(self, result, "at least 2")


class TestCheckGeometry(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_reports_validity_and_counts(self):
        """check_geometry reports Valid, Solids, Faces, Edges, Vertices."""
        box = make_box_object("B")
        # _make_shape defaults: 6 faces, 12 edges, 8 vertices, 1 solid
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.check_geometry({'object_name': 'B'})

        assert_success_contains(self, result,
                                "Valid: True",
                                "Solids: 1",
                                "Faces: 6",
                                "Edges: 12",
                                "Vertices: 8")

    def test_reports_invalid_geometry(self):
        box = make_box_object("B")
        box.Shape.isValid = MagicMock(return_value=False)
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.check_geometry({'object_name': 'B'})

        assert_success_contains(self, result, "Valid: False")

    def test_no_shape_errors(self):
        obj = MagicMock()
        obj.Name = "X"
        obj.Label = "X"
        del obj.Shape
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.check_geometry({'object_name': 'X'})
        assert_error_contains(self, result, "no shape")


class TestShapeString(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_no_font_errors_helpfully(self):
        """Without a usable font, shape_string returns guidance, not a crash."""
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        # Force find_font to return ''
        with patch.object(self.handler, 'find_font', return_value=''):
            result = self.handler.shape_string({'string': 'Hi'})
        assert_error_contains(self, result, "font", ".ttf")

    def test_creates_part_feature_with_compound_shape(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        # makeWireString: list of [list of wires] per character
        wire = MagicMock()
        mock_Part.makeWireString = MagicMock(return_value=[[wire], [wire, wire]])
        compound = MagicMock()
        mock_Part.makeCompound = MagicMock(return_value=compound)
        with patch.object(self.handler, 'find_font',
                          return_value='/fake/Arial.ttf'):
            result = self.handler.shape_string({
                'string': 'Hi', 'size': 10, 'name': 'Label',
            })

        assert_success_contains(self, result, "Hi", "Label", "Arial.ttf")
        # 2 chars × 1+2 wires = 3 wires flattened
        mock_Part.makeCompound.assert_called_once()
        flat = mock_Part.makeCompound.call_args.args[0]
        self.assertEqual(len(flat), 3)


def _next_result_shape(doc, type_id, solids, shape_type="Solid", valid=True, volume=0.0):
    """Give the next doc.addObject(type_id) a real-looking shape."""
    original = doc.addObject.side_effect

    def add(t, name=None):
        obj = original(t, name)
        if t == type_id:
            obj.State = []
            obj.Shape.Solids = [MagicMock() for _ in range(solids)]
            obj.Shape.ShapeType = shape_type
            obj.Shape.isValid = MagicMock(return_value=valid)
            obj.Shape.Volume = volume
        return obj
    doc.addObject.side_effect = add


class TestLoftSweepNotASolid(unittest.TestCase):
    """MCP FREECAD v2 (2026-10-30): a solid loft of open profiles is an invalid
    "solid" of volume 0, a solid sweep of an open profile a mere shell; both
    computed with a fine State and were replied "Created ..."."""

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def test_invalid_zero_volume_loft_is_refused_and_removed(self):
        doc = make_mock_doc([make_sketch("S1"), make_sketch("S2")])
        mock_FreeCAD.ActiveDocument = doc
        _next_result_shape(doc, "Part::Loft", solids=1, valid=False, volume=0.0)

        result = self.handler.loft({'sketches': ['S1', 'S2'], 'name': 'L'})

        assert_error_contains(self, result, "invalid solid", "open", "solid=false", "not kept")
        doc.removeObject.assert_called_once_with("L")

    def test_shell_sweep_is_refused_and_removed(self):
        doc = make_mock_doc([make_sketch("P"), make_sketch("Path")])
        mock_FreeCAD.ActiveDocument = doc
        _next_result_shape(doc, "Part::Sweep", solids=0, shape_type="Shell")

        result = self.handler.sweep({'profile_sketch': 'P', 'path_sketch': 'Path'})

        assert_error_contains(self, result, "a shell, not a solid", "not kept")
        doc.removeObject.assert_called_once_with("Sweep")

    def test_surface_requested_is_not_judged(self):
        doc = make_mock_doc([make_sketch("P"), make_sketch("Path")])
        mock_FreeCAD.ActiveDocument = doc
        _next_result_shape(doc, "Part::Sweep", solids=0, shape_type="Shell")

        result = self.handler.sweep({'profile_sketch': 'P', 'path_sketch': 'Path', 'solid': False})

        assert_success_contains(self, result, "Created sweep")
        doc.removeObject.assert_not_called()

    def test_real_solid_loft_passes(self):
        doc = make_mock_doc([make_sketch("S1"), make_sketch("S2")])
        mock_FreeCAD.ActiveDocument = doc
        _next_result_shape(doc, "Part::Loft", solids=1, volume=1570.8)

        result = self.handler.loft({'sketches': ['S1', 'S2']})

        assert_success_contains(self, result, "Created loft")


class TestScaleByType(unittest.TestCase):
    """MCP FREECAD v2 (2026-10-30): the parametric branches were chosen by
    property names (a Part::Helix kept its pitch: 10 turns instead of 5; a
    PartDesign Fillet only got a bigger radius), and the scaled copy went
    through transformGeometry (B-splines: a cone 3293.48 mm3 instead of
    3267.26)."""

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(PartOpsHandler)

    def _scaled_copy(self, type_id):
        obj = make_part_object("Obj")
        obj.TypeId = type_id
        obj.Radius = 5.0
        obj.Height = 10.0
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.scale_object({'object_name': 'Obj', 'scale_factor': 2})
        return obj, result

    def test_helix_is_scaled_as_a_whole_shape(self):
        obj, result = self._scaled_copy("Part::Helix")
        assert_success_contains(self, result, "scaled copy", "Obj_scaled", "document origin")
        self.assertEqual(obj.Radius, 5.0)       # not "R10" with the pitch unchanged
        obj.Shape.scale.assert_called_once_with(2.0, _Vec(0, 0, 0))
        obj.Shape.transformGeometry.assert_not_called()

    def test_partdesign_fillet_is_not_scaled_by_its_radius(self):
        obj, result = self._scaled_copy("PartDesign::Fillet")
        self.assertEqual(obj.Radius, 5.0)
        obj.Shape.scale.assert_called_once()


if __name__ == '__main__':
    unittest.main()
