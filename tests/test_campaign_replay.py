"""Campaign scheduler replay and terminal-evidence regressions.

Pinned findings:
- review 1 #1: terminal validation trusted producer payloads.
- review 1 #7: terminal projection used the installed version, not the plan's.
- review 2 #6: signal PGID and retry payloads accepted JSON floats.
- review 3 #2: a scheduler failure sealed a terminal replay refuses.
- review 4 #1: an attempt failing before device_ready was unreplayable.
- review 4 #2/#5 #1: the terminal was sealed before evidence could be checked.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from valordroid.campaign import (
    CAMPAIGN_SCHEMA_VERSION,
    build_campaign_manifest,
    recover_campaign_manifest,
    summarize_campaign,
    validate_campaign,
)
from valordroid.fleet_plan import create_fleet_plan, verify_fleet_plan
from valordroid.fleet_runner import FleetRunner

from . import support


class CampaignReplayTest(unittest.TestCase):
    """Drives a real attach-mode campaign whose children fail deterministically."""

    @classmethod
    def setUpClass(cls) -> None:
        support.suppress_environment_noise()
        cls._root = Path(tempfile.mkdtemp(prefix="valordroid-replay."))
        cls.tools = support.fake_tools(cls._root / "tools")
        cls.prepared = support.build_prepared(cls._root / "work", cls.tools["aapt"])
        cls.config_path = support.attach_fleet_config(
            cls._root / "work",
            cls.prepared,
            cls.tools,
            lease_root=cls._root / "leases",
        )
        cls.campaign = cls._root / "campaign"
        create_fleet_plan(cls.config_path, cls.campaign)
        cls.result = FleetRunner(cls.campaign).run()
        cls.manifest = json.loads((cls.campaign / "campaign.json").read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self) -> None:
        self.original_events = (
            self.campaign / "campaign-events.jsonl"
        ).read_text()

    def tearDown(self) -> None:
        (self.campaign / "campaign-events.jsonl").write_text(self.original_events)

    def test_campaign_is_not_reported_finished_without_successful_jobs(self) -> None:
        self.assertIn(self.result.status, {"failed", "aborted"})
        self.assertNotEqual(self.result.exit_code, 0)
        self.assertTrue(
            all(job["status"] != "succeeded" for job in self.manifest["jobs"])
        )

    def test_terminal_manifest_uses_the_plan_tool_version(self) -> None:
        _, plan = verify_fleet_plan(self.campaign, verify_prepared=False)
        self.assertEqual(self.manifest["tool_version"], plan["tool_version"])
        self.assertEqual(self.manifest["schema_version"], CAMPAIGN_SCHEMA_VERSION)

    def test_execution_identity_is_recorded(self) -> None:
        self.assertRegex(self.manifest["execution_id"], r"^execution-[0-9a-f]{32}$")

    def test_every_planned_job_has_exactly_one_terminal_result(self) -> None:
        _, plan = verify_fleet_plan(self.campaign, verify_prepared=False)
        self.assertEqual(
            sorted(job["job_id"] for job in self.manifest["jobs"]),
            sorted(item["job_id"] for item in plan["jobs"]),
        )

    def test_independent_validation_accepts_the_campaign(self) -> None:
        result = validate_campaign(self.campaign, verify_prepared=True)
        self.assertTrue(
            result.ok, "; ".join(f"{i.code}: {i.message}" for i in result.errors)
        )

    def test_recovery_is_idempotent(self) -> None:
        self.assertEqual(recover_campaign_manifest(self.campaign), self.manifest)

    def test_rerun_returns_existing_terminal_evidence(self) -> None:
        self.assertEqual(FleetRunner(self.campaign).run().status, self.result.status)

    def test_attempt_that_failed_before_device_ready_still_replays(self) -> None:
        """Review 4 #1: the timing chain rejected clean provisioning failures."""

        attempts = [
            attempt
            for job in self.manifest["jobs"]
            for attempt in job["attempts"]
        ]
        self.assertTrue(attempts, "expected at least one recorded attempt")
        for attempt in attempts:
            self.assertLessEqual(attempt["started_at"], attempt["finished_at"])

    def test_log_files_named_by_evidence_exist(self) -> None:
        """Review 5 #2: the log placeholder was the only creator and failed silently."""

        for job in self.manifest["jobs"]:
            for attempt in job["attempts"]:
                for name in ("stdout_log", "stderr_log"):
                    path = self.campaign / attempt[name]
                    self.assertTrue(path.is_file(), f"missing {name}: {path}")
                    self.assertFalse(path.is_symlink())

    def test_a_terminal_run_records_rebindable_ledger_checksums(self) -> None:
        for job in self.manifest["jobs"]:
            for attempt in job["attempts"]:
                if attempt["run_path"] is None:
                    continue
                manifest_path = self.campaign / attempt["run_path"] / "run.json"
                if not manifest_path.is_file():
                    continue
                run_manifest = json.loads(manifest_path.read_text())
                if run_manifest["status"] not in {"finished", "aborted"}:
                    continue
                recorded = run_manifest["evidence_head_checksums"]
                self.assertTrue(recorded)
                for name, value in recorded.items():
                    self.assertEqual(
                        set(value),
                        {"count", "terminal_hash", "file_sha256", "bytes", "index_sha256"},
                        name,
                    )

    def test_a_tampered_ledger_index_breaks_the_campaign_binding(self) -> None:
        """Counts and heads alone cannot tell republished evidence from measured."""

        from valordroid.ledger import HashChainLedger

        target = next(
            path
            for path in sorted(self.campaign.rglob("lifecycle.jsonl.index.jsonl"))
        )
        original = target.read_bytes()
        try:
            target.write_text("garbage\n", encoding="utf-8")
            result = validate_campaign(self.campaign, verify_prepared=False)
            self.assertFalse(result.ok)
            self.assertTrue(
                any(
                    "ledger checksums differ" in issue.message
                    for issue in result.errors
                ),
                [issue.message for issue in result.errors],
            )
            # Rebuilding from the untouched ledger must restore the exact bytes,
            # which is what makes the binding a property of the evidence.
            HashChainLedger(target.with_name("lifecycle.jsonl")).rebuild_index()
            self.assertEqual(target.read_bytes(), original)
            self.assertTrue(validate_campaign(self.campaign, verify_prepared=False).ok)
        finally:
            target.write_bytes(original)

    def test_tampered_terminal_event_is_rejected(self) -> None:
        entries = support.read_events(self.campaign)
        entries[-1]["payload"]["details"]["status"] = "finished"
        support.write_events(self.campaign, entries)
        self.assertFalse(validate_campaign(self.campaign, verify_prepared=False).ok)

    def test_forged_float_attempt_number_is_rejected(self) -> None:
        """Review 2 #6: JSON floats satisfied integer comparisons."""

        entries = support.read_events(self.campaign)
        changed = False
        for entry in entries:
            if entry["payload"]["event_type"] == "attempt_started":
                entry["payload"]["attempt"] = 1.0
                changed = True
                break
        self.assertTrue(changed, "expected an attempt_started event")
        support.write_events(self.campaign, entries)
        self.assertFalse(validate_campaign(self.campaign, verify_prepared=False).ok)

    def test_summary_refuses_to_invent_coverage(self) -> None:
        summary = summarize_campaign(self.campaign)
        self.assertEqual(summary["valid_finished_runs"], 0)
        self.assertEqual(summary["coverage_gain_vs_baseline"], "not_established")
        self.assertIn("control", summary["aggregation_rule"])

    def test_planned_cells_reproduce_the_plan_exactly(self) -> None:
        """The summary's cell identities must be the plan's, arm included.

        Comparison decides whether an arm ran every repetition it declared, and
        it reads the requirement from here. `planned_cells_by_app` cannot serve
        that purpose because it totals jobs across every arm in the campaign, so
        the per-arm identities have to survive into the summary unchanged.
        """

        summary = summarize_campaign(self.campaign)
        _, plan = verify_fleet_plan(self.campaign, verify_prepared=False)
        expected = sorted(
            (
                (
                    str(item["job_id"]),
                    str(item["arm"]),
                    str(item["app_id"]),
                    int(item["repetition"]),
                )
                for item in plan["jobs"]
                if item.get("arm") is not None and item.get("app_id") is not None
            )
        )
        self.assertEqual(
            sorted(
                (
                    item["job_id"],
                    item["arm"],
                    item["app_id"],
                    item["repetition"],
                )
                for item in summary["planned_cells"]
            ),
            expected,
        )
        # Every cell names an arm, so a consumer never has to guess which arm's
        # requirement it belongs to.
        for cell in summary["planned_cells"]:
            self.assertIsInstance(cell["arm"], str)
            self.assertIsInstance(cell["repetition"], int)
            self.assertGreaterEqual(cell["repetition"], 1)
            self.assertLessEqual(cell["repetition"], summary["declared_repetitions"])

    def test_failed_jobs_become_invalid_cells_instead_of_disappearing(self) -> None:
        """Dropping them is what makes a partial campaign look complete."""

        summary = summarize_campaign(self.campaign)
        completion = summary["completion"]
        planned = len(self.manifest["jobs"])
        self.assertEqual(completion["planned_cells"], planned)
        self.assertEqual(completion["valid_cells"], 0)
        self.assertEqual(completion["invalid_cells"], planned)
        self.assertEqual(len(completion["invalid_cell_details"]), planned)
        self.assertEqual(completion["completion_rate"], 0.0)
        self.assertEqual(
            completion["valid_cells"] + completion["invalid_cells"],
            completion["planned_cells"],
        )

    def test_every_invalid_cell_keeps_its_failure_class_and_identity(self) -> None:
        summary = summarize_campaign(self.campaign)
        planned = {item["job_id"]: item for item in verify_fleet_plan(
            self.campaign, verify_prepared=False
        )[1]["jobs"]}
        for cell in summary["completion"]["invalid_cell_details"]:
            self.assertIn(cell["job_id"], planned)
            self.assertNotEqual(cell["status"], "succeeded")
            self.assertEqual(cell["arm"], planned[cell["job_id"]]["arm"])
            self.assertEqual(cell["app_id"], planned[cell["job_id"]]["app_id"])
            self.assertEqual(cell["repetition"], planned[cell["job_id"]]["repetition"])
            self.assertGreaterEqual(cell["attempts_used"], 1)
            self.assertEqual(len(cell["attempt_statuses"]), cell["attempts_used"])
            self.assertEqual(len(cell["retry_reasons"]), cell["attempts_used"] - 1)
            self.assertIsNotNone(cell["terminal_error"])

    def test_attempt_counts_and_retry_rates_are_surfaced(self) -> None:
        summary = summarize_campaign(self.campaign)
        completion = summary["completion"]
        recorded = sum(len(job["attempts"]) for job in self.manifest["jobs"])
        self.assertEqual(completion["attempts_recorded"], recorded)
        self.assertGreaterEqual(completion["attempts_recorded"], completion["planned_cells"])
        self.assertEqual(completion["job_status_counts"], self.manifest["counts"])
        retried = sum(1 for job in self.manifest["jobs"] if len(job["attempts"]) > 1)
        self.assertEqual(completion["retried_cells"], retried)
        self.assertEqual(
            completion["retry_rate"], retried / completion["planned_cells"]
        )

    def test_the_summary_records_the_frozen_randomization(self) -> None:
        summary = summarize_campaign(self.campaign)
        _, plan = verify_fleet_plan(self.campaign, verify_prepared=False)
        self.assertEqual(summary["assignment_version"], plan["assignment_version"])
        self.assertEqual(summary["assignment_seed"], plan["assignment_seed"])
        self.assertEqual(summary["declared_repetitions"], plan["repetitions"])


class PreDeviceReadyFailureTest(unittest.TestCase):
    """A device that never becomes ready must still seal complete evidence.

    Pins review 4 #1 and review 5 #2 together: the attempt fails before the child
    starts, so nothing opens the log files and no interior event advances the
    timing chain.
    """

    @classmethod
    def setUpClass(cls) -> None:
        support.suppress_environment_noise()
        cls._root = Path(tempfile.mkdtemp(prefix="valordroid-notready."))
        tools = support.fake_tools(cls._root / "tools")
        prepared = support.build_prepared(cls._root / "work", tools["aapt"])
        config_path = support.attach_fleet_config(
            cls._root / "work",
            prepared,
            tools,
            lease_root=cls._root / "leases",
            jobs=1,
            adb=tools["adb_never_ready"],
        )
        cls.campaign = cls._root / "campaign"
        create_fleet_plan(config_path, cls.campaign)
        cls.result = FleetRunner(cls.campaign).run()
        cls.manifest = json.loads((cls.campaign / "campaign.json").read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._root, ignore_errors=True)

    def test_attempt_is_recorded_as_a_provisioning_failure(self) -> None:
        attempt = self.manifest["jobs"][0]["attempts"][0]
        self.assertEqual(attempt["status"], "provision_failed")
        self.assertIsNone(attempt["device_identity"])
        self.assertIn("did not become ready", attempt["error"])

    def test_log_files_exist_even_though_no_child_started(self) -> None:
        attempt = self.manifest["jobs"][0]["attempts"][0]
        for name in ("stdout_log", "stderr_log"):
            path = self.campaign / attempt[name]
            self.assertTrue(path.is_file(), f"missing {name}")
            self.assertEqual(path.stat().st_size, 0)

    def test_campaign_seals_and_validates(self) -> None:
        self.assertEqual(self.result.status, "failed")
        self.assertEqual(self.result.exit_code, 10)
        result = validate_campaign(self.campaign, verify_prepared=False)
        self.assertTrue(
            result.ok, "; ".join(f"{i.code}: {i.message}" for i in result.errors)
        )

    def test_removing_a_log_is_refused_by_projected_evidence(self) -> None:
        """Review 5 #1: artifact checks must reject missing evidence."""

        from valordroid.campaign import validate_projected_evidence

        _, plan = verify_fleet_plan(self.campaign, verify_prepared=False)
        attempt = self.manifest["jobs"][0]["attempts"][0]
        target = self.campaign / attempt["stdout_log"]
        target.unlink()
        try:
            with self.assertRaises(ValueError):
                validate_projected_evidence(
                    self.campaign, plan, self.manifest["jobs"]
                )
        finally:
            target.touch()


class ReplayContractTest(unittest.TestCase):
    """Adversarial event shapes fed straight to the replay state machine."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._root = Path(tempfile.mkdtemp(prefix="valordroid-replay-unit."))
        cls.tools = support.fake_tools(cls._root / "tools")
        cls.prepared = support.build_prepared(cls._root / "work", cls.tools["aapt"])
        cls.config_path = support.attach_fleet_config(
            cls._root / "work",
            cls.prepared,
            cls.tools,
            lease_root=cls._root / "leases",
            jobs=1,
        )
        cls.campaign = cls._root / "campaign"
        create_fleet_plan(cls.config_path, cls.campaign)
        cls.config, cls.plan = verify_fleet_plan(cls.campaign, verify_prepared=False)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._root, ignore_errors=True)

    def _job(self) -> dict:
        return self.plan["jobs"][0]

    def _attempt_result(self, **overrides) -> dict:
        result = {
            "attempt": 1,
            "status": "invalid",
            "started_at": 10.0,
            "finished_at": 11.0,
            "exit_code": 10,
            "run_path": None,
            "run_manifest_sha256": None,
            "run_status": None,
            "validation_ok": False,
            "stdout_log": "logs/job-0.attempt-0001.stdout.log",
            "stderr_log": "logs/job-0.attempt-0001.stderr.log",
            "error": "run-android returned invalid/aborted evidence",
            "device_identity": support.attach_identity(self._job_serial()),
            "escalation": "none",
        }
        result.update(overrides)
        return result

    def _job_serial(self) -> str:
        device_id = self._job()["device_id"]
        return next(
            item["serial"]
            for item in self.plan["devices"]
            if item["device_id"] == device_id
        )

    def _child_argv(self, job: dict) -> list[str]:
        """The exact 18-token run-android argv the scheduler is allowed to record."""

        return [
            "/usr/bin/python3",
            "-m",
            "valordroid.cli",
            "run-android",
            "--prepared",
            job["prepared"],
            "--runtime-config",
            str(self.campaign / job["runtime_config"]),
            "--core-config",
            str(self.campaign / job["core_config"]),
            "--run-id",
            job["run_id"],
            "--output",
            str(self.campaign / "runs" / job["job_id"] / "attempt-0001"),
            "--adb",
            self.config.adb_executable,
            "--aapt",
            self.config.aapt_executable,
        ]

    def _events(self, *, claimed_status: str, attempt_overrides=None) -> list[dict]:
        job = self._job()
        job_id, device_id = job["job_id"], job["device_id"]
        identity = support.attach_identity(self._job_serial())
        attempt = self._attempt_result(**(attempt_overrides or {}))
        job_result = {
            "job_id": job_id,
            "run_id": job["run_id"],
            "device_id": device_id,
            "prepared_bundle_sha256": job["prepared_bundle_sha256"],
            "universe_sha256": job["universe_sha256"],
            "status": attempt["status"],
            "attempts": [attempt],
        }
        return [
            support.event(
                "campaign_started",
                1.0,
                details={
                    "assignment_version": self.plan["assignment_version"],
                    "execution_id": "execution-" + "a" * 32,
                    "plan_sha256": self.plan["plan_sha256"],
                },
            ),
            support.event("attempt_started", 10.0, job_id=job_id, device_id=device_id, attempt=1),
            support.event(
                "device_ready",
                10.2,
                job_id=job_id,
                device_id=device_id,
                attempt=1,
                details={"identity": identity},
            ),
            support.event(
                "child_started",
                10.4,
                job_id=job_id,
                device_id=device_id,
                attempt=1,
                details={
                    "argv": self._child_argv(job),
                    "pid": 4321,
                    "process_group": 4321,
                },
            ),
            support.event(
                "attempt_finished",
                11.5,
                job_id=job_id,
                device_id=device_id,
                attempt=1,
                details={"result": attempt},
            ),
            support.event(
                "job_finished",
                11.6,
                job_id=job_id,
                device_id=device_id,
                details={"result": job_result},
            ),
            support.event(
                "campaign_finished",
                12.0,
                details={"status": claimed_status, "reason": "one_or_more_jobs_failed"},
            ),
        ]

    def test_replay_derives_failed_status_for_an_invalid_job(self) -> None:
        manifest = build_campaign_manifest(
            self.config, self.plan, self._events(claimed_status="failed")
        )
        self.assertEqual(manifest["status"], "failed")

    def test_claimed_finished_status_is_rejected(self) -> None:
        """Review 1 #1: a producer could declare a failed campaign finished."""

        with self.assertRaises(ValueError):
            build_campaign_manifest(
                self.config, self.plan, self._events(claimed_status="finished")
            )

    def test_attempt_start_before_its_event_is_rejected(self) -> None:
        """The timing chain must stay anchored to recorded events."""

        with self.assertRaises(ValueError):
            build_campaign_manifest(
                self.config,
                self.plan,
                self._events(
                    claimed_status="failed", attempt_overrides={"started_at": 9.0}
                ),
            )

    def test_attempt_finished_after_its_event_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_campaign_manifest(
                self.config,
                self.plan,
                self._events(
                    claimed_status="failed", attempt_overrides={"finished_at": 99.0}
                ),
            )

    def test_success_without_a_child_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_campaign_manifest(
                self.config,
                self.plan,
                self._events(
                    claimed_status="finished",
                    attempt_overrides={
                        "status": "succeeded",
                        "exit_code": 0,
                        "error": None,
                        "run_status": "finished",
                        "validation_ok": True,
                    },
                ),
            )

    def test_missing_terminal_event_is_rejected(self) -> None:
        events = self._events(claimed_status="failed")[:-1]
        with self.assertRaises(ValueError):
            build_campaign_manifest(self.config, self.plan, events)


if __name__ == "__main__":
    unittest.main()
