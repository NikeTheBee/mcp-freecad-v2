"""Unit tests for BooleanOpsHandler.

Covers fuse (Part::MultiFuse), cut (Part::Cut), common (Part::MultiCommon).
Boolean ops are the highest-traffic operations in the server — every
non-trivial assembly goes through them. Tests verify TypeId routing,
shape parameter assembly, source visibility hiding, and error paths
(insufficient operands, missing objects).
"""

import unittest
from unittest.mock import MagicMock, patch

from handlers.boolean_ops import BooleanOpsHandler

from tests.unit._freecad_mocks import (
    assert_error_contains,
    assert_success_contains,
    make_box_object,
    make_handler,
    make_mock_doc,
    mock_FreeCAD,
    reset_mocks,
)
from tests.unit._freecad_mocks import _Vec


class TestFuseObjects(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(BooleanOpsHandler)

    def test_needs_at_least_two_objects(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.fuse_objects({'objects': ['OnlyOne']})
        assert_error_contains(self, result, "at least 2")

    def test_no_active_document(self):
        mock_FreeCAD.ActiveDocument = None
        result = self.handler.fuse_objects({'objects': ['A', 'B']})
        assert_error_contains(self, result, "no active document")

    def test_missing_object_aborts(self):
        a = make_box_object("A")
        doc = make_mock_doc([a])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.fuse_objects({'objects': ['A', 'GhostB']})
        assert_error_contains(self, result, "not found", "ghostb")

    def test_creates_multifuse_with_shapes_and_hides_sources(self):
        a = make_box_object("A")
        b = make_box_object("B")
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.fuse_objects({
            'objects': ['A', 'B'], 'name': 'Union',
        })

        assert_success_contains(self, result, "Union", "2 objects")
        doc.addObject.assert_called_with("Part::MultiFuse", "Union")
        fusion = doc.Objects[-1]
        self.assertEqual(list(fusion.Shapes), [a, b])
        # Sources hidden after fuse
        self.assertFalse(a.Visibility)
        self.assertFalse(b.Visibility)

    def test_fuse_null_result_keeps_sources_visible(self):
        """If the boolean yields a null shape (non-manifold/coincident geometry),
        the sources must stay visible and the handler must report failure — not
        hide them behind an empty result."""
        a = make_box_object("A")
        b = make_box_object("B")
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc
        base_add = doc.addObject.side_effect

        def add_null(type_id, name=None):
            obj = base_add(type_id, name)
            obj.Shape.isNull = MagicMock(return_value=True)
            return obj
        doc.addObject.side_effect = add_null

        result = self.handler.fuse_objects({'objects': ['A', 'B'], 'name': 'Union'})

        self.assertIn("empty/invalid", result)
        self.assertTrue(a.Visibility)   # sources NOT hidden on failure
        self.assertTrue(b.Visibility)

    def test_fuse_three_objects(self):
        a = make_box_object("A")
        b = make_box_object("B")
        c = make_box_object("C")
        doc = make_mock_doc([a, b, c])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.fuse_objects({
            'objects': ['A', 'B', 'C'],
        })

        assert_success_contains(self, result, "3 objects")
        fusion = doc.Objects[-1]
        self.assertEqual(len(list(fusion.Shapes)), 3)


class TestCutObjects(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(BooleanOpsHandler)

    def test_no_base_or_tools(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.cut_objects({})
        assert_error_contains(self, result, "base", "tool")

    def test_missing_base(self):
        a = make_box_object("Tool1")
        doc = make_mock_doc([a])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.cut_objects({
            'base': 'GhostBase', 'tools': ['Tool1'],
        })
        assert_error_contains(self, result, "base", "not found")

    def test_missing_tool_aborts(self):
        base = make_box_object("Base")
        doc = make_mock_doc([base])
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.cut_objects({
            'base': 'Base', 'tools': ['GhostTool'],
        })
        assert_error_contains(self, result, "tool", "not found")

    def test_single_tool_assigned_directly(self):
        """One tool: cut.Tool = tool object (not list)."""
        base = make_box_object("Base")
        tool = make_box_object("Tool")
        doc = make_mock_doc([base, tool])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.cut_objects({
            'base': 'Base', 'tools': ['Tool'], 'name': 'Hole',
        })

        cut = doc.Objects[-1]
        self.assertEqual(cut.Base, base)
        self.assertEqual(cut.Tool, tool)

    def test_multiple_tools_fused_then_cut(self):
        """>1 tool: tools are fused into a Part::MultiFuse, which becomes the
        single Cut.Tool. Assigning a list directly to Part::Cut.Tool raises
        'Type must be App.DocumentObject or None, not list' in real FreeCAD."""
        base = make_box_object("Base")
        t1 = make_box_object("T1")
        t2 = make_box_object("T2")
        doc = make_mock_doc([base, t1, t2])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.cut_objects({
            'base': 'Base', 'tools': ['T1', 'T2'],
        })

        cut = next(o for o in doc.Objects if o.TypeId == "Part::Cut")
        fusion = next(o for o in doc.Objects if o.TypeId == "Part::MultiFuse")
        self.assertEqual(cut.Base, base)
        self.assertEqual(cut.Tool, fusion)
        self.assertEqual(list(fusion.Shapes), [t1, t2])

    def test_cut_hides_base_and_tools(self):
        base = make_box_object("Base")
        tool = make_box_object("Tool")
        doc = make_mock_doc([base, tool])
        mock_FreeCAD.ActiveDocument = doc

        self.handler.cut_objects({
            'base': 'Base', 'tools': ['Tool'],
        })

        self.assertFalse(base.Visibility)
        self.assertFalse(tool.Visibility)


class TestCommonObjects(unittest.TestCase):
    def setUp(self):
        reset_mocks()
        self.handler = make_handler(BooleanOpsHandler)

    def test_needs_at_least_two_objects(self):
        doc = make_mock_doc()
        mock_FreeCAD.ActiveDocument = doc
        result = self.handler.common_objects({'objects': ['Only']})
        assert_error_contains(self, result, "at least 2")

    def test_creates_multicommon(self):
        a = make_box_object("A")
        b = make_box_object("B")
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.common_objects({
            'objects': ['A', 'B'], 'name': 'Intersection',
        })

        assert_success_contains(self, result, "Intersection", "2 objects")
        doc.addObject.assert_called_with("Part::MultiCommon", "Intersection")
        common = doc.Objects[-1]
        self.assertEqual(list(common.Shapes), [a, b])


def _results_with_solids(doc, n):
    """Booleans created from now on hold n solids (n=0: OCCT's EMPTY compound)."""
    original = doc.addObject.side_effect

    def add(type_id, name=None):
        obj = original(type_id, name)
        obj.Shape.Solids = [MagicMock() for _ in range(n)]
        obj.Shape.Volume = 1000.0 * n
        return obj
    doc.addObject.side_effect = add


class TestEmptyAndSplitResults(unittest.TestCase):
    """MCP FREECAD v2 (2026-10-30): OCCT answers a cut that swallows its base, or
    a common of disjoint solids, with an EMPTY compound -- not a null shape. The
    isNull() guard never fired: the sources were hidden and the reply said
    "Created ..., volume 0 mm3"."""

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(BooleanOpsHandler)

    def test_cut_swallowing_the_base_is_refused_and_not_kept(self):
        base, t1, t2 = make_box_object("Base"), make_box_object("T1"), make_box_object("T2")
        doc = make_mock_doc([base, t1, t2])
        mock_FreeCAD.ActiveDocument = doc
        _results_with_solids(doc, 0)

        result = self.handler.cut_objects({'base': 'Base', 'tools': ['T1', 'T2']})

        assert_error_contains(self, result, "empty", "sources left visible", "not kept")
        self.assertTrue(base.Visibility)
        self.assertTrue(t1.Visibility)
        removed = [c.args[0] for c in doc.removeObject.call_args_list]
        self.assertEqual(sorted(removed), ["Cut", "Cut_Tools"])

    def test_common_of_disjoint_solids_is_refused_and_not_kept(self):
        a, b = make_box_object("A"), make_box_object("B")
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc
        _results_with_solids(doc, 0)

        result = self.handler.common_objects({'objects': ['A', 'B']})

        assert_error_contains(self, result, "empty", "do not overlap", "not kept")
        self.assertTrue(a.Visibility)
        self.assertTrue(b.Visibility)
        doc.removeObject.assert_called_once_with("Common")

    def test_fuse_of_solids_that_do_not_touch_says_so(self):
        a, b = make_box_object("A"), make_box_object("B")
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc
        _results_with_solids(doc, 2)

        result = self.handler.fuse_objects({'objects': ['A', 'B']})

        assert_success_contains(self, result, "2 separate solids")

    def test_one_solid_fuse_says_nothing_about_pieces(self):
        a, b = make_box_object("A"), make_box_object("B")
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc
        _results_with_solids(doc, 1)

        result = self.handler.fuse_objects({'objects': ['A', 'B']})

        self.assertNotIn("separate", result)


class TestContainerFrame(unittest.TestCase):
    """MCP FREECAD v2 (2026-10-30): a Part boolean reads each operand in its
    container's frame. A common of a box in an App::Part moved by 100 mm and a
    free box overlapping it by 500 mm3 came out EMPTY; the cut of one by the
    other "removed NOTHING"."""

    def setUp(self):
        reset_mocks()
        self.handler = make_handler(BooleanOpsHandler)
        self.patch = patch.object(mock_FreeCAD, 'DocumentObject', MagicMock, create=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    @staticmethod
    def _cell(moved=True):
        cell = MagicMock()
        cell.Name = "Cell"
        cell.TypeId = "App::Part"
        pl = MagicMock()
        pl.isIdentity = MagicMock(return_value=not moved)
        pl.Base = _Vec(100, 0, 0) if moved else _Vec()
        cell.getGlobalPlacement = MagicMock(return_value=pl)
        return cell

    @staticmethod
    def _in(obj, parent):
        obj.getParentGeoFeatureGroup = MagicMock(return_value=parent)
        return obj

    def test_operands_sharing_a_moved_container_get_the_result_inside_it(self):
        cell = self._cell()
        a, b = self._in(make_box_object("A"), cell), self._in(make_box_object("B"), cell)
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.common_objects({'objects': ['A', 'B']})

        common = next(o for o in doc.Objects if o.TypeId == "Part::MultiCommon")
        cell.addObject.assert_called_once_with(common)
        assert_success_contains(self, result, "inside Cell")

    def test_cut_tools_fusion_goes_in_the_container_too(self):
        cell = self._cell()
        objs = [self._in(make_box_object(n), cell) for n in ("Base", "T1", "T2")]
        doc = make_mock_doc(objs)
        mock_FreeCAD.ActiveDocument = doc

        self.handler.cut_objects({'base': 'Base', 'tools': ['T1', 'T2']})

        added = {c.args[0].TypeId for c in cell.addObject.call_args_list}
        self.assertEqual(added, {"Part::Cut", "Part::MultiFuse"})

    def test_operands_in_differently_placed_frames_are_refused(self):
        a = self._in(make_box_object("A"), self._cell())
        b = self._in(make_box_object("B"), None)
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.cut_objects({'base': 'B', 'tools': ['A']})

        assert_error_contains(self, result, "cannot make this boolean", "A in Cell",
                              "(100, 0, 0)", "B at the document root")
        doc.addObject.assert_not_called()

    def test_unmoved_container_and_root_stay_at_the_root(self):
        cell = self._cell(moved=False)
        a = self._in(make_box_object("A"), cell)
        b = self._in(make_box_object("B"), None)
        doc = make_mock_doc([a, b])
        mock_FreeCAD.ActiveDocument = doc

        result = self.handler.fuse_objects({'objects': ['A', 'B']})

        assert_success_contains(self, result, "Created fusion")
        cell.addObject.assert_not_called()


if __name__ == '__main__':
    unittest.main()
