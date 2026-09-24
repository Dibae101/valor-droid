"""TypeSafe Jev tarpit verdict: the Noul question behind the divert decision.

Jev answers one Noul question -- "is this run stuck in a tarpit?" -- with a
0-1 verdict the runner uses to divert immediately instead of waiting out the
forced-sweep pacing gap. These tests pin that contract without any network
request:

- the provider posts a single noul-typed question to /v1/systemone and parses
  the documented wire shape, rejecting anything else;
- the gateway spends recovery-action budget on the check and returns the call
  record beside the verdict, with None for the verdict on any failure;
- the new config knobs validate like the existing Jev knob;
- anything but the Jev provider refuses the tarpit check loudly.

No test here performs a network request.
"""

from __future__ import annotations

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
    ModelGateway,
    PendingModelCall,
)
from valordroid.llm.providers import (
    JevNoul,
    ModelProviderError,
    TypeSafeJevProvider,
    _parse_jev_noul,
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


def _document(noul: Any = 0.87, **overrides: Any) -> dict[str, Any]:
    answer: dict[str, Any] = {"type": "noul", "noul": noul}
    answer.update(overrides)
    return {
        "model": "jev-1.13.0",
        "answers": {"tarpit_check": answer},
        "usage": {"input_tokens": 200, "output_tokens": 9},
    }


class StubNoulProvider(TypeSafeJevProvider):
    """Answers Noul questions from a script, recording what it was asked."""

    def __init__(
        self,
        config: ModelConfig,
        replies: tuple[JevNoul | ModelProviderError, ...],
        environment: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(config, environment)
        self.replies = list(replies)
        self.seen: list[dict[str, Any]] = []

    def decide_noul(
        self,
        *,
        state: Mapping[str, Any],
        instructions: str,
    ) -> JevNoul:
        self.seen.append({"state": dict(state), "instructions": instructions})
        reply = self.replies.pop(0)
        if isinstance(reply, ModelProviderError):
            raise reply
        return reply


def _verdict(noul: float = 0.87) -> JevNoul:
    return JevNoul(
        noul=noul,
        model="jev-1.13.0",
        input_tokens=200,
        output_tokens=9,
    )


def _gateway(*replies: JevNoul | ModelProviderError, **overrides: Any):
    config = _config(**overrides)
    provider = StubNoulProvider(config, tuple(replies), _ENV)
    return ModelGateway(config, provider, environment=_ENV), provider


def _check_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "state_id": "s1",
        "activity": "com.example.Main",
        "seconds_since_gain": 120.5,
        "actions_since_gain": 40,
        "zero_gain_stalls": 2,
        "recent_state_ids": ["s1", "s2", "s1"],
        "unentered_targets": 5,
    }
    args.update(overrides)
    return args


class ParseNoulTests(unittest.TestCase):
    def test_valid_document_parses(self) -> None:
        verdict = _parse_jev_noul(_document(), question="tarpit_check")
        self.assertAlmostEqual(verdict.noul, 0.87)
        self.assertEqual(verdict.model, "jev-1.13.0")
        self.assertEqual(verdict.input_tokens, 200)
        self.assertEqual(verdict.output_tokens, 9)

    def test_boundary_verdicts_parse(self) -> None:
        self.assertAlmostEqual(
            _parse_jev_noul(_document(noul=0.0), question="tarpit_check").noul, 0.0
        )
        self.assertAlmostEqual(
            _parse_jev_noul(_document(noul=1), question="tarpit_check").noul, 1.0
        )

    def test_wrong_answer_type_rejected(self) -> None:
        with self.assertRaises(ModelProviderError):
            _parse_jev_noul(_document(type="choice"), question="tarpit_check")

    def test_missing_noul_rejected(self) -> None:
        document = _document()
        del document["answers"]["tarpit_check"]["noul"]
        with self.assertRaises(ModelProviderError):
            _parse_jev_noul(document, question="tarpit_check")

    def test_out_of_range_noul_rejected(self) -> None:
        with self.assertRaises(ModelProviderError):
            _parse_jev_noul(_document(noul=1.5), question="tarpit_check")
        with self.assertRaises(ModelProviderError):
            _parse_jev_noul(_document(noul="high"), question="tarpit_check")

    def test_non_object_rejected(self) -> None:
        with self.assertRaises(ModelProviderError):
            _parse_jev_noul([], question="tarpit_check")  # type: ignore[arg-type]

    def test_missing_usage_is_not_an_error(self) -> None:
        document = _document()
        del document["usage"]
        verdict = _parse_jev_noul(document, question="tarpit_check")
        self.assertIsNone(verdict.input_tokens)
        self.assertIsNone(verdict.output_tokens)


class DecideNoulTests(unittest.TestCase):
    def test_request_shape_matches_the_documented_api(self) -> None:
        config = _config()
        seen: dict[str, Any] = {}

        class CaptureProvider(TypeSafeJevProvider):
            def _post_json(self, url: str, headers: Any, payload: Any) -> Any:  # type: ignore[override]
                seen["url"] = url
                seen["headers"] = headers
                seen["payload"] = payload
                return _document()

        provider = CaptureProvider(config, _ENV)
        verdict = provider.decide_noul(
            state={"screen": "s1"}, instructions="is this a tarpit?"
        )
        self.assertAlmostEqual(verdict.noul, 0.87)
        self.assertTrue(seen["url"].endswith("/systemone"))
        self.assertEqual(
            seen["headers"]["Authorization"], "Bearer test-key"
        )
        question = seen["payload"]["questions"]["tarpit_check"]
        self.assertEqual(question["type"], "noul")
        self.assertEqual(question["instructions"], "is this a tarpit?")
        self.assertEqual(seen["payload"]["model"], "jev-latest")


class ConfigKnobTests(unittest.TestCase):
    def test_tarpit_knob_defaults(self) -> None:
        config = _config()
        self.assertAlmostEqual(config.typesafe_jev_tarpit_threshold, 0.7)
        self.assertEqual(config.typesafe_jev_tarpit_min_stalls, 2)

    def test_tarpit_threshold_must_be_a_probability(self) -> None:
        with self.assertRaises(ModelConfigError):
            _config(typesafe_jev_tarpit_threshold=1.5)
        with self.assertRaises(ModelConfigError):
            _config(typesafe_jev_tarpit_threshold=-0.1)
        with self.assertRaises(ModelConfigError):
            _config(typesafe_jev_tarpit_threshold="high")  # type: ignore[arg-type]

    def test_tarpit_min_stalls_must_be_a_positive_integer(self) -> None:
        with self.assertRaises(ModelConfigError):
            _config(typesafe_jev_tarpit_min_stalls=0)
        with self.assertRaises(ModelConfigError):
            _config(typesafe_jev_tarpit_min_stalls=1.5)  # type: ignore[arg-type]

    def test_build_provider_still_constructs_the_jev_provider(self) -> None:
        provider = build_provider(_config(), _ENV)
        self.assertIsInstance(provider, TypeSafeJevProvider)


class CheckTarpitTests(unittest.TestCase):
    def test_verdict_returned_beside_the_call_record(self) -> None:
        gateway, provider = _gateway(_verdict(0.87))
        call, noul = gateway.check_tarpit(**_check_args())
        self.assertIsInstance(call, PendingModelCall)
        self.assertAlmostEqual(noul, 0.87)  # type: ignore[arg-type]
        self.assertEqual(call.purpose, RECOVERY_ACTION)
        self.assertEqual(call.provider, TYPESAFE_JEV)
        self.assertIsNone(call.error)
        # The stall shape reaches the engine: episode count and unentered
        # targets are what separate a tarpit from a plateau.
        asked = provider.seen[0]
        self.assertEqual(
            asked["state"]["stall"]["consecutive_zero_gain_stall_episodes"], 2
        )
        self.assertEqual(asked["state"]["stall"]["unentered_sweep_targets"], 5)

    def test_transport_failure_is_a_recorded_none_verdict(self) -> None:
        gateway, _ = _gateway(ModelProviderError("boom"))
        call, noul = gateway.check_tarpit(**_check_args())
        self.assertIsNone(noul)
        self.assertIn("boom", call.error or "")

    def test_budget_is_spent_at_dispatch(self) -> None:
        gateway, _ = _gateway(_verdict(), _verdict(), _verdict())
        gateway.check_tarpit(**_check_args())
        gateway.check_tarpit(**_check_args())
        with self.assertRaises(ModelBudgetExhausted):
            gateway.check_tarpit(**_check_args())

    def test_non_jev_provider_refuses_loudly(self) -> None:
        config = _config(
            provider="openai_compatible", base_url="https://example.invalid"
        )
        env = {"OPENAI_API_KEY": "test-key"}
        gateway = ModelGateway(config, build_provider(config, env), environment=env)
        with self.assertRaises(ModelConfigError):
            gateway.check_tarpit(**_check_args())


if __name__ == "__main__":
    unittest.main()
