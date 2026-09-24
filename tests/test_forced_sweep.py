"""Forced activation was in the wrong place in the loop.

It existed only as `RecoveryRung.FORCED_ACTIVATION`, the last rung of the recovery
ladder, which means it required the GUI to fail first. Measured over a six-app A/B
at 3600s with the rung otherwise fully working (root launch, gate widened):

    Nextcloud       120 plateaus  rung 7 x7   27.12% -> 38.84%  (+11.72)
    Geohash-Droid    53 plateaus  rung 7 x6   14.24% -> 28.29%  (+14.05)
    Orgzly-Revived   16 ladder events, rung 4 absorbed them     rung 7 x0
    AntennaPod       20 ladder events, rung 4 fired 12          rung 7 x0
    Binary-Eye        0 forced targets                          rung 7 x0
    AnkiDroid        routes.jsonl empty, recovery never invoked rung 7 x0

Both apps where it fired gained 12-14 points. The four where it did not sit inside
the +/-10 point run-to-run band, so they are noise. Two independent reasons blocked
it: an app that explores smoothly never invokes recovery at all, and in an app that
does, an earlier rung succeeding ends the episode before rung 7 is reached. Fixing
deep links made the second worse, because rung 4 now succeeds.

The sweep runs on saturation, ahead of the ladder, one target per stall. These
tests pin the parts that are easy to get wrong: the evidence contract, the
one-shot-per-target rule shared with rung 7, the unentered filter, and the
dead-shell correction without which forcing took Chess from 28.70% to 5.93%.
"""
from __future__ import annotations

import inspect
import unittest

from valordroid.recovery import RUNG_PROVENANCE, RecoveryRung
from valordroid.models import Provenance
from valordroid.routes import (
    _REPEATABLE_RUNGS,
    _ROUTE_DETAIL_FIELDS,
    android_route_targets,
)
from valordroid.runner import AndroidRunner


class ForcedSweepContractTest(unittest.TestCase):
    """The sweep must satisfy the same evidence contract as ladder rung 7."""

    def setUp(self) -> None:
        self.sweep = inspect.getsource(AndroidRunner._forced_sweep)
        self.targets = inspect.getsource(AndroidRunner._forced_sweep_targets)
        self.explore = inspect.getsource(AndroidRunner._explore)

    def test_the_route_is_recorded_before_the_attempt(self) -> None:
        """`record_action` rejects non-GUI provenance without a route_id."""
        route_at = self.sweep.index("record_route")
        attempt_at = self.sweep.index("_record_execution")
        self.assertLess(
            route_at,
            attempt_at,
            "the route authorizes the target and yields the route_id the attempt needs",
        )

    def test_it_records_the_forced_rung_and_forced_provenance(self) -> None:
        self.assertIn("RecoveryRung.FORCED_ACTIVATION", self.sweep)
        self.assertIn("Provenance.FORCED", self.sweep)
        self.assertIs(RUNG_PROVENANCE[RecoveryRung.FORCED_ACTIVATION], Provenance.FORCED)

    def test_route_details_carry_exactly_trigger_and_error(self) -> None:
        """rung 7 is not in _ROUTE_DETAIL_FIELDS, so it gets the strict default."""
        expected = _ROUTE_DETAIL_FIELDS.get(
            RecoveryRung.FORCED_ACTIVATION, frozenset({"trigger", "error"})
        )
        self.assertEqual(expected, frozenset({"trigger", "error"}))
        self.assertIn('"trigger": trigger, "error": execution.error', self.sweep)

    def test_the_sweep_is_distinguishable_from_a_ladder_launch(self) -> None:
        """Same rung and schema, different trigger label, so evidence separates them."""
        self.assertEqual(AndroidRunner.FORCED_SWEEP_TRIGGER, "forced_sweep")
        self.assertIn("trigger = self.FORCED_SWEEP_TRIGGER", self.sweep)

    def test_it_launches_with_root_because_targets_are_not_exported(self) -> None:
        self.assertIn("allow_root=True", self.sweep)

    def test_it_is_gated_on_allow_forced_routes(self) -> None:
        """The master switch for forcing, and the only variable in the A/B."""
        self.assertIn("self.core.config.allow_forced_routes", self.targets)

    def test_targets_are_filtered_to_never_entered_activities(self) -> None:
        """Relaunching a screen already stood in spends a one-shot target for nothing."""
        self.assertIn("_entered_activities()", self.targets)
        self.assertIn("_activity_target_was_entered", self.targets)

    def test_one_shot_per_target_is_enforced_by_the_route_ledger(self) -> None:
        """Not by the sweep: rung 7 is not repeatable, so a reuse raises."""
        self.assertNotIn(RecoveryRung.FORCED_ACTIVATION, _REPEATABLE_RUNGS)

    def test_forced_targets_are_unauthorized_when_forcing_is_off(self) -> None:
        """Defence in depth: even a bug in the sweep cannot launch an unlisted target."""

        class _Runtime:
            deep_links = ()
            exported_components = ()
            forced_components = ("pkg/.Internal",)
            reset_seed_enabled = False

        self.assertEqual(
            android_route_targets(
                RecoveryRung.FORCED_ACTIVATION, _Runtime(), "pkg",
                allow_forced_routes=False,
            ),
            (),
        )
        self.assertEqual(
            android_route_targets(
                RecoveryRung.FORCED_ACTIVATION, _Runtime(), "pkg",
                allow_forced_routes=True,
            ),
            ("pkg/.Internal",),
        )

    def test_the_dead_shell_correction_is_applied(self) -> None:
        """The protection that made forcing safe; Chess 28.70% -> 5.93% without it."""
        self.assertIn("_launch_landed_dead", self.sweep)
        self.assertIn("_return_to_app", self.sweep)

    def test_it_does_not_mutate_ladder_state(self) -> None:
        """`record_result` and `reset` persist episode/failed_rungs to recovery.json."""
        self.assertNotIn("recovery.record_result", self.sweep)
        self.assertNotIn("recovery.reset", self.sweep)

    def test_it_returns_none_when_there_is_nothing_to_sweep(self) -> None:
        """The caller must fall through to the unchanged ladder."""
        self.assertIn("return None", self.sweep)


class ForcedSweepPlacementTest(unittest.TestCase):
    """Where it sits in the loop is the entire point of the change."""

    def setUp(self) -> None:
        self.explore = inspect.getsource(AndroidRunner._explore)

    def test_it_runs_before_the_plateau_ladder_call(self) -> None:
        sweep_at = self.explore.index("swept = self._forced_sweep(before)")
        recover_at = self.explore.index('trigger="coverage_plateau"')
        self.assertLess(
            sweep_at,
            recover_at,
            "waiting for the ladder is what left rung 7 at 0 fires in 111 runs",
        )

    def test_it_also_runs_on_an_exhausted_screen(self) -> None:
        """Three call sites: plateau, exhausted screen, and the paced step path."""
        self.assertEqual(self.explore.count("self._forced_sweep(before)"), 3)

    def test_it_does_not_run_on_a_transient_cooldown(self) -> None:
        """A candidate returning after its cooldown is not saturation."""
        self.assertIn('if trigger != "temporarily_no_eligible":', self.explore)

    def test_a_sweep_takes_the_step_and_continues(self) -> None:
        """It must not also fall through into the ladder in the same iteration."""
        block = self.explore[self.explore.index("swept = self._forced_sweep(before)") :][:220]
        self.assertIn("before = swept", block)
        self.assertIn("continue", block)

    def test_it_also_fires_on_the_ordinary_step_path(self) -> None:
        """An app that never stalls reaches neither recovery trigger.

        Measured at 700s with the sweep on the stall paths only: AnkiDroid fired
        it 0 times holding 19 unentered non-exported activities, because it
        explores smoothly and never stalls. Three call sites, not two.
        """
        self.assertEqual(self.explore.count("self._forced_sweep(before)"), 3)
        self.assertIn("if self._forced_sweep_due():", self.explore)

    def test_the_step_path_fires_before_a_gui_action_is_chosen(self) -> None:
        due_at = self.explore.index("if self._forced_sweep_due():")
        select_at = self.explore.index("selected: ActionCandidate = allowed[0].candidate")
        self.assertLess(due_at, select_at)


class ForcedSweepPacingTest(unittest.TestCase):
    """Unpaced, the sweep would drain every target in consecutive actions."""

    def setUp(self) -> None:
        self.due = inspect.getsource(AndroidRunner._forced_sweep_due)

    def test_it_waits_for_the_component_launch_clock(self) -> None:
        """Early budget is when a tap is worth more."""
        self.assertIn("_component_launches_earned()", self.due)

    def test_it_enforces_a_minimum_action_gap(self) -> None:
        self.assertIn("FORCED_SWEEP_MIN_ACTION_GAP", self.due)
        self.assertGreaterEqual(AndroidRunner.FORCED_SWEEP_MIN_ACTION_GAP, 2)

    def test_the_gap_is_measured_from_the_last_launch(self) -> None:
        self.assertIn("_last_sweep_attempt", self.due)
        sweep = inspect.getsource(AndroidRunner._forced_sweep)
        self.assertIn("self._last_sweep_attempt = self.core.attempt_count", sweep)

    def test_the_marker_starts_at_zero(self) -> None:
        init = inspect.getsource(AndroidRunner.__init__)
        self.assertIn("self._last_sweep_attempt = 0", init)


if __name__ == "__main__":
    unittest.main()
