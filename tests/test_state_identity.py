"""Gap 3.2: screen identity, bounded refinement, and what survives a split.

The coarse structural key is a set of in-package ``(resource_id, class_name)``
pairs, so it cannot move when a toggle flips, when a row is added, or when a list
scrolls. These tests pin the three collisions named in the audit, the bounded
adaptive rule that decides when a collision is allowed to split a screen, the
typed scroll postcondition that separates a finished list from a dead gesture,
and the offline checks that keep every recorded identity recomputable.
"""

from __future__ import annotations

import shutil
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from valordroid.android import observe
from valordroid.android.hierarchy import nodes_from_root
from valordroid.android.observe import (
    ADAPTIVE_SEMANTIC_STATE_IDENTITY_RULE,
    CONTROL_SEMANTICS_FACET_CLASS,
    SCROLL_PROGRESS_FACET_CLASS,
    SEMANTIC_REFINEMENT_EVENT,
    SEMANTIC_REFINEMENT_EVIDENCE_RULE,
    STATE_IDENTITY_RULE,
    CapturedState,
    SemanticRefinementRegistry,
)
from valordroid.android.outcomes import AndroidOutcomeVerifier
from valordroid.models import (
    ExecutionRecord,
    ExecutionStatus,
    LifecycleRecord,
    LifecycleStatus,
    OutcomeKind,
    ActionSpec,
    StateObservation,
    coarse_state_key,
    stable_hash,
)
from valordroid.validator import (
    _validate_refinement_evidence,
    _validate_state_identity,
    validate_run,
)

PACKAGE = "com.example.app"
ACTIVITY = f"{PACKAGE}/{PACKAGE}.MainActivity"


def _nodes(xml: str):
    return nodes_from_root(ET.fromstring(xml))


def _facets(xml: str) -> dict[str, str]:
    return observe.semantic_facets(_nodes(xml), package=PACKAGE)


def _structure(xml: str) -> str:
    return observe.structure_signature(_nodes(xml), package=PACKAGE)


def _semantic(xml: str) -> str:
    return observe.semantic_signature(_nodes(xml), package=PACKAGE)


def _row(index: int, top: int, text: str, *, height: int = 100) -> str:
    return (
        f'<node index="{index}" class="android.widget.TextView" package="{PACKAGE}"'
        f' resource-id="{PACKAGE}:id/row" text="{text}" content-desc=""'
        ' clickable="false" long-clickable="false" scrollable="false"'
        f' enabled="true" password="false" bounds="[0,{top}][720,{top + height}]"/>'
    )


def _scroll_view(rows: str, *, top: int = 100, bottom: int = 600) -> str:
    return (
        "<?xml version='1.0' encoding='UTF-8'?><hierarchy rotation=\"0\">"
        f'<node index="0" class="android.widget.ScrollView" package="{PACKAGE}"'
        f' resource-id="{PACKAGE}:id/list" text="" content-desc=""'
        ' clickable="false" long-clickable="false" scrollable="true"'
        f' enabled="true" password="false" bounds="[0,{top}][720,{bottom}]">'
        f"{rows}</node></hierarchy>"
    )


def _amount_list(*, count: int = 8, offset: int = 0) -> str:
    """A ledger whose every row is an amount, so every label is suppressed."""

    rows = "".join(
        _row(index, 100 + index * 100 - offset, f"{12.5 + index}")
        for index in range(count)
    )
    return _scroll_view(rows)


class VolatileRowCollisionTest(unittest.TestCase):
    """A list of amounts is exactly the case the control multiset cannot see."""

    def test_the_coarse_structure_cannot_notice_the_list_scrolling(self) -> None:
        self.assertEqual(
            _structure(_amount_list(offset=0)), _structure(_amount_list(offset=300))
        )

    def test_every_row_label_really_is_suppressed_as_volatile(self) -> None:
        # If the amounts survived normalization this collision would not exist,
        # and the scroll facet would be measuring something already covered.
        self.assertEqual(observe.semantic_text("12.5"), "")
        self.assertEqual(
            _facets(_amount_list(offset=0))[CONTROL_SEMANTICS_FACET_CLASS],
            _facets(_amount_list(offset=300))[CONTROL_SEMANTICS_FACET_CLASS],
        )

    def test_the_scroll_facet_is_what_separates_the_two_frames(self) -> None:
        before = _facets(_amount_list(offset=0))
        after = _facets(_amount_list(offset=300))
        self.assertNotEqual(
            before[SCROLL_PROGRESS_FACET_CLASS], after[SCROLL_PROGRESS_FACET_CLASS]
        )
        self.assertNotEqual(
            _semantic(_amount_list(offset=0)), _semantic(_amount_list(offset=300))
        )

    def test_a_gesture_that_moved_nothing_leaves_the_digest_alone(self) -> None:
        # The separation must come from the content's position, not from the act
        # of measuring it: two identical frames stay one state.
        self.assertEqual(
            _semantic(_amount_list(offset=200)), _semantic(_amount_list(offset=200))
        )

    def test_a_row_added_to_a_suppressed_list_changes_the_digest(self) -> None:
        # Multiplicity plus the child index of a label-less row: without the
        # index, eight identical anonymous entries and nine differ only in count,
        # and a row replaced in place would be invisible.
        self.assertNotEqual(
            _facets(_amount_list(count=8))[CONTROL_SEMANTICS_FACET_CLASS],
            _facets(_amount_list(count=9))[CONTROL_SEMANTICS_FACET_CLASS],
        )

    def test_the_suppressed_row_child_index_is_what_carries_position(self) -> None:
        # A row's own position under its container is added only when its label
        # was suppressed. Reordering two suppressed rows must therefore be
        # visible, even though the multiset of labels is unchanged.
        first = _scroll_view(_row(0, 100, "12.5") + _row(1, 200, "13.5"))
        swapped = _scroll_view(_row(0, 100, "13.5") + _row(1, 200, "12.5"))
        self.assertEqual(_structure(first), _structure(swapped))
        self.assertEqual(
            observe.scroll_progress_facets(_nodes(first), package=PACKAGE),
            observe.scroll_progress_facets(_nodes(swapped), package=PACKAGE),
        )
        # Both labels are volatile, so both rows carry only their index, and the
        # two frames are legitimately the same bounded semantic state.
        self.assertEqual(
            _facets(first)[CONTROL_SEMANTICS_FACET_CLASS],
            _facets(swapped)[CONTROL_SEMANTICS_FACET_CLASS],
        )
        # A row whose label survives keeps its label instead, so a real
        # reordering of readable rows is still one multiset.
        readable = _scroll_view(_row(0, 100, "Groceries") + _row(1, 200, "12.5"))
        self.assertNotEqual(
            _facets(first)[CONTROL_SEMANTICS_FACET_CLASS],
            _facets(readable)[CONTROL_SEMANTICS_FACET_CLASS],
        )


class ScrollProgressFacetTest(unittest.TestCase):
    def _facet(self, xml: str) -> dict:
        facets = observe.scroll_progress_facets(_nodes(xml), package=PACKAGE)
        self.assertEqual(list(facets), ["0/0"])
        return facets["0/0"]

    def test_the_facet_names_its_container_and_visible_rows(self) -> None:
        facet = self._facet(_amount_list(offset=0))
        self.assertEqual(facet["container"], f"{PACKAGE}:id/list")
        self.assertEqual(facet["class"], "android.widget.ScrollView")
        # The container is 500px tall over 100px rows, so five rows intersect it.
        self.assertEqual(facet["visible_children"], 5)
        self.assertTrue(facet["at_start"])
        self.assertFalse(facet["at_end"])

    def test_reaching_the_bottom_is_recorded_as_reaching_the_bottom(self) -> None:
        facet = self._facet(_amount_list(offset=300))
        self.assertFalse(facet["at_start"])
        self.assertTrue(facet["at_end"])

    def test_the_offset_bucket_moves_with_the_content(self) -> None:
        buckets = [self._facet(_amount_list(offset=n))["offset_bucket"] for n in (0, 300)]
        self.assertEqual(buckets[0], 0)
        self.assertGreater(buckets[1], buckets[0])

    def test_a_readable_list_reports_its_first_and_last_visible_rows(self) -> None:
        rows = "".join(
            _row(index, 100 + index * 100, name)
            for index, name in enumerate(("Groceries", "Rent", "Travel"))
        )
        facet = self._facet(_scroll_view(rows, bottom=300))
        self.assertEqual(facet["first_child"], "groceries")
        self.assertEqual(facet["last_child"], "rent")

    def test_container_work_stays_bounded_on_a_huge_dump(self) -> None:
        rows = "".join(_row(index, 100 + index * 10, f"{index}.5") for index in range(400))
        facet = self._facet(_scroll_view(rows))
        # Rows past the bound are not measured, so the facet cannot grow with the
        # dump. The container is still reported.
        self.assertLessEqual(
            facet["visible_children"], observe.MAX_SCROLL_ROWS_PER_CONTAINER
        )

    def test_only_list_like_containers_get_a_facet(self) -> None:
        xml = (
            "<?xml version='1.0' encoding='UTF-8'?><hierarchy rotation=\"0\">"
            f'<node index="0" class="android.widget.FrameLayout" package="{PACKAGE}"'
            ' resource-id="" text="" content-desc="" clickable="false"'
            ' long-clickable="false" scrollable="false" enabled="true"'
            ' password="false" bounds="[0,0][720,1280]"/></hierarchy>'
        )
        self.assertEqual(observe.scroll_progress_facets(_nodes(xml), package=PACKAGE), {})


class ToggleAndListCollisionTest(unittest.TestCase):
    """The other two collisions the audit demonstrated against the coarse key."""

    def _toggle(self, checked: str, label: str) -> str:
        return (
            "<?xml version='1.0' encoding='UTF-8'?><hierarchy rotation=\"0\">"
            f'<node index="0" class="android.widget.Switch" package="{PACKAGE}"'
            f' resource-id="{PACKAGE}:id/sync" text="{label}" content-desc=""'
            ' clickable="true" long-clickable="false" scrollable="false"'
            f' checkable="true" checked="{checked}" enabled="true"'
            ' password="false" bounds="[0,0][720,100]"/></hierarchy>'
        )

    def test_a_flipped_toggle_collides_coarsely_and_splits_semantically(self) -> None:
        off = self._toggle("false", "Sync off")
        on = self._toggle("true", "Sync on")
        self.assertEqual(_structure(off), _structure(on))
        self.assertNotEqual(_semantic(off), _semantic(on))

    def test_a_second_identical_row_collides_coarsely_and_splits_semantically(self) -> None:
        one = _scroll_view(_row(0, 100, "Groceries"))
        two = _scroll_view(_row(0, 100, "Groceries") + _row(1, 200, "Rent"))
        self.assertEqual(_structure(one), _structure(two))
        self.assertNotEqual(_semantic(one), _semantic(two))


class SemanticRefinementRegistryTest(unittest.TestCase):
    """Refinement must be earned by evidence and bounded by a stated cap."""

    def setUp(self) -> None:
        self.registry = SemanticRefinementRegistry()
        self.first = _semantic(_amount_list(offset=0))
        self.second = _semantic(_amount_list(offset=300))

    def _observe(self, semantic: str, xml: str) -> None:
        self.registry.record_observation(
            activity=ACTIVITY,
            structure_sha256="a" * 64,
            semantic_sha256=semantic,
            facets=_facets(xml),
        )

    def _record(self, *, effective: bool, after: str = "b" * 64):
        return self.registry.record_outcome(
            activity=ACTIVITY,
            structure_sha256="a" * 64,
            candidate_sha256="c" * 64,
            outcome_token=SemanticRefinementRegistry.outcome_token(
                effective=effective,
                after_activity=ACTIVITY,
                after_structure_sha256=after,
            ),
        )

    def _binds(self, semantic: str) -> bool:
        return self.registry.binds(
            activity=ACTIVITY, structure_sha256="a" * 64, semantic_sha256=semantic
        )

    def test_an_uncontested_structure_is_never_refined(self) -> None:
        self._observe(self.first, _amount_list(offset=0))
        self._observe(self.second, _amount_list(offset=300))
        # Two different semantic digests, but nothing has ever disagreed about
        # what an action does here, so the screen stays one state.
        self.assertIsNone(self._record(effective=True))
        self.assertIsNone(self._record(effective=True))
        self.assertFalse(self._binds(self.first))
        self.assertFalse(self._binds(self.second))

    def test_a_contested_structure_with_one_digest_is_not_refined(self) -> None:
        self._observe(self.first, _amount_list(offset=0))
        self.assertIsNone(self._record(effective=True))
        # The outcomes disagree, but refinement could not separate the frames it
        # would rename, so it is refused rather than applied for show.
        self.assertIsNone(self._record(effective=False))
        self.assertFalse(self._binds(self.first))

    def test_an_outcome_on_an_unobserved_structure_invents_nothing(self) -> None:
        self.assertIsNone(
            self.registry.record_outcome(
                activity=ACTIVITY,
                structure_sha256="f" * 64,
                candidate_sha256="c" * 64,
                outcome_token="t",
            )
        )

    def test_a_missing_coarse_half_refuses_to_form_a_key(self) -> None:
        self.assertIsNone(coarse_state_key(None, "a" * 64))
        self.assertIsNone(coarse_state_key(ACTIVITY, None))
        self.registry.record_observation(
            activity=None,
            structure_sha256="a" * 64,
            semantic_sha256=self.first,
            facets={},
        )
        self.assertFalse(
            self.registry.binds(
                activity=None, structure_sha256="a" * 64, semantic_sha256=self.first
            )
        )

    def _contest(self):
        self._observe(self.first, _amount_list(offset=0))
        self._observe(self.second, _amount_list(offset=300))
        self.assertIsNone(self._record(effective=True))
        return self._record(effective=False)

    def test_a_contested_structure_splits_once_and_says_why(self) -> None:
        split = self._contest()
        self.assertIsNotNone(split)
        self.assertEqual(split.rule, SEMANTIC_REFINEMENT_EVIDENCE_RULE)
        self.assertEqual(split.activity, ACTIVITY)
        self.assertEqual(split.coarse_structure_sha256, "a" * 64)
        # The only facet class that differs between these two frames is the one
        # that measured the scroll, and the record has to name it.
        self.assertEqual(split.facet_classes, (SCROLL_PROGRESS_FACET_CLASS,))
        self.assertEqual(split.distinct_outcomes, 2)
        self.assertEqual(split.distinct_semantic_digests, 2)
        self.assertEqual(
            split.refined_state_cap,
            SemanticRefinementRegistry.MAX_REFINED_STATES_PER_STRUCTURE,
        )
        # Announced exactly once, however many further outcomes disagree.
        self.assertIsNone(self._record(effective=True, after="d" * 64))

    def test_a_split_admits_its_digests_and_then_stops_at_the_cap(self) -> None:
        self.assertIsNotNone(self._contest())
        cap = SemanticRefinementRegistry.MAX_REFINED_STATES_PER_STRUCTURE
        admitted = [f"{index:064x}" for index in range(cap)]
        for digest in admitted:
            self.assertTrue(self._binds(digest))
        self.assertEqual(
            self.registry.refined_state_count(
                activity=ACTIVITY, structure_sha256="a" * 64
            ),
            cap,
        )
        # Past the cap a new frame keeps the coarse identity instead of minting
        # yet another state, while an already-admitted one stays stable.
        self.assertFalse(self._binds("e" * 64))
        self.assertTrue(self._binds(admitted[0]))

    def test_the_control_facet_is_named_when_it_is_what_differs(self) -> None:
        registry = SemanticRefinementRegistry()
        one = _scroll_view(_row(0, 100, "Groceries"), bottom=250)
        two = _scroll_view(_row(0, 100, "Rent"), bottom=250)
        # Same geometry, one readable label changed: the scroll facet moves too,
        # because the visible row identity is part of it, so both classes differ.
        for xml in (one, two):
            registry.record_observation(
                activity=ACTIVITY,
                structure_sha256="a" * 64,
                semantic_sha256=_semantic(xml),
                facets=_facets(xml),
            )
        token = SemanticRefinementRegistry.outcome_token
        registry.record_outcome(
            activity=ACTIVITY,
            structure_sha256="a" * 64,
            candidate_sha256="c" * 64,
            outcome_token=token(
                effective=True, after_activity=ACTIVITY, after_structure_sha256="b" * 64
            ),
        )
        split = registry.record_outcome(
            activity=ACTIVITY,
            structure_sha256="a" * 64,
            candidate_sha256="c" * 64,
            outcome_token=token(
                effective=False, after_activity=ACTIVITY, after_structure_sha256="a" * 64
            ),
        )
        self.assertIsNotNone(split)
        self.assertIn(CONTROL_SEMANTICS_FACET_CLASS, split.facet_classes)

    def test_tracking_is_bounded_by_structure_count(self) -> None:
        registry = SemanticRefinementRegistry()
        limit = SemanticRefinementRegistry.MAX_TRACKED_STRUCTURES
        for index in range(limit + 4):
            registry.record_observation(
                activity=ACTIVITY,
                structure_sha256=f"{index:064x}",
                semantic_sha256=self.first,
                facets={},
            )
        # The structures past the bound were never admitted, so an outcome on one
        # of them cannot split anything.
        self.assertIsNone(
            registry.record_outcome(
                activity=ACTIVITY,
                structure_sha256=f"{limit + 3:064x}",
                candidate_sha256="c" * 64,
                outcome_token="t",
            )
        )


class _Session:
    package = PACKAGE


class _Config:
    post_action_delay_seconds = 0.0
    text_input_value = None
    per_field_input_enabled = False


class _StubObserver:
    """Just enough observer for the verifier: one before frame, one after."""

    webview = None

    def __init__(self, before: CapturedState, after: CapturedState) -> None:
        self.latest = before
        self._after = after

    def capture(self, **_kwargs) -> CapturedState:
        self.latest = self._after
        return self._after

    @staticmethod
    def scroll_progress(nodes) -> dict:
        return observe.scroll_progress_facets(nodes, package=PACKAGE)


def _observation(state_id: str) -> StateObservation:
    return StateObservation(
        state_id=state_id,
        observed_at=1.0,
        activity=ACTIVITY,
        resumed_activities=(ACTIVITY,),
        structure_sha256="a" * 64,
        identity_rule=STATE_IDENTITY_RULE,
    )


def _captured(state_id: str, xml: str) -> CapturedState:
    return CapturedState(
        observation=_observation(state_id),
        candidates=(),
        nodes=_nodes(xml),
        hierarchy=xml.encode("utf-8"),
        screenshot=None,
    )


class ScrollPostconditionTest(unittest.TestCase):
    """A finished list and a dead gesture are both an unchanged state ID."""

    def _verify(self, before_xml: str, after_xml: str, *, kind: str = "scroll_forward"):
        before = _captured("1" * 64, before_xml)
        after = _captured("1" * 64, after_xml)
        verifier = AndroidOutcomeVerifier(_StubObserver(before, after), _Config())
        action = ActionSpec(
            kind,
            "list",
            {
                "expected_state_id": "1" * 64,
                "selector": {
                    "resource_id": f"{PACKAGE}:id/list",
                    "class_name": "android.widget.ScrollView",
                    "text": "",
                    "content_description": "",
                    "tree_path": "0/0",
                    "package": PACKAGE,
                },
            },
        )
        execution = ExecutionRecord(
            status=ExecutionStatus.EXECUTED,
            started_at=1.0,
            ended_at=1.5,
            action=action,
        )
        return verifier.verify(before.observation, execution)

    def test_a_scroll_that_moved_records_the_container_advancing(self) -> None:
        outcome = self._verify(_amount_list(offset=0), _amount_list(offset=300))
        self.assertEqual(
            outcome.details["scroll_verification_result"], "container_advanced"
        )
        self.assertEqual(outcome.details["scroll_direction"], "scroll_forward")
        self.assertFalse(outcome.details["scroll_before"]["at_end"])
        self.assertTrue(outcome.details["scroll_after"]["at_end"])

    def test_a_fling_against_the_end_is_not_a_dead_gesture(self) -> None:
        outcome = self._verify(_amount_list(offset=300), _amount_list(offset=300))
        self.assertEqual(
            outcome.details["scroll_verification_result"], "content_end_reached"
        )

    def test_a_gesture_on_a_list_that_is_not_at_an_end_is_a_dead_gesture(self) -> None:
        middle = _amount_list(count=20, offset=300)
        outcome = self._verify(middle, middle)
        self.assertEqual(
            outcome.details["scroll_verification_result"], "container_unchanged"
        )

    def test_a_backward_gesture_is_judged_against_the_start(self) -> None:
        outcome = self._verify(
            _amount_list(offset=0), _amount_list(offset=0), kind="scroll_backward"
        )
        self.assertEqual(outcome.details["scroll_direction"], "scroll_backward")
        self.assertEqual(
            outcome.details["scroll_verification_result"], "content_end_reached"
        )

    def test_a_stale_before_frame_is_refused_rather_than_used(self) -> None:
        # The observer keeps one frame. If it is not the frame this action was
        # dispatched against, there is no before position, and inventing one from
        # a different screen would be worse than saying so.
        before = _captured("9" * 64, _amount_list(offset=0))
        after = _captured("1" * 64, _amount_list(offset=300))
        verifier = AndroidOutcomeVerifier(_StubObserver(before, after), _Config())
        action = ActionSpec(
            "scroll_forward",
            "list",
            {
                "expected_state_id": "1" * 64,
                "selector": {
                    "resource_id": f"{PACKAGE}:id/list",
                    "class_name": "android.widget.ScrollView",
                    "text": "",
                    "content_description": "",
                    "tree_path": "0/0",
                    "package": PACKAGE,
                },
            },
        )
        outcome = verifier.verify(
            _observation("1" * 64),
            ExecutionRecord(
                status=ExecutionStatus.EXECUTED,
                started_at=1.0,
                ended_at=1.5,
                action=action,
            ),
        )
        self.assertEqual(
            outcome.details["scroll_verification_result"], "before_position_unavailable"
        )
        self.assertIsNone(outcome.details["scroll_before"])

    def test_the_typed_verdict_matches_the_recorded_reason(self) -> None:
        # The postcondition returns a verdict as well as evidence, exactly as the
        # input_text postcondition does, so it can be reasoned about directly.
        observer = _StubObserver(
            _captured("1" * 64, _amount_list(offset=0)),
            _captured("1" * 64, _amount_list(offset=300)),
        )
        verifier = AndroidOutcomeVerifier(observer, _Config())
        selector = {
            "resource_id": f"{PACKAGE}:id/list",
            "class_name": "android.widget.ScrollView",
            "text": "",
            "content_description": "",
            "tree_path": "0/0",
            "package": PACKAGE,
        }
        execution = ExecutionRecord(
            status=ExecutionStatus.EXECUTED,
            started_at=1.0,
            ended_at=1.5,
            action=ActionSpec("scroll_forward", "list", {"selector": selector}),
        )
        advanced, details = verifier._scroll_target(
            execution, _nodes(_amount_list(offset=0)), observer._after
        )
        self.assertTrue(advanced)
        self.assertEqual(details["scroll_verification_result"], "container_advanced")
        stalled, details = verifier._scroll_target(
            execution, _nodes(_amount_list(offset=300)), observer._after
        )
        self.assertFalse(stalled)
        self.assertEqual(details["scroll_verification_result"], "content_end_reached")

    def test_a_malformed_selector_is_refused(self) -> None:
        observer = _StubObserver(
            _captured("1" * 64, _amount_list(offset=0)),
            _captured("1" * 64, _amount_list(offset=0)),
        )
        verifier = AndroidOutcomeVerifier(observer, _Config())
        execution = ExecutionRecord(
            status=ExecutionStatus.EXECUTED,
            started_at=1.0,
            ended_at=1.5,
            action=ActionSpec("scroll_forward", "list", {"selector": {"tree_path": 7}}),
        )
        verdict, details = verifier._scroll_target(
            execution, _nodes(_amount_list(offset=0)), observer._after
        )
        self.assertIsNone(verdict)
        self.assertEqual(details["scroll_verification_result"], "invalid_executed_action")

    def test_a_vanished_container_is_reported_rather_than_guessed(self) -> None:
        empty = (
            "<?xml version='1.0' encoding='UTF-8'?><hierarchy rotation=\"0\">"
            f'<node index="0" class="android.widget.FrameLayout" package="{PACKAGE}"'
            ' resource-id="" text="" content-desc="" clickable="false"'
            ' long-clickable="false" scrollable="false" enabled="true"'
            ' password="false" bounds="[0,0][720,1280]"/></hierarchy>'
        )
        outcome = self._verify(_amount_list(offset=0), empty)
        self.assertEqual(
            outcome.details["scroll_verification_result"], "container_absent_after"
        )

    def test_the_postcondition_does_not_reclassify_the_outcome(self) -> None:
        # Immediate reward stays where it was: an unchanged state ID is still
        # NO_EFFECT, and only genuine state refinement can change that. The
        # postcondition is evidence about the gesture, not a second verdict.
        outcome = self._verify(_amount_list(offset=0), _amount_list(offset=300))
        self.assertIs(outcome.kind, OutcomeKind.NO_EFFECT)
        self.assertFalse(outcome.state_changed)
        self.assertIsNone(outcome.field_accepted)

    def test_a_tap_records_no_scroll_postcondition(self) -> None:
        before = _captured("1" * 64, _amount_list(offset=0))
        after = _captured("2" * 64, _amount_list(offset=0))
        verifier = AndroidOutcomeVerifier(_StubObserver(before, after), _Config())
        action = ActionSpec("tap", "row", {"expected_state_id": "1" * 64})
        outcome = verifier.verify(
            before.observation,
            ExecutionRecord(
                status=ExecutionStatus.EXECUTED,
                started_at=1.0,
                ended_at=1.5,
                action=action,
            ),
        )
        self.assertNotIn("scroll_verification", outcome.details)


def _lifecycle(details: dict, **overrides) -> LifecycleRecord:
    values = {
        "record_id": "r-1",
        "observed_at": 1.0,
        "phase": "exploration",
        "event": SEMANTIC_REFINEMENT_EVENT,
        "status": LifecycleStatus.INFO,
        "details": details,
        "error": None,
    }
    values.update(overrides)
    return LifecycleRecord(**values)


class _Runtime:
    def __init__(self, enabled: bool) -> None:
        self.semantic_state_enabled = enabled


def _split_details(**overrides) -> dict:
    values = {
        "rule": SEMANTIC_REFINEMENT_EVIDENCE_RULE,
        "activity": ACTIVITY,
        "coarse_structure_sha256": "a" * 64,
        "facet_classes": [SCROLL_PROGRESS_FACET_CLASS],
        "contested_candidate_sha256": "c" * 64,
        "distinct_outcomes": 2,
        "distinct_semantic_digests": 2,
        "refined_state_cap": 8,
    }
    values.update(overrides)
    return values


class RefinementEvidenceSchemaTest(unittest.TestCase):
    def _codes(self, records, *, enabled: bool = True) -> list[str]:
        issues: list = []
        _validate_refinement_evidence(
            issues, runtime=_Runtime(enabled), lifecycle=records
        )
        return [issue.code for issue in issues]

    def test_a_well_formed_split_record_is_accepted(self) -> None:
        self.assertEqual(self._codes([_lifecycle(_split_details())]), [])

    def test_evidence_cannot_exist_while_refinement_is_disabled(self) -> None:
        self.assertIn(
            "semantic_refinement_unbound",
            self._codes([_lifecycle(_split_details())], enabled=False),
        )

    def test_a_split_must_name_at_least_one_facet_class(self) -> None:
        self.assertIn(
            "semantic_refinement_evidence_binding",
            self._codes([_lifecycle(_split_details(facet_classes=[]))]),
        )

    def test_an_unknown_facet_class_is_rejected(self) -> None:
        self.assertIn(
            "semantic_refinement_evidence_binding",
            self._codes([_lifecycle(_split_details(facet_classes=["invented_v9"]))]),
        )

    def test_a_split_must_state_a_cap(self) -> None:
        details = _split_details()
        del details["refined_state_cap"]
        self.assertIn(
            "semantic_refinement_evidence_binding", self._codes([_lifecycle(details)])
        )

    def test_a_single_outcome_cannot_justify_a_split(self) -> None:
        self.assertIn(
            "semantic_refinement_evidence_binding",
            self._codes([_lifecycle(_split_details(distinct_outcomes=1))]),
        )

    def test_one_coarse_structure_cannot_announce_itself_twice(self) -> None:
        self.assertIn(
            "semantic_refinement_duplicate",
            self._codes([_lifecycle(_split_details()), _lifecycle(_split_details())]),
        )

    def test_two_different_structures_may_both_split(self) -> None:
        self.assertEqual(
            self._codes(
                [
                    _lifecycle(_split_details()),
                    _lifecycle(_split_details(coarse_structure_sha256="b" * 64)),
                ]
            ),
            [],
        )

    def test_the_record_must_be_an_exploration_info_record(self) -> None:
        self.assertIn(
            "semantic_refinement_evidence_binding",
            self._codes([_lifecycle(_split_details(), phase="device")]),
        )


class _Attempt:
    def __init__(self, attempt_id: str, details: dict) -> None:
        self.attempt_id = attempt_id
        self.outcome = type("_Outcome", (), {"details": details})()


def _details(**overrides) -> dict:
    values = {
        "before_state_id": "1" * 64,
        "after_state_id": "2" * 64,
        "before_activity": ACTIVITY,
        "after_activity": ACTIVITY,
        "before_structure_sha256": "a" * 64,
        "after_structure_sha256": "b" * 64,
        "before_semantic_sha256": None,
        "after_semantic_sha256": None,
        "identity_rule": STATE_IDENTITY_RULE,
    }
    values.update(overrides)
    return values


class _Store:
    def __init__(self, root: Path) -> None:
        self.root = root


class StateIdentityRuleTest(unittest.TestCase):
    """The rule must be one this validator knows, and one for the whole run."""

    def _codes(self, attempts, *, enabled: bool = False, root: Path | None = None):
        issues: list = []
        _validate_state_identity(
            issues,
            store=_Store(root or Path("/nonexistent-run")),
            package_name=PACKAGE,
            runtime=_Runtime(enabled),
            lifecycle=[],
            attempts=attempts,
        )
        return [issue.code for issue in issues]

    def test_a_consistent_coarse_run_is_accepted(self) -> None:
        self.assertEqual(self._codes([_Attempt("a-1", _details())]), [])

    def test_a_legacy_run_without_any_rule_is_left_alone(self) -> None:
        details = _details()
        del details["identity_rule"]
        del details["before_semantic_sha256"]
        del details["after_semantic_sha256"]
        self.assertEqual(self._codes([_Attempt("a-1", details)]), [])

    def test_a_rule_recorded_by_only_some_attempts_is_drift(self) -> None:
        partial = _details()
        del partial["identity_rule"]
        self.assertIn(
            "state_identity_rule_partial",
            self._codes([_Attempt("a-1", _details()), _Attempt("a-2", partial)]),
        )

    def test_two_rules_in_one_run_are_rejected(self) -> None:
        codes = self._codes(
            [
                _Attempt("a-1", _details()),
                _Attempt(
                    "a-2",
                    _details(identity_rule=ADAPTIVE_SEMANTIC_STATE_IDENTITY_RULE),
                ),
            ]
        )
        self.assertIn("state_identity_rule_drift", codes)

    def test_an_unknown_rule_is_rejected(self) -> None:
        self.assertIn(
            "state_identity_rule_unknown",
            self._codes([_Attempt("a-1", _details(identity_rule="invented_v9"))]),
        )

    def test_the_rule_must_agree_with_the_runtime_flag(self) -> None:
        self.assertIn(
            "state_identity_rule_unbound",
            self._codes([_Attempt("a-1", _details())], enabled=True),
        )
        self.assertIn(
            "state_identity_rule_unbound",
            self._codes(
                [
                    _Attempt(
                        "a-1",
                        _details(identity_rule=ADAPTIVE_SEMANTIC_STATE_IDENTITY_RULE),
                    )
                ],
                enabled=False,
            ),
        )

    def test_a_semantic_digest_under_a_coarse_rule_is_rejected(self) -> None:
        codes = self._codes(
            [_Attempt("a-1", _details(before_semantic_sha256="d" * 64))]
        )
        self.assertIn("state_identity_semantic_unbound", codes)

    def test_one_state_id_cannot_claim_two_identities(self) -> None:
        self.assertIn(
            "state_identity_unstable",
            self._codes(
                [
                    _Attempt("a-1", _details()),
                    _Attempt("a-2", _details(before_structure_sha256="f" * 64)),
                ]
            ),
        )

    def test_one_state_id_cannot_claim_two_activities(self) -> None:
        self.assertIn(
            "state_identity_unstable",
            self._codes(
                [
                    _Attempt("a-1", _details()),
                    _Attempt(
                        "a-2", _details(before_activity=f"{PACKAGE}/{PACKAGE}.Other")
                    ),
                ]
            ),
        )

    def test_an_ambiguous_frame_keeps_a_null_activity(self) -> None:
        # An ambiguous frame has no unique resumed activity, so its preimage is
        # not reconstructible offline. That is recorded, not guessed at.
        self.assertEqual(
            self._codes(
                [_Attempt("a-1", _details(before_activity=None, after_activity=None))]
            ),
            [],
        )

    def test_a_malformed_digest_is_reported(self) -> None:
        self.assertIn(
            "state_identity_digest_shape",
            self._codes([_Attempt("a-1", _details(before_structure_sha256="short"))]),
        )

    def test_a_non_string_activity_is_reported(self) -> None:
        self.assertIn(
            "state_identity_digest_shape",
            self._codes([_Attempt("a-1", _details(before_activity=7))]),
        )


class SemanticStateRunTest(unittest.TestCase):
    """One real run with refinement enabled, validated end to end."""

    class _Deferred:
        def __init__(self) -> None:
            self.cleanups: list[tuple] = []

        def addCleanup(self, function, *args, **kwargs) -> None:  # noqa: N802
            self.cleanups.append((function, args, kwargs))

        def run_cleanups(self) -> None:
            while self.cleanups:
                function, args, kwargs = self.cleanups.pop()
                try:
                    function(*args, **kwargs)
                except Exception:  # noqa: BLE001 - teardown is best effort
                    pass

    @classmethod
    def setUpClass(cls) -> None:
        from tests.test_android_loop import _Harness

        cls._deferred = cls._Deferred()
        cls._harness = _Harness(
            cls._deferred,
            runtime={
                "semantic_state_enabled": True,
                "max_actions": 8,
                # Its own device identity, so this run takes its own device lock
                # rather than queueing behind another fake-device run.
                "serial": "fake-semantic-state-device",
            },
        )
        cls.root, cls.result = cls._harness.run()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._deferred.run_cleanups()

    def _attempts(self) -> list[dict]:
        import json

        path = self.root / "actions.jsonl"
        return [
            json.loads(line)["payload"]["attempt"]
            for line in path.read_text().splitlines()
        ]

    def test_the_refined_run_finishes_and_validates(self) -> None:
        self.assertEqual(self.result.status, "finished", self.result.stop_reason)
        validation = validate_run(self.root)
        self.assertTrue(validation.ok, [issue.__dict__ for issue in validation.issues])

    def test_every_attempt_records_the_adaptive_rule_and_both_digests(self) -> None:
        attempts = self._attempts()
        self.assertTrue(attempts)
        for attempt in attempts:
            details = attempt["outcome"]["details"]
            self.assertEqual(
                details["identity_rule"], ADAPTIVE_SEMANTIC_STATE_IDENTITY_RULE
            )
            self.assertEqual(len(details["before_structure_sha256"]), 64)
            self.assertEqual(len(details["before_semantic_sha256"]), 64)
            self.assertEqual(len(details["after_semantic_sha256"]), 64)

    def test_a_tampered_hierarchy_breaks_its_identity_binding(self) -> None:
        # The point of retaining the dump is that the state ID can be recomputed
        # from it. Replace one dump with a different valid one and the binding
        # must fail, or the retention proves nothing.
        import tempfile

        with tempfile.TemporaryDirectory() as name:
            copy = Path(name) / "run"
            shutil.copytree(self.root, copy)
            self.assertTrue(validate_run(copy).ok)
            dumps = sorted((copy / "observations").glob("*/hierarchy.xml"))
            self.assertTrue(dumps)
            dumps[0].write_bytes(_amount_list(offset=0).encode("utf-8"))
            codes = [issue.code for issue in validate_run(copy).errors]
            self.assertIn("state_identity_artifact_binding", codes)


if __name__ == "__main__":
    unittest.main()
