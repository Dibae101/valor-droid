"""The coverage-event ledger's delta dialect must lose nothing.

Coverage percentages are computed from these events, so an encoding that drops
or alters a single unit would corrupt a result while still looking healthy. The
tests below therefore assert reconstruction *by equality against the original
event objects*, not by spot-checking fields, and they assert it through the two
real readers rather than only through the codec.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valordroid.coverage_delta import (
    DELTA_RECORD_TYPE,
    CoverageEventAccumulator,
    read_coverage_event_payload,
)
from valordroid.events import CoverageEventLedger
from valordroid.ledger import HashChainLedger
from valordroid.models import CoverageEvent, CoverageEventStatus, canonical_json
from valordroid.universe import CoverageUniverse


def _universe(unit_ids: tuple[str, ...]) -> CoverageUniverse:
    """A frozen universe over `unit_ids`, built through the real factory."""

    return CoverageUniverse.create(
        package_name="com.example.app",
        metric="method",
        backend="logcat",
        apk_sha256="a" * 64,
        unit_ids=unit_ids,
    )


def _unit_index(unit_id: str) -> int:
    return int(unit_id.rsplit("-", 1)[1])


def _event(
    event_id: str,
    unit_ids: tuple[str, ...],
    universe: CoverageUniverse,
    *,
    at: float,
) -> CoverageEvent:
    """A successful cumulative event covering `unit_ids`, shaped like a real one.

    Provenance is a pure function of the unit, never of the event, because the
    collector freezes a unit's provenance the moment it first publishes it and
    the ledger rejects any later event that revises it. Deriving the values from
    the unit's index also means a fold that mixed two units up would produce
    unequal events rather than coincidentally equal ones.
    """

    ordered = tuple(sorted(unit_ids))

    def provenance(unit_id: str) -> tuple[float, float, int, str, str, float, float]:
        index = _unit_index(unit_id)
        host = 50.0 + index / 1000
        # The hit has to sit inside its ownership interval, and the interval has
        # to close before the collection did.
        return (host, 1000.0 + index, 4000 + index, universe.package_name,
                f"token-{index}", host - 1.0, host + 1.0)

    values = {unit_id: provenance(unit_id) for unit_id in ordered}
    return CoverageEvent(
        event_id=event_id,
        session_id="session-1",
        package_name=universe.package_name,
        backend=universe.backend,
        universe_sha256=universe.universe_sha256,
        status=CoverageEventStatus.SUCCEEDED,
        started_at=at - 1.0,
        observed_at=at,
        observed_unit_ids=ordered,
        unit_observed_at={u: values[u][0] for u in ordered},
        unit_device_observed_at={u: values[u][1] for u in ordered},
        unit_process_ids={u: values[u][2] for u in ordered},
        unit_process_names={u: values[u][3] for u in ordered},
        unit_process_start_tokens={u: values[u][4] for u in ordered},
        unit_ownership_observed_before={u: values[u][5] for u in ordered},
        unit_ownership_observed_after={u: values[u][6] for u in ordered},
    )


class CoverageDeltaRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.units = tuple(f"unit-{number:04d}" for number in range(1, 41))
        self.universe = _universe(self.units)
        # Three cumulative events, each strictly containing the previous, which
        # is the shape the collector actually emits.
        self.events = [
            _event("event-1", self.units[:10], self.universe, at=100.0),
            _event("event-2", self.units[:25], self.universe, at=200.0),
            _event("event-3", self.units, self.universe, at=300.0),
        ]

    def test_fold_reconstructs_every_event_exactly(self) -> None:
        writer = CoverageEventAccumulator()
        payloads = [writer.record_payload(event) for event in self.events]

        reader = CoverageEventAccumulator()
        rebuilt = [read_coverage_event_payload(payload, reader) for payload in payloads]

        for original, restored in zip(self.events, rebuilt):
            self.assertEqual(
                canonical_json(original.to_dict()),
                canonical_json(restored.to_dict()),
                f"{original.event_id} did not survive the round trip",
            )

    def test_only_new_units_are_retained(self) -> None:
        writer = CoverageEventAccumulator()
        payloads = [writer.record_payload(event) for event in self.events]

        self.assertEqual([p["record_type"] for p in payloads], [DELTA_RECORD_TYPE] * 3)
        self.assertEqual([len(p["event"]["new_unit_ids"]) for p in payloads], [10, 15, 15])
        # The cumulative form would repeat all 40 units on the last event; the
        # delta names 15. This is the size win, asserted rather than assumed.
        self.assertEqual(len(payloads[-1]["event"]["unit_process_ids"]), 15)

    def test_retained_bytes_grow_with_units_not_with_events(self) -> None:
        """The property that fixes the disk problem, measured end to end."""

        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_bytes = sum(
                len(canonical_json({"record_type": "coverage_event", "event": e.to_dict()}))
                for e in self.events
            )
            writer = CoverageEventAccumulator()
            delta_bytes = sum(
                len(canonical_json(writer.record_payload(event))) for event in self.events
            )
            self.assertLess(delta_bytes, snapshot_bytes)
            # With every event re-listing the whole table, the last event alone
            # costs more than all three increments together.
            self.assertLess(
                delta_bytes,
                len(
                    canonical_json(
                        {"record_type": "coverage_event", "event": self.events[-1].to_dict()}
                    )
                )
                * 2,
            )
            del root

    def test_ledger_reload_sees_the_same_events(self) -> None:
        """The runtime reader: write through the ledger, reopen, compare."""

        with TemporaryDirectory() as directory:
            path = Path(directory) / "coverage_events.jsonl"
            ledger = CoverageEventLedger(HashChainLedger(path), self.universe)
            for event in self.events:
                ledger.record(event)
            written = [event.to_dict() for event in ledger.events]

            reopened = CoverageEventLedger(HashChainLedger(path), self.universe)
            self.assertEqual(
                canonical_json(written),
                canonical_json([event.to_dict() for event in reopened.events]),
            )

    def test_snapshot_lines_still_decode(self) -> None:
        """A ledger written before this change must keep reading."""

        reader = CoverageEventAccumulator()
        for event in self.events:
            restored = read_coverage_event_payload(
                {"record_type": "coverage_event", "event": event.to_dict()}, reader
            )
            self.assertEqual(
                canonical_json(event.to_dict()), canonical_json(restored.to_dict())
            )

    def test_failed_event_carries_no_units_and_inherits_none(self) -> None:
        writer = CoverageEventAccumulator()
        writer.record_payload(self.events[0])
        failure = CoverageEvent(
            event_id="event-failed",
            session_id="session-1",
            package_name=self.universe.package_name,
            backend=self.universe.backend,
            universe_sha256=self.universe.universe_sha256,
            status=CoverageEventStatus.FAILED,
            started_at=150.0,
            observed_at=151.0,
            error="collector stopped responding",
        )
        payload = writer.record_payload(failure)
        self.assertEqual(payload["event"]["new_unit_ids"], [])

        reader = CoverageEventAccumulator()
        read_coverage_event_payload(
            {"record_type": DELTA_RECORD_TYPE, "event": writer_first(self.events[0])},
            reader,
        )
        restored = read_coverage_event_payload(payload, reader)
        self.assertIs(restored.status, CoverageEventStatus.FAILED)
        self.assertEqual(restored.observed_unit_ids, ())
        self.assertEqual(restored.unit_process_ids, {})

    def test_a_drain_event_after_a_failure_is_still_cumulative(self) -> None:
        """The one successful event allowed to follow a failure must fold whole."""

        with TemporaryDirectory() as directory:
            path = Path(directory) / "coverage_events.jsonl"
            ledger = CoverageEventLedger(HashChainLedger(path), self.universe)
            ledger.record(self.events[0])
            ledger.record(
                CoverageEvent(
                    event_id="event-failed",
                    session_id="session-1",
                    package_name=self.universe.package_name,
                    backend=self.universe.backend,
                    universe_sha256=self.universe.universe_sha256,
                    status=CoverageEventStatus.FAILED,
                    started_at=150.0,
                    observed_at=151.0,
                    error="collector stopped responding",
                )
            )
            drain = _event("event-drain", self.units[:25], self.universe, at=200.0)
            ledger.record(drain)

            reopened = CoverageEventLedger(HashChainLedger(path), self.universe)
            self.assertEqual(
                canonical_json(drain.to_dict()),
                canonical_json(reopened.events[-1].to_dict()),
            )

    def test_a_repeated_unit_is_rejected_rather_than_absorbed(self) -> None:
        """Corruption must surface, not be papered over by the fold."""

        writer = CoverageEventAccumulator()
        payload = writer.record_payload(self.events[0])
        reader = CoverageEventAccumulator()
        read_coverage_event_payload(payload, reader)
        with self.assertRaises(ValueError) as caught:
            read_coverage_event_payload(payload, reader)
        self.assertIn("already-observed", str(caught.exception))

    def test_unknown_record_type_fails_closed(self) -> None:
        with self.assertRaises(ValueError) as caught:
            read_coverage_event_payload(
                {"record_type": "something_else", "event": {}},
                CoverageEventAccumulator(),
            )
        self.assertIn("another record type", str(caught.exception))


def writer_first(event: CoverageEvent) -> dict:
    """The delta for `event` against an empty table (its whole content)."""

    return CoverageEventAccumulator().encode(event)


if __name__ == "__main__":
    unittest.main()
