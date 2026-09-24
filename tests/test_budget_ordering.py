"""A run must spend its time budget, not end early on bookkeeping.

Ten runs in the 50-app campaign stopped before their hour was up and left 5.5
hours of device time unused. Two causes: the ladder-reset counter was checked
before the clock, so a run that was still exploring was ended by a counter
(Scarlet-Notes stopped at 25 of 60 minutes holding 33.80%); and a flat allowance
of ten crash relaunches capped crash-prone apps at their launch coverage
(Jellyfin crashed eleven times and ended after 3.7 minutes).
"""

from __future__ import annotations

import unittest

from valordroid.config import CoreConfig
from valordroid.runner import AndroidRunner
from valordroid.store import (
    EXPLORATION_BUDGET_STARTED,
    EXPLORATION_STOPPED,
    derive_budget_accounting,
)


def _runner(*, max_seconds: float, max_actions: int, core: CoreConfig | None = None):
    runner = AndroidRunner.__new__(AndroidRunner)
    runner.runtime = type(
        "R", (), {"max_seconds": max_seconds, "max_actions": max_actions}
    )()
    runner.core = type("C", (), {"config": core or CoreConfig()})()
    return runner


class LadderResetBudgetTest(unittest.TestCase):
    def test_the_configured_value_is_a_floor_not_a_ceiling(self):
        runner = _runner(max_seconds=3600.0, max_actions=20000)
        self.assertGreaterEqual(
            runner._ladder_reset_budget(), runner.core.config.max_ladder_resets
        )

    def test_the_budget_scales_with_the_action_budget(self):
        small = _runner(max_seconds=600.0, max_actions=100)
        large = _runner(max_seconds=3600.0, max_actions=20000)
        self.assertGreater(large._ladder_reset_budget(), small._ladder_reset_budget())

    def test_a_short_run_still_gets_the_configured_allowance(self):
        runner = _runner(max_seconds=600.0, max_actions=10)
        self.assertEqual(
            runner._ladder_reset_budget(), runner.core.config.max_ladder_resets
        )


class CrashRelaunchBudgetTest(unittest.TestCase):
    def test_an_hour_long_run_tolerates_more_than_the_flat_ten(self):
        runner = _runner(max_seconds=3600.0, max_actions=20000)
        self.assertGreater(runner._crash_relaunch_budget(), 10)
        # Jellyfin's eleventh crash must not be the end of its run.
        self.assertGreaterEqual(runner._crash_relaunch_budget(), 11)

    def test_a_short_run_keeps_the_configured_allowance(self):
        runner = _runner(max_seconds=300.0, max_actions=20000)
        self.assertEqual(
            runner._crash_relaunch_budget(), runner.core.config.max_crash_relaunches
        )

    def test_the_allowance_is_finite_so_a_broken_app_still_stops(self):
        runner = _runner(max_seconds=3600.0, max_actions=20000)
        budget = runner._crash_relaunch_budget()
        self.assertLess(budget, 10000)
        self.assertIsInstance(budget, int)


def _lifecycle(event: str, observed_at: float) -> dict:
    return {
        "record_type": "lifecycle",
        "event": {
            "record_id": f"l-{event}",
            "observed_at": observed_at,
            "phase": "exploration",
            "event": event,
            "status": "info",
            "details": {},
            "error": None,
        },
    }


class SpentBudgetIsRecordedTest(unittest.TestCase):
    """The ten runs that stopped early left no trace of it in their manifests.

    Scarlet-Notes stopped at 25 of 60 minutes and its manifest recorded only
    `max_seconds=3600`. Whatever ends a run, the sealed evidence must state what
    the run declared and what it actually spent, so an underspent run cannot be
    averaged against a full one as though the budgets matched.
    """

    def test_an_underspent_run_shows_the_gap_against_its_declared_budget(self):
        accounting = derive_budget_accounting(
            {
                "created_at": 0.0,
                "finished_at": 1500.0,
                "runtime_configuration": {"max_seconds": 3600.0},
            },
            [
                _lifecycle(EXPLORATION_BUDGET_STARTED, 30.0),
                _lifecycle(EXPLORATION_STOPPED, 1480.0),
            ],
        )
        self.assertEqual(accounting["budget_declared_seconds"], 3600.0)
        self.assertEqual(accounting["exploration_seconds"], 1450.0)
        self.assertEqual(accounting["total_wall_clock_seconds"], 1500.0)
        self.assertLess(
            accounting["total_wall_clock_seconds"],
            accounting["budget_declared_seconds"],
            "a 25-minute run must not be indistinguishable from a full hour",
        )

    def test_a_run_that_fills_its_hour_reports_the_full_window(self):
        accounting = derive_budget_accounting(
            {
                "created_at": 0.0,
                "finished_at": 3700.0,
                "runtime_configuration": {"max_seconds": 3600.0},
            },
            [
                _lifecycle(EXPLORATION_BUDGET_STARTED, 40.0),
                _lifecycle(EXPLORATION_STOPPED, 3640.0),
            ],
        )
        self.assertEqual(accounting["exploration_seconds"], 3600.0)
        self.assertEqual(accounting["setup_seconds"], 40.0)
        self.assertEqual(accounting["teardown_seconds"], 60.0)


if __name__ == "__main__":
    unittest.main()
