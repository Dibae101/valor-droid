"""A rejected Vertex token silently cost the Gemini arm a quarter of its calls.

Measured over the 111 v2 runs, from `model_calls.jsonl`:

    Vertex calls                     22,261
    refused with HTTP 401             5,870   26.4%
    runs affected                        44   every one in the gemini arm
    Bedrock calls refused                 0

`use_application_default_credentials` was already true and `AdcTokenSource`
already refreshed on a 2,400-second timer, so expiry-by-timer was not enough on
its own. Nothing invalidated a token the provider had just been told was bad, and
`ModelGateway._invoke` records the failure and returns no response, so the loop
fell back to deterministic behaviour for those calls and the run summary still
reported a healthy `model_calls` count.

Two changes are covered here: a rejected token is dropped and re-minted once, and
the summary reports how many calls failed.
"""
from __future__ import annotations

import unittest

from valordroid.llm.config import ModelConfig
from valordroid.llm.providers import (
    AdcTokenSource,
    ModelProviderError,
    VertexAiProvider,
)


VERTEX_RAW = {
    "schema_version": 1,
    "enabled": True,
    "provider": "vertex_ai",
    "model": "gemini-2.5-flash",
    "base_url": None,
    "region": "us-central1",
    "project": "p",
    "timeout_seconds": 30.0,
    "max_calls_per_run": 10,
    "max_calls_per_purpose": 10,
    "max_prompt_characters": 8000,
    "max_candidates_offered": 15,
    "max_response_characters": 4000,
    "max_output_tokens": 256,
    "use_application_default_credentials": True,
    "recovery_escalation_threshold": 0,
    "field_retry_threshold": 1,
}


class _StubTokenSource:
    """Mints a new token string on every refresh."""

    def __init__(self) -> None:
        self._n = 0
        self._token = "token-0"
        self.mints = 0
        self.invalidations: list[str | None] = []

    def token(self) -> str:
        if self._token is None:
            self._n += 1
            self._token = f"token-{self._n}"
            self.mints += 1
        return self._token

    def invalidate(self, stale: str | None = None) -> None:
        self.invalidations.append(stale)
        if stale is None or self._token == stale:
            self._token = None


class ModelProviderErrorStatusTest(unittest.TestCase):
    def test_the_http_status_is_carried_not_only_the_message(self) -> None:
        """Telling an expired credential from a bad request must not need parsing."""
        error = ModelProviderError("provider returned HTTP 401: nope", status=401)
        self.assertEqual(error.status, 401)

    def test_a_non_http_failure_has_no_status(self) -> None:
        self.assertIsNone(ModelProviderError("timed out").status)


class AdcTokenSourceInvalidationTest(unittest.TestCase):
    def test_invalidate_forces_the_next_token_to_be_reminted(self) -> None:
        source = AdcTokenSource()
        source._token = "cached"          # noqa: SLF001 - exercising the cache
        source._fetched_at = 1.0          # noqa: SLF001
        source.invalidate()
        self.assertIsNone(source._token)  # noqa: SLF001
        self.assertEqual(source._fetched_at, 0.0)  # noqa: SLF001

    def test_invalidate_only_drops_the_token_it_was_given(self) -> None:
        """A refresh already done by a parallel run must not be thrown away."""
        source = AdcTokenSource()
        source._token = "fresh"           # noqa: SLF001
        source._fetched_at = 5.0          # noqa: SLF001
        source.invalidate("stale-from-another-worker")
        self.assertEqual(source._token, "fresh")  # noqa: SLF001


class VertexReauthenticationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ModelConfig.from_mapping(dict(VERTEX_RAW))

    def _provider(self, responses: list[object]) -> tuple[VertexAiProvider, list[str]]:
        provider = VertexAiProvider(self.config, {})
        provider._adc = _StubTokenSource()  # noqa: SLF001
        seen: list[str] = []

        def fake_post(url, headers, body):
            seen.append(headers["Authorization"].split(" ", 1)[1])
            outcome = responses.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        provider._post_json = fake_post  # type: ignore[method-assign]  # noqa: SLF001
        return provider, seen

    def test_a_401_is_retried_once_with_a_freshly_minted_token(self) -> None:
        ok = {
            "candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
        }
        provider, seen = self._provider(
            [ModelProviderError("provider returned HTTP 401: bad", status=401), ok]
        )
        response = provider.complete("hello")
        self.assertEqual(response.text, '{"ok":true}')
        self.assertEqual(
            seen,
            ["token-0", "token-1"],
            "the retry must not reuse the credential that was just refused",
        )
        self.assertEqual(provider._adc.invalidations, ["token-0"])  # noqa: SLF001

    def test_a_403_is_treated_the_same_way(self) -> None:
        ok = {
            "candidates": [{"content": {"parts": [{"text": "x"}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }
        provider, seen = self._provider(
            [ModelProviderError("provider returned HTTP 403: denied", status=403), ok]
        )
        provider.complete("hello")
        self.assertEqual(len(seen), 2)

    def test_only_one_retry_happens(self) -> None:
        """A second refusal is evidence, not grounds for a third attempt."""
        provider, seen = self._provider(
            [
                ModelProviderError("provider returned HTTP 401: bad", status=401),
                ModelProviderError("provider returned HTTP 401: still bad", status=401),
            ]
        )
        with self.assertRaises(ModelProviderError):
            provider.complete("hello")
        self.assertEqual(len(seen), 2)

    def test_a_non_auth_failure_is_not_retried(self) -> None:
        """Budgets stay exact: only an auth refusal earns a second request."""
        provider, seen = self._provider(
            [ModelProviderError("provider returned HTTP 429: slow down", status=429)]
        )
        with self.assertRaises(ModelProviderError):
            provider.complete("hello")
        self.assertEqual(len(seen), 1)

    def test_a_static_credential_is_not_retried(self) -> None:
        """With no ADC source there is nothing to re-mint, so do not double the cost."""
        raw = dict(VERTEX_RAW)
        raw["use_application_default_credentials"] = False
        provider = VertexAiProvider(
            ModelConfig.from_mapping(raw), {"VERTEX_ACCESS_TOKEN": "static"}
        )
        seen: list[str] = []

        def fake_post(url, headers, body):
            seen.append(headers["Authorization"])
            raise ModelProviderError("provider returned HTTP 401: bad", status=401)

        provider._post_json = fake_post  # type: ignore[method-assign]  # noqa: SLF001
        with self.assertRaises(ModelProviderError):
            provider.complete("hello")
        self.assertEqual(len(seen), 1)

    def test_an_unchanged_token_is_not_sent_twice(self) -> None:
        """If the refresh returns the same string, a retry would be refused too."""
        provider = VertexAiProvider(self.config, {})

        class _Fixed:
            def token(self) -> str:
                return "same"

            def invalidate(self, stale: str | None = None) -> None:
                return None

        provider._adc = _Fixed()  # type: ignore[assignment]  # noqa: SLF001
        seen: list[str] = []

        def fake_post(url, headers, body):
            seen.append(headers["Authorization"])
            raise ModelProviderError("provider returned HTTP 401: bad", status=401)

        provider._post_json = fake_post  # type: ignore[method-assign]  # noqa: SLF001
        with self.assertRaises(ModelProviderError):
            provider.complete("hello")
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
