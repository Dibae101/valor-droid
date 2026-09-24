"""The dump ladder must repair a stuck UiAutomation instead of retrying it.

A `uiautomator` process that is killed without unregistering leaves
AccessibilityManagerService holding the dead client, and every later dump on
that device fails with `IllegalStateException: ... already registered!`. Retrying
the same call can never clear that, so the ladder restarts the framework once and
then continues.

These tests drive the real `AndroidObserver.capture()` with only the device dump
stubbed, so the production ladder is what is being exercised, and they pin that
the repair stays bounded: a device that cannot be revived still ends the run, and
a merely animating window is never mistaken for a stuck registration.
"""

from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from valordroid.android.observe import AndroidObserver, ScreenNeverIdleError


PACKAGE = "com.example.app"

HIERARCHY = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<hierarchy rotation="0">'
    b'<node index="0" class="android.widget.Button" package="' + PACKAGE.encode() + b'"'
    b' resource-id="' + PACKAGE.encode() + b':id/go" text="Go" content-desc=""'
    b' bounds="[0,0][720,1280]" clickable="true" long-clickable="false"'
    b' scrollable="false" focusable="true" enabled="true" checkable="false"'
    b' checked="false" password="false" selected="false"/>'
    b"</hierarchy>"
)

STUCK = (
    "UIAutomator compressed dump failed: java.lang.IllegalStateException: "
    "UiAutomationService already registered!"
)
ANIMATING = "UIAutomator compressed dump failed: ERROR: could not get idle state."
# The shell sees only the SIGKILL. Whether that was a leaked registration or a
# one-off kill under load cannot be told from this text alone.
KILLED = "UIAutomator compressed dump failed: Killed"


class _Config:
    observation_timeout_seconds = 5.0
    adb_timeout_seconds = 5.0
    capture_screenshots = False
    text_input_value = "valordroid"
    per_field_input_enabled = False


class _AdbResult:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _Adb:
    """Stands in for the device, recording every call the ladder makes.

    The recording exists so a test can assert the ladder never opens a second
    logcat reader, which is what once corrupted the coverage measurement.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def shell(self, *arguments: str, timeout=None, check: bool = True) -> _AdbResult:
        self.calls.append(arguments)
        return _AdbResult("")


class _Session:
    """Only what `capture()` touches, plus a recorded repair."""

    package = PACKAGE

    def __init__(self) -> None:
        self.repairs = 0
        self.adb = _Adb()

    def repair_ui_automation(self) -> str:
        self.repairs += 1
        return "root, framework_restarted, boot_completed"

    @staticmethod
    def foreground() -> tuple[tuple[str, ...], str]:
        return ((f"{PACKAGE}/.MainActivity",), f"{PACKAGE}/.MainActivity")


class _Observer(AndroidObserver):
    def __init__(self, artifact_root: Path, outcomes) -> None:
        super().__init__(_Session(), _Config(), artifact_root)
        self._outcomes = list(outcomes)
        self.dump_calls = 0
        # The real ladder sleeps between attempts; the schedule is not under test.
        self.DUMP_BACKOFF_SECONDS = (0.0,)

    def _dump_once(self, *, compressed: bool, timeout=None):
        self.dump_calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else STUCK
        if outcome == "ok":
            return HIERARCHY, ET.fromstring(HIERARCHY)
        return outcome


class UiAutomationRepairTest(unittest.TestCase):
    def _observer(self, outcomes) -> _Observer:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        return _Observer(root, outcomes)

    def test_a_stuck_registration_is_repaired_and_capture_then_succeeds(self):
        observer = self._observer([STUCK, STUCK, STUCK, STUCK, "ok"])
        captured = observer.capture()
        self.assertEqual(captured.hierarchy, HIERARCHY)
        self.assertEqual(observer.session.repairs, 1)
        # The observation is real evidence, not a placeholder.
        self.assertTrue(captured.observation.state_id)
        self.assertEqual(len(captured.candidates), 1)

    def test_a_confirmed_leak_is_repaired_on_the_first_failure(self):
        """Waiting four attempts wastes the whole budget on certain failures.

        Measured on Trackbook: every dump hung for the full observation timeout,
        so the ladder's wall-clock budget was gone before the 4-attempt gate let
        the repair run, and a device one framework restart would have fixed
        stayed dead for the rest of the run. When the reason names the leak,
        retrying is known to be pointless, so the restart happens at once.
        """
        observer = self._observer([STUCK, "ok"])
        captured = observer.capture()
        self.assertEqual(observer.session.repairs, 1)
        self.assertEqual(observer.dump_calls, 2)
        self.assertEqual(captured.hierarchy, HIERARCHY)

    def test_a_kill_the_shell_cannot_explain_waits_for_the_retries(self):
        """A SIGKILL alone may be a fluke, and a framework restart is expensive.

        The exception behind a wedged registration is usually only in logcat,
        because `uiautomator dump` crashes in its own process. Reading it back
        from there was tried and withdrawn: the coverage collector holds a
        streaming `adb logcat`, and a second reader on the same transport stalls
        it long enough to lose METHOD= lines from the ring buffer. Chess measured
        42.72% without the probe and 23.70% with it, back to back, on identical
        exploration. An occasionally slower recovery is much preferable to an
        unreliable measurement.
        """
        observer = self._observer([KILLED, "ok"])
        observer.capture()
        self.assertEqual(observer.session.repairs, 0)
        self.assertEqual(observer.dump_calls, 2)

    def test_the_dump_ladder_never_opens_a_second_logcat_reader(self):
        """Guards the measurement itself, not just this one regression."""
        observer = self._observer([STUCK] * 40)
        with self.assertRaises(ScreenNeverIdleError):
            observer.capture()
        logcat_calls = [
            call
            for call in observer.session.adb.calls
            if any("logcat" in str(part) for part in call)
        ]
        self.assertEqual(logcat_calls, [])

    def test_repair_happens_once_and_a_dead_device_still_ends_the_run(self):
        observer = self._observer([STUCK] * 40)
        with self.assertRaises(ScreenNeverIdleError):
            observer.capture()
        self.assertEqual(observer.session.repairs, 1)
        self.assertEqual(observer.dump_calls, observer.DUMP_ATTEMPTS)

    def test_an_animating_window_is_not_treated_as_a_stuck_registration(self):
        observer = self._observer([ANIMATING] * 40)
        with self.assertRaises(ScreenNeverIdleError):
            observer.capture()
        self.assertEqual(observer.session.repairs, 0)


if __name__ == "__main__":
    unittest.main()


class ObservationBudgetTest(unittest.TestCase):
    """A never-idle screen must not consume most of a short run."""

    def setUp(self):
        # `time.monotonic` and `time.sleep` are replaced so the budget arithmetic
        # is tested without spending the wall clock it is measuring.
        import valordroid.android.observe as module

        self._module = module
        self._real_monotonic = module.time.monotonic
        self._real_sleep = module.time.sleep
        self._clock = {"now": 0.0}

        def monotonic():
            return self._clock["now"]

        def sleep(seconds):
            self._clock["now"] += seconds

        module.time.monotonic = monotonic
        module.time.sleep = sleep
        self.addCleanup(self._restore)

    def _restore(self):
        self._module.time.monotonic = self._real_monotonic
        self._module.time.sleep = self._real_sleep

    def _observer(self, seconds_per_dump: float, max_seconds: float):
        root = Path(tempfile.mkdtemp())
        observer = _Observer(root, [ANIMATING] * 100)
        observer.config.max_seconds = max_seconds
        clock = self._clock
        cost = seconds_per_dump

        def dump(*, compressed: bool, timeout=None):
            observer.dump_calls += 1
            clock["now"] += min(cost, timeout if timeout is not None else cost)
            return ANIMATING

        observer._dump_once = dump
        return observer

    def test_a_never_idle_screen_cannot_outlast_its_share_of_a_short_run(self):
        # 600s run: the ladder may spend 30s, not the 395s measured in the field.
        observer = self._observer(seconds_per_dump=60.0, max_seconds=600.0)
        with self.assertRaises(ScreenNeverIdleError):
            observer.capture()
        self.assertLessEqual(self._clock["now"], 31.0)

    def test_a_long_run_is_allowed_to_be_more_patient(self):
        observer = self._observer(seconds_per_dump=60.0, max_seconds=3600.0)
        with self.assertRaises(ScreenNeverIdleError):
            observer.capture()
        # Capped at the ceiling rather than scaling to 5% of an hour.
        self.assertLessEqual(self._clock["now"], 91.0)
        self.assertGreater(self._clock["now"], 31.0)

    def test_the_attempt_ladder_still_bounds_a_fast_failing_dump(self):
        # Cheap failures exhaust the attempt count long before the budget.
        observer = self._observer(seconds_per_dump=0.0, max_seconds=600.0)
        with self.assertRaises(ScreenNeverIdleError):
            observer.capture()
        self.assertEqual(observer.dump_calls, observer.DUMP_ATTEMPTS)

    def test_the_error_names_the_budget_when_it_is_what_stopped_the_ladder(self):
        observer = self._observer(seconds_per_dump=60.0, max_seconds=600.0)
        with self.assertRaises(ScreenNeverIdleError) as caught:
            observer.capture()
        self.assertIn("observation budget", str(caught.exception))


class SharedWindowTest(ObservationBudgetTest):
    """A sequence of captures must cost one window, not the sum of their budgets."""

    def test_three_chained_captures_share_one_budget(self):
        observer = self._observer(seconds_per_dump=60.0, max_seconds=600.0)
        # Without a shared window each capture would spend its own 30s.
        with observer.bounded_window(40.0):
            for _ in range(3):
                with self.assertRaises(ScreenNeverIdleError):
                    observer.capture()
        self.assertLessEqual(self._clock["now"], 41.0)

    def test_nesting_narrows_the_window_and_never_widens_it(self):
        observer = self._observer(seconds_per_dump=60.0, max_seconds=600.0)
        with observer.bounded_window(25.0):
            with observer.bounded_window(600.0):
                with self.assertRaises(ScreenNeverIdleError):
                    observer.capture()
        self.assertLessEqual(self._clock["now"], 26.0)

    def test_the_window_is_released_afterwards(self):
        observer = self._observer(seconds_per_dump=0.0, max_seconds=600.0)
        with observer.bounded_window(10.0):
            pass
        self.assertIsNone(observer._shared_deadline)
