"""Hard-deadline regressions for scoped foreign workflow transactions."""

from __future__ import annotations

import threading
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from valordroid.android.logcat import LogcatCollector
from valordroid.android.observe import AndroidObserver, ScreenNeverIdleError
from valordroid.android.outcomes import AndroidOutcomeVerifier
from valordroid.models import (
    ExecutionRecord,
    ExecutionStatus,
    OutcomeKind,
    StateObservation,
)

AUT = "com.example.app"
AUT_COMPONENT = f"{AUT}/.MainActivity"


def _observation(state_id: str, observed_at: float) -> StateObservation:
    return StateObservation(
        state_id=state_id,
        observed_at=observed_at,
        activity=AUT_COMPONENT,
        resumed_activities=(AUT_COMPONENT,),
        hierarchy_sha256="a" * 64,
    )


def _observer_config(*, screenshots: bool) -> SimpleNamespace:
    return SimpleNamespace(
        serial="emulator-5554",
        observation_timeout_seconds=5.0,
        max_seconds=60.0,
        capture_screenshots=screenshots,
        text_input_value=None,
        per_field_input_enabled=True,
        semantic_state_enabled=False,
        uiautomator2_fallback_enabled=False,
        webview_enabled=False,
        webview_url_patterns=(),
    )


class _Adb:
    def __init__(self) -> None:
        self.screenshot_timeouts: list[float] = []

    def exec_out(self, *_args, timeout: float):
        self.screenshot_timeouts.append(timeout)
        return b"png"


class _Session:
    package = AUT

    def __init__(self, *, foreground_delay: float = 0.0) -> None:
        self.adb = _Adb()
        self.foreground_delay = foreground_delay
        self.foreground_deadlines: list[float] = []

    def foreground(self, *, deadline_monotonic: float):
        self.foreground_deadlines.append(deadline_monotonic)
        if self.foreground_delay:
            time.sleep(self.foreground_delay)
        return (AUT_COMPONENT,), AUT_COMPONENT


class ObserverDeadlineTest(unittest.TestCase):
    XML = (
        f'<hierarchy><node package="{AUT}" class="android.widget.Button" '
        'resource-id="com.example.app:id/go" text="Go" '
        'clickable="true" enabled="true" bounds="[0,0][10,10]" />'
        "</hierarchy>"
    ).encode("utf-8")

    def _observer(
        self, root: Path, *, screenshots: bool, foreground_delay: float = 0.0
    ) -> tuple[AndroidObserver, _Session]:
        session = _Session(foreground_delay=foreground_delay)
        observer = AndroidObserver(
            session,
            _observer_config(screenshots=screenshots),
            root,
        )
        observer.DUMP_MINIMUM_ATTEMPT_SECONDS = 0.001
        observer._dump_once = lambda **_kwargs: (
            self.XML,
            ET.fromstring(self.XML),
        )
        return observer, session

    def test_deadline_reaches_screenshot_and_foreground(self) -> None:
        with TemporaryDirectory() as temporary:
            observer, session = self._observer(
                Path(temporary), screenshots=True
            )
            deadline = time.monotonic() + 0.5
            captured = observer.capture(deadline_monotonic=deadline)

        self.assertEqual(captured.observation.activity, AUT_COMPONENT)
        self.assertEqual(session.foreground_deadlines, [deadline])
        self.assertEqual(len(session.adb.screenshot_timeouts), 1)
        self.assertGreater(session.adb.screenshot_timeouts[0], 0.0)
        self.assertLessEqual(session.adb.screenshot_timeouts[0], 0.5)

    def test_foreground_that_finishes_after_deadline_cannot_publish(self) -> None:
        with TemporaryDirectory() as temporary:
            observer, session = self._observer(
                Path(temporary),
                screenshots=False,
                foreground_delay=0.03,
            )
            deadline = time.monotonic() + 0.02
            with self.assertRaisesRegex(
                ScreenNeverIdleError, "observation deadline exhausted"
            ):
                observer.capture(deadline_monotonic=deadline)
            self.assertIsNone(observer.latest)
            self.assertEqual(session.foreground_deadlines, [deadline])


class OutcomeDeadlineTest(unittest.TestCase):
    def test_verifier_forwards_exact_deadline_and_clears_stale_after(self) -> None:
        before = _observation("before", 1.0)
        after = _observation("after", 2.0)

        class _Observer:
            def __init__(self) -> None:
                self.deadlines: list[float] = []
                self.fail = False

            def capture(self, *, deadline_monotonic: float):
                self.deadlines.append(deadline_monotonic)
                if self.fail:
                    raise RuntimeError("capture failed")
                return SimpleNamespace(observation=after, nodes=())

        observer = _Observer()
        verifier = AndroidOutcomeVerifier(
            observer,
            SimpleNamespace(post_action_delay_seconds=0.0),
        )
        execution = ExecutionRecord(
            status=ExecutionStatus.NOT_EXECUTED,
            started_at=1.5,
            ended_at=1.5,
            action=None,
            error="stale target",
        )
        deadline = time.monotonic() + 1.0
        outcome = verifier.verify(
            before, execution, deadline_monotonic=deadline
        )
        self.assertEqual(outcome.kind, OutcomeKind.NOT_EXECUTED)
        self.assertEqual(observer.deadlines, [deadline])

        observer.fail = True
        with self.assertRaisesRegex(RuntimeError, "capture failed"):
            verifier.verify(before, execution, deadline_monotonic=deadline)
        self.assertIsNone(verifier.after)


class CoverageDeadlineTest(unittest.TestCase):
    def test_ownership_refresh_uses_one_shrinking_deadline(self) -> None:
        timeouts: list[float] = []

        class _CoverageAdb:
            def shell(self, *args, timeout: float, **_kwargs):
                timeouts.append(timeout)
                if args[0] == "ps":
                    output = f"123 {AUT}\n"
                elif args[-1] == "/proc/sys/kernel/random/boot_id":
                    output = "boot-id"
                else:
                    output = "123 (app) S " + " ".join(["0"] * 20)
                return SimpleNamespace(returncode=0, stdout=output, stderr="")

        collector = LogcatCollector.__new__(LogcatCollector)
        collector.session = SimpleNamespace(
            package=AUT, adb=_CoverageAdb()
        )
        collector.config = SimpleNamespace(adb_timeout_seconds=5.0)
        deadline = time.monotonic() + 0.5
        snapshot = collector._owned_snapshot(deadline_monotonic=deadline)

        self.assertEqual(tuple(snapshot), (123,))
        self.assertEqual(len(timeouts), 3)
        self.assertTrue(all(0.0 < value <= 0.5 for value in timeouts))
        self.assertTrue(
            all(later <= earlier for earlier, later in zip(timeouts, timeouts[1:]))
        )

    def test_poll_lock_wait_is_bounded_by_deadline(self) -> None:
        collector = LogcatCollector.__new__(LogcatCollector)
        collector._poll_call_lock = threading.Lock()
        collector._poll_call_lock.acquire()
        try:
            started = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, "poll lock deadline"):
                collector._poll_once(deadline_monotonic=started + 0.01)
            self.assertLess(time.monotonic() - started, 0.1)
        finally:
            collector._poll_call_lock.release()


if __name__ == "__main__":
    unittest.main()


class NativeHierarchyDeadlineRegressionTest(unittest.TestCase):
    def test_unique_device_paths_prevent_stale_xml_reuse(self) -> None:
        xml = ObserverDeadlineTest.XML

        class Result:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        class Adb:
            def __init__(self):
                self.files = {}
                self.dump_paths = []

            def shell(self, *args, **_kwargs):
                if args[:2] == ("uiautomator", "dump"):
                    path = args[-1]
                    self.dump_paths.append(path)
                    if len(self.dump_paths) == 1:
                        self.files[path] = xml
                    return Result()
                if args[:2] == ("test", "-s"):
                    return Result(0 if args[-1] in self.files else 1)
                if args[:2] == ("rm", "-f"):
                    # Simulate cleanup failure: uniqueness must still prevent reuse.
                    return Result()
                raise AssertionError(args)

            def exec_out(self, command, path, **_kwargs):
                self.asserted_command = command
                return self.files[path]

        with TemporaryDirectory() as temporary:
            adb = Adb()
            observer = AndroidObserver(
                SimpleNamespace(package=AUT, adb=adb),
                _observer_config(screenshots=False),
                Path(temporary),
            )
            first = observer._dump_once(compressed=True, timeout=0.5)
            second = observer._dump_once(compressed=True, timeout=0.5)

        self.assertIsInstance(first, tuple)
        self.assertIsInstance(second, str)
        self.assertIn("produced no hierarchy file", second)
        self.assertEqual(len(set(adb.dump_paths)), 2)

    def test_ordinary_native_commands_keep_per_call_timeouts(self) -> None:
        xml = ObserverDeadlineTest.XML

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        class Adb:
            def __init__(self):
                self.timeouts = []

            def shell(self, *args, timeout, **_kwargs):
                self.timeouts.append((args[0], timeout))
                return Result()

            def exec_out(self, *_args, timeout):
                self.timeouts.append(("cat", timeout))
                return xml

        with TemporaryDirectory() as temporary:
            adb = Adb()
            observer = AndroidObserver(
                SimpleNamespace(package=AUT, adb=adb),
                _observer_config(screenshots=False),
                Path(temporary),
            )
            result = observer._dump_once(compressed=True, timeout=0.5)

        self.assertIsInstance(result, tuple)
        device_timeouts = [value for operation, value in adb.timeouts if operation != "rm"]
        self.assertEqual(device_timeouts, [0.5, 0.5, 0.5])


class DispatchedActionDurabilityTest(unittest.TestCase):
    def test_deadline_crossing_after_collection_does_not_erase_attempt(self) -> None:
        from valordroid.models import ActionSpec, OutcomeRecord
        from valordroid.runner import AndroidRunner

        before = _observation("before", 1.0)
        after = _observation("after", 2.0)
        action = ActionSpec(
            "tap",
            target_id="target",
            parameters={"expected_state_id": "before", "selector": {"package": AUT}},
        )
        execution = ExecutionRecord(
            status=ExecutionStatus.EXECUTED,
            started_at=1.5,
            ended_at=1.6,
            action=action,
        )

        class Verifier:
            def __init__(self):
                self.after = SimpleNamespace(observation=after)

            def verify(self, *_args, **_kwargs):
                return OutcomeRecord(
                    OutcomeKind.EFFECT,
                    state_changed=True,
                    details={"before_state_id": "before", "after_state_id": "after"},
                )

        class Core:
            def __init__(self):
                self.committed = False

            def record_action(self, **_kwargs):
                self.committed = True
                return SimpleNamespace(attempt_id="a-1"), SimpleNamespace(
                    associated_unit_ids=()
                )

        runner = AndroidRunner.__new__(AndroidRunner)
        runner.verifier = Verifier()
        runner.executor = SimpleNamespace(last_target_text=None, last_field_cleared=None)
        runner.core = Core()
        runner._asks_for_credentials = lambda: False
        runner._settle = lambda *_args, **_kwargs: None
        runner._collect = lambda **_kwargs: "coverage-after-deadline"
        runner._maybe_start_foreign_workflow = lambda **_kwargs: None

        recorded_after, gain, attempt_id = runner._record_execution(
            before=before,
            requested=action,
            execution=execution,
            provenance=__import__("valordroid.models", fromlist=["Provenance"]).Provenance.GUI,
            deadline_monotonic=0.0,
        )
        self.assertTrue(runner.core.committed)
        self.assertEqual((recorded_after, gain, attempt_id), (after, 0, "a-1"))


class LifecycleAppendRecoveryRegressionTest(unittest.TestCase):
    def test_idempotent_append_recovers_before_and_after_durability(self) -> None:
        from valordroid.events import LifecycleLedger
        from valordroid.models import LifecycleStatus

        class FaultLedger:
            def __init__(self, *, after_durable: bool) -> None:
                self.entries = []
                self.after_durable = after_durable
                self.raised = False

            def payloads(self):
                return list(self.entries)

            def append(self, payload):
                if not self.raised:
                    self.raised = True
                    if self.after_durable:
                        self.entries.append(payload)
                    raise KeyboardInterrupt("injected append interruption")
                self.entries.append(payload)

        before = FaultLedger(after_durable=False)
        ledger = LifecycleLedger(before)
        with self.assertRaises(KeyboardInterrupt):
            ledger.record(
                observed_at=1.0,
                phase="exploration",
                event="fault_injection",
                status=LifecycleStatus.INFO,
                record_id="l-fault-injection",
                idempotent=True,
            )
        ledger.record(
            observed_at=1.0,
            phase="exploration",
            event="fault_injection",
            status=LifecycleStatus.INFO,
            record_id="l-fault-injection",
            idempotent=True,
        )
        self.assertEqual(len(before.entries), 1)

        after = FaultLedger(after_durable=True)
        recovered = LifecycleLedger(after).record(
            observed_at=1.0,
            phase="exploration",
            event="fault_injection",
            status=LifecycleStatus.INFO,
            record_id="l-fault-injection",
            idempotent=True,
        )
        self.assertEqual(recovered.record_id, "l-fault-injection")
        self.assertEqual(len(after.entries), 1)


class PostDispatchFailureBindingRegressionTest(unittest.TestCase):
    def test_each_post_dispatch_stage_is_finalized_without_an_attempt_or_sample(self) -> None:
        from valordroid.dispatch import ActionDispatchReservation
        from valordroid.models import ActionSpec, OutcomeRecord, Provenance
        from valordroid.runner import AndroidRunner, RunAbort

        before = _observation("before", 1.0)
        after = _observation("after", 2.0)
        action = ActionSpec(
            "tap",
            target_id="target",
            parameters={"expected_state_id": "before", "selector": {"package": AUT}},
        )
        execution = ExecutionRecord(
            status=ExecutionStatus.EXECUTED,
            started_at=1.5,
            ended_at=1.6,
            action=action,
        )

        for stage, expected_disposition in (
            ("verifier", "post_action_verifier_failed"),
            ("settle", "post_action_settle_failed"),
            ("coverage", "coverage_collection_failed"),
            ("transaction_commit", "action_transaction_failed"),
        ):
            with self.subTest(stage=stage):
                class Core:
                    config = SimpleNamespace(association_settle_seconds=0.0)

                    def __init__(self):
                        self.finalized = False
                        self.finalization = None
                        self.attempts = []
                        self.raw_coverage_events = []

                    def reserve_action_dispatch(self, **kwargs):
                        return ActionDispatchReservation.create(
                            dispatch_sequence=1,
                            state_id=kwargs["state_id"],
                            before_activity=kwargs["before_activity"],
                            provenance=kwargs["provenance"],
                            requested=kwargs["requested"],
                            route_id=kwargs["route_id"],
                            reserved_at=1.4,
                            expected_attempt_sequence=1,
                        )

                    def dispatch_finalized(self, _dispatch_id):
                        return self.finalized

                    def finalize_action_dispatch(self, _reservation, **kwargs):
                        self.finalization = kwargs
                        self.finalized = True

                    def record_action(self, **_kwargs):
                        if stage == "transaction_commit":
                            raise RuntimeError("transaction commit failed")
                        raise AssertionError("record_action reached unexpectedly")

                class Verifier:
                    def __init__(self):
                        self.after = SimpleNamespace(observation=after)

                    def verify(self, *_args, **_kwargs):
                        if stage == "verifier":
                            raise RuntimeError("verifier failed")
                        return OutcomeRecord(
                            OutcomeKind.EFFECT,
                            state_changed=True,
                            details={
                                "before_state_id": "before",
                                "after_state_id": "after",
                            },
                        )

                core = Core()
                runner = AndroidRunner.__new__(AndroidRunner)
                runner.core = core
                runner.verifier = Verifier()
                runner.executor = SimpleNamespace(
                    last_target_text=None, last_field_cleared=None
                )
                runner._last_incomplete_dispatch_id = None
                runner._last_incomplete_dispatch_executed = False
                runner._asks_for_credentials = lambda: False
                runner._maybe_start_foreign_workflow = lambda **_kwargs: None

                def settle(*_args, **_kwargs):
                    if stage == "settle":
                        raise RuntimeError("settle failed")

                def collect(*, dispatch_progress=None, **_kwargs):
                    event_id = f"coverage-{stage}"
                    core.raw_coverage_events.append(event_id)
                    if dispatch_progress is not None:
                        dispatch_progress["coverage_event_id"] = event_id
                    if stage == "coverage":
                        raise RunAbort(
                            "coverage", "coverage_collection_failed", "collector failed"
                        )
                    return event_id

                runner._settle = settle
                runner._collect = collect

                with self.assertRaises((RuntimeError, RunAbort)):
                    runner._execute_and_record_action(
                        before=before,
                        requested=action,
                        provenance=Provenance.GUI,
                        dispatch=lambda: execution,
                    )

                self.assertTrue(core.finalized)
                self.assertEqual(core.attempts, [])
                self.assertEqual(
                    core.finalization["failure_stage"], stage
                )
                self.assertEqual(
                    core.finalization["disposition"], expected_disposition
                )
                expected_event = (
                    f"coverage-{stage}"
                    if stage in {"coverage", "transaction_commit"}
                    else None
                )
                self.assertEqual(
                    core.finalization["coverage_event_id"], expected_event
                )
                self.assertEqual(
                    core.raw_coverage_events,
                    [] if expected_event is None else [expected_event],
                )
