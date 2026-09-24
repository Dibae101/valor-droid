"""A broken device must not be recorded as a broken app.

The v2 campaign lost six apps in nineteen seconds on one lane. Device 5568's
framework was not up, so every install failed with

    adb: failed to install ...: cmd: Can't find service: package

while adb answered every command and sys.boot_completed was already 1. The
driver called the device healthy, blamed the app, and moved to the next one --
Chess, Jellyfin, Muzei, Open-Food-Facts, Sunflower and Vinyl-Music-Player were
all written off in turn, each in a few seconds.

Two things are pinned here: that failure is infrastructure and earns a retry, and
readiness means the package service answers rather than adb answering.
"""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "app" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import run_coverage_experiment as driver  # noqa: E402


class FaultClassificationTest(unittest.TestCase):
    def test_a_missing_package_service_is_infrastructure(self) -> None:
        record = {
            "error": "ADB command failed (adb -s 127.0.0.1:5568 install -g app.apk): "
            "adb: failed to install app.apk: cmd: Can't find service: package"
        }
        self.assertEqual(driver.fault_kind(record), "infrastructure")
        self.assertTrue(driver.infrastructure_fault(record))

    def test_the_other_spelling_is_matched_too(self) -> None:
        record = {"error": "cmd: Cannot find service: package"}
        self.assertTrue(driver.infrastructure_fault(record))

    def test_an_offline_device_is_still_infrastructure(self) -> None:
        self.assertTrue(driver.infrastructure_fault({"error": "device offline"}))

    def test_a_lost_collector_is_still_infrastructure(self) -> None:
        self.assertTrue(
            driver.infrastructure_fault({"error": "coverage_collection_failed"})
        )

    def test_an_app_that_will_not_launch_is_not_retried_as_infrastructure(self) -> None:
        """Amaze failed this way in the same campaign and is a real app fault."""
        record = {
            "error": "prepared package did not become foreground after launch; "
            "last resumed activities=['com.android.launcher3/.QuickstepLauncher']"
        }
        self.assertFalse(driver.infrastructure_fault(record))

    def test_an_app_crash_is_not_retried_as_infrastructure(self) -> None:
        record = {"error": "java.lang.NullPointerException"}
        self.assertFalse(driver.infrastructure_fault(record))

    def test_a_successful_run_is_not_a_fault(self) -> None:
        self.assertFalse(driver.infrastructure_fault({"observed_coverage_percent": 41.2}))


class DeviceReadinessTest(unittest.TestCase):
    """Readiness is the package service answering, not adb answering."""

    def setUp(self) -> None:
        self.calls: list[list[str]] = []
        self._real = driver.subprocess.run

        def fake(arguments, **kwargs):
            self.calls.append(list(arguments))
            return self._reply(list(arguments))

        driver.subprocess.run = fake
        self.addCleanup(setattr, driver.subprocess, "run", self._real)

    @staticmethod
    def _result(stdout: str, returncode: int = 0):
        return type("_R", (), {"stdout": stdout, "stderr": "", "returncode": returncode})()

    def test_a_device_whose_package_service_is_missing_is_not_ready(self) -> None:
        def reply(arguments):
            if "sys.boot_completed" in arguments:
                return self._result("1\n")
            return self._result("Can't find service: package\n")

        self._reply = reply
        self.assertFalse(driver.device_ready("127.0.0.1:5568"))

    def test_a_fully_started_device_is_ready(self) -> None:
        def reply(arguments):
            if "sys.boot_completed" in arguments:
                return self._result("1\n")
            return self._result("package:/system/framework/framework-res.apk\n")

        self._reply = reply
        self.assertTrue(driver.device_ready("127.0.0.1:5561"))
        # Both questions are asked: boot alone was what made 5568 look healthy.
        self.assertEqual(len(self.calls), 2)

    def test_a_device_still_booting_is_not_ready(self) -> None:
        self._reply = lambda arguments: self._result("\n")
        self.assertFalse(driver.device_ready("127.0.0.1:5561"))

    def test_a_device_that_stops_answering_is_not_ready(self) -> None:
        def reply(arguments):
            raise driver.subprocess.TimeoutExpired(cmd="adb", timeout=30)

        self._reply = reply
        self.assertFalse(driver.device_ready("127.0.0.1:5561"))


class DiskFloorTest(unittest.TestCase):
    """A tight disk must delay an app, not write it off.

    v1 covered 49 of 50 apps for this single reason. Vinyl-Music-Player was
    skipped with "only 0 GiB free" and never measured, while the app was
    perfectly healthy: it reached 19.79% the first time it was given a device
    with room. The coverage ledgers grow with samples times units, so a wave of
    concurrent runs can approach the floor and then recover as each finishing run
    is compacted -- which means waiting is usually all that was needed.
    """

    def test_the_wait_outlasts_one_full_run(self) -> None:
        self.assertGreater(
            driver.DISK_WAIT_SECONDS,
            3600,
            "space is freed when a 60-minute run finishes and is compacted, "
            "so a shorter wait cannot see that happen",
        )

    def test_the_wait_is_still_bounded(self) -> None:
        """A campaign must not hang forever on a genuinely full disk."""
        self.assertLess(driver.DISK_WAIT_SECONDS, 4 * 3600)

    def test_the_floor_itself_is_unchanged(self) -> None:
        """Only the response to the floor changed, not the floor."""
        self.assertEqual(driver.MIN_FREE_BYTES, 8 * 1024**3)


class ChildReaperTest(unittest.TestCase):
    """Stopping the driver must not leave a run owning a device.

    A `run-android` child holds its device's flock for as long as it lives, and
    flock is released only when the holder dies. Stopping a campaign with
    `pkill -f run_coverage_experiment.py` matches the driver and not its
    children, so one orphan owned 127.0.0.1:5561 for 45 minutes and every app
    handed to that lane afterwards aborted in seconds with "device is already
    owned by another VALOR-Droid run".
    """

    def setUp(self) -> None:
        import signal

        self.signal = signal
        self.saved = {
            name: signal.getsignal(name)
            for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
        }

        def restore() -> None:
            for name, handler in self.saved.items():
                signal.signal(name, handler)

        self.addCleanup(restore)

    def test_the_termination_signals_are_handled(self) -> None:
        driver.install_child_reaper()
        for name in (self.signal.SIGTERM, self.signal.SIGINT, self.signal.SIGHUP):
            with self.subTest(signal=name):
                handler = self.signal.getsignal(name)
                self.assertTrue(callable(handler))
                self.assertNotIn(
                    handler, (self.signal.SIG_DFL, self.signal.SIG_IGN)
                )

    def test_installing_twice_is_harmless(self) -> None:
        """The driver may be imported and initialised more than once."""
        driver.install_child_reaper()
        driver.install_child_reaper()
        self.assertTrue(callable(self.signal.getsignal(self.signal.SIGTERM)))


class TerminalErrorTest(unittest.TestCase):
    """The driver must see why a run gave up, not just that it did.

    In the v2 campaign device 5562 lost its package service at 07:25 and three
    apps in a row were written off as app faults: Phonograph, Vespucci and
    ownCloud, each of which had actually failed on
    `cmd: Can't find service: package`. Two things hid it. The run's JSON summary
    reports only a category, "unrecoverable_runtime_error", so the classifier had
    no text to match; and `device_healthy` asked `sys.boot_completed`, which reads
    1 throughout that condition.

    ownCloud was the run carrying the campaign's login fixture, so the weaker
    probe cost the app that most needed measuring.
    """

    def setUp(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        self.root = Path(tempfile.mkdtemp())
        self.json = json

    def _write(self, events) -> None:
        lines = []
        for event in events:
            lines.append(self.json.dumps({"payload": {"event": event}}))
        (self.root / "lifecycle.jsonl").write_text("\n".join(lines) + "\n")

    def test_the_last_recorded_error_is_recovered(self) -> None:
        self._write(
            [
                {"event": "launch", "error": None},
                {"event": "runtime_failure", "error": "cmd: Can't find service: package"},
                {"event": "run_aborted", "error": "cmd: Can't find service: package"},
            ]
        )
        self.assertIn("find service", driver.terminal_error(self.root))

    def test_a_recovered_error_makes_the_fault_infrastructure(self) -> None:
        """This is the whole point: with the text, the run earns its retry."""
        record = {
            "stop_reason": "unrecoverable_runtime_error",
            "error": "adb: failed to install app.apk: cmd: Can't find service: package",
        }
        self.assertEqual(driver.fault_kind(record), "infrastructure")
        # Without the text it was merely ambiguous, and then blamed on the app.
        self.assertEqual(
            driver.fault_kind({"stop_reason": "unrecoverable_runtime_error"}),
            "ambiguous",
        )

    def test_a_missing_ledger_is_not_an_error(self) -> None:
        self.assertEqual(driver.terminal_error(self.root), "")

    def test_a_torn_line_does_not_stop_the_read(self) -> None:
        (self.root / "lifecycle.jsonl").write_text(
            '{"payload": {"event": {"error": "first"}}}\n{"payload": {tru\n'
        )
        self.assertEqual(driver.terminal_error(self.root), "first")

    def test_health_now_means_the_package_service_answers(self) -> None:
        """device_healthy must ask the same question as device_ready."""
        calls: list[list[str]] = []
        import subprocess

        real = driver.subprocess.run

        def fake(arguments, **kwargs):
            calls.append(list(arguments))
            stdout = "1\n" if "sys.boot_completed" in arguments else "Can't find service\n"
            return type("_R", (), {"stdout": stdout, "stderr": "", "returncode": 0})()

        driver.subprocess.run = fake
        self.addCleanup(setattr, driver.subprocess, "run", real)
        self.assertFalse(driver.device_healthy("127.0.0.1:5562"))
        self.assertTrue(any("package" in " ".join(call) for call in calls))
        del subprocess


if __name__ == "__main__":
    unittest.main()
