"""A route activation is a launch, not a GUI action dispatch.

`replay_action_dispatches` requires every canonical attempt from the first
dispatch intent onward to appear in the dispatch ledger. That is right for actions
the observer dispatches against a selector, and wrong for a route activation: a
forced component launch, a deep link or a bare intent has no selector, takes no
pre-dispatch reservation, and records how the screen was entered in the route
ledger instead.

The invariant did not make that distinction, so every run that performed a forced
sweep failed with "canonical action attempt lacks modern dispatch evidence". The
runtime failure then left the dispatch intent unfinalized, and the run was
discarded as invalid with all its coverage. Seven of ten runs in the first
forced-activation campaign died that way, ODK-Collect among them, and the two that
survived were the two that never reached a sweep.

It was invisible until now because the published V2 campaign ran with
`allow_forced_routes` false, so nothing ever exercised the path.
"""

from __future__ import annotations

import unittest

from valordroid.dispatch import (
    ActionDispatchReservation,
    DISPATCH_FINALIZED,
    DISPATCH_INTENT,
    _GUI_DISPATCH_KINDS,
    replay_action_dispatches,
)
from valordroid.models import (
    ActionAttempt,
    ActionSpec,
    ExecutionRecord,
    ExecutionStatus,
    LifecycleRecord,
    LifecycleStatus,
    OutcomeKind,
    OutcomeRecord,
    Provenance,
)

STATE = "s" * 64
SELECTOR = {
    "resource_id": "com.example.app:id/button",
    "class_name": "android.widget.Button",
    "text": "",
    "content_description": "",
    "tree_path": "0/0",
    "package": "com.example.app",
}


def _tap() -> ActionSpec:
    return ActionSpec(
        kind="tap",
        target_id="t" * 64,
        parameters={"selector": dict(SELECTOR), "expected_state_id": STATE},
    )


def _forced(component: str) -> ActionSpec:
    return ActionSpec(
        kind="forced_component",
        target_id=None,
        parameters={"component": component},
    )


def _execution(action: ActionSpec) -> ExecutionRecord:
    return ExecutionRecord(
        action=action,
        status=ExecutionStatus.EXECUTED,
        started_at=1.0,
        ended_at=2.0,
        error=None,
    )


def _attempt(
    sequence: int,
    action: ActionSpec,
    *,
    provenance: Provenance,
    route_id: str | None = None,
) -> ActionAttempt:
    return ActionAttempt(
        attempt_id=f"a-{sequence:08d}-{'b' * 12}",
        sequence=sequence,
        state_id=STATE,
        provenance=provenance,
        requested=action,
        execution=_execution(action),
        outcome=OutcomeRecord(kind=OutcomeKind.EFFECT, state_changed=True),
        window_started_at=1.0,
        window_ended_at=2.0,
        route_id=route_id,
    )


def _reservation(sequence: int, action: ActionSpec, attempt_sequence: int):
    """Built by the real factory, so the dispatch ID is the canonical one."""

    return ActionDispatchReservation.create(
        dispatch_sequence=sequence,
        state_id=STATE,
        before_activity="com.example.app/.Main",
        provenance=Provenance.GUI,
        requested=action,
        route_id=None,
        reserved_at=1.0,
        expected_attempt_sequence=attempt_sequence,
    )


def _intent(reservation) -> LifecycleRecord:
    return LifecycleRecord(
        record_id=reservation.intent_record_id,
        phase="exploration",
        event=DISPATCH_INTENT,
        status=LifecycleStatus.STARTED,
        observed_at=reservation.reserved_at,
        details={
            "dispatch_id": reservation.dispatch_id,
            "dispatch_sequence": reservation.dispatch_sequence,
            "state_id": reservation.state_id,
            "before_activity": reservation.before_activity,
            "provenance": reservation.provenance.value,
            "requested": reservation.requested.to_dict(),
            "route_id": reservation.route_id,
            "reserved_at": reservation.reserved_at,
            "expected_attempt_sequence": reservation.expected_attempt_sequence,
        },
        error=None,
    )


def _finalized(reservation, attempt: ActionAttempt) -> LifecycleRecord:
    return LifecycleRecord(
        record_id=reservation.final_record_id,
        phase="exploration",
        event=DISPATCH_FINALIZED,
        status=LifecycleStatus.SUCCEEDED,
        observed_at=2.0,
        details={
            "dispatch_id": reservation.dispatch_id,
            "dispatch_sequence": reservation.dispatch_sequence,
            "finalized_at": 2.0,
            "disposition": "committed",
            "failure_stage": None,
            "coverage_event_id": None,
            "execution": attempt.execution.to_dict(),
            "attempt_id": attempt.attempt_id,
            "attempt_sequence": attempt.sequence,
        },
        error=None,
    )


class ForcedActivationNeedsNoDispatchEvidenceTest(unittest.TestCase):
    def test_a_forced_launch_after_a_dispatch_does_not_invalidate_the_run(self) -> None:
        """The reproduction: a sweep launch follows an ordinary tap."""

        tap = _attempt(1, _tap(), provenance=Provenance.GUI)
        launch = _attempt(
            2,
            _forced("com.example.app/.InternalActivity"),
            provenance=Provenance.FORCED,
            route_id="r-" + "d" * 22,
        )
        reservation = _reservation(1, tap.requested, 1)
        lifecycle = [_intent(reservation), _finalized(reservation, tap)]

        replay = replay_action_dispatches(
            lifecycle, [tap, launch], run_status="finished"
        )

        self.assertEqual(len(replay.finalized), 1)
        self.assertIsNone(replay.pending)

    def test_several_forced_launches_are_all_exempt(self) -> None:
        tap = _attempt(1, _tap(), provenance=Provenance.GUI)
        launches = [
            _attempt(
                index,
                _forced(f"com.example.app/.Activity{index}"),
                provenance=Provenance.FORCED,
                route_id=f"r-{index:022d}",
            )
            for index in (2, 3, 4)
        ]
        reservation = _reservation(1, tap.requested, 1)
        lifecycle = [_intent(reservation), _finalized(reservation, tap)]

        replay = replay_action_dispatches(
            lifecycle, [tap, *launches], run_status="finished"
        )

        self.assertEqual(len(replay.finalized), 1)

    def test_a_gui_action_still_requires_its_dispatch_evidence(self) -> None:
        """The invariant must not be weakened for the actions it governs."""

        first = _attempt(1, _tap(), provenance=Provenance.GUI)
        undocumented = _attempt(2, _tap(), provenance=Provenance.GUI)
        reservation = _reservation(1, first.requested, 1)
        lifecycle = [_intent(reservation), _finalized(reservation, first)]

        with self.assertRaises(ValueError) as caught:
            replay_action_dispatches(
                lifecycle, [first, undocumented], run_status="finished"
            )
        self.assertIn("lacks modern dispatch evidence", str(caught.exception))

    def test_a_gui_action_reached_through_a_forced_route_is_not_exempt(self) -> None:
        """Provenance describes how the screen was reached, not the action.

        A tap on a screen entered by a forced launch is still a tap, and
        exempting it by provenance would have let a real GUI dispatch go
        unrecorded.
        """

        first = _attempt(1, _tap(), provenance=Provenance.GUI)
        after_launch = _attempt(
            2, _tap(), provenance=Provenance.FORCED, route_id="r-" + "e" * 22
        )
        reservation = _reservation(1, first.requested, 1)
        lifecycle = [_intent(reservation), _finalized(reservation, first)]

        with self.assertRaises(ValueError):
            replay_action_dispatches(
                lifecycle, [first, after_launch], run_status="finished"
            )


class GuiKindsDoNotDriftTest(unittest.TestCase):
    def test_the_dispatch_ledger_governs_the_same_kinds_transactions_enforce(
        self,
    ) -> None:
        """Two copies of one set; a silent divergence would reopen this defect."""

        from valordroid.transactions import _GUI_ACTION_KINDS

        self.assertEqual(_GUI_DISPATCH_KINDS, _GUI_ACTION_KINDS)

    def test_a_route_activation_kind_is_not_a_dispatch_kind(self) -> None:
        for kind in ("forced_component", "deep_link", "intent"):
            with self.subTest(kind=kind):
                self.assertNotIn(kind, _GUI_DISPATCH_KINDS)


if __name__ == "__main__":
    unittest.main()
