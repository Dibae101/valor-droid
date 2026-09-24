"""Setup-profile reseed: its evidence contract, and surviving an interrupt.

A seed reset wipes accumulated app state, so when a run is backed by a setup
profile the reset is only honest if the profile is replayed and re-verified
afterwards. The validator enforces a strict shape for that: start, every step,
every postcondition, the immediately committed background coverage, replay
success, then the settled coverage that must precede the action the route was
taken for.

The awkward part is interruption. Ctrl-C can land *after* a lifecycle record is
durably appended but *before* the append returns, which leaves a legitimate run
whose reseed evidence stops mid-sequence. The validator normalizes exactly those
prefixes and nothing wider. Before this file nothing exercised either half: no
test drove a setup profile at all, so both the contract and its interruption
normalization existed only as code.

Every case here drives the real `AndroidRunner` against the scriptable fake
device. The interrupt is injected by wrapping `record_lifecycle` so the durable
append happens and *then* the interrupt is raised, which is the exact boundary the
validator reasons about rather than an approximation of it.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests import fake_device
from tests.test_android_loop import _core, _prepared, _runtime
from valordroid.models import LifecycleStatus
from valordroid.prepared import verify_prepared_bundle
from valordroid.recovery import RecoveryRung
from valordroid.runner import AndroidRunner
from valordroid.setup_profile import SetupProfile
from valordroid.store import RunStore
from valordroid.validator import validate_run

PACKAGE = fake_device.PACKAGE

# One step and one postcondition, both satisfiable by the fake device's own main
# screen, so the profile exercises the contract without simulating an app.
PROFILE = {
    "schema": 1,
    "package": PACKAGE,
    "profile": "wait-for-the-main-screen",
    "persona": "local-fixture-only",
    "timeout_seconds": 120.0,
    "allowed_foreign_packages": [],
    "secret_environment": {},
    "steps": [
        {
            "id": "wait-for-main",
            "kind": "wait_for",
            "required": True,
            "timeout_seconds": 30.0,
            "parameters": {
                "predicate": {
                    "kind": "node_present",
                    "timeout_seconds": 30.0,
                    "parameters": {
                        "selector": {
                            "package": PACKAGE,
                            "resource_id": f"{PACKAGE}:id/go",
                        }
                    },
                }
            },
        }
    ],
    "success_predicates": [
        {
            "kind": "package_foreground",
            "timeout_seconds": 30.0,
            "parameters": {"package": PACKAGE},
        }
    ],
}


class _ReseedRun:
    """One setup-backed run whose ladder reaches a setup-backed seed reset."""

    def __init__(self, test: unittest.TestCase, *, serial: str, **overrides) -> None:
        temporary = tempfile.TemporaryDirectory()
        test.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.tools = fake_device.install_fake_device(self.work / "tools")
        os.environ["FAKE_DEVICE_STATE"] = self.tools["state"]
        test.addCleanup(os.environ.pop, "FAKE_DEVICE_STATE", None)
        self.bundle_path = _prepared(self.work, self.tools)
        self.runtime = _runtime(
            serial=serial,
            setup_reseed_enabled=True,
            reset_seed_enabled=True,
            max_actions=overrides.pop("max_actions", 14),
        )
        # A one-action stall drives the ladder quickly, and two permitted seed
        # resets make the setup-backed reset rung reachable more than once.
        self.core = _core(stall_actions=1, stall_seconds=0.5, max_seed_resets=2)
        self.profile = SetupProfile.from_mapping(PROFILE)

    def run(self, interrupt_at: tuple[str, LifecycleStatus] | None = None):
        bundle = verify_prepared_bundle(self.bundle_path, aapt=self.tools["aapt"])
        store = RunStore.create(
            self.work / "run",
            bundle.universe,
            run_id="reseed-run",
            configuration=self.core.to_dict(),
            runtime_configuration=self.runtime.to_dict(),
            prepared_bundle_sha256=bundle.manifest.bundle_sha256,
            prepared_manifest=bundle.manifest.to_dict(),
        )
        store.setup_profile_path.write_text(
            self.profile.canonical_json, encoding="utf-8"
        )
        runner = AndroidRunner(
            store,
            bundle,
            self.runtime,
            self.core,
            adb_executable=self.tools["adb"],
            setup=self.profile.resolve({}),
        )
        interrupted = False
        if interrupt_at is not None:
            event, status = interrupt_at
            original = runner.core.record_lifecycle

            def durable_then_interrupt(**kwargs):
                nonlocal interrupted
                # The append happens first, exactly as it does in production, so
                # the record is durable before the interrupt is observed.
                result = original(**kwargs)
                if (
                    not interrupted
                    and kwargs.get("event") == event
                    and kwargs.get("status") is status
                ):
                    interrupted = True
                    raise KeyboardInterrupt
                return result

            runner.core.record_lifecycle = durable_then_interrupt
        try:
            result = runner.run()
        except KeyboardInterrupt:
            # The runner terminalizes the interrupt itself and returns an aborted
            # result; a propagated interrupt is accepted here too so the test
            # does not depend on which of the two it chooses.
            result = None
        self.interrupted = interrupted
        return store.root, result


def _lifecycle(root: Path) -> list[dict]:
    records = []
    for line in (root / "lifecycle.jsonl").read_text().splitlines():
        if line.strip():
            records.append(json.loads(line)["payload"]["event"])
    return records


def _reseed_events(root: Path) -> list[tuple[str, str]]:
    return [
        (record["event"], record["status"])
        for record in _lifecycle(root)
        if record["event"].startswith("setup_profile_reseed")
    ]


def _routes(root: Path) -> list[dict]:
    records = []
    for line in (root / "routes.jsonl").read_text().splitlines():
        if line.strip():
            records.append(json.loads(line)["payload"]["route"])
    return records


def _manifest(root: Path) -> dict:
    return json.loads((root / "run.json").read_text())


class ReseedContractTest(unittest.TestCase):
    """A completed setup-backed reset replays the profile and proves it worked."""

    @classmethod
    def setUpClass(cls) -> None:
        class _Deferred(unittest.TestCase):
            def runTest(self) -> None:  # pragma: no cover - cleanup holder
                pass

        cls._deferred = _Deferred()
        cls.harness = _ReseedRun(cls._deferred, serial="fake-reseed-device")
        cls.root, cls.result = cls.harness.run()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._deferred.doCleanups()

    def test_the_run_finishes_and_validates(self) -> None:
        self.assertEqual(self.result.status, "finished")
        validation = validate_run(self.root)
        self.assertTrue(
            validation.ok, [issue.__dict__ for issue in validation.issues]
        )

    def test_the_ladder_actually_reached_a_setup_backed_reset(self) -> None:
        """Otherwise every assertion below would pass on an empty sequence."""

        rungs = {route["rung"] for route in _routes(self.root)}
        self.assertIn(int(RecoveryRung.RESET_SEED), rungs)
        self.assertTrue(_reseed_events(self.root))

    def test_every_reset_route_names_the_profile_it_replayed(self) -> None:
        resets = [
            route
            for route in _routes(self.root)
            if route["rung"] == int(RecoveryRung.RESET_SEED)
        ]
        self.assertTrue(resets)
        for route in resets:
            self.assertEqual(
                route["details"]["setup_profile_sha256"],
                self.harness.profile.profile_sha256,
            )

    def test_the_reseed_sequence_is_start_steps_predicates_coverage_success(
        self,
    ) -> None:
        events = _reseed_events(self.root)
        expected = [
            ("setup_profile_reseed", "started"),
            ("setup_profile_reseed_step", "started"),
            ("setup_profile_reseed_step", "succeeded"),
            ("setup_profile_reseed_predicate", "started"),
            ("setup_profile_reseed_predicate", "succeeded"),
            ("setup_profile_reseed_coverage_committed", "succeeded"),
            ("setup_profile_reseed", "succeeded"),
            ("setup_profile_reseed_settled_coverage_committed", "succeeded"),
        ]
        # The run may reseed more than once; every occurrence has this shape.
        self.assertEqual(len(events) % len(expected), 0)
        for index in range(0, len(events), len(expected)):
            self.assertEqual(events[index : index + len(expected)], expected)

    def test_every_reseed_record_is_bound_to_its_route_and_profile(self) -> None:
        route_ids = {
            route["route_id"]
            for route in _routes(self.root)
            if route["rung"] == int(RecoveryRung.RESET_SEED)
        }
        records = [
            record
            for record in _lifecycle(self.root)
            if record["event"].startswith("setup_profile_reseed")
        ]
        self.assertTrue(records)
        for record in records:
            self.assertEqual(record["phase"], "recovery")
            self.assertEqual(
                record["details"]["setup_profile_sha256"],
                self.harness.profile.profile_sha256,
            )
            self.assertEqual(record["details"]["reason"], "reset_seed")
            self.assertIn(record["details"]["route_id"], route_ids)

    def test_the_two_reseed_coverage_commitments_are_distinct_events(self) -> None:
        """Immediate and settled coverage must not be one event counted twice."""

        immediate = [
            record["details"]["coverage_event_id"]
            for record in _lifecycle(self.root)
            if record["event"] == "setup_profile_reseed_coverage_committed"
        ]
        settled = [
            record["details"]["coverage_event_id"]
            for record in _lifecycle(self.root)
            if record["event"] == "setup_profile_reseed_settled_coverage_committed"
        ]
        self.assertTrue(immediate and settled)
        self.assertEqual(len(set(immediate) & set(settled)), 0)

    def test_setup_coverage_is_committed_as_background_not_as_an_action(self) -> None:
        committed = {
            record["details"]["coverage_event_id"]
            for record in _lifecycle(self.root)
            if record["event"].endswith("coverage_committed")
        }
        background = set()
        for line in (self.root / "transactions.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)["payload"]
            if payload.get("record_type") == "background_coverage_transaction":
                background.add(payload["coverage_event_id"])
        self.assertTrue(committed)
        self.assertTrue(committed <= background)


class ReseedInterruptionTest(unittest.TestCase):
    """Ctrl-C between a durable append and its return is a legitimate run."""

    def _interrupted(self, event: str, status: LifecycleStatus, *, serial: str):
        harness = _ReseedRun(self, serial=serial)
        root, result = harness.run(interrupt_at=(event, status))
        self.assertTrue(
            harness.interrupted, f"the interrupt never reached {event}/{status.value}"
        )
        if result is not None:
            self.assertEqual(result.status, "aborted")
            self.assertEqual(result.stop_reason, "keyboard_interrupt")
        manifest = _manifest(root)
        self.assertEqual(manifest["status"], "aborted")
        self.assertEqual(manifest["abort_phase"], "interruption")
        self.assertEqual(manifest["abort_reason"], "keyboard_interrupt")
        self.assertEqual(manifest["abort_error"], "KeyboardInterrupt")
        return root

    def _assert_validates(self, root: Path) -> None:
        validation = validate_run(root)
        reseed = [
            issue
            for issue in validation.errors
            if issue.code.startswith("setup_reseed")
        ]
        self.assertEqual(
            reseed,
            [],
            [issue.__dict__ for issue in reseed],
        )
        self.assertTrue(
            validation.ok, [issue.__dict__ for issue in validation.errors]
        )

    def test_an_interrupt_after_the_start_record_is_accepted(self) -> None:
        """The start append sits just outside the guarded replay body.

        Because the interrupt is raised by the append itself, the replay body is
        never entered, so there is no terminator and no child work at all. That
        single dangling start is exactly the prefix the validator normalizes, and
        it must be accepted rather than read as a reseed that vanished.
        """

        root = self._interrupted(
            "setup_profile_reseed",
            LifecycleStatus.STARTED,
            serial="fake-reseed-start",
        )
        self.assertEqual(
            _reseed_events(root), [("setup_profile_reseed", "started")]
        )
        self._assert_validates(root)

    def test_an_interrupt_after_the_success_record_is_accepted(self) -> None:
        """The inverse boundary: success is durable, then the handler fails it."""

        root = self._interrupted(
            "setup_profile_reseed",
            LifecycleStatus.SUCCEEDED,
            serial="fake-reseed-success",
        )
        events = _reseed_events(root)
        self.assertIn(("setup_profile_reseed", "succeeded"), events)
        self.assertEqual(events[-1], ("setup_profile_reseed", "failed"))
        self._assert_validates(root)

    def test_an_interrupt_mid_step_leaves_a_valid_prefix(self) -> None:
        root = self._interrupted(
            "setup_profile_reseed_step",
            LifecycleStatus.STARTED,
            serial="fake-reseed-step",
        )
        events = _reseed_events(root)
        self.assertIn(("setup_profile_reseed_step", "started"), events)
        # The step never reported a result, and nothing after it was attempted.
        self.assertNotIn(("setup_profile_reseed_step", "succeeded"), events)
        self.assertNotIn(
            ("setup_profile_reseed_coverage_committed", "succeeded"), events
        )
        self._assert_validates(root)

    def test_an_interrupt_after_a_predicate_started_leaves_a_valid_prefix(
        self,
    ) -> None:
        root = self._interrupted(
            "setup_profile_reseed_predicate",
            LifecycleStatus.STARTED,
            serial="fake-reseed-predicate",
        )
        events = _reseed_events(root)
        self.assertIn(("setup_profile_reseed_predicate", "started"), events)
        self.assertNotIn(
            ("setup_profile_reseed_coverage_committed", "succeeded"), events
        )
        self._assert_validates(root)

    def test_an_interrupt_after_immediate_coverage_is_accepted(self) -> None:
        """Every child completed and the background transaction is committed."""

        root = self._interrupted(
            "setup_profile_reseed_coverage_committed",
            LifecycleStatus.SUCCEEDED,
            serial="fake-reseed-coverage",
        )
        events = _reseed_events(root)
        self.assertIn(
            ("setup_profile_reseed_coverage_committed", "succeeded"), events
        )
        self.assertNotIn(("setup_profile_reseed", "succeeded"), events)
        self._assert_validates(root)

    def test_an_interrupted_reseed_claims_no_settled_coverage(self) -> None:
        """Settled coverage follows replay success, so a stopped replay has none."""

        for event, status, serial in (
            ("setup_profile_reseed", LifecycleStatus.STARTED, "fake-reseed-s1"),
            (
                "setup_profile_reseed_step",
                LifecycleStatus.STARTED,
                "fake-reseed-s2",
            ),
        ):
            with self.subTest(event=event):
                root = self._interrupted(event, status, serial=serial)
                self.assertNotIn(
                    (
                        "setup_profile_reseed_settled_coverage_committed",
                        "succeeded",
                    ),
                    _reseed_events(root),
                )

    def test_the_interrupted_reset_route_is_the_last_route_and_failed(self) -> None:
        root = self._interrupted(
            "setup_profile_reseed",
            LifecycleStatus.STARTED,
            serial="fake-reseed-route",
        )
        routes = _routes(root)
        self.assertTrue(routes)
        last = routes[-1]
        self.assertEqual(last["rung"], int(RecoveryRung.RESET_SEED))
        self.assertEqual(last["outcome"], "failed")
        self.assertEqual(last["details"]["error"], "KeyboardInterrupt")


if __name__ == "__main__":
    unittest.main()
