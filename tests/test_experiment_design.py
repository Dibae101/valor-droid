"""Repeated-measures experiment design: repetitions, arms, seeded assignment.

Section 5 of the gap audit asks for a frozen baseline with randomized paired
device assignment and run order, at least three paired repetitions per app, and a
primary statistic that is the mean over an app's predeclared repetitions rather
than the best available run. None of that is expressible unless the plan records
which arm and which app each job belongs to, which repetition it is, and how the
randomization can be recomputed.

These tests pin the parts that can go wrong silently:
- a seeded plan must be reproducible byte for byte, or "randomized" means "lost";
- verification must recompute the permutation instead of asserting a fixed sorted
  round robin, so a hand-edited assignment cannot pass;
- a null seed must keep behaving exactly like the previous sorted assignment;
- an unbalanced set of arms must be refused before it is averaged.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from valordroid.fleet_config import (
    ASSIGNMENT_VERSION,
    MAX_REPETITIONS,
    FleetConfig,
    assign_devices,
    assignment_order,
)
from valordroid.fleet_plan import (
    PLAN_SCHEMA_VERSION,
    create_fleet_plan,
    verify_fleet_plan,
)

from . import support


class AssignmentAlgorithmTest(unittest.TestCase):
    """The permutation is a pure function of its declared inputs."""

    JOBS = tuple(f"job-{index:02d}" for index in range(12))
    DEVICES = ("slot-0", "slot-1", "slot-2")

    def test_null_seed_keeps_the_legacy_sorted_order(self) -> None:
        self.assertEqual(
            assignment_order(self.JOBS, assignment_seed=None), sorted(self.JOBS)
        )
        assignment = assign_devices(self.JOBS, self.DEVICES, assignment_seed=None)
        for index, job_id in enumerate(sorted(self.JOBS)):
            self.assertEqual(assignment[job_id], sorted(self.DEVICES)[index % 3])

    def test_a_seed_permutes_the_sorted_order(self) -> None:
        seeded = assignment_order(self.JOBS, assignment_seed="screen-2026-09")
        self.assertEqual(sorted(seeded), sorted(self.JOBS))
        self.assertNotEqual(seeded, sorted(self.JOBS))

    def test_the_same_seed_reproduces_the_same_order(self) -> None:
        first = assignment_order(self.JOBS, assignment_seed="screen-2026-09")
        second = assignment_order(self.JOBS, assignment_seed="screen-2026-09")
        self.assertEqual(first, second)

    def test_a_different_seed_gives_a_different_order(self) -> None:
        self.assertNotEqual(
            assignment_order(self.JOBS, assignment_seed="seed-a"),
            assignment_order(self.JOBS, assignment_seed="seed-b"),
        )

    def test_the_assignment_version_is_part_of_the_key(self) -> None:
        """Bumping the algorithm must not silently keep old assignments."""

        self.assertNotEqual(
            assignment_order(self.JOBS, assignment_seed="seed-a"),
            assignment_order(
                self.JOBS,
                assignment_seed="seed-a",
                assignment_version="sorted-round-robin-v1",
            ),
        )

    def test_the_order_depends_on_the_whole_declared_job_set(self) -> None:
        """Adding a job reshuffles the design instead of appending to it."""

        smaller = assignment_order(self.JOBS[:-1], assignment_seed="seed-a")
        larger = assignment_order(self.JOBS, assignment_seed="seed-a")
        self.assertNotEqual(smaller, [item for item in larger if item in set(smaller)])

    def test_every_device_receives_a_balanced_share(self) -> None:
        assignment = assign_devices(
            self.JOBS, self.DEVICES, assignment_seed="screen-2026-09"
        )
        counts = {device: 0 for device in self.DEVICES}
        for device in assignment.values():
            counts[device] += 1
        self.assertEqual(sorted(counts.values()), [4, 4, 4])

    def test_duplicate_job_identities_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            assignment_order(("job-0", "job-0"), assignment_seed="seed-a")

    def test_an_unsafe_seed_is_refused(self) -> None:
        for seed in ("", " ", "seed with spaces", "seed/../escape", "x" * 200):
            with self.assertRaises(ValueError, msg=seed):
                assignment_order(self.JOBS, assignment_seed=seed)


def _cell_jobs(
    prepared: Path,
    runtime_path: Path,
    core_path: Path,
    *,
    arms: tuple[str, ...],
    apps: tuple[str, ...],
    repetitions: int,
) -> list[dict[str, object]]:
    jobs: list[dict[str, object]] = []
    for arm in arms:
        for app in apps:
            for repetition in range(1, repetitions + 1):
                job_id = f"{arm}-{app}-r{repetition}"
                jobs.append(
                    {
                        "job_id": job_id,
                        "run_id": job_id,
                        "prepared": str(prepared),
                        "runtime_config": str(runtime_path),
                        "core_config": str(core_path),
                        "max_attempts": 1,
                        "arm": arm,
                        "app_id": app,
                        "repetition": repetition,
                    }
                )
    return jobs


class ExperimentConfigTest(unittest.TestCase):
    """Arms, apps, and repetitions must be balanced and identifiable."""

    @classmethod
    def setUpClass(cls) -> None:
        support.suppress_environment_noise()
        cls._root = Path(tempfile.mkdtemp(prefix="valordroid-design."))
        cls.tools = support.fake_tools(cls._root / "tools")
        cls.prepared = support.build_prepared(cls._root / "work", cls.tools["aapt"])
        cls.runtime_path, cls.core_path = support.write_runtime_and_core(
            cls._root / "work"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._root, ignore_errors=True)

    def _config(
        self,
        *,
        arms: tuple[str, ...] = ("control", "treatment"),
        apps: tuple[str, ...] = ("appone", "apptwo"),
        repetitions: int = 3,
        assignment_seed: str | None = "screen-2026-09",
        jobs: list[dict[str, object]] | None = None,
        declared_repetitions: int | None = None,
    ) -> dict[str, object]:
        job_list = (
            jobs
            if jobs is not None
            else _cell_jobs(
                self.prepared,
                self.runtime_path,
                self.core_path,
                arms=arms,
                apps=apps,
                repetitions=repetitions,
            )
        )
        device_count = max(2, min(8, len(job_list)))
        return {
            "schema_version": 2,
            "campaign_id": "design",
            "mode": "manage",
            "max_parallel": 2,
            "adb_executable": self.tools["adb"],
            "docker_executable": self.tools["docker"],
            "aapt_executable": self.tools["aapt"],
            "image": "redroid@sha256:" + "a" * 64,
            "owner_label": "regression",
            "lease_root": str(self._root / "leases"),
            "readiness_timeout_seconds": 3.0,
            "poll_interval_seconds": 0.05,
            "command_timeout_seconds": 10.0,
            "attempt_timeout_seconds": 90.0,
            "interrupt_grace_seconds": 1.0,
            "terminate_grace_seconds": 1.0,
            "kill_grace_seconds": 1.0,
            "remove_owned_containers": True,
            "reclaim_abandoned_containers": False,
            "assignment_seed": assignment_seed,
            "repetitions": (
                repetitions if declared_repetitions is None else declared_repetitions
            ),
            "devices": [
                {
                    "device_id": f"slot-{index}",
                    "serial": f"127.0.0.1:{6000 + 2 * index}",
                    "container_name": f"redroid-{index}",
                    "data_root": str(self._root / "data" / f"slot-{index}"),
                    "cpus": 1.0,
                    "memory": "1g",
                    "androidboot_args": [],
                    "expected_properties": {},
                }
                for index in range(device_count)
            ],
            "jobs": job_list,
        }

    def _write(self, value: dict[str, object], name: str) -> Path:
        path = self._root / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_a_balanced_design_loads(self) -> None:
        config = FleetConfig.from_mapping(self._config())
        self.assertEqual(config.repetitions, 3)
        self.assertEqual(config.assignment_seed, "screen-2026-09")
        self.assertEqual(len(config.jobs), 12)
        self.assertEqual({job.arm for job in config.jobs}, {"control", "treatment"})
        self.assertEqual({job.app_id for job in config.jobs}, {"appone", "apptwo"})

    def test_a_missing_cell_is_refused(self) -> None:
        """One arm measuring fewer apps cannot be paired app by app."""

        value = self._config()
        jobs = [
            job
            for job in value["jobs"]  # type: ignore[index]
            if not (job["arm"] == "treatment" and job["app_id"] == "apptwo")
        ]
        value["jobs"] = jobs
        with self.assertRaises(ValueError) as caught:
            FleetConfig.from_mapping(value)
        self.assertIn("unbalanced", str(caught.exception))

    def test_an_extra_repetition_in_one_cell_is_refused(self) -> None:
        value = self._config()
        jobs = list(value["jobs"])  # type: ignore[arg-type]
        extra = dict(jobs[0])
        extra.update({"job_id": "control-appone-r4", "run_id": "control-appone-r4"})
        value["jobs"] = jobs + [extra]
        with self.assertRaises(ValueError) as caught:
            FleetConfig.from_mapping(value)
        self.assertIn("unbalanced", str(caught.exception))

    def test_nonconsecutive_repetition_ordinals_are_refused(self) -> None:
        value = self._config()
        jobs = [dict(job) for job in value["jobs"]]  # type: ignore[arg-type]
        for job in jobs:
            if job["arm"] == "control" and job["app_id"] == "appone" and job["repetition"] == 3:
                job["repetition"] = 1
        value["jobs"] = jobs
        with self.assertRaises(ValueError) as caught:
            FleetConfig.from_mapping(value)
        self.assertIn("ordinals", str(caught.exception))

    def test_a_partially_declared_design_is_refused(self) -> None:
        """Half the jobs naming an arm is not a design; it is a mistake."""

        value = self._config(repetitions=1)
        jobs = [dict(job) for job in value["jobs"]]  # type: ignore[arg-type]
        jobs[0]["arm"] = None
        jobs[0]["app_id"] = None
        value["jobs"] = jobs
        with self.assertRaises(ValueError) as caught:
            FleetConfig.from_mapping(value)
        self.assertIn("every job or on none", str(caught.exception))

    def test_arm_without_app_id_is_refused(self) -> None:
        value = self._config(repetitions=1)
        jobs = [dict(job) for job in value["jobs"]]  # type: ignore[arg-type]
        for job in jobs:
            job["app_id"] = None
        value["jobs"] = jobs
        with self.assertRaises(ValueError):
            FleetConfig.from_mapping(value)

    def test_repetitions_without_declared_cells_are_refused(self) -> None:
        """A repetition count needs to say which cell each repeat belongs to."""

        value = self._config(repetitions=1, declared_repetitions=3)
        jobs = [dict(job) for job in value["jobs"]]  # type: ignore[arg-type]
        for job in jobs:
            job["arm"] = None
            job["app_id"] = None
        value["jobs"] = jobs
        with self.assertRaises(ValueError) as caught:
            FleetConfig.from_mapping(value)
        self.assertIn("declare its arm and app_id", str(caught.exception))

    def test_an_out_of_range_repetition_count_is_refused(self) -> None:
        for count in (0, -1, MAX_REPETITIONS + 1):
            value = self._config(repetitions=1, declared_repetitions=count)
            with self.assertRaises(ValueError, msg=str(count)):
                FleetConfig.from_mapping(value)

    def test_a_schema_two_document_without_the_new_fields_still_loads(self) -> None:
        """The added design fields are optional, so frozen configs keep working."""

        value = self._config(repetitions=1, assignment_seed=None)
        jobs = [
            {
                name: job[name]
                for name in (
                    "job_id",
                    "run_id",
                    "prepared",
                    "runtime_config",
                    "core_config",
                    "max_attempts",
                )
            }
            for job in value["jobs"]  # type: ignore[union-attr]
        ]
        value["jobs"] = jobs
        del value["assignment_seed"]
        del value["repetitions"]
        config = FleetConfig.from_mapping(value)
        self.assertIsNone(config.assignment_seed)
        self.assertEqual(config.repetitions, 1)
        self.assertTrue(all(job.arm is None for job in config.jobs))
        self.assertTrue(all(job.repetition == 1 for job in config.jobs))


class SeededPlanTest(unittest.TestCase):
    """A frozen plan must be reproducible and independently recomputable."""

    @classmethod
    def setUpClass(cls) -> None:
        support.suppress_environment_noise()
        cls._root = Path(tempfile.mkdtemp(prefix="valordroid-seedplan."))
        cls.tools = support.fake_tools(cls._root / "tools")
        cls.prepared = support.build_prepared(cls._root / "work", cls.tools["aapt"])
        cls.runtime_path, cls.core_path = support.write_runtime_and_core(
            cls._root / "work"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._root, ignore_errors=True)

    def _config_path(
        self, name: str, *, assignment_seed: str | None, repetitions: int = 2
    ) -> Path:
        jobs = _cell_jobs(
            self.prepared,
            self.runtime_path,
            self.core_path,
            arms=("control", "treatment"),
            apps=("appone", "apptwo"),
            repetitions=repetitions,
        )
        value = {
            "schema_version": 2,
            "campaign_id": "seeded",
            "mode": "attach",
            "max_parallel": 2,
            "adb_executable": self.tools["adb"],
            "docker_executable": self.tools["docker"],
            "aapt_executable": self.tools["aapt"],
            "image": None,
            "owner_label": "regression",
            "lease_root": str(self._root / "leases"),
            "readiness_timeout_seconds": 3.0,
            "poll_interval_seconds": 0.05,
            "command_timeout_seconds": 10.0,
            "attempt_timeout_seconds": 90.0,
            "interrupt_grace_seconds": 1.0,
            "terminate_grace_seconds": 1.0,
            "kill_grace_seconds": 1.0,
            "remove_owned_containers": False,
            "reclaim_abandoned_containers": False,
            "assignment_seed": assignment_seed,
            "repetitions": repetitions,
            "devices": [
                {
                    "device_id": f"slot-{index}",
                    "serial": f"127.0.0.1:{7100 + 2 * index}",
                    "container_name": None,
                    "data_root": None,
                    "cpus": None,
                    "memory": None,
                    "androidboot_args": [],
                    "expected_properties": {},
                }
                for index in range(len(jobs))
            ],
            "jobs": jobs,
        }
        path = self._root / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_the_same_seed_produces_a_byte_identical_plan(self) -> None:
        config_path = self._config_path("same", assignment_seed="screen-2026-09")
        first = self._root / "campaign-first"
        second = self._root / "campaign-second"
        create_fleet_plan(config_path, first)
        create_fleet_plan(config_path, second)
        self.assertEqual(
            (first / "campaign-plan.json").read_bytes(),
            (second / "campaign-plan.json").read_bytes(),
        )
        self.assertEqual(
            (first / "fleet-config.json").read_bytes(),
            (second / "fleet-config.json").read_bytes(),
        )

    def test_a_different_seed_produces_a_different_assignment(self) -> None:
        left = self._root / "campaign-seed-a"
        right = self._root / "campaign-seed-b"
        create_fleet_plan(self._config_path("seed-a", assignment_seed="seed-alpha"), left)
        create_fleet_plan(self._config_path("seed-b", assignment_seed="seed-omega"), right)
        _, first = verify_fleet_plan(left, verify_prepared=False)
        _, second = verify_fleet_plan(right, verify_prepared=False)
        self.assertNotEqual(
            [item["device_id"] for item in first["jobs"]],
            [item["device_id"] for item in second["jobs"]],
        )

    def test_the_plan_records_the_design_and_the_seed(self) -> None:
        campaign = self._root / "campaign-design"
        plan = create_fleet_plan(
            self._config_path("design", assignment_seed="screen-2026-09"), campaign
        )
        self.assertEqual(plan["schema_version"], PLAN_SCHEMA_VERSION)
        self.assertEqual(plan["assignment_version"], ASSIGNMENT_VERSION)
        self.assertEqual(plan["assignment_seed"], "screen-2026-09")
        self.assertEqual(plan["repetitions"], 2)
        cells = sorted(
            (item["arm"], item["app_id"], item["repetition"]) for item in plan["jobs"]
        )
        self.assertEqual(
            cells,
            [
                ("control", "appone", 1),
                ("control", "appone", 2),
                ("control", "apptwo", 1),
                ("control", "apptwo", 2),
                ("treatment", "appone", 1),
                ("treatment", "appone", 2),
                ("treatment", "apptwo", 1),
                ("treatment", "apptwo", 2),
            ],
        )

    def test_verification_recomputes_the_permutation(self) -> None:
        """A hand-edited device assignment must not survive verification."""

        campaign = self._root / "campaign-tamper"
        create_fleet_plan(
            self._config_path("tamper", assignment_seed="screen-2026-09"), campaign
        )
        plan_path = campaign / "campaign-plan.json"
        plan = json.loads(plan_path.read_text())
        devices = [item["device_id"] for item in plan["devices"]]
        swapped = next(
            item
            for item in plan["jobs"]
            if item["device_id"] != devices[-1]
        )
        swapped["device_id"] = devices[-1]
        # Re-seal the digest so only the assignment itself is wrong.
        from valordroid.models import stable_hash

        payload = {name: plan[name] for name in plan if name != "plan_sha256"}
        plan["plan_sha256"] = stable_hash(payload)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            verify_fleet_plan(campaign, verify_prepared=False)
        self.assertIn("recomputed", str(caught.exception))

    def test_changing_the_recorded_seed_is_refused(self) -> None:
        campaign = self._root / "campaign-reseed"
        create_fleet_plan(
            self._config_path("reseed", assignment_seed="screen-2026-09"), campaign
        )
        plan_path = campaign / "campaign-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["assignment_seed"] = "another-seed"
        from valordroid.models import stable_hash

        payload = {name: plan[name] for name in plan if name != "plan_sha256"}
        plan["plan_sha256"] = stable_hash(payload)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(ValueError):
            verify_fleet_plan(campaign, verify_prepared=False)

    def test_a_null_seed_plan_keeps_the_sorted_assignment(self) -> None:
        campaign = self._root / "campaign-legacy"
        plan = create_fleet_plan(
            self._config_path("legacy", assignment_seed=None), campaign
        )
        self.assertIsNone(plan["assignment_seed"])
        devices = sorted(item["device_id"] for item in plan["devices"])
        ordered = sorted(item["job_id"] for item in plan["jobs"])
        assignment = {item["job_id"]: item["device_id"] for item in plan["jobs"]}
        for index, job_id in enumerate(ordered):
            self.assertEqual(assignment[job_id], devices[index % len(devices)])
        verify_fleet_plan(campaign, verify_prepared=False)


if __name__ == "__main__":
    unittest.main()
