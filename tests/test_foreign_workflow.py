"""Focused contract tests for frozen workflow-scoped foreign episodes."""

from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from valordroid.android.hierarchy import nodes_from_root
from valordroid.dispatch import (
    DISPATCH_FINALIZED,
    DISPATCH_INTENT,
    ActionDispatchReservation,
    finalization_details,
)
from valordroid.crashes import parse_app_crashes
from valordroid.foreign_workflow import (
    EPISODE_CONTAINMENT,
    EPISODE_FAILED,
    EPISODE_STARTED,
    EPISODE_STEP,
    EPISODE_SUCCEEDED,
    FOREIGN_TRANSITION_INTENT,
    RUN_BUDGET_BOUNDARY,
    RUN_BUDGET_STARTED,
    RUN_BUDGET_START_RECORD_ID,
    ForeignWorkflow,
    ForeignWorkflowEpisode,
    episode_lifecycle_record_id,
    foreign_transition_intent_record_id,
    run_boundary_record_id,
    _artifact_predicate_match_count,
    evaluate_return_predicate,
    matching_started_workflow,
    validate_foreign_workflow_lifecycle,
)
from valordroid.frontier import RankedCandidate
from valordroid.models import (
    ActionAttempt,
    ActionCandidate,
    ActionSpec,
    ExecutionRecord,
    ExecutionStatus,
    LifecycleRecord,
    LifecycleStatus,
    OutcomeKind,
    OutcomeRecord,
    Provenance,
    StateObservation,
    stable_hash,
)
from valordroid.runner import AndroidRunner
from valordroid.runtime_config import RuntimeConfig
from valordroid.setup_profile import SetupPredicate
from valordroid.summary import _foreign_workflow_diagnostics

AUT = "com.example.app"
FOREIGN = "com.android.documentsui"
OTHER = "com.android.permissioncontroller"
ENTRY_STATE = "entry-state"
FOREIGN_STATE = "foreign-state"
AUT_STATE = "returned-state"
ENTRY_COMPONENT = f"{AUT}/.MainActivity"
FOREIGN_COMPONENT = f"{FOREIGN}/.FilesActivity"
RETURN_COMPONENT = f"{AUT}/.DetailActivity"


def _entry_selector(package: str = AUT) -> dict[str, str]:
    return {
        "resource_id": f"{AUT}:id/import_file",
        "class_name": "android.widget.Button",
        "text": "Import",
        "content_description": "",
        "tree_path": "0/1/2",
        "package": package,
    }


def _foreign_selector(package: str = FOREIGN, *, tree_path: str = "0/2") -> dict[str, str]:
    return {
        "resource_id": "android:id/title",
        "class_name": "android.widget.TextView",
        "text": "photo.jpg",
        "content_description": "",
        "tree_path": tree_path,
        "package": package,
    }


def _action(
    selector: dict[str, str],
    *,
    state_id: str,
    kind: str = "tap",
) -> ActionSpec:
    return ActionSpec(
        kind=kind,
        target_id=stable_hash(selector),
        parameters={"expected_state_id": state_id, "selector": dict(selector)},
    )


def _workflow_mapping(
    *,
    entry_package: str = AUT,
    allowed_packages: list[str] | None = None,
    allowed_kinds: list[str] | None = None,
    max_actions: int = 3,
    max_seconds: float = 30.0,
    return_predicate: dict | None = None,
) -> dict:
    return {
        "workflow_id": "import-photo",
        "entry_action": _action(
            _entry_selector(entry_package),
            state_id=ENTRY_STATE,
        ).to_dict(),
        "allowed_foreign_packages": allowed_packages or [FOREIGN],
        "allowed_action_kinds": allowed_kinds or ["long_press", "tap"],
        "max_actions": max_actions,
        "max_seconds": max_seconds,
        "return_predicate": return_predicate
        or {
            "kind": "package_foreground",
            "parameters": {"package": AUT},
            "timeout_seconds": 1.0,
        },
    }


def _workflow(**overrides) -> ForeignWorkflow:
    return ForeignWorkflow.from_mapping(_workflow_mapping(**overrides), ordinal=1)


def _runtime_values(**overrides) -> dict:
    values = {
        "schema_version": RuntimeConfig.SCHEMA_VERSION,
        "serial": "emulator-5554",
        "launcher": None,
        "max_actions": 20,
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
    return values


def _observation(
    state_id: str,
    observed_at: float,
    component: str | None,
    *,
    hierarchy_sha256: str | None = None,
) -> StateObservation:
    return StateObservation(
        state_id=state_id,
        observed_at=observed_at,
        activity=component,
        resumed_activities=((component,) if component is not None else ()),
        hierarchy_sha256=hierarchy_sha256,
    )


def _execution(action: ActionSpec, started_at: float, ended_at: float) -> ExecutionRecord:
    return ExecutionRecord(
        status=ExecutionStatus.EXECUTED,
        started_at=started_at,
        ended_at=ended_at,
        action=action,
    )


def _install_scoped_execution_stub(runner: AndroidRunner) -> None:
    """Keep episode-policy unit tests independent of the core journal fixture."""

    def execute_and_record(
        *,
        before,
        requested,
        provenance,
        dispatch,
        route_id=None,
        model_call=None,
        unattributed_before_action=None,
        deadline_monotonic=None,
    ):
        execution = dispatch()
        after, gain, attempt_id = runner._record_execution(
            before=before,
            requested=requested,
            execution=execution,
            provenance=provenance,
            route_id=route_id,
            model_call=model_call,
            unattributed_before_action=unattributed_before_action,
            deadline_monotonic=deadline_monotonic,
        )
        return execution, after, gain, attempt_id

    runner._execute_and_record_action = execute_and_record


def _attempt(
    *,
    attempt_id: str,
    sequence: int,
    action: ActionSpec,
    before_component: str,
    after_component: str,
    after_state_id: str,
    started_at: float,
    ended_at: float,
    associated_units: tuple[str, ...] = (),
) -> ActionAttempt:
    execution = _execution(action, started_at, ended_at)
    return ActionAttempt(
        attempt_id=attempt_id,
        sequence=sequence,
        state_id=str(action.parameters["expected_state_id"]),
        provenance=Provenance.GUI,
        requested=action,
        execution=execution,
        outcome=OutcomeRecord(
            OutcomeKind.EFFECT,
            state_changed=True,
            details={
                "before_state_id": action.parameters["expected_state_id"],
                "after_state_id": after_state_id,
                "before_activity": before_component,
                "after_activity": after_component,
            },
        ),
        window_started_at=started_at,
        window_ended_at=ended_at + 0.2,
        associated_unit_ids=associated_units,
    )


def _record(
    record_id: str,
    observed_at: float,
    event: str,
    status: LifecycleStatus,
    details: dict,
    *,
    error: str | None = None,
    canonical_id: bool = True,
) -> LifecycleRecord:
    if canonical_id and event in {
        EPISODE_STARTED,
        EPISODE_STEP,
        EPISODE_SUCCEEDED,
        EPISODE_FAILED,
        EPISODE_CONTAINMENT,
    }:
        record_id = episode_lifecycle_record_id(
            event,
            str(details["episode_id"]),
            step_ordinal=(
                int(details["step_ordinal"])
                if event == EPISODE_STEP
                else None
            ),
        )
    return LifecycleRecord(
        record_id=record_id,
        observed_at=observed_at,
        phase="exploration",
        event=event,
        status=status,
        details=details,
        error=error,
    )


def _successful_lifecycle() -> tuple[
    ForeignWorkflow, tuple[ActionAttempt, ...], tuple[LifecycleRecord, ...]
]:
    workflow = _workflow()
    entry_action = _action(_entry_selector(), state_id=ENTRY_STATE)
    step_action = _action(_foreign_selector(), state_id=FOREIGN_STATE)
    entry = _attempt(
        attempt_id="a-entry",
        sequence=1,
        action=entry_action,
        before_component=ENTRY_COMPONENT,
        after_component=FOREIGN_COMPONENT,
        after_state_id=FOREIGN_STATE,
        started_at=1.0,
        ended_at=1.1,
    )
    step = _attempt(
        attempt_id="a-step",
        sequence=2,
        action=step_action,
        before_component=FOREIGN_COMPONENT,
        after_component=RETURN_COMPONENT,
        after_state_id=AUT_STATE,
        started_at=2.0,
        ended_at=2.1,
        associated_units=("aut.method.1",),
    )
    candidate_id = ActionCandidate(ENTRY_STATE, entry_action).stable_id
    episode = ForeignWorkflowEpisode.start(
        workflow,
        entry_attempt_id=entry.attempt_id,
        entry_action_id=entry_action.stable_id,
        entry_candidate_id=candidate_id,
        resolved_component=FOREIGN_COMPONENT,
        after_state_id=FOREIGN_STATE,
        started_at=1.2,
        started_monotonic=0.0,
    )
    start = _record(
        "l-start",
        1.3,
        EPISODE_STARTED,
        LifecycleStatus.STARTED,
        {
            **episode.common_details(),
            "after_state_id": FOREIGN_STATE,
            "allowed_foreign_packages": [FOREIGN],
            "allowed_action_kinds": ["long_press", "tap"],
            "return_predicate": workflow.return_predicate.to_dict(),
        },
    )
    ordinal = episode.note_executed_step(
        after_state_id=AUT_STATE, after_component=RETURN_COMPONENT
    )
    step_record = _record(
        "l-step",
        2.2,
        EPISODE_STEP,
        LifecycleStatus.SUCCEEDED,
        {
            **episode.common_details(),
            "step_ordinal": ordinal,
            "attempt_id": step.attempt_id,
            "action_id": step_action.stable_id,
            "action_kind": "tap",
            "selector_package": FOREIGN,
            "before_state_id": FOREIGN_STATE,
            "after_state_id": AUT_STATE,
            "after_observed_at": 2.15,
            "fresh_observation": True,
            "before_package": FOREIGN,
            "before_component": FOREIGN_COMPONENT,
            "after_package": AUT,
            "after_component": RETURN_COMPONENT,
            "actions_executed": 1,
            "elapsed_seconds": 1.0,
        },
    )
    evidence = {
        "predicate_kind": "package_foreground",
        "predicate_result": True,
        "matched_node_count": None,
        "state_id": AUT_STATE,
        "observation_sequence": None,
        "observation_observed_at": 2.15,
        "observed_package": AUT,
        "observed_component": RETURN_COMPONENT,
        "resumed_activities": [RETURN_COMPONENT],
        "hierarchy_sha256": None,
    }
    success = _record(
        "l-success",
        2.3,
        EPISODE_SUCCEEDED,
        LifecycleStatus.SUCCEEDED,
        {
            **episode.common_details(),
            "actions_executed": 1,
            "elapsed_seconds": 1.1,
            "terminal_reason": "return_postcondition_satisfied",
            "observed_state_id": AUT_STATE,
            "observed_package": AUT,
            "observed_component": RETURN_COMPONENT,
            "return_predicate_satisfied": True,
            "predicate_evidence": evidence,
        },
    )
    return workflow, (entry, step), (start, step_record, success)


def _failed_lifecycle() -> tuple[
    ForeignWorkflow, tuple[ActionAttempt, ...], tuple[LifecycleRecord, ...]
]:
    workflow = _workflow()
    entry_action = _action(_entry_selector(), state_id=ENTRY_STATE)
    entry = _attempt(
        attempt_id="a-entry",
        sequence=1,
        action=entry_action,
        before_component=ENTRY_COMPONENT,
        after_component=FOREIGN_COMPONENT,
        after_state_id=FOREIGN_STATE,
        started_at=1.0,
        ended_at=1.1,
    )
    episode = ForeignWorkflowEpisode.start(
        workflow,
        entry_attempt_id=entry.attempt_id,
        entry_action_id=entry_action.stable_id,
        entry_candidate_id=ActionCandidate(ENTRY_STATE, entry_action).stable_id,
        resolved_component=FOREIGN_COMPONENT,
        after_state_id=FOREIGN_STATE,
        started_at=1.2,
        started_monotonic=0.0,
    )
    start = _record(
        "l-start",
        1.3,
        EPISODE_STARTED,
        LifecycleStatus.STARTED,
        {
            **episode.common_details(),
            "after_state_id": FOREIGN_STATE,
            "allowed_foreign_packages": [FOREIGN],
            "allowed_action_kinds": ["long_press", "tap"],
            "return_predicate": workflow.return_predicate.to_dict(),
        },
    )
    evidence = {
        "predicate_kind": "package_foreground",
        "predicate_result": False,
        "matched_node_count": None,
        "state_id": FOREIGN_STATE,
        "observation_sequence": None,
        "observation_observed_at": 1.2,
        "observed_package": FOREIGN,
        "observed_component": FOREIGN_COMPONENT,
        "resumed_activities": [FOREIGN_COMPONENT],
        "hierarchy_sha256": None,
    }
    failure = _record(
        "l-failure",
        1.4,
        EPISODE_FAILED,
        LifecycleStatus.FAILED,
        {
            **episode.common_details(),
            "actions_executed": 0,
            "elapsed_seconds": 0.2,
            "terminal_reason": "no_allowed_action",
            "observed_state_id": FOREIGN_STATE,
            "observed_package": FOREIGN,
            "observed_component": FOREIGN_COMPONENT,
            "return_predicate_satisfied": False,
            "predicate_evidence": evidence,
        },
        error="no_allowed_action",
    )
    containment = _record(
        "l-containment",
        1.5,
        EPISODE_CONTAINMENT,
        LifecycleStatus.SUCCEEDED,
        {
            **episode.common_details(),
            "actions_executed": 0,
            "terminal_reason": "no_allowed_action",
            "method": "back",
            "containment_started_at": 1.4,
            "containment_completed_at": 1.47,
            "containment_elapsed_seconds": 0.07,
            "containment_timeout_seconds": 15.0,
            "last_dispatch_at": 1.45,
            "before_state_id": FOREIGN_STATE,
            "before_package": FOREIGN,
            "before_component": FOREIGN_COMPONENT,
            "after_state_id": ENTRY_STATE,
            "after_observed_at": 1.46,
            "after_package": AUT,
            "after_component": ENTRY_COMPONENT,
            "resumed_activities": [ENTRY_COMPONENT],
            "hierarchy_sha256": None,
            "fresh_observation": True,
            "back_attempted": True,
            "relaunch_attempted": False,
            "returned_to_aut": True,
            "return_predicate_satisfied": True,
        },
    )
    return workflow, (entry,), (start, failure, containment)


def _failed_action_lifecycle(
    *,
    changed: bool,
    status: ExecutionStatus = ExecutionStatus.NOT_EXECUTED,
) -> tuple[
    ForeignWorkflow, tuple[ActionAttempt, ...], tuple[LifecycleRecord, ...]
]:
    workflow, entry_attempts, base_lifecycle = _failed_lifecycle()
    entry = entry_attempts[0]
    action = _action(_foreign_selector(), state_id=FOREIGN_STATE)
    error = "stale target" if status is ExecutionStatus.NOT_EXECUTED else "ADB failed"
    execution = ExecutionRecord(
        status=status,
        started_at=1.30,
        ended_at=1.31,
        action=action if status is ExecutionStatus.FAILED else None,
        error=error,
    )
    after_state = "foreign-state-changed" if changed else FOREIGN_STATE
    after_component = (
        f"{FOREIGN}/.DirectoryActivity" if changed else FOREIGN_COMPONENT
    )
    outcome_details = {
        "before_state_id": FOREIGN_STATE,
        "after_state_id": after_state,
        "before_activity": FOREIGN_COMPONENT,
        "after_activity": after_component,
    }
    if status is ExecutionStatus.FAILED:
        outcome_details["execution_error"] = error
    failed_attempt = ActionAttempt(
        attempt_id="a-failed-step",
        sequence=2,
        state_id=FOREIGN_STATE,
        provenance=Provenance.GUI,
        requested=action,
        execution=execution,
        outcome=OutcomeRecord(
            OutcomeKind.NOT_EXECUTED
            if status is ExecutionStatus.NOT_EXECUTED
            else OutcomeKind.FAILED,
            state_changed=changed,
            details=outcome_details,
        ),
        window_started_at=1.30,
        window_ended_at=1.40,
    )
    failure_details = dict(base_lifecycle[1].details)
    failure_details.update(
        {
            "attempt_id": failed_attempt.attempt_id,
            "terminal_reason": "foreign_action_not_executed",
            "observed_state_id": after_state,
            "observed_package": FOREIGN,
            "observed_component": after_component,
            "predicate_evidence": {
                **failure_details["predicate_evidence"],
                "state_id": after_state,
                "observation_observed_at": 1.35,
                "observed_package": FOREIGN,
                "observed_component": after_component,
                "resumed_activities": [after_component],
            },
        }
    )
    failure = _record(
        "l-failed-attempt",
        1.40,
        EPISODE_FAILED,
        LifecycleStatus.FAILED,
        failure_details,
        error="foreign_action_not_executed",
    )
    containment_details = dict(base_lifecycle[2].details)
    containment_details.update(
        {
            "terminal_reason": "foreign_action_not_executed",
            "before_state_id": after_state,
            "before_package": FOREIGN,
            "before_component": after_component,
        }
    )
    containment = _record(
        "l-failed-attempt-containment",
        1.50,
        EPISODE_CONTAINMENT,
        LifecycleStatus.SUCCEEDED,
        containment_details,
    )
    return workflow, (entry, failed_attempt), (
        base_lifecycle[0],
        failure,
        containment,
    )


class RuntimeSerializationTest(unittest.TestCase):
    def test_absent_workflows_round_trip_as_legacy_schema_four(self) -> None:
        runtime = RuntimeConfig.from_mapping(_runtime_values())
        self.assertEqual(runtime.foreign_workflows, ())
        self.assertNotIn("foreign_workflows", runtime.to_dict())
        self.assertEqual(RuntimeConfig.from_mapping(runtime.to_dict()), runtime)

    def test_configured_workflow_is_canonical_and_frozen_in_the_manifest_shape(self) -> None:
        runtime = RuntimeConfig.from_mapping(
            _runtime_values(foreign_workflows=[_workflow_mapping()])
        )
        encoded = runtime.to_dict()
        self.assertEqual(encoded["foreign_workflows"], [_workflow_mapping()])
        self.assertEqual(RuntimeConfig.from_mapping(encoded), runtime)

    def test_entry_and_return_contracts_must_be_exactly_aut_owned(self) -> None:
        workflow = _workflow(entry_package=f"{AUT}.debug")
        with self.assertRaisesRegex(ValueError, "entry selector is not AUT-owned"):
            workflow.validate_for_aut(AUT)
        wrong_return = _workflow(
            return_predicate={
                "kind": "package_foreground",
                "parameters": {"package": f"{AUT}.debug"},
                "timeout_seconds": 1.0,
            }
        )
        with self.assertRaisesRegex(ValueError, "return predicate is not AUT-owned"):
            wrong_return.validate_for_aut(AUT)


class EntryAuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = _workflow()
        self.workflow.validate_for_aut(AUT)
        self.action = _action(_entry_selector(), state_id=ENTRY_STATE)
        now = time.time()
        self.before = _observation(ENTRY_STATE, now - 2.0, ENTRY_COMPONENT)
        self.after = _observation(FOREIGN_STATE, now, FOREIGN_COMPONENT)
        self.execution = _execution(self.action, now - 1.5, now - 0.5)

    def test_only_an_executed_exact_entry_followed_by_fresh_unique_target_starts(self) -> None:
        matched = matching_started_workflow(
            (self.workflow,),
            aut_package=AUT,
            before=self.before,
            action=self.action,
            execution=self.execution,
            after=self.after,
        )
        self.assertIs(matched, self.workflow)

    def test_wrong_or_prefix_sharing_foreign_packages_are_not_authorized(self) -> None:
        for component in (f"{FOREIGN}.debug/.FilesActivity", f"{OTHER}/.Grant"):
            with self.subTest(component=component):
                after = _observation(FOREIGN_STATE, time.time(), component)
                self.assertIsNone(
                    matching_started_workflow(
                        (self.workflow,),
                        aut_package=AUT,
                        before=self.before,
                        action=self.action,
                        execution=self.execution,
                        after=after,
                    )
                )

    def test_unexecuted_or_stale_entry_observation_cannot_start(self) -> None:
        not_executed = ExecutionRecord(
            status=ExecutionStatus.NOT_EXECUTED,
            started_at=self.execution.started_at,
            ended_at=self.execution.ended_at,
            error="stale target",
        )
        self.assertIsNone(
            matching_started_workflow(
                (self.workflow,),
                aut_package=AUT,
                before=self.before,
                action=self.action,
                execution=not_executed,
                after=self.after,
            )
        )
        stale = _observation(
            FOREIGN_STATE, self.execution.ended_at - 0.1, FOREIGN_COMPONENT
        )
        self.assertIsNone(
            matching_started_workflow(
                (self.workflow,),
                aut_package=AUT,
                before=self.before,
                action=self.action,
                execution=self.execution,
                after=stale,
            )
        )

    def test_requested_match_cannot_hide_a_different_executed_action(self) -> None:
        different = _action(_foreign_selector(), state_id=ENTRY_STATE)
        execution = _execution(
            different,
            self.execution.started_at,
            self.execution.ended_at,
        )
        self.assertIsNone(
            matching_started_workflow(
                (self.workflow,),
                aut_package=AUT,
                before=self.before,
                action=self.action,
                execution=execution,
                after=self.after,
            )
        )

    def test_configured_workflows_close_the_unscoped_foreign_path(self) -> None:
        runner = AndroidRunner.__new__(AndroidRunner)
        runner.session = SimpleNamespace(package=AUT)
        runner.runtime = SimpleNamespace(foreign_workflows=(self.workflow,))
        runner._activity_package_name = AUT
        runner._active_foreign_episode = None
        foreign_candidate = ActionCandidate(
            FOREIGN_STATE,
            _action(_foreign_selector(), state_id=FOREIGN_STATE),
        )
        self.assertFalse(
            runner._workflow_candidate_allowed(self.after, foreign_candidate)
        )
        self.assertTrue(runner._workflow_foreground_rejected(self.after))
        runner.runtime = SimpleNamespace(foreign_workflows=())
        self.assertTrue(
            runner._workflow_candidate_allowed(self.after, foreign_candidate)
        )
        self.assertFalse(runner._workflow_foreground_rejected(self.after))

    def test_active_episode_filters_both_package_and_action_kind_exactly(self) -> None:
        episode = ForeignWorkflowEpisode.start(
            self.workflow,
            entry_attempt_id="a-entry",
            entry_action_id=self.action.stable_id,
            entry_candidate_id=ActionCandidate(ENTRY_STATE, self.action).stable_id,
            resolved_component=FOREIGN_COMPONENT,
            after_state_id=FOREIGN_STATE,
            started_at=self.after.observed_at,
            started_monotonic=time.monotonic(),
        )
        allowed = _action(_foreign_selector(), state_id=FOREIGN_STATE)
        self.assertTrue(episode.allows(FOREIGN_COMPONENT, allowed))
        self.assertFalse(
            episode.allows(
                FOREIGN_COMPONENT,
                _action(_foreign_selector(OTHER), state_id=FOREIGN_STATE),
            )
        )
        self.assertFalse(
            episode.allows(
                f"{FOREIGN}.debug/.FilesActivity",
                allowed,
            )
        )
        self.assertFalse(
            episode.allows(
                FOREIGN_COMPONENT,
                _action(_foreign_selector(), state_id=FOREIGN_STATE, kind="scroll_forward"),
            )
        )


class BoundsAndProgressTest(unittest.TestCase):
    def test_action_and_wall_clock_bounds_are_independent(self) -> None:
        workflow = _workflow(max_actions=2, max_seconds=5.0)
        action = _action(_entry_selector(), state_id=ENTRY_STATE)
        episode = ForeignWorkflowEpisode.start(
            workflow,
            entry_attempt_id="a-entry",
            entry_action_id=action.stable_id,
            entry_candidate_id=ActionCandidate(ENTRY_STATE, action).stable_id,
            resolved_component=FOREIGN_COMPONENT,
            after_state_id=FOREIGN_STATE,
            started_at=1.0,
            started_monotonic=10.0,
        )
        self.assertIsNone(episode.bound_reason(10.1))
        episode.note_executed_step(
            after_state_id="foreign-2", after_component=FOREIGN_COMPONENT
        )
        self.assertIsNone(episode.bound_reason(10.2))
        episode.note_executed_step(
            after_state_id="foreign-3", after_component=FOREIGN_COMPONENT
        )
        self.assertEqual(episode.bound_reason(10.3), "max_actions")
        episode.actions_executed = 0
        self.assertEqual(episode.bound_reason(15.0), "max_seconds")

    def test_runner_allows_zero_gain_multi_step_episode_then_exact_return(self) -> None:
        workflow = _workflow(max_actions=3, max_seconds=30.0)
        entry_action = _action(_entry_selector(), state_id=ENTRY_STATE)
        now = time.time()
        first = _observation(FOREIGN_STATE, now, FOREIGN_COMPONENT)
        second_component = f"{FOREIGN}/.DirectoryActivity"
        second = _observation("foreign-2", now + 1.0, second_component)
        returned = _observation(AUT_STATE, now + 2.0, RETURN_COMPONENT)
        episode = ForeignWorkflowEpisode.start(
            workflow,
            entry_attempt_id="a-entry",
            entry_action_id=entry_action.stable_id,
            entry_candidate_id=ActionCandidate(ENTRY_STATE, entry_action).stable_id,
            resolved_component=FOREIGN_COMPONENT,
            after_state_id=FOREIGN_STATE,
            started_at=now,
            started_monotonic=time.monotonic(),
        )
        first_action = _action(_foreign_selector(tree_path="0/2"), state_id=FOREIGN_STATE)
        second_action = _action(_foreign_selector(tree_path="0/3"), state_id="foreign-2")
        executions = [
            _execution(first_action, now + 0.1, now + 0.5),
            _execution(second_action, now + 1.1, now + 1.5),
        ]
        afters = [second, returned]
        attempt_ids = ["a-step-1", "a-step-2"]

        class _Executor:
            def execute(
                self,
                _action_spec: ActionSpec,
                *,
                timeout_seconds: float | None = None,
                deadline_monotonic: float | None = None,
            ) -> ExecutionRecord:
                self.timeout_seconds = timeout_seconds
                return executions.pop(0)

        class _Observer:
            latest = None

            @contextmanager
            def bounded_window(self, seconds: float):
                self.window_seconds = seconds
                yield

        runner = AndroidRunner.__new__(AndroidRunner)
        runner._activity_package_name = AUT
        runner._active_foreign_episode = episode
        runner._step_index = 0
        runner._consecutive_stuck_screens = 0
        runner._consecutive_foreign_actions = 99
        runner.executor = _Executor()
        runner.observer = _Observer()
        runner._lifecycle_calls = []
        runner._lifecycle = lambda phase, event, status, details=None, error=None, **_metadata: (
            runner._lifecycle_calls.append((event, status, details, error))
        )

        def record_execution(**_kwargs):
            return afters.pop(0), 0, attempt_ids.pop(0)

        runner._record_execution = record_execution
        _install_scoped_execution_stub(runner)
        ranked_first = (
            RankedCandidate(
                ActionCandidate(FOREIGN_STATE, first_action),
                False,
                -1000.0,
                "repeated zero associated-unit yield",
            ),
        )
        observed = runner._run_foreign_episode_step(first, ranked_first)
        self.assertEqual(observed.state_id, "foreign-2")
        self.assertIsNotNone(runner._active_foreign_episode)
        ranked_second = (
            RankedCandidate(
                ActionCandidate("foreign-2", second_action), True, 1.0, "untried"
            ),
        )
        observed = runner._run_foreign_episode_step(observed, ranked_second)
        self.assertEqual(observed.state_id, AUT_STATE)
        self.assertIsNone(runner._active_foreign_episode)
        events = [
            event
            for event, *_ in runner._lifecycle_calls
            if event != "foreign_workflow_transition_intent"
        ]
        self.assertEqual(events, [EPISODE_STEP, EPISODE_STEP, EPISODE_SUCCEEDED])
        self.assertLessEqual(runner.executor.timeout_seconds, workflow.max_seconds)
        self.assertLessEqual(runner.observer.window_seconds, workflow.max_seconds)
        # The legacy generic counter is deliberately irrelevant while scoped.
        self.assertEqual(runner._consecutive_foreign_actions, 0)

    def test_nonexecuted_foreign_attempt_is_bound_without_spending_action(self) -> None:
        workflow = _workflow(max_actions=2, max_seconds=30.0)
        entry_action = _action(_entry_selector(), state_id=ENTRY_STATE)
        now = time.time()
        before = _observation(FOREIGN_STATE, now, FOREIGN_COMPONENT)
        changed = _observation(
            "foreign-state-changed",
            now + 0.2,
            f"{FOREIGN}/.DirectoryActivity",
        )
        action = _action(_foreign_selector(), state_id=FOREIGN_STATE)
        execution = ExecutionRecord(
            status=ExecutionStatus.NOT_EXECUTED,
            started_at=now + 0.05,
            ended_at=now + 0.05,
            action=None,
            error="stale target",
        )
        episode = ForeignWorkflowEpisode.start(
            workflow,
            entry_attempt_id="a-entry",
            entry_action_id=entry_action.stable_id,
            entry_candidate_id=ActionCandidate(ENTRY_STATE, entry_action).stable_id,
            resolved_component=FOREIGN_COMPONENT,
            after_state_id=FOREIGN_STATE,
            started_at=now,
            started_monotonic=time.monotonic(),
        )

        class _Executor:
            def execute(self, _action_spec, **_kwargs):
                return execution

        class _Observer:
            latest = None

            @contextmanager
            def bounded_window(self, _seconds):
                yield

        runner = AndroidRunner.__new__(AndroidRunner)
        runner._activity_package_name = AUT
        runner._active_foreign_episode = episode
        runner._step_index = 0
        runner._consecutive_stuck_screens = 0
        runner._consecutive_foreign_actions = 0
        runner.executor = _Executor()
        runner.observer = _Observer()
        runner._lifecycle_calls = []
        runner._lifecycle = lambda phase, event, status, details=None, error=None, **_metadata: (
            runner._lifecycle_calls.append((event, status, details, error))
        )
        runner._record_execution = lambda **_kwargs: (
            changed,
            0,
            "a-failed-step",
        )
        _install_scoped_execution_stub(runner)
        def contain_failed(observation, *, episode, episode_terminal_reason):
            runner._record_episode_containment(
                episode,
                reason=episode_terminal_reason,
                before=observation,
                after=observation,
                method="failed",
                back_attempted=True,
                relaunch_attempted=True,
                error="scripted containment failure",
            )
            return observation

        runner._return_to_app = contain_failed

        observed = runner._run_foreign_episode_step(
            before,
            (
                RankedCandidate(
                    ActionCandidate(FOREIGN_STATE, action), True, 1.0, "untried"
                ),
            ),
        )

        self.assertEqual(observed.state_id, changed.state_id)
        self.assertIsNone(runner._active_foreign_episode)
        self.assertEqual(episode.actions_executed, 0)
        self.assertEqual(episode.last_state_id, changed.state_id)
        failure = next(
            call for call in runner._lifecycle_calls if call[0] == EPISODE_FAILED
        )
        self.assertEqual(failure[0], EPISODE_FAILED)
        self.assertEqual(failure[2]["attempt_id"], "a-failed-step")
        self.assertEqual(failure[2]["actions_executed"], 0)

    def test_remaining_deadline_clamps_action_observation_and_outcome_windows(self) -> None:
        workflow = _workflow(max_actions=2, max_seconds=100.0)
        entry_action = _action(_entry_selector(), state_id=ENTRY_STATE)
        now = time.time()
        before = _observation(FOREIGN_STATE, now, FOREIGN_COMPONENT)
        returned = _observation(AUT_STATE, now + 0.2, RETURN_COMPONENT)
        action = _action(_foreign_selector(), state_id=FOREIGN_STATE)
        execution = _execution(action, now + 0.05, now + 0.1)
        episode = ForeignWorkflowEpisode.start(
            workflow,
            entry_attempt_id="a-entry",
            entry_action_id=entry_action.stable_id,
            entry_candidate_id=ActionCandidate(ENTRY_STATE, entry_action).stable_id,
            resolved_component=FOREIGN_COMPONENT,
            after_state_id=FOREIGN_STATE,
            started_at=now,
            started_monotonic=time.monotonic() - 80.0,
        )
        deadline = episode.deadline_monotonic
        captured: dict[str, float] = {}

        class _Executor:
            def execute(
                self,
                _action_spec: ActionSpec,
                *,
                timeout_seconds: float | None = None,
                deadline_monotonic: float | None = None,
            ) -> ExecutionRecord:
                assert timeout_seconds is not None
                assert deadline_monotonic is not None
                captured["action_timeout"] = timeout_seconds
                captured["executor_deadline"] = deadline_monotonic
                return execution

        class _Observer:
            latest = None

            @contextmanager
            def bounded_window(self, seconds: float):
                captured["observation_window"] = seconds
                yield

        runner = AndroidRunner.__new__(AndroidRunner)
        runner._activity_package_name = AUT
        runner._active_foreign_episode = episode
        runner._step_index = 0
        runner._consecutive_stuck_screens = 0
        runner._consecutive_foreign_actions = 0
        runner.executor = _Executor()
        runner.observer = _Observer()
        runner._lifecycle = lambda *_args, **_kwargs: None

        def record_execution(**kwargs):
            captured["outcome_deadline"] = kwargs["deadline_monotonic"]
            return returned, 0, "a-step"

        runner._record_execution = record_execution
        _install_scoped_execution_stub(runner)
        observed = runner._run_foreign_episode_step(
            before,
            (
                RankedCandidate(
                    ActionCandidate(FOREIGN_STATE, action), True, 1.0, "untried"
                ),
            ),
        )

        self.assertEqual(observed.state_id, AUT_STATE)
        self.assertIsNone(runner._active_foreign_episode)
        self.assertGreater(captured["action_timeout"], 0.0)
        self.assertLess(captured["action_timeout"], workflow.max_seconds / 2)
        self.assertEqual(
            captured["observation_window"], captured["action_timeout"]
        )
        self.assertEqual(captured["executor_deadline"], deadline)
        self.assertEqual(captured["outcome_deadline"], deadline)


class ExactReturnPredicateTest(unittest.TestCase):
    def test_node_postcondition_requires_exact_aut_ownership(self) -> None:
        workflow = _workflow(
            return_predicate={
                "kind": "node_present",
                "parameters": {
                    "selector": {
                        "package": AUT,
                        "resource_id": f"{AUT}:id/imported_name",
                        "text": "photo.jpg",
                    }
                },
                "timeout_seconds": 1.0,
            }
        )
        workflow.validate_for_aut(AUT)
        node = SimpleNamespace(
            selector={
                "package": AUT,
                "resource_id": f"{AUT}:id/imported_name",
                "class_name": "android.widget.TextView",
                "text": "photo.jpg",
                "content_description": "",
                "tree_path": "0/4",
            },
            attributes={},
        )
        exact = _observation(AUT_STATE, 3.0, RETURN_COMPONENT)
        result, evidence = evaluate_return_predicate(
            workflow, aut_package=AUT, observation=exact, nodes=(node,)
        )
        self.assertTrue(result)
        self.assertEqual(evidence["matched_node_count"], 1)
        prefix = _observation(
            AUT_STATE, 3.0, f"{AUT}.debug/.DetailActivity"
        )
        result, _ = evaluate_return_predicate(
            workflow, aut_package=AUT, observation=prefix, nodes=(node,)
        )
        self.assertFalse(result)

    def test_node_absence_is_not_proven_without_the_bound_fresh_hierarchy(self) -> None:
        workflow = _workflow(
            return_predicate={
                "kind": "node_absent",
                "parameters": {
                    "selector": {
                        "package": AUT,
                        "resource_id": f"{AUT}:id/progress",
                    }
                },
                "timeout_seconds": 1.0,
            }
        )
        entry_action = _action(_entry_selector(), state_id=ENTRY_STATE)
        episode = ForeignWorkflowEpisode.start(
            workflow,
            entry_attempt_id="a-entry",
            entry_action_id=entry_action.stable_id,
            entry_candidate_id=ActionCandidate(ENTRY_STATE, entry_action).stable_id,
            resolved_component=FOREIGN_COMPONENT,
            after_state_id=FOREIGN_STATE,
            started_at=1.0,
            started_monotonic=1.0,
        )
        runner = AndroidRunner.__new__(AndroidRunner)
        runner._activity_package_name = AUT
        runner.observer = SimpleNamespace(latest=None)
        result, evidence = runner._episode_predicate(
            episode, _observation(AUT_STATE, 2.0, RETURN_COMPONENT)
        )
        self.assertFalse(result)
        self.assertIsNone(evidence["matched_node_count"])

    def test_node_absence_replay_excludes_missing_invalid_and_non_node_bounds(
        self,
    ) -> None:
        workflow = _workflow(
            return_predicate={
                "kind": "node_absent",
                "parameters": {
                    "selector": {
                        "package": AUT,
                        "resource_id": f"{AUT}:id/progress",
                    }
                },
                "timeout_seconds": 1.0,
            }
        )
        xml = (
            f'<hierarchy><node package="{AUT}" '
            f'resource-id="{AUT}:id/progress" />'
            f'<node package="{AUT}" resource-id="{AUT}:id/progress" '
            'bounds="invalid" />'
            f'<node package="{AUT}" resource-id="{AUT}:id/progress" '
            'bounds="[0,0][0,10]" />'
            f'<metadata package="{AUT}" resource-id="{AUT}:id/progress" '
            'bounds="[0,0][10,10]" />'
            f'<node package="{AUT}" resource-id="{AUT}:id/other" '
            'bounds="[-10,-10][10,10]" /></hierarchy>'
        ).encode("utf-8")
        digest = hashlib.sha256(xml).hexdigest()
        observation = _observation(
            AUT_STATE,
            3.0,
            RETURN_COMPONENT,
            hierarchy_sha256=digest,
        )
        root = ET.fromstring(xml)
        result, evidence = evaluate_return_predicate(
            workflow,
            aut_package=AUT,
            observation=observation,
            nodes=nodes_from_root(root),
            observation_sequence=9,
        )
        self.assertTrue(result)
        self.assertEqual(evidence["matched_node_count"], 0)

        with tempfile.TemporaryDirectory() as temporary:
            artifact_directory = (
                Path(temporary) / f"{9:08d}-{AUT_STATE[:12]}"
            )
            artifact_directory.mkdir()
            (artifact_directory / "hierarchy.xml").write_bytes(xml)
            self.assertEqual(
                _artifact_predicate_match_count(
                    Path(temporary),
                    sequence=9,
                    state_id=AUT_STATE,
                    hierarchy_sha256=digest,
                    predicate=workflow.return_predicate,
                ),
                0,
            )


class LifecycleValidationTest(unittest.TestCase):
    def test_start_step_success_sequence_binds_to_canonical_gui_attempts(self) -> None:
        workflow, attempts, lifecycle = _successful_lifecycle()
        validate_foreign_workflow_lifecycle(
            (workflow,),
            aut_package=AUT,
            lifecycle=lifecycle,
            attempts=attempts,
            run_status="finished",
        )
        self.assertTrue(all(attempt.provenance is Provenance.GUI for attempt in attempts))
        self.assertEqual(attempts[1].associated_unit_ids, ("aut.method.1",))

    def test_failure_requires_a_bounded_containment_terminal(self) -> None:
        workflow, attempts, lifecycle = _failed_lifecycle()
        validate_foreign_workflow_lifecycle(
            (workflow,),
            aut_package=AUT,
            lifecycle=lifecycle,
            attempts=attempts,
            run_status="finished",
        )
        with self.assertRaisesRegex(ValueError, "unterminated"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=lifecycle[:-1],
                attempts=attempts,
                run_status="finished",
            )

    def test_failed_attempt_terminal_binds_changed_and_unchanged_observations(
        self,
    ) -> None:
        for status in (ExecutionStatus.NOT_EXECUTED, ExecutionStatus.FAILED):
            for changed in (False, True):
                with self.subTest(status=status.value, changed=changed):
                    workflow, attempts, lifecycle = _failed_action_lifecycle(
                        changed=changed, status=status
                    )
                    validate_foreign_workflow_lifecycle(
                        (workflow,),
                        aut_package=AUT,
                        lifecycle=lifecycle,
                        attempts=attempts,
                        run_status="finished",
                    )
                    self.assertEqual(
                        lifecycle[1].details["actions_executed"], 0
                    )

        workflow, attempts, lifecycle = _failed_action_lifecycle(changed=False)
        details = dict(lifecycle[1].details)
        del details["attempt_id"]
        unbound = (
            lifecycle[0],
            _record(
                "l-unbound-failure",
                lifecycle[1].observed_at,
                EPISODE_FAILED,
                LifecycleStatus.FAILED,
                details,
                error="foreign_action_not_executed",
            ),
            lifecycle[2],
        )
        with self.assertRaisesRegex(ValueError, "details fields"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=unbound,
                attempts=attempts,
                run_status="finished",
            )

    def test_runtime_stop_ignores_an_uncommitted_latest_capture(self) -> None:
        workflow, attempts, base_lifecycle = _failed_lifecycle()
        entry = attempts[0]
        canonical = _observation(FOREIGN_STATE, 1.2, FOREIGN_COMPONENT)
        uncommitted = _observation(
            "uncommitted-state",
            1.35,
            f"{FOREIGN}/.UncommittedActivity",
        )
        episode = ForeignWorkflowEpisode.start(
            workflow,
            entry_attempt_id=entry.attempt_id,
            entry_action_id=entry.requested.stable_id,
            entry_candidate_id=ActionCandidate(
                entry.state_id, entry.requested
            ).stable_id,
            resolved_component=FOREIGN_COMPONENT,
            after_state_id=FOREIGN_STATE,
            started_at=canonical.observed_at,
            started_monotonic=time.monotonic(),
            after_observation=canonical,
        )
        runner = AndroidRunner.__new__(AndroidRunner)
        runner._activity_package_name = AUT
        runner._active_foreign_episode = episode
        runner._consecutive_foreign_actions = 0
        runner.observer = SimpleNamespace(
            latest=SimpleNamespace(observation=uncommitted, nodes=()),
            sequence=99,
        )
        generated: list[LifecycleRecord] = []

        def lifecycle(phase, event, status, details=None, error=None, **metadata):
            self.assertEqual(phase, "exploration")
            generated.append(
                _record(
                    str(metadata.get("record_id") or f"generated-{len(generated) + 1}"),
                    float(metadata.get("observed_at") or (2.0 + len(generated))),
                    event,
                    status,
                    dict(details or {}),
                    error=error,
                )
            )

        def return_to_app(before, *, episode, episode_terminal_reason):
            after = _observation(ENTRY_STATE, 1.6, ENTRY_COMPONENT)
            runner._record_episode_containment(
                episode,
                reason=episode_terminal_reason,
                before=before,
                after=after,
                method="back",
                back_attempted=True,
                relaunch_attempted=False,
            )
            return after

        runner._lifecycle = lifecycle
        runner._return_to_app = return_to_app
        selected = runner._canonical_episode_observation(episode)
        self.assertEqual(selected, canonical)
        self.assertNotEqual(selected, uncommitted)
        runner._fail_foreign_episode(selected, reason="runtime_stopped")

        validate_foreign_workflow_lifecycle(
            (workflow,),
            aut_package=AUT,
            lifecycle=(base_lifecycle[0], *generated),
            attempts=attempts,
            run_status="aborted",
        )

    def test_wrong_package_or_tampered_step_is_rejected(self) -> None:
        workflow, attempts, lifecycle = _successful_lifecycle()
        step = lifecycle[1]
        details = dict(step.details)
        details["selector_package"] = OTHER
        tampered = (
            lifecycle[0],
            _record(
                "l-step-tampered",
                step.observed_at,
                EPISODE_STEP,
                LifecycleStatus.SUCCEEDED,
                details,
            ),
            lifecycle[2],
        )
        with self.assertRaisesRegex(ValueError, "canonical action"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=tampered,
                attempts=attempts,
                run_status="finished",
            )

    def test_node_predicate_replays_retained_hierarchy_and_rejects_bad_evidence(
        self,
    ) -> None:
        workflow = _workflow(
            return_predicate={
                "kind": "node_present",
                "parameters": {
                    "selector": {
                        "package": AUT,
                        "resource_id": f"{AUT}:id/imported_name",
                        "text": "photo.jpg",
                    }
                },
                "timeout_seconds": 1.0,
            }
        )
        _base_workflow, attempts, base_lifecycle = _successful_lifecycle()
        entry = attempts[0]
        episode = ForeignWorkflowEpisode.start(
            workflow,
            entry_attempt_id=entry.attempt_id,
            entry_action_id=entry.requested.stable_id,
            entry_candidate_id=ActionCandidate(
                entry.state_id, entry.requested
            ).stable_id,
            resolved_component=FOREIGN_COMPONENT,
            after_state_id=FOREIGN_STATE,
            started_at=1.2,
            started_monotonic=0.0,
        )
        episode.note_executed_step(
            after_state_id=AUT_STATE, after_component=RETURN_COMPONENT
        )
        xml = (
            f'<hierarchy><node package="{AUT}" '
            f'resource-id="{AUT}:id/imported_name" '
            'class="android.widget.TextView" text="photo.jpg" '
            'content-desc="" bounds="[0,0][10,10]" />'
            f'<node package="{AUT}" resource-id="{AUT}:id/imported_name" '
            'class="android.widget.TextView" text="photo.jpg" />'
            f'<node package="{AUT}" resource-id="{AUT}:id/imported_name" '
            'class="android.widget.TextView" text="photo.jpg" '
            'bounds="[0,0][0,10]" />'
            f'<metadata package="{AUT}" resource-id="{AUT}:id/imported_name" '
            'class="android.widget.TextView" text="photo.jpg" '
            'bounds="[0,0][10,10]" /></hierarchy>'
        ).encode("utf-8")
        digest = hashlib.sha256(xml).hexdigest()
        sequence = 7
        evidence = {
            "predicate_kind": "node_present",
            "predicate_result": True,
            "matched_node_count": 1,
            "state_id": AUT_STATE,
            "observation_sequence": sequence,
            "observation_observed_at": 2.15,
            "observed_package": AUT,
            "observed_component": RETURN_COMPONENT,
            "resumed_activities": [RETURN_COMPONENT],
            "hierarchy_sha256": digest,
        }
        start_details = {
            **base_lifecycle[0].details,
            **episode.common_details(),
            "return_predicate": workflow.return_predicate.to_dict(),
        }
        step_details = {
            **base_lifecycle[1].details,
            **episode.common_details(),
        }
        success_details = {
            **base_lifecycle[2].details,
            **episode.common_details(),
            "predicate_evidence": evidence,
        }
        lifecycle = (
            _record(
                "node-start",
                base_lifecycle[0].observed_at,
                EPISODE_STARTED,
                LifecycleStatus.STARTED,
                start_details,
            ),
            _record(
                "node-step",
                base_lifecycle[1].observed_at,
                EPISODE_STEP,
                LifecycleStatus.SUCCEEDED,
                step_details,
            ),
            _record(
                "node-success",
                base_lifecycle[2].observed_at,
                EPISODE_SUCCEEDED,
                LifecycleStatus.SUCCEEDED,
                success_details,
            ),
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_directory = root / f"{sequence:08d}-{AUT_STATE[:12]}"
            artifact_directory.mkdir()
            hierarchy = artifact_directory / "hierarchy.xml"
            hierarchy.write_bytes(xml)
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=lifecycle,
                attempts=attempts,
                run_status="finished",
                observation_root=root,
            )

            hierarchy.write_bytes(xml + b"\n")
            with self.assertRaisesRegex(ValueError, "digest differs"):
                validate_foreign_workflow_lifecycle(
                    (workflow,),
                    aut_package=AUT,
                    lifecycle=lifecycle,
                    attempts=attempts,
                    run_status="finished",
                    observation_root=root,
                )
            hierarchy.write_bytes(xml)

            missing_digest_details = dict(success_details)
            missing_digest_evidence = dict(evidence)
            missing_digest_evidence["hierarchy_sha256"] = None
            missing_digest_details["predicate_evidence"] = missing_digest_evidence
            missing_digest = (
                lifecycle[0],
                lifecycle[1],
                _record(
                    "node-missing-digest",
                    base_lifecycle[2].observed_at,
                    EPISODE_SUCCEEDED,
                    LifecycleStatus.SUCCEEDED,
                    missing_digest_details,
                ),
            )
            with self.assertRaisesRegex(ValueError, "lacks a hierarchy digest"):
                validate_foreign_workflow_lifecycle(
                    (workflow,),
                    aut_package=AUT,
                    lifecycle=missing_digest,
                    attempts=attempts,
                    run_status="finished",
                    observation_root=root,
                )

    def test_replay_rejects_decreasing_elapsed_and_stale_success(self) -> None:
        workflow, attempts, lifecycle = _successful_lifecycle()
        terminal = lifecycle[2]
        decreasing_details = dict(terminal.details)
        decreasing_details["elapsed_seconds"] = 0.5
        decreasing = (
            lifecycle[0],
            lifecycle[1],
            _record(
                "l-success-decreasing",
                terminal.observed_at,
                EPISODE_SUCCEEDED,
                LifecycleStatus.SUCCEEDED,
                decreasing_details,
            ),
        )
        with self.assertRaisesRegex(ValueError, "terminal elapsed time"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=decreasing,
                attempts=attempts,
                run_status="finished",
            )

        stale_step_details = dict(lifecycle[1].details)
        stale_step_details["after_observed_at"] = 1.2
        stale_step_details["fresh_observation"] = False
        stale_terminal_details = dict(terminal.details)
        stale_evidence = dict(stale_terminal_details["predicate_evidence"])
        stale_evidence["observation_observed_at"] = 1.2
        stale_terminal_details["predicate_evidence"] = stale_evidence
        stale = (
            lifecycle[0],
            _record(
                "l-step-stale",
                lifecycle[1].observed_at,
                EPISODE_STEP,
                LifecycleStatus.SUCCEEDED,
                stale_step_details,
            ),
            _record(
                "l-success-stale",
                terminal.observed_at,
                EPISODE_SUCCEEDED,
                LifecycleStatus.SUCCEEDED,
                stale_terminal_details,
            ),
        )
        with self.assertRaisesRegex(ValueError, "mandatory terminal reason"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=stale,
                attempts=attempts,
                run_status="finished",
            )


class SummaryAccountingTest(unittest.TestCase):
    def test_summary_reports_outcomes_without_claiming_episode_coverage_gain(self) -> None:
        workflow, _attempts, lifecycle = _successful_lifecycle()
        runtime = RuntimeConfig.from_mapping(
            _runtime_values(foreign_workflows=[workflow.to_dict()])
        )

        class _Ledger:
            def payloads(self):
                return [{"event": record.to_dict()} for record in lifecycle]

        class _Store:
            @staticmethod
            def manifest():
                return {"runtime_configuration": runtime.to_dict()}

            @staticmethod
            def ledger(name: str):
                if name != "lifecycle":
                    raise AssertionError(name)
                return _Ledger()

        summary = _foreign_workflow_diagnostics(_Store())
        assert summary is not None
        self.assertEqual(summary["episodes_started"], 1)
        self.assertEqual(summary["episode_steps"], 1)
        self.assertEqual(summary["episodes_succeeded"], 1)
        self.assertEqual(summary["episodes_failed"], 0)
        self.assertNotIn("coverage_gain", summary)
        self.assertEqual(
            summary["coverage_accounting"],
            "unchanged_aut_universe_and_existing_provenance",
        )

class ForeignWorkflowHardeningRegressionTest(unittest.TestCase):
    def test_complete_entry_action_and_nested_contracts_are_immutable(self) -> None:
        workflow = _workflow()
        original_shape = workflow.to_dict()
        original_digest = workflow.workflow_sha256
        different_state = _action(_entry_selector(), state_id="different-state")
        self.assertNotEqual(different_state.stable_id, workflow.entry_action.stable_id)
        self.assertFalse(workflow.matches_entry(different_state))

        with self.assertRaises(TypeError):
            workflow.entry_action.parameters["expected_state_id"] = "tampered"
        with self.assertRaises(TypeError):
            workflow.entry_action.parameters["selector"]["text"] = "Tampered"
        with self.assertRaises(TypeError):
            workflow.return_predicate.parameters["package"] = OTHER

        # Exercise the inherited-dict hole directly: binding the nested mapping
        # to a local prevents an outer frozen assignment from masking __ior__.
        entry_parameters = workflow.entry_action.parameters
        selector = entry_parameters["selector"]
        predicate_parameters = workflow.return_predicate.parameters
        for target, patch in (
            (entry_parameters, {"expected_state_id": "tampered"}),
            (selector, {"text": "MUTATED"}),
            (predicate_parameters, {"package": OTHER}),
        ):
            with self.subTest(target=repr(target)):
                with self.assertRaises(TypeError):
                    target |= patch
        for target in (entry_parameters, selector, predicate_parameters):
            for method, arguments in (
                ("update", ({"x": "y"},)),
                ("setdefault", ("x", "y")),
                ("pop", (next(iter(target)),)),
                ("popitem", ()),
                ("clear", ()),
            ):
                with self.subTest(method=method, target=repr(target)):
                    with self.assertRaises((AttributeError, TypeError)):
                        getattr(target, method)(*arguments)

        self.assertEqual(workflow.to_dict(), original_shape)
        self.assertEqual(workflow.workflow_sha256, original_digest)

    def test_direct_construction_copies_source_mappings_before_freezing(self) -> None:
        selector = _entry_selector()
        entry_parameters = {
            "expected_state_id": ENTRY_STATE,
            "selector": selector,
        }
        predicate_parameters = {"package": AUT}
        workflow = ForeignWorkflow(
            workflow_id="direct-import",
            entry_action=ActionSpec(
                "tap",
                stable_hash(selector),
                entry_parameters,
            ),
            allowed_foreign_packages=(FOREIGN,),
            allowed_action_kinds=("tap",),
            max_actions=1,
            max_seconds=1.0,
            return_predicate=SetupPredicate(
                "package_foreground",
                predicate_parameters,
                1.0,
            ),
        )
        original_shape = workflow.to_dict()
        original_digest = workflow.workflow_sha256

        selector["text"] = "source changed"
        entry_parameters["expected_state_id"] = "source changed"
        predicate_parameters["package"] = OTHER

        self.assertEqual(workflow.to_dict(), original_shape)
        self.assertEqual(workflow.workflow_sha256, original_digest)

    def test_same_selector_from_a_different_state_cannot_start(self) -> None:
        workflow = _workflow()
        configured = _action(_entry_selector(), state_id=ENTRY_STATE)
        different = _action(_entry_selector(), state_id="different-state")
        now = time.time()
        self.assertIsNone(
            matching_started_workflow(
                (workflow,),
                aut_package=AUT,
                before=_observation("different-state", now - 2.0, ENTRY_COMPONENT),
                action=different,
                execution=_execution(different, now - 1.0, now - 0.5),
                after=_observation(FOREIGN_STATE, now, FOREIGN_COMPONENT),
            )
        )
        self.assertNotEqual(configured.stable_id, different.stable_id)

    def test_foreign_attempts_cannot_survive_deleted_episode_evidence(self) -> None:
        workflow, attempts, _lifecycle = _successful_lifecycle()
        with self.assertRaisesRegex(ValueError, "not bound"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=(),
                attempts=attempts,
                run_status="finished",
            )

    def test_ambiguous_foreground_evidence_preserves_all_resumed_components(self) -> None:
        workflow, attempts, lifecycle = _failed_lifecycle()
        failure_details = dict(lifecycle[1].details)
        ambiguous_components = sorted(
            [f"{FOREIGN}/.FilesActivity", f"{OTHER}/.GrantPermissionsActivity"]
        )
        failure_details.update(
            {
                "terminal_reason": "ambiguous_foreground",
                "observed_state_id": "ambiguous-state",
                "observed_package": None,
                "observed_component": None,
                "elapsed_seconds": 0.2,
                "predicate_evidence": {
                    **failure_details["predicate_evidence"],
                    "state_id": "ambiguous-state",
                    "observation_observed_at": 1.35,
                    "observed_package": None,
                    "observed_component": None,
                    "resumed_activities": ambiguous_components,
                },
            }
        )
        failure = _record(
            "l-ambiguous-failure",
            1.4,
            EPISODE_FAILED,
            LifecycleStatus.FAILED,
            failure_details,
            error="ambiguous_foreground",
        )
        containment_details = dict(lifecycle[2].details)
        containment_details.update(
            {
                "terminal_reason": "ambiguous_foreground",
                "before_state_id": "ambiguous-state",
                "before_package": None,
                "before_component": None,
                "last_dispatch_at": 1.45,
                "after_observed_at": 1.46,
            }
        )
        containment = _record(
            "l-ambiguous-containment",
            1.5,
            EPISODE_CONTAINMENT,
            LifecycleStatus.SUCCEEDED,
            containment_details,
        )
        validate_foreign_workflow_lifecycle(
            (workflow,),
            aut_package=AUT,
            lifecycle=(lifecycle[0], failure, containment),
            attempts=attempts,
            run_status="finished",
        )

    def test_node_evidence_rejects_a_symbolic_observation_root(self) -> None:
        workflow = _workflow(
            return_predicate={
                "kind": "node_absent",
                "parameters": {"selector": {"package": AUT, "text": "missing"}},
                "timeout_seconds": 1.0,
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real"
            real.mkdir()
            symbolic = root / "observations"
            symbolic.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                _artifact_predicate_match_count(
                    symbolic,
                    sequence=1,
                    state_id=AUT_STATE,
                    hierarchy_sha256="a" * 64,
                    predicate=workflow.return_predicate,
                )

    def test_unsubstantiated_run_bound_reason_is_rejected(self) -> None:
        workflow, attempts, lifecycle = _failed_lifecycle()
        details = dict(lifecycle[1].details)
        details.update(
            {
                "terminal_reason": "run_max_seconds",
                "elapsed_seconds": 0.2,
            }
        )
        failure = _record(
            "l-fake-run-bound",
            1.4,
            EPISODE_FAILED,
            LifecycleStatus.FAILED,
            details,
            error="run_max_seconds",
        )
        containment_details = dict(lifecycle[2].details)
        containment_details["terminal_reason"] = "run_max_seconds"
        containment = _record(
            "l-fake-run-bound-containment",
            1.5,
            EPISODE_CONTAINMENT,
            LifecycleStatus.SUCCEEDED,
            containment_details,
        )
        with self.assertRaisesRegex(
            ValueError, "details fields|not independently|run bound"
        ):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=(lifecycle[0], failure, containment),
                attempts=attempts,
                run_status="finished",
            )


class ForeignReturnPollingTest(unittest.TestCase):
    def test_return_predicate_timeout_polls_fresh_retained_observations(self) -> None:
        workflow = _workflow(
            return_predicate={
                "kind": "activity_resumed",
                "parameters": {"component": RETURN_COMPONENT},
                "timeout_seconds": 1.0,
            }
        )
        now = time.time()
        monotonic_now = time.monotonic()
        entry_action = _action(_entry_selector(), state_id=ENTRY_STATE)
        episode = ForeignWorkflowEpisode.start(
            workflow,
            entry_attempt_id="a-entry",
            entry_action_id=entry_action.stable_id,
            entry_candidate_id=ActionCandidate(ENTRY_STATE, entry_action).stable_id,
            resolved_component=FOREIGN_COMPONENT,
            after_state_id=FOREIGN_STATE,
            started_at=now - 1.0,
            started_monotonic=monotonic_now - 1.0,
        )
        initial = _observation(
            "returned-main", now - 0.2, f"{AUT}/.MainActivity"
        )
        satisfied = _observation(AUT_STATE, now + 0.1, RETURN_COMPONENT)

        class Observer:
            sequence = 8

            def __init__(self):
                self.latest = SimpleNamespace(observation=initial, nodes=())
                self.deadlines = []

            def capture(self, *, deadline_monotonic):
                self.deadlines.append(deadline_monotonic)
                self.latest = SimpleNamespace(observation=satisfied, nodes=())
                return self.latest

        runner = AndroidRunner.__new__(AndroidRunner)
        runner._activity_package_name = AUT
        runner._active_foreign_episode = episode
        runner._consecutive_foreign_actions = 0
        runner._exploration_started_at = None
        runner.observer = Observer()
        runner.runtime = SimpleNamespace(max_seconds=60.0)
        events = []
        runner._lifecycle = lambda phase, event, status, **kwargs: events.append(
            (phase, event, status, kwargs)
        )

        returned = runner._resolve_foreign_return(episode, initial)
        self.assertEqual(returned, satisfied)
        self.assertIsNone(runner._active_foreign_episode)
        self.assertEqual(len(runner.observer.deadlines), 1)
        self.assertEqual(events[-1][1], EPISODE_SUCCEEDED)

    def test_episode_operations_are_clamped_to_the_global_run_clock(self) -> None:
        workflow = _workflow(max_seconds=100.0)
        action = _action(_entry_selector(), state_id=ENTRY_STATE)
        started = time.monotonic()
        episode = ForeignWorkflowEpisode.start(
            workflow,
            entry_attempt_id="a-entry",
            entry_action_id=action.stable_id,
            entry_candidate_id=ActionCandidate(ENTRY_STATE, action).stable_id,
            resolved_component=FOREIGN_COMPONENT,
            after_state_id=FOREIGN_STATE,
            started_at=time.time(),
            started_monotonic=started,
        )
        runner = AndroidRunner.__new__(AndroidRunner)
        runner.runtime = SimpleNamespace(max_seconds=2.0)
        runner._exploration_started_at = started - 1.5
        deadline = runner._foreign_operation_deadline(episode)
        self.assertLess(deadline, episode.deadline_monotonic)
        self.assertEqual(
            runner._foreign_expired_reason(
                episode, runner._exploration_started_at + 2.0
            ),
            "run_max_seconds",
        )


class TransitionDurabilityRegressionTest(unittest.TestCase):
    def test_every_episode_transition_recovers_before_and_after_durable_append(self) -> None:
        episode_id = "fw-transition-regression"
        cases = (
            (EPISODE_STARTED, LifecycleStatus.STARTED, None, None),
            (EPISODE_STEP, LifecycleStatus.SUCCEEDED, None, 1),
            (EPISODE_SUCCEEDED, LifecycleStatus.SUCCEEDED, None, None),
            (EPISODE_FAILED, LifecycleStatus.FAILED, "failed", None),
            (EPISODE_CONTAINMENT, LifecycleStatus.SUCCEEDED, None, None),
        )
        for event, status, error, ordinal in cases:
            for interrupt_after_append in (False, True):
                with self.subTest(
                    event=event, interrupt_after_append=interrupt_after_append
                ):
                    runner = AndroidRunner.__new__(AndroidRunner)
                    runner._pending_foreign_transition = None
                    retained: dict[str, LifecycleRecord] = {}
                    target_id = episode_lifecycle_record_id(
                        event, episode_id, step_ordinal=ordinal
                    )
                    interrupted = False
                    apply_count = 0

                    def lifecycle(
                        phase,
                        emitted_event,
                        emitted_status,
                        *,
                        details=None,
                        error=None,
                        observed_at=None,
                        record_id=None,
                        idempotent=False,
                    ):
                        nonlocal interrupted
                        assert record_id is not None
                        record = LifecycleRecord(
                            record_id=record_id,
                            observed_at=float(observed_at),
                            phase=phase,
                            event=emitted_event,
                            status=emitted_status,
                            details=dict(details or {}),
                            error=error,
                        )
                        existing = retained.get(record_id)
                        if existing is not None:
                            self.assertEqual(existing.to_dict(), record.to_dict())
                            return
                        is_target = record_id == target_id
                        if is_target and not interrupted and not interrupt_after_append:
                            interrupted = True
                            raise KeyboardInterrupt("before durable append")
                        retained[record_id] = record
                        if is_target and not interrupted and interrupt_after_append:
                            interrupted = True
                            raise KeyboardInterrupt("after durable append")

                    def apply() -> None:
                        nonlocal apply_count
                        apply_count += 1

                    runner._lifecycle = lifecycle
                    with self.assertRaises(KeyboardInterrupt):
                        runner._publish_foreign_transition(
                            record_id=target_id,
                            event=event,
                            status=status,
                            details={"episode_id": episode_id, "ordinal": ordinal},
                            error=error,
                            apply=apply,
                        )
                    self.assertEqual(apply_count, 0)
                    self.assertIsNotNone(runner._pending_foreign_transition)

                    runner._resume_foreign_transition()

                    self.assertEqual(apply_count, 1)
                    self.assertIsNone(runner._pending_foreign_transition)
                    self.assertIn(target_id, retained)
                    self.assertIn(
                        foreign_transition_intent_record_id(target_id), retained
                    )
                    self.assertEqual(
                        sum(record_id == target_id for record_id in retained), 1
                    )

    def test_validator_rejects_arbitrary_ids_for_every_episode_event(self) -> None:
        successful = _successful_lifecycle()
        failed = _failed_lifecycle()
        for workflow, attempts, lifecycle in (successful, failed):
            for index, record in enumerate(lifecycle):
                tampered = list(lifecycle)
                tampered[index] = LifecycleRecord(
                    record_id=f"l-arbitrary-{index}",
                    observed_at=record.observed_at,
                    phase=record.phase,
                    event=record.event,
                    status=record.status,
                    details=record.details,
                    error=record.error,
                )
                with self.subTest(event=record.event):
                    with self.assertRaisesRegex(ValueError, "record ID"):
                        validate_foreign_workflow_lifecycle(
                            (workflow,),
                            aut_package=AUT,
                            lifecycle=tuple(tampered),
                            attempts=attempts,
                            run_status="finished",
                        )


class IndependentTerminalEvidenceRegressionTest(unittest.TestCase):
    @staticmethod
    def _terminal(observed_at: float, reason: str) -> LifecycleRecord:
        return LifecycleRecord(
            record_id=f"l-run-{reason}",
            observed_at=observed_at,
            phase="terminal",
            event="run_finished",
            status=LifecycleStatus.SUCCEEDED,
            details={"reason": reason},
        )

    @staticmethod
    def _budget_start(
        *, observed_at: float, max_seconds: float, max_actions: int
    ) -> LifecycleRecord:
        return LifecycleRecord(
            record_id=RUN_BUDGET_START_RECORD_ID,
            observed_at=observed_at,
            phase="exploration",
            event=RUN_BUDGET_STARTED,
            status=LifecycleStatus.STARTED,
            details={
                "started_at": observed_at,
                "max_seconds": max_seconds,
                "max_actions": max_actions,
                "action_count": 0,
            },
        )

    @staticmethod
    def _failure_with_reason(
        record: LifecycleRecord,
        *,
        reason: str,
        extra: dict[str, object],
    ) -> LifecycleRecord:
        details = dict(record.details)
        details.update({"terminal_reason": reason, **extra})
        return _record(
            "ignored",
            record.observed_at,
            EPISODE_FAILED,
            LifecycleStatus.FAILED,
            details,
            error=reason,
        )

    def test_run_max_seconds_requires_retained_start_and_elapsed_boundary(self) -> None:
        workflow, attempts, base = _failed_lifecycle()
        boundary_id = run_boundary_record_id("run_max_seconds")
        failure = self._failure_with_reason(
            base[1],
            reason="run_max_seconds",
            extra={"run_boundary_record_id": boundary_id},
        )
        containment_details = dict(base[2].details)
        containment_details["terminal_reason"] = "run_max_seconds"
        containment = _record(
            "ignored",
            1.5,
            EPISODE_CONTAINMENT,
            LifecycleStatus.SUCCEEDED,
            containment_details,
        )
        start = self._budget_start(
            observed_at=0.2, max_seconds=1.0, max_actions=20
        )
        boundary = LifecycleRecord(
            record_id=boundary_id,
            observed_at=1.4,
            phase="exploration",
            event=RUN_BUDGET_BOUNDARY,
            status=LifecycleStatus.INFO,
            details={
                "reason": "run_max_seconds",
                "reached_at": 1.4,
                "elapsed_seconds": 1.2,
                "action_count": 1,
                "last_dispatch_id": None,
                "last_dispatch_sequence": None,
            },
        )
        lifecycle = (
            start,
            base[0],
            boundary,
            failure,
            containment,
            self._terminal(1.6, "max_seconds"),
        )
        validate_foreign_workflow_lifecycle(
            (workflow,),
            aut_package=AUT,
            lifecycle=lifecycle,
            attempts=attempts,
            run_status="finished",
            run_max_actions=20,
            run_max_seconds=1.0,
        )

        tampered_boundary = LifecycleRecord(
            record_id=boundary.record_id,
            observed_at=boundary.observed_at,
            phase=boundary.phase,
            event=boundary.event,
            status=boundary.status,
            details={**boundary.details, "elapsed_seconds": 0.2},
        )
        with self.assertRaisesRegex(ValueError, "not independently proven"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=(
                    start,
                    base[0],
                    tampered_boundary,
                    failure,
                    containment,
                    self._terminal(1.6, "max_seconds"),
                ),
                attempts=attempts,
                run_status="finished",
                run_max_actions=20,
                run_max_seconds=1.0,
            )

    def test_run_max_actions_binds_exact_final_dispatch_boundary(self) -> None:
        workflow, attempts, base = _failed_lifecycle()
        entry = attempts[0]
        reservation = ActionDispatchReservation.create(
            dispatch_sequence=1,
            state_id=entry.state_id,
            before_activity=ENTRY_COMPONENT,
            provenance=Provenance.GUI,
            requested=entry.requested,
            route_id=None,
            reserved_at=0.9,
            expected_attempt_sequence=1,
        )
        intent = LifecycleRecord(
            record_id=reservation.intent_record_id,
            observed_at=reservation.reserved_at,
            phase="exploration",
            event=DISPATCH_INTENT,
            status=LifecycleStatus.STARTED,
            details=reservation.intent_details(),
        )
        finalized_at = 1.15
        final = LifecycleRecord(
            record_id=reservation.final_record_id,
            observed_at=finalized_at,
            phase="exploration",
            event=DISPATCH_FINALIZED,
            status=LifecycleStatus.SUCCEEDED,
            details=finalization_details(
                reservation,
                finalized_at=finalized_at,
                disposition="committed",
                execution=entry.execution,
                attempt=entry,
            ),
        )
        boundary_id = run_boundary_record_id("run_max_actions")
        failure = self._failure_with_reason(
            base[1],
            reason="run_max_actions",
            extra={"run_boundary_record_id": boundary_id},
        )
        containment_details = dict(base[2].details)
        containment_details["terminal_reason"] = "run_max_actions"
        containment = _record(
            "ignored",
            1.5,
            EPISODE_CONTAINMENT,
            LifecycleStatus.SUCCEEDED,
            containment_details,
        )
        start = self._budget_start(
            observed_at=0.8, max_seconds=60.0, max_actions=1
        )
        boundary = LifecycleRecord(
            record_id=boundary_id,
            observed_at=1.35,
            phase="exploration",
            event=RUN_BUDGET_BOUNDARY,
            status=LifecycleStatus.INFO,
            details={
                "reason": "run_max_actions",
                "reached_at": 1.35,
                "elapsed_seconds": 0.55,
                "action_count": 1,
                "last_dispatch_id": reservation.dispatch_id,
                "last_dispatch_sequence": 1,
            },
        )
        lifecycle = (
            start,
            intent,
            final,
            base[0],
            boundary,
            failure,
            containment,
            self._terminal(1.6, "max_actions"),
        )
        validate_foreign_workflow_lifecycle(
            (workflow,),
            aut_package=AUT,
            lifecycle=lifecycle,
            attempts=attempts,
            run_status="finished",
            run_max_actions=1,
            run_max_seconds=60.0,
        )

        tampered = LifecycleRecord(
            record_id=boundary.record_id,
            observed_at=boundary.observed_at,
            phase=boundary.phase,
            event=boundary.event,
            status=boundary.status,
            details={**boundary.details, "last_dispatch_id": "d-unrelated"},
        )
        with self.assertRaisesRegex(ValueError, "not independently proven"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=(
                    start,
                    intent,
                    final,
                    base[0],
                    tampered,
                    failure,
                    containment,
                    self._terminal(1.6, "max_actions"),
                ),
                attempts=attempts,
                run_status="finished",
                run_max_actions=1,
                run_max_seconds=60.0,
            )

    @staticmethod
    def _crash_text(device_started_at: float) -> str:
        return "\n".join(
            (
                f"{device_started_at:.2f} 123 123 E AndroidRuntime: FATAL EXCEPTION: main",
                f"{device_started_at + 0.01:.2f} 123 123 E AndroidRuntime: "
                f"Process: {AUT}, PID: 123",
                f"{device_started_at + 0.02:.2f} 123 123 E AndroidRuntime: "
                "java.lang.RuntimeException: boom",
                f"{device_started_at + 0.03:.2f} 123 123 E AndroidRuntime: "
                f"at {AUT}.Foo.bar(Foo.java:1)",
            )
        )

    def test_owned_crash_must_be_timestamped_inside_the_episode(self) -> None:
        workflow, attempts, base = _failed_lifecycle()
        # The device fatal block falls inside the episode's own host interval,
        # which is the only thing that makes it this episode's crash.
        crash_text = self._crash_text(1.30)
        crash = parse_app_crashes(
            crash_text, package=AUT, observed_at=1.35
        )[0]
        failure = self._failure_with_reason(
            base[1],
            reason="owned_crash_during_episode",
            extra={"crash_id": crash.crash_id},
        )
        containment_details = dict(base[2].details)
        containment_details["terminal_reason"] = "owned_crash_during_episode"
        containment = _record(
            "ignored",
            1.5,
            EPISODE_CONTAINMENT,
            LifecycleStatus.SUCCEEDED,
            containment_details,
        )
        lifecycle = (base[0], failure, containment)
        validate_foreign_workflow_lifecycle(
            (workflow,),
            aut_package=AUT,
            lifecycle=lifecycle,
            attempts=attempts,
            run_status="finished",
            crashes=(crash,),
        )

        with self.assertRaisesRegex(ValueError, "in-episode crash evidence"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=lifecycle,
                attempts=attempts,
                run_status="finished",
                owned_crash_count=1,
            )
        early_crash = parse_app_crashes(
            crash_text, package=AUT, observed_at=1.1
        )[0]
        with self.assertRaisesRegex(ValueError, "in-episode crash evidence"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=lifecycle,
                attempts=attempts,
                run_status="finished",
                crashes=(early_crash,),
            )
        # Ingestion inside the episode cannot launder a fatal whose retained
        # device timestamps lie far outside the permitted skew.
        skewed_crash = parse_app_crashes(
            self._crash_text(100.0), package=AUT, observed_at=1.35
        )[0]
        self.assertIsNotNone(skewed_crash.device_started_at)
        with self.assertRaisesRegex(ValueError, "in-episode crash evidence"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=lifecycle,
                attempts=attempts,
                run_status="finished",
                crashes=(skewed_crash,),
            )

    def test_containment_accepts_exact_boundary_and_rejects_late_completion(self) -> None:
        workflow, attempts, base = _failed_lifecycle()
        exact_details = {
            **base[2].details,
            "containment_started_at": 1.4,
            "containment_completed_at": 1.5,
            "containment_elapsed_seconds": 0.1,
            "containment_timeout_seconds": 0.1,
        }
        exact = _record(
            "ignored",
            1.5,
            EPISODE_CONTAINMENT,
            LifecycleStatus.SUCCEEDED,
            exact_details,
        )
        validate_foreign_workflow_lifecycle(
            (workflow,),
            aut_package=AUT,
            lifecycle=(base[0], base[1], exact),
            attempts=attempts,
            run_status="finished",
        )

        late_details = {
            **exact_details,
            "containment_completed_at": 1.51,
            "containment_elapsed_seconds": 0.11,
        }
        late = _record(
            "ignored",
            1.51,
            EPISODE_CONTAINMENT,
            LifecycleStatus.SUCCEEDED,
            late_details,
        )
        with self.assertRaisesRegex(ValueError, "containment result is invalid"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=(base[0], base[1], late),
                attempts=attempts,
                run_status="finished",
            )


class IncompleteForeignDispatchAccountingRegressionTest(unittest.TestCase):
    def test_executed_incomplete_dispatch_consumes_episode_bound_once(self) -> None:
        workflow, attempts, base = _failed_lifecycle()
        action = _action(_foreign_selector(), state_id=FOREIGN_STATE)
        reservation = ActionDispatchReservation.create(
            dispatch_sequence=1,
            state_id=FOREIGN_STATE,
            before_activity=FOREIGN_COMPONENT,
            provenance=Provenance.GUI,
            requested=action,
            route_id=None,
            reserved_at=1.31,
            expected_attempt_sequence=2,
        )
        execution = _execution(action, 1.32, 1.33)
        intent = LifecycleRecord(
            record_id=reservation.intent_record_id,
            observed_at=reservation.reserved_at,
            phase="exploration",
            event=DISPATCH_INTENT,
            status=LifecycleStatus.STARTED,
            details=reservation.intent_details(),
        )
        disposition = "post_action_verifier_failed"
        final = LifecycleRecord(
            record_id=reservation.final_record_id,
            observed_at=1.34,
            phase="exploration",
            event=DISPATCH_FINALIZED,
            status=LifecycleStatus.FAILED,
            details=finalization_details(
                reservation,
                finalized_at=1.34,
                disposition=disposition,
                execution=execution,
                attempt=None,
                failure_stage="verifier",
            ),
            error=disposition,
        )
        failure_details = {
            **base[1].details,
            "actions_executed": 1,
            "terminal_reason": "post_action_evidence_incomplete",
            "dispatch_id": reservation.dispatch_id,
        }
        failure = _record(
            "ignored",
            1.4,
            EPISODE_FAILED,
            LifecycleStatus.FAILED,
            failure_details,
            error="post_action_evidence_incomplete",
        )
        containment_details = {
            **base[2].details,
            "actions_executed": 1,
            "terminal_reason": "post_action_evidence_incomplete",
        }
        containment = _record(
            "ignored",
            1.5,
            EPISODE_CONTAINMENT,
            LifecycleStatus.SUCCEEDED,
            containment_details,
        )
        lifecycle = (base[0], intent, final, failure, containment)
        validate_foreign_workflow_lifecycle(
            (workflow,),
            aut_package=AUT,
            lifecycle=lifecycle,
            attempts=attempts,
            run_status="finished",
        )

        understated = _record(
            "ignored",
            1.4,
            EPISODE_FAILED,
            LifecycleStatus.FAILED,
            {**failure_details, "actions_executed": 0},
            error="post_action_evidence_incomplete",
        )
        with self.assertRaisesRegex(ValueError, "action count"):
            validate_foreign_workflow_lifecycle(
                (workflow,),
                aut_package=AUT,
                lifecycle=(base[0], intent, final, understated, containment),
                attempts=attempts,
                run_status="finished",
            )


if __name__ == "__main__":
    unittest.main()
