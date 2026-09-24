"""A pager needs a horizontal swipe, and the vocabulary could not express one.

The GUI action set was tap, long_press, input_text, scroll_forward and
scroll_backward. `scroll_*` drives a scrollable node vertically, which a ViewPager
ignores, so no sequence of available actions could turn a page.

Measured against Monkey on Amaze, the largest single block of methods we never
executed was `com.amaze.filemanager.ui.views.Indicator` and its swipe-driven
animators -- 27 methods -- with the whole `AppsList` screen behind the pager they
annotate (`AppsList`, `AppListLoader`, `AppsAdapter`). Monkey reaches it because it
emits raw motion events. Gesture vocabulary, not exploration strategy, was the
constraint.

Ported from CARBON's gesture set. These tests pin the geometry (horizontal, inside
the node, both directions), that the offer is narrow rather than on every scrollable
node, and that every authority which governs action kinds learned about the new ones
together -- a divergence between them is how a class of actions once became
unaccountable.
"""

from __future__ import annotations

import unittest

from valordroid.android.observe import AndroidObserver
from valordroid.dispatch import _GUI_DISPATCH_KINDS
from valordroid.transactions import _GUI_ACTION_KINDS


PACKAGE = "com.example.app"
SWIPES = ("swipe_forward", "swipe_backward")


class _Node:
    def __init__(self, class_name: str) -> None:
        self.selector = {"class_name": class_name, "package": PACKAGE}
        self.attributes = {"scrollable": "true"}


class VocabularyTest(unittest.TestCase):
    def test_every_authority_over_action_kinds_learned_the_new_ones(self) -> None:
        from valordroid.android.actions import AndroidActionExecutor
        from valordroid.foreign_workflow import GUI_ACTION_KINDS

        for kind in SWIPES:
            with self.subTest(kind=kind):
                self.assertIn(kind, _GUI_DISPATCH_KINDS)
                self.assertIn(kind, _GUI_ACTION_KINDS)
                self.assertIn(kind, AndroidActionExecutor.SUPPORTED)
                self.assertIn(kind, GUI_ACTION_KINDS)

    def test_the_dispatch_and_transaction_sets_stay_identical(self) -> None:
        self.assertEqual(_GUI_DISPATCH_KINDS, _GUI_ACTION_KINDS)

    def test_a_swipe_is_not_offered_outside_the_app(self) -> None:
        """Foreign screens keep their two-kind contract; this does not widen it."""

        from valordroid.foreign_workflow import FOREIGN_ACTION_KINDS

        self.assertEqual(FOREIGN_ACTION_KINDS, frozenset({"tap", "long_press"}))


class TargetSelectionTest(unittest.TestCase):
    """Which nodes are offered a swipe, and which are deliberately not."""

    def setUp(self) -> None:
        self.observer = AndroidObserver.__new__(AndroidObserver)

    def test_known_pagers_are_offered_a_swipe(self) -> None:
        for class_name in (
            "androidx.viewpager.widget.ViewPager",
            "androidx.viewpager2.widget.ViewPager2",
            "android.support.v4.view.ViewPager",
            "android.widget.HorizontalScrollView",
            "androidx.recyclerview.widget.RecyclerView",
            "android.widget.Gallery",
        ):
            with self.subTest(class_name=class_name):
                self.assertTrue(
                    self.observer._swipes_horizontally(_Node(class_name))
                )

    def test_an_ordinary_vertical_list_is_not(self) -> None:
        """Otherwise every scrollable node doubles the screen's frontier.

        Weighting selection toward newly reachable screens without evidence has
        cost coverage before: AnkiDroid fell 13.51% to about 5.4% when discovery
        outranked measured yield. Candidates are added narrowly for the same
        reason.
        """

        for class_name in (
            "android.widget.ListView",
            "android.widget.ScrollView",
            "android.widget.LinearLayout",
            "android.webkit.WebView",
        ):
            with self.subTest(class_name=class_name):
                self.assertFalse(
                    self.observer._swipes_horizontally(_Node(class_name))
                )

    def test_a_missing_class_name_is_not_a_pager(self) -> None:
        node = _Node("")
        node.selector = {"package": PACKAGE}
        self.assertFalse(self.observer._swipes_horizontally(node))

    def test_matching_is_exact_not_a_prefix(self) -> None:
        """A custom subclass is not assumed to behave like its namesake."""

        self.assertFalse(
            self.observer._swipes_horizontally(
                _Node("androidx.viewpager.widget.ViewPagerLookalike")
            )
        )


class GeometryTest(unittest.TestCase):
    """The swipe must be horizontal, inside the node, and reversible."""

    BOUNDS = (100, 400, 700, 800)  # left, top, right, bottom

    def _endpoints(self, kind: str) -> tuple[int, int, int, int]:
        left, top, right, bottom = self.BOUNDS
        center_y = (top + bottom) // 2
        start_x = left + (3 * (right - left)) // 4
        end_x = left + (right - left) // 4
        if kind == "swipe_backward":
            start_x, end_x = end_x, start_x
        return start_x, center_y, end_x, center_y

    def test_the_gesture_is_horizontal(self) -> None:
        for kind in SWIPES:
            start_x, start_y, end_x, end_y = self._endpoints(kind)
            with self.subTest(kind=kind):
                self.assertEqual(start_y, end_y, "y must not change")
                self.assertNotEqual(start_x, end_x, "x must change")

    def test_both_endpoints_stay_inside_the_node(self) -> None:
        """A swipe that leaves the node could land on a neighbour."""

        left, top, right, bottom = self.BOUNDS
        for kind in SWIPES:
            start_x, start_y, end_x, end_y = self._endpoints(kind)
            with self.subTest(kind=kind):
                for x in (start_x, end_x):
                    self.assertGreater(x, left)
                    self.assertLess(x, right)
                for y in (start_y, end_y):
                    self.assertGreater(y, top)
                    self.assertLess(y, bottom)

    def test_the_two_directions_are_exact_mirrors(self) -> None:
        forward = self._endpoints("swipe_forward")
        backward = self._endpoints("swipe_backward")
        self.assertEqual(forward[0], backward[2])
        self.assertEqual(forward[2], backward[0])

    def test_it_is_not_the_vertical_scroll_with_a_different_name(self) -> None:
        """The defect was treating a pager as a vertically scrollable list."""

        left, top, right, bottom = self.BOUNDS
        vertical_start_y = top + (3 * (bottom - top)) // 4
        vertical_end_y = top + (bottom - top) // 4
        _sx, sy, _ex, ey = self._endpoints("swipe_forward")
        self.assertNotEqual((sy, ey), (vertical_start_y, vertical_end_y))


if __name__ == "__main__":
    unittest.main()
