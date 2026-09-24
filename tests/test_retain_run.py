"""Retention must not cost verifiability.

Compressing a run's ledgers gets 12.7:1, which is the difference between a 50-app
campaign fitting on this box and not. Compressing them *before* recording the verdict
is what made one sweep worthless: `valordroid summary` reads ledgers by name and
refused every archived run with `missing transactions.jsonl; missing actions.jsonl;
...`, so the numbers existed only in a log file and nothing could be re-checked.

These tests pin the order, pin that compression is refused when the verdict is
missing, and pin that restoring brings the run back byte for byte so an archived run
can be re-validated instead of trusted.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def _load():
    path = (
        Path(__file__).resolve().parent.parent / "tools" / "retain_run.py"
    )
    spec = importlib.util.spec_from_file_location("retain_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RetainRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load()

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.run = Path(self._temporary.name) / "run"
        self.run.mkdir(parents=True)
        self.addCleanup(self._temporary.cleanup)
        self.payloads = {
            "actions.jsonl": '{"payload":{"attempt":1}}\n' * 200,
            "coverage_events.jsonl": '{"payload":{"event":{"units":[1,2,3]}}}\n' * 400,
            "transactions.jsonl": '{"payload":{"record_type":"x"}}\n' * 100,
            "raw-logcat.txt": "VALORDROID: METHOD=<a: void b()>\n" * 300,
        }
        for name, text in self.payloads.items():
            (self.run / name).write_text(text, encoding="utf-8")
        observations = self.run / "observations" / "00000001-abcdef012345"
        observations.mkdir(parents=True)
        (observations / "hierarchy.xml").write_text("<hierarchy/>", encoding="utf-8")

    def _write_verdict(self) -> None:
        (self.run / "summary.json").write_text(
            json.dumps({"summary": {"observed_coverage_percent": 12.5}}) + "\n",
            encoding="utf-8",
        )

    def test_compressing_without_a_recorded_verdict_is_refused(self) -> None:
        """The regression, stated as a rule: no verdict, no compression."""

        with self.assertRaises(SystemExit) as caught:
            self.module.compress(self.run)
        self.assertIn("summary.json", str(caught.exception))
        # And nothing was touched.
        self.assertTrue((self.run / "actions.jsonl").is_file())
        self.assertFalse((self.run / "actions.jsonl.gz").is_file())

    def test_compression_shrinks_the_run_once_the_verdict_exists(self) -> None:
        self._write_verdict()
        sizes = self.module.compress(self.run)
        self.assertLess(sizes["bytes_after"], sizes["bytes_before"])
        self.assertTrue((self.run / "actions.jsonl.gz").is_file())
        self.assertFalse((self.run / "actions.jsonl").is_file())

    def test_the_verdict_survives_compression_readable(self) -> None:
        """The point of writing it first: it outlives the evidence's readability."""

        self._write_verdict()
        self.module.compress(self.run)
        recorded = json.loads((self.run / "summary.json").read_text())
        self.assertEqual(recorded["summary"]["observed_coverage_percent"], 12.5)

    def test_observations_are_archived_and_the_directory_removed(self) -> None:
        self._write_verdict()
        self.module.compress(self.run)
        self.assertTrue((self.run / "observations.tar.gz").is_file())
        self.assertFalse((self.run / "observations").is_dir())

    def test_restore_returns_every_ledger_byte_for_byte(self) -> None:
        """So an archived run is re-verifiable rather than taken on trust."""

        self._write_verdict()
        self.module.compress(self.run)
        self.module.restore(self.run)
        for name, text in self.payloads.items():
            self.assertTrue((self.run / name).is_file(), name)
            self.assertEqual((self.run / name).read_text(), text, name)

    def test_restore_brings_the_observations_back(self) -> None:
        self._write_verdict()
        self.module.compress(self.run)
        self.module.restore(self.run)
        frame = self.run / "observations" / "00000001-abcdef012345" / "hierarchy.xml"
        self.assertTrue(frame.is_file())
        self.assertEqual(frame.read_text(), "<hierarchy/>")

    def test_restore_is_safe_on_an_uncompressed_run(self) -> None:
        self.module.restore(self.run)
        self.assertTrue((self.run / "actions.jsonl").is_file())

    def test_compression_is_idempotent(self) -> None:
        self._write_verdict()
        self.module.compress(self.run)
        again = self.module.compress(self.run)
        self.assertTrue((self.run / "actions.jsonl.gz").is_file())
        self.assertGreater(again["bytes_after"], 0)


if __name__ == "__main__":
    unittest.main()
