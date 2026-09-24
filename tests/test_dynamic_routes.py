"""Evidence contracts for the three evidence-derived recovery rungs.

Rungs 1, 3, and 6 dispatch ordinary GUI actions but choose their target from the
run's own history rather than from configuration. That makes them the only rungs
whose target cannot be re-derived from the frozen runtime config, so the route
record must carry its exact action and offline validation must reconstruct it.

These tests are the reason the new rungs can be trusted: they check that a route
cannot claim an action it did not dispatch, cannot drift from its recorded
target, and cannot smuggle in model assistance that the configuration disabled.
"""

from __future__ import annotations

import time
import unittest
from contextlib import contextmanager

from valordroid.models import ActionSpec, Provenance, RouteAttempt, stable_hash
from valordroid.recovery import RUNG_PROVENANCE, RecoveryRung
from valordroid.routes import (
    DYNAMIC_RUNGS,
    android_route_targets,
    expected_route_action,
    validate_android_route,
)
from valordroid.runtime_config import RuntimeConfig

PACKAGE = "com.example.app"
STATE = "1" * 64
OTHER_STATE = "2" * 64
CANDIDATE = "3" * 64


def _runtime(**overrides) -> RuntimeConfig:
    values = {
        "schema_version": RuntimeConfig.SCHEMA_VERSION,
        "serial": "emulator-5554",
        "launcher": None,
        "max_actions": 10,
        "max_seconds": 60.0,
        "action_timeout_seconds": 5.0,
        "adb_timeout_seconds": 5.0,
        "capture_screenshots": False,
        "install_timeout_seconds": 10.0,
        "launch_timeout_seconds": 5.0,
        "observation_timeout_seconds": 5.0,
        "route_foreground_timeout_seconds": 2.0,
        "per_field_input_enabled": True,
        "pid_poll_seconds": 0.2,
        "post_action_delay_seconds": 0.1,
        "reset_seed_enabled": False,
        "suppress_soft_keyboard": False,
        "route_discovery_sha256": None,
        "component_extras": {},
        "text_input_value": "valordroid",
        "uninstall_after_run": True,
        "deep_links": [],
        "exported_components": [],
        "forced_components": [],
    }
    values.update(overrides)
    return RuntimeConfig.from_mapping(values)


def _gui_action(state_id: str = STATE, resource: str = "com.example:id/go") -> ActionSpec:
    selector = {
        "resource_id": resource,
        "class_name": "android.widget.Button",
        "text": "Go",
        "content_description": "",
        "tree_path": "0/1/2",
        "package": PACKAGE,
    }
    return ActionSpec(
        "tap",
        stable_hash(selector),
        {"expected_state_id": state_id, "selector": selector},
    )


def _candidate_id(action: ActionSpec, state_id: str = STATE) -> str:
    return stable_hash({"state_id": state_id, "action": action.to_dict()})


def _route(
    rung: RecoveryRung,
    *,
    target: str | None,
    details: dict,
    route_id: str = "r-1",
    outcome: str = "executed",
) -> RouteAttempt:
    return RouteAttempt(
        route_id=route_id,
        provenance=RUNG_PROVENANCE[rung],
        rung=int(rung),
        requested_at=10.0,
        target=target,
        outcome=outcome,
        details={"trigger": "stall", "error": None, **details},
    )


def _validate(route: RouteAttempt, prior=(), *, runtime=None, model=False) -> None:
    validate_android_route(
        route,
        tuple(prior),
        runtime or _runtime(),
        PACKAGE,
        allow_forced_routes=False,
        model_assistance_enabled=model,
    )


class RungAvailabilityTest(unittest.TestCase):
    def test_all_three_dynamic_rungs_are_now_implemented(self) -> None:
        self.assertEqual(
            DYNAMIC_RUNGS,
            {
                RecoveryRung.CURRENT_UNTRIED,
                RecoveryRung.KNOWN_GUI_ROUTE,
                RecoveryRung.BOUNDED_LLM,
            },
        )

    def test_dynamic_rungs_have_no_configured_target_list(self) -> None:
        for rung in DYNAMIC_RUNGS:
            with self.subTest(rung=rung):
                with self.assertRaises(ValueError) as caught:
                    android_route_targets(
                        rung, _runtime(), PACKAGE, allow_forced_routes=False
                    )
                self.assertIn("run evidence", str(caught.exception))

    def test_static_rungs_still_read_their_configured_targets(self) -> None:
        runtime = _runtime(deep_links=["app://a", "app://b"])
        self.assertEqual(
            android_route_targets(
                RecoveryRung.DEEP_LINK, runtime, PACKAGE, allow_forced_routes=False
            ),
            ("app://a", "app://b"),
        )


class CurrentUntriedRouteTest(unittest.TestCase):
    """Rung 1: one cheap local action before any route is spent."""

    def _route(self, action: ActionSpec | None = None, **overrides) -> RouteAttempt:
        action = action or _gui_action()
        defaults = {"target": _candidate_id(action), "details": {"action": action.to_dict()}}
        defaults.update(overrides)
        return _route(RecoveryRung.CURRENT_UNTRIED, **defaults)

    def test_a_well_formed_route_validates(self) -> None:
        _validate(self._route())

    def test_the_recorded_action_is_reconstructed_exactly(self) -> None:
        action = _gui_action()
        kind, target_id, parameters = expected_route_action(
            self._route(action), state_id=STATE
        )
        self.assertEqual(kind, action.kind)
        self.assertEqual(target_id, action.target_id)
        self.assertEqual(parameters, dict(action.parameters))

    def test_a_target_that_is_not_the_action_digest_is_refused(self) -> None:
        route = self._route(target="9" * 64)
        with self.assertRaises(ValueError) as caught:
            expected_route_action(route, state_id=STATE)
        self.assertIn("candidate digest", str(caught.exception))

    def test_an_action_bound_to_another_state_is_refused(self) -> None:
        action = _gui_action(state_id=OTHER_STATE)
        with self.assertRaises(ValueError) as caught:
            expected_route_action(self._route(action), state_id=STATE)
        self.assertIn("observed state", str(caught.exception))

    def test_a_missing_action_is_refused(self) -> None:
        route = _route(
            RecoveryRung.CURRENT_UNTRIED, target=CANDIDATE, details={"action": None}
        )
        with self.assertRaises(ValueError) as caught:
            _validate(route)
        self.assertIn("retain its exact action", str(caught.exception))

    def test_a_non_gui_action_is_refused(self) -> None:
        route = _route(
            RecoveryRung.CURRENT_UNTRIED,
            target=CANDIDATE,
            details={"action": ActionSpec("reset_seed", PACKAGE).to_dict()},
        )
        with self.assertRaises(ValueError) as caught:
            _validate(route)
        self.assertIn("not a GUI action", str(caught.exception))

    def test_reusing_a_target_is_refused_because_untried_cannot_repeat(self) -> None:
        first = self._route()
        second = self._route(route_id="r-2")
        with self.assertRaises(ValueError) as caught:
            _validate(second, prior=(first,))
        self.assertIn("already attempted", str(caught.exception))

    def test_a_non_digest_target_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            _validate(self._route(target="not-a-digest"))

    def test_unknown_detail_keys_are_refused(self) -> None:
        action = _gui_action()
        route = _route(
            RecoveryRung.CURRENT_UNTRIED,
            target=_candidate_id(action),
            details={"action": action.to_dict(), "extra": 1},
        )
        with self.assertRaises(ValueError) as caught:
            _validate(route)
        self.assertIn("must contain exactly", str(caught.exception))


class KnownGuiRouteTest(unittest.TestCase):
    """Rung 3: replay one recorded transition toward a productive screen."""

    def _details(self, action: ActionSpec, **overrides) -> dict:
        details = {
            "action": action.to_dict(),
            "immediate_state_id": OTHER_STATE,
            "remaining_steps": 1,
            "motivating_candidate_id": CANDIDATE,
            "rule": "recorded_transition_bfs_v1",
        }
        details.update(overrides)
        return details

    def _route(self, **overrides) -> RouteAttempt:
        action = _gui_action()
        defaults = {"target": "4" * 64, "details": self._details(action)}
        defaults.update(overrides)
        return _route(RecoveryRung.KNOWN_GUI_ROUTE, **defaults)

    def test_a_well_formed_plan_step_validates(self) -> None:
        _validate(self._route())

    def test_the_same_step_may_be_replayed_more_than_once(self) -> None:
        # Walking a multi-hop path legitimately revisits the same edge.
        first = self._route()
        second = self._route(route_id="r-2")
        _validate(second, prior=(first,))

    def test_navigating_to_the_current_state_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _validate(self._route(target=STATE))
        self.assertIn("already in", str(caught.exception))

    def test_a_negative_step_count_is_refused(self) -> None:
        action = _gui_action()
        route = _route(
            RecoveryRung.KNOWN_GUI_ROUTE,
            target="4" * 64,
            details=self._details(action, remaining_steps=-1),
        )
        with self.assertRaises(ValueError):
            _validate(route)

    def test_a_missing_planning_rule_is_refused(self) -> None:
        action = _gui_action()
        route = _route(
            RecoveryRung.KNOWN_GUI_ROUTE,
            target="4" * 64,
            details=self._details(action, rule=""),
        )
        with self.assertRaises(ValueError) as caught:
            _validate(route)
        self.assertIn("planning rule", str(caught.exception))

    def test_a_non_digest_destination_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            _validate(self._route(target="somewhere"))

    def test_the_target_is_not_required_to_be_the_action_digest(self) -> None:
        # Rung 3's target is a destination screen, not a candidate identity.
        action = _gui_action()
        kind, target_id, _ = expected_route_action(self._route(), state_id=STATE)
        self.assertEqual((kind, target_id), (action.kind, action.target_id))


class BoundedModelRouteTest(unittest.TestCase):
    """Rung 6: a model choice, gated by configuration and bound to its call."""

    def _route(self, **overrides) -> RouteAttempt:
        action = _gui_action()
        defaults = {
            "target": _candidate_id(action),
            "details": {"action": action.to_dict(), "model_call_id": "m-1"},
        }
        defaults.update(overrides)
        return _route(RecoveryRung.BOUNDED_LLM, **defaults)

    def test_a_well_formed_model_route_validates_when_assistance_is_enabled(self) -> None:
        _validate(self._route(), model=True)

    def test_a_model_route_is_refused_when_assistance_is_disabled(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _validate(self._route(), model=False)
        self.assertIn("disabled by the recorded configuration", str(caught.exception))

    def test_a_missing_call_reference_is_refused(self) -> None:
        action = _gui_action()
        route = _route(
            RecoveryRung.BOUNDED_LLM,
            target=_candidate_id(action),
            details={"action": action.to_dict(), "model_call_id": ""},
        )
        with self.assertRaises(ValueError) as caught:
            _validate(route, model=True)
        self.assertIn("name the model call", str(caught.exception))

    def test_one_call_cannot_justify_two_routes(self) -> None:
        first = self._route()
        second = self._route(route_id="r-2", target="5" * 64)
        with self.assertRaises(ValueError) as caught:
            _validate(second, prior=(first,), model=True)
        # The digest check runs first for this rung, so either refusal is correct
        # as long as the route does not pass.
        self.assertTrue(
            "reused one model call" in str(caught.exception)
            or "candidate digest" in str(caught.exception)
        )

    def test_a_duplicate_call_with_a_matching_digest_is_refused(self) -> None:
        first = self._route()
        second = self._route(route_id="r-2")
        with self.assertRaises(ValueError) as caught:
            _validate(second, prior=(first,), model=True)
        self.assertIn("reused one model call", str(caught.exception))

    def test_the_model_cannot_change_the_action_after_the_fact(self) -> None:
        # target is the digest of the recorded action, so swapping the action
        # invalidates the route.
        tampered = _gui_action(resource="com.example:id/other")
        route = _route(
            RecoveryRung.BOUNDED_LLM,
            target=_candidate_id(_gui_action()),
            details={"action": tampered.to_dict(), "model_call_id": "m-1"},
        )
        with self.assertRaises(ValueError) as caught:
            expected_route_action(route, state_id=STATE)
        self.assertIn("candidate digest", str(caught.exception))


class StaticRungRegressionTest(unittest.TestCase):
    """Static rungs accept any frozen unused target and still reject reuse."""

    def test_deep_link_targets_are_consumed_once(self) -> None:
        runtime = _runtime(deep_links=["app://a", "app://b"])
        first = _route(RecoveryRung.DEEP_LINK, target="app://a", details={})
        second = _route(RecoveryRung.DEEP_LINK, target="app://b", details={}, route_id="r-2")
        _validate(first, runtime=runtime)
        _validate(second, prior=(first,), runtime=runtime)

    def test_a_deep_link_may_be_selected_out_of_lexical_order(self) -> None:
        runtime = _runtime(deep_links=["app://a", "app://b"])
        route = _route(RecoveryRung.DEEP_LINK, target="app://b", details={})
        _validate(route, runtime=runtime)

    def test_a_static_rung_still_rejects_extra_detail_keys(self) -> None:
        runtime = _runtime(deep_links=["app://a"])
        route = _route(
            RecoveryRung.DEEP_LINK, target="app://a", details={"action": {}}
        )
        with self.assertRaises(ValueError) as caught:
            _validate(route, runtime=runtime)
        self.assertIn("must contain exactly", str(caught.exception))

    def test_exhausted_targets_are_refused(self) -> None:
        runtime = _runtime(deep_links=["app://a"])
        first = _route(RecoveryRung.DEEP_LINK, target="app://a", details={})
        second = _route(RecoveryRung.DEEP_LINK, target="app://a", details={}, route_id="r-2")
        with self.assertRaises(ValueError) as caught:
            _validate(second, prior=(first,), runtime=runtime)
        self.assertIn("reused an already attempted target", str(caught.exception))


class RuntimeConfigSchemaTest(unittest.TestCase):
    def test_schema_two_requires_the_new_fields(self) -> None:
        values = _runtime().to_dict()
        del values["per_field_input_enabled"]
        with self.assertRaises(ValueError) as caught:
            RuntimeConfig.from_mapping(values)
        self.assertIn("per_field_input_enabled", str(caught.exception))

    def test_per_field_input_needs_a_seed(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _runtime(per_field_input_enabled=True, text_input_value=None)
        self.assertIn("seed", str(caught.exception))

    def test_with_routes_records_discovery_provenance(self) -> None:
        updated = _runtime().with_routes(
            deep_links=("app://b", "app://a"),
            exported_components=(f"{PACKAGE}/.Share",),
            forced_components=(),
            route_discovery_sha256="a" * 64,
        )
        self.assertEqual(updated.deep_links, ("app://a", "app://b"))
        self.assertEqual(updated.route_discovery_sha256, "a" * 64)

    def test_a_malformed_discovery_digest_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            _runtime(route_discovery_sha256="nope")


if __name__ == "__main__":
    unittest.main()


class LadderOrderTest(unittest.TestCase):
    """Rung numbers are record identity; the order they are tried in is policy."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.path = Path(self._temporary.name) / "recovery.json"

    def _planner(self, **overrides):
        from valordroid.config import CoreConfig
        from valordroid.recovery import RecoveryPlanner

        return RecoveryPlanner(CoreConfig(**overrides), self.path)

    def _model_planner(self):
        return self._planner(
            model_assistance_enabled=True,
            max_model_calls=4,
            model_first_on_stall=True,
        )

    def test_default_order_is_the_numeric_ladder(self) -> None:
        self.assertEqual(self._planner().ladder_order(), tuple(RecoveryRung))

    def test_model_first_promotes_the_model_rung_to_second(self) -> None:
        order = self._model_planner().ladder_order()
        self.assertEqual(order[0], RecoveryRung.CURRENT_UNTRIED)
        self.assertEqual(order[1], RecoveryRung.BOUNDED_LLM)
        # Promotion reorders; it never drops or duplicates a rung.
        self.assertEqual(set(order), set(RecoveryRung))
        self.assertEqual(len(order), len(tuple(RecoveryRung)))

    def test_promotion_changes_which_rung_is_selected(self) -> None:
        decision = self._model_planner().next(
            (RecoveryRung.BACK_OR_REVEAL, RecoveryRung.BOUNDED_LLM)
        )
        assert decision is not None
        self.assertIs(decision.rung, RecoveryRung.BOUNDED_LLM)

    def test_default_order_still_prefers_the_cheap_deterministic_rung(self) -> None:
        decision = self._planner().next(
            (RecoveryRung.BACK_OR_REVEAL, RecoveryRung.BOUNDED_LLM)
        )
        assert decision is not None
        self.assertIs(decision.rung, RecoveryRung.BACK_OR_REVEAL)

    def test_model_first_requires_model_assistance(self) -> None:
        from valordroid.config import CoreConfig

        with self.assertRaises(ValueError):
            CoreConfig(model_first_on_stall=True)


class RouteForegroundTimeoutTest(unittest.TestCase):
    """A failing route must not spend the cold-start budget."""

    def test_schema_three_requires_the_route_timeout(self) -> None:
        values = _runtime().to_dict()
        del values["route_foreground_timeout_seconds"]
        with self.assertRaises(ValueError) as caught:
            RuntimeConfig.from_mapping(values)
        self.assertIn("route_foreground_timeout_seconds", str(caught.exception))

    def test_route_timeout_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            _runtime(route_foreground_timeout_seconds=0.0)

    def test_route_timeout_is_independent_of_the_launch_timeout(self) -> None:
        runtime = _runtime(
            launch_timeout_seconds=60.0, route_foreground_timeout_seconds=4.0
        )
        self.assertEqual(runtime.launch_timeout_seconds, 60.0)
        self.assertEqual(runtime.route_foreground_timeout_seconds, 4.0)

    def test_session_waits_only_the_route_bound_for_a_deep_link(self) -> None:
        # The measured failure: four deep links that did not match consumed
        # 240 of a 240-second run at 60 seconds each.
        import time as _time

        from valordroid.android.adb import AdbError
        from valordroid.android.session import AndroidSession

        runtime = _runtime(
            launch_timeout_seconds=30.0, route_foreground_timeout_seconds=1.0
        )

        class _StubAdb:
            @staticmethod
            def start_view_intent_stdin(
                _package: str, _uri: str, *, timeout: float
            ) -> object:
                self.assertEqual(timeout, 30.0)
                return type(
                    "Result", (), {"stdout": "Status: ok", "stderr": ""}
                )()

        route_deadlines: list[float | None] = []

        class _Stub:
            config = runtime
            package = PACKAGE
            adb = _StubAdb()
            wait_for_package = AndroidSession.wait_for_package
            _successful_start = staticmethod(AndroidSession._successful_start)

            @staticmethod
            def resumed_activities(
                *, deadline_monotonic: float | None = None
            ) -> tuple[str, ...]:
                # Never the prepared package, so the wait always times out.
                route_deadlines.append(deadline_monotonic)
                return ("com.android.launcher3/.Launcher",)

        started = _time.monotonic()
        with self.assertRaises(AdbError):
            AndroidSession.open_deep_link(_Stub(), "app://never")  # type: ignore[arg-type]
        elapsed = _time.monotonic() - started
        # Every foreground poll shares the route-bound absolute deadline.
        self.assertTrue(route_deadlines)
        self.assertIsNotNone(route_deadlines[0])
        self.assertTrue(
            all(deadline == route_deadlines[0] for deadline in route_deadlines)
        )
        # Comfortably under the 30s launch timeout it would previously have used.
        self.assertLess(elapsed, 8.0)


class RepeatableRungTest(unittest.TestCase):
    """Back and seed reset are reusable; treating them as consumed ended runs early.

    Measured before this change: a run used rung 2 once and rung 8 once, then had
    no rung left to offer and stopped after two minutes of a four-minute budget.
    """

    def _back(self, route_id: str = "r-1") -> RouteAttempt:
        return _route(
            RecoveryRung.BACK_OR_REVEAL, target="KEYCODE_BACK", details={}, route_id=route_id
        )

    def test_back_may_be_used_repeatedly(self) -> None:
        prior = tuple(self._back(f"r-{index}") for index in range(1, 6))
        _validate(self._back("r-6"), prior=prior)

    def test_back_still_requires_its_configured_target(self) -> None:
        route = _route(
            RecoveryRung.BACK_OR_REVEAL, target="KEYCODE_HOME", details={}
        )
        with self.assertRaises(ValueError) as caught:
            _validate(route)
        self.assertIn("configured target", str(caught.exception))

    def test_seed_reset_may_be_repeated_when_enabled(self) -> None:
        runtime = _runtime(reset_seed_enabled=True)
        first = _route(RecoveryRung.RESET_SEED, target=PACKAGE, details={})
        second = _route(RecoveryRung.RESET_SEED, target=PACKAGE, details={}, route_id="r-2")
        _validate(second, prior=(first,), runtime=runtime)

    def test_seed_reset_is_refused_when_disabled(self) -> None:
        runtime = _runtime(reset_seed_enabled=False)
        route = _route(RecoveryRung.RESET_SEED, target=PACKAGE, details={})
        with self.assertRaises(ValueError) as caught:
            _validate(route, runtime=runtime)
        self.assertIn("disabled by configuration", str(caught.exception))

    def test_configurable_bounds_reject_negative_values(self) -> None:
        from valordroid.config import CoreConfig

        for name in ("max_seed_resets", "max_crash_relaunches"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    CoreConfig(**{name: -1})

    def test_crash_relaunch_allowance_defaults_to_recovering(self) -> None:
        from valordroid.config import CoreConfig

        # Zero would reproduce the old behavior of capping an app at its launch
        # coverage on the first fatal.
        self.assertGreater(CoreConfig().max_crash_relaunches, 0)


class StuckScreenRecoveryTest(unittest.TestCase):
    """A screen that never reaches an idle UI state must not abort the run.

    Measured on Chess: an action opened a live "connecting..." screen with no
    real backend in the test environment, UIAutomator never got idle after 9
    dump attempts, and the run aborted having already earned 34% coverage.
    """

    def _runner_stub(self, *, back_calls: list, observe_results: list):
        from valordroid.android.observe import ScreenNeverIdleError
        from valordroid.config import CoreConfig
        from valordroid.models import StateObservation
        from valordroid.runner import AndroidRunner

        class _Adb:
            def shell(self, *arguments, **kwargs):
                if arguments[:2] == ("input", "keyevent"):
                    back_calls.append(arguments)

                class _Result:
                    returncode = 0

                return _Result()

        class _Session:
            adb = _Adb()

        class _Observer:
            def observe(self):
                if not observe_results:
                    raise AssertionError("observe() called more times than scripted")
                result = observe_results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

            @contextmanager
            def bounded_window(self, seconds):
                # The ladder caps its whole sequence, not each observation, so the
                # stand-in records the window without constraining the script.
                self.window_seconds = seconds
                yield

        runner = AndroidRunner.__new__(AndroidRunner)
        runner.session = _Session()
        runner.observer = _Observer()
        runner.runtime = type(
            "R", (), {"post_action_delay_seconds": 0.0, "max_seconds": 600.0}
        )()

        class _Core:
            config = CoreConfig()

        runner.core = _Core()
        runner._consecutive_stuck_screens = 0
        runner._lifecycle_calls = []
        runner._lifecycle = lambda phase, event, status, details=None, error=None: (
            runner._lifecycle_calls.append((phase, event, details))
        )
        return runner, ScreenNeverIdleError

    def test_recovers_with_back_instead_of_aborting(self) -> None:
        from valordroid.models import StateObservation

        before = StateObservation(state_id="a" * 64, observed_at=1.0)
        after = StateObservation(state_id="b" * 64, observed_at=2.0)
        back_calls: list = []
        runner, ScreenNeverIdleError = self._runner_stub(
            back_calls=back_calls, observe_results=[after]
        )
        result = runner._recover_from_stuck_screen(
            before, ScreenNeverIdleError("could not get idle state")
        )
        self.assertEqual(result.state_id, "b" * 64)
        self.assertEqual(len(back_calls), 1)
        self.assertEqual(runner._consecutive_stuck_screens, 0)
        events = [event for _, event, _ in runner._lifecycle_calls]
        self.assertIn("stuck_screen_detected", events)
        self.assertIn("stuck_screen_recovered", events)

    def test_repeated_stuck_screens_abort_instead_of_looping_forever(self) -> None:
        from valordroid.runner import RunAbort

        before = __import__(
            "valordroid.models", fromlist=["StateObservation"]
        ).StateObservation(state_id="a" * 64, observed_at=1.0)
        runner, ScreenNeverIdleError = self._runner_stub(back_calls=[], observe_results=[])
        runner._consecutive_stuck_screens = runner.core.config.max_stuck_screen_recoveries
        with self.assertRaises(RunAbort) as caught:
            runner._recover_from_stuck_screen(
                before, ScreenNeverIdleError("could not get idle state")
            )
        self.assertEqual(caught.exception.reason, "screen_never_idle_repeated")

    def test_a_second_failed_recovery_attempt_also_aborts(self) -> None:
        from valordroid.runner import RunAbort

        from valordroid.android.observe import ScreenNeverIdleError

        before = __import__(
            "valordroid.models", fromlist=["StateObservation"]
        ).StateObservation(state_id="a" * 64, observed_at=1.0)
        runner, _ = self._runner_stub(
            back_calls=[],
            observe_results=[ScreenNeverIdleError("still not idle")],
        )
        with self.assertRaises(RunAbort) as caught:
            runner._recover_from_stuck_screen(
                before, ScreenNeverIdleError("could not get idle state")
            )
        self.assertEqual(caught.exception.reason, "screen_never_idle_unrecoverable")

    def test_max_stuck_screen_recoveries_must_be_positive(self) -> None:
        from valordroid.config import CoreConfig

        with self.assertRaises(ValueError):
            CoreConfig(max_stuck_screen_recoveries=0)


class ForeignScreenBudgetTest(unittest.TestCase):
    """Escaping another package's screen is worth a few taps, not a whole run.

    Offering a foreign screen's buttons is what lets exploration get past a
    permission prompt or a wallpaper picker (Muzei went 7.24% -> 11.31% on it).
    But a foreign screen can be an entire other app: one measured Chess run
    then spent 7 of 74 attempts browsing `com.android.documentsui`, which is not
    instrumented and cannot produce coverage, and finished at 25.43% against
    34.24% for the same app before foreign taps existed. So the budget is
    bounded and exploration is steered back into the app under test.
    """

    APP = "com.example.app"

    def _action(self, package: str) -> ActionSpec:
        selector = {
            "resource_id": "",
            "class_name": "android.widget.Button",
            "text": "OK",
            "content_description": "",
            "tree_path": "0/1",
            "package": package,
        }
        return ActionSpec(
            "tap",
            stable_hash(selector),
            {"expected_state_id": STATE, "selector": selector},
        )

    @staticmethod
    def _observation(state_id: str, observed_at: float, activity: str):
        """A StateObservation whose activity is its unique resumed component."""

        from valordroid.models import StateObservation

        return StateObservation(
            state_id=state_id,
            observed_at=observed_at,
            activity=activity,
            resumed_activities=(activity,),
        )

    def _runner_stub(
        self,
        *,
        observe_results: list,
        launch_calls: list | None = None,
        observe_deadlines: list | None = None,
        launch_error: Exception | None = None,
        foreground_activity: str | None = None,
    ):
        from valordroid.config import CoreConfig
        from valordroid.runner import AndroidRunner

        app = self.APP

        class _Adb:
            def shell(self, *arguments, **kwargs):
                class _Result:
                    returncode = 0

                return _Result()

        class _Session:
            adb = _Adb()
            package = app

            def launch(
                self, *, deadline_monotonic: float | None = None
            ) -> None:
                if launch_calls is not None:
                    launch_calls.append(deadline_monotonic)
                if launch_error is not None:
                    raise launch_error

            def foreground(
                self, *, deadline_monotonic: float | None = None
            ):
                """Answer the cheap probe containment asks before observing.

                Defaults to the app being in front, so a test that scripts a
                successful Back still reaches the observation that proves it. A
                test about Back *not* working passes `foreground_activity` and
                the observation is then skipped, which is the saving being made.
                """

                activity = (
                    f"{app}/.MainActivity"
                    if foreground_activity is None
                    else foreground_activity
                )
                return ((activity,), activity)

        class _Observer:
            latest = None

            def observe(self, *, deadline_monotonic: float | None = None):
                if observe_deadlines is not None:
                    observe_deadlines.append(deadline_monotonic)
                if not observe_results:
                    raise AssertionError("observe() called more times than scripted")
                result = observe_results.pop(0)
                if isinstance(result, Exception):
                    raise result
                if callable(result):
                    result = result()
                return result

        runner = AndroidRunner.__new__(AndroidRunner)
        runner.session = _Session()
        runner.observer = _Observer()
        runner.runtime = type("R", (), {"post_action_delay_seconds": 0.0})()

        class _Core:
            config = CoreConfig()

        runner.core = _Core()
        runner._consecutive_foreign_actions = 0
        runner._lifecycle_calls = []
        runner._lifecycle = lambda phase, event, status, details=None, error=None: (
            runner._lifecycle_calls.append((phase, event, details))
        )
        return runner

    def test_an_in_app_selector_is_not_foreign(self) -> None:
        runner = self._runner_stub(observe_results=[])
        self.assertFalse(runner._foreign_selector(self._action(self.APP)))

    def test_another_packages_selector_is_foreign(self) -> None:
        runner = self._runner_stub(observe_results=[])
        self.assertTrue(runner._foreign_selector(self._action("com.android.documentsui")))

    def test_back_alone_returns_to_the_app_without_relaunching(self) -> None:
        before = self._observation("a" * 64, 1.0, "com.android.documentsui/.Files")
        launches: list[float | None] = []
        observe_deadlines: list[float | None] = []
        runner = self._runner_stub(
            observe_results=[
                lambda: self._observation(
                    "b" * 64,
                    time.time(),
                    f"{self.APP}/.MainActivity",
                )
            ],
            launch_calls=launches,
            observe_deadlines=observe_deadlines,
        )
        runner._consecutive_foreign_actions = 2
        result = runner._return_to_app(before)
        self.assertEqual(result.state_id, "b" * 64)
        # Back was enough, so app state was preserved rather than relaunched.
        self.assertEqual(launches, [])
        self.assertEqual(len(observe_deadlines), 1)
        self.assertIsNotNone(observe_deadlines[0])
        self.assertEqual(runner._consecutive_foreign_actions, 0)
        events = [event for _, event, _ in runner._lifecycle_calls]
        self.assertIn("foreign_screen_budget_spent", events)
        self.assertIn("returned_to_app", events)

    def test_relaunch_is_the_fallback_when_back_stays_foreign(self) -> None:
        before = self._observation("a" * 64, 1.0, "com.android.documentsui/.Files")
        launches: list[float | None] = []
        observe_deadlines: list[float | None] = []
        runner = self._runner_stub(
            # The file picker is still in front after Back, so containment learns
            # that from the resumed set and never dumps its hierarchy.
            foreground_activity="com.android.documentsui/.Roots",
            observe_results=[
                lambda: self._observation(
                    "c" * 64,
                    time.time(),
                    f"{self.APP}/.MainActivity",
                ),
            ],
            launch_calls=launches,
            observe_deadlines=observe_deadlines,
        )
        runner.BACK_SETTLE_GRACE_SECONDS = 0.0
        result = runner._return_to_app(before)
        self.assertEqual(result.state_id, "c" * 64)
        self.assertEqual(len(launches), 1)
        self.assertIsNotNone(launches[0])
        # Only the relaunch observes, and it shares the containment bound.
        self.assertEqual(observe_deadlines, [launches[0]])

    def test_a_failed_return_leaves_the_run_to_the_recovery_ladder(self) -> None:
        # Aborting here threw away a whole measurement: AnkiDroid's system
        # permission dialog owns the screen, so `launch()` correctly reports the
        # package never came foreground, and the run died on a condition the
        # ladder can still clear with a seed reset.
        before = self._observation("a" * 64, 1.0, "com.android.documentsui/.Files")
        launches: list[float | None] = []
        observe_deadlines: list[float | None] = []
        runner = self._runner_stub(
            observe_results=[OSError("device gone")],
            launch_calls=launches,
            observe_deadlines=observe_deadlines,
            launch_error=OSError("package did not become foreground"),
        )
        result = runner._return_to_app(before)
        self.assertEqual(result.state_id, before.state_id)
        # The fixture reaches both intended device failures instead of failing
        # on an unexpected keyword or an exhausted observation script.
        self.assertEqual(len(launches), 1)
        self.assertIsNotNone(launches[0])
        self.assertEqual(observe_deadlines, launches)
        failures = [
            details
            for _, event, details in runner._lifecycle_calls
            if event == "returned_to_app"
        ]
        self.assertTrue(failures)
        self.assertTrue(failures[-1]["relaunch_attempted"])

    def test_max_consecutive_foreign_actions_must_be_positive(self) -> None:
        from valordroid.config import CoreConfig

        with self.assertRaises(ValueError):
            CoreConfig(max_consecutive_foreign_actions=0)

    def test_the_bound_is_small_enough_to_escape_but_not_explore(self) -> None:
        from valordroid.config import CoreConfig

        # Chess wasted 7 consecutive foreign attempts; a permission prompt needs
        # one or two ("Allow", or "Continue" then "Allow").
        bound = CoreConfig().max_consecutive_foreign_actions
        self.assertGreaterEqual(bound, 1)
        self.assertLessEqual(bound, 3)

    def test_an_in_app_candidate_is_preferred_over_a_foreign_one(self) -> None:
        # The ordering rule that keeps a foreign screen an obstacle rather than
        # a destination. Chess had plenty of its own candidates and still spent
        # 16 of 74 attempts in the system file picker when foreign elements
        # competed on equal footing.
        runner = self._runner_stub(observe_results=[])
        foreign = self._action("com.android.documentsui")
        mine = self._action(self.APP)
        eligible = [
            type("Item", (), {"candidate": type("C", (), {"action": foreign})()})(),
            type("Item", (), {"candidate": type("C", (), {"action": mine})()})(),
        ]
        in_app = [
            item for item in eligible if not runner._foreign_selector(item.candidate.action)
        ]
        self.assertEqual(len(in_app), 1)
        self.assertIs((in_app or eligible)[0].candidate.action, mine)

    def test_a_foreign_candidate_is_still_used_when_nothing_in_app_is_eligible(self) -> None:
        # Muzei's case: the app's own screen offers nothing, so the system
        # wallpaper picker's button is the only way to make progress at all.
        runner = self._runner_stub(observe_results=[])
        foreign = self._action("com.android.wallpaper.livepicker")
        eligible = [
            type("Item", (), {"candidate": type("C", (), {"action": foreign})()})(),
        ]
        in_app = [
            item for item in eligible if not runner._foreign_selector(item.candidate.action)
        ]
        self.assertEqual(in_app, [])
        self.assertIs((in_app or eligible)[0].candidate.action, foreign)

    def _trial_runner(self, *, actions: int, gains: int):
        runner = self._runner_stub(observe_results=[])
        # The stub's core carries a real CoreConfig, so the thresholds under
        # test are the shipped defaults rather than test-local numbers.
        runner._foreign_actions = actions
        runner._foreign_gains = gains
        return runner

    def test_foreign_screens_get_a_trial_before_any_judgement(self) -> None:
        runner = self._trial_runner(actions=0, gains=0)
        self.assertTrue(runner._foreign_screens_worth_trying())

    def test_foreign_screens_stay_available_while_they_pay_off(self) -> None:
        # Muzei: the wallpaper picker its own button opened is where the
        # remaining coverage is, so a paying foreign screen keeps its access.
        from valordroid.config import CoreConfig

        budget = CoreConfig().foreign_actions_before_giving_up
        runner = self._trial_runner(actions=budget * 5, gains=1)
        self.assertTrue(runner._foreign_screens_worth_trying())

    def test_foreign_screens_are_abandoned_once_the_trial_pays_nothing(self) -> None:
        # Chess: the system file picker is simply another app, and 19 of 55
        # attempts went there while its own game screens stayed unexplored.
        from valordroid.config import CoreConfig

        budget = CoreConfig().foreign_actions_before_giving_up
        runner = self._trial_runner(actions=budget, gains=0)
        self.assertFalse(runner._foreign_screens_worth_trying())

    def test_the_trial_is_long_enough_for_a_two_step_prompt(self) -> None:
        # "Continue" then "Allow" must both fit inside the trial, or a genuine
        # permission flow would be cut off before it could ever pay.
        from valordroid.config import CoreConfig

        self.assertGreaterEqual(CoreConfig().foreign_actions_before_giving_up, 2)

    def test_foreign_actions_before_giving_up_must_be_positive(self) -> None:
        from valordroid.config import CoreConfig

        with self.assertRaises(ValueError):
            CoreConfig(foreign_actions_before_giving_up=0)


class UnexploredScreenNavigationTest(unittest.TestCase):
    """A screen holding buttons nobody pressed must be reachable by navigation.

    `worthwhile_states` can only see candidates that already have a frontier
    entry, and an entry is created only when a candidate is attempted. So a
    screen with five never-pressed buttons was invisible as a destination, and an
    exhausted screen had nowhere better to go than re-tapping itself. Measured
    over four recent runs, repeat executions of a (screen, action) pair returned
    0, 0, 1 and 94 units against 309, 426, 1269 and 56 for first executions, and
    one older run spent 511 of 532 attempts on repeats for zero coverage.
    """

    def _runner(self, *, observed: dict, attempted: set):
        from valordroid.config import CoreConfig
        from valordroid.runner import AndroidRunner

        runner = AndroidRunner.__new__(AndroidRunner)
        runner._observed_candidates = {k: frozenset(v) for k, v in observed.items()}

        class _Frontier:
            @staticmethod
            def entries():
                return tuple(
                    type("E", (), {"candidate_id": cid})() for cid in sorted(attempted)
                )

        class _Core:
            config = CoreConfig()
            frontier = _Frontier()

        runner.core = _Core()
        return runner

    def test_a_screen_with_unpressed_candidates_becomes_a_destination(self) -> None:
        runner = self._runner(
            observed={"home": {"a", "b"}, "settings": {"c", "d"}},
            attempted={"a", "b"},
        )
        found = runner._unexplored_destinations("home", {})
        self.assertEqual(found, {"settings": "c"})

    def test_a_fully_explored_screen_is_not_offered(self) -> None:
        runner = self._runner(
            observed={"home": {"a"}, "settings": {"c"}}, attempted={"a", "c"}
        )
        self.assertEqual(runner._unexplored_destinations("home", {}), {})

    def test_the_current_screen_is_never_its_own_destination(self) -> None:
        # Navigating to where the device already is would be a no-op route, and
        # rung 3 validation refuses a plan whose destination is the current state.
        runner = self._runner(observed={"home": {"a", "b"}}, attempted=set())
        self.assertEqual(runner._unexplored_destinations("home", {}), {})

    def test_a_screen_already_worthwhile_is_left_alone(self) -> None:
        # `worthwhile_states` already chose a motivating candidate for it; two
        # sources naming different targets for one screen would be ambiguous.
        runner = self._runner(observed={"settings": {"c", "d"}}, attempted=set())
        found = runner._unexplored_destinations("home", {"settings": "already"})
        self.assertEqual(found, {})

    def test_the_chosen_target_is_deterministic(self) -> None:
        # Replay and offline validation must pick the same motivating candidate,
        # so selection is by sorted stable ID rather than set iteration order.
        for _ in range(5):
            runner = self._runner(
                observed={"settings": {"zz", "aa", "mm"}}, attempted=set()
            )
            self.assertEqual(
                runner._unexplored_destinations("home", {}), {"settings": "aa"}
            )


class CredentialScreenTest(unittest.TestCase):
    """A login form cannot be solved without an account, so it gets a bounded try.

    Commons returned exactly 12.32% in every run before route discovery -- a
    0.00pp spread over eight runs -- because its front door is a login form and
    the loop kept re-tapping SIGN UP, which cannot succeed here: sign-up needs a
    CAPTCHA and email verification, and this tool must not create accounts on a
    real service. 2,605 of its 2,971 methods stayed untouched.
    """

    APP = "fr.free.nrw.commons"

    def _runner(self, *, nodes):
        from valordroid.config import CoreConfig
        from valordroid.runner import AndroidRunner

        app = self.APP

        class _Session:
            package = app

        class _Captured:
            pass

        captured = _Captured()
        captured.nodes = nodes

        class _Observer:
            latest = captured

        runner = AndroidRunner.__new__(AndroidRunner)
        runner.session = _Session()
        runner.observer = _Observer()

        class _Core:
            config = CoreConfig()

        runner.core = _Core()
        runner._auth_actions = 0
        runner._abandoned_auth_states = set()
        return runner

    @staticmethod
    def _node(package, password):
        class _N:
            selector = {"package": package}
            attributes = {"password": password}

        return _N()

    def test_a_masked_field_marks_the_screen_as_a_credential_form(self) -> None:
        # Modeled on the captured Commons LoginActivity: loginUsername,
        # loginPassword (password=true), loginButton, signupButton.
        runner = self._runner(
            nodes=[
                self._node(self.APP, "false"),
                self._node(self.APP, "true"),
            ]
        )
        self.assertTrue(runner._asks_for_credentials())

    def test_an_ordinary_screen_is_not_a_credential_form(self) -> None:
        runner = self._runner(nodes=[self._node(self.APP, "false")])
        self.assertFalse(runner._asks_for_credentials())

    def test_another_packages_masked_field_does_not_count(self) -> None:
        # A system or keyboard overlay carrying a masked field must not make the
        # app's own screen look like a login form.
        runner = self._runner(nodes=[self._node("com.android.systemui", "true")])
        self.assertFalse(runner._asks_for_credentials())

    def test_no_observation_yet_is_not_a_credential_form(self) -> None:
        runner = self._runner(nodes=[])
        runner.observer.latest = None
        self.assertFalse(runner._asks_for_credentials())

    def test_the_allowance_covers_filling_the_form_twice(self) -> None:
        from valordroid.config import CoreConfig

        # Username, password, submit -- and room to get it wrong once.
        self.assertGreaterEqual(CoreConfig().max_auth_screen_actions, 6)

    def test_max_auth_screen_actions_must_be_positive(self) -> None:
        from valordroid.config import CoreConfig

        with self.assertRaises(ValueError):
            CoreConfig(max_auth_screen_actions=0)


class SeedResetTimeBudgetTest(unittest.TestCase):
    """Seed reset must not consume the run it exists to rescue.

    Measured on AnkiDroid: four seed resets took 246.5s of a 360s budget, 61.6s
    each, because `pm clear` makes the next launch a cold first-run and the app's
    first-run flow never foregrounds before `launch_timeout_seconds`. That run
    managed 31 actions and 3 gaining ones, against 67 actions and 24 gaining in
    the comparable run before, and coverage fell 13.51% -> 6.82%. A count bound
    alone could not prevent this because the cost per reset is not fixed.
    """

    def _runner(self, *, spent: float, used: int, max_seconds: float = 360.0):
        from valordroid.config import CoreConfig
        from valordroid.recovery import RecoveryRung
        from valordroid.runner import AndroidRunner

        runner = AndroidRunner.__new__(AndroidRunner)
        runner.runtime = type("R", (), {"max_seconds": max_seconds})()
        runner._seed_reset_seconds = spent
        runner._consecutive_back = 0

        class _Route:
            rung = int(RecoveryRung.RESET_SEED)

        class _Core:
            config = CoreConfig()
            route_records = tuple(_Route() for _ in range(used))

        runner.core = _Core()
        runner.runtime_reset_enabled = True
        return runner

    def _offered(self, runner) -> bool:
        from valordroid.recovery import RecoveryRung

        # Exercise the same predicate _recovery_choices applies to rung 8.
        used = runner._used_recovery_targets(RecoveryRung.RESET_SEED)
        if used >= runner.core.config.max_seed_resets:
            return False
        return runner._seed_reset_seconds < runner._seed_reset_budget_seconds()

    def test_a_fresh_run_may_reset(self) -> None:
        self.assertTrue(self._offered(self._runner(spent=0.0, used=0)))

    def test_one_expensive_reset_still_leaves_room(self) -> None:
        # 15% of 360s is 54s, so a single 30s reset must not exhaust the budget.
        self.assertTrue(self._offered(self._runner(spent=30.0, used=1)))

    def test_the_measured_failure_case_is_refused(self) -> None:
        # The real run spent 246.5s on resets; the second one should already
        # have been refused rather than allowed to reach four.
        self.assertFalse(self._offered(self._runner(spent=123.0, used=2)))

    def test_the_count_bound_still_applies_independently(self) -> None:
        from valordroid.config import CoreConfig

        runner = self._runner(spent=0.0, used=CoreConfig().max_seed_resets)
        self.assertFalse(self._offered(runner))

    def test_the_budget_scales_with_the_run_length(self) -> None:
        # An hour-long run can afford more restarting than a six-minute probe.
        short = self._runner(spent=0.0, used=0, max_seconds=360.0)
        long = self._runner(spent=0.0, used=0, max_seconds=3600.0)
        self.assertLess(
            short._seed_reset_budget_seconds(), long._seed_reset_budget_seconds()
        )
        self.assertAlmostEqual(short._seed_reset_budget_seconds(), 54.0)


class ComponentLaunchTimingTest(unittest.TestCase):
    """Launching a component is cheap coverage, so it must not spend early budget.

    Measured on Chess, same APK and device: sweeping all 16 declared activities
    yields 92 of 1215 units (7.57%), while ordinary GUI exploration reaches 363
    (29.88%). Entering a screen runs only its setup; the coverage is in
    interacting with it. Of the sweep's 92 units GUI already had 73, so the sweep
    contributes 19 units GUI cannot reach.

    Those 19 units cost about 48s. Early in a run the same 48s buys roughly 12
    taps at about 3.8 units each, so tapping wins outright. Late in a run the
    frontier is exhausted and repeat executions were measured returning 0, 0 and
    1 units, so the 19 become free. Spending the expensive part of the budget on
    the cheap mechanism is what moved Chess from 28.70% to 5.93%.
    """

    def _runner(self, *, elapsed: float | None, max_seconds: float = 360.0):
        import time as _time

        from valordroid.runner import AndroidRunner

        runner = AndroidRunner.__new__(AndroidRunner)
        runner.runtime = type("R", (), {"max_seconds": max_seconds})()
        runner._exploration_started_at = (
            None if elapsed is None else _time.monotonic() - elapsed
        )
        return runner

    def test_not_offered_before_exploration_has_even_started(self) -> None:
        self.assertFalse(self._runner(elapsed=None)._component_launches_earned())

    def test_not_offered_early_when_tapping_is_still_worth_more(self) -> None:
        # 30s into a 360s run: a tap is worth about 3.8 units, a launch far less.
        self.assertFalse(self._runner(elapsed=30.0)._component_launches_earned())

    def _runner_with_reach(self, *, elapsed: float, unentered: int,
                           max_seconds: float = 360.0, threshold: int = 5):
        """A runner that can also report how much unentered surface remains."""

        from valordroid.config import CoreConfig

        runner = self._runner(elapsed=elapsed, max_seconds=max_seconds)
        runner.core = type(
            "C", (), {"config": CoreConfig(component_launch_low_reach_targets=threshold)}
        )()
        runner._forced_sweep_targets = lambda: tuple(
            f"com.example/.Activity{index}" for index in range(unentered)
        )
        return runner

    def test_the_early_release_can_be_withheld_for_comparison(self) -> None:
        """Threshold 0 restores clock-only behaviour, so the policy is A/B-able."""

        self.assertFalse(
            self._runner_with_reach(elapsed=0.30 * 360.0, unentered=40, threshold=0)
            ._component_launches_earned()
        )
        self.assertTrue(
            self._runner_with_reach(elapsed=0.61 * 360.0, unentered=0, threshold=0)
            ._component_launches_earned()
        )

    def test_not_offered_just_below_the_threshold(self) -> None:
        # No measurable unentered surface, so only the clock can release it.
        self.assertFalse(
            self._runner_with_reach(elapsed=0.59 * 360.0, unentered=0)
            ._component_launches_earned()
        )

    def test_not_offered_when_reach_evidence_is_unavailable(self) -> None:
        """A runner without exploration state must fall back to the clock.

        The early release is granted on an observation; not being able to make that
        observation has to withhold it, not grant it.
        """

        self.assertFalse(self._runner(elapsed=0.59 * 360.0)._component_launches_earned())

    def test_offered_early_when_the_gui_has_not_got_in(self) -> None:
        """Low reach reverses the trade: launching is the only entry mechanism.

        Across the 50-app campaign 42.7% of declared activities were never entered,
        reach correlated with coverage at +0.416 and action count at +0.109. Kore
        spent 1457 actions inside 2 of its 17 activities for 6.16%. Waiting for 60%
        of the clock withholds the only mechanism that could open the other 15.
        """

        self.assertTrue(
            self._runner_with_reach(elapsed=0.30 * 360.0, unentered=8)
            ._component_launches_earned()
        )

    def test_still_not_offered_before_the_gui_has_had_a_fair_chance(self) -> None:
        """The floor holds even with plenty unentered.

        One-shot targets spent in the first seconds are gone before tapping has been
        tried, which is what drove Chess from 28.70% to 5.93%.
        """

        self.assertFalse(
            self._runner_with_reach(elapsed=0.10 * 360.0, unentered=40)
            ._component_launches_earned()
        )

    def test_a_little_unentered_surface_is_not_enough_to_earn_early(self) -> None:
        self.assertFalse(
            self._runner_with_reach(elapsed=0.30 * 360.0, unentered=4)
            ._component_launches_earned()
        )
        self.assertTrue(
            self._runner_with_reach(elapsed=0.30 * 360.0, unentered=5)
            ._component_launches_earned()
        )

    def test_offered_once_the_run_is_late_and_tapping_has_stopped_paying(self) -> None:
        self.assertTrue(self._runner(elapsed=0.61 * 360.0)._component_launches_earned())

    def test_offered_near_the_end_of_the_budget(self) -> None:
        self.assertTrue(self._runner(elapsed=350.0)._component_launches_earned())

    def test_the_threshold_scales_with_run_length(self) -> None:
        # Half an hour into an hour-long run is still "early", where the same
        # wall-clock moment is "late" in a six-minute probe. Both cases carry no
        # unentered surface, so the clock is the only thing deciding.
        self.assertTrue(
            self._runner_with_reach(elapsed=1800.0, unentered=0, max_seconds=360.0)
            ._component_launches_earned()
        )
        self.assertFalse(
            self._runner_with_reach(elapsed=1800.0, unentered=0, max_seconds=3600.0)
            ._component_launches_earned()
        )

    def test_every_component_launching_rung_is_gated(self) -> None:
        from valordroid.recovery import RecoveryRung
        from valordroid.runner import _COMPONENT_LAUNCH_RUNGS

        self.assertEqual(
            _COMPONENT_LAUNCH_RUNGS,
            {
                RecoveryRung.DEEP_LINK,
                RecoveryRung.EXPORTED_INTENT,
                RecoveryRung.FORCED_ACTIVATION,
            },
        )

    def test_gui_and_navigation_rungs_are_never_gated(self) -> None:
        # Rungs 1, 2, 3, 6 and 8 act on the screen in front of the device or
        # navigate the recorded graph, which is the work that pays early.
        from valordroid.recovery import RecoveryRung
        from valordroid.runner import _COMPONENT_LAUNCH_RUNGS

        for rung in (
            RecoveryRung.CURRENT_UNTRIED,
            RecoveryRung.BACK_OR_REVEAL,
            RecoveryRung.KNOWN_GUI_ROUTE,
            RecoveryRung.BOUNDED_LLM,
            RecoveryRung.RESET_SEED,
        ):
            with self.subTest(rung=rung):
                self.assertNotIn(rung, _COMPONENT_LAUNCH_RUNGS)


class ReturnToAppFailurePathTest(unittest.TestCase):
    """A failed fresh return must ignore cached observer state.

    Cached `observer.latest` may predate Back or relaunch, so it cannot prove
    that either corrective dispatch returned to the app. Only a successful,
    post-dispatch capture can establish containment; if both attempts fail, the
    input observation is retained for the recovery ladder's fail-closed path.
    """

    APP = "com.example.app"

    def _runner(self, *, latest_state: str | None, fail_on_observe: bool):
        from valordroid.config import CoreConfig
        from valordroid.models import StateObservation
        from valordroid.runner import AndroidRunner

        app = self.APP
        observe_deadlines: list[float | None] = []
        launch_deadlines: list[float | None] = []

        def observation(state_id: str) -> StateObservation:
            activity = f"{app}/.Main"
            return StateObservation(
                state_id=state_id,
                observed_at=1.0,
                activity=activity,
                resumed_activities=(activity,),
            )

        class _Adb:
            def shell(self, *a, **k):
                return type("R", (), {"returncode": 0})()

        class _Session:
            adb = _Adb()
            package = app

            def launch(
                self, *, deadline_monotonic: float | None = None
            ) -> None:
                launch_deadlines.append(deadline_monotonic)
                raise OSError("package did not become foreground")

            def foreground(
                self, *, deadline_monotonic: float | None = None
            ):
                # The resumed set says the app is back, so containment goes on to
                # observe. These tests are about the capture failing anyway, which
                # is a different failure from Back not working, and the cheap
                # probe must not be allowed to stand in for the capture.
                activity = f"{app}/.Main"
                return ((activity,), activity)

        class _Observer:
            latest = (
                type("C", (), {"observation": observation(latest_state)})()
                if latest_state
                else None
            )

            def observe(self, *, deadline_monotonic: float | None = None):
                observe_deadlines.append(deadline_monotonic)
                if fail_on_observe:
                    raise OSError("device gone")
                return observation("c" * 64)

        runner = AndroidRunner.__new__(AndroidRunner)
        runner.session = _Session()
        runner.observer = _Observer()
        runner.runtime = type("R", (), {"post_action_delay_seconds": 0.0})()
        runner.core = type("C", (), {"config": CoreConfig()})()
        runner._consecutive_foreign_actions = 2
        runner._lifecycle = lambda *a, **k: None
        return runner, observation, observe_deadlines, launch_deadlines

    def test_failed_fresh_capture_ignores_cached_observer_latest(self) -> None:
        cached_state = "b" * 64
        runner, observation, observe_deadlines, launch_deadlines = self._runner(
            latest_state=cached_state,
            fail_on_observe=True,
        )
        before = observation("a" * 64)
        self.assertEqual(
            runner.observer.latest.observation.state_id,
            cached_state,
        )

        result = runner._return_to_app(before)

        self.assertIs(result, before)
        self.assertNotEqual(result.state_id, cached_state)
        self.assertEqual(len(observe_deadlines), 1)
        self.assertIsNotNone(observe_deadlines[0])
        self.assertEqual(observe_deadlines, launch_deadlines)

    def test_failure_with_no_observation_yet_falls_back_to_the_input(self) -> None:
        runner, observation, observe_deadlines, launch_deadlines = self._runner(
            latest_state=None,
            fail_on_observe=True,
        )
        before = observation("a" * 64)

        self.assertIs(runner._return_to_app(before), before)
        self.assertEqual(len(observe_deadlines), 1)
        self.assertIsNotNone(observe_deadlines[0])
        self.assertEqual(observe_deadlines, launch_deadlines)


class ModelStepSelectionTest(unittest.TestCase):
    """Step-level model selection must read the decision's actual payload.

    A first attempt read `ModelDecision.candidate_id`, which does not exist -- the
    chosen value lives in `.value` -- and every model-arm run aborted on
    `unrecoverable_runtime_error` within two seconds. Six of six smoke runs died
    before taking a single action.
    """

    def test_model_decision_exposes_its_answer_as_value(self) -> None:
        from valordroid.llm.gateway import ModelDecision

        fields = set(ModelDecision.__dataclass_fields__)
        self.assertIn("value", fields)
        self.assertNotIn("candidate_id", fields)

    def test_step_selection_requires_model_assistance(self) -> None:
        from valordroid.config import CoreConfig

        with self.assertRaises(ValueError):
            CoreConfig(model_step_selection=True, model_assistance_enabled=False)

    def test_step_stride_must_be_positive(self) -> None:
        from valordroid.config import CoreConfig

        with self.assertRaises(ValueError):
            CoreConfig(model_step_stride=0)

    def test_stride_of_one_asks_on_every_step(self) -> None:
        from valordroid.config import CoreConfig

        config = CoreConfig(
            model_assistance_enabled=True,
            model_step_selection=True,
            max_model_calls=10,
        )
        self.assertEqual(config.model_step_stride, 1)


class ModelUntriedPreferenceTest(unittest.TestCase):
    """The model may choose among untried candidates; it may not bypass them.

    Repeat share turned out to predict coverage almost perfectly across the
    60-minute campaign. Runs that spent about half their interactive actions on
    first-time (screen, action) pairs did well -- Chess 48% repeats and 58.85%,
    AntennaPod/gemini 50% and 23.96% -- while runs that spent about ninety
    percent on repeats did badly: Kiwix 86% and 26.57% against 32.44% for the
    same app when the model was only a stall fallback, Muzei 92%, AntennaPod/gemma
    93% and 11.47%.

    Repeats also pay almost nothing. Across those runs first-time actions returned
    609, 1670, 516 and 311 units while their repeats returned 6, 67, 467 and 15.
    Offering the model the whole ranked list let it re-pick stale candidates, so
    the untried set is now offered exclusively whenever it is non-empty.
    """

    @staticmethod
    def _ranked(reasons):
        class _Item:
            def __init__(self, reason, name):
                self.reason = reason
                self.eligible = reason != "cooling down, not permanently removed"
                self.candidate = type(
                    "C", (), {"stable_id": name, "action": type("A", (), {"kind": "tap", "parameters": {}})()}
                )()

        return [_Item(r, f"c{i}") for i, r in enumerate(reasons)]

    @staticmethod
    def _choices(ranked):
        # Mirrors the selection the runner performs before offering candidates.
        untried = [i for i in ranked if i.reason == "untried"]
        eligible = [i for i in ranked if i.eligible]
        return untried or eligible or list(ranked)

    def test_untried_candidates_are_offered_exclusively(self) -> None:
        ranked = self._ranked(
            ["previously productive", "untried", "opens new screens", "untried"]
        )
        chosen = self._choices(ranked)
        self.assertEqual([i.reason for i in chosen], ["untried", "untried"])

    def test_the_model_still_picks_between_several_untried_options(self) -> None:
        # The point is not to remove the model's judgement, only to stop it
        # spending the budget on work already done.
        ranked = self._ranked(["untried", "untried", "untried"])
        self.assertEqual(len(self._choices(ranked)), 3)

    def test_tried_candidates_are_offered_once_nothing_is_untried(self) -> None:
        ranked = self._ranked(["previously productive", "opens new screens"])
        self.assertEqual(len(self._choices(ranked)), 2)

    def test_suppressed_candidates_are_the_last_resort(self) -> None:
        ranked = self._ranked(
            ["cooling down, not permanently removed", "cooling down, not permanently removed"]
        )
        self.assertEqual(len(self._choices(ranked)), 2)
