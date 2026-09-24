"""Hard-deadline regressions for retained Android setup-profile steps."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from valordroid.android.observe import UiNode
from valordroid.android.setup import AndroidSetupExecutor, SetupExecutionError
from valordroid.setup_profile import SetupStep

PACKAGE = "com.example.app"
SELECTOR = {"package": PACKAGE, "resource_id": f"{PACKAGE}:id/optional"}


class _Clock:
    def __init__(self, *, sleep_overshoot: float = 0.0) -> None:
        self.now = 100.0
        self.sleep_overshoot = sleep_overshoot

    def monotonic(self) -> float:
        return self.now

    def wall_time(self) -> float:
        return 1_700_000_000.0 + self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds + self.sleep_overshoot


class _Observer:
    def __init__(
        self,
        clock: _Clock,
        *,
        nodes: tuple[UiNode, ...] = (),
        capture_advance: float = 0.0,
    ) -> None:
        self.clock = clock
        self.nodes = nodes
        self.capture_advance = capture_advance
        self.deadlines: list[float] = []

    def capture(self, *, deadline_monotonic: float):
        self.deadlines.append(deadline_monotonic)
        self.clock.now += self.capture_advance
        return SimpleNamespace(nodes=self.nodes)


class _Adb:
    def __init__(self, clock: _Clock, *, shell_advance: float = 0.0) -> None:
        self.clock = clock
        self.shell_advance = shell_advance
        self.timeouts: list[float] = []

    def shell(self, *_args: str, timeout: float, **_kwargs: object) -> None:
        self.timeouts.append(timeout)
        self.clock.now += self.shell_advance


def _node() -> UiNode:
    return UiNode(
        selector={**SELECTOR, "class_name": "android.widget.Button"},
        bounds=(0, 0, 100, 40),
        attributes={},
    )


def _step(*, required: bool) -> SetupStep:
    return SetupStep.from_mapping(
        {
            "id": "optional-control",
            "kind": "tap",
            "required": required,
            "timeout_seconds": 0.5,
            "parameters": {"selector": SELECTOR},
        },
        ordinal=1,
    )


def _executor(
    clock: _Clock,
    observer: _Observer,
    *,
    shell_advance: float = 0.0,
) -> AndroidSetupExecutor:
    session = SimpleNamespace(
        package=PACKAGE,
        config=SimpleNamespace(
            adb_timeout_seconds=5.0,
            post_action_delay_seconds=0.0,
        ),
        adb=_Adb(clock, shell_advance=shell_advance),
    )
    resolved = SimpleNamespace(
        profile=SimpleNamespace(package=PACKAGE, timeout_seconds=5.0),
        redact=lambda error: str(error),
    )
    return AndroidSetupExecutor(session, observer, resolved)


class AndroidSetupDeadlineTest(unittest.TestCase):
    def _clock_patches(self, clock: _Clock):
        return (
            patch("valordroid.android.setup.time.monotonic", side_effect=clock.monotonic),
            patch("valordroid.android.setup.time.time", side_effect=clock.wall_time),
            patch("valordroid.android.setup.time.sleep", side_effect=clock.sleep),
        )

    def test_absent_optional_selector_is_skipped_at_its_deadline(self) -> None:
        clock = _Clock(sleep_overshoot=0.000001)
        observer = _Observer(clock)
        monotonic, wall_time, sleep = self._clock_patches(clock)
        with monotonic, wall_time, sleep:
            result = _executor(clock, observer).execute_step(_step(required=False))

        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(observer.deadlines, [100.5, 100.5])
        self.assertGreater(clock.now, 100.5)

    def test_absent_required_selector_fails_at_its_deadline(self) -> None:
        clock = _Clock(sleep_overshoot=0.000001)
        observer = _Observer(clock)
        monotonic, wall_time, sleep = self._clock_patches(clock)
        with monotonic, wall_time, sleep:
            with self.assertRaisesRegex(
                SetupExecutionError, "setup selector was not found before timeout"
            ):
                _executor(clock, observer).execute_step(_step(required=True))

        self.assertEqual(observer.deadlines, [100.5, 100.5])

    def test_optional_step_that_executes_past_deadline_fails_closed(self) -> None:
        clock = _Clock()
        observer = _Observer(clock, nodes=(_node(),))
        monotonic, wall_time, sleep = self._clock_patches(clock)
        with monotonic, wall_time, sleep:
            with self.assertRaisesRegex(
                SetupExecutionError, "setup step exceeded its timeout"
            ):
                _executor(
                    clock, observer, shell_advance=0.6
                ).execute_step(_step(required=False))

        self.assertEqual(observer.deadlines, [100.5])

    def test_capture_that_returns_after_deadline_fails_even_when_optional(self) -> None:
        clock = _Clock()
        observer = _Observer(clock, capture_advance=0.6)
        monotonic, wall_time, sleep = self._clock_patches(clock)
        with monotonic, wall_time, sleep:
            with self.assertRaisesRegex(
                SetupExecutionError, "setup observation exceeded its timeout"
            ):
                _executor(clock, observer).execute_step(_step(required=False))

        self.assertEqual(observer.deadlines, [100.5])


if __name__ == "__main__":
    unittest.main()
