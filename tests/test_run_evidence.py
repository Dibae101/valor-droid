"""Single-run evidence regressions: coverage, crashes, outcomes, activity.

Pinned findings:
- review 1 #9: coverage events were not required to be cumulative or immutable.
- review 1 #11: activity parsing accepted stale lines; crash blocks allowed
  decreasing timestamps.
- review 2 #14: an aborted run discarded its terminal drain event.
- review 3 #6 / 5 #6: state identity must stay recomputable from stored evidence.
- review 4 #6 / 5 #7: launch and observation must agree about ambiguity.
"""

from __future__ import annotations

import unittest

from valordroid.android.session import AndroidSession
from valordroid.crashes import ParsedLogLine, _within_bounds
from valordroid.events import CoverageEventLedger
from valordroid.models import (
    ActionAttempt,
    ActionSpec,
    CoverageEvent,
    CoverageEventStatus,
    ExecutionRecord,
    ExecutionStatus,
    OutcomeKind,
    OutcomeRecord,
    Provenance,
    StateObservation,
    stable_hash,
)

PACKAGE = "com.example.app"
UNIVERSE_SHA = "a" * 64


class _FakeLedger:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def payloads(self) -> list[dict]:
        return list(self.entries)

    def append(self, payload: dict) -> None:
        self.entries.append(payload)


class _FakeUniverse:
    package_name = PACKAGE
    backend = "androlog-logcat-method-v1"
    universe_sha256 = UNIVERSE_SHA
    unit_ids = ("u1", "u2")


def _event(
    units, *, event_id: str, observed_at: float, host_time: float, times=None
) -> CoverageEvent:
    resolved = {unit: (times or {}).get(unit, host_time) for unit in units}
    return CoverageEvent(
        event_id=event_id,
        session_id="s-1",
        package_name=PACKAGE,
        backend="androlog-logcat-method-v1",
        universe_sha256=UNIVERSE_SHA,
        status=CoverageEventStatus.SUCCEEDED,
        started_at=1.0,
        observed_at=observed_at,
        observed_unit_ids=tuple(sorted(units)),
        unit_observed_at=dict(resolved),
        unit_device_observed_at={unit: 100.0 for unit in units},
        unit_process_ids={unit: 42 for unit in units},
        unit_process_names={unit: PACKAGE for unit in units},
        unit_process_start_tokens={unit: "gen-1" for unit in units},
        unit_ownership_observed_before={unit: resolved[unit] - 0.5 for unit in units},
        unit_ownership_observed_after={unit: resolved[unit] + 0.5 for unit in units},
    )


def _failed_event(event_id: str, observed_at: float) -> CoverageEvent:
    return CoverageEvent(
        event_id=event_id,
        session_id="s-1",
        package_name=PACKAGE,
        backend="androlog-logcat-method-v1",
        universe_sha256=UNIVERSE_SHA,
        status=CoverageEventStatus.FAILED,
        started_at=1.0,
        observed_at=observed_at,
        error="collector failed",
    )


class CoverageLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = CoverageEventLedger(_FakeLedger(), _FakeUniverse())

    def test_cumulative_snapshot_is_accepted(self) -> None:
        self.ledger.record(_event({"u1", "u2"}, event_id="e1", observed_at=2.0, host_time=1.5))
        self.ledger.record(_event({"u1", "u2"}, event_id="e2", observed_at=3.0, host_time=1.5))

    def test_dropping_a_previously_observed_unit_is_rejected(self) -> None:
        self.ledger.record(_event({"u1", "u2"}, event_id="e1", observed_at=2.0, host_time=1.5))
        with self.assertRaises(ValueError):
            self.ledger.record(_event({"u1"}, event_id="e2", observed_at=3.0, host_time=1.5))

    def test_rewriting_first_observation_provenance_is_rejected(self) -> None:
        self.ledger.record(_event({"u1", "u2"}, event_id="e1", observed_at=2.0, host_time=1.5))
        with self.assertRaises(ValueError):
            self.ledger.record(
                _event({"u1", "u2"}, event_id="e2", observed_at=5.0, host_time=2.9)
            )

    def test_one_terminal_drain_survives_a_failed_collection(self) -> None:
        """Review 2 #14: the shutdown drain event was being discarded."""

        self.ledger.record(_event({"u1"}, event_id="d1", observed_at=2.0, host_time=1.5))
        self.ledger.record(_failed_event("d2", 3.0))
        self.ledger.record(
            _event(
                {"u1", "u2"},
                event_id="d3",
                observed_at=4.0,
                host_time=3.5,
                times={"u1": 1.5},
            )
        )

    def test_nothing_may_follow_the_terminal_drain(self) -> None:
        self.ledger.record(_event({"u1"}, event_id="d1", observed_at=2.0, host_time=1.5))
        self.ledger.record(_failed_event("d2", 3.0))
        self.ledger.record(
            _event({"u1"}, event_id="d3", observed_at=4.0, host_time=1.5)
        )
        with self.assertRaises(ValueError):
            self.ledger.record(
                _event({"u1"}, event_id="d4", observed_at=5.0, host_time=1.5)
            )


class CrashBlockTest(unittest.TestCase):
    @staticmethod
    def _line(device_time: float) -> ParsedLogLine:
        return ParsedLogLine(
            raw=f"{device_time} 1 1 E AndroidRuntime: boom",
            device_time=device_time,
            pid=1,
            tid=1,
            tag="AndroidRuntime",
            message="boom",
        )

    def test_nondecreasing_block_is_accepted(self) -> None:
        self.assertTrue(
            _within_bounds([self._line(10.0), self._line(10.5), self._line(11.0)])
        )

    def test_decreasing_block_is_rejected(self) -> None:
        """Review 1 #11: crash blocks allowed time to run backwards."""

        self.assertFalse(
            _within_bounds([self._line(10.0), self._line(9.0), self._line(11.0)])
        )

    def test_block_longer_than_the_window_is_rejected(self) -> None:
        self.assertFalse(_within_bounds([self._line(0.0), self._line(60.0)]))


class OutcomeContractTest(unittest.TestCase):
    def _attempt(self, execution: ExecutionRecord, outcome: OutcomeRecord) -> ActionAttempt:
        selector = {
            "resource_id": "r",
            "class_name": "android.widget.Button",
            "text": "OK",
            "content_description": "",
            "tree_path": "0/1",
            "package": PACKAGE,
        }
        action = ActionSpec(
            kind="tap",
            target_id=stable_hash(dict(selector)),
            parameters={"expected_state_id": "state-1", "selector": dict(selector)},
        )
        return ActionAttempt(
            attempt_id="a-1",
            sequence=1,
            state_id="state-1",
            provenance=Provenance.GUI,
            requested=action,
            execution=execution,
            outcome=outcome,
            window_started_at=1.0,
            window_ended_at=2.5,
        )

    def test_not_executed_requires_a_not_executed_outcome(self) -> None:
        execution = ExecutionRecord(
            status=ExecutionStatus.NOT_EXECUTED,
            started_at=1.0,
            ended_at=2.0,
            error="stale target",
        )
        with self.assertRaises(ValueError):
            self._attempt(
                execution, OutcomeRecord(OutcomeKind.EFFECT, state_changed=True)
            )

    def test_executed_action_cannot_claim_not_executed(self) -> None:
        selector_action = ActionSpec(
            kind="tap",
            target_id="t",
            parameters={"expected_state_id": "state-1", "selector": {}},
        )
        execution = ExecutionRecord(
            status=ExecutionStatus.EXECUTED,
            started_at=1.0,
            ended_at=2.0,
            action=selector_action,
        )
        with self.assertRaises(ValueError):
            self._attempt(
                execution,
                OutcomeRecord(OutcomeKind.NOT_EXECUTED, state_changed=False),
            )


class StateObservationTest(unittest.TestCase):
    def test_resolved_activity_must_be_the_unique_resumed_activity(self) -> None:
        """Review 5 #6: state identity bound a field nothing recorded."""

        with self.assertRaises(ValueError):
            StateObservation(
                state_id="s",
                observed_at=1.0,
                activity="com.example.app/.Main",
                resumed_activities=(
                    "com.example.app/.Main",
                    "com.other/.Other",
                ),
            )

    def test_ambiguous_frame_records_every_resumed_activity(self) -> None:
        observation = StateObservation(
            state_id="s",
            observed_at=1.0,
            activity=None,
            resumed_activities=("com.a/.A", "com.b/.B"),
        )
        self.assertIsNone(observation.activity)
        self.assertEqual(len(observation.resumed_activities), 2)

    def test_unique_resumed_activity_is_the_resolved_activity(self) -> None:
        observation = StateObservation(
            state_id="s",
            observed_at=1.0,
            activity="com.example.app/.Main",
            resumed_activities=("com.example.app/.Main",),
        )
        self.assertEqual(observation.activity, observation.resumed_activities[0])


class _FakeResult:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeAdb:
    def __init__(self, dump: str, returncode: int = 0) -> None:
        self.dump = dump
        self.returncode = returncode

    def shell(self, *args, **kwargs):
        return _FakeResult(self.dump, self.returncode)


class ActivityResolutionTest(unittest.TestCase):
    def _session(self, dump: str, returncode: int = 0) -> AndroidSession:
        session = AndroidSession.__new__(AndroidSession)
        session.adb = _FakeAdb(dump, returncode)  # type: ignore[attr-defined]
        session.package = PACKAGE
        return session

    def test_single_resumed_activity_is_authoritative(self) -> None:
        session = self._session(
            "  mResumedActivity: ActivityRecord{a u0 com.example.app/.MainActivity t1}"
        )
        self.assertEqual(
            AndroidSession.current_activity(session), f"{PACKAGE}/{PACKAGE}.MainActivity"
        )

    def test_null_markers_are_skipped(self) -> None:
        """Review 5: unfocused tasks and secondary displays report null."""

        session = self._session(
            "  topResumedActivity=null\n"
            "  mResumedActivity: ActivityRecord{a u0 com.example.app/.MainActivity t1}"
        )
        self.assertEqual(
            AndroidSession.current_activity(session), f"{PACKAGE}/{PACKAGE}.MainActivity"
        )

    def test_window_focus_is_never_authoritative(self) -> None:
        """Review 1 #11: a focused overlay must not be read as the activity."""

        session = self._session(
            "  mCurrentFocus=Window{x u0 com.android.systemui/.Overlay}"
        )
        self.assertIsNone(AndroidSession.current_activity(session))

    def test_ambiguous_frame_has_no_single_activity(self) -> None:
        session = self._session(
            "  mResumedActivity: ActivityRecord{a u0 com.android.launcher3/.Launcher t1}\n"
            "  mResumedActivity: ActivityRecord{b u0 com.example.app/.MainActivity t2}"
        )
        self.assertIsNone(AndroidSession.current_activity(session))
        self.assertEqual(len(AndroidSession.resumed_activities(session)), 2)

    def test_failed_dump_reports_nothing_resumed(self) -> None:
        session = self._session("", returncode=1)
        self.assertEqual(AndroidSession.resumed_activities(session), ())

    def test_resolved_activity_rule_is_shared(self) -> None:
        """Review 5 #10: the ambiguity rule must live in one place."""

        self.assertIsNone(AndroidSession.resolved_activity(()))
        self.assertEqual(AndroidSession.resolved_activity(("a/.A",)), "a/.A")
        self.assertIsNone(AndroidSession.resolved_activity(("a/.A", "b/.B")))


if __name__ == "__main__":
    unittest.main()


class RawLogRetentionTest(unittest.TestCase):
    """An hour of full logcat filled the disk; only evidence lines are retained.

    The filter must still keep every probe hit and every line of a fatal block,
    because the seal step re-parses the retained text for crashes.
    """

    def test_a_full_fatal_block_and_every_probe_hit_survive_the_filter(self) -> None:
        from valordroid.crashes import LiveOwnedCrashDetector, parse_app_crashes, parse_epoch_line

        package = "com.example.app"
        detector = LiveOwnedCrashDetector(package)
        lines = [
            "1.0 100 100 I ActivityManager: noise that must be dropped",
            "1.0 100 100 E AndroidRuntime: FATAL EXCEPTION: main",
            "1.0 100 100 E AndroidRuntime: Process: com.example.app, PID: 100",
            "1.0 100 100 E AndroidRuntime: java.lang.RuntimeException: boom",
            "1.0 100 100 E AndroidRuntime: \tat com.example.app.Foo.bar(Foo.java:1)",
            "9.0 100 100 D chatty: unrelated noise long after the block closed",
            "1.5 200 200 I VALORDROID: METHOD=<com.example.app.A: void m()>",
        ]
        kept = []
        for line in lines:
            is_crash = detector.feed(line)
            parsed = parse_epoch_line(line)
            relevant = is_crash or detector.active or (
                parsed is not None and parsed.tag == "VALORDROID"
            )
            if relevant:
                kept.append(line)
        # Noise before any fatal block began is dropped.
        self.assertNotIn(lines[0], kept)
        # Every probe hit is kept.
        self.assertIn(lines[6], kept)
        # The filter keeps far less than everything: the pre-block noise line is
        # gone, so the file cannot grow without bound from unrelated chatter.
        self.assertLess(len(kept), len(lines))
        # The complete fatal block is kept and still parses.
        crashes = parse_app_crashes("\n".join(kept), package=package, observed_at=1.0)
        self.assertEqual(len(crashes), 1)
        self.assertEqual(crashes[0].process, package)
