"""The stall detector must fire on a global plateau and stay quiet otherwise.

Gap 1 measured in the v1 campaign: 91.4% of actions associated no coverage
unit, so a detector anchored on coverage gain alone fired almost every action
and pre-empted the loop before the frontier's eligibility rule was consulted.
23 of 49 apps logged more stall events than actions.

These tests pin the corrected contract:
- still trying candidates never tried before is progress, so no trigger;
- reaching a screen never seen before is progress, so no trigger;
- repeating known candidates on known screens with no coverage still
  triggers, which is the falsification case -- if this test ever passes
  vacuously the detector has been widened too far and recovery is dead.
"""
from __future__ import annotations
import unittest
from valordroid.config import CoreConfig
from valordroid.models import (
    ActionAttempt,
    ActionSpec,
    ExecutionRecord,
    ExecutionStatus,
    OutcomeKind,
    OutcomeRecord,
    Provenance,
)
from valordroid.recovery import StallDetector


def _attempt(
    sequence: int,
    state_id: str,
    action: ActionSpec,
    *,
    after_state_id: str | None = None,
    units: tuple[str, ...] = (),
) -> ActionAttempt:
    after = state_id if after_state_id is None else after_state_id
    changed = after != state_id
    return ActionAttempt(
        attempt_id=f"a-{sequence:08d}",
        sequence=sequence,
        state_id=state_id,
        provenance=Provenance.GUI,
        requested=action,
        execution=ExecutionRecord(
            status=ExecutionStatus.EXECUTED,
            started_at=float(sequence),
            ended_at=float(sequence),
            action=action,
            error=None,
        ),
        outcome=OutcomeRecord(
            OutcomeKind.EFFECT if changed else OutcomeKind.NO_EFFECT,
            state_changed=changed,
            details={"before_state_id": state_id, "after_state_id": after},
        ),
        window_started_at=float(sequence),
        window_ended_at=float(sequence),
        associated_unit_ids=units,
    )


def _tap(target: str) -> ActionSpec:
    return ActionSpec(kind="tap", target_id=target)


class StallSignalTest(unittest.TestCase):
    def setUp(self) -> None:
        # stall_actions=40 by default; a small budget keeps the tests short
        # while exercising the same comparison the runner uses.
        self.config = CoreConfig(stall_actions=5, stall_seconds=10_000.0)

    def test_untried_candidates_are_progress(self) -> None:
        """Trying something new on one screen must not look like a stall.

        This is the v1 regression: every one of these actions gains no
        coverage, yet the explorer is doing exactly what it should.
        """
        detector = StallDetector(self.config)
        for sequence in range(1, 21):
            detector.record(_attempt(sequence, "screen-a", _tap(f"w{sequence}")))
        signal = detector.signal(now=20.0)
        self.assertFalse(signal.triggered)
        self.assertEqual(signal.actions_since_gain, 0)
        self.assertEqual(signal.candidates_tried, 20)

    def test_new_screen_is_progress(self) -> None:
        """Reaching an unseen screen resets the plateau even with zero units."""
        detector = StallDetector(self.config)
        for sequence in range(1, 7):
            detector.record(_attempt(sequence, "screen-a", _tap("w1")))
        self.assertTrue(detector.signal(now=6.0).triggered)
        detector.record(
            _attempt(7, "screen-a", _tap("w1"), after_state_id="screen-b")
        )
        signal = detector.signal(now=7.0)
        self.assertFalse(signal.triggered)
        self.assertEqual(signal.states_seen, 2)

    def test_repeating_known_work_still_triggers(self) -> None:
        """Falsification: a two-screen loop must still be caught.

        Both screens and both candidates are known after the first pass, so
        novelty is exhausted and the detector must fire. Without this the
        widened rule would disable recovery entirely.
        """
        detector = StallDetector(self.config)
        sequence = 0
        for _ in range(2):
            sequence += 1
            detector.record(
                _attempt(sequence, "screen-a", _tap("go"), after_state_id="screen-b")
            )
            sequence += 1
            detector.record(
                _attempt(sequence, "screen-b", _tap("back"), after_state_id="screen-a")
            )
        # First pass was novel, second was not.
        self.assertEqual(detector.signal(now=float(sequence)).actions_since_gain, 2)
        for _ in range(6):
            sequence += 1
            detector.record(
                _attempt(sequence, "screen-a", _tap("go"), after_state_id="screen-b")
            )
        signal = detector.signal(now=float(sequence))
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.states_seen, 2)
        self.assertEqual(signal.candidates_tried, 2)

    def test_coverage_gain_remains_progress(self) -> None:
        """The original signal is preserved, not replaced."""
        detector = StallDetector(self.config)
        for sequence in range(1, 7):
            detector.record(_attempt(sequence, "screen-a", _tap("w1")))
        self.assertTrue(detector.signal(now=6.0).triggered)
        detector.record(_attempt(7, "screen-a", _tap("w1"), units=("m#1",)))
        self.assertFalse(detector.signal(now=7.0).triggered)

    def test_self_transition_is_not_a_discovery(self) -> None:
        """An action reporting its own screen as the result gains nothing.

        Mirrors the frontier's `discoveries` rule so the two agree.
        """
        detector = StallDetector(self.config)
        for sequence in range(1, 8):
            detector.record(
                _attempt(sequence, "screen-a", _tap("w1"), after_state_id="screen-a")
            )
        signal = detector.signal(now=7.0)
        self.assertTrue(signal.triggered)
        self.assertEqual(signal.states_seen, 1)

    def test_replay_rebuilds_an_identical_detector(self) -> None:
        """Resume correctness: the detector is a pure function of attempts.

        controller.py replays every stored attempt through record() when a run
        is reopened, so a detector fed the same stream must reach the same
        verdict or a resumed run would recover differently from a fresh one.
        """
        attempts = [
            _attempt(1, "screen-a", _tap("w1"), after_state_id="screen-b"),
            _attempt(2, "screen-b", _tap("w2")),
            _attempt(3, "screen-b", _tap("w2")),
            _attempt(4, "screen-b", _tap("w2")),
            _attempt(5, "screen-b", _tap("w2")),
            _attempt(6, "screen-b", _tap("w2")),
            _attempt(7, "screen-b", _tap("w2")),
        ]
        live = StallDetector(self.config)
        for attempt in attempts:
            live.record(attempt)
        replayed = StallDetector(self.config)
        for attempt in attempts:
            replayed.record(attempt)
        self.assertEqual(live.signal(now=7.0), replayed.signal(now=7.0))
        self.assertTrue(replayed.signal(now=7.0).triggered)


class RecoverySpinTest(unittest.TestCase):
    """The recovery spin was measured four ways; the plain one wins.

    And-Bible at the full 3600s budget:

      charge every reset    5,000 resets, 334 actions,  65% budget, 48.31%
      end run on 20 barren     15 resets,   6 min of 60,            12.65%
      pause 2s per barren      15 resets,  55 actions, 100% budget, 14.36%
      exempt barren resets     15 resets, 187 actions, 100% budget, 15.09%

    Charging every reset is the original behaviour and the best of the four. The
    exemption removes the early stop but lets the loop spin all hour -- 18,486
    stall reports for 187 actions -- because a reset that changes nothing becomes
    free to retry at once. The reset budget is what bounds that, and a run ending
    at 65% of its budget is the price.

    The app also varies threefold between runs with the code held constant, so
    these tests pin only that the three rejected mechanisms are gone.
    """

    def test_no_barren_reset_limit(self) -> None:
        """Ending a run on barren resets stopped And-Bible at a tenth of budget."""
        from valordroid.runner import AndroidRunner

        self.assertFalse(hasattr(AndroidRunner, "BARREN_LADDER_RESET_LIMIT"))

    def test_no_barren_reset_pause(self) -> None:
        """Pausing spent 48 of 60 minutes asleep."""
        from valordroid.runner import AndroidRunner

        self.assertFalse(hasattr(AndroidRunner, "BARREN_RESET_PAUSE_SECONDS"))

    def test_the_reset_budget_is_still_a_real_bound(self) -> None:
        """Every reset is charged, which is what stops an endless spin."""
        from valordroid.runner import AndroidRunner
        import inspect

        source = inspect.getsource(AndroidRunner._explore)
        self.assertIn("ladder_resets += 1", source)
        self.assertNotIn("attempts_at_last_reset", source)


if __name__ == "__main__":
    unittest.main()
