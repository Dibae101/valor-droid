"""Real-slot acceptance regressions, driven against the committed fakes.

Pinned findings (review 6):
- #6: `package_manager_responsive` could not fail.
- #7: attach-mode teardown and distinctness steps passed without acting.
- #10: a fixed job ID made manage-mode acceptance single-shot.
- #11: the provider step recorded an object repr.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from valordroid.acceptance import ACCEPTANCE_SCHEMA_VERSION, run_fleet_acceptance

from . import support


class AttachAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        support.suppress_environment_noise()
        cls._root = Path(tempfile.mkdtemp(prefix="valordroid-accept."))
        cls.tools = support.fake_tools(cls._root / "tools")
        prepared = support.build_prepared(cls._root / "work", cls.tools["aapt"])
        cls.config_path = support.attach_fleet_config(
            cls._root / "work",
            prepared,
            cls.tools,
            lease_root=cls._root / "leases",
            jobs=2,
        )
        cls.report = run_fleet_acceptance(cls.config_path)
        cls.steps = {item["name"]: item for item in cls.report["steps"]}

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._root, ignore_errors=True)

    def test_report_is_versioned_and_makes_no_coverage_claim(self) -> None:
        self.assertEqual(self.report["schema_version"], ACCEPTANCE_SCHEMA_VERSION)
        self.assertIn("provisioning only", self.report["coverage_claim"])

    def test_provider_step_records_resolved_paths_not_an_object(self) -> None:
        """Review 6 #11: the detail embedded a heap address."""

        detail = self.steps["provider_resolved"]["detail"]
        self.assertIn("adb=", detail)
        self.assertNotIn("object at 0x", detail)

    def test_readiness_and_identity_are_established(self) -> None:
        self.assertEqual(self.steps["slot_lease_acquired"]["status"], "pass")
        self.assertEqual(self.steps["provisioned_and_ready"]["status"], "pass")
        self.assertEqual(self.steps["expected_properties_matched"]["status"], "pass")

    def test_package_manager_step_fails_on_an_unresponsive_device(self) -> None:
        """Review 6 #6: the step opted out of error checking."""

        self.assertEqual(self.steps["package_manager_responsive"]["status"], "fail")

    def test_attach_mode_does_not_claim_container_teardown(self) -> None:
        """Review 6 #7: a no-op step reported success."""

        step = self.steps["cleanup_by_revalidated_container_id"]
        self.assertEqual(step["status"], "not_applicable")
        self.assertIn("never owns a container", step["detail"])

    def test_attach_mode_does_not_claim_slot_distinctness(self) -> None:
        step = self.steps["concurrent_slots_are_distinct"]
        self.assertEqual(step["status"], "not_applicable")

    def test_report_separates_established_from_not_established(self) -> None:
        self.assertIn("provisioned_and_ready", self.report["established"])
        self.assertIn(
            "cleanup_by_revalidated_container_id", self.report["not_established"]
        )

    def test_acceptance_fails_overall_when_a_step_fails(self) -> None:
        self.assertFalse(self.report["ok"])

    def test_job_ids_are_unique_per_run(self) -> None:
        """Review 6 #10: a fixed job ID collided with retained data."""

        again = run_fleet_acceptance(self.config_path)
        self.assertNotEqual(
            sorted(self.report["job_ids"].values()),
            sorted(again["job_ids"].values()),
        )
        self.assertNotEqual(self.report["execution_id"], again["execution_id"])

    def test_leases_are_released_so_a_later_run_can_acquire_them(self) -> None:
        again = run_fleet_acceptance(self.config_path)
        steps = {item["name"]: item for item in again["steps"]}
        self.assertEqual(steps["slot_lease_acquired"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
