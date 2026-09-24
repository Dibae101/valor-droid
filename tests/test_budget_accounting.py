"""Total-budget accounting and rebindable ledger digests in the run manifest.

Section 5 of the gap audit requires equal wall-clock budgets and says a union of
two 60-minute runs is a 120-minute result. The published V1/V2 manifests recorded
`max_seconds=3600` and nothing about what was actually spent, so a run that
stopped at 25 minutes and a run that filled the hour were indistinguishable in
analysis. These tests pin the accounting that closes that gap and the rule that
every figure in it is derived from evidence the run already retained.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from valordroid.foreign_workflow import RUN_BUDGET_BOUNDARY, RUN_BUDGET_STARTED
from valordroid.ledger import HashChainLedger
from valordroid.store import (
    BUDGET_ACCOUNTING_SOURCE,
    EXPLORATION_BUDGET_BOUNDARY,
    EXPLORATION_BUDGET_STARTED,
    EXPLORATION_STOPPED,
    MANIFEST_ABORTED_FIELDS,
    MANIFEST_BUDGET_FIELDS,
    MANIFEST_FINISHED_FIELDS,
    MANIFEST_LEDGER_CHECKSUM_FIELD,
    RunStore,
    derive_budget_accounting,
    validate_manifest_shape,
)
from valordroid.universe import CoverageUniverse


def _lifecycle(event: str, observed_at: float, phase: str = "exploration") -> dict:
    return {
        "record_type": "lifecycle",
        "event": {
            "record_id": f"l-{event}-{observed_at}",
            "observed_at": observed_at,
            "phase": phase,
            "event": event,
            "status": "info",
            "details": {},
            "error": None,
        },
    }


class PhaseBoundaryNameTest(unittest.TestCase):
    """The literals used for derivation must be the producers' own names."""

    def test_the_budget_events_match_their_producing_constants(self) -> None:
        self.assertEqual(EXPLORATION_BUDGET_STARTED, RUN_BUDGET_STARTED)
        self.assertEqual(EXPLORATION_BUDGET_BOUNDARY, RUN_BUDGET_BOUNDARY)

    def test_the_exploration_stop_event_is_the_one_the_validator_requires(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "src"
            / "valordroid"
            / "validator.py"
        ).read_text(encoding="utf-8")
        self.assertIn(f'"{EXPLORATION_STOPPED}"', source)


class BudgetDerivationTest(unittest.TestCase):
    """Every phase is a difference of two retained timestamps, or it is null."""

    BASE = {
        "created_at": 1000.0,
        "runtime_configuration": {"max_seconds": 3600.0},
    }

    def test_a_complete_run_accounts_for_every_phase(self) -> None:
        manifest = {**self.BASE, "finished_at": 4000.0}
        payloads = [
            _lifecycle(EXPLORATION_BUDGET_STARTED, 1100.0),
            _lifecycle(EXPLORATION_STOPPED, 3900.0),
        ]
        result = derive_budget_accounting(manifest, payloads)
        self.assertEqual(result["budget_declared_seconds"], 3600.0)
        self.assertEqual(result["setup_seconds"], 100.0)
        self.assertEqual(result["exploration_seconds"], 2800.0)
        self.assertEqual(result["teardown_seconds"], 100.0)
        self.assertEqual(result["total_wall_clock_seconds"], 3000.0)
        self.assertEqual(result["budget_accounting_source"], BUDGET_ACCOUNTING_SOURCE)

    def test_the_phases_sum_to_the_total(self) -> None:
        manifest = {**self.BASE, "finished_at": 4000.0}
        result = derive_budget_accounting(
            manifest,
            [
                _lifecycle(EXPLORATION_BUDGET_STARTED, 1250.0),
                _lifecycle(EXPLORATION_STOPPED, 3111.0),
            ],
        )
        self.assertAlmostEqual(
            result["setup_seconds"]
            + result["exploration_seconds"]
            + result["teardown_seconds"],
            result["total_wall_clock_seconds"],
        )

    def test_the_budget_boundary_substitutes_for_a_missing_stop(self) -> None:
        result = derive_budget_accounting(
            {**self.BASE, "finished_at": 4000.0},
            [
                _lifecycle(EXPLORATION_BUDGET_STARTED, 1100.0),
                _lifecycle(EXPLORATION_BUDGET_BOUNDARY, 3800.0),
            ],
        )
        self.assertEqual(result["exploration_seconds"], 2700.0)

    def test_an_abort_before_exploration_reports_nulls_not_zeros(self) -> None:
        """A phase that never happened must not be reported as instantaneous."""

        result = derive_budget_accounting(
            {**self.BASE, "aborted_at": 1050.0}, []
        )
        self.assertIsNone(result["setup_seconds"])
        self.assertIsNone(result["exploration_seconds"])
        self.assertIsNone(result["teardown_seconds"])
        self.assertEqual(result["total_wall_clock_seconds"], 50.0)
        self.assertEqual(result["budget_declared_seconds"], 3600.0)

    def test_a_core_only_run_has_no_declared_budget(self) -> None:
        """A run with no Android runtime must not borrow a budget figure."""

        result = derive_budget_accounting(
            {"created_at": 10.0, "finished_at": 20.0, "runtime_configuration": {}}, []
        )
        self.assertIsNone(result["budget_declared_seconds"])
        self.assertEqual(result["total_wall_clock_seconds"], 10.0)

    def test_only_the_first_budget_start_is_used(self) -> None:
        """The budget start is idempotent evidence; a later copy cannot move it."""

        result = derive_budget_accounting(
            {**self.BASE, "finished_at": 4000.0},
            [
                _lifecycle(EXPLORATION_BUDGET_STARTED, 1100.0),
                _lifecycle(EXPLORATION_BUDGET_STARTED, 2000.0),
                _lifecycle(EXPLORATION_STOPPED, 3900.0),
            ],
        )
        self.assertEqual(result["setup_seconds"], 100.0)

    def test_an_out_of_order_stop_is_refused_rather_than_negated(self) -> None:
        result = derive_budget_accounting(
            {**self.BASE, "finished_at": 4000.0},
            [
                _lifecycle(EXPLORATION_BUDGET_STARTED, 3000.0),
                _lifecycle(EXPLORATION_STOPPED, 1100.0),
            ],
        )
        self.assertIsNone(result["exploration_seconds"])
        self.assertIsNone(result["teardown_seconds"])
        self.assertEqual(result["setup_seconds"], 2000.0)

    def test_a_terminal_time_before_creation_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            derive_budget_accounting({**self.BASE, "finished_at": 1.0}, [])

    def test_a_manifest_without_a_terminal_time_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            derive_budget_accounting(dict(self.BASE), [])

    def test_derivation_is_pure(self) -> None:
        """Repeating the derivation must reproduce it byte for byte."""

        manifest = {**self.BASE, "finished_at": 4000.0}
        payloads = [
            _lifecycle(EXPLORATION_BUDGET_STARTED, 1100.0),
            _lifecycle(EXPLORATION_STOPPED, 3900.0),
        ]
        first = derive_budget_accounting(manifest, list(payloads))
        second = derive_budget_accounting(manifest, list(payloads))
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


class ManifestShapeTest(unittest.TestCase):
    """The terminal shape requires the accounting and refuses nonsense values."""

    def test_both_terminal_shapes_require_the_budget_fields(self) -> None:
        for fields in (MANIFEST_FINISHED_FIELDS, MANIFEST_ABORTED_FIELDS):
            self.assertTrue(MANIFEST_BUDGET_FIELDS <= fields)
            self.assertIn(MANIFEST_LEDGER_CHECKSUM_FIELD, fields)

    def test_the_seconds_fields_are_not_treated_as_integer_counters(self) -> None:
        """A float duration must not be rejected by the counter type check."""

        self.assertNotIn("total_wall_clock_seconds", {"attempts", "covered_units"})
        manifest = _finished_manifest()
        manifest["exploration_seconds"] = 1234.5
        validate_manifest_shape(manifest)

    def test_a_negative_duration_is_refused(self) -> None:
        manifest = _finished_manifest()
        manifest["setup_seconds"] = -1.0
        with self.assertRaises(ValueError):
            validate_manifest_shape(manifest)

    def test_a_missing_ledger_checksum_is_refused(self) -> None:
        manifest = _finished_manifest()
        del manifest[MANIFEST_LEDGER_CHECKSUM_FIELD]["actions"]
        with self.assertRaises(ValueError):
            validate_manifest_shape(manifest)

    def test_an_incomplete_ledger_checksum_is_refused(self) -> None:
        manifest = _finished_manifest()
        manifest[MANIFEST_LEDGER_CHECKSUM_FIELD]["actions"] = {"count": 1}
        with self.assertRaises(ValueError):
            validate_manifest_shape(manifest)


def _finished_manifest() -> dict:
    heads = {
        name: {"count": 0, "terminal_hash": "0" * 64}
        for name in RunStore.LEDGER_FILES
    }
    checksums = {
        name: {
            "count": 0,
            "terminal_hash": "0" * 64,
            "file_sha256": "a" * 64,
            "bytes": 0,
            "index_sha256": "b" * 64,
        }
        for name in RunStore.LEDGER_FILES
    }
    return {
        "run_id": "run",
        "tool_version": "0",
        "schema_version": __import__(
            "valordroid.models", fromlist=["SCHEMA_VERSION"]
        ).SCHEMA_VERSION,
        "created_at": 1.0,
        "universe_sha256": "c" * 64,
        "package_name": "com.example.app",
        "metric": "method",
        "backend": "backend",
        "prepared_bundle_sha256": None,
        "status": "finished",
        "configuration": {},
        "runtime_configuration": {},
        "last_attempt_sequence": 0,
        "last_coverage_sequence": 0,
        "finished_at": 2.0,
        "attempts": 0,
        "coverage_samples": 0,
        "association_units": 0,
        "association_batches": 0,
        "covered_units": 0,
        "total_units": 0,
        "observed_coverage_percent": 0.0,
        "primary_gui_deep_link_units": 0,
        "all_route_covered_units": 0,
        "model_calls": 0,
        "crash_events": 0,
        "route_attempts": 0,
        "evidence_heads": heads,
        "coverage_gain_claim": "not_established_without_matched_control",
        "budget_declared_seconds": None,
        "setup_seconds": None,
        "exploration_seconds": None,
        "teardown_seconds": None,
        "total_wall_clock_seconds": 1.0,
        "budget_accounting_source": BUDGET_ACCOUNTING_SOURCE,
        MANIFEST_LEDGER_CHECKSUM_FIELD: checksums,
    }


class TerminalInjectionTest(unittest.TestCase):
    """Sealing derives the accounting; the caller never supplies it."""

    def setUp(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="valordroid-budget."))
        universe = CoverageUniverse.create(
            package_name="com.example.app",
            metric="method",
            backend="regression",
            apk_sha256="d" * 64,
            unit_ids=["com.example.app.A.a()V"],
        )
        self.store = RunStore.create(
            self._root / "run",
            universe,
            run_id="budget-run",
            runtime_configuration={"max_seconds": 1800.0},
            created_at=500.0,
        )
        lifecycle = self.store.ledger("lifecycle")
        lifecycle.append(_lifecycle(EXPLORATION_BUDGET_STARTED, 560.0))
        lifecycle.append(_lifecycle(EXPLORATION_STOPPED, 2300.0))

    def tearDown(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)

    def _aborted(self) -> dict:
        current = self.store.manifest()
        return {
            **current,
            "status": "aborted",
            "last_attempt_sequence": 0,
            "last_coverage_sequence": 0,
            "aborted_at": 2400.0,
            "abort_phase": "exploration",
            "abort_reason": "no_action_attempt_completed",
            "abort_error": "no_action_attempt_completed",
            "evidence_heads": self.store.evidence_heads(),
            "coverage_gain_claim": "not_established_without_matched_control",
        }

    def test_sealing_injects_the_accounting(self) -> None:
        self.store.finalize_terminal(self._aborted())
        manifest = self.store.manifest()
        self.assertEqual(manifest["budget_declared_seconds"], 1800.0)
        self.assertEqual(manifest["setup_seconds"], 60.0)
        self.assertEqual(manifest["exploration_seconds"], 1740.0)
        self.assertEqual(manifest["teardown_seconds"], 100.0)
        self.assertEqual(manifest["total_wall_clock_seconds"], 1900.0)

    def test_sealing_injects_rebindable_ledger_digests(self) -> None:
        self.store.finalize_terminal(self._aborted())
        recorded = self.store.manifest()[MANIFEST_LEDGER_CHECKSUM_FIELD]
        self.assertEqual(set(recorded), set(RunStore.LEDGER_FILES))
        observed = self.store.evidence_heads(checksums=True)
        self.assertEqual(recorded, observed)
        lifecycle = HashChainLedger(self._root / "run" / "lifecycle.jsonl")
        self.assertEqual(recorded["lifecycle"]["count"], 2)
        self.assertEqual(
            recorded["lifecycle"]["file_sha256"], lifecycle.checksums().file_sha256
        )

    def test_evidence_heads_keeps_its_two_key_shape_by_default(self) -> None:
        """Independent run validation recomputes exactly these two keys."""

        for value in self.store.evidence_heads().values():
            self.assertEqual(set(value), {"count", "terminal_hash"})

    def test_resealing_the_same_terminal_evidence_is_accepted(self) -> None:
        candidate = self._aborted()
        self.store.finalize_terminal(candidate)
        before = (self._root / "run" / "run.json").read_bytes()
        self.store.finalize_terminal(candidate)
        self.assertEqual((self._root / "run" / "run.json").read_bytes(), before)

    def test_setup_and_teardown_can_push_a_run_past_its_declared_budget(self) -> None:
        """Exploration fits the budget; setup and teardown make the run exceed it.

        This is the section 5 case that matters: a strategy whose initialization
        and handoff sit outside the declared window has not matched an arm that
        declares the same `max_seconds`.
        """

        self.store.finalize_terminal(self._aborted())
        manifest = self.store.manifest()
        self.assertLessEqual(
            manifest["exploration_seconds"], manifest["budget_declared_seconds"]
        )
        self.assertGreater(
            manifest["total_wall_clock_seconds"], manifest["budget_declared_seconds"]
        )
        self.assertEqual(
            manifest["total_wall_clock_seconds"] - manifest["budget_declared_seconds"],
            100.0,
        )


if __name__ == "__main__":
    unittest.main()
