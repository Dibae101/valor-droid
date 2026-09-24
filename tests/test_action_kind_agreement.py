"""Every layer must agree on which action kinds exist.

These sets are declared independently in the executor, the dispatch ledger, the
transaction replay, the route validator, and the foreign-workflow contract. A
divergence is invisible until a run happens to choose the odd kind out, and then
it is fatal: a campaign run aborted with "dynamic recovery route action kind is
not a GUI action: swipe_backward" because the route validator was missing two
kinds the executor had always supported.

The invariant is not that every set is identical. `back` is a global navigation
action rather than something aimed at an element, so the element-targeted layers
deliberately exclude it, and actions outside the app under test are deliberately
narrowed to the two that cannot type or scroll someone else's UI. What must hold
is that no layer silently drifts from those two intentional shapes.
"""

from __future__ import annotations

import unittest

from valordroid.android.actions import AndroidActionExecutor
from valordroid.dispatch import _GUI_DISPATCH_KINDS
from valordroid.foreign_workflow import FOREIGN_ACTION_KINDS, GUI_ACTION_KINDS
from valordroid.routes import _DYNAMIC_GUI_KINDS
from valordroid.transactions import _GUI_ACTION_KINDS

#: Navigation that targets no element, so the element-targeted sets omit it.
GLOBAL_KINDS = frozenset({"back"})

ELEMENT_TARGETED_SETS = {
    "dispatch._GUI_DISPATCH_KINDS": _GUI_DISPATCH_KINDS,
    "transactions._GUI_ACTION_KINDS": _GUI_ACTION_KINDS,
    "routes._DYNAMIC_GUI_KINDS": _DYNAMIC_GUI_KINDS,
    "foreign_workflow.GUI_ACTION_KINDS": GUI_ACTION_KINDS,
}


class ActionKindAgreementTest(unittest.TestCase):
    def test_the_executor_defines_the_authoritative_set(self) -> None:
        self.assertEqual(
            set(AndroidActionExecutor.SUPPORTED),
            {
                "back",
                "tap",
                "long_press",
                "input_text",
                "scroll_forward",
                "scroll_backward",
                "swipe_forward",
                "swipe_backward",
            },
            "the executor's capabilities changed; every dependent set must be "
            "reviewed rather than left to diverge silently",
        )

    def test_every_element_targeted_set_is_the_executor_minus_global_kinds(self) -> None:
        expected = set(AndroidActionExecutor.SUPPORTED) - GLOBAL_KINDS
        for name, kinds in ELEMENT_TARGETED_SETS.items():
            self.assertEqual(
                set(kinds),
                expected,
                f"{name} disagrees with the executor: "
                f"missing={sorted(expected - set(kinds))} "
                f"extra={sorted(set(kinds) - expected)}",
            )

    def test_the_element_targeted_sets_agree_with_each_other(self) -> None:
        shapes = {name: frozenset(kinds) for name, kinds in ELEMENT_TARGETED_SETS.items()}
        self.assertEqual(
            len(set(shapes.values())),
            1,
            f"element-targeted kind sets have diverged: "
            f"{ {name: sorted(value) for name, value in shapes.items()} }",
        )

    def test_a_swipe_is_dispatchable_everywhere_it_can_be_chosen(self) -> None:
        """The exact regression that aborted a campaign run."""

        for name, kinds in ELEMENT_TARGETED_SETS.items():
            self.assertIn("swipe_backward", kinds, name)
            self.assertIn("swipe_forward", kinds, name)

    def test_the_foreign_set_is_a_deliberate_narrowing(self) -> None:
        """Outside the app under test, only the two safest kinds are allowed."""

        self.assertEqual(set(FOREIGN_ACTION_KINDS), {"tap", "long_press"})
        self.assertTrue(set(FOREIGN_ACTION_KINDS) < set(GUI_ACTION_KINDS))


if __name__ == "__main__":
    unittest.main()
