"""Persistent UIAutomator2 fallback: ownership, generations, and its evidence.

The adapter's whole purpose is to own Android's singleton UiAutomation
registration without ever intentionally running two owners at once, and to leave
an audit trail that says which generation produced which observation. Neither
property is checked by anything else in the suite: no test drove this module
before, so the ordering rules below existed only as code.

These tests drive the real parent-side implementation. Only the spawned worker is
replaced, by a thread speaking the same framed socket protocol, so `_rpc`,
`_invoke`, `_connect`, `_retire_generation`, `capture`, `commit_observation` and
`close` are the production paths and the evidence is the production evidence. The
worker is scripted, which is what lets a dropped transport, a foreign service and
a refused retirement be reproduced deterministically instead of waited for.
"""

from __future__ import annotations

import hashlib
import os
import socket
import threading
import unittest
import xml.etree.ElementTree as ET

from valordroid.android import uiautomator2_fallback as u2
from valordroid.android.uiautomator2_fallback import (
    UIAUTOMATOR2_EVIDENCE_RULE,
    UIAUTOMATOR2_IDLE_TIMEOUT_MS,
    PersistentUiAutomator2Adapter,
    UiAutomator2FallbackError,
)

PACKAGE = "com.example.app"
ACTIVITY = f"{PACKAGE}/{PACKAGE}.MainActivity"
HIERARCHY = (
    "<?xml version='1.0' encoding='UTF-8'?><hierarchy rotation=\"0\">"
    f'<node index="0" class="android.widget.Button" package="{PACKAGE}"'
    ' resource-id="" text="Go" content-desc="" clickable="true"'
    ' long-clickable="false" scrollable="false" enabled="true"'
    ' password="false" bounds="[0,0][720,100]"/></hierarchy>'
)


class _Script:
    """What the fake device service does, per operation, in order.

    ``None`` means "answer normally". Anything else is either an exception type
    to simulate as a worker-side failure, or the literal string ``"drop"``, which
    closes the transport without answering -- the case the adapter must treat as
    an unproven release.
    """

    def __init__(
        self,
        *,
        opens: tuple[object, ...] = (None,),
        dumps: tuple[object, ...] = (None,),
        retires: tuple[object, ...] = (None,),
    ) -> None:
        self.opens = list(opens)
        self.dumps = list(dumps)
        self.retires = list(retires)

    def take(self, operation: str) -> object:
        queue = {"open": self.opens, "dump": self.dumps, "retire": self.retires}[
            operation
        ]
        return queue.pop(0) if queue else None


class _FakeWorker(threading.Thread):
    """One connection's worth of the framed protocol, driven by a script."""

    def __init__(self, transport: socket.socket, script: _Script) -> None:
        super().__init__(daemon=True)
        self.transport = transport
        self.script = script
        self.operations: list[str] = []

    def run(self) -> None:  # noqa: C901 - one linear protocol loop
        try:
            while True:
                try:
                    message, _payload = u2._recv_frame_blocking(self.transport)
                except (EOFError, OSError, ValueError):
                    return
                operation = str(message.get("operation"))
                request_id = message.get("request_id")
                self.operations.append(operation)
                behaviour = self.script.take(operation)
                if behaviour == "drop":
                    self.transport.close()
                    return
                payload = b""
                if behaviour is not None:
                    response = {
                        "request_id": request_id,
                        "operation": operation,
                        "ok": False,
                        "error_type": str(behaviour),
                        "error": f"scripted {behaviour}",
                        "release_disposition": (
                            "retired" if operation == "open" else None
                        ),
                    }
                elif operation == "open":
                    response = {
                        "request_id": request_id,
                        "operation": operation,
                        "ok": True,
                        "foreground_package": PACKAGE,
                        "foreground_activity": ACTIVITY,
                        "idle_timeout_ms": UIAUTOMATOR2_IDLE_TIMEOUT_MS,
                    }
                elif operation == "dump":
                    payload = HIERARCHY.encode("utf-8")
                    response = {
                        "request_id": request_id,
                        "operation": operation,
                        "ok": True,
                        "foreground_package": PACKAGE,
                        "foreground_activity": ACTIVITY,
                        "idle_timeout_ms": UIAUTOMATOR2_IDLE_TIMEOUT_MS,
                    }
                else:
                    response = {
                        "request_id": request_id,
                        "operation": operation,
                        "ok": True,
                        "service_retired": True,
                    }
                try:
                    u2._send_frame_blocking(self.transport, response, payload)
                except (OSError, ValueError):
                    return
                if operation == "retire" or (
                    operation == "open" and response.get("ok") is not True
                ):
                    return
        finally:
            try:
                self.transport.close()
            except OSError:
                pass


class _FakeProcess:
    """Just enough of `multiprocessing.Process` for the disposal path."""

    def __init__(self, target, args, name=None, daemon=None) -> None:
        # `_start_worker` closes its own copy of the child socket immediately
        # after start(), which is correct for a real subprocess because the fd is
        # duplicated into it. A thread shares the object, so the descriptor is
        # duplicated here to reproduce that ownership split.
        self._child = socket.socket(fileno=os.dup(args[0].fileno()))
        self._script = _FakeProcess.script
        self._worker = _FakeWorker(self._child, self._script)
        self.started = False
        _FakeProcess.instances.append(self)

    instances: list["_FakeProcess"] = []
    script = _Script()

    def start(self) -> None:
        self.started = True
        self._worker.start()

    def join(self, timeout=None) -> None:
        self._worker.join(0.5 if timeout is None else max(0.01, timeout))

    def is_alive(self) -> bool:
        return self._worker.is_alive()

    def terminate(self) -> None:
        try:
            self._child.close()
        except OSError:
            pass

    kill = terminate

    def close(self) -> None:
        # The real Process.close() releases the handle's resources; closing the
        # duplicated descriptor here keeps the test from leaking one per case.
        try:
            self._child.close()
        except OSError:
            pass

    def pool(self):  # pragma: no cover - retirement probe parity
        return None


class _FakeContext:
    Process = _FakeProcess


def _adapter(records: list[dict], script: _Script) -> PersistentUiAutomator2Adapter:
    _FakeProcess.instances = []
    _FakeProcess.script = script
    adapter = PersistentUiAutomator2Adapter(
        serial="fake-u2-device",
        timeout_seconds=5.0,
        evidence_recorder=records.append,
    )
    adapter._context = _FakeContext()
    return adapter


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class LifecycleEvidenceTest(unittest.TestCase):
    """Every generation announces itself, and every record is well formed."""

    def setUp(self) -> None:
        self.records: list[dict] = []

    def _capture_and_commit(self, adapter, sequence: int = 1) -> None:
        capture = adapter.capture(timeout_seconds=5.0)
        root = ET.fromstring(capture.hierarchy)
        self.assertEqual(root.tag, "hierarchy")
        adapter.commit_observation(
            capture,
            observation_sequence=sequence,
            state_id="1" * 64,
            hierarchy_sha256=_digest(capture.hierarchy),
            resumed_activities=(ACTIVITY,),
        )

    def test_activation_then_observation_then_clean_close(self) -> None:
        adapter = _adapter(self.records, _Script())
        self._capture_and_commit(adapter)
        self.assertEqual(adapter.close(), ())
        events = [(item["event"], item["status"]) for item in self.records]
        self.assertEqual(
            events,
            [
                ("activation", "succeeded"),
                ("observation", "succeeded"),
                ("retirement", "succeeded"),
                ("terminal", "closed"),
            ],
        )

    def test_activation_is_what_hands_ownership_over(self) -> None:
        adapter = _adapter(self.records, _Script())
        self.assertFalse(adapter.activated)
        self._capture_and_commit(adapter)
        # Monotone: once the service owns UiAutomation, the shell dumper must
        # never be started beside it again.
        self.assertTrue(adapter.activated)
        adapter.close()
        self.assertTrue(adapter.activated)

    def test_every_record_carries_the_rule_and_a_zero_idle_timeout(self) -> None:
        adapter = _adapter(self.records, _Script())
        self._capture_and_commit(adapter)
        adapter.close()
        for record in self.records:
            self.assertEqual(record["rule"], UIAUTOMATOR2_EVIDENCE_RULE)
            self.assertEqual(record["idle_timeout_ms"], 0)
            self.assertLessEqual(
                record["request_started_at"], record["response_received_at"]
            )

    def test_the_fallback_sequence_is_strictly_increasing(self) -> None:
        adapter = _adapter(self.records, _Script(dumps=(None, None)))
        self._capture_and_commit(adapter, sequence=1)
        self._capture_and_commit(adapter, sequence=2)
        adapter.close()
        sequences = [record["fallback_sequence"] for record in self.records]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))

    def test_a_committed_observation_binds_its_artifact(self) -> None:
        adapter = _adapter(self.records, _Script())
        capture = adapter.capture(timeout_seconds=5.0)
        digest = _digest(capture.hierarchy)
        adapter.commit_observation(
            capture,
            observation_sequence=7,
            state_id="a" * 64,
            hierarchy_sha256=digest,
            resumed_activities=(ACTIVITY,),
        )
        adapter.close()
        observation = next(
            item for item in self.records if item["event"] == "observation"
        )
        self.assertEqual(observation["observation_sequence"], 7)
        self.assertEqual(observation["state_id"], "a" * 64)
        self.assertEqual(observation["hierarchy_sha256"], digest)
        self.assertEqual(observation["foreground_activity"], ACTIVITY)
        self.assertEqual(observation["resumed_activities"], [ACTIVITY])
        self.assertEqual(observation["session_generation"], 1)
        self.assertIsNotNone(observation["server_port"])

    def test_a_connection_event_claims_no_observation_fields(self) -> None:
        adapter = _adapter(self.records, _Script())
        self._capture_and_commit(adapter)
        adapter.close()
        activation = self.records[0]
        for name in ("observation_sequence", "hierarchy_sha256", "state_id"):
            self.assertIsNone(activation[name], name)

    def test_a_malformed_digest_or_sequence_is_refused(self) -> None:
        adapter = _adapter(self.records, _Script())
        capture = adapter.capture(timeout_seconds=5.0)
        for kwargs in (
            {"state_id": "short"},
            {"hierarchy_sha256": "NOTHEX" + "0" * 58},
            {"observation_sequence": 0},
        ):
            with self.subTest(kwargs=kwargs):
                values = {
                    "observation_sequence": 1,
                    "state_id": "1" * 64,
                    "hierarchy_sha256": _digest(capture.hierarchy),
                    "resumed_activities": (ACTIVITY,),
                }
                values.update(kwargs)
                with self.assertRaises(ValueError):
                    adapter.commit_observation(capture, **values)
        adapter.close()


class ForegroundBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.records: list[dict] = []

    def test_a_foreground_that_left_the_resumed_set_fails_the_observation(self) -> None:
        """The dump is only usable if the screen it named is still the screen."""

        adapter = _adapter(self.records, _Script())
        capture = adapter.capture(timeout_seconds=5.0)
        with self.assertRaises(UiAutomator2FallbackError):
            adapter.commit_observation(
                capture,
                observation_sequence=1,
                state_id="1" * 64,
                hierarchy_sha256=_digest(capture.hierarchy),
                resumed_activities=(f"{PACKAGE}/{PACKAGE}.OtherActivity",),
            )
        failed = next(
            item
            for item in self.records
            if item["event"] == "observation" and item["status"] == "failed"
        )
        self.assertEqual(failed["error_type"], "ForegroundChangedAfterCapture")
        # A failed observation claims no artifact.
        self.assertIsNone(failed["hierarchy_sha256"])
        self.assertIsNone(failed["observation_sequence"])
        self.assertNotIn(
            failed["foreground_activity"], failed["resumed_activities"]
        )
        adapter.close()

    def test_an_uncommitted_response_is_retired_not_fatal(self) -> None:
        """An abandoned frame costs one observation, not the run.

        Blocking the next capture was a mechanism for accountability, not the
        property being protected. The property is that a captured hierarchy can
        never silently become evidence, and retiring the orphan as
        ObservationNotCommitted keeps it while letting the loop continue.

        Blocking cost whole runs. Any stage between capture and commit can
        abandon a frame -- a frame refused because the foreground moved among
        them -- and the next capture then died. Muzei aborted this way at a 1.3s
        and a 0.5s per-action floor while finishing normally at 2.6s, because a
        faster loop abandons frames more often, not because the run was unhealthy.
        """

        adapter = _adapter(self.records, _Script(dumps=(None, None)))
        abandoned = adapter.capture(timeout_seconds=5.0)

        # The next capture proceeds instead of raising.
        again = adapter.capture(timeout_seconds=5.0)
        self.assertNotEqual(again.request_id, abandoned.request_id)

        retired = [
            record for record in self.records
            if record.get("error_type") == "ObservationNotCommitted"
        ]
        self.assertEqual(len(retired), 1, self.records)
        self.assertEqual(retired[0]["status"], "failed")
        self.assertIsNone(retired[0]["observation_sequence"])

        # The abandoned frame can never become an observation afterwards.
        with self.assertRaises(UiAutomator2FallbackError):
            adapter.commit_observation(
                abandoned,
                observation_sequence=1,
                state_id="1" * 64,
                hierarchy_sha256=_digest(abandoned.hierarchy),
                resumed_activities=(ACTIVITY,),
            )
        adapter.close()

    def test_a_foreign_capture_cannot_be_committed(self) -> None:
        adapter = _adapter(self.records, _Script())
        capture = adapter.capture(timeout_seconds=5.0)
        adapter.commit_observation(
            capture,
            observation_sequence=1,
            state_id="1" * 64,
            hierarchy_sha256=_digest(capture.hierarchy),
            resumed_activities=(ACTIVITY,),
        )
        # Committing the same response twice would double-count an observation.
        with self.assertRaises(UiAutomator2FallbackError):
            adapter.commit_observation(
                capture,
                observation_sequence=2,
                state_id="1" * 64,
                hierarchy_sha256=_digest(capture.hierarchy),
                resumed_activities=(ACTIVITY,),
            )
        adapter.close()


class GenerationOwnershipTest(unittest.TestCase):
    """A generation may be replaced only after its service acknowledged release."""

    def setUp(self) -> None:
        self.records: list[dict] = []

    def _events(self) -> list[tuple[str, str]]:
        return [(item["event"], item["status"]) for item in self.records]

    def test_a_recoverable_dump_failure_reconnects_as_a_new_generation(self) -> None:
        """The service answered, so its release can be proven and replaced."""

        adapter = _adapter(
            self.records, _Script(dumps=("_HierarchyTooLargeError", None))
        )
        capture = adapter.capture(timeout_seconds=5.0)
        adapter.commit_observation(
            capture,
            observation_sequence=1,
            state_id="1" * 64,
            hierarchy_sha256=_digest(capture.hierarchy),
            resumed_activities=(ACTIVITY,),
        )
        events = self._events()
        self.assertEqual(events[0], ("activation", "succeeded"))
        self.assertIn(("observation", "failed"), events)
        self.assertIn(("retirement", "succeeded"), events)
        self.assertIn(("reconnect", "succeeded"), events)
        # The observation that survived belongs to the second generation.
        self.assertEqual(capture.session_generation, 2)
        adapter.close()

    def test_a_dropped_transport_refuses_to_reconnect(self) -> None:
        """The load-bearing ownership rule, stated as a refusal.

        A lost transport means the worker cannot acknowledge that it released the
        device service. Starting a second generation would then risk two owners of
        Android's singleton UiAutomation registration, so the adapter must fail
        the run instead of retrying -- the opposite of the recoverable case above.
        """

        adapter = _adapter(self.records, _Script(dumps=("drop",)))
        with self.assertRaises(UiAutomator2FallbackError) as caught:
            adapter.capture(timeout_seconds=5.0)
        self.assertIn("without a safe reconnect", str(caught.exception))
        events = self._events()
        self.assertIn(("observation", "failed"), events)
        self.assertNotIn("reconnect", [event for event, _status in events])
        # And no later generation may be opened either.
        with self.assertRaises(UiAutomator2FallbackError):
            adapter.capture(timeout_seconds=5.0)
        self.assertEqual(
            [event for event, _status in self._events()].count("activation"), 1
        )

    def test_a_reconnect_never_reuses_the_retired_port(self) -> None:
        adapter = _adapter(
            self.records, _Script(dumps=("_HierarchyTooLargeError", None))
        )
        adapter.capture(timeout_seconds=5.0)
        ports = {
            record["server_port"]
            for record in self.records
            if record["server_port"] is not None
        }
        self.assertGreaterEqual(len(ports), 2)
        generations = [
            record["session_generation"]
            for record in self.records
            if record["event"] in ("activation", "reconnect")
        ]
        self.assertEqual(generations, [1, 2])

    def test_generations_never_decrease(self) -> None:
        adapter = _adapter(
            self.records, _Script(dumps=("_HierarchyTooLargeError", None))
        )
        adapter.capture(timeout_seconds=5.0)
        seen = [record["session_generation"] for record in self.records]
        self.assertEqual(seen, sorted(seen))

    def test_a_dump_failure_the_transport_survived_is_recorded(self) -> None:
        adapter = _adapter(
            self.records, _Script(dumps=("_HierarchyTooLargeError", None))
        )
        adapter.capture(timeout_seconds=5.0)
        failed = next(
            item
            for item in self.records
            if item["event"] == "observation" and item["status"] == "failed"
        )
        self.assertEqual(failed["error_type"], "HierarchyTooLargeError")
        adapter.close()

    def test_a_refused_activation_is_recorded_and_not_retried(self) -> None:
        adapter = _adapter(self.records, _Script(opens=("_ServiceOwnershipError",)))
        with self.assertRaises(UiAutomator2FallbackError):
            adapter.capture(timeout_seconds=5.0)
        self.assertEqual(self._events()[0], ("activation", "failed"))
        # A foreign service may already own Android's singleton registration, so
        # a second attempt is forbidden rather than merely discouraged.
        with self.assertRaises(UiAutomator2FallbackError):
            adapter.capture(timeout_seconds=5.0)
        self.assertEqual(
            [event for event, _status in self._events()].count("activation"), 1
        )

    def test_a_refused_retirement_is_recorded_as_a_failure(self) -> None:
        adapter = _adapter(
            self.records, _Script(retires=("_ServiceRetirementError",))
        )
        capture = adapter.capture(timeout_seconds=5.0)
        adapter.commit_observation(
            capture,
            observation_sequence=1,
            state_id="1" * 64,
            hierarchy_sha256=_digest(capture.hierarchy),
            resumed_activities=(ACTIVITY,),
        )
        errors = adapter.close()
        events = self._events()
        self.assertIn(("retirement", "failed"), events)
        self.assertEqual(events[-1][0], "terminal")
        self.assertEqual(events[-1][1], "failed")
        self.assertTrue(errors)


class TerminalDispositionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.records: list[dict] = []

    def test_an_adapter_that_never_activated_reports_not_activated(self) -> None:
        adapter = _adapter(self.records, _Script())
        self.assertEqual(adapter.close(), ())
        self.assertEqual(len(self.records), 1)
        terminal = self.records[0]
        self.assertEqual(terminal["event"], "terminal")
        self.assertEqual(terminal["status"], "not_activated")
        self.assertEqual(terminal["session_generation"], 0)
        self.assertIsNone(terminal["server_port"])

    def test_exactly_one_terminal_record_however_often_close_is_called(self) -> None:
        adapter = _adapter(self.records, _Script())
        capture = adapter.capture(timeout_seconds=5.0)
        adapter.commit_observation(
            capture,
            observation_sequence=1,
            state_id="1" * 64,
            hierarchy_sha256=_digest(capture.hierarchy),
            resumed_activities=(ACTIVITY,),
        )
        first = adapter.close()
        second = adapter.close()
        self.assertEqual(first, second)
        terminals = [item for item in self.records if item["event"] == "terminal"]
        self.assertEqual(len(terminals), 1)

    def test_the_terminal_record_is_last(self) -> None:
        adapter = _adapter(self.records, _Script())
        capture = adapter.capture(timeout_seconds=5.0)
        adapter.commit_observation(
            capture,
            observation_sequence=1,
            state_id="1" * 64,
            hierarchy_sha256=_digest(capture.hierarchy),
            resumed_activities=(ACTIVITY,),
        )
        adapter.close()
        self.assertEqual(self.records[-1]["event"], "terminal")
        self.assertNotIn(
            "terminal", [item["event"] for item in self.records[:-1]]
        )

    def test_an_uncommitted_response_is_failed_before_the_terminal_record(self) -> None:
        """Otherwise a dump that was never bound would vanish from the record."""

        adapter = _adapter(self.records, _Script())
        adapter.capture(timeout_seconds=5.0)
        adapter.close()
        events = [(item["event"], item["status"]) for item in self.records]
        self.assertIn(("observation", "failed"), events)
        self.assertEqual(events[-1][0], "terminal")

    def test_capture_after_close_is_refused(self) -> None:
        adapter = _adapter(self.records, _Script())
        adapter.close()
        with self.assertRaises(UiAutomator2FallbackError):
            adapter.capture(timeout_seconds=5.0)

    def test_a_request_budget_below_the_floor_is_refused(self) -> None:
        adapter = _adapter(self.records, _Script())
        with self.assertRaises(UiAutomator2FallbackError) as caught:
            adapter.capture(timeout_seconds=0.0)
        self.assertIn("budget is exhausted", str(caught.exception))
        adapter.close()


class ConstructionTest(unittest.TestCase):
    def test_a_serial_and_a_positive_timeout_are_required(self) -> None:
        for kwargs in (
            {"serial": ""},
            {"serial": "   "},
            {"timeout_seconds": 0},
            {"timeout_seconds": -1.0},
        ):
            with self.subTest(kwargs=kwargs):
                values = {"serial": "fake-u2-device", "timeout_seconds": 5.0}
                values.update(kwargs)
                with self.assertRaises(ValueError):
                    PersistentUiAutomator2Adapter(**values)

    def test_the_pinned_idle_timeout_is_zero(self) -> None:
        """A nonzero idle wait is the failure this whole module exists to avoid."""

        self.assertEqual(UIAUTOMATOR2_IDLE_TIMEOUT_MS, 0)


if __name__ == "__main__":
    unittest.main()
