"""Gap 3.1/§4: one bounded, secret-refusing visual step, and nothing wider.

The audit authorises a screenshot fallback only "for that failed step", notes
that screenshot collection was disabled in every published V1/V2 manifest, and
defers the plateau case. So the properties worth pinning are mostly negative: the
mechanism must be off by default, must refuse itself outright for any run that
ever held a secret, must refuse a screen the deterministic rule could represent,
must cost exactly one bounded model call and one action, and must prove the app
came back before exploration is allowed to continue from it.

Each refusal below is reached by making exactly one input wrong, so a test that
passes for the wrong reason is visible: the eligibility decision is a pure
function of `EligibilityInputs`, and these construct it directly.
"""

from __future__ import annotations

import hashlib
import json
import unittest
import xml.etree.ElementTree as ET

from valordroid.android.hierarchy import nodes_from_root
from valordroid.config import CoreConfig
from valordroid.llm.config import VISUAL_ESCAPE, ModelConfig
from valordroid.llm.gateway import (
    ModelGateway,
    PendingModelCall,
    build_visual_escape_prompt,
    visual_escape_choice_from_response,
)
from valordroid.llm.providers import (
    ModelProviderError,
    ModelResponse,
    RecordingProvider,
)
from valordroid.models import LifecycleRecord, LifecycleStatus, stable_hash
from valordroid.runtime_config import RuntimeConfig
from valordroid.validator import _validate_visual_escape
from valordroid.visual_escape import (
    MAX_VISUAL_ESCAPES,
    VISUAL_ESCAPE_EVENT,
    VISUAL_ESCAPE_EVIDENCE_RULE,
    EligibilityInputs,
    RestorationProof,
    VisualEscapePolicy,
    VisualEscapeRecord,
    dispatch_failure_proof,
    eligibility_refusal,
    escape_offers,
    evaluate_restoration,
    offerable_elements,
    resolve_choice,
)

PACKAGE = "com.example.app"
ACTIVITY = f"{PACKAGE}/{PACKAGE}.MainActivity"
PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-frame"


def _inputs(**overrides) -> EligibilityInputs:
    """An eligible decision. Every test makes exactly one thing wrong."""

    values = {
        "trigger": "screen_exhausted",
        "secrets_present": False,
        "screenshots_suppressed": False,
        "screenshot_available": True,
        "screenshot_digest_matches": True,
        "observation_is_current": True,
        "foreground_owned": True,
        "ladder_exhausted": True,
        "untried_present": False,
        "plan_present": False,
        "deterministic_candidates": 0,
        "offered_elements": 3,
        "model_available": True,
        "model_calls_recorded": 1,
        "max_model_calls": 8,
        "escapes_spent": 0,
        "escape_budget": 2,
        "actions_remaining": 5,
        "run_seconds_remaining": 120.0,
    }
    values.update(overrides)
    return EligibilityInputs(**values)


class EligibilityTest(unittest.TestCase):
    def test_the_baseline_decision_is_allowed(self) -> None:
        self.assertIsNone(eligibility_refusal(_inputs()))

    def test_a_run_that_held_a_secret_is_refused_first(self) -> None:
        """Secret safety is answered before anything reads image bytes.

        A screenshot is pixels: the hierarchy sanitizer cannot redact it. So the
        only admissible proof is that no secret was ever present, and it must be
        checked before eligibility, before budgets, and before the frame is read.
        """

        self.assertEqual(
            eligibility_refusal(
                _inputs(
                    secrets_present=True,
                    # Everything else is also wrong; secrecy must still win.
                    observation_is_current=False,
                    screenshot_available=False,
                    model_available=False,
                )
            ),
            "secrets_present",
        )

    def test_suppressed_screenshots_are_refused_before_eligibility(self) -> None:
        self.assertEqual(
            eligibility_refusal(
                _inputs(screenshots_suppressed=True, observation_is_current=False)
            ),
            "screenshots_suppressed",
        )

    def test_a_superseded_frame_is_refused(self) -> None:
        self.assertEqual(
            eligibility_refusal(_inputs(observation_is_current=False)),
            "stale_observation",
        )

    def test_a_missing_or_mismatched_screenshot_is_refused(self) -> None:
        self.assertEqual(
            eligibility_refusal(_inputs(screenshot_available=False)),
            "screenshot_unavailable",
        )
        self.assertEqual(
            eligibility_refusal(_inputs(screenshot_digest_matches=False)),
            "screenshot_digest_mismatch",
        )

    def test_a_foreign_foreground_is_refused(self) -> None:
        self.assertEqual(
            eligibility_refusal(_inputs(foreground_owned=False)),
            "foreign_foreground",
        )

    def test_only_the_two_exhaustion_triggers_qualify(self) -> None:
        for trigger in ("screen_exhausted", "coverage_plateau"):
            with self.subTest(trigger=trigger):
                self.assertIsNone(eligibility_refusal(_inputs(trigger=trigger)))
        # A cooling-down candidate is expected back, so paying a model to
        # pre-empt the cooldown recreates the repeat loop it exists to stop.
        self.assertEqual(
            eligibility_refusal(_inputs(trigger="temporarily_no_eligible")),
            "trigger_not_eligible",
        )

    def test_remaining_deterministic_work_is_refused(self) -> None:
        for name in ("untried_present", "plan_present"):
            with self.subTest(name=name):
                self.assertEqual(
                    eligibility_refusal(_inputs(**{name: True})),
                    "ladder_not_exhausted",
                )
        self.assertEqual(
            eligibility_refusal(_inputs(ladder_exhausted=False)),
            "ladder_not_exhausted",
        )

    def test_a_screen_the_rule_could_represent_is_refused(self) -> None:
        """This is not an ordinary stall. A stall has candidates; this has none."""

        self.assertEqual(
            eligibility_refusal(_inputs(deterministic_candidates=1)),
            "screen_is_representable",
        )

    def test_a_frame_with_nothing_dispatchable_is_refused(self) -> None:
        self.assertEqual(
            eligibility_refusal(_inputs(offered_elements=0)),
            "no_offerable_element",
        )

    def test_each_budget_is_refused_on_its_own_terms(self) -> None:
        cases = {
            "model_unavailable": {"model_available": False},
            "model_budget_spent": {"model_calls_recorded": 8, "max_model_calls": 8},
            "escape_budget_spent": {"escapes_spent": 2, "escape_budget": 2},
            "action_budget_spent": {"actions_remaining": 0},
            "run_budget_spent": {"run_seconds_remaining": 0.0},
        }
        for expected, overrides in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(eligibility_refusal(_inputs(**overrides)), expected)

    def test_a_run_without_a_time_bound_is_not_treated_as_expired(self) -> None:
        self.assertIsNone(eligibility_refusal(_inputs(run_seconds_remaining=None)))


class PolicyTest(unittest.TestCase):
    def test_the_mechanism_is_inert_by_default(self) -> None:
        policy = VisualEscapePolicy(enabled=False, budget=0)
        self.assertFalse(policy.active)
        self.assertEqual(policy.records_written, 0)

    def test_an_enabled_policy_with_no_budget_is_still_inert(self) -> None:
        self.assertFalse(VisualEscapePolicy(enabled=True, budget=0).active)

    def test_the_budget_cannot_exceed_the_hard_bound(self) -> None:
        with self.assertRaises(ValueError):
            VisualEscapePolicy(enabled=True, budget=MAX_VISUAL_ESCAPES + 1)

    def test_one_step_per_invocation_and_never_past_the_budget(self) -> None:
        policy = VisualEscapePolicy(enabled=True, budget=2)
        policy.note_escape()
        policy.note_escape()
        self.assertEqual(policy.escapes_spent, 2)
        with self.assertRaises(ValueError):
            policy.note_escape()

    def test_a_refusal_reason_is_recorded_once(self) -> None:
        """Otherwise a run whose ladder empties often turns refusal into noise."""

        policy = VisualEscapePolicy(enabled=True, budget=1)
        self.assertTrue(policy.should_record_refusal("no_offerable_element"))
        self.assertFalse(policy.should_record_refusal("no_offerable_element"))
        self.assertTrue(policy.should_record_refusal("run_budget_spent"))

    def test_an_unknown_refusal_reason_is_refused(self) -> None:
        policy = VisualEscapePolicy(enabled=True, budget=1)
        with self.assertRaises(ValueError):
            policy.should_record_refusal("because_i_said_so")

    def test_stopping_retires_the_mechanism_and_keeps_the_first_reason(self) -> None:
        policy = VisualEscapePolicy(enabled=True, budget=2)
        policy.stop("secrets_present")
        policy.stop("something_else")
        self.assertFalse(policy.active)
        self.assertEqual(policy.stopped_reason, "secrets_present")


def _node(
    index: int,
    *,
    package: str = PACKAGE,
    resource: str = "",
    text: str = "",
    enabled: str = "true",
    scrollable: str = "false",
    source: str = "",
    top: int | None = None,
) -> str:
    offset = index * 100 if top is None else top
    extra = f' source="{source}"' if source else ""
    return (
        f'<node index="{index}" class="android.widget.Button" package="{package}"'
        f' resource-id="{resource}" text="{text}" content-desc=""'
        f' clickable="true" long-clickable="false" scrollable="{scrollable}"'
        f' enabled="{enabled}" password="false"'
        f' bounds="[0,{offset}][720,{offset + 100}]"{extra}/>'
    )


def _nodes(*inner: str):
    xml = (
        "<?xml version='1.0' encoding='UTF-8'?><hierarchy rotation=\"0\">"
        + "".join(inner)
        + "</hierarchy>"
    )
    return nodes_from_root(ET.fromstring(xml))


class OfferableElementTest(unittest.TestCase):
    def test_an_owned_enabled_unique_node_is_offered(self) -> None:
        elements = offerable_elements(
            _nodes(_node(0, resource=f"{PACKAGE}:id/go", text="Go")),
            package=PACKAGE,
        )
        self.assertEqual(len(elements), 1)
        self.assertEqual(elements[0].kinds, ("tap", "long_press"))
        # The offered identity is the selector digest, which is exactly the
        # target_id the committed action must carry.
        self.assertEqual(
            elements[0].element_id, stable_hash(dict(elements[0].selector))
        )

    def test_a_scrollable_node_also_offers_both_scroll_kinds(self) -> None:
        elements = offerable_elements(
            _nodes(_node(0, resource=f"{PACKAGE}:id/list", scrollable="true")),
            package=PACKAGE,
        )
        self.assertEqual(
            elements[0].kinds, ("tap", "long_press", "scroll_forward", "scroll_backward")
        )

    def test_a_foreign_node_is_never_offered(self) -> None:
        self.assertEqual(
            offerable_elements(
                _nodes(_node(0, package="com.other.app", resource="x")),
                package=PACKAGE,
            ),
            (),
        )

    def test_a_disabled_node_is_not_offered(self) -> None:
        self.assertEqual(
            offerable_elements(
                _nodes(_node(0, resource=f"{PACKAGE}:id/go", enabled="false")),
                package=PACKAGE,
            ),
            (),
        )

    def test_a_projected_dom_node_is_not_offered(self) -> None:
        """Its coordinates belong to a page its own adapter re-proves."""

        self.assertEqual(
            offerable_elements(
                _nodes(
                    _node(0, resource="valordroid.webview:abc", source="webview_dom")
                ),
                package=PACKAGE,
            ),
            (),
        )

    def test_two_nodes_of_one_shape_stay_distinct_by_tree_path(self) -> None:
        """A real dump cannot produce an ambiguous selector: the path separates."""

        elements = offerable_elements(
            _nodes(_node(0, top=0), _node(1, top=0)), package=PACKAGE
        )
        self.assertEqual(len(elements), 2)
        self.assertNotEqual(elements[0].element_id, elements[1].element_id)

    def test_an_ambiguous_selector_is_not_offered(self) -> None:
        """The executor refuses an ambiguous target, so offering one wastes a call.

        Built by hand rather than from XML: a well-formed hierarchy always gives
        two nodes different tree paths, so this guard exists for a malformed or
        merged tree, and that is the only way to reach it.
        """

        class _Node:
            selector = {
                "resource_id": f"{PACKAGE}:id/go",
                "class_name": "android.widget.Button",
                "text": "",
                "content_description": "",
                "tree_path": "0/0",
                "package": PACKAGE,
            }
            attributes = {"enabled": "true"}
            bounds = (0, 0, 720, 100)

        self.assertEqual(offerable_elements([_Node(), _Node()], package=PACKAGE), ())

    def test_the_order_is_deterministic_and_bounded(self) -> None:
        nodes = _nodes(
            *(
                _node(index, resource=f"{PACKAGE}:id/b{index}")
                for index in range(20)
            )
        )
        elements = offerable_elements(nodes, package=PACKAGE, limit=5)
        self.assertEqual(len(elements), 5)
        self.assertEqual(
            [item.bounds[1] for item in elements], sorted(item.bounds[1] for item in elements)
        )
        self.assertEqual(
            [item.element_id for item in elements],
            [item.element_id for item in offerable_elements(nodes, package=PACKAGE, limit=5)],
        )

    def test_resolve_choice_accepts_only_an_offered_pair(self) -> None:
        elements = offerable_elements(
            _nodes(_node(0, resource=f"{PACKAGE}:id/go")), package=PACKAGE
        )
        chosen = elements[0]
        self.assertIs(resolve_choice(elements, chosen.element_id, "tap"), chosen)
        # A kind this element does not support, and an element never offered.
        self.assertIsNone(resolve_choice(elements, chosen.element_id, "scroll_forward"))
        self.assertIsNone(resolve_choice(elements, "e" * 64, "tap"))
        self.assertIsNone(resolve_choice(elements, None, "tap"))


class _Observation:
    def __init__(self, state_id: str, observed_at: float, activity: str | None) -> None:
        self.state_id = state_id
        self.observed_at = observed_at
        self.activity = activity


class RestorationTest(unittest.TestCase):
    BEFORE = _Observation("1" * 64, 100.0, ACTIVITY)

    def _proof(self, **overrides) -> RestorationProof:
        values = {
            "before": self.BEFORE,
            "after": _Observation("2" * 64, 105.0, ACTIVITY),
            "dispatched_at": 101.0,
            "executed": True,
            "aut_package": PACKAGE,
            "observation_is_current": True,
            "candidate_count": 3,
        }
        values.update(overrides)
        return evaluate_restoration(**values)

    def test_an_owned_usable_fresh_frame_is_restoration(self) -> None:
        proof = self._proof()
        self.assertTrue(proof.restored)
        self.assertEqual(proof.reason, "owned_usable_state")
        self.assertTrue(proof.safe_to_continue)

    def test_an_unexecuted_action_proves_nothing(self) -> None:
        proof = self._proof(executed=False)
        self.assertFalse(proof.restored)
        self.assertEqual(proof.reason, "action_not_executed")

    def test_a_frame_that_is_not_the_live_one_is_unsafe(self) -> None:
        proof = self._proof(observation_is_current=False)
        self.assertEqual(proof.reason, "observation_not_current")
        self.assertFalse(proof.safe_to_continue)

    def test_a_frame_older_than_the_dispatch_is_unsafe(self) -> None:
        proof = self._proof(after=_Observation("2" * 64, 100.5, ACTIVITY))
        self.assertEqual(proof.reason, "observation_not_fresh")
        self.assertFalse(proof.safe_to_continue)

    def test_leaving_the_app_is_not_restoration_but_is_still_live(self) -> None:
        """Usability may honestly fail; that stops the mechanism, not the run."""

        proof = self._proof(after=_Observation("2" * 64, 105.0, "com.other/.Main"))
        self.assertFalse(proof.restored)
        self.assertEqual(proof.reason, "left_the_app")
        self.assertTrue(proof.safe_to_continue)

    def test_an_owned_screen_with_nothing_to_do_is_not_restoration(self) -> None:
        proof = self._proof(candidate_count=0)
        self.assertEqual(proof.reason, "no_interactive_elements")
        self.assertTrue(proof.safe_to_continue)

    def test_an_unparsable_activity_is_not_owned(self) -> None:
        proof = self._proof(after=_Observation("2" * 64, 105.0, None))
        self.assertEqual(proof.reason, "left_the_app")

    def test_a_dispatch_failure_proof_claims_nothing(self) -> None:
        proof = dispatch_failure_proof()
        self.assertFalse(proof.restored)
        self.assertEqual(proof.reason, "dispatch_failed")
        self.assertFalse(proof.safe_to_continue)
        self.assertIsNone(proof.after_state_id)


def _record(**overrides) -> VisualEscapeRecord:
    values = {
        "escape_sequence": 1,
        "status": "restored",
        "trigger": "screen_exhausted",
        "state_id": "1" * 64,
        "activity": ACTIVITY,
        "observation_sequence": 4,
        "hierarchy_sha256": "a" * 64,
        "screenshot_sha256": "b" * 64,
        "deterministic_candidates": 0,
        "offered_elements": 2,
        "failed_rungs": (1, 2, 3),
        "ladder_exhausted": True,
        "secrets_present": False,
        "screenshots_suppressed": False,
        "escape_sequence_budget": 2,
        "escapes_spent": 1,
        "model_calls_recorded": 2,
        "model_call_id": "m-1",
        "model_purpose": VISUAL_ESCAPE,
        "prompt": "prompt text",
        "response": '{"element_id": "e", "kind": "tap"}',
        "action_kind": "tap",
        "action_target_sha256": "c" * 64,
        "attempt_id": "a-1",
        "restoration": RestorationProof(
            restored=True,
            reason="owned_usable_state",
            observation_current=True,
            observation_fresh=True,
            package_owned=True,
            after_state_id="2" * 64,
            after_activity=ACTIVITY,
            after_observed_at=105.0,
            after_candidates=3,
        ),
    }
    values.update(overrides)
    return VisualEscapeRecord(**values)


class RecordShapeTest(unittest.TestCase):
    def test_a_restored_record_serializes_to_the_fixed_schema(self) -> None:
        details = _record().to_details()
        self.assertEqual(details["rule"], VISUAL_ESCAPE_EVIDENCE_RULE)
        self.assertTrue(details["restored"])
        self.assertEqual(details["restoration_reason"], "owned_usable_state")
        # Digests, not text: the prompt and reply live in model_calls.jsonl.
        self.assertEqual(
            details["prompt_sha256"], hashlib.sha256(b"prompt text").hexdigest()
        )
        self.assertNotIn("prompt", details)

    def test_a_screenshot_may_not_be_sent_when_redaction_is_unproven(self) -> None:
        """The record refuses to exist, so no code path can write it."""

        for flag in ("secrets_present", "screenshots_suppressed"):
            with self.subTest(flag=flag):
                with self.assertRaises(ValueError) as caught:
                    _record(**{flag: True})
                self.assertIn("redaction is unproven", str(caught.exception))

    def test_a_refusal_carries_no_call_no_attempt_and_no_proof(self) -> None:
        record = _record(
            status="refused",
            refusal_reason="no_offerable_element",
            model_call_id=None,
            model_purpose=None,
            prompt=None,
            response=None,
            action_kind=None,
            action_target_sha256=None,
            attempt_id=None,
            restoration=None,
        )
        details = record.to_details()
        self.assertEqual(details["status"], "refused")
        self.assertFalse(details["restored"])
        self.assertIsNone(details["model_call_id"])
        self.assertIsNone(details["after_state_id"])

    def test_a_refusal_reason_belongs_to_exactly_a_refused_record(self) -> None:
        with self.assertRaises(ValueError):
            _record(refusal_reason="no_offerable_element")
        with self.assertRaises(ValueError):
            _record(status="refused", refusal_reason=None)

    def test_an_unsupported_status_or_refusal_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _record(status="worked_i_think")
        with self.assertRaises(ValueError):
            _record(status="refused", refusal_reason="vibes")

    def test_the_proof_cannot_contradict_the_status(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _record(status="restoration_unverified")
        self.assertIn("contradicts", str(caught.exception))

    def test_only_a_committed_step_names_an_attempt(self) -> None:
        with self.assertRaises(ValueError):
            _record(status="model_refused", attempt_id="a-1", restoration=None)


class GatewayTest(unittest.TestCase):
    """The screenshot informs the choice and can never widen it."""

    def setUp(self) -> None:
        self.config = ModelConfig(
            schema_version=1,
            enabled=True,
            provider="openai_compatible",
            model="fixture-model",
            base_url="https://model.invalid",
            max_calls_per_run=4,
            max_calls_per_purpose=4,
        )
        self.elements = escape_offers(
            offerable_elements(
                _nodes(
                    _node(0, resource=f"{PACKAGE}:id/go", text="Go"),
                    _node(1, resource=f"{PACKAGE}:id/list", scrollable="true"),
                ),
                package=PACKAGE,
            )
        )
        self.first = self.elements[0]["element_id"]

    def _gateway(self, *replies, supports_image: bool = True) -> ModelGateway:
        provider = RecordingProvider(
            self.config, tuple(replies), supports_image=supports_image
        )
        self.provider = provider
        return ModelGateway(self.config, provider, environment={})

    def _select(self, gateway: ModelGateway):
        return gateway.select_visual_escape_action(
            elements=self.elements,
            screenshot_png=PNG,
            state_id="1" * 64,
            activity=ACTIVITY,
            failed_rungs=(1, 2),
        )

    def test_an_offered_choice_is_accepted_and_carries_the_frame(self) -> None:
        gateway = self._gateway(
            ModelResponse(json.dumps({"element_id": self.first, "kind": "tap"}), 10, 5)
        )
        outcome = self._select(gateway)
        self.assertEqual(outcome.value, (self.first, "tap"))
        self.assertEqual(self.provider.images, [PNG])
        self.assertEqual(gateway.calls_for(VISUAL_ESCAPE), 1)

    def test_an_element_that_was_not_offered_is_refused(self) -> None:
        gateway = self._gateway(
            ModelResponse(json.dumps({"element_id": "e" * 64, "kind": "tap"}), 10, 5)
        )
        outcome = self._select(gateway)
        self.assertIsInstance(outcome, PendingModelCall)
        self.assertIn("not offered", outcome.error)

    def test_a_kind_the_element_does_not_support_is_refused(self) -> None:
        gateway = self._gateway(
            ModelResponse(
                json.dumps({"element_id": self.first, "kind": "scroll_forward"}), 10, 5
            )
        )
        outcome = self._select(gateway)
        self.assertIsInstance(outcome, PendingModelCall)
        self.assertIn("does not support", outcome.error)

    def test_a_reply_that_is_not_json_is_refused(self) -> None:
        gateway = self._gateway(ModelResponse("I would tap the blue button", 10, 5))
        outcome = self._select(gateway)
        self.assertIsInstance(outcome, PendingModelCall)
        self.assertIn("JSON object", outcome.error)

    def test_a_provider_failure_is_recorded_and_charged(self) -> None:
        gateway = self._gateway(ModelProviderError("endpoint refused"))
        outcome = self._select(gateway)
        self.assertIsInstance(outcome, PendingModelCall)
        self.assertIn("endpoint refused", outcome.error)
        # Charged at dispatch, so a failure cannot be retried for free.
        self.assertEqual(gateway.calls_for(VISUAL_ESCAPE), 1)

    def test_a_text_only_provider_refuses_before_spending_a_call(self) -> None:
        """A prose description of a screen is not a visual decision."""

        gateway = self._gateway(
            ModelResponse("unused", 1, 1), supports_image=False
        )
        self.assertFalse(gateway.can_carry_screenshot())
        with self.assertRaises(ModelProviderError):
            self._select(gateway)
        self.assertEqual(gateway.calls_for(VISUAL_ESCAPE), 0)
        self.assertEqual(self.provider.images, [])

    def test_the_visual_purpose_has_its_own_budget(self) -> None:
        gateway = self._gateway(
            ModelResponse(json.dumps({"element_id": self.first, "kind": "tap"}), 1, 1)
        )
        self._select(gateway)
        from valordroid.llm.config import RECOVERY_ACTION

        self.assertEqual(gateway.calls_for(VISUAL_ESCAPE), 1)
        self.assertEqual(gateway.calls_for(RECOVERY_ACTION), 0)

    def test_an_empty_offer_list_is_a_programming_error(self) -> None:
        gateway = self._gateway(ModelResponse("{}", 1, 1))
        with self.assertRaises(ValueError):
            gateway.select_visual_escape_action(
                elements=(),
                screenshot_png=PNG,
                state_id="1" * 64,
                activity=ACTIVITY,
                failed_rungs=(),
            )

    def test_the_prompt_is_reconstructible_offline(self) -> None:
        gateway = self._gateway(
            ModelResponse(json.dumps({"element_id": self.first, "kind": "tap"}), 1, 1)
        )
        self._select(gateway)
        rebuilt = build_visual_escape_prompt(
            elements=self.elements,
            state_id="1" * 64,
            activity=ACTIVITY,
            failed_rungs=(1, 2),
        )
        self.assertEqual(self.provider.prompts, [rebuilt])
        self.assertIn("You cannot name coordinates.", rebuilt)
        self.assertIn(self.first, rebuilt)

    def test_the_choice_decoder_matches_the_online_protocol(self) -> None:
        self.assertEqual(
            visual_escape_choice_from_response('{"element_id": "e", "kind": "tap"}'),
            ("e", "tap"),
        )
        for bad in ("", "{}", '{"element_id": "e"}', '{"element_id": 1, "kind": "tap"}'):
            with self.subTest(bad=bad):
                self.assertIsNone(visual_escape_choice_from_response(bad))


class ProviderImageTest(unittest.TestCase):
    def test_a_provider_refuses_an_image_unless_it_implements_one(self) -> None:
        from valordroid.llm.providers import ModelProvider

        provider = ModelProvider(ModelConfig())
        self.assertFalse(provider.supports_image)
        with self.assertRaises(ModelProviderError):
            provider.complete_with_image("prompt", PNG)

    def test_the_chat_completions_shape_declares_image_support(self) -> None:
        from valordroid.llm.providers import OpenAICompatibleProvider

        self.assertTrue(OpenAICompatibleProvider.supports_image)

    def test_a_non_png_or_oversized_frame_is_refused(self) -> None:
        from valordroid.llm.providers import OpenAICompatibleProvider

        config = ModelConfig(
            schema_version=1,
            enabled=True,
            provider="openai_compatible",
            model="m",
            base_url="https://model.invalid",
        )
        provider = OpenAICompatibleProvider(config, {})
        for payload in (b"", b"not-a-png", b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024)):
            with self.subTest(size=len(payload)):
                with self.assertRaises(ModelProviderError):
                    provider.complete_with_image("prompt", payload)


class VertexImageTest(unittest.TestCase):
    """Vertex is the only provider reachable by application default credentials.

    While it declared no image support, a campaign authenticated that way could
    budget a visual escape and never once send a screen, because the gateway
    correctly refuses rather than answering from text. `generateContent` carries
    bytes in an `inlineData` part, so the capability was missing, not impossible.
    """

    def _provider(self):
        from valordroid.llm.providers import VertexAiProvider

        config = ModelConfig(
            schema_version=1,
            enabled=True,
            provider="vertex_ai",
            model="gemini-2.5-flash",
            base_url="https://us-central1-aiplatform.googleapis.com",
            project="p",
            region="us-central1",
        )
        provider = VertexAiProvider(config, {"VERTEX_ACCESS_TOKEN": "token"})
        posted: list[dict] = []

        def capture(url, headers, body):
            posted.append({"url": url, "body": body})
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
            }

        provider._post_json = capture
        return provider, posted

    def test_the_generate_content_shape_declares_image_support(self) -> None:
        from valordroid.llm.providers import VertexAiProvider

        self.assertTrue(VertexAiProvider.supports_image)

    def test_the_screenshot_travels_as_an_inline_data_part(self) -> None:
        import base64

        provider, posted = self._provider()
        response = provider.complete_with_image("look", PNG)

        self.assertEqual(response.text, "ok")
        self.assertEqual(len(posted), 1)
        parts = posted[0]["body"]["contents"][0]["parts"]
        self.assertEqual(parts[0], {"text": "look"})
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(
            base64.b64decode(parts[1]["inlineData"]["data"]),
            PNG,
            "the bytes on the wire are not the frame the caller passed",
        )

    def test_a_text_only_call_carries_no_image_part(self) -> None:
        provider, posted = self._provider()
        provider.complete("just text")
        parts = posted[0]["body"]["contents"][0]["parts"]
        self.assertEqual(parts, [{"text": "just text"}])

    def test_the_same_frame_bounds_apply(self) -> None:
        provider, posted = self._provider()
        for payload in (b"", b"not-a-png", b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024)):
            with self.subTest(size=len(payload)):
                with self.assertRaises(ModelProviderError):
                    provider.complete_with_image("prompt", payload)
        self.assertEqual(posted, [], "a refused frame must not reach the network")


class ConfigurationTest(unittest.TestCase):
    def test_the_mechanism_is_off_in_a_default_configuration(self) -> None:
        self.assertEqual(CoreConfig().max_visual_escapes, 0)

    def test_a_budget_needs_model_assistance_and_a_call_budget(self) -> None:
        with self.assertRaises(ValueError):
            CoreConfig.from_mapping({"max_visual_escapes": 1})
        with self.assertRaises(ValueError) as caught:
            CoreConfig.from_mapping(
                {
                    "model_assistance_enabled": True,
                    "max_model_calls": 1,
                    "max_visual_escapes": 4,
                }
            )
        self.assertIn("cannot exceed max_model_calls", str(caught.exception))

    def test_the_budget_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            CoreConfig.from_mapping(
                {
                    "model_assistance_enabled": True,
                    "max_model_calls": 99,
                    "max_visual_escapes": MAX_VISUAL_ESCAPES + 1,
                }
            )

    def _runtime(self, **overrides) -> dict:
        values = {
            "schema_version": RuntimeConfig.SCHEMA_VERSION,
            "serial": "127.0.0.1:5555",
            "launcher": f"{PACKAGE}/.MainActivity",
            "max_actions": 4,
            "max_seconds": 60.0,
            "action_timeout_seconds": 5.0,
            "adb_timeout_seconds": 5.0,
            "capture_screenshots": True,
            "install_timeout_seconds": 10.0,
            "launch_timeout_seconds": 5.0,
            "observation_timeout_seconds": 5.0,
            "route_foreground_timeout_seconds": 2.0,
            "per_field_input_enabled": False,
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

    def test_a_historical_configuration_without_the_flag_still_loads(self) -> None:
        runtime = RuntimeConfig.from_mapping(self._runtime())
        self.assertFalse(runtime.visual_escape_enabled)
        # Off means absent, so a historical file serializes byte-identically.
        self.assertNotIn("visual_escape_enabled", runtime.to_dict())

    def test_enabling_it_enters_the_serialized_configuration(self) -> None:
        runtime = RuntimeConfig.from_mapping(
            self._runtime(visual_escape_enabled=True)
        )
        self.assertTrue(runtime.to_dict()["visual_escape_enabled"])

    def test_it_cannot_be_enabled_without_screenshots(self) -> None:
        """Its whole input is a retained frame."""

        with self.assertRaises(ValueError) as caught:
            RuntimeConfig.from_mapping(
                self._runtime(visual_escape_enabled=True, capture_screenshots=False)
            )
        self.assertIn("requires capture_screenshots", str(caught.exception))


class _Runtime:
    def __init__(self, enabled: bool) -> None:
        self.visual_escape_enabled = enabled


class _Core:
    def __init__(self, budget: int) -> None:
        self.max_visual_escapes = budget


def _lifecycle(details: dict, **overrides) -> LifecycleRecord:
    values = {
        "record_id": "r-1",
        "observed_at": 1.0,
        "phase": "recovery",
        "event": VISUAL_ESCAPE_EVENT,
        "status": LifecycleStatus.INFO,
        "details": details,
        "error": None,
    }
    values.update(overrides)
    return LifecycleRecord(**values)


class ValidatorTest(unittest.TestCase):
    """Offline validation re-derives the decision without a device."""

    def _codes(
        self,
        records,
        *,
        enabled: bool = True,
        budget: int = 2,
        model_calls=("m-1",),
        attempts=("a-1",),
    ) -> list[str]:
        issues: list = []
        _validate_visual_escape(
            issues,
            runtime=_Runtime(enabled),
            config=_Core(budget),
            lifecycle=records,
            model_calls=set(model_calls),
            attempts=set(attempts),
        )
        return [issue.code for issue in issues]

    def test_a_well_formed_restored_record_is_accepted(self) -> None:
        self.assertEqual(self._codes([_lifecycle(_record().to_details())]), [])

    def test_evidence_cannot_exist_while_the_mechanism_is_off(self) -> None:
        details = _record().to_details()
        self.assertIn(
            "visual_escape_unbound", self._codes([_lifecycle(details)], enabled=False)
        )
        self.assertIn(
            "visual_escape_unbound", self._codes([_lifecycle(details)], budget=0)
        )

    def test_a_record_claiming_a_call_while_a_secret_was_held_is_rejected(self) -> None:
        # The producer cannot build this, so it is forged by hand: validation
        # must reject it independently rather than trusting the producer.
        details = _record().to_details()
        details["secrets_present"] = True
        self.assertIn(
            "visual_escape_evidence_binding", self._codes([_lifecycle(details)])
        )

    def test_a_call_naming_an_unretained_model_call_is_rejected(self) -> None:
        self.assertIn(
            "visual_escape_evidence_binding",
            self._codes([_lifecycle(_record().to_details())], model_calls=()),
        )

    def test_an_attempt_the_run_never_committed_is_rejected(self) -> None:
        self.assertIn(
            "visual_escape_evidence_binding",
            self._codes([_lifecycle(_record().to_details())], attempts=()),
        )

    def test_restoration_must_be_owned_and_usable(self) -> None:
        for name, value in (
            ("after_package_owned", False),
            ("after_candidates", 0),
            ("after_state_id", None),
        ):
            with self.subTest(name=name):
                details = _record().to_details()
                details[name] = value
                self.assertIn(
                    "visual_escape_evidence_binding",
                    self._codes([_lifecycle(details)]),
                )

    def test_a_record_must_claim_an_exhausted_ladder_and_a_real_trigger(self) -> None:
        for name, value in (
            ("ladder_exhausted", False),
            ("trigger", "temporarily_no_eligible"),
        ):
            with self.subTest(name=name):
                details = _record().to_details()
                details[name] = value
                self.assertIn(
                    "visual_escape_evidence_binding",
                    self._codes([_lifecycle(details)]),
                )

    def test_an_unknown_key_or_missing_key_is_rejected(self) -> None:
        details = _record().to_details()
        details["extra"] = 1
        self.assertIn(
            "visual_escape_evidence_binding", self._codes([_lifecycle(details)])
        )
        pruned = _record().to_details()
        del pruned["restored"]
        self.assertIn(
            "visual_escape_evidence_binding", self._codes([_lifecycle(pruned)])
        )

    def test_sequences_must_advance(self) -> None:
        details = _record().to_details()
        self.assertIn(
            "visual_escape_evidence_binding",
            self._codes([_lifecycle(details), _lifecycle(dict(details))]),
        )

    def test_a_record_must_agree_with_the_configured_budget(self) -> None:
        self.assertIn(
            "visual_escape_budget_unbound",
            self._codes([_lifecycle(_record().to_details())], budget=1),
        )

    def test_more_escapes_than_the_budget_are_rejected(self) -> None:
        first = _record().to_details()
        second = _record(escape_sequence=2, escapes_spent=2).to_details()
        second["model_call_id"] = "m-2"
        second["attempt_id"] = "a-2"
        codes = self._codes(
            [_lifecycle(first), _lifecycle(second)],
            budget=1,
            model_calls=("m-1", "m-2"),
            attempts=("a-1", "a-2"),
        )
        self.assertIn("visual_escape_budget_exceeded", codes)

    def test_a_wrong_phase_or_status_is_rejected(self) -> None:
        details = _record().to_details()
        self.assertIn(
            "visual_escape_evidence_binding",
            self._codes([_lifecycle(details, phase="exploration")]),
        )
        self.assertIn(
            "visual_escape_evidence_binding",
            self._codes([_lifecycle(details, status=LifecycleStatus.SUCCEEDED)]),
        )

    def test_a_refusal_record_is_accepted_without_a_call(self) -> None:
        record = _record(
            status="refused",
            refusal_reason="secrets_present",
            secrets_present=True,
            model_call_id=None,
            model_purpose=None,
            prompt=None,
            response=None,
            action_kind=None,
            action_target_sha256=None,
            attempt_id=None,
            restoration=None,
        )
        self.assertEqual(self._codes([_lifecycle(record.to_details())]), [])


if __name__ == "__main__":
    unittest.main()
