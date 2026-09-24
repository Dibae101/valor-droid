"""Frontier eligibility, cross-screen navigation, and per-field input values.

These cover the three deterministic mechanisms that decide what the explorer
does next, which had no test coverage at all before:
- gap 1: a repeated (state, action) pair must lose to an untried one.
- gap 2: a screen with no local work must be able to route to another screen.
- gap 4: a typed value must fit its field and be re-derivable from evidence.
"""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
import unittest
from pathlib import Path

from valordroid.config import CoreConfig
from valordroid.field_values import (
    DETERMINISTIC_VALUE_RULE,
    SAFE_VALUE,
    deterministic_field_value,
    expected_deterministic_value,
    generate_field_value,
    infer_field_kind,
    validate_candidate_value,
)
from valordroid.frontier import FrontierStore
from valordroid.input_validation import FieldKind
from valordroid.models import (
    ActionAttempt,
    ActionCandidate,
    ActionSpec,
    ExecutionRecord,
    ExecutionStatus,
    OutcomeKind,
    OutcomeRecord,
    Provenance,
)
from valordroid.models import coarse_state_key
from valordroid.navigation import (
    COARSE_REBIND_NAVIGATION_RULE,
    NAVIGATION_RULE,
    StateGraph,
    StateTransition,
    plan_navigation_step,
    untried_local_candidate,
    worthwhile_states,
)


def _action(name: str, state_id: str) -> ActionSpec:
    return ActionSpec(
        "tap",
        name,
        {"expected_state_id": state_id, "selector": {"resource_id": name}},
    )


def _attempt(
    sequence: int,
    state_id: str,
    action: ActionSpec,
    *,
    after_state_id: str,
    units: tuple[str, ...] = (),
    status: ExecutionStatus = ExecutionStatus.EXECUTED,
    route_id: str | None = None,
    coarse: tuple[str, str] | None = None,
    after_coarse: tuple[str, str] | None = None,
) -> ActionAttempt:
    changed = after_state_id != state_id
    if status is ExecutionStatus.EXECUTED:
        kind = OutcomeKind.EFFECT if changed else OutcomeKind.NO_EFFECT
    elif status is ExecutionStatus.FAILED:
        kind = OutcomeKind.FAILED
    else:
        kind = OutcomeKind.NOT_EXECUTED
    details = {"before_state_id": state_id, "after_state_id": after_state_id}
    if coarse is not None:
        details["before_activity"], details["before_structure_sha256"] = coarse
    if after_coarse is not None:
        details["after_activity"], details["after_structure_sha256"] = after_coarse
    return ActionAttempt(
        attempt_id=f"a-{sequence:08d}",
        sequence=sequence,
        state_id=state_id,
        provenance=Provenance.GUI,
        requested=action,
        execution=ExecutionRecord(
            status=status,
            started_at=float(sequence),
            ended_at=float(sequence) + 0.5,
            action=action if status is not ExecutionStatus.NOT_EXECUTED else None,
            error=None if status is ExecutionStatus.EXECUTED else "boom",
        ),
        outcome=OutcomeRecord(kind, state_changed=changed, details=details),
        window_started_at=float(sequence),
        window_ended_at=float(sequence) + 2.5,
        associated_unit_ids=units,
        route_id=route_id,
    )


class FrontierEligibilityTest(unittest.TestCase):
    """Gap 1: suppression must be earned by evidence, and bounded."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.config = CoreConfig(
            retry_cooldown_seconds=60.0,
            initial_no_yield_attempts=2,
            productive_yield_floor=0.05,
        )

    def _store(self) -> FrontierStore:
        return FrontierStore(self.root / "frontier.json", self.config)

    def test_untried_candidate_outranks_every_attempted_one(self) -> None:
        store = self._store()
        tried = _action("tried", "home")
        untried = _action("untried", "home")
        store.reconcile((("h1", _attempt(1, "home", tried, after_state_id="home")),))
        ranked = store.rank(
            [ActionCandidate("home", untried), ActionCandidate("home", tried)],
            now=100.0,
        )
        self.assertEqual(ranked[0].candidate.action.target_id, "untried")
        self.assertEqual(ranked[0].reason, "untried")

    def test_no_effect_and_no_coverage_becomes_ineligible_after_the_bound(self) -> None:
        store = self._store()
        action = _action("dead", "home")
        history = tuple(
            (f"h{index}", _attempt(index, "home", action, after_state_id="home"))
            for index in range(1, 3)
        )
        store.reconcile(history)
        ranked = store.rank([ActionCandidate("home", action)], now=3.0)
        self.assertFalse(ranked[0].eligible)
        self.assertEqual(ranked[0].reason, "cooling down, not permanently removed")

    def test_suppression_expires_so_a_stateful_action_is_retried(self) -> None:
        store = self._store()
        action = _action("later", "home")
        history = tuple(
            (f"h{index}", _attempt(index, "home", action, after_state_id="home"))
            for index in range(1, 3)
        )
        store.reconcile(history)
        ranked = store.rank([ActionCandidate("home", action)], now=1000.0)
        self.assertTrue(ranked[0].eligible)
        self.assertEqual(ranked[0].reason, "cooldown expired")

    def test_coverage_producing_action_stays_eligible_immediately(self) -> None:
        store = self._store()
        action = _action("productive", "home")
        store.reconcile(
            (
                (
                    "h1",
                    _attempt(1, "home", action, after_state_id="detail", units=("u1", "u2")),
                ),
            )
        )
        ranked = store.rank([ActionCandidate("home", action)], now=1.6)
        self.assertTrue(ranked[0].eligible)
        # This action both produced coverage and moved to an unseen screen, and
        # the discovery is the more useful fact about it.
        self.assertEqual(ranked[0].reason, "opens new screens")

    def test_entry_verdict_answers_for_off_screen_candidates(self) -> None:
        store = self._store()
        action = _action("elsewhere", "settings")
        candidate_id = ActionCandidate("settings", action).stable_id
        store.reconcile(
            (
                (
                    "h1",
                    _attempt(1, "settings", action, after_state_id="detail", units=("u1",)),
                ),
            )
        )
        verdict = store.entry_verdict(candidate_id, now=2.0)
        self.assertIsNotNone(verdict)
        eligible, _score, reason = verdict
        self.assertTrue(eligible)
        self.assertEqual(reason, "opens new screens")
        self.assertIsNone(store.entry_verdict("f" * 64, now=2.0))


class StateGraphTest(unittest.TestCase):
    """Gap 2: the run must be able to reason about screens it is not on."""

    def test_only_effective_executed_gui_actions_become_edges(self) -> None:
        walk = _action("open", "home")
        graph = StateGraph.from_attempts(
            [
                _attempt(1, "home", walk, after_state_id="detail"),
                _attempt(2, "home", _action("noop", "home"), after_state_id="home"),
                _attempt(
                    3,
                    "home",
                    _action("broken", "home"),
                    after_state_id="detail",
                    status=ExecutionStatus.FAILED,
                ),
                _attempt(
                    4,
                    "home",
                    _action("routed", "home"),
                    after_state_id="other",
                    route_id="r-1",
                ),
            ]
        )
        self.assertEqual(graph.states, ("detail", "home"))
        self.assertEqual(len(graph.outgoing("home")), 1)
        self.assertEqual(graph.outgoing("home")[0].target_state_id, "detail")

    def test_duplicate_edges_are_collapsed(self) -> None:
        walk = _action("open", "home")
        graph = StateGraph.from_attempts(
            [
                _attempt(1, "home", walk, after_state_id="detail"),
                _attempt(2, "home", walk, after_state_id="detail"),
            ]
        )
        self.assertEqual(len(graph.outgoing("home")), 1)

    def test_shortest_path_respects_the_depth_bound(self) -> None:
        graph = StateGraph(
            [
                StateTransition("a", "b", _action("ab", "a"), "a-1", 0),
                StateTransition("b", "c", _action("bc", "b"), "a-2", 0),
                StateTransition("c", "d", _action("cd", "c"), "a-3", 0),
            ]
        )
        self.assertEqual(len(graph.shortest_path("a", ["d"], max_depth=3) or ()), 3)
        self.assertIsNone(graph.shortest_path("a", ["d"], max_depth=2))

    def test_path_to_an_unreachable_state_is_none(self) -> None:
        graph = StateGraph([StateTransition("a", "b", _action("ab", "a"), "a-1", 0)])
        self.assertIsNone(graph.shortest_path("a", ["z"], max_depth=4))

    def test_current_state_is_never_its_own_destination(self) -> None:
        graph = StateGraph([StateTransition("a", "b", _action("ab", "a"), "a-1", 0)])
        self.assertIsNone(graph.shortest_path("a", ["a"], max_depth=4))


class NavigationPlanTest(unittest.TestCase):
    def test_plan_returns_the_first_hop_toward_a_worthwhile_screen(self) -> None:
        graph = StateGraph(
            [
                StateTransition("home", "middle", _action("hm", "home"), "a-1", 0),
                StateTransition("middle", "goal", _action("mg", "middle"), "a-2", 0),
            ]
        )
        step = plan_navigation_step(
            graph,
            current_state_id="home",
            destinations={"goal": "c" * 64},
            max_depth=4,
        )
        assert step is not None
        self.assertEqual(step.action.target_id, "hm")
        self.assertEqual(step.immediate_state_id, "middle")
        self.assertEqual(step.destination_state_id, "goal")
        self.assertEqual(step.remaining_steps, 1)
        self.assertEqual(step.rule, NAVIGATION_RULE)

    def test_no_destination_means_no_plan(self) -> None:
        graph = StateGraph([StateTransition("home", "goal", _action("hg", "home"), "a", 0)])
        self.assertIsNone(
            plan_navigation_step(
                graph, current_state_id="home", destinations={}, max_depth=4
            )
        )

    def test_edge_bound_to_a_different_state_is_refused(self) -> None:
        # The recorded action names another observed state, so replaying it here
        # would misreport what is being repeated.
        graph = StateGraph(
            [StateTransition("home", "goal", _action("stale", "elsewhere"), "a", 0)]
        )
        self.assertIsNone(
            plan_navigation_step(
                graph,
                current_state_id="home",
                destinations={"goal": "c" * 64},
                max_depth=4,
            )
        )

    def test_a_plan_needs_the_edge_to_be_reachable_at_all(self) -> None:
        # Without a coarse identity, a state the graph has never seen has no
        # outgoing edges, and there is nothing to plan. This is the behaviour a
        # refinement split produces if the coarse key is not carried.
        graph = StateGraph(
            [StateTransition("home", "goal", _action("hg", "home"), "a", 0)]
        )
        self.assertIsNone(
            plan_navigation_step(
                graph,
                current_state_id="home-refined",
                destinations={"goal": "c" * 64},
                max_depth=4,
            )
        )

    def test_worthwhile_states_uses_the_frontier_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            config = CoreConfig()
            store = FrontierStore(Path(name) / "frontier.json", config)
            productive = _action("gold", "goal")
            dead = _action("dead", "dull")
            store.reconcile(
                (
                    (
                        "h1",
                        _attempt(1, "goal", productive, after_state_id="x", units=("u1",)),
                    ),
                    ("h2", _attempt(2, "dull", dead, after_state_id="dull")),
                    ("h3", _attempt(3, "dull", dead, after_state_id="dull")),
                )
            )
            destinations = worthwhile_states(
                store.entries(),
                lambda candidate_id: store.entry_verdict(candidate_id, now=4.0),
            )
        self.assertEqual(list(destinations), ["goal"])

    def test_untried_local_candidate_prefers_the_first_untried(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            store = FrontierStore(Path(name) / "frontier.json", CoreConfig())
            tried = _action("tried", "home")
            fresh = _action("fresh", "home")
            store.reconcile((("h1", _attempt(1, "home", tried, after_state_id="home")),))
            ranked = store.rank(
                [ActionCandidate("home", tried), ActionCandidate("home", fresh)],
                now=5.0,
            )
        candidate = untried_local_candidate(ranked)
        assert candidate is not None
        self.assertEqual(candidate.action.target_id, "fresh")

    def test_no_untried_candidate_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            store = FrontierStore(Path(name) / "frontier.json", CoreConfig())
            tried = _action("tried", "home")
            store.reconcile((("h1", _attempt(1, "home", tried, after_state_id="home")),))
            ranked = store.rank([ActionCandidate("home", tried)], now=5.0)
        self.assertIsNone(untried_local_candidate(ranked))


HOME_ACTIVITY = "com.example.app/com.example.app.MainActivity"
HOME_STRUCTURE = "a" * 64
SETTINGS_ACTIVITY = "com.example.app/com.example.app.SettingsActivity"
SETTINGS_STRUCTURE = "b" * 64
HOME_COARSE = (HOME_ACTIVITY, HOME_STRUCTURE)
SETTINGS_COARSE = (SETTINGS_ACTIVITY, SETTINGS_STRUCTURE)


class ZeroGainEdgeSurvivesRefinementTest(unittest.TestCase):
    """Gap 3.2: a split screen must not strand the routes recorded on it.

    Semantic refinement mints a new state ID for a screen the graph already knows.
    Every edge recorded on that screen names the pre-split ID, so a strict
    state-ID match finds no outgoing edges at all and the run loses its recorded
    routes -- including the zero-gain ones, which are exactly the doors that pay
    off later rather than immediately. The fixture below is that case: an edge
    that has never earned a single unit is the only way to reach a screen holding
    untried work.
    """

    def setUp(self) -> None:
        self.walk = _action("open_settings", "home")
        # Zero associated units, twice: this edge has never paid, and the
        # frontier's immediate-reward view of it is exactly as bleak as the
        # audit describes. Reachability is a separate question.
        self.graph = StateGraph.from_attempts(
            [
                _attempt(
                    index,
                    "home",
                    self.walk,
                    after_state_id="settings",
                    units=(),
                    coarse=HOME_COARSE,
                    after_coarse=SETTINGS_COARSE,
                )
                for index in (1, 2)
            ]
        )
        # The feature behind the door: a candidate on `settings` nobody has tried.
        self.destinations = {"settings": "c" * 64}
        self.home_coarse = coarse_state_key(*HOME_COARSE)

    def _plan(self, current: str, coarse: str | None):
        return plan_navigation_step(
            self.graph,
            current_state_id=current,
            destinations=self.destinations,
            max_depth=4,
            current_coarse_key=coarse,
        )

    def test_the_edge_carries_both_identities(self) -> None:
        edge = self.graph.outgoing("home")[0]
        self.assertEqual(edge.associated_units, 0)
        self.assertEqual(edge.source_coarse_key, self.home_coarse)
        self.assertEqual(edge.target_coarse_key, coarse_state_key(*SETTINGS_COARSE))
        self.assertEqual(self.graph.coarse_sources(self.home_coarse), ("home",))

    def test_before_a_split_the_exact_rule_is_used(self) -> None:
        step = self._plan("home", self.home_coarse)
        assert step is not None
        self.assertEqual(step.rule, NAVIGATION_RULE)
        self.assertEqual(step.destination_state_id, "settings")

    def test_after_a_split_the_zero_gain_edge_is_still_traversable(self) -> None:
        step = self._plan("home-refined", self.home_coarse)
        assert step is not None
        self.assertEqual(step.action.target_id, "open_settings")
        self.assertEqual(step.immediate_state_id, "settings")
        self.assertEqual(step.destination_state_id, "settings")
        self.assertEqual(step.remaining_steps, 0)
        self.assertEqual(step.motivating_candidate_id, "c" * 64)
        # A weaker claim than an exact state match, and it says so.
        self.assertEqual(step.rule, COARSE_REBIND_NAVIGATION_RULE)

    def test_a_multi_hop_path_survives_a_split_at_its_source(self) -> None:
        graph = StateGraph.from_attempts(
            [
                _attempt(
                    1,
                    "home",
                    self.walk,
                    after_state_id="settings",
                    units=(),
                    coarse=HOME_COARSE,
                    after_coarse=SETTINGS_COARSE,
                ),
                _attempt(
                    2,
                    "settings",
                    _action("open_about", "settings"),
                    after_state_id="about",
                    units=(),
                    coarse=SETTINGS_COARSE,
                    after_coarse=("com.example.app/.AboutActivity", "d" * 64),
                ),
            ]
        )
        step = plan_navigation_step(
            graph,
            current_state_id="home-refined",
            destinations={"about": "c" * 64},
            max_depth=4,
            current_coarse_key=self.home_coarse,
        )
        assert step is not None
        self.assertEqual(step.action.target_id, "open_settings")
        self.assertEqual(step.destination_state_id, "about")
        self.assertEqual(step.remaining_steps, 1)
        self.assertEqual(step.rule, COARSE_REBIND_NAVIGATION_RULE)

    def test_without_a_coarse_identity_the_split_still_strands_the_edge(self) -> None:
        # The original guard is untouched: re-binding happens only when the caller
        # supplies the coarse identity that makes it honest.
        self.assertIsNone(self._plan("home-refined", None))

    def test_another_screen_cannot_borrow_the_edge(self) -> None:
        # A different coarse identity is a different physical screen, and the
        # recorded selector says nothing about it.
        self.assertIsNone(
            self._plan("home-refined", coarse_state_key(*SETTINGS_COARSE))
        )

    def test_an_edge_not_bound_to_its_own_source_is_still_refused(self) -> None:
        # Re-binding relaxes "the device is in the state the action names" to
        # "the device is on the screen that state belonged to". It never accepts
        # an action that was not bound to its recorded source in the first place.
        graph = StateGraph.from_attempts(
            [
                _attempt(
                    1,
                    "home",
                    _action("stale", "elsewhere"),
                    after_state_id="settings",
                    coarse=HOME_COARSE,
                    after_coarse=SETTINGS_COARSE,
                )
            ]
        )
        self.assertIsNone(
            plan_navigation_step(
                graph,
                current_state_id="home-refined",
                destinations=self.destinations,
                max_depth=4,
                current_coarse_key=self.home_coarse,
            )
        )

    def test_a_legacy_edge_without_coarse_evidence_never_rebinds(self) -> None:
        # An attempt recorded before the digests travelled with the outcome has no
        # coarse identity, so it is simply not eligible rather than assumed.
        graph = StateGraph.from_attempts(
            [_attempt(1, "home", self.walk, after_state_id="settings")]
        )
        self.assertIsNone(
            plan_navigation_step(
                graph,
                current_state_id="home-refined",
                destinations=self.destinations,
                max_depth=4,
                current_coarse_key=self.home_coarse,
            )
        )

    def test_the_frontier_view_of_the_edge_is_left_alone(self) -> None:
        # Retirement still belongs to the frontier and still depends on the
        # candidate's own cumulative attribution: nothing here revives a
        # genuinely ineffective action, it only keeps a recorded route usable.
        with tempfile.TemporaryDirectory() as name:
            store = FrontierStore(
                Path(name) / "frontier.json",
                CoreConfig(initial_no_yield_attempts=1, retry_cooldown_seconds=60.0),
            )
            dead = _action("dead", "home")
            store.reconcile(
                tuple(
                    (f"h{index}", _attempt(index, "home", dead, after_state_id="home"))
                    for index in (1, 2)
                )
            )
            ranked = store.rank([ActionCandidate("home", dead)], now=3.0)
        self.assertFalse(ranked[0].eligible)


class FieldKindInferenceTest(unittest.TestCase):
    """Gap 4: one constant cannot satisfy an email box and a quantity box."""

    def test_password_attribute_wins_over_every_keyword(self) -> None:
        inference = infer_field_kind(
            {"resource_id": "com.example:id/email_field", "content_description": ""},
            {"password": "true"},
        )
        self.assertIs(inference.kind, FieldKind.PASSWORD)

    def test_resource_id_keywords_are_recognized(self) -> None:
        cases = {
            "com.example:id/email_input": FieldKind.EMAIL,
            "com.example:id/server_url": FieldKind.URL,
            "com.example:id/phone": FieldKind.PHONE,
            "com.example:id/item_quantity": FieldKind.INTEGER,
            "com.example:id/unit_price": FieldKind.DECIMAL,
            "com.example:id/nickname": FieldKind.TEXT,
        }
        for resource_id, expected in cases.items():
            with self.subTest(resource_id=resource_id):
                inference = infer_field_kind(
                    {"resource_id": resource_id, "content_description": ""}, {}
                )
                self.assertIs(inference.kind, expected)

    def test_hint_attribute_is_consulted_when_the_id_is_uninformative(self) -> None:
        inference = infer_field_kind(
            {"resource_id": "", "content_description": ""},
            {"hint": "Enter your email"},
        )
        self.assertIs(inference.kind, FieldKind.EMAIL)

    def test_unrecognized_field_stays_text_rather_than_guessing(self) -> None:
        inference = infer_field_kind({"resource_id": "com.example:id/x9", "content_description": ""}, {})
        self.assertIs(inference.kind, FieldKind.TEXT)


class FieldValueTest(unittest.TestCase):
    def test_every_generated_value_is_device_shell_safe(self) -> None:
        for kind in FieldKind:
            with self.subTest(kind=kind):
                value = generate_field_value(kind, "valordroid")
                self.assertRegex(value, SAFE_VALUE)
                self.assertNotIn(" ", value)

    def test_every_generated_value_passes_its_own_validation(self) -> None:
        for kind in FieldKind:
            with self.subTest(kind=kind):
                value = generate_field_value(kind, "valordroid")
                self.assertTrue(validate_candidate_value(value, kind).accepted)

    def test_generation_is_a_pure_function_of_kind_and_seed(self) -> None:
        first = generate_field_value(FieldKind.EMAIL, "alpha")
        second = generate_field_value(FieldKind.EMAIL, "alpha")
        third = generate_field_value(FieldKind.EMAIL, "beta")
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)

    def test_recorded_value_is_re_derivable_for_offline_validation(self) -> None:
        value = generate_field_value(FieldKind.INTEGER, "valordroid")
        self.assertEqual(expected_deterministic_value("integer", "valordroid"), value)

    def test_placeholder_and_empty_values_are_refused(self) -> None:
        for candidate in ("<value>", "{value}", "[value]", "placeholder", ""):
            with self.subTest(candidate=candidate):
                self.assertFalse(
                    validate_candidate_value(candidate, FieldKind.TEXT).accepted
                )

    def test_shell_metacharacters_are_refused(self) -> None:
        for candidate in ("a;rm -rf /", "a$(id)", "a b", "a`id`", "a|b"):
            with self.subTest(candidate=candidate):
                self.assertFalse(
                    validate_candidate_value(candidate, FieldKind.TEXT).accepted
                )

    def test_value_longer_than_the_bound_is_refused(self) -> None:
        self.assertFalse(validate_candidate_value("a" * 129, FieldKind.TEXT).accepted)

    def test_wrong_kind_is_refused(self) -> None:
        self.assertFalse(validate_candidate_value("not-an-email", FieldKind.EMAIL).accepted)
        self.assertFalse(validate_candidate_value("abc", FieldKind.INTEGER).accepted)

    def test_deterministic_decision_reports_kind_and_value(self) -> None:
        decision = deterministic_field_value(
            {"resource_id": "com.example:id/email", "content_description": ""},
            {},
            seed="valordroid",
        )
        assert decision is not None
        value, kind, _evidence = decision
        self.assertIs(kind, FieldKind.EMAIL)
        self.assertEqual(value, "valordroid@valordroid.test")

    def test_email_seed_containing_an_address_is_used_directly(self) -> None:
        value = generate_field_value(FieldKind.EMAIL, "person@example.org")
        self.assertEqual(value, "person@example.org")

    def test_integer_field_does_not_receive_the_text_seed(self) -> None:
        decision = deterministic_field_value(
            {"resource_id": "com.example:id/quantity", "content_description": ""},
            {},
            seed="valordroid",
        )
        assert decision is not None
        value, kind, _ = decision
        self.assertIs(kind, FieldKind.INTEGER)
        self.assertNotEqual(value, "valordroid")
        self.assertEqual(value, "7")


if __name__ == "__main__":
    unittest.main()


class ListRowCandidateTest(unittest.TestCase):
    """Content rows inside a scrollable list must become tap candidates.

    Run evidence showed apps whose main screen is a RecyclerView (AnkiDroid's
    deck list, Commons' feeds) exploring only a handful of targets before the
    frontier went dark and the run degenerated into hammering Back, because the
    individual rows were not marked ``clickable`` and so never became candidates.
    """

    def _observer(self):
        from valordroid.android.observe import AndroidObserver

        class _Obs(AndroidObserver):
            def __init__(self) -> None:
                self._field_values = {}

        obs = _Obs()

        class _Session:
            package = "com.example.app"

        class _Config:
            text_input_value = None
            per_field_input_enabled = False

        obs.session = _Session()
        obs.config = _Config()
        return obs

    def _hierarchy(self) -> "ET.Element":
        import xml.etree.ElementTree as ET

        xml = """<?xml version='1.0' encoding='UTF-8'?>
        <hierarchy rotation="0">
          <node index="0" class="androidx.recyclerview.widget.RecyclerView"
                package="com.example.app" resource-id="com.example.app:id/list"
                text="" content-desc="" clickable="false" long-clickable="false"
                scrollable="true" enabled="true" password="false"
                bounds="[0,100][720,1200]">
            <node index="0" class="android.widget.LinearLayout"
                  package="com.example.app" resource-id="com.example.app:id/row"
                  text="First deck" content-desc="" clickable="false"
                  long-clickable="false" scrollable="false" enabled="true"
                  password="false" bounds="[0,100][720,300]"/>
            <node index="1" class="android.widget.LinearLayout"
                  package="com.example.app" resource-id="com.example.app:id/row"
                  text="Second deck" content-desc="" clickable="false"
                  long-clickable="false" scrollable="false" enabled="true"
                  password="false" bounds="[0,300][720,500]"/>
          </node>
          <node index="1" class="android.widget.FrameLayout"
                package="com.example.app" resource-id="" text=""
                content-desc="" clickable="false" long-clickable="false"
                scrollable="false" enabled="true" password="false"
                bounds="[0,1200][720,1280]"/>
        </hierarchy>"""
        return ET.fromstring(xml)

    def test_content_rows_of_a_scrollable_list_become_tap_candidates(self) -> None:
        obs = self._observer()
        nodes = obs._nodes(self._hierarchy())
        candidates, _ = obs._candidates("state-1", nodes)
        taps = [c for c in candidates if c.action.kind == "tap"]
        texts = {
            (c.action.parameters["selector"].get("text") or "")
            for c in taps
        }
        # Both list rows are now tappable even though neither is clickable.
        self.assertIn("First deck", texts)
        self.assertIn("Second deck", texts)

    def test_a_non_scrollable_listview_menu_still_exposes_its_rows(self) -> None:
        # Chess's main menu is an 8-item ListView that fits entirely on screen,
        # so it never reports scrollable="true". Gating on scrollability alone
        # left the whole menu invisible: 0 candidates on the app's first screen,
        # explaining a 1.15% coverage floor that persisted across every run.
        import xml.etree.ElementTree as ET

        xml = """<?xml version='1.0' encoding='UTF-8'?>
        <hierarchy rotation="0">
          <node index="0" class="android.widget.ListView"
                package="com.example.app" resource-id="" text="" content-desc=""
                clickable="false" long-clickable="false" scrollable="false"
                enabled="true" password="false" bounds="[0,100][720,900]">
            <node index="0" class="android.widget.TextView"
                  package="com.example.app" resource-id="" text="Play"
                  content-desc="" clickable="false" long-clickable="false"
                  scrollable="false" enabled="true" password="false"
                  bounds="[0,100][720,200]"/>
            <node index="1" class="android.widget.TextView"
                  package="com.example.app" resource-id="" text="Settings"
                  content-desc="" clickable="false" long-clickable="false"
                  scrollable="false" enabled="true" password="false"
                  bounds="[0,200][720,300]"/>
          </node>
        </hierarchy>"""
        obs = self._observer()
        nodes = obs._nodes(ET.fromstring(xml))
        candidates, _ = obs._candidates("state-1", nodes)
        texts = {
            (c.action.parameters["selector"].get("text") or "")
            for c in candidates
            if c.action.kind == "tap"
        }
        self.assertIn("Play", texts)
        self.assertIn("Settings", texts)

    def test_the_scrollable_container_still_offers_scroll_not_tap(self) -> None:
        obs = self._observer()
        nodes = obs._nodes(self._hierarchy())
        candidates, _ = obs._candidates("state-1", nodes)
        by_kind = {}
        for c in candidates:
            sel = c.action.parameters["selector"]
            if sel.get("resource_id") == "com.example.app:id/list":
                by_kind.setdefault(sel["resource_id"], set()).add(c.action.kind)
        kinds = by_kind.get("com.example.app:id/list", set())
        self.assertIn("scroll_forward", kinds)
        self.assertNotIn("tap", kinds)

    def test_a_contentless_wrapper_outside_a_list_is_not_a_candidate(self) -> None:
        # The empty FrameLayout must not become a candidate; only content-bearing
        # rows inside a scrollable container are promoted.
        obs = self._observer()
        nodes = obs._nodes(self._hierarchy())
        candidates, _ = obs._candidates("state-1", nodes)
        selectors = [c.action.parameters["selector"] for c in candidates]
        self.assertFalse(
            any(s.get("class_name", "").endswith("FrameLayout") for s in selectors)
        )


class ForeignPackageScreenTest(unittest.TestCase):
    """A system screen the app itself opened must still offer a way out.

    Run evidence (Muzei, round 3) showed the app's own "Activate" button
    opening the system live-wallpaper picker (a different package). Every
    node there was excluded for not belonging to the app, leaving 0
    candidates; Back was then refused too (a stale-state safety check caught
    mid-animation), so recovery fell straight to `reset_seed` -- wiping the
    app and relaunching into the exact same trap. 75 attempts, 0 gaining,
    flat at 16 units across every round. Clickable elements on a foreign
    screen (its real "Set wallpaper" / "Navigate up" buttons) must become tap
    candidates so exploration can act its way back, not just Back its way
    back.
    """

    def _observer(self):
        from valordroid.android.observe import AndroidObserver

        class _Obs(AndroidObserver):
            def __init__(self) -> None:
                self._field_values = {}

        obs = _Obs()

        class _Session:
            package = "net.nurik.roman.muzei"

        class _Config:
            text_input_value = "valordroid"
            per_field_input_enabled = False

        obs.session = _Session()
        obs.config = _Config()
        return obs

    def _foreign_hierarchy(self) -> "ET.Element":
        import xml.etree.ElementTree as ET

        # Modeled directly on the captured Muzei run's LiveWallpaperChange
        # hierarchy.xml: a foreign-package screen with real clickable buttons
        # plus inert layout chrome, and an EditText that does not belong to
        # the app under test.
        xml = """<?xml version='1.0' encoding='UTF-8'?>
        <hierarchy rotation="0">
          <node index="0" class="android.widget.FrameLayout"
                package="com.android.wallpaper.livepicker" resource-id=""
                text="" content-desc="" clickable="false" long-clickable="false"
                scrollable="false" enabled="true" password="false"
                bounds="[0,0][720,1184]">
            <node index="0" class="android.widget.ImageButton"
                  package="com.android.wallpaper.livepicker" resource-id=""
                  text="" content-desc="Navigate up" clickable="true"
                  long-clickable="false" scrollable="false" enabled="true"
                  password="false" bounds="[0,48][112,160]"/>
            <node index="1" class="android.widget.EditText"
                  package="com.android.wallpaper.livepicker"
                  resource-id="com.android.wallpaper.livepicker:id/search"
                  text="" content-desc="" clickable="true"
                  long-clickable="false" scrollable="false" enabled="true"
                  password="false" bounds="[112,48][436,160]"/>
            <node index="2" class="android.widget.Button"
                  package="com.android.wallpaper.livepicker"
                  resource-id="com.android.wallpaper.livepicker:id/preview_attribution_pane_set_wallpaper_button"
                  text="Set wallpaper" content-desc="" clickable="true"
                  long-clickable="false" scrollable="false" enabled="true"
                  password="false" bounds="[243,1066][477,1168]"/>
          </node>
        </hierarchy>"""
        return ET.fromstring(xml)

    def test_clickable_elements_on_a_foreign_screen_become_tap_candidates(self) -> None:
        obs = self._observer()
        nodes = obs._nodes(self._foreign_hierarchy())
        candidates, _ = obs._candidates("state-1", nodes)
        taps = [c for c in candidates if c.action.kind == "tap"]
        texts = {
            c.action.parameters["selector"].get("text", "")
            or c.action.parameters["selector"].get("content_description", "")
            for c in taps
        }
        self.assertIn("Navigate up", texts)
        self.assertIn("Set wallpaper", texts)

    def test_foreign_screen_no_longer_yields_zero_candidates(self) -> None:
        # This is the exact condition that triggered the trap: 0 candidates
        # meant no_eligible_candidate fired immediately, and Back's stale-state
        # guard could refuse the very next action, escalating straight to a
        # destructive `reset_seed`.
        obs = self._observer()
        nodes = obs._nodes(self._foreign_hierarchy())
        candidates, _ = obs._candidates("state-1", nodes)
        self.assertGreater(len(candidates), 0)

    def test_foreign_screen_edit_text_is_not_offered_a_typed_value(self) -> None:
        # The element is still tappable (it is clickable), but the app's
        # deterministic seed value is not something the app under test can be
        # credited for typing into someone else's field.
        obs = self._observer()
        nodes = obs._nodes(self._foreign_hierarchy())
        candidates, _ = obs._candidates("state-1", nodes)
        self.assertFalse(any(c.action.kind == "input_text" for c in candidates))

    def test_foreign_screen_inert_wrapper_is_still_excluded(self) -> None:
        # The FrameLayout backdrop is foreign and not clickable: it must stay
        # excluded exactly as before, so this is not a blanket "trust every
        # foreign node" change.
        obs = self._observer()
        nodes = obs._nodes(self._foreign_hierarchy())
        candidates, _ = obs._candidates("state-1", nodes)
        selectors = [c.action.parameters["selector"] for c in candidates]
        self.assertFalse(
            any(s.get("class_name", "").endswith("FrameLayout") for s in selectors)
        )

    def test_in_app_candidates_are_unaffected(self) -> None:
        # Same-package screens must keep every existing behavior: list rows,
        # scroll actions, and typed input all still apply.
        import xml.etree.ElementTree as ET

        xml = """<?xml version='1.0' encoding='UTF-8'?>
        <hierarchy rotation="0">
          <node index="0" class="android.widget.EditText"
                package="net.nurik.roman.muzei" resource-id="" text=""
                content-desc="" clickable="true" long-clickable="false"
                scrollable="false" enabled="true" password="false"
                bounds="[0,0][720,100]"/>
        </hierarchy>"""
        obs = self._observer()
        nodes = obs._nodes(ET.fromstring(xml))
        candidates, _ = obs._candidates("state-1", nodes)
        kinds = {c.action.kind for c in candidates}
        self.assertIn("input_text", kinds)


class ScreenDiscoveryRankingTest(unittest.TestCase):
    """Reaching new screens is the signal that actually tracks coverage.

    Measured over 44 runs, within each app: distinct screens reached correlates
    with coverage at r=0.896 (AnkiDroid, 9 runs), 0.994 (AntennaPod), 1.000
    (FeederD), 0.992 (Kiwix). The number of actions taken correlates
    *negatively* (-0.512 on AnkiDroid): re-tapping a mapped screen consumes the
    budget without paying. `effects` alone cannot express the difference, since
    it counts any state change including a bounce back to a screen already seen
    twenty times.
    """

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.config = CoreConfig()

    def _store(self) -> FrontierStore:
        return FrontierStore(self.root / "frontier.json", self.config)

    def test_a_screen_opener_outranks_a_screen_bouncer(self) -> None:
        store = self._store()
        opener = _action("opener", "home")
        bouncer = _action("bouncer", "home")
        store.reconcile(
            (
                # Reaches a screen never acted from before.
                ("h1", _attempt(1, "home", opener, after_state_id="fresh")),
                # Changes the screen, but only back to one already in the map.
                ("h2", _attempt(2, "home", bouncer, after_state_id="home")),
                ("h3", _attempt(3, "home", bouncer, after_state_id="home")),
            )
        )
        ranked = store.rank(
            [ActionCandidate("home", bouncer), ActionCandidate("home", opener)],
            now=10.0,
        )
        self.assertEqual(ranked[0].candidate.action.target_id, "opener")
        self.assertEqual(ranked[0].reason, "opens new screens")

    def test_an_untried_candidate_still_outranks_a_known_opener(self) -> None:
        # Ordering must stay: never tried, then known to open screens, then the
        # rest. Promoting a known opener above untried work would re-walk mapped
        # routes instead of expanding the map.
        store = self._store()
        opener = _action("opener", "home")
        store.reconcile((("h1", _attempt(1, "home", opener, after_state_id="fresh")),))
        ranked = store.rank(
            [ActionCandidate("home", opener), ActionCandidate("home", _action("new", "home"))],
            now=10.0,
        )
        self.assertEqual(ranked[0].reason, "untried")
        self.assertEqual(ranked[1].reason, "opens new screens")

    def test_revisiting_a_known_screen_is_not_counted_as_discovery(self) -> None:
        store = self._store()
        action = _action("revisit", "home")
        store.reconcile(
            (
                ("h1", _attempt(1, "home", action, after_state_id="detail")),
                # 'detail' has now been acted from, so arriving again is not new.
                ("h2", _attempt(2, "detail", _action("x", "detail"), after_state_id="detail")),
                ("h3", _attempt(3, "home", action, after_state_id="detail")),
            )
        )
        entry = next(e for e in store.entries() if e.action_id == action.stable_id)
        self.assertEqual(entry.discoveries, 1)
        self.assertEqual(entry.executed, 2)

    def test_a_no_effect_action_never_counts_as_discovery(self) -> None:
        store = self._store()
        action = _action("inert", "home")
        store.reconcile((("h1", _attempt(1, "home", action, after_state_id="home")),))
        entry = store.entries()[0]
        self.assertEqual(entry.discoveries, 0)

    def test_discovery_count_is_reconstructible_by_the_validator(self) -> None:
        # The validator recomputes the frontier from the transaction log and
        # compares byte for byte, so discovery must be derived only from the
        # replayed attempts, never from live-only bookkeeping.
        opener = _action("opener", "home")
        history = (
            ("h1", _attempt(1, "home", opener, after_state_id="fresh")),
            ("h2", _attempt(2, "fresh", _action("y", "fresh"), after_state_id="deeper")),
        )
        store = self._store()
        store.reconcile(history)
        expected = FrontierStore.expected_snapshot(history)
        import json as _json

        actual = _json.loads((self.root / "frontier.json").read_text())
        self.assertEqual(actual["entries"], expected["entries"])

    def test_screen_openers_are_worth_travelling_to(self) -> None:
        # navigation.worthwhile_states gates on the frontier's reason string, so
        # a new reason that is not listed there silently removes the best
        # candidates from cross-screen navigation.
        store = self._store()
        opener = _action("opener", "goal")
        store.reconcile((("h1", _attempt(1, "goal", opener, after_state_id="fresh")),))
        destinations = worthwhile_states(
            store.entries(),
            lambda candidate_id: store.entry_verdict(candidate_id, now=10.0),
        )
        self.assertIn("goal", destinations)
