"""Typing appended instead of replacing, so 95% of it verified as a mismatch.

`input text` inserts at the cursor. The exploration loop tapped a field and typed
without emptying it first, so any field that already held content received the
concatenation and never the value asked for. `AndroidOutcomeVerifier._input_target`
then compares the field's text against the value typed, which can only match when
the field started empty, so the action recorded `no_effect` and the loop retyped
into the same field for the rest of the run.

Measured over the 111 v2 runs, from `actions.jsonl`:

    input_text actions                     7,375   15.7% of all actions
    recorded no_effect                     95.2%
    field_accepted is False                6,639
      of those, typed value was a suffix   6,306   95.0%

The growth is visible in the evidence -- `7` typed into a field holding `777` was
observed as `7777`, and `valordroid` into `valordroid` as `valordroidvalordroid`.

`android/setup.py::_clear_focused_field` has always done this correctly for setup
profiles, where `clear` defaults to True. The exploration loop never called it.
The sequence is deliberately one long-press delete rather than a loop of plain
DELs: once the field is empty Android treats further presses as Back, which
dismisses the app under test.
"""
from __future__ import annotations

import unittest

from valordroid.android.actions import AndroidActionExecutor
from valordroid.android.observe import UiNode


class _RecordingAdb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def shell(self, *args: str, **_kwargs: object) -> None:
        self.calls.append(tuple(str(a) for a in args))


class _Session:
    def __init__(self) -> None:
        self.adb = _RecordingAdb()


class _Observer:
    def __init__(self, node: UiNode, state_id: str) -> None:
        self._node = node
        self._state_id = state_id

    def resolve(self, _requested: object) -> tuple[object, tuple[UiNode, ...]]:
        class _Captured:
            observation = type("_Obs", (), {"state_id": self._state_id})()

        return _Captured(), (self._node,)


def _node(text: str) -> UiNode:
    return UiNode(
        selector={
            "package": "com.example.app",
            "resource_id": "com.example.app:id/field",
            "class_name": "android.widget.EditText",
            "content_description": "",
            "text": text,
            "tree_path": "0/0",
        },
        bounds=(0, 0, 100, 40),
        attributes={"text": text, "focusable": "true"},
    )


class InputFieldClearTest(unittest.TestCase):
    """The executor is exercised directly; only the adb calls are recorded."""

    STATE = "state-1"

    def _execute(self, existing_text: str):
        from valordroid.models import ActionSpec

        session = _Session()
        node = _node(existing_text)
        observer = _Observer(node, self.STATE)

        class _Config:
            action_timeout_seconds = 40.0
            text_input_value = "valordroid"
            per_field_input_enabled = False

        executor = AndroidActionExecutor(session, observer, _Config())  # type: ignore[arg-type]
        requested = ActionSpec(
            kind="input_text",
            target_id="t",
            parameters={
                "expected_state_id": self.STATE,
                "selector": node.selector,
                "value": "valordroid",
                "field_kind": "text",
                "value_rule": "deterministic_field_kind_seed_v1",
            },
        )
        record = executor.execute(requested)
        return executor, session.adb.calls, record

    def test_a_field_holding_text_is_emptied_before_typing(self) -> None:
        executor, calls, _ = self._execute("valordroid")
        kinds = [c for c in calls if c[:2] == ("input", "keyevent")]
        self.assertIn(("input", "keyevent", "KEYCODE_MOVE_END"), kinds)
        deletes = [c for c in kinds if "KEYCODE_DEL" in c]
        self.assertEqual(
            len(deletes),
            1,
            "one remote call carries every delete; a call per character would "
            "cost more than the action itself",
        )
        self.assertEqual(
            deletes[0].count("KEYCODE_DEL"),
            len("valordroid"),
            "exactly one delete per observed character: fewer leaves a prefix "
            "for the value to append to, more is delivered as Back",
        )
        self.assertTrue(executor.last_field_cleared)

    def test_the_delete_count_matches_the_field_length(self) -> None:
        for existing in ("a", "ab", "{{Front}}", "x" * 40):
            _, calls, _ = self._execute(existing)
            deletes = [c for c in calls if "KEYCODE_DEL" in c][0]
            self.assertEqual(deletes.count("KEYCODE_DEL"), len(existing))

    def test_a_long_press_delete_alone_is_not_relied_on(self) -> None:
        """It removed two characters of a nine-character field on redroid."""
        _, calls, _ = self._execute("{{Front}}")
        self.assertNotIn(
            "--longpress", [part for call in calls for part in call]
        )

    def test_a_field_only_showing_a_hint_is_treated_as_empty(self) -> None:
        """DELs against an empty field are delivered as Back."""
        from valordroid.models import ActionSpec

        session = _Session()
        node = _node("Front")
        node.attributes["hint"] = "Front"
        observer = _Observer(node, self.STATE)

        class _Config:
            action_timeout_seconds = 40.0
            text_input_value = "valordroid"
            per_field_input_enabled = False

        executor = AndroidActionExecutor(session, observer, _Config())  # type: ignore[arg-type]
        executor.execute(
            ActionSpec(
                kind="input_text",
                target_id="t",
                parameters={
                    "expected_state_id": self.STATE,
                    "selector": node.selector,
                    "value": "valordroid",
                    "field_kind": "text",
                    "value_rule": "deterministic_field_kind_seed_v1",
                },
            )
        )
        self.assertFalse(executor.last_field_cleared)
        self.assertNotIn(
            "KEYCODE_DEL", [part for call in session.adb.calls for part in call]
        )

    def test_an_implausibly_long_field_is_left_alone(self) -> None:
        """A bound stops one action becoming hundreds of key events."""
        executor, calls, _ = self._execute(
            "x" * (AndroidActionExecutor.MAX_CLEAR_KEYSTROKES + 1)
        )
        self.assertFalse(executor.last_field_cleared)
        self.assertNotIn("KEYCODE_DEL", [part for call in calls for part in call])

    def test_a_multi_line_field_is_left_alone(self) -> None:
        """MOVE_END reaches the end of the line, so the count cannot be exact.

        Observed on AnkiDroid's template editor at 3600s: the first line was
        emptied and the value landed in front of the rest, giving
        `valordroid\\n{{Back}}`. Over-deleting to compensate is what Android
        delivers as Back, so the field is declined instead.
        """
        for existing in ("{{FrontSide}}\n<hr id=answer>", "a\r\nb"):
            executor, calls, _ = self._execute(existing)
            self.assertFalse(executor.last_field_cleared)
            self.assertNotIn(
                "KEYCODE_DEL", [part for call in calls for part in call]
            )

    def test_the_clear_happens_after_the_tap_and_before_the_text(self) -> None:
        """Order matters: the field has to be focused, and empty, before typing."""
        _, calls, _ = self._execute("existing")
        shapes = [c[:3] for c in calls]
        tap = next(i for i, c in enumerate(shapes) if c[:2] == ("input", "tap"))
        clear = next(
            i for i, c in enumerate(shapes) if c == ("input", "keyevent", "KEYCODE_MOVE_END")
        )
        typed = next(i for i, c in enumerate(shapes) if c[:2] == ("input", "text"))
        self.assertLess(tap, clear)
        self.assertLess(clear, typed)

    def test_an_empty_field_is_not_cleared(self) -> None:
        """Nothing to delete, and a keystroke on an empty field is not free."""
        executor, calls, _ = self._execute("")
        self.assertNotIn(
            ("input", "keyevent", "--longpress", "KEYCODE_DEL"),
            [c for c in calls],
        )
        self.assertFalse(executor.last_field_cleared)

    def test_the_value_still_reaches_the_device(self) -> None:
        _, calls, record = self._execute("existing")
        self.assertIn(("input", "text", "valordroid"), calls)
        self.assertEqual(record.status.value, "executed")

    def test_the_previous_text_is_retained_for_the_outcome_record(self) -> None:
        executor, _, _ = self._execute("previous")
        self.assertEqual(executor.last_target_text, "previous")


if __name__ == "__main__":
    unittest.main()
