"""End-to-end exploration against a scriptable fake device.

This is the test that makes the gap fixes credible rather than merely present:
it drives the real `AndroidRunner` through a whole run and then validates the
resulting evidence with the independent offline validator. If a mechanism
produced a record the validator cannot reconstruct, this fails.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests import fake_device
from valordroid.config import CoreConfig
from valordroid.field_values import DETERMINISTIC_VALUE_RULE, MODEL_VALUE_RULE
from valordroid.llm.config import ModelConfig
from valordroid.llm.gateway import ModelGateway
from valordroid.llm.providers import ModelResponse, RecordingProvider
from valordroid.models import stable_hash
from valordroid.prepared import (
    PROOF_SCHEMA_VERSION,
    SUPPORTED_BACKEND,
    import_prepared_bundle,
)
from valordroid.runner import AndroidRunner
from valordroid.runtime_config import RuntimeConfig
from valordroid.store import RunStore
from valordroid.summary import ACTIVITY_REACH_RULE, summarize_run
from valordroid.validator import validate_run

PACKAGE = fake_device.PACKAGE


def _prepared(work: Path, tools: dict[str, str]) -> Path:
    apk = work / "app.apk"
    apk.write_bytes(b"fake-apk-bytes-for-the-simulated-device")
    units = work / "units.json"
    units.write_text(json.dumps(sorted(fake_device.UNITS)))
    digest = stable_hash(sorted(fake_device.UNITS))
    import hashlib

    apk_sha = hashlib.sha256(apk.read_bytes()).hexdigest()
    proof = {
        "schema_version": PROOF_SCHEMA_VERSION,
        "producer": "fake-device-harness",
        "producer_version": "1",
        "original_apk_sha256": apk_sha,
        "original_package_name": PACKAGE,
        "original_version_code": "7",
        "original_version_name": "1.2.3",
        "instrumented_apk_sha256": apk_sha,
        "package_name": PACKAGE,
        "version_code": "7",
        "version_name": "1.2.3",
        "backend": SUPPORTED_BACKEND,
        "backend_version": "1.0",
        "inclusion_policy": "all-app-methods",
        "unit_count": len(fake_device.UNITS),
        "unit_ids_sha256": digest,
    }
    proof_path = work / "proof.json"
    proof_path.write_text(json.dumps(proof))
    output = work / "prepared"
    import_prepared_bundle(
        original_apk=apk,
        instrumented_apk=apk,
        units_path=units,
        proof_path=proof_path,
        output=output,
        backend_version="1.0",
        inclusion_policy="all-app-methods",
        log_tag=fake_device.LOG_TAG,
        method_prefix=fake_device.METHOD_PREFIX,
        aapt=tools["aapt"],
    )
    return output


def _runtime(**overrides) -> RuntimeConfig:
    values = {
        "schema_version": RuntimeConfig.SCHEMA_VERSION,
        "serial": "fake-device",
        "launcher": fake_device.LAUNCHER,
        "max_actions": 14,
        "max_seconds": 120.0,
        "action_timeout_seconds": 10.0,
        "adb_timeout_seconds": 10.0,
        "capture_screenshots": False,
        "install_timeout_seconds": 10.0,
        "launch_timeout_seconds": 10.0,
        "observation_timeout_seconds": 10.0,
        "route_foreground_timeout_seconds": 3.0,
        "per_field_input_enabled": True,
        "pid_poll_seconds": 0.05,
        "post_action_delay_seconds": 0.05,
        "reset_seed_enabled": True,
        "suppress_soft_keyboard": False,
        "route_discovery_sha256": None,
        "component_extras": {},
        "text_input_value": "valordroid",
        "uninstall_after_run": False,
        "deep_links": ["fakeapp://deep"],
        "exported_components": [f"{PACKAGE}/{PACKAGE}.DetailActivity"],
        "forced_components": [],
    }
    values.update(overrides)
    return RuntimeConfig.from_mapping(values)


def _core(**overrides) -> CoreConfig:
    values = {
        "stall_seconds": 3600.0,
        "stall_actions": 3,
        "association_settle_seconds": 0.35,
        "retry_cooldown_seconds": 3600.0,
        "initial_no_yield_attempts": 1,
        "max_navigation_depth": 4,
    }
    values.update(overrides)
    return CoreConfig(**values)


class _Harness:
    def __init__(self, test: unittest.TestCase, **kwargs) -> None:
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.tools = fake_device.install_fake_device(self.work / "tools")
        os.environ["FAKE_DEVICE_STATE"] = self.tools["state"]
        test.addCleanup(os.environ.pop, "FAKE_DEVICE_STATE", None)
        self.bundle_path = _prepared(self.work, self.tools)
        self.runtime = _runtime(**kwargs.pop("runtime", {}))
        self.core = _core(**kwargs.pop("core", {}))
        self.gateway = kwargs.pop("gateway", None)

    def run(self) -> tuple[Path, object]:
        from valordroid.prepared import verify_prepared_bundle

        bundle = verify_prepared_bundle(self.bundle_path, aapt=self.tools["aapt"])
        store = RunStore.create(
            self.work / "run",
            bundle.universe,
            run_id="fake-device-run",
            configuration=self.core.to_dict(),
            runtime_configuration=self.runtime.to_dict(),
            prepared_bundle_sha256=bundle.manifest.bundle_sha256,
            prepared_manifest=bundle.manifest.to_dict(),
        )
        result = AndroidRunner(
            store,
            bundle,
            self.runtime,
            self.core,
            adb_executable=self.tools["adb"],
            gateway=self.gateway,
        ).run()
        return store.root, result


def _attempts(root: Path) -> list[dict]:
    records = []
    for line in (root / "actions.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line)["payload"]["attempt"])
    return records


def _routes(root: Path) -> list[dict]:
    records = []
    for line in (root / "routes.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line)["payload"]["route"])
    return records


def _lifecycle(root: Path, phase: str | None = None) -> list[dict]:
    records = []
    for line in (root / "lifecycle.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)["payload"]["event"]
        if phase is None or event.get("phase") == phase:
            records.append(event)
    return records


def _model_calls(root: Path) -> list[dict]:
    path = root / "model_calls.jsonl"
    records = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line)["payload"]["call"])
    return records


class DeterministicRunTest(unittest.TestCase):
    """A whole run with no model, validated by the independent validator."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._case = unittest.TestCase()
        cls._case.addCleanup = lambda *args, **kwargs: None  # type: ignore[assignment]

    def setUp(self) -> None:
        self.harness = _Harness(self)
        self.root, self.result = self.harness.run()

    def test_the_run_finishes_and_validates(self) -> None:
        self.assertEqual(self.result.status, "finished", self.result.stop_reason)
        validation = validate_run(self.root)
        self.assertTrue(validation.ok, [issue.__dict__ for issue in validation.issues])

    def test_coverage_was_actually_observed(self) -> None:
        summary = summarize_run(self.root)
        self.assertGreater(summary["observed_covered_units"], 0)
        self.assertEqual(summary["total_units"], len(fake_device.UNITS))
        self.assertGreater(summary["observed_coverage_percent"], 0.0)

    def test_reach_reports_its_absence_rather_than_a_zero(self) -> None:
        """A run with no route discovery has no denominator, and must say so.

        Declared activities are read from route-discovery.json. Reporting 0 entered
        of 0 declared as a share of 0.0 would be a measurement that looks like total
        failure to reach anything, which is a different claim from having no basis
        to judge reach at all.
        """

        reach = summarize_run(self.root)["activity_reach"]
        self.assertEqual(reach["rule"], ACTIVITY_REACH_RULE)
        self.assertIsNone(reach["share_entered"])
        self.assertIsNotNone(reach["issue"])
        self.assertIn("route_discovery", reach["issue"])

    def test_reach_uses_the_same_rule_label_as_the_offline_tool(self) -> None:
        """Live and offline reach must not drift apart silently.

        Both derive their sets through activity_reach.py; this pins the label they
        publish, so changing one without the other fails here rather than in a
        comparison nobody re-derives.
        """

        tool = (
            Path(__file__).resolve().parents[1] / "app" / "tools" / "screen_reach.py"
        ).read_text(encoding="utf-8")
        self.assertIn(f'ACTIVITY_REACH_RULE = "{ACTIVITY_REACH_RULE}"', tool)

    def test_deep_link_coverage_is_reported_separately_from_forced_routes(self) -> None:
        summary = summarize_run(self.root)
        self.assertLessEqual(
            summary["primary_gui_deep_link_units"], summary["all_route_covered_units"]
        )

    def test_screen_identity_survives_changing_element_text(self) -> None:
        # The fake clock changes on every dump. Under the old whole-hierarchy
        # hash each observation would have been a new screen.
        states = {attempt["state_id"] for attempt in _attempts(self.root)}
        self.assertLess(len(states), self.result.attempts)
        self.assertGreater(len(states), 1)

    def test_no_single_action_dominates_the_run(self) -> None:
        # Gap 1 measured 93.4% of selections repeating one (state, target) pair.
        # The fake app is deliberately tiny, so a recorded transition is
        # legitimately replayed by navigation; what must not happen is one pair
        # crowding out the rest of the exploration.
        pairs = [
            (attempt["state_id"], attempt["requested"]["target_id"])
            for attempt in _attempts(self.root)
        ]
        self.assertGreaterEqual(len(set(pairs)), 4, pairs)
        dominant = max(pairs.count(pair) for pair in set(pairs))
        self.assertLess(dominant / len(pairs), 0.5, pairs)

    def test_each_field_receives_a_value_matching_its_own_kind(self) -> None:
        typed = [
            attempt["requested"]["parameters"]
            for attempt in _attempts(self.root)
            if attempt["requested"]["kind"] == "input_text"
        ]
        if not typed:
            self.skipTest("the run did not reach a text field")
        kinds = {parameters["field_kind"] for parameters in typed}
        for parameters in typed:
            self.assertEqual(parameters["value_rule"], DETERMINISTIC_VALUE_RULE)
            self.assertNotIn("<value>", parameters["value"])
            self.assertNotEqual(parameters["value"], "")
            if parameters["field_kind"] == "email":
                self.assertIn("@", parameters["value"])
            if parameters["field_kind"] == "integer":
                self.assertTrue(parameters["value"].isdigit())
        # An email box and a quantity box must not receive the same constant.
        if {"email", "integer"} <= kinds:
            values = {
                parameters["field_kind"]: parameters["value"] for parameters in typed
            }
            self.assertNotEqual(values["email"], values["integer"])

    def test_field_evidence_records_both_sides_of_the_change(self) -> None:
        for attempt in _attempts(self.root):
            if attempt["requested"]["kind"] != "input_text":
                continue
            details = attempt["outcome"]["details"]
            self.assertIn("before_target_text", details)
            self.assertIn("observed_target_text", details)
            return
        self.skipTest("the run did not reach a text field")

    def test_recovery_is_driven_by_exhaustion_not_by_a_stall_misfire(self) -> None:
        """The v1 regression guard for the stall signal.

        v1 anchored the stall on coverage gain alone. Because 91.4% of real
        actions associate no unit, the detector fired almost every action and
        the ladder pre-empted the frontier: 23 of 49 apps logged more stall
        events than actions. This fixture sets `stall_actions=3`, so the old
        rule tripped repeatedly here; the corrected rule counts untried
        candidates and unseen screens as progress, so recovery must instead be
        entered by the frontier reporting that a screen has no work left.

        `test_stall_signal.py` proves the detector still fires on a genuine
        plateau, so this assertion is not satisfied by a dead detector.
        """
        recovery = _lifecycle(self.root, "recovery")
        self.assertTrue(recovery, "the run never needed recovery")
        stalls = [event for event in recovery if event["event"] == "stall_triggered"]
        self.assertEqual(
            stalls,
            [],
            "the stall detector pre-empted the frontier while untried work remained",
        )
        triggers = {
            event["details"]["trigger"]
            for event in recovery
            if "trigger" in event.get("details", {})
        }
        self.assertTrue(
            triggers <= {"screen_exhausted", "temporarily_no_eligible"},
            f"recovery entered for an unjustified reason: {sorted(triggers)}",
        )

    def test_recovery_does_not_dominate_the_loop(self) -> None:
        """Ladder actions must stay a minority of the run.

        The ladder is the expensive path -- it re-observes, presses Back, and
        can reset the app -- so it earns its budget only as an exception. In v1
        it became the common case.
        """
        attempts = _attempts(self.root)
        routes = _routes(self.root)
        self.assertTrue(attempts, "the run performed no actions")
        self.assertLess(
            len(routes),
            len(attempts) / 2,
            f"recovery drove {len(routes)} of {len(attempts)} attempts",
        )

    def test_every_dynamic_route_retains_its_exact_action(self) -> None:
        for route in _routes(self.root):
            if route["rung"] in (1, 3, 6):
                self.assertIn("action", route["details"])
                self.assertEqual(
                    route["details"]["action"]["parameters"]["expected_state_id"],
                    route["details"]["action"]["parameters"]["expected_state_id"],
                )

    def test_no_model_call_is_recorded_without_a_gateway(self) -> None:
        self.assertEqual(_model_calls(self.root), [])
        self.assertEqual(self.result.model_calls, 0)

    def test_the_summary_refuses_to_claim_a_coverage_gain(self) -> None:
        summary = summarize_run(self.root)
        self.assertEqual(summary["coverage_gain_vs_baseline"], "not_established")


class RouteDiscoveryIntegrationTest(unittest.TestCase):
    def test_discovery_fills_the_targets_the_ladder_consumes(self) -> None:
        harness = _Harness(self)
        from valordroid.android.manifest import discover_apk_routes
        from valordroid.prepared import verify_prepared_bundle

        bundle = verify_prepared_bundle(harness.bundle_path, aapt=harness.tools["aapt"])
        discovery = discover_apk_routes(
            bundle.instrumented_apk,
            package_name=PACKAGE,
            apk_sha256=bundle.manifest.instrumented_apk_sha256,
            aapt=harness.tools["aapt"],
        )
        self.assertEqual(discovery.deep_links, ("fakeapp://deep",))
        # The launcher is excluded because the runner already starts it; the two
        # other exported activities are public intent targets, and only the
        # non-exported one is a forced target.
        self.assertEqual(
            discovery.exported_components,
            (
                f"{PACKAGE}/{PACKAGE}.DeepActivity",
                f"{PACKAGE}/{PACKAGE}.DetailActivity",
            ),
        )
        self.assertEqual(
            discovery.forced_components, (f"{PACKAGE}/{PACKAGE}.InternalActivity",)
        )
        updated = _runtime(deep_links=[], exported_components=[]).with_routes(
            deep_links=discovery.deep_links,
            exported_components=discovery.exported_components,
            forced_components=(),
            route_discovery_sha256=discovery.discovery_sha256,
        )
        self.assertEqual(updated.deep_links, ("fakeapp://deep",))
        self.assertEqual(updated.route_discovery_sha256, discovery.discovery_sha256)


class ModelAssistedRunTest(unittest.TestCase):
    """A run where the deterministic ladder is exhausted and rung 6 fires."""

    def _gateway(self, replies) -> ModelGateway:
        config = ModelConfig(
            enabled=True,
            provider="openai_compatible",
            model="test-model",
            base_url="https://example.invalid/v1",
            max_calls_per_run=6,
            max_calls_per_purpose=6,
            recovery_escalation_threshold=0,
        )
        provider = RecordingProvider(config, tuple(replies))
        self.provider = provider
        return ModelGateway(config, provider, environment={})

    def test_a_model_route_is_recorded_and_validates(self) -> None:
        # Reply with a candidate ID the gateway has not offered yet; the gateway
        # refuses it, which still produces a recorded call. Then reply with a
        # valid choice extracted from the prompt on the second attempt.
        gateway = self._gateway(
            [ModelResponse('{"candidate_id": "%s"}' % ("f" * 64), 9, 2)] * 6
        )
        harness = _Harness(
            self,
            core={
                "stall_actions": 2,
                "model_assistance_enabled": True,
                "max_model_calls": 6,
            },
            runtime={"deep_links": [], "exported_components": [], "max_actions": 10},
            gateway=gateway,
        )
        root, result = harness.run()
        validation = validate_run(root)
        self.assertTrue(validation.ok, [issue.__dict__ for issue in validation.issues])
        calls = _model_calls(root)
        if calls:
            # Every refusal is retained with its reason, which is the point.
            for call in calls:
                self.assertEqual(call["purpose"], "recovery_action_selection")
                self.assertTrue(call["prompt"])
            self.assertEqual(result.model_calls, len(calls))

    def test_a_run_without_assistance_refuses_a_gateway(self) -> None:
        harness = _Harness(self, gateway=self._gateway([]))
        with self.assertRaises(ValueError) as caught:
            harness.run()
        self.assertIn("disables assistance", str(caught.exception))

    def test_assistance_without_a_gateway_is_refused(self) -> None:
        harness = _Harness(
            self, core={"model_assistance_enabled": True, "max_model_calls": 2}
        )
        with self.assertRaises(ValueError) as caught:
            harness.run()
        self.assertIn("no gateway was supplied", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
