"""TypeSafe Jev decision engine wired into recovery action selection.

Jev is not a chat model: it answers one Choice question over caller-supplied
options. These tests pin that contract without any network request:

- the gateway offers exactly the verified candidate ids plus one explicit
  abstention, and accepts nothing else back;
- an abstention, an unknown id, or confidence below the configured threshold
  is a recorded refusal that leaves the deterministic explorer in charge;
- every Jev call becomes a call record, including transport failures;
- free-text purposes cannot use the engine: they fail loudly at the provider.

No test here performs a network request.
"""

from __future__ import annotations

import json
import unittest
from typing import Any, Mapping

from valordroid.llm.config import (
    RECOVERY_ACTION,
    TYPESAFE_JEV,
    ModelConfig,
    ModelConfigError,
)
from valordroid.llm.gateway import (
    ModelBudgetExhausted,
    ModelDecision,
    ModelGateway,
    PendingModelCall,
)
from valordroid.llm.providers import (
    JevChoice,
    ModelProviderError,
    TypeSafeJevProvider,
    _parse_jev_choice,
    build_provider,
)


def _config(**overrides: Any) -> ModelConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "provider": TYPESAFE_JEV,
        "model": "jev-latest",
        "base_url": "https://example.invalid/v1",
        "max_calls_per_run": 4,
        "max_calls_per_purpose": 2,
        "recovery_escalation_threshold": 2,
    }
    values.update(overrides)
    return ModelConfig(**values)


_ENV = {"TYPESAFE_API_KEY": "test-key"}


class StubJevProvider(TypeSafeJevProvider):
    """Answers Choice questions from a script, recording what it was asked."""

    def __init__(
        self,
        config: ModelConfig,
        replies: tuple[JevChoice | ModelProviderError, ...],
        environment: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(config, environment)
        self.replies = list(replies)
        self.seen: list[dict[str, Any]] = []

    def decide_choice(
        self,
        *,
        state: Mapping[str, Any],
        instructions: str,
        criteria: Mapping[str, str],
    ) -> JevChoice:
        self.seen.append(
            {"state": dict(state), "instructions": instructions, "criteria": dict(criteria)}
        )
        reply = self.replies.pop(0)
        if isinstance(reply, ModelProviderError):
            raise reply
        return reply


def _gateway(*replies: JevChoice | ModelProviderError, **overrides: Any):
    config = _config(**overrides)
    provider = StubJevProvider(config, tuple(replies), _ENV)
    return ModelGateway(config, provider, environment=_ENV), provider


def _answer(
    choice: str,
    confidence: float = 0.9,
    options: tuple[str, ...] = ("c1", "c2"),
) -> JevChoice:
    probabilities = {option: (1.0 if option == choice else 0.0) for option in options}
    return JevChoice(
        choice=choice,
        probabilities=probabilities,
        confidence=confidence,
        model="jev-1.13.0",
        input_tokens=120,
        output_tokens=12,
    )


def _candidates() -> list[dict[str, Any]]:
    return [
        {"candidate_id": "c1", "kind": "tap", "class_name": "Button", "text": "Open"},
        {"candidate_id": "c2", "kind": "tap", "class_name": "Button", "text": "Close"},
    ]


class ConfigTests(unittest.TestCase):
    def test_typesafe_jev_is_a_supported_provider(self) -> None:
        config = _config()
        self.assertEqual(config.provider, TYPESAFE_JEV)
        self.assertEqual(config.credential(_ENV), "test-key")

    def test_credential_is_read_from_typesafe_env_name(self) -> None:
        with self.assertRaises(ModelConfigError):
            _config().credential({})

    def test_default_endpoint_is_the_hosted_systemone_api(self) -> None:
        config = _config(base_url=None)
        self.assertEqual(config.resolved_base_url(_ENV), "https://api.typesafe.ai/v1")

    def test_confidence_threshold_must_be_a_probability(self) -> None:
        with self.assertRaises(ModelConfigError):
            _config(typesafe_jev_min_confidence=1.5)
        with self.assertRaises(ModelConfigError):
            _config(typesafe_jev_min_confidence=-0.1)
        with self.assertRaises(ModelConfigError):
            _config(typesafe_jev_min_confidence="high")  # type: ignore[arg-type]

    def test_build_provider_constructs_the_jev_provider(self) -> None:
        provider = build_provider(_config(), _ENV)
        self.assertIsInstance(provider, TypeSafeJevProvider)
        self.assertFalse(provider.supports_image)


class ParseTests(unittest.TestCase):
    def _document(self, **overrides: Any) -> dict[str, Any]:
        answer: dict[str, Any] = {
            "type": "choice",
            "choice": "c1",
            "confidence": 0.84,
            "probabilities": {"c1": 0.84, "c2": 0.16},
        }
        answer.update(overrides)
        return {
            "model": "jev-1.13.0",
            "answers": {"recovery_action": answer},
            "usage": {"input_tokens": 100, "output_tokens": 10},
        }

    def test_valid_answer_parses_with_token_counts(self) -> None:
        parsed = _parse_jev_choice(self._document(), question="recovery_action")
        self.assertEqual(parsed.choice, "c1")
        self.assertAlmostEqual(parsed.confidence, 0.84)
        self.assertEqual(parsed.probabilities, {"c1": 0.84, "c2": 0.16})
        self.assertEqual((parsed.input_tokens, parsed.output_tokens), (100, 10))

    def test_missing_usage_leaves_token_counts_unset(self) -> None:
        document = self._document()
        del document["usage"]
        parsed = _parse_jev_choice(document, question="recovery_action")
        self.assertIsNone(parsed.input_tokens)
        self.assertIsNone(parsed.output_tokens)

    def test_malformed_answers_are_refused(self) -> None:
        bad = [
            {"answers": {}},
            {"answers": {"recovery_action": {"type": "score"}}},
            {"answers": {"recovery_action": {"type": "choice"}}},
            self._document(confidence=2.0),
            self._document(probabilities={"c1": "high"}),
            "not-an-object",
        ]
        for document in bad:
            with self.assertRaises(ModelProviderError, msg=repr(document)):
                _parse_jev_choice(document, question="recovery_action")

    def test_complete_refuses_because_jev_is_not_a_chat_model(self) -> None:
        provider = TypeSafeJevProvider(_config(), _ENV)
        with self.assertRaises(ModelProviderError):
            provider.complete("pick a candidate")


class SelectionTests(unittest.TestCase):
    def _select(self, gateway: ModelGateway, **overrides: Any):
        return gateway.select_recovery_action(
            candidates=_candidates(),
            state_id="s1",
            activity="com.example.Main",
            failed_rungs=[1, 2],
            recent_state_ids=["s0"],
            **overrides,
        )

    def test_a_confident_choice_is_returned_with_its_call_record(self) -> None:
        gateway, provider = _gateway(_answer("c2"))
        result = self._select(gateway)
        self.assertIsInstance(result, ModelDecision)
        assert isinstance(result, ModelDecision)
        self.assertEqual(result.value, "c2")
        call = result.call
        self.assertEqual(call.purpose, RECOVERY_ACTION)
        self.assertEqual(call.provider, TYPESAFE_JEV)
        self.assertIsNone(call.error)
        body = json.loads(call.response)
        self.assertEqual(body["candidate_id"], "c2")
        self.assertAlmostEqual(body["confidence"], 0.9)
        self.assertEqual(body["probabilities"]["c2"], 1.0)
        # The engine saw exactly the offered ids plus the abstention option.
        asked = provider.seen[0]
        self.assertEqual(set(asked["criteria"]) - {"other"}, {"c1", "c2"})
        self.assertIn("state_id", json.dumps(asked["state"]))
        self.assertIn("other", asked["criteria"])

    def test_an_abstention_is_a_recorded_refusal(self) -> None:
        gateway, _ = _gateway(_answer("other", options=("c1", "c2", "other")))
        result = self._select(gateway)
        self.assertIsInstance(result, PendingModelCall)
        assert isinstance(result, PendingModelCall)
        self.assertIn("abstained", result.error or "")

    def test_a_choice_outside_the_offered_set_is_refused(self) -> None:
        gateway, _ = _gateway(_answer("c9", options=("c1", "c2", "c9")))
        result = self._select(gateway)
        self.assertIsInstance(result, PendingModelCall)
        assert isinstance(result, PendingModelCall)
        self.assertIn("not offered", result.error or "")

    def test_low_confidence_is_a_recorded_refusal(self) -> None:
        gateway, _ = _gateway(_answer("c1", confidence=0.2))
        result = self._select(gateway)
        self.assertIsInstance(result, PendingModelCall)
        assert isinstance(result, PendingModelCall)
        self.assertIn("confidence", result.error or "")

    def test_a_transport_failure_is_recorded_and_spends_budget(self) -> None:
        gateway, _ = _gateway(ModelProviderError("boom"))
        result = self._select(gateway)
        self.assertIsInstance(result, PendingModelCall)
        assert isinstance(result, PendingModelCall)
        self.assertEqual(result.error, "boom")
        self.assertEqual(gateway.calls_for(RECOVERY_ACTION), 1)

    def test_budget_exhaustion_raises_before_dispatch(self) -> None:
        gateway, provider = _gateway(
            _answer("c1"), _answer("c1"), max_calls_per_purpose=1
        )
        self._select(gateway)
        with self.assertRaises(ModelBudgetExhausted):
            self._select(gateway)
        self.assertEqual(len(provider.seen), 1)

    def test_no_candidates_is_a_programming_error_not_a_call(self) -> None:
        gateway, _ = _gateway()
        with self.assertRaises(ValueError):
            gateway.select_recovery_action(
                candidates=[],
                state_id="s1",
                activity=None,
                failed_rungs=[],
                recent_state_ids=[],
            )
        self.assertEqual(gateway.calls_made, 0)

    def test_free_text_purposes_cannot_use_the_decision_engine(self) -> None:
        gateway, _ = _gateway()
        result = gateway.synthesize_field_value(
            selector={"resource_id": "f"},
            field_kind="text",
            rejected_value=None,
            rejection_reasons=[],
        )
        self.assertIsInstance(result, PendingModelCall)
        assert isinstance(result, PendingModelCall)
        self.assertIn("decision engine", result.error or "")


if __name__ == "__main__":
    unittest.main()
