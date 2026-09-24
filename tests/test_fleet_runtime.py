"""Lease, supervision, clock, and configuration regressions.

Pinned findings:
- review 1 #3: same-plan labels permitted deleting a live peer's container.
- review 2 #7: the lease key split one physical slot across modes.
- review 3 #8: `localhost:5555` and `127.0.0.1:5555` took different locks.
- review 3 #9: a lease conflict named no holder.
- review 4 #4: the descendant gate was vacuous.
- review 5 #4: `monotone_now()` never recorded the floor it handed out.
- review 5 #5: lease-root creation escaped the permission message.
- review 5 #11: the /proc walk ran on every poll.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from valordroid.campaign import CampaignStore
from valordroid.fleet_config import FLEET_SCHEMA_VERSION, DeviceSpec, FleetConfig
from valordroid.fleet_runner import FleetRunner
from valordroid.redroid import SlotLease

from . import support


def _device(serial: str, container: str | None = None) -> DeviceSpec:
    return DeviceSpec(
        device_id="slot-0",
        serial=serial,
        container_name=container,
        data_root="/tmp/valordroid-test-data" if container else None,
        cpus=1.0 if container else None,
        memory="1g" if container else None,
        androidboot_args=(),
        expected_properties={},
    )


class SlotLeaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="valordroid-lease."))
        self.lease_root = self._root / "leases"
        self.serial = support.free_port_serials(1)[0]

    def tearDown(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def _lease(self, serial: str, execution: str, mode: str, container=None) -> SlotLease:
        return SlotLease(
            self.lease_root,
            device=_device(serial, container),
            execution_id=f"execution-{execution * 32}",
            mode=mode,
        )

    def test_one_slot_cannot_be_leased_twice(self) -> None:
        first = self._lease(self.serial, "a", "attach")
        second = self._lease(self.serial, "b", "attach")
        first.acquire()
        try:
            with self.assertRaises(RuntimeError):
                second.acquire()
        finally:
            first.release()

    def test_attach_and_manage_contend_for_one_physical_slot(self) -> None:
        """Review 2 #7 and 3 #8: mode and container name must not split the key."""

        attach = self._lease(self.serial, "a", "attach")
        manage = self._lease(self.serial, "b", "manage", container="other-name")
        attach.acquire()
        try:
            with self.assertRaises(RuntimeError):
                manage.acquire()
        finally:
            attach.release()

    def test_loopback_spellings_share_one_lock(self) -> None:
        host, port = self.serial.rsplit(":", 1)
        del host
        localhost = self._lease(f"localhost:{port}", "a", "attach")
        loopback = self._lease(f"127.0.0.1:{port}", "b", "attach")
        localhost.acquire()
        try:
            with self.assertRaises(RuntimeError):
                loopback.acquire()
        finally:
            localhost.release()

    def test_conflict_names_the_recorded_holder(self) -> None:
        """Review 3 #9: the operator was sent hunting for an unnamed peer."""

        holder = self._lease(self.serial, "a", "attach")
        other = self._lease(self.serial, "b", "attach")
        holder.acquire()
        try:
            with self.assertRaises(RuntimeError) as caught:
                other.acquire()
            self.assertIn("execution-" + "a" * 32, str(caught.exception))
        finally:
            holder.release()

    def test_lease_is_reusable_after_release(self) -> None:
        lease = self._lease(self.serial, "a", "attach")
        lease.acquire()
        lease.release()
        again = self._lease(self.serial, "b", "attach")
        again.acquire()
        again.release()

    def test_unusable_lease_root_explains_the_single_user_assumption(self) -> None:
        """Review 5 #5: creation raised a bare errno instead of the message."""

        if os.geteuid() == 0:
            self.skipTest("root bypasses directory permissions")
        blocked = self._root / "blocked"
        blocked.mkdir()
        blocked.chmod(0o500)
        try:
            lease = SlotLease(
                blocked / "leases",
                device=_device(self.serial),
                execution_id="execution-" + "c" * 32,
                mode="attach",
            )
            with self.assertRaises(RuntimeError) as caught:
                lease.acquire()
            self.assertIn("lease_root", str(caught.exception))
        finally:
            blocked.chmod(0o700)


class MonotoneClockTest(unittest.TestCase):
    def setUp(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="valordroid-clock."))
        (self._root / "campaign-events.jsonl").touch()
        self.store = CampaignStore(self._root)

    def tearDown(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def test_monotone_now_records_the_floor_it_returns(self) -> None:
        """Review 5 #4: a handed-out timestamp could precede a later event."""

        first = self.store.monotone_now()
        second = self.store.monotone_now()
        self.assertGreaterEqual(second, first)
        recorded = self.store.append_event("campaign_started", first - 500.0, details={})
        self.assertGreaterEqual(recorded, second)

    def test_append_event_returns_the_recorded_timestamp(self) -> None:
        recorded = self.store.append_event("campaign_started", 10.0, details={})
        self.assertEqual(recorded, 10.0)
        clamped = self.store.append_event("campaign_interrupted", 5.0, details={})
        self.assertEqual(clamped, 10.0)


class SupervisionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._root = Path(tempfile.mkdtemp(prefix="valordroid-supervision."))
        tools = support.fake_tools(cls._root / "tools")
        prepared = support.build_prepared(cls._root / "work", tools["aapt"])
        config_path = support.attach_fleet_config(
            cls._root / "work",
            prepared,
            tools,
            lease_root=cls._root / "leases",
            jobs=1,
        )
        from valordroid.fleet_plan import create_fleet_plan

        campaign = cls._root / "campaign"
        create_fleet_plan(config_path, campaign)
        cls.runner = FleetRunner(campaign)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._root, ignore_errors=True)

    def test_running_leader_is_supervised(self) -> None:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.5)"],
            start_new_session=True,
        )
        try:
            self.assertTrue(self.runner._supervised_alive(child))
        finally:
            child.wait()

    def test_reaped_childless_leader_is_not_supervised(self) -> None:
        """Review 4 #4: the leader is a member of its own group."""

        child = subprocess.Popen(
            [sys.executable, "-c", "pass"], start_new_session=True
        )
        child.wait()
        self.assertFalse(self.runner._supervised_alive(child))

    def test_orphaned_descendant_keeps_the_attempt_supervised(self) -> None:
        spawner = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import subprocess,sys;"
                "subprocess.Popen([sys.executable,'-c','import time; time.sleep(1.5)'])",
            ],
            start_new_session=True,
        )
        spawner.wait()
        try:
            self.assertTrue(self.runner._supervised_alive(spawner))
        finally:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and self.runner._supervised_alive(spawner):
                time.sleep(0.1)

    def test_dead_group_skips_the_exact_membership_walk(self) -> None:
        """Review 5 #11: the /proc walk must not run when the group is gone."""

        child = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        child.wait()
        self.assertFalse(self.runner._group_possibly_alive(child.pid))

        walked = []
        original = type(self.runner)._has_other_group_member

        def spy(process_group: int) -> bool:
            walked.append(process_group)
            return original(process_group)

        type(self.runner)._has_other_group_member = staticmethod(spy)
        try:
            self.assertFalse(self.runner._supervised_alive(child))
            self.assertEqual(walked, [], "the exact walk should have been skipped")
        finally:
            type(self.runner)._has_other_group_member = staticmethod(original)


class FleetConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="valordroid-config."))
        self.tools = support.fake_tools(self._root / "tools")
        self.prepared = support.build_prepared(self._root / "work", self.tools["aapt"])
        self.config_path = support.attach_fleet_config(
            self._root / "work",
            self.prepared,
            self.tools,
            lease_root=self._root / "leases",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def test_current_schema_loads(self) -> None:
        config = FleetConfig.load(self.config_path)
        self.assertEqual(config.schema_version, FLEET_SCHEMA_VERSION)
        self.assertTrue(Path(config.lease_root).is_absolute())

    def test_previous_schema_version_is_refused(self) -> None:
        """Review 3 #13: required fields changed without a schema bump."""

        import json

        value = json.loads(self.config_path.read_text())
        value["schema_version"] = 1
        downgraded = self._root / "old.json"
        downgraded.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            FleetConfig.load(downgraded)

    def test_attach_mode_refuses_container_reclaim(self) -> None:
        import json

        value = json.loads(self.config_path.read_text())
        value["reclaim_abandoned_containers"] = True
        path = self._root / "attach-reclaim.json"
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            FleetConfig.load(path)

    def test_relative_lease_root_is_refused(self) -> None:
        import json

        value = json.loads(self.config_path.read_text())
        value["lease_root"] = "relative/leases"
        path = self._root / "relative.json"
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            FleetConfig.load(path)

    def test_the_experiment_design_defaults_are_the_previous_behaviour(self) -> None:
        """A config written before the design fields existed must not change."""

        config = FleetConfig.load(self.config_path)
        self.assertIsNone(config.assignment_seed)
        self.assertEqual(config.repetitions, 1)
        self.assertTrue(all(job.arm is None and job.app_id is None for job in config.jobs))
        self.assertTrue(all(job.cell is None for job in config.jobs))

    def test_a_declared_seed_round_trips_through_the_document(self) -> None:
        import json

        value = json.loads(self.config_path.read_text())
        value["assignment_seed"] = "screen-2026-09"
        path = self._root / "seeded.json"
        path.write_text(json.dumps(value))
        config = FleetConfig.load(path)
        self.assertEqual(config.assignment_seed, "screen-2026-09")
        self.assertEqual(config.to_dict()["assignment_seed"], "screen-2026-09")

    def test_an_unsafe_seed_in_the_document_is_refused(self) -> None:
        import json

        value = json.loads(self.config_path.read_text())
        value["assignment_seed"] = "seed with spaces"
        path = self._root / "unsafe-seed.json"
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            FleetConfig.load(path)

    def test_a_nonstring_seed_is_refused(self) -> None:
        import json

        value = json.loads(self.config_path.read_text())
        value["assignment_seed"] = 17
        path = self._root / "numeric-seed.json"
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            FleetConfig.load(path)

    def test_an_unknown_field_is_still_refused(self) -> None:
        """Optional design fields must not turn the contract into a free-for-all."""

        import json

        value = json.loads(self.config_path.read_text())
        value["assignment_seeed"] = "typo"
        path = self._root / "typo.json"
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            FleetConfig.load(path)

    def test_an_unknown_job_field_is_still_refused(self) -> None:
        import json

        value = json.loads(self.config_path.read_text())
        value["jobs"][0]["app"] = "appone"
        path = self._root / "job-typo.json"
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            FleetConfig.load(path)


if __name__ == "__main__":
    unittest.main()
