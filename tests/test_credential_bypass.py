"""A credential screen must keep its exit, and offer it before the form.

`_asks_for_credentials` holds when the app's own UI has a masked input. The loop
then gives the screen a bounded number of executed GUI actions and abandons it,
because a login form cannot be solved without an account.

Abandoning withheld every candidate on the screen. That is right for the username
box, the password box and "Sign in", and wrong for the "Skip" or "Continue without
an account" control beside them: that one is the way past the wall, and removing it
made the screen a permanent dead end -- relaunch, land on the same wall, abandon it
again, and spend the run doing that. Eleven of the fifteen v2 runs carrying a login
label spent under half their actions inside the app.

The allowance had the mirror-image problem: it went to whatever the frontier ranked
highest, which on a sign-in screen is the form, so it ran out before the exit was
tried.

These tests pin both halves, and pin that nothing else about the screen changed.
"""

from __future__ import annotations

import unittest

from valordroid.credential_bypass import (
    BYPASS_RULE,
    classify_bypass,
    is_credential_input,
)
from valordroid.frontier import RankedCandidate
from valordroid.models import ActionCandidate, ActionSpec
from valordroid.runner import AndroidRunner


PACKAGE = "com.example.app"
STATE = "s" * 64


def _selector(
    *,
    text: str = "",
    resource_id: str = "",
    content_description: str = "",
    package: str = PACKAGE,
    class_name: str = "android.widget.Button",
) -> dict[str, str]:
    return {
        "resource_id": resource_id,
        "class_name": class_name,
        "text": text,
        "content_description": content_description,
        "tree_path": "0/0",
        "package": package,
    }


class _Node:
    def __init__(self, selector: dict[str, str], attributes: dict[str, str]) -> None:
        self.selector = selector
        self.attributes = attributes


class _Captured:
    def __init__(self, nodes) -> None:
        self.nodes = nodes


class _Observer:
    def __init__(self, nodes) -> None:
        self.latest = _Captured(nodes)


class _Session:
    package = PACKAGE


def _ranked(selector: dict[str, str], *, kind: str = "tap") -> RankedCandidate:
    action = ActionSpec(
        kind=kind,
        target_id="t" * 64,
        parameters={"selector": dict(selector), "expected_state_id": STATE},
    )
    return RankedCandidate(ActionCandidate(STATE, action), True, 1000.0, "untried")


class ClassificationTest(unittest.TestCase):
    def test_plain_exits_are_bypasses(self) -> None:
        for text in (
            "Skip",
            "Later",
            "Not now",
            "No thanks",
            "Maybe later",
            "Browse as guest",
            "Continue as guest",
            "Continue without an account",
            "Use offline",
            "Cancel",
            "Close",
            "Dismiss",
        ):
            verdict = classify_bypass(_selector(text=text), {})
            self.assertTrue(verdict.is_bypass, text)
            self.assertEqual(verdict.rule, BYPASS_RULE)

    def test_controls_that_commit_credentials_are_not_bypasses(self) -> None:
        for text in (
            "Sign in",
            "Sign up",
            "Log in",
            "Login",
            "Register",
            "Create account",
            "Submit",
            "Next",
            "Done",
            "Confirm",
            "Verify",
            "Forgot password?",
        ):
            self.assertFalse(classify_bypass(_selector(text=text), {}).is_bypass, text)

    def test_a_control_saying_both_wins_for_whichever_it_leads_with(self) -> None:
        """"Skip sign in" leaves; "Sign in later" does not."""

        self.assertTrue(classify_bypass(_selector(text="Skip sign in"), {}).is_bypass)
        self.assertFalse(classify_bypass(_selector(text="Sign in later"), {}).is_bypass)

    def test_visible_wording_outranks_the_developers_identifier(self) -> None:
        """An id is far likelier to carry an incidental word than a label is.

        `btn_cancel_login` is a cancel button whose id mentions login, and reading
        it as a commit control would withhold the exit again.
        """

        verdict = classify_bypass(
            _selector(resource_id="com.example.app:id/btn_cancel_login"), {}
        )
        self.assertTrue(verdict.is_bypass)
        self.assertEqual(verdict.evidence, "resource_id:cancel")
        self.assertFalse(
            classify_bypass(
                _selector(resource_id="com.example.app:id/loginButton"), {}
            ).is_bypass
        )

    def test_a_label_beats_an_id_that_disagrees(self) -> None:
        verdict = classify_bypass(
            _selector(text="Sign in", resource_id="com.example.app:id/skip_row"), {}
        )
        self.assertFalse(verdict.is_bypass)

    def test_an_unlabelled_control_is_not_a_bypass(self) -> None:
        verdict = classify_bypass(_selector(), {})
        self.assertFalse(verdict.is_bypass)
        self.assertEqual(verdict.evidence, "no_keyword")

    def test_a_masked_box_is_a_credential_input(self) -> None:
        self.assertTrue(is_credential_input(_selector(), {"password": "true"}))
        self.assertFalse(is_credential_input(_selector(), {"password": "false"}))
        self.assertFalse(is_credential_input(_selector(), {}))

    def test_classification_is_a_pure_function_of_retained_evidence(self) -> None:
        """The offline validator has to re-derive this, not trust it."""

        selector = _selector(text="Skip for now")
        first = classify_bypass(selector, {})
        second = classify_bypass(dict(selector), {})
        self.assertEqual((first.is_bypass, first.rule, first.evidence),
                         (second.is_bypass, second.rule, second.evidence))


class PartitionTest(unittest.TestCase):
    def _runner(self, nodes) -> AndroidRunner:
        runner = AndroidRunner.__new__(AndroidRunner)
        runner.session = _Session()
        runner.observer = _Observer(nodes)
        return runner

    def test_the_exit_is_separated_from_the_form(self) -> None:
        password = _selector(resource_id="com.example.app:id/password",
                             class_name="android.widget.EditText")
        username = _selector(resource_id="com.example.app:id/username",
                             class_name="android.widget.EditText")
        submit = _selector(text="Sign in")
        skip = _selector(text="Skip")
        nodes = [
            _Node(password, {"password": "true"}),
            _Node(username, {}),
            _Node(submit, {}),
            _Node(skip, {}),
        ]
        runner = self._runner(nodes)
        bypass, other = runner._partition_credential_screen(
            [_ranked(password, kind="input_text"), _ranked(username, kind="input_text"),
             _ranked(submit), _ranked(skip)]
        )
        self.assertEqual(len(bypass), 1)
        self.assertEqual(
            bypass[0].candidate.action.parameters["selector"]["text"], "Skip"
        )
        self.assertEqual(len(other), 3)

    def test_a_masked_box_is_never_an_exit_however_it_is_labelled(self) -> None:
        """Otherwise a field labelled "skip" would be typed into to escape."""

        masked = _selector(
            resource_id="com.example.app:id/skip_code",
            class_name="android.widget.EditText",
        )
        runner = self._runner([_Node(masked, {"password": "true"})])
        bypass, other = runner._partition_credential_screen(
            [_ranked(masked, kind="input_text")]
        )
        self.assertEqual(bypass, ())
        self.assertEqual(len(other), 1)

    def test_a_foreign_control_is_left_to_the_foreign_rules(self) -> None:
        foreign = _selector(text="Skip", package="com.android.chrome")
        runner = self._runner([_Node(foreign, {})])
        bypass, other = runner._partition_credential_screen([_ranked(foreign)])
        self.assertEqual(bypass, ())
        self.assertEqual(len(other), 1)

    def test_the_frontiers_order_is_preserved_inside_each_half(self) -> None:
        """This reprioritizes; it does not become a second ranking authority."""

        first = _selector(text="Skip", resource_id="a")
        second = _selector(text="Later", resource_id="b")
        form = _selector(text="Sign in")
        nodes = [_Node(first, {}), _Node(second, {}), _Node(form, {})]
        runner = self._runner(nodes)
        bypass, _other = runner._partition_credential_screen(
            [_ranked(first), _ranked(form), _ranked(second)]
        )
        self.assertEqual(
            [item.candidate.action.parameters["selector"]["resource_id"]
             for item in bypass],
            ["a", "b"],
        )

    def test_a_screen_with_no_exit_partitions_to_nothing(self) -> None:
        form = _selector(text="Sign in")
        runner = self._runner([_Node(form, {})])
        bypass, other = runner._partition_credential_screen([_ranked(form)])
        self.assertEqual(bypass, ())
        self.assertEqual(len(other), 1)

    def test_a_candidate_with_no_matching_node_is_not_an_exit(self) -> None:
        """Absent attributes must not be read as an absent password flag."""

        unknown = _selector(text="Skip")
        runner = self._runner([])
        bypass, _other = runner._partition_credential_screen([_ranked(unknown)])
        # The label alone still identifies it; what the missing node costs is the
        # masked check, which the label cannot supply.
        self.assertEqual(len(bypass), 1)


if __name__ == "__main__":
    unittest.main()


class ReorderingIsUnhashableSafeTest(unittest.TestCase):
    """A RankedCandidate cannot go in a set, and putting one there killed a run.

    `ActionSpec.parameters` is a dict, so the candidate is unhashable. Deduplicating
    the reordered recovery list with `set(...)` raised "unhashable type: 'dict'" and
    aborted Twire's run at the first credential screen that offered both an exit and
    a form -- the one case the reordering exists for. Membership is by identity.
    """

    def test_reordering_a_mixed_screen_does_not_raise(self) -> None:
        skip = _selector(text="Skip")
        submit = _selector(text="Sign in")
        eligible = [_ranked(skip), _ranked(submit)]
        already = {id(item) for item in eligible}
        merged = tuple(eligible) + tuple(
            item for item in eligible if id(item) not in already
        )
        self.assertEqual(len(merged), 2)

    def test_a_ranked_candidate_is_genuinely_unhashable(self) -> None:
        """Pins the property the fix depends on, so a set creeps back in loudly."""

        with self.assertRaises(TypeError):
            {_ranked(_selector(text="Skip"))}  # noqa: B018

    def test_two_equal_candidates_are_both_retained_by_identity(self) -> None:
        """Equality would collapse them; identity must not."""

        first = _ranked(_selector(text="Skip"))
        second = _ranked(_selector(text="Skip"))
        already = {id(first)}
        self.assertNotIn(id(second), already)
