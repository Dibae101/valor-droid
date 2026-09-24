"""Ledger checksum indexing: fast validation that can never launder a bad chain.

The audit's evidence boundary is that published campaigns could not be rebound to
their detailed ledgers: counts and terminal hashes were recorded, but nothing tied
them to the exact bytes, and revalidating a large ledger meant recanonicalizing
every payload. The sidecar index at `<ledger>.index.jsonl` fixes both halves, and
the invariant that matters is the negative one: the index is a memoization of a
verified scan, so a stale, truncated, or forged index costs a rescan and never
substitutes for one.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from valordroid.ledger import (
    EMPTY_SHA256,
    INDEX_SUFFIX,
    ZERO_HASH,
    HashChainLedger,
    LedgerCorruption,
)


class LedgerIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="valordroid-ledger-index."))
        self.path = self._root / "actions.jsonl"
        self.ledger = HashChainLedger(self.path)
        self.ledger.ensure_exists()
        for index in range(6):
            self.ledger.append({"record_type": "probe", "index": index, "text": "héllo"})

    def tearDown(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def _fresh(self) -> HashChainLedger:
        """A ledger object with no process-local cache, like an auditor's."""

        return HashChainLedger(self.path)

    def _index_lines(self) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in self.ledger.index_path.read_text(encoding="utf-8").splitlines()
        ]

    def _write_index_lines(self, values: list[dict[str, object]]) -> None:
        self.ledger.index_path.write_text(
            "".join(
                json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
                for value in values
            ),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ shape

    def test_the_sidecar_is_named_after_its_ledger(self) -> None:
        self.assertEqual(self.ledger.index_path.name, "actions.jsonl" + INDEX_SUFFIX)
        self.assertTrue(self.ledger.index_path.is_file())

    def test_an_empty_ledger_gets_an_empty_rollup(self) -> None:
        empty = HashChainLedger(self._root / "empty.jsonl")
        empty.ensure_exists()
        rollup = json.loads(
            empty.index_path.read_text(encoding="utf-8").splitlines()[-1]
        )
        self.assertEqual(
            rollup,
            {
                "count": 0,
                "terminal_hash": ZERO_HASH,
                "file_sha256": EMPTY_SHA256,
                "bytes": 0,
            },
        )
        self.assertEqual(empty.validate(), (0, ZERO_HASH))

    def test_every_entry_line_records_its_exact_byte_range(self) -> None:
        lines = self._index_lines()
        entries, rollup = lines[:-1], lines[-1]
        self.assertEqual(len(entries), 6)
        raw = self.path.read_bytes()
        for entry in entries:
            self.assertEqual(set(entry), {"sequence", "byte_offset", "byte_length", "entry_hash"})
            slice_ = raw[
                int(entry["byte_offset"]) : int(entry["byte_offset"])
                + int(entry["byte_length"])
            ]
            self.assertTrue(slice_.endswith(b"\n"))
            self.assertEqual(
                json.loads(slice_.decode("utf-8"))["entry_hash"], entry["entry_hash"]
            )
        self.assertEqual(set(rollup), {"count", "terminal_hash", "file_sha256", "bytes"})
        self.assertEqual(rollup["count"], 6)
        self.assertEqual(rollup["bytes"], len(raw))
        self.assertEqual(rollup["file_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(rollup["terminal_hash"], entries[-1]["entry_hash"])

    def test_the_rollup_is_rewritten_in_place_on_every_append(self) -> None:
        before = self._index_lines()
        self.ledger.append({"record_type": "probe", "index": 6})
        after = self._index_lines()
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[:-2], before[:-1])
        self.assertEqual(after[-1]["count"], 7)

    def test_checksums_expose_the_rebindable_digests(self) -> None:
        checksums = self.ledger.checksums().to_dict()
        self.assertEqual(
            set(checksums),
            {"count", "terminal_hash", "file_sha256", "bytes", "index_sha256"},
        )
        self.assertEqual(checksums["count"], 6)
        self.assertEqual(
            checksums["file_sha256"],
            hashlib.sha256(self.path.read_bytes()).hexdigest(),
        )
        self.assertEqual(checksums["bytes"], self.path.stat().st_size)
        self.assertEqual(
            checksums["index_sha256"],
            hashlib.sha256(self.ledger.index_path.read_bytes()).hexdigest(),
        )

    # ------------------------------------------------------- fast path parity

    def test_the_fast_path_agrees_with_a_full_scan(self) -> None:
        fresh = self._fresh()
        self.assertEqual(fresh.validate(), fresh.validate(full_scan=True))

    def test_the_fast_path_avoids_recanonicalizing_every_payload(self) -> None:
        """O(1) here means a bounded number of entry verifications, not a rescan."""

        fresh = self._fresh()
        calls: list[int] = []
        original = HashChainLedger.__dict__["_scan_indexed"]

        def spy(cls, stream):  # type: ignore[no-untyped-def]
            calls.append(1)
            return original.__func__(cls, stream)

        HashChainLedger._scan_indexed = classmethod(spy)  # type: ignore[assignment]
        try:
            self.assertEqual(fresh.validate()[0], 6)
            self.assertEqual(calls, [], "the fast path should not have scanned")
            self.assertEqual(fresh.validate(full_scan=True)[0], 6)
            self.assertEqual(len(calls), 1, "the full scan should have scanned once")
        finally:
            HashChainLedger._scan_indexed = original  # type: ignore[assignment]

    def test_a_missing_index_still_validates_by_scanning(self) -> None:
        self.ledger.index_path.unlink()
        fresh = self._fresh()
        self.assertEqual(fresh.validate()[0], 6)

    # -------------------------------------------------------------- tampering

    def test_an_edited_ledger_payload_is_rejected(self) -> None:
        """Same length, different bytes: the digest binding forces the rescan."""

        lines = self.path.read_text(encoding="utf-8").splitlines()
        value = json.loads(lines[2])
        value["payload"]["index"] = 99
        lines[2] = json.dumps(value, sort_keys=True, separators=(",", ":"))
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(LedgerCorruption):
            self._fresh().validate()

    def test_a_ledger_truncated_mid_entry_is_rejected(self) -> None:
        raw = self.path.read_bytes()
        self.path.write_bytes(raw[:-20])
        with self.assertRaises(LedgerCorruption):
            self._fresh().validate()

    def test_a_ledger_truncated_on_a_line_boundary_reports_the_shorter_chain(self) -> None:
        """A prefix of a chain is a valid chain, so the stale count must not stand."""

        lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        self.path.write_text("".join(lines[:3]), encoding="utf-8")
        fresh = self._fresh()
        self.assertEqual(fresh.validate()[0], 3)
        self.assertEqual(fresh.validate(), fresh.validate(full_scan=True))

    def test_a_dropped_ledger_entry_with_a_regenerated_index_is_rejected(self) -> None:
        """The strongest case: the forger rebuilds the sidecar for a bad chain."""

        lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        del lines[2]
        self.path.write_text("".join(lines), encoding="utf-8")
        raw = self.path.read_bytes()
        offset = 0
        rebuilt: list[dict[str, object]] = []
        for line in lines:
            encoded = line.encode("utf-8")
            value = json.loads(line)
            rebuilt.append(
                {
                    "sequence": value["sequence"],
                    "byte_offset": offset,
                    "byte_length": len(encoded),
                    "entry_hash": value["entry_hash"],
                }
            )
            offset += len(encoded)
        rebuilt.append(
            {
                "count": len(rebuilt),
                "terminal_hash": str(rebuilt[-1]["entry_hash"]),
                "file_sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        )
        self._write_index_lines(rebuilt)
        with self.assertRaises(LedgerCorruption):
            self._fresh().validate()

    def test_a_stale_index_from_an_earlier_append_forces_a_rescan(self) -> None:
        stale = self.ledger.index_path.read_bytes()
        self.ledger.append({"record_type": "probe", "index": 6})
        expected = self.ledger.validate(full_scan=True)
        self.ledger.index_path.write_bytes(stale)
        fresh = self._fresh()
        self.assertEqual(fresh.validate(), expected)

    def test_a_truncated_index_forces_a_rescan(self) -> None:
        raw = self.ledger.index_path.read_bytes()
        self.ledger.index_path.write_bytes(raw[: len(raw) - 40])
        fresh = self._fresh()
        self.assertEqual(fresh.validate(), (6, self.ledger.validate(full_scan=True)[1]))

    def test_an_index_without_its_rollup_forces_a_rescan(self) -> None:
        lines = self._index_lines()
        self._write_index_lines(lines[:-1])
        fresh = self._fresh()
        self.assertEqual(fresh.validate()[0], 6)

    def test_a_forged_count_or_terminal_hash_forces_a_rescan(self) -> None:
        expected = self.ledger.validate(full_scan=True)
        for field, forged in (
            ("count", 99),
            ("terminal_hash", "b" * 64),
            ("bytes", 1),
            ("file_sha256", "c" * 64),
        ):
            with self.subTest(field=field):
                lines = self._index_lines()
                lines[-1][field] = forged
                self._write_index_lines(lines)
                self.assertEqual(self._fresh().validate(), expected)

    def test_forged_offsets_that_do_not_tile_the_file_force_a_rescan(self) -> None:
        expected = self.ledger.validate(full_scan=True)
        lines = self._index_lines()
        lines[3]["byte_offset"] = int(lines[3]["byte_offset"]) + 1
        self._write_index_lines(lines)
        self.assertEqual(self._fresh().validate(), expected)

    def test_a_reordered_index_forces_a_rescan(self) -> None:
        expected = self.ledger.validate(full_scan=True)
        lines = self._index_lines()
        entries, rollup = lines[:-1], lines[-1]
        entries[1], entries[2] = entries[2], entries[1]
        self._write_index_lines(entries + [rollup])
        self.assertEqual(self._fresh().validate(), expected)

    def test_a_garbage_index_forces_a_rescan(self) -> None:
        self.ledger.index_path.write_text("not json at all\n", encoding="utf-8")
        self.assertEqual(self._fresh().validate()[0], 6)

    # ------------------------------------------------------------- rebuilding

    def test_rebuild_repairs_a_damaged_index(self) -> None:
        self.ledger.index_path.write_text("garbage\n", encoding="utf-8")
        checksums = self._fresh().rebuild_index()
        self.assertEqual(checksums.count, 6)
        self.assertEqual(self._index_lines()[-1]["count"], 6)
        self.assertEqual(self._fresh().validate(), (6, checksums.terminal_hash))

    def test_rebuild_refuses_to_index_a_bad_chain(self) -> None:
        lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        del lines[1]
        self.path.write_text("".join(lines), encoding="utf-8")
        with self.assertRaises(LedgerCorruption):
            self._fresh().rebuild_index()

    def test_appending_after_a_damaged_index_restores_it(self) -> None:
        self.ledger.index_path.write_text("garbage\n", encoding="utf-8")
        fresh = self._fresh()
        fresh.append({"record_type": "probe", "index": 6})
        self.assertEqual(fresh.validate(), fresh.validate(full_scan=True))
        self.assertEqual(self._index_lines()[-1]["count"], 7)

    def test_the_index_survives_many_appends(self) -> None:
        for index in range(40):
            self.ledger.append({"record_type": "probe", "index": 100 + index})
        fresh = self._fresh()
        self.assertEqual(fresh.validate(), fresh.validate(full_scan=True))
        self.assertEqual(fresh.validate()[0], 46)
        lines = self._index_lines()
        self.assertEqual(len(lines), 47)
        self.assertEqual(lines[-1]["bytes"], self.path.stat().st_size)


if __name__ == "__main__":
    unittest.main()
