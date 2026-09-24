"""A leading observer that fails must hand back, not end the run.

Making the persistent UIAutomator2 service lead observation is what raised the
action rate from ~23/min to 37-61/min. It also made every service failure fatal,
because once the service owns the device the shell dumper is unreachable: ownership
is monotone, so a second dumper is never started while the service holds it.

That cost whole measurements. WordPress aborted on `UIAutomator2 fallback
observation budget is exhausted` and AnkiDroid on `reconnect failed:
LaunchUiAutomationError`, each discarding every unit it had already covered, while
the shell ladder sat available and unused. In the reconnect case the adapter had
*already* retired the previous generation before attempting the reconnect, so
nothing owned the device at all.

So the adapter gained a way to say "finished, and no longer holding anything", and
the observer falls through to the shell ladder after it. These tests pin that the
fallback happens, that ownership is released before the shell dumper starts, that an
unconfirmed release refuses the fallback, and that the plain fallback role is
unchanged.
"""

from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from valordroid.android.observe import AndroidObserver, ScreenNeverIdleError
from valordroid.android.uiautomator2_fallback import (
    UiAutomator2Capture,
    UiAutomator2FallbackError,
)


PACKAGE = "com.example.app"
ACTIVITY = f"{PACKAGE}/.MainActivity"

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


class _Config:
    observation_timeout_seconds = 5.0
    adb_timeout_seconds = 5.0
    capture_screenshots = False
    text_input_value = "valordroid"
    per_field_input_enabled = False


class _AdbResult:
    stdout = ""
    stderr = ""
    returncode = 0


class _Adb:
    @staticmethod
    def shell(*arguments: str, timeout=None, check: bool = True) -> _AdbResult:
        return _AdbResult()


class _Session:
    package = PACKAGE
    adb = _Adb()

    @staticmethod
    def foreground() -> tuple[tuple[str, ...], str]:
        return ((ACTIVITY,), ACTIVITY)

    @staticmethod
    def repair_ui_automation() -> str:
        return "root, framework_restarted, boot_completed"


class _Adapter:
    """A service that activates, then fails every later capture."""

    MINIMUM_REQUEST_SECONDS = 0.1

    def __init__(self, *, fail_after: int = 1, release_unknown: bool = False) -> None:
        self.activated = False
        self.stood_down = False
        self.release_unknown = False
        self._release_unknown_on_stand_down = release_unknown
        self.captures = 0
        self.stand_downs = 0
        self.fail_after = fail_after

    @property
    def serving(self) -> bool:
        return self.activated and not self.stood_down

    def capture(self, *, timeout_seconds: float) -> UiAutomator2Capture:
        self.activated = True
        self.captures += 1
        if self.captures > self.fail_after:
            raise UiAutomator2FallbackError(
                "UIAutomator2 hierarchy reconnect failed: LaunchUiAutomationError"
            )
        return UiAutomator2Capture(
            request_id=self.captures,
            hierarchy=HIERARCHY,
            root=ET.fromstring(HIERARCHY),
            session_generation=1,
            server_port=9008,
            request_started_at=1.0,
            response_received_at=1.5,
            foreground_package=PACKAGE,
            foreground_activity=ACTIVITY,
            idle_timeout_ms=0,
        )

    def commit_observation(self, capture, **_kwargs) -> None:
        del capture

    def stand_down(self) -> tuple[str, ...]:
        self.stand_downs += 1
        self.stood_down = True
        if self._release_unknown_on_stand_down:
            self.release_unknown = True
        return ()

    @staticmethod
    def close() -> tuple[str, ...]:
        return ()


class _Observer(AndroidObserver):
    def __init__(
        self,
        artifact_root: Path,
        adapter: _Adapter,
        *,
        primary: bool,
        shell_works: bool = True,
    ) -> None:
        super().__init__(_Session(), _Config(), artifact_root)
        self._uiautomator2 = adapter
        self._uiautomator2_primary = primary
        self.DUMP_BACKOFF_SECONDS = (0.0,)
        self.shell_dumps = 0
        self._shell_works = shell_works

    def _dump_once(self, *, compressed: bool, timeout=None):
        self.shell_dumps += 1
        if not self._shell_works:
            return "UIAutomator compressed dump failed: ERROR: could not get idle state."
        return HIERARCHY, ET.fromstring(HIERARCHY)


class PrimaryObserverFallbackTest(unittest.TestCase):
    def _observer(self, **kwargs) -> _Observer:
        root = Path(tempfile.mkdtemp()) / "observations"
        adapter = _Adapter(
            fail_after=kwargs.pop("fail_after", 1),
            release_unknown=kwargs.pop("release_unknown", False),
        )
        return _Observer(root, adapter, **kwargs)

    def test_a_leading_service_that_fails_hands_over_to_the_shell(self) -> None:
        observer = self._observer(primary=True)
        first = observer.capture()
        self.assertEqual(first.hierarchy, HIERARCHY)
        self.assertEqual(observer.shell_dumps, 0)

        second = observer.capture()
        self.assertEqual(second.hierarchy, HIERARCHY)
        self.assertEqual(observer._uiautomator2.stand_downs, 1)
        self.assertGreaterEqual(observer.shell_dumps, 1)

    def test_the_device_is_released_before_the_shell_dumper_starts(self) -> None:
        """Two dumpers on one device break every later dump in the run."""

        observer = self._observer(primary=True)
        observer.capture()
        adapter = observer._uiautomator2
        order: list[str] = []
        original_stand_down = adapter.stand_down

        def recording_stand_down():
            order.append("stand_down")
            return original_stand_down()

        adapter.stand_down = recording_stand_down  # type: ignore[method-assign]
        outer = observer._dump_once

        def recording_dump(*args, **kwargs):
            order.append("shell_dump")
            return outer(*args, **kwargs)

        observer._dump_once = recording_dump  # type: ignore[method-assign]
        observer.capture()
        self.assertEqual(order[:2], ["stand_down", "shell_dump"])

    def test_the_service_is_not_asked_again_after_standing_down(self) -> None:
        observer = self._observer(primary=True)
        observer.capture()
        observer.capture()
        captures_after_handover = observer._uiautomator2.captures
        observer.capture()
        self.assertEqual(observer._uiautomator2.captures, captures_after_handover)
        self.assertEqual(observer._uiautomator2.stand_downs, 1)

    def test_an_unconfirmed_release_refuses_the_fallback(self) -> None:
        """A release that cannot be confirmed may still hold the device."""

        observer = self._observer(primary=True, release_unknown=True)
        observer.capture()
        with self.assertRaises(ScreenNeverIdleError) as caught:
            observer.capture()
        self.assertIn("without confirmation", str(caught.exception))
        self.assertEqual(observer.shell_dumps, 0)

    def test_the_ordinary_fallback_role_is_unchanged(self) -> None:
        """When the shell dumper leads, a service failure keeps its old meaning.

        By then the shell ladder has already been tried and failed, so there is
        nothing to fall back to and the frame is genuinely lost.
        """

        observer = self._observer(primary=False)
        observer._uiautomator2.activated = True
        observer._uiautomator2.fail_after = 0
        with self.assertRaises(UiAutomator2FallbackError):
            observer.capture()
        self.assertEqual(observer._uiautomator2.stand_downs, 0)

    def test_a_dead_device_after_handover_still_ends_the_frame(self) -> None:
        """Handing over is not a way to hide a device that cannot be dumped."""

        observer = self._observer(primary=True, shell_works=False)
        observer.capture()
        with self.assertRaises(ScreenNeverIdleError):
            observer.capture()
        self.assertEqual(observer._uiautomator2.stand_downs, 1)


if __name__ == "__main__":
    unittest.main()
