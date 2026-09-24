"""What the stop record counts is what the run *spent*, not what it committed.

`Controller.attempt_count` is canonical attempts plus any dispatch that was
finalized without one, deliberately, so an action already sent to the device whose
evidence then failed is charged rather than forgotten. The runner writes that
number into `exploration_stopped`.

The validator recomputed the expectation as canonical attempts alone. The two
agree only while no dispatch ever fails after being dispatched, so the moment one
did, the run was refused as evidence for *agreeing with the runner*. Measured:
Muzei finished at 43.78% with 212 canonical attempts and 2 finalized dispatches,
reported 214, and `summary` refused the whole run with
`android_stop_evidence: exploration_stopped does not record its exact reason and
attempt count`. Making a moved-foreground refusal recoverable is what turned that
latent disagreement into a routine one.

These tests pin that the validator now expects the runner's definition, and --
just as importantly -- that it still refuses a count that matches neither.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import fake_device
from tests.test_android_loop import _core, _prepared, _runtime
from valordroid.ledger import HashChainLedger, ZERO_HASH
from valordroid.prepared import verify_prepared_bundle
from valordroid.runner import AndroidRunner
from valordroid.store import RunStore
from valordroid import validator as validator_module
from valordroid.validator import validate_run


class _Finalized:
    """One finalized dispatch, as `replay_action_dispatches` reports them."""

    def __init__(self, attempt_id: str | None) -> None:
        self.attempt_id = attempt_id
        self.coverage_event_id = None
        self.failure_stage = "coverage"
        self.finalized_at = 0.0


class _Replay:
    def __init__(self, finalized: tuple[_Finalized, ...]) -> None:
        self.finalized = finalized
        self.pending = ()


class StopEvidenceAttemptCountTest(unittest.TestCase):
    """One real finished run, then exact copies with the stop count rewritten."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._class_temporary = tempfile.TemporaryDirectory()
        cls.class_root = Path(cls._class_temporary.name)
        cls.tools = fake_device.install_fake_device(cls.class_root / "tools")
        cls._old_device_state = os.environ.get("FAKE_DEVICE_STATE")
        os.environ["FAKE_DEVICE_STATE"] = cls.tools["state"]

        bundle_path = _prepared(cls.class_root, cls.tools)
        bundle = verify_prepared_bundle(bundle_path, aapt=cls.tools["aapt"])
        runtime = _runtime()
        core = _core()
        store = RunStore.create(
            cls.class_root / "valid-run",
            bundle.universe,
            run_id="stop-evidence-fixture",
            configuration=core.to_dict(),
            runtime_configuration=runtime.to_dict(),
            prepared_bundle_sha256=bundle.manifest.bundle_sha256,
            prepared_manifest=bundle.manifest.to_dict(),
        )
        result = AndroidRunner(
            store,
            bundle,
            runtime,
            core,
            adb_executable=cls.tools["adb"],
        ).run()
        if result.status != "finished":
            raise AssertionError(f"fixture run did not finish: {result.stop_reason}")
        validation = validate_run(store.root)
        if not validation.ok:
            raise AssertionError([issue.__dict__ for issue in validation.issues])
        cls.valid_run = store.root

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._old_device_state is None:
            os.environ.pop("FAKE_DEVICE_STATE", None)
        else:
            os.environ["FAKE_DEVICE_STATE"] = cls._old_device_state
        cls._class_temporary.cleanup()

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.work = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.run = self.work / "run"
        shutil.copytree(self.valid_run, self.run)

    def _stub_finalized(self, count: int) -> None:
        """Report `count` dispatches finalized without a canonical attempt.

        Forging admissible dispatch-ledger evidence by hand would test the forgery,
        not the arithmetic under repair, so the replay is substituted and only the
        expectation it feeds is exercised. The dispatch ledger has its own checks.
        """

        original = validator_module.replay_action_dispatches
        finalized = tuple(_Finalized(None) for _ in range(count))

        def replacement(*args, **kwargs):
            del args, kwargs
            return _Replay(finalized)

        validator_module.replay_action_dispatches = replacement
        self.addCleanup(setattr, validator_module, "replay_action_dispatches", original)

    def _canonical_attempts(self) -> int:
        return sum(
            1 for line in (self.run / "actions.jsonl").read_text().splitlines() if line.strip()
        )

    def _rewrite_stop_attempts(self, attempts: int) -> None:
        """Set the recorded stop count and rebind the lifecycle chain and head."""

        path = self.run / "lifecycle.jsonl"
        entries = [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]
        rewritten = 0
        previous = ZERO_HASH
        for sequence, entry in enumerate(entries, 1):
            payload = entry["payload"]
            event = payload.get("event") or {}
            if event.get("event") == "exploration_stopped":
                event["details"]["attempts"] = attempts
                rewritten += 1
            entry["sequence"] = sequence
            entry["previous_hash"] = previous
            entry["entry_hash"] = HashChainLedger._entry_hash(
                sequence, previous, payload
            )
            previous = entry["entry_hash"]
        if rewritten != 1:
            raise AssertionError(f"expected one stop record, rewrote {rewritten}")
        path.write_text(
            "".join(
                json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
                for entry in entries
            ),
            encoding="utf-8",
        )
        manifest_path = self.run / "run.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["evidence_heads"]["lifecycle"] = {
            "count": len(entries),
            "terminal_hash": previous,
        }
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _stop_issues(self):
        return [
            issue
            for issue in validate_run(self.run).issues
            if issue.code == "android_stop_evidence"
        ]

    def test_the_untouched_fixture_validates(self) -> None:
        """Control: the rewriting machinery is what changes outcomes below."""

        validation = validate_run(self.run)
        self.assertTrue(validation.ok, [issue.__dict__ for issue in validation.issues])

    def test_rebinding_alone_does_not_change_the_verdict(self) -> None:
        """Rewriting the count to its own value must still validate."""

        self._rewrite_stop_attempts(self._canonical_attempts())
        validation = validate_run(self.run)
        self.assertTrue(validation.ok, [issue.__dict__ for issue in validation.issues])

    def test_a_charged_dispatch_is_included_in_the_expected_count(self) -> None:
        """The regression: canonical + 1 is correct when one dispatch was charged."""

        self._stub_finalized(1)
        self._rewrite_stop_attempts(self._canonical_attempts() + 1)
        self.assertEqual(self._stop_issues(), [])

    def test_two_charged_dispatches_are_both_included(self) -> None:
        """Muzei's exact shape: 212 canonical, 2 charged, 214 reported."""

        self._stub_finalized(2)
        self._rewrite_stop_attempts(self._canonical_attempts() + 2)
        self.assertEqual(self._stop_issues(), [])

    def test_a_count_no_definition_supports_is_still_refused(self) -> None:
        """The check still bites: an inflated count with nothing charged fails."""

        self._rewrite_stop_attempts(self._canonical_attempts() + 1)
        self.assertEqual(len(self._stop_issues()), 1)

    def test_omitting_a_charged_dispatch_is_refused(self) -> None:
        """Under-reporting is a disagreement with the runner too, not a rounding."""

        self._stub_finalized(1)
        self._rewrite_stop_attempts(self._canonical_attempts())
        self.assertEqual(len(self._stop_issues()), 1)

    def test_a_count_below_the_canonical_attempts_is_refused(self) -> None:
        self._rewrite_stop_attempts(max(0, self._canonical_attempts() - 1))
        self.assertEqual(len(self._stop_issues()), 1)


if __name__ == "__main__":
    unittest.main()
