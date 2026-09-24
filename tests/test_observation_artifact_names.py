"""An artifact directory name belongs to a capture attempt, not to a success.

`capture()` writes the window dump to `observations/<sequence>-<state-id-prefix>`
*before* the observation is committed, because a dump that is not retained cannot
be audited later. The number in that name used to come from the last *committed*
observation, which was fine only while every refused frame ended the run.

Once a frame refused at commit became recoverable, the retry recomputed the same
number, and a retry usually sees the same screen -- so the same state-ID prefix,
so the same directory. `open("xb")` then raised `FileExistsError` and killed the
run, which is exactly the outcome making the refusal recoverable was meant to
avoid. Observed as
`[Errno 17] File exists: .../observations/00000314-18b9c5c7e4a7/hierarchy.xml`.

These tests pin both halves of the fix: names are unique per attempt, and the
sequence that evidence is bound to still identifies `latest` rather than the
leftovers of a frame that was thrown away.
"""

from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from valordroid.android.observe import AndroidObserver, ScreenNeverIdleError
from valordroid.android.uiautomator2_fallback import (
    UiAutomator2Capture,
    UiAutomator2ForegroundChangedError,
)


PACKAGE = "com.example.app"


def _hierarchy(text: str) -> bytes:
    return (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<hierarchy rotation="0">'
        b'<node index="0" class="android.widget.Button"'
        b' package="' + PACKAGE.encode() + b'"'
        b' resource-id="' + PACKAGE.encode() + b':id/go"'
        b' text="' + text.encode() + b'" content-desc=""'
        b' bounds="[0,0][720,1280]" clickable="true" long-clickable="false"'
        b' scrollable="false" focusable="true" enabled="true" checkable="false"'
        b' checked="false" password="false" selected="false"/>'
        b"</hierarchy>"
    )


ACTIVITY = f"{PACKAGE}/.MainActivity"


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
    def repair_ui_automation() -> str:  # pragma: no cover - never reached here
        return "root, framework_restarted, boot_completed"


class _Adapter:
    """A persistent-service stand-in whose commits can be told to refuse.

    Only the surface `capture()` touches is implemented. `refuse_commits` counts
    down, so a test can make the first N commits refuse the frame the way a
    moving screen does and let the next one through.
    """

    MINIMUM_REQUEST_SECONDS = 0.1

    def __init__(self, refuse_commits: int = 0) -> None:
        self.activated = False
        self.refuse_commits = refuse_commits
        self.captures = 0
        self.committed: list[int] = []
        self.stood_down = False
        self.release_unknown = False

    @property
    def serving(self) -> bool:
        return self.activated and not self.stood_down

    def stand_down(self) -> tuple[str, ...]:
        self.stood_down = True
        return ()

    def capture(self, *, timeout_seconds: float) -> UiAutomator2Capture:
        self.activated = True
        self.captures += 1
        hierarchy = _hierarchy("Go")
        return UiAutomator2Capture(
            request_id=self.captures,
            hierarchy=hierarchy,
            root=ET.fromstring(hierarchy),
            session_generation=1,
            server_port=9008,
            request_started_at=1.0,
            response_received_at=1.5,
            foreground_package=PACKAGE,
            foreground_activity=ACTIVITY,
            idle_timeout_ms=0,
        )

    def commit_observation(
        self,
        capture: UiAutomator2Capture,
        *,
        observation_sequence: int,
        state_id: str,
        hierarchy_sha256: str,
        resumed_activities,
    ) -> None:
        if self.refuse_commits > 0:
            self.refuse_commits -= 1
            raise UiAutomator2ForegroundChangedError(
                "foreground changed between capture and commit"
            )
        self.committed.append(observation_sequence)

    @staticmethod
    def close() -> tuple[str, ...]:
        return ()


class _Observer(AndroidObserver):
    def __init__(self, artifact_root: Path, adapter: _Adapter) -> None:
        super().__init__(_Session(), _Config(), artifact_root)
        self._uiautomator2 = adapter
        self._uiautomator2_primary = True

    def _dump_once(self, *, compressed: bool, timeout=None):  # pragma: no cover
        raise AssertionError("the persistent service is primary in these tests")


class ArtifactNameTest(unittest.TestCase):
    def _observer(self, refuse_commits: int = 0) -> _Observer:
        root = Path(tempfile.mkdtemp()) / "observations"
        return _Observer(root, _Adapter(refuse_commits=refuse_commits))

    def _directories(self, observer: _Observer) -> list[str]:
        return sorted(
            entry.name
            for entry in observer.artifact_root.iterdir()
            if entry.is_dir()
        )

    def test_a_refused_frame_does_not_poison_the_retry(self) -> None:
        """The regression: retrying a refused capture reused its directory."""

        observer = self._observer(refuse_commits=1)
        with self.assertRaises(ScreenNeverIdleError):
            observer.capture()
        captured = observer.capture()
        self.assertEqual(captured.hierarchy, _hierarchy("Go"))
        self.assertEqual(len(self._directories(observer)), 2)

    def test_a_refused_frame_keeps_its_own_name(self) -> None:
        """Two attempts, one screen: distinct numbers, identical prefix."""

        observer = self._observer(refuse_commits=1)
        with self.assertRaises(ScreenNeverIdleError):
            observer.capture()
        observer.capture()
        names = self._directories(observer)
        numbers = [name.partition("-")[0] for name in names]
        prefixes = {name.partition("-")[2] for name in names}
        self.assertEqual(numbers, ["00000001", "00000002"])
        self.assertEqual(len(prefixes), 1)

    def test_the_bound_sequence_still_identifies_the_committed_observation(
        self,
    ) -> None:
        """`sequence` is what retained evidence is filed under.

        If it advanced on a refused frame, evidence recorded against `latest`
        would name a directory holding a frame that was thrown away.
        """

        observer = self._observer(refuse_commits=0)
        observer.capture()
        self.assertEqual(observer.sequence, 1)
        observer._uiautomator2.refuse_commits = 1
        with self.assertRaises(ScreenNeverIdleError):
            observer.capture()
        self.assertEqual(observer.sequence, 1)
        self.assertIsNotNone(observer.latest)

    def test_committed_sequences_increase_but_need_not_be_adjacent(self) -> None:
        """Every consumer requires increase; a refusal is allowed to leave a gap."""

        observer = self._observer(refuse_commits=0)
        observer.capture()
        observer._uiautomator2.refuse_commits = 1
        with self.assertRaises(ScreenNeverIdleError):
            observer.capture()
        observer.capture()
        self.assertEqual(observer._uiautomator2.committed, [1, 3])
        self.assertEqual(observer.sequence, 3)

    def test_many_consecutive_refusals_never_collide(self) -> None:
        observer = self._observer(refuse_commits=5)
        for _ in range(5):
            with self.assertRaises(ScreenNeverIdleError):
                observer.capture()
        observer.capture()
        self.assertEqual(len(self._directories(observer)), 6)
        self.assertEqual(observer.sequence, 6)


if __name__ == "__main__":
    unittest.main()
