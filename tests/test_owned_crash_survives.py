"""An ordinary owned crash must not discard the run.

A crash once ended the run outright, which capped an app at its launch coverage.
That was fixed: the crash is retained as evidence and exploration relaunches on a
bounded allowance. The comment recording it still sits at the call site in
`runner.py`.

`_retain_live_owned_crash` then reintroduced the old behaviour by accident. It was
added to bind a crash to a foreign-workflow episode -- an episode that blames a
crash has to show a device occurrence interval inside its own window -- but its
first branch raised `RunAbort("coverage", "owned_crash_evidence_missing")` whenever
no episode was active. A foreign workflow has to be configured per app, and none
ever were, so the episode was always absent and every owned crash aborted the run.

Measured cost in the smoke campaign: Muzei reached 40.42% in the v2 campaign and
produced no valid run at all in either smoke arm, aborting on exactly this reason.
v2's four CRASH_LOOP runs averaged 23.44% coverage, all of which this branch would
have thrown away.

The episode strictness itself is correct and is kept: these tests pin both sides.
"""

from __future__ import annotations

import unittest

from valordroid.models import CrashRecord
from valordroid.runner import RunAbort, AndroidRunner


def _crash(
    crash_id: str,
    *,
    observed_at: float,
    device_started_at: float | None = None,
    device_ended_at: float | None = None,
) -> CrashRecord:
    return CrashRecord(
        crash_id=crash_id,
        observed_at=observed_at,
        package="com.example.app",
        process="com.example.app",
        exception_type="java.lang.IllegalStateException",
        first_app_frame="com.example.app.Main.onCreate",
        signature="sig-" + crash_id,
        lines=("FATAL EXCEPTION: main",),
        device_started_at=device_started_at,
        device_ended_at=device_ended_at,
    )


class _Core:
    def __init__(self, records):
        self._records = list(records)
        self.ingested = 0

    def record_crashes(self, text, *, observed_at):
        self.ingested += 1
        return list(self._records)


class _Collector:
    @staticmethod
    def evidence_text():
        return "FATAL EXCEPTION: main"


class _Episode:
    def __init__(self, started_at: float) -> None:
        self.started_at = started_at


def _runner(records, *, episode=None) -> AndroidRunner:
    runner = AndroidRunner.__new__(AndroidRunner)
    runner.core = _Core(records)
    runner.collector = _Collector()
    runner._active_foreign_episode = episode
    return runner


class OrdinaryCrashIsRetainedNotFatalTest(unittest.TestCase):
    def test_a_crash_outside_an_episode_does_not_abort(self) -> None:
        """The reproduction. No episode is configured, so none is active."""

        runner = _runner(
            [_crash("c1", observed_at=10.0, device_started_at=8.0, device_ended_at=9.0)]
        )
        # Must not raise. Raising here is what discarded whole runs.
        self.assertEqual(runner._retain_live_owned_crash(), "c1")

    def test_the_crash_is_still_ingested_as_evidence(self) -> None:
        runner = _runner([_crash("c1", observed_at=10.0)])
        runner._retain_live_owned_crash()
        self.assertEqual(runner.core.ingested, 1)

    def test_a_crash_the_parser_cannot_place_still_does_not_abort(self) -> None:
        """Device timestamps are absent, which is common and not fatal."""

        runner = _runner([_crash("c1", observed_at=10.0)])
        self.assertEqual(runner._retain_live_owned_crash(), "c1")

    def test_no_parsed_record_at_all_still_does_not_abort(self) -> None:
        runner = _runner([])
        self.assertEqual(runner._retain_live_owned_crash(), "")

    def test_the_newest_record_is_the_one_reported(self) -> None:
        runner = _runner(
            [
                _crash("old", observed_at=10.0, device_started_at=1.0, device_ended_at=2.0),
                _crash("new", observed_at=10.0, device_started_at=5.0, device_ended_at=6.0),
            ]
        )
        self.assertEqual(runner._retain_live_owned_crash(), "new")


class EpisodeBindingStaysStrictTest(unittest.TestCase):
    """Inside an episode the strict window check is the point and is kept."""

    def test_a_crash_inside_the_episode_window_binds(self) -> None:
        runner = _runner(
            [
                _crash(
                    "c1",
                    observed_at=100.0,
                    device_started_at=100.0,
                    device_ended_at=101.0,
                )
            ],
            episode=_Episode(99.0),
        )
        self.assertEqual(runner._retain_live_owned_crash(), "c1")

    def test_an_episode_cannot_blame_a_crash_it_cannot_place(self) -> None:
        """No device interval, so the episode has no occurrence to point at."""

        runner = _runner(
            [_crash("c1", observed_at=100.0)], episode=_Episode(99.0)
        )
        with self.assertRaises(RunAbort):
            runner._retain_live_owned_crash()

    def test_an_episode_cannot_blame_a_crash_that_predates_it(self) -> None:
        runner = _runner(
            [
                _crash(
                    "c1",
                    observed_at=100.0,
                    device_started_at=1.0,
                    device_ended_at=2.0,
                )
            ],
            episode=_Episode(1e9),
        )
        with self.assertRaises(RunAbort):
            runner._retain_live_owned_crash()


if __name__ == "__main__":
    unittest.main()
