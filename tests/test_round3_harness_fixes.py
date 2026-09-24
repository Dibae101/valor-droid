"""Round-3 harness fixes from the batch-2 log analysis.

Six issues, each with a config knob following the existing CoreConfig
pattern:

1. FrostDebug spin loop -- ~25,000 identical rung-2 selections while every
   relaunch landed on the same login activity. The spin breaker trips after
   `max_identical_rung_selections` consecutive zero-gain selections of one
   rung, force-escalates one rung 8, and ends the run with `credential_wall`
   when the screen asks for credentials.
2. Exported-component sweep gap -- FeederD declared every activity exported,
   so the forced list was empty by construction and the sweep never fired.
   The sweep now falls back to exported-but-unentered components (rung 5's
   pool, no root, INTENT provenance).
3. LLM gate starvation -- episodes died at rung 2 (BACK "executed", zero
   gain) and reset before the two-failure gate opened. Zero-gain rungs now
   count as failures; a deep link that did not change the foreground
   activity always does.
4. Dead recorded transitions -- Commons replayed one long_press against a
   gone login screen 210x. A transition is retired after
   `max_dead_transition_strikes` consecutive `not_executed` outcomes.
5. Crash blacklist -- the same NPE cost one relaunch per retry. After
   `max_identical_crashes_before_blacklist` identical signatures the
   attributed target is skipped by exploration and recovery.
6. Long-press on a zero-size view -- AntennaPod died in
   View.startDragAndDrop ("Drag shadow dimensions must be positive").
   long_press is no longer offered on zero-size nodes.

No test here touches a device or the network.
"""

from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from typing import Any

from valordroid.android.hierarchy import UiNode
from valordroid.android.observe import has_positive_size
from valordroid.config import CoreConfig
from valordroid.crashes import crash_signature
from valordroid.models import ExecutionStatus, Provenance, RouteAttempt
from valordroid.recovery import RecoveryRung
from valordroid.runner import (
    AndroidRunner,
    _CredentialWall,
    _transition_key,
)


def _core_config(**overrides: Any) -> CoreConfig:
    values: dict[str, Any] = {
        "model_assistance_enabled": True,
        "max_model_calls": 48,
    }
    values.update(overrides)
    return CoreConfig(**values)


def _runner_stub(**config_overrides: Any) -> AndroidRunner:
    """A runner with only the attributes the round-3 methods need."""

    runner = AndroidRunner.__new__(AndroidRunner)
    runner.core = SimpleNamespace(config=_core_config(**config_overrides))  # type: ignore[attr-defined]
    runner._spin_last_rung = None  # type: ignore[attr-defined]
    runner._spin_streak = 0  # type: ignore[attr-defined]
    runner._spin_force_reset_seed = False  # type: ignore[attr-defined]
    runner._dead_transition_strikes = {}  # type: ignore[attr-defined]
    runner._retired_transitions = set()  # type: ignore[attr-defined]
    runner._crash_blacklisted_targets = set()  # type: ignore[attr-defined]
    runner.lifecycle_events = []  # type: ignore[attr-defined]

    def _lifecycle(phase: str, event: str, status: Any, **kwargs: Any) -> None:
        runner.lifecycle_events.append((phase, event, kwargs.get("details")))  # type: ignore[attr-defined]

    runner._lifecycle = _lifecycle  # type: ignore[method-assign]
    runner._asks_for_credentials = lambda: False  # type: ignore[method-assign]
    return runner


def _route(details: dict[str, Any], **overrides: Any) -> RouteAttempt:
    values: dict[str, Any] = {
        "route_id": "r-1",
        "provenance": Provenance.INTENT,
        "rung": int(RecoveryRung.KNOWN_GUI_ROUTE),
        "requested_at": 0.0,
        "target": "state-9",
        "outcome": "executed",
        "details": details,
    }
    values.update(overrides)
    return RouteAttempt(**values)  # type: ignore[arg-type]


def _rung3_details(selector: str = "sel-1") -> dict[str, Any]:
    return {
        "action": {"kind": "long_press", "parameters": {"selector": selector}},
        "immediate_state_id": "login-screen",
        "remaining_steps": 0,
        "motivating_candidate_id": "cand-1",
        "rule": "recorded_transition_bfs_v1",
    }


class ConfigKnobTests(unittest.TestCase):
    def test_defaults(self) -> None:
        config = _core_config()
        self.assertEqual(config.max_identical_rung_selections, 8)
        self.assertTrue(config.forced_sweep_exported_fallback)
        self.assertTrue(config.recovery_zero_gain_counts_as_failure)
        self.assertEqual(config.max_dead_transition_strikes, 3)
        self.assertEqual(config.max_identical_crashes_before_blacklist, 2)

    def test_negative_bounds_rejected(self) -> None:
        for kwargs in (
            {"max_identical_rung_selections": -1},
            {"max_dead_transition_strikes": -1},
            {"max_identical_crashes_before_blacklist": -1},
        ):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                _core_config(**kwargs)

    def test_zero_disables(self) -> None:
        config = _core_config(
            max_identical_rung_selections=0,
            max_dead_transition_strikes=0,
            max_identical_crashes_before_blacklist=0,
        )
        self.assertEqual(config.max_identical_rung_selections, 0)
        self.assertEqual(config.max_dead_transition_strikes, 0)
        self.assertEqual(config.max_identical_crashes_before_blacklist, 0)

    def test_wrong_types_rejected(self) -> None:
        for kwargs in (
            {"max_identical_rung_selections": 1.5},
            {"max_dead_transition_strikes": "3"},  # type: ignore[dict-item]
            {"max_identical_crashes_before_blacklist": None},  # type: ignore[dict-item]
            {"forced_sweep_exported_fallback": "yes"},  # type: ignore[dict-item]
            {"recovery_zero_gain_counts_as_failure": 1},  # type: ignore[dict-item]
        ):
            with self.assertRaises(TypeError, msg=str(kwargs)):
                _core_config(**kwargs)


class SpinBreakerTests(unittest.TestCase):
    def test_streak_fires_at_the_bound(self) -> None:
        runner = _runner_stub(max_identical_rung_selections=3)
        runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)
        runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)
        self.assertFalse(runner._spin_force_reset_seed)
        runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)
        self.assertTrue(runner._spin_force_reset_seed)
        # The streak resets when the breaker fires, so it cannot re-fire
        # immediately on the next selection.
        self.assertEqual(runner._spin_streak, 0)
        self.assertIsNone(runner._spin_last_rung)

    def test_gain_breaks_the_streak(self) -> None:
        runner = _runner_stub(max_identical_rung_selections=3)
        runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)
        runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)
        runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 5)
        runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)
        self.assertFalse(runner._spin_force_reset_seed)
        self.assertEqual(runner._spin_streak, 1)

    def test_different_rung_restarts_the_streak(self) -> None:
        runner = _runner_stub(max_identical_rung_selections=3)
        runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)
        runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)
        runner._note_recovery_spin(RecoveryRung.DEEP_LINK, 0)
        self.assertFalse(runner._spin_force_reset_seed)
        self.assertEqual(runner._spin_streak, 1)
        self.assertIs(runner._spin_last_rung, RecoveryRung.DEEP_LINK)

    def test_zero_disables_the_breaker(self) -> None:
        runner = _runner_stub(max_identical_rung_selections=0)
        for _ in range(50):
            runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)
        self.assertFalse(runner._spin_force_reset_seed)

    def test_credential_screen_raises_credential_wall(self) -> None:
        runner = _runner_stub(max_identical_rung_selections=2)
        runner._asks_for_credentials = lambda: True  # type: ignore[method-assign]
        runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)
        with self.assertRaises(_CredentialWall):
            runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)

    def test_break_is_recorded_in_evidence(self) -> None:
        runner = _runner_stub(max_identical_rung_selections=1)
        runner._note_recovery_spin(RecoveryRung.BACK_OR_REVEAL, 0)
        events = [
            details
            for _, event, details in runner.lifecycle_events
            if event == "recovery_spin_broken"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["consecutive_zero_gain_selections"],
            1,
            "the event must carry the observed streak, not the configured bound",
        )


class DeadTransitionTests(unittest.TestCase):
    def test_transition_key_shape(self) -> None:
        self.assertEqual(
            _transition_key("state-1", "long_press", "sel-9"),
            ("state-1", "long_press:sel-9"),
        )

    def test_route_transition_key_matches_plan_key(self) -> None:
        route = _route(_rung3_details())
        self.assertEqual(
            AndroidRunner._route_transition_key(route),
            ("login-screen", "long_press:sel-1"),
        )

    def test_route_transition_key_none_without_details(self) -> None:
        route = _route({})
        self.assertIsNone(AndroidRunner._route_transition_key(route))

    def test_three_strikes_retire(self) -> None:
        runner = _runner_stub(max_dead_transition_strikes=3)
        route = _route(_rung3_details())
        runner._note_transition_result(route, ExecutionStatus.NOT_EXECUTED)
        runner._note_transition_result(route, ExecutionStatus.NOT_EXECUTED)
        self.assertEqual(len(runner._retired_transitions), 0)
        runner._note_transition_result(route, ExecutionStatus.NOT_EXECUTED)
        self.assertEqual(
            runner._retired_transitions, {("login-screen", "long_press:sel-1")}
        )
        events = [event for _, event, _ in runner.lifecycle_events]
        self.assertIn("recovery_dead_transition_retired", events)

    def test_executed_clears_strikes(self) -> None:
        runner = _runner_stub(max_dead_transition_strikes=3)
        route = _route(_rung3_details())
        runner._note_transition_result(route, ExecutionStatus.NOT_EXECUTED)
        runner._note_transition_result(route, ExecutionStatus.NOT_EXECUTED)
        runner._note_transition_result(route, ExecutionStatus.EXECUTED)
        runner._note_transition_result(route, ExecutionStatus.NOT_EXECUTED)
        runner._note_transition_result(route, ExecutionStatus.NOT_EXECUTED)
        self.assertEqual(len(runner._retired_transitions), 0)

    def test_failed_execution_is_neither_strike_nor_clear(self) -> None:
        runner = _runner_stub(max_dead_transition_strikes=2)
        route = _route(_rung3_details())
        runner._note_transition_result(route, ExecutionStatus.FAILED)
        self.assertEqual(runner._dead_transition_strikes, {})
        runner._note_transition_result(route, ExecutionStatus.NOT_EXECUTED)
        runner._note_transition_result(route, ExecutionStatus.FAILED)
        # Only the single NOT_EXECUTED counted: not retired yet.
        self.assertEqual(len(runner._retired_transitions), 0)

    def test_zero_disables_retirement(self) -> None:
        runner = _runner_stub(max_dead_transition_strikes=0)
        route = _route(_rung3_details())
        for _ in range(10):
            runner._note_transition_result(route, ExecutionStatus.NOT_EXECUTED)
        self.assertEqual(len(runner._retired_transitions), 0)

    def test_strikes_are_per_transition(self) -> None:
        runner = _runner_stub(max_dead_transition_strikes=2)
        route_a = _route(_rung3_details(selector="sel-a"))
        route_b = _route(_rung3_details(selector="sel-b"))
        runner._note_transition_result(route_a, ExecutionStatus.NOT_EXECUTED)
        runner._note_transition_result(route_b, ExecutionStatus.NOT_EXECUTED)
        self.assertEqual(len(runner._retired_transitions), 0)
        runner._note_transition_result(route_a, ExecutionStatus.NOT_EXECUTED)
        self.assertEqual(len(runner._retired_transitions), 1)


def _crash_record(crash_id: str, **overrides: Any) -> Any:
    from valordroid.models import CrashRecord

    values: dict[str, Any] = {
        "crash_id": crash_id,
        "observed_at": 1.0,
        "package": "com.example",
        "process": "com.example",
        "exception_type": "java.lang.NullPointerException",
        "first_app_frame": "com.example.FeedActivity.onCreate",
        "signature": crash_signature(
            package="com.example",
            process="com.example",
            exception_type="java.lang.NullPointerException",
            first_app_frame="com.example.FeedActivity.onCreate",
        ),
        "lines": (),
    }
    values.update(overrides)
    return CrashRecord(**values)  # type: ignore[arg-type]


class CrashBlacklistTests(unittest.TestCase):
    def _blacklist_runner(self, **config_overrides: Any) -> AndroidRunner:
        runner = _runner_stub(**config_overrides)
        route = _route(
            {"trigger": "forced_sweep", "error": None},
            route_id="r-7",
            rung=int(RecoveryRung.FORCED_ACTIVATION),
            provenance=Provenance.FORCED,
            target="com.example/.FeedActivity",
            outcome="executed",
        )
        attempt = SimpleNamespace(route_id="r-7", state_id="s-1", requested=None)
        runner.core = SimpleNamespace(  # type: ignore[attr-defined]
            config=runner.core.config,
            crash_records=(
                _crash_record("c-1"),
                _crash_record("c-2"),
            ),
            route_records=(route,),
            attempts=(attempt,),
        )
        return runner

    def test_repeat_signature_blacklists_the_route_target(self) -> None:
        runner = self._blacklist_runner()
        runner._note_crash_for_blacklist("c-2")
        self.assertEqual(
            runner._crash_blacklisted_targets, {"com.example/.FeedActivity"}
        )
        events = [event for _, event, _ in runner.lifecycle_events]
        self.assertIn("crash_target_blacklisted", events)

    def test_single_crash_does_not_blacklist(self) -> None:
        runner = self._blacklist_runner()
        runner.core.crash_records = (_crash_record("c-1"),)  # type: ignore[attr-defined]
        runner._note_crash_for_blacklist("c-1")
        self.assertEqual(runner._crash_blacklisted_targets, set())

    def test_different_signatures_do_not_combine(self) -> None:
        runner = self._blacklist_runner()
        other = _crash_record(
            "c-3",
            exception_type="java.lang.IllegalStateException",
            first_app_frame="com.example.Other.onResume",
            signature=crash_signature(
                package="com.example",
                process="com.example",
                exception_type="java.lang.IllegalStateException",
                first_app_frame="com.example.Other.onResume",
            ),
        )
        runner.core.crash_records = (_crash_record("c-1"), other)  # type: ignore[attr-defined]
        runner._note_crash_for_blacklist("c-1")
        self.assertEqual(runner._crash_blacklisted_targets, set())

    def test_zero_disables_blacklisting(self) -> None:
        runner = self._blacklist_runner(max_identical_crashes_before_blacklist=0)
        runner._note_crash_for_blacklist("c-2")
        self.assertEqual(runner._crash_blacklisted_targets, set())

    def test_unknown_crash_id_is_ignored(self) -> None:
        runner = self._blacklist_runner()
        runner._note_crash_for_blacklist("no-such-crash")
        self.assertEqual(runner._crash_blacklisted_targets, set())

    def test_attribution_falls_back_to_candidate_stable_id(self) -> None:
        from valordroid.models import ActionCandidate, ActionSpec

        runner = _runner_stub()
        action = ActionSpec("tap", parameters={"selector": "sel-5"})
        attempt = SimpleNamespace(route_id=None, state_id="s-9", requested=action)
        runner.core = SimpleNamespace(  # type: ignore[attr-defined]
            config=runner.core.config,
            crash_records=(),
            route_records=(),
            attempts=(attempt,),
        )
        self.assertEqual(
            runner._attribute_crash_target(),
            ActionCandidate("s-9", action).stable_id,
        )

    def test_attribution_prefers_the_route_target(self) -> None:
        runner = self._blacklist_runner()
        self.assertEqual(
            runner._attribute_crash_target(), "com.example/.FeedActivity"
        )


class LongPressSizeTests(unittest.TestCase):
    def _node(self, bounds: tuple[int, int, int, int]) -> UiNode:
        return UiNode(
            selector={"package": "com.example"},
            bounds=bounds,
            attributes={"long-clickable": "true"},
        )

    def test_positive_size_passes(self) -> None:
        self.assertTrue(has_positive_size(self._node((0, 0, 100, 200))))

    def test_zero_width_rejected(self) -> None:
        self.assertFalse(has_positive_size(self._node((50, 0, 50, 200))))

    def test_zero_height_rejected(self) -> None:
        self.assertFalse(has_positive_size(self._node((0, 70, 100, 70))))

    def test_inverted_bounds_rejected(self) -> None:
        self.assertFalse(has_positive_size(self._node((100, 0, 0, 200))))


class Round3WiringTests(unittest.TestCase):
    """Pin the wiring the unit tests above cannot reach without a device.

    Same style as test_forced_sweep.py: source contracts over the runner and
    observer, so a refactor that drops a hook fails loudly here.
    """

    def setUp(self) -> None:
        self.recover = inspect.getsource(AndroidRunner._recover)
        self.choices = inspect.getsource(AndroidRunner._recovery_choices)
        self.context = inspect.getsource(AndroidRunner._recovery_context)
        self.sweep = inspect.getsource(AndroidRunner._forced_sweep)
        self.untried = inspect.getsource(AndroidRunner._untried_recovery_targets)
        self.explore = inspect.getsource(AndroidRunner._explore)

    def test_spin_is_tracked_in_the_result_section(self) -> None:
        self.assertIn("_note_recovery_spin(decision.rung, gain)", self.recover)

    def test_spin_breaker_can_force_rung_8(self) -> None:
        self.assertIn("_spin_force_reset_seed", self.choices)
        self.assertIn("RecoveryRung.RESET_SEED", self.choices)

    def test_credential_wall_ends_the_run(self) -> None:
        self.assertIn("_CredentialWall", self.explore)
        self.assertEqual(self.explore.count('return "credential_wall"'), 2)

    def test_sweep_falls_back_to_exported_targets(self) -> None:
        self.assertIn("_exported_sweep_targets()", self.sweep)
        self.assertIn("RecoveryRung.EXPORTED_INTENT", self.sweep)
        self.assertIn("forced_sweep_exported_fallback", inspect.getsource(AndroidRunner._exported_sweep_targets))

    def test_sweep_fallback_uses_no_root_and_intent_provenance(self) -> None:
        self.assertIn("Provenance.INTENT", self.sweep)
        # Root stays reserved for the forced path only.
        self.assertEqual(self.sweep.count("allow_root=True"), 1)

    def test_deep_link_noop_counts_as_failure(self) -> None:
        self.assertIn("recovery_deep_link_no_foreground_change", self.recover)
        self.assertIn("after.activity == before.activity", self.recover)

    def test_zero_gain_counts_as_failure_when_enabled(self) -> None:
        self.assertIn("recovery_zero_gain_counts_as_failure", self.recover)

    def test_dead_transitions_are_skipped_in_context(self) -> None:
        self.assertIn("_retired_transitions", self.context)
        self.assertIn("recovery_dead_transition_skipped", self.context)

    def test_blacklist_filters_recovery_targets(self) -> None:
        self.assertIn("_crash_blacklisted_targets", self.untried)

    def test_blacklist_filters_exploration_candidates(self) -> None:
        self.assertIn("_crash_blacklisted_targets", self.explore)

    def test_crash_branch_notes_the_blacklist(self) -> None:
        self.assertIn("_note_crash_for_blacklist(crash_id)", self.explore)

    def test_long_press_requires_positive_size(self) -> None:
        import valordroid.android.observe as observe

        self.assertIn("has_positive_size(node)", inspect.getsource(observe))


if __name__ == "__main__":
    unittest.main()
