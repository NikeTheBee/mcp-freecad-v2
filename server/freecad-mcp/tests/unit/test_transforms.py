"""Unit tests for TransformsHandler.

Covers move_object (relative + absolute, including the 4247599 fix),
rotate_object, copy_object, and array_object. Smallest of the part-
umbrella handlers (95 LOC) — used as a pilot to validate the shared-
helpers approach before expanding to primitives, boolean_ops, part_ops.
"""

import unittest
from unittest.mock import MagicMock, patch

from handlers.transforms import TransformsHandler

from tests.unit._freecad_mocks import (
    _Placement,
    _Rotation,
    _Vec,
    assert_error_contains,
    assert_success_contains,
    make_box_object,
    make_handler,
    make_mock_doc,
    mock_FreeCAD,
    reset_mocks,
)


class TestMoveObject(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(TransformsHandler)

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.move_object({'object_name': 'Box', 'x': 10})
        assert_error_contains(self, result, "no active document")

    def test_missing_object(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.move_object({'object_name': 'Ghost', 'x': 10})
        assert_error_contains(self, result, "object not found", "ghost")

    def test_relative_move_adds_to_position(self):
        """Default behavior: relative=True adds offset to current Placement.Base."""
        box = make_box_object("B")
        box.Placement = _Placement(_Vec(5, 5, 5))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.move_object({
            'object_name': 'B', 'x': 10, 'y': -2, 'z': 3,
        })

        assert_success_contains(self, result, "Moved B by (10, -2, 3)")
        # 5+10, 5-2, 5+3
        self.assertEqual(box.Placement.Base.x, 15.0)
        self.assertEqual(box.Placement.Base.y, 3.0)
        self.assertEqual(box.Placement.Base.z, 8.0)

    def test_absolute_move_replaces_position(self):
        """relative=False sets Placement.Base directly (commit 4247599)."""
        box = make_box_object("B")
        box.Placement = _Placement(_Vec(5, 5, 5))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.move_object({
            'object_name': 'B', 'x': 100, 'y': 0, 'z': 0,
            'relative': False,
        })

        assert_success_contains(self, result, "Moved B to (100, 0, 0)")
        # Replaced, not added
        self.assertEqual(box.Placement.Base.x, 100.0)
        self.assertEqual(box.Placement.Base.y, 0.0)
        self.assertEqual(box.Placement.Base.z, 0.0)

    def test_label_fallback_used_for_lookup(self):
        """get_object label fallback reaches transform handlers (e62ebc5)."""
        box = make_box_object("Box001")  # internal name
        box.Label = "MyBox"
        box.Placement = _Placement(_Vec(0, 0, 0))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.move_object({
            'object_name': 'MyBox', 'x': 5, 'y': 0, 'z': 0,
        })

        assert_success_contains(self, result, "Moved MyBox")
        self.assertEqual(box.Placement.Base.x, 5.0)


class TestRotateObject(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(TransformsHandler)

    def test_rotate_around_z_default(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.rotate_object({
            'object_name': 'B', 'angle': 45,
        })
        assert_success_contains(self, result, "Rotated B by 45", "Z")

    def test_rotate_around_x(self):
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.rotate_object({
            'object_name': 'B', 'angle': 90, 'axis': 'x',
        })
        assert_success_contains(self, result, "X")

    def test_missing_object(self):
        doc = make_mock_doc([])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.rotate_object({
            'object_name': 'Ghost', 'angle': 45,
        })
        assert_error_contains(self, result, "not found")

    def test_invalid_axis_rejected_instead_of_silent_default(self):
        """An unrecognized axis used to silently rotate around Z while the
        success message still echoed the requested axis string, e.g.
        axis="q" reporting "around Q-axis" while actually rotating Z."""
        box = make_box_object("B")
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.rotate_object({
            'object_name': 'B', 'angle': 90, 'axis': 'q',
        })

        assert_error_contains(self, result, "invalid axis", "q")


class TestCopyObject(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(TransformsHandler)

    def test_copy_with_offset(self):
        box = make_box_object("B")
        box.Placement = _Placement(_Vec(0, 0, 0))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.copy_object({
            'object_name': 'B', 'name': 'BCopy', 'x': 20, 'y': 0, 'z': 0,
        })

        assert_success_contains(self, result, "copy", "20")
        # copyObject was called once, with_dependencies=True so parametric/
        # body-backed objects copy their dep chain instead of referencing the source
        doc.copyObject.assert_called_once_with(box, True)
        # The new copy's Label is set to the requested name.
        # Our make_mock_doc uses side_effect, not return_value;
        # the copy is the last addObject result
        self.assertGreaterEqual(len(doc.Objects), 2)
        new_obj = doc.Objects[-1]
        self.assertEqual(new_obj.Label, 'BCopy')


class TestArrayObject(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(TransformsHandler)

    def test_array_count_creates_n_minus_1_copies(self):
        """count=4 means original + 3 new copies (4 total)."""
        box = make_box_object("B")
        box.Label = "B"
        box.Placement = _Placement(_Vec(0, 0, 0))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.array_object({
            'object_name': 'B', 'count': 4,
            'spacing_x': 15, 'spacing_y': 0, 'spacing_z': 0,
        })

        # Message now reports "original + N copies" (N = count-1) instead
        # of overstating the new-object count as "{count} copies" (full-
        # review 2026-09-23, Low finding #62).
        assert_success_contains(self, result, "array", "3")
        # 3 new copies (original is in array but not duplicated)
        self.assertEqual(doc.copyObject.call_count, 3)
        # Original + 3 copies = 4 objects total
        self.assertEqual(len(doc.Objects), 4)

    def test_array_spacing_x_only_moves_in_x(self):
        """spacing_y=spacing_z=0 ⇒ y and z of copies stay at 0."""
        box = make_box_object("B")
        box.Label = "B"
        box.Placement = _Placement(_Vec(0, 0, 0))
        doc = make_mock_doc([box])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.array_object({
            'object_name': 'B', 'count': 3,
            'spacing_x': 10, 'spacing_y': 0, 'spacing_z': 0,
        })

        # Two new copies at (10,0,0) and (20,0,0)
        copies = [o for o in doc.Objects if o is not box]
        self.assertEqual(len(copies), 2)
        xs = sorted(c.Placement.Base.x for c in copies)
        self.assertEqual(xs, [10.0, 20.0])
        for c in copies:
            self.assertEqual(c.Placement.Base.y, 0.0)
            self.assertEqual(c.Placement.Base.z, 0.0)


class _TPlacement:
    """Translation-only placement with the algebra of the attachment path."""

    def __init__(self, base=None, rotation=None):
        self.Base = base if base is not None else _Vec()
        self.Rotation = rotation if rotation is not None else _Rotation()

    def multiply(self, other):
        return _TPlacement(self.Base + other.Base, self.Rotation)

    def inverse(self):
        return _TPlacement(_Vec(-self.Base.x, -self.Base.y, -self.Base.z), self.Rotation)


class TestAttachedObjects(unittest.TestCase):
    """MCP FREECAD v2 (2026-10-30): an attached sketch's Placement is recomputed
    from its attachment, so "Moved S by (0, 0, 10)" / "Rotated S by 90°" left it
    where it was. The change now goes through AttachmentOffset."""

    def setUp(self):
        reset_mocks()
        self.handler = TransformsHandler()

    @staticmethod
    def _attached(placement_base, offset_base):
        obj = make_box_object("S")
        obj.MapMode = "FlatFace"
        obj.Placement = _TPlacement(placement_base)
        obj.AttachmentOffset = _TPlacement(offset_base)
        return obj

    def test_move_goes_through_the_attachment_offset(self):
        # attached at (2, 0, 0) with an offset of (3, 0, 0): placed at (5, 0, 0)
        obj = self._attached(_Vec(5, 0, 0), _Vec(3, 0, 0))
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.move_object({'object_name': 'S', 'x': 10, 'y': 0, 'z': 0})

        assert_success_contains(self, result, "attachment offset")
        self.assertEqual(obj.AttachmentOffset.Base, _Vec(13, 0, 0))
        self.assertEqual(obj.MapMode, "FlatFace")

    def test_absolute_move_goes_through_the_attachment_offset(self):
        obj = self._attached(_Vec(5, 0, 0), _Vec(3, 0, 0))
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.move_object({'object_name': 'S', 'x': 0, 'y': 0, 'z': 7, 'relative': False})

        self.assertEqual(obj.AttachmentOffset.Base, _Vec(-2, 0, 7))

    def test_rotate_goes_through_the_attachment_offset(self):
        obj = self._attached(_Vec(5, 0, 0), _Vec(3, 0, 0))
        rotation_before = obj.Placement.Rotation
        doc = make_mock_doc([obj])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.rotate_object({'object_name': 'S', 'axis': 'z', 'angle': 90})

        assert_success_contains(self, result, "attachment offset")
        self.assertIs(obj.Placement.Rotation, rotation_before)
        self.assertIsInstance(obj.AttachmentOffset, _TPlacement)

    def test_a_placement_the_recompute_puts_back_is_reported(self):
        """A PartDesign Pad is placed by FreeCAD: "Moved Pad by (10, 0, 0)" was
        replied while the recompute put it back."""
        box = make_box_object("Pad")
        box.Placement = _Placement(_Vec(0, 0, 0))
        doc = make_mock_doc([box])
        doc.recompute.side_effect = lambda: setattr(box.Placement, 'Base', _Vec(0, 0, 0))
        mock_FreeCAD.ActiveDocument = doc

        with patch.object(mock_FreeCAD, 'DocumentObject', MagicMock, create=True):
            result = self.handler.move_object({'object_name': 'Pad', 'x': 10, 'y': 0, 'z': 0})

        assert_error_contains(self, result, "did not move", "its sketch, its body")


if __name__ == '__main__':
    unittest.main()
