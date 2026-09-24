"""Bounded model assistance: budgets, refusals, and constrained answers.

The gateway exists so a model can help without being trusted. These tests pin
the three properties that make that true:
- it cannot name a widget that was not offered (the gap-3 failure mode),
- it cannot bypass field validation (the gap-4 failure mode),
- every call becomes a record, including refusals and transport failures, which
  is the evidence chain gap 12 found missing.

No test here performs a network request.
"""

from __future__ import annotations

import json
import unittest

from valordroid.llm.config import (
    DEEP_LINK,
    FIELD_VALUE,
    RECOVERY_ACTION,
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
    ModelProviderError,
    ModelResponse,
    RecordingProvider,
    build_provider,
)


def _config(**overrides) -> ModelConfig:
    values = {
        "enabled": True,
        "provider": "openai_compatible",
        "model": "test-model",
        "base_url": "https://example.invalid/v1",
        "max_calls_per_run": 4,
        "max_calls_per_purpose": 2,
        "recovery_escalation_threshold": 2,
    }
    values.update(overrides)
    return ModelConfig(**values)


def _gateway(*replies, **overrides) -> tuple[ModelGateway, RecordingProvider]:
    config = _config(**overrides)
    provider = RecordingProvider(config, tuple(replies))
    return ModelGateway(config, provider, environment={}), provider


def _reply(payload: dict) -> ModelResponse:
    return ModelResponse(json.dumps(payload), 12, 3)


_CANDIDATES = (
    {
        "candidate_id": "a" * 64,
        "kind": "tap",
        "resource_id": "com.example:id/first",
        "text": "First",
        "content_description": "",
    },
    {
        "candidate_id": "b" * 64,
        "kind": "tap",
        "resource_id": "com.example:id/second",
        "text": "Second",
        "content_description": "",
    },
)


class ConfigurationTest(unittest.TestCase):
    def test_default_configuration_is_disabled(self) -> None:
        config = ModelConfig()
        self.assertFalse(config.enabled)
        self.assertEqual(config.provider, "disabled")

    def test_enabled_configuration_requires_a_real_provider(self) -> None:
        with self.assertRaises(ModelConfigError):
            ModelConfig(enabled=True, provider="disabled", model="x")

    def test_enabled_configuration_requires_a_model_name(self) -> None:
        with self.assertRaises(ModelConfigError):
            ModelConfig(enabled=True, provider="vertex_ai", model="")

    def test_disabled_configuration_cannot_name_a_provider(self) -> None:
        with self.assertRaises(ModelConfigError):
            ModelConfig(enabled=False, provider="vertex_ai")

    def test_unknown_provider_is_refused(self) -> None:
        with self.assertRaises(ModelConfigError):
            ModelConfig(enabled=True, provider="mystery", model="x")

    def test_purpose_budget_cannot_exceed_the_run_budget(self) -> None:
        with self.assertRaises(ModelConfigError):
            ModelConfig(
                enabled=True,
                provider="vertex_ai",
                model="x",
                max_calls_per_run=2,
                max_calls_per_purpose=3,
            )

    def test_unknown_configuration_key_is_refused(self) -> None:
        with self.assertRaises(ModelConfigError):
            ModelConfig.from_mapping({"enabled": False, "mystery": 1})

    def test_credential_is_read_from_the_environment_by_name(self) -> None:
        config = _config(provider="bedrock_mantle")
        self.assertEqual(
            config.credential({"BEDROCK_API_KEY": "secret-value"}), "secret-value"
        )

    def test_missing_credential_is_an_explicit_error(self) -> None:
        with self.assertRaises(ModelConfigError):
            _config(provider="bedrock_mantle").credential({})

    def test_bedrock_endpoint_falls_back_to_the_environment(self) -> None:
        config = _config(provider="bedrock_mantle", base_url=None)
        self.assertEqual(
            config.resolved_base_url({"BEDROCK_BASE_URL": "https://host/v1/"}),
            "https://host/v1",
        )

    def test_vertex_endpoint_is_derived_from_its_region(self) -> None:
        config = _config(provider="vertex_ai", base_url=None, region="us-central1")
        self.assertEqual(
            config.resolved_base_url({}),
            "https://us-central1-aiplatform.googleapis.com",
        )

    def test_redacted_summary_never_contains_a_credential(self) -> None:
        config = _config(provider="bedrock_mantle")
        summary = config.redacted({"BEDROCK_API_KEY": "secret-value"})
        self.assertNotIn("secret-value", json.dumps(summary))

    def test_disabled_configuration_cannot_build_a_provider(self) -> None:
        with self.assertRaises(ModelConfigError):
            build_provider(ModelConfig())


class BudgetTest(unittest.TestCase):
    def test_budget_is_spent_per_purpose_and_per_run(self) -> None:
        gateway, _ = _gateway(
            _reply({"candidate_id": "a" * 64}),
            _reply({"candidate_id": "b" * 64}),
        )
        self.assertEqual(gateway.budget_remaining(RECOVERY_ACTION), 2)
        for _ in range(2):
            gateway.select_recovery_action(
                candidates=_CANDIDATES,
                state_id="s",
                activity=None,
                failed_rungs=(),
                recent_state_ids=(),
            )
        self.assertEqual(gateway.budget_remaining(RECOVERY_ACTION), 0)
        self.assertFalse(gateway.available(RECOVERY_ACTION))
        with self.assertRaises(ModelBudgetExhausted):
            gateway.select_recovery_action(
                candidates=_CANDIDATES,
                state_id="s",
                activity=None,
                failed_rungs=(),
                recent_state_ids=(),
            )

    def test_a_transport_failure_still_spends_budget(self) -> None:
        # Otherwise a broken endpoint becomes an unbounded retry loop.
        gateway, _ = _gateway(ModelProviderError("connection refused"))
        outcome = gateway.select_recovery_action(
            candidates=_CANDIDATES,
            state_id="s",
            activity=None,
            failed_rungs=(),
            recent_state_ids=(),
        )
        self.assertIsInstance(outcome, PendingModelCall)
        self.assertEqual(gateway.calls_made, 1)

    def test_other_purposes_keep_their_own_budget(self) -> None:
        gateway, _ = _gateway(
            _reply({"candidate_id": "a" * 64}),
            _reply({"candidate_id": "b" * 64}),
            _reply({"value": "abc"}),
        )
        for _ in range(2):
            gateway.select_recovery_action(
                candidates=_CANDIDATES,
                state_id="s",
                activity=None,
                failed_rungs=(),
                recent_state_ids=(),
            )
        self.assertTrue(gateway.available(FIELD_VALUE))

    def test_an_oversized_prompt_is_refused_before_dispatch(self) -> None:
        gateway, provider = _gateway(_reply({"value": "abc"}), max_prompt_characters=256)
        with self.assertRaises(ValueError):
            gateway.synthesize_field_value(
                selector={"resource_id": "x" * 4000, "class_name": "", "content_description": "", "text": ""},
                field_kind="text",
                rejected_value=None,
                rejection_reasons=(),
            )
        self.assertEqual(provider.prompts, [])


class RecoverySelectionTest(unittest.TestCase):
    def test_a_valid_choice_is_returned_with_its_call_record(self) -> None:
        gateway, _ = _gateway(_reply({"candidate_id": "b" * 64}))
        outcome = gateway.select_recovery_action(
            candidates=_CANDIDATES,
            state_id="s",
            activity="com.example/.Main",
            failed_rungs=(2, 4),
            recent_state_ids=("s",),
        )
        self.assertIsInstance(outcome, ModelDecision)
        self.assertEqual(outcome.value, "b" * 64)
        record = outcome.call.record(produced_attempt_id=None)
        self.assertEqual(record.purpose, RECOVERY_ACTION)
        self.assertEqual(record.prompt_tokens, 12)
        self.assertIsNone(record.error)

    def test_a_candidate_outside_the_offered_set_is_refused(self) -> None:
        gateway, _ = _gateway(_reply({"candidate_id": "f" * 64}))
        outcome = gateway.select_recovery_action(
            candidates=_CANDIDATES,
            state_id="s",
            activity=None,
            failed_rungs=(),
            recent_state_ids=(),
        )
        self.assertIsInstance(outcome, PendingModelCall)
        self.assertIn("not offered", outcome.error or "")

    def test_non_json_prose_is_refused(self) -> None:
        gateway, _ = _gateway(ModelResponse("I think you should tap the first one", 5, 5))
        outcome = gateway.select_recovery_action(
            candidates=_CANDIDATES,
            state_id="s",
            activity=None,
            failed_rungs=(),
            recent_state_ids=(),
        )
        self.assertIsInstance(outcome, PendingModelCall)
        self.assertIn("JSON object", outcome.error or "")

    def test_a_fenced_json_block_is_accepted(self) -> None:
        fenced = '```json\n{"candidate_id": "%s"}\n```' % ("a" * 64)
        gateway, _ = _gateway(ModelResponse(fenced, 4, 4))
        outcome = gateway.select_recovery_action(
            candidates=_CANDIDATES,
            state_id="s",
            activity=None,
            failed_rungs=(),
            recent_state_ids=(),
        )
        self.assertIsInstance(outcome, ModelDecision)

    def test_the_prompt_only_contains_offered_candidates_and_ui_descriptors(self) -> None:
        gateway, provider = _gateway(_reply({"candidate_id": "a" * 64}))
        gateway.select_recovery_action(
            candidates=_CANDIDATES,
            state_id="state-1",
            activity="com.example/.Main",
            failed_rungs=(2,),
            recent_state_ids=("state-0",),
        )
        prompt = provider.prompts[0]
        self.assertIn("a" * 64, prompt)
        self.assertIn("com.example:id/first", prompt)
        self.assertNotIn("BEDROCK_API_KEY", prompt)

    def test_offered_candidates_are_capped_by_configuration(self) -> None:
        many = tuple(
            {
                "candidate_id": f"{index:064d}",
                "kind": "tap",
                "resource_id": f"id{index}",
                "text": "",
                "content_description": "",
            }
            for index in range(30)
        )
        gateway, provider = _gateway(
            _reply({"candidate_id": f"{0:064d}"}), max_candidates_offered=5
        )
        gateway.select_recovery_action(
            candidates=many,
            state_id="s",
            activity=None,
            failed_rungs=(),
            recent_state_ids=(),
        )
        self.assertNotIn(f"{9:064d}", provider.prompts[0])

    def test_no_candidates_is_a_programming_error_not_a_call(self) -> None:
        gateway, provider = _gateway(_reply({"candidate_id": "a" * 64}))
        with self.assertRaises(ValueError):
            gateway.select_recovery_action(
                candidates=(),
                state_id="s",
                activity=None,
                failed_rungs=(),
                recent_state_ids=(),
            )
        self.assertEqual(provider.prompts, [])


class FieldValueSynthesisTest(unittest.TestCase):
    def test_a_string_value_is_returned_for_the_caller_to_validate(self) -> None:
        gateway, _ = _gateway(_reply({"value": "person@example.org"}))
        outcome = gateway.synthesize_field_value(
            selector={"resource_id": "com.example:id/email", "class_name": "EditText", "content_description": "", "text": ""},
            field_kind="email",
            rejected_value="valordroid",
            rejection_reasons=("value is not an email address",),
        )
        self.assertIsInstance(outcome, ModelDecision)
        self.assertEqual(outcome.value, "person@example.org")

    def test_an_empty_value_is_refused(self) -> None:
        gateway, _ = _gateway(_reply({"value": ""}))
        outcome = gateway.synthesize_field_value(
            selector={"resource_id": "x", "class_name": "", "content_description": "", "text": ""},
            field_kind="text",
            rejected_value=None,
            rejection_reasons=(),
        )
        self.assertIsInstance(outcome, PendingModelCall)

    def test_the_rejection_reasons_reach_the_prompt(self) -> None:
        gateway, provider = _gateway(_reply({"value": "person@example.org"}))
        gateway.synthesize_field_value(
            selector={"resource_id": "com.example:id/email", "class_name": "", "content_description": "", "text": ""},
            field_kind="email",
            rejected_value="valordroid",
            rejection_reasons=("value is not an email address",),
        )
        self.assertIn("value is not an email address", provider.prompts[0])


class DeepLinkSynthesisTest(unittest.TestCase):
    def test_only_manifest_declared_schemes_survive(self) -> None:
        gateway, _ = _gateway(
            _reply(
                {
                    "uris": [
                        "myapp://open/profile",
                        "https://invented.example.com/x",
                        "not a uri",
                    ]
                }
            )
        )
        outcome = gateway.synthesize_deep_links(
            package_name="com.example.app",
            declared_schemes=("myapp",),
            known_deep_links=(),
            unreached_components=("com.example.app/.Profile",),
            wanted=3,
        )
        self.assertIsInstance(outcome, ModelDecision)
        self.assertEqual(outcome.value, ("myapp://open/profile",))

    def test_no_declared_scheme_survives_means_refusal(self) -> None:
        gateway, _ = _gateway(_reply({"uris": ["https://invented.example.com"]}))
        outcome = gateway.synthesize_deep_links(
            package_name="com.example.app",
            declared_schemes=("myapp",),
            known_deep_links=(),
            unreached_components=(),
            wanted=2,
        )
        self.assertIsInstance(outcome, PendingModelCall)
        self.assertIn("manifest-declared scheme", outcome.error or "")

    def test_synthesis_without_a_declared_scheme_is_a_programming_error(self) -> None:
        gateway, provider = _gateway(_reply({"uris": []}))
        with self.assertRaises(ValueError):
            gateway.synthesize_deep_links(
                package_name="com.example.app",
                declared_schemes=(),
                known_deep_links=(),
                unreached_components=(),
                wanted=1,
            )
        self.assertEqual(provider.prompts, [])

    def test_result_is_capped_at_the_requested_count(self) -> None:
        gateway, _ = _gateway(
            _reply({"uris": ["myapp://a", "myapp://b", "myapp://c"]})
        )
        outcome = gateway.synthesize_deep_links(
            package_name="com.example.app",
            declared_schemes=("myapp",),
            known_deep_links=(),
            unreached_components=(),
            wanted=2,
        )
        self.assertEqual(len(outcome.value), 2)


class ProviderContractTest(unittest.TestCase):
    def test_missing_token_counts_require_an_explicit_reason(self) -> None:
        with self.assertRaises(ValueError):
            ModelResponse("text", None, 4)
        self.assertIsNotNone(
            ModelResponse("text", None, 4, "provider omitted usage").token_unavailable_reason
        )

    def test_a_call_without_tokens_records_why(self) -> None:
        gateway, _ = _gateway(
            ModelResponse(
                json.dumps({"candidate_id": "a" * 64}), None, None, "no usage block"
            )
        )
        outcome = gateway.select_recovery_action(
            candidates=_CANDIDATES,
            state_id="s",
            activity=None,
            failed_rungs=(),
            recent_state_ids=(),
        )
        record = outcome.call.record()
        self.assertIsNone(record.prompt_tokens)
        self.assertEqual(record.token_unavailable_reason, "no usage block")

    def test_plain_http_endpoint_is_refused(self) -> None:
        config = _config(base_url="http://insecure.invalid/v1")
        provider = build_provider(config, {"OPENAI_API_KEY": "k"})
        with self.assertRaises(ModelProviderError) as caught:
            provider.complete("hello")
        self.assertIn("must be HTTPS", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
