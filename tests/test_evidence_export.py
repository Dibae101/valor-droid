"""Deterministic evidence export and the section 8 historical reproduction.

The audit could recompute its numbers only because it fetched every published
file by hand and rederived each figure. Two things make that repeatable: an export
whose bytes are a function of the evidence alone, and a verifier that implements
section 8's checks rather than trusting a report column.

The historical fixtures under `reproduction/results` are the real published V1 and
V2 exports. When they are not present the historical tests skip rather than
inventing evidence, and the ledger absence those exports genuinely have is
reported as an external blocker rather than silently tolerated.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from valordroid.baseline import (
    HISTORICAL_SCHEMA_VERSION,
    compare_historical_campaigns,
    git_blob_sha1,
    load_historical_campaign,
    verify_historical,
    verify_historical_campaign,
)
from valordroid.campaign import (
    EXPORT_MANIFEST_NAME,
    EXPORT_METADATA_NAME,
    EXPORT_SCHEMA_VERSION,
    export_evidence,
    verify_exported_evidence,
)
from valordroid.fleet_plan import create_fleet_plan
from valordroid.fleet_runner import FleetRunner

from . import support

_RESULTS = Path(__file__).resolve().parents[1] / "reproduction" / "results"
_V1 = _RESULTS / "valordroid-50app-60m-v1"
_V2 = _RESULTS / "valordroid-50app-60m-v2"


class DeterministicExportTest(unittest.TestCase):
    """The same evidence must publish the same bytes, every time."""

    @classmethod
    def setUpClass(cls) -> None:
        support.suppress_environment_noise()
        cls._root = Path(tempfile.mkdtemp(prefix="valordroid-export."))
        tools = support.fake_tools(cls._root / "tools")
        prepared = support.build_prepared(cls._root / "work", tools["aapt"])
        config_path = support.attach_fleet_config(
            cls._root / "work",
            prepared,
            tools,
            lease_root=cls._root / "leases",
            jobs=1,
            campaign_id="exportable",
        )
        cls.campaign = cls._root / "campaign"
        create_fleet_plan(config_path, cls.campaign)
        FleetRunner(cls.campaign).run()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self) -> None:
        self._work = Path(tempfile.mkdtemp(prefix="valordroid-export-case."))
        self.addCleanup(shutil.rmtree, self._work, True)

    def _files(self, export: Path) -> dict[str, bytes]:
        return {
            path.relative_to(export).as_posix(): path.read_bytes()
            for path in sorted(export.rglob("*"))
            if path.is_file()
        }

    def test_two_exports_of_one_campaign_are_byte_identical(self) -> None:
        first = self._work / "first"
        second = self._work / "second"
        export_evidence(self.campaign, first)
        export_evidence(self.campaign, second)
        self.assertEqual(self._files(first), self._files(second))

    def test_gzip_members_carry_no_timestamp_or_name(self) -> None:
        """mtime and stored filename are the two bytes that break reproducibility."""

        export = self._work / "export"
        export_evidence(self.campaign, export)
        members = sorted((export / "files").rglob("*.gz"))
        self.assertTrue(members)
        for member in members:
            header = member.read_bytes()[:10]
            self.assertEqual(header[:2], b"\x1f\x8b")
            self.assertEqual(header[4:8], b"\x00\x00\x00\x00", member.name)
            self.assertEqual(header[3] & 0x08, 0, f"{member.name} stores a filename")

    def test_every_published_file_round_trips(self) -> None:
        export = self._work / "export"
        report = export_evidence(self.campaign, export)
        metadata = json.loads((export / EXPORT_METADATA_NAME).read_text())
        self.assertEqual(metadata["schema_version"], EXPORT_SCHEMA_VERSION)
        self.assertEqual(len(metadata["files"]), report["file_count"])
        for entry in metadata["files"]:
            published = export / entry["published_path"]
            self.assertTrue(published.is_file(), entry["published_path"])
            data = gzip.decompress(published.read_bytes())
            self.assertEqual(len(data), entry["source_bytes"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["source_sha256"])
            self.assertEqual(
                data, (self.campaign / entry["source_path"]).read_bytes()
            )

    def test_the_manifest_covers_every_published_file_including_metadata(self) -> None:
        export = self._work / "export"
        export_evidence(self.campaign, export)
        listed = {
            line.split("  ", 1)[1]
            for line in (export / EXPORT_MANIFEST_NAME)
            .read_text()
            .splitlines()
        }
        present = {
            path.relative_to(export).as_posix()
            for path in export.rglob("*")
            if path.is_file()
        } - {EXPORT_MANIFEST_NAME}
        self.assertEqual(listed, present)
        self.assertIn(EXPORT_METADATA_NAME, listed)
        self.assertTrue(verify_exported_evidence(export)["ok"])

    def test_a_tampered_published_file_fails_verification(self) -> None:
        export = self._work / "export"
        export_evidence(self.campaign, export)
        target = next((export / "files").rglob("*.gz"))
        target.write_bytes(target.read_bytes() + b"\x00")
        result = verify_exported_evidence(export)
        self.assertFalse(result["ok"])
        self.assertTrue(result["mismatched"])

    def test_a_removed_published_file_fails_verification(self) -> None:
        export = self._work / "export"
        export_evidence(self.campaign, export)
        next((export / "files").rglob("*.gz")).unlink()
        result = verify_exported_evidence(export)
        self.assertFalse(result["ok"])
        self.assertTrue(result["missing"])

    def test_re_exporting_identical_evidence_is_accepted(self) -> None:
        export = self._work / "export"
        first = export_evidence(self.campaign, export)
        second = export_evidence(self.campaign, export)
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])

    def test_overwriting_differing_content_is_refused(self) -> None:
        export = self._work / "export"
        export_evidence(self.campaign, export)
        target = next((export / "files").rglob("*.gz"))
        target.write_bytes(b"different bytes entirely")
        with self.assertRaises(FileExistsError) as caught:
            export_evidence(self.campaign, export)
        self.assertIn("differs", str(caught.exception))

    def test_an_unpublished_stray_file_is_refused(self) -> None:
        export = self._work / "export"
        export_evidence(self.campaign, export)
        (export / "files" / "smuggled.gz").write_bytes(b"smuggled")
        with self.assertRaises(FileExistsError):
            export_evidence(self.campaign, export)

    def test_the_export_records_ledger_heads_and_plan_digests(self) -> None:
        export = self._work / "export"
        report = export_evidence(self.campaign, export)
        heads = report["ledger_heads"]
        self.assertIn("campaign_events", heads)
        self.assertEqual(
            set(heads["campaign_events"]),
            {"count", "terminal_hash", "file_sha256", "bytes", "index_sha256"},
        )
        plan = json.loads((self.campaign / "campaign-plan.json").read_text())
        self.assertEqual(report["digests"]["plan"]["plan_sha256"], plan["plan_sha256"])
        self.assertEqual(
            report["digests"]["plan"]["assignment_version"], plan["assignment_version"]
        )
        manifest = json.loads((self.campaign / "campaign.json").read_text())
        self.assertEqual(
            report["digests"]["campaign"]["campaign_sha256"], manifest["campaign_sha256"]
        )

    def test_host_lock_state_is_not_published(self) -> None:
        export = self._work / "export"
        report = export_evidence(self.campaign, export)
        metadata = json.loads((export / EXPORT_METADATA_NAME).read_text())
        published = {entry["source_path"] for entry in metadata["files"]}
        self.assertNotIn(".campaign.lock", published)
        self.assertTrue(published, report)

    def test_an_empty_directory_is_refused(self) -> None:
        empty = self._work / "empty"
        empty.mkdir()
        with self.assertRaises(ValueError):
            export_evidence(empty, self._work / "out")

    def test_a_missing_source_is_refused(self) -> None:
        with self.assertRaises(FileNotFoundError):
            export_evidence(self._work / "absent", self._work / "out")


class GitBlobIdentityTest(unittest.TestCase):
    def test_the_blob_id_matches_git_hash_object(self) -> None:
        """Known Git object id for the empty blob and for a one-line file."""

        self.assertEqual(
            git_blob_sha1(b""), "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
        )
        self.assertEqual(
            git_blob_sha1(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a"
        )


@unittest.skipUnless(
    _V1.is_dir() and _V2.is_dir(),
    "published historical campaign exports are not present in this checkout",
)
class HistoricalVerificationTest(unittest.TestCase):
    """Section 8's checks, recomputed against the real published exports.

    The expected values are the audit's own published figures. If the arithmetic
    here drifts, either the implementation or the audit is wrong, and the test says
    which numbers disagree.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = compare_historical_campaigns(_V1, _V2)

    def test_identity_matching_is_a_bijection_over_run_identities(self) -> None:
        for side, expected in (("baseline", 97), ("treatment", 111)):
            summary = self.report[side]
            self.assertEqual(summary["run_records"], expected)
            self.assertEqual(summary["run_manifests"], expected)
            self.assertEqual(summary["matched_records"], expected)
        self.assertEqual(self.report["mismatches"], [])
        self.assertTrue(self.report["ok"])

    def test_a_row_whose_asserted_field_differs_is_reported(self) -> None:
        """The equality assertions must actually be able to fail."""

        work = Path(tempfile.mkdtemp(prefix="valordroid-historical."))
        self.addCleanup(shutil.rmtree, work, True)
        copy = work / "campaign"
        shutil.copytree(_V1, copy)
        runs_path = copy / "analysis" / "runs.json"
        rows = json.loads(runs_path.read_text())
        rows[0]["covered_units"] = int(rows[0]["covered_units"]) + 1
        runs_path.write_text(json.dumps(rows), encoding="utf-8")
        report = verify_historical_campaign(copy)
        self.assertFalse(report["ok"])
        kinds = {item["kind"] for item in report["mismatches"]}
        self.assertIn("asserted_field_inequality", kinds)

    def test_a_row_without_a_matching_manifest_is_reported(self) -> None:
        work = Path(tempfile.mkdtemp(prefix="valordroid-historical-id."))
        self.addCleanup(shutil.rmtree, work, True)
        copy = work / "campaign"
        shutil.copytree(_V1, copy)
        runs_path = copy / "analysis" / "runs.json"
        rows = json.loads(runs_path.read_text())
        # A near-miss name must not be matched fuzzily onto a real manifest.
        rows[0]["app"] = str(rows[0]["app"]).lower() + "-x"
        runs_path.write_text(json.dumps(rows), encoding="utf-8")
        report = verify_historical_campaign(copy)
        self.assertFalse(report["ok"])
        kinds = {item["kind"] for item in report["mismatches"]}
        self.assertIn("row_without_manifest", kinds)
        self.assertIn("manifest_without_row", kinds)

    def test_aborted_and_running_records_are_counted_separately(self) -> None:
        self.assertEqual(self.report["baseline"]["aborted_records"], 6)
        self.assertEqual(self.report["baseline"]["running_records"], 8)
        self.assertEqual(self.report["baseline"]["valid_coverage_records"], 83)
        self.assertEqual(self.report["treatment"]["aborted_records"], 14)
        self.assertEqual(self.report["treatment"]["running_records"], 0)
        self.assertEqual(self.report["treatment"]["valid_coverage_records"], 97)

    def test_the_published_macro_and_pooled_figures_are_reproduced(self) -> None:
        baseline = self.report["baseline"]["aggregate"]
        treatment = self.report["treatment"]["aggregate"]
        self.assertEqual(baseline["apps"], 49)
        self.assertEqual(treatment["apps"], 50)
        self.assertAlmostEqual(baseline["macro_mean_observed_percent"], 28.0922, places=4)
        self.assertAlmostEqual(baseline["median_observed_percent"], 27.1245, places=4)
        self.assertEqual(baseline["pooled_covered_units"], 108464)
        self.assertEqual(baseline["pooled_total_units"], 450868)
        self.assertAlmostEqual(baseline["pooled_percent"], 24.0567, places=4)
        self.assertAlmostEqual(treatment["macro_mean_observed_percent"], 28.6393, places=4)
        self.assertAlmostEqual(treatment["median_observed_percent"], 27.0153, places=4)
        self.assertEqual(treatment["pooled_covered_units"], 111237)
        self.assertEqual(treatment["pooled_total_units"], 456567)
        self.assertAlmostEqual(treatment["pooled_percent"], 24.3638, places=4)

    def test_best_of_aggregates_are_labelled_descriptive_only(self) -> None:
        for side in ("baseline", "treatment"):
            self.assertEqual(
                self.report[side]["aggregate"]["figure_class"], "descriptive_only"
            )

    def test_the_route_decomposition_is_reproduced(self) -> None:
        """The additional intent/forced association is fractions of a point."""

        treatment = self.report["treatment"]["aggregate"]
        self.assertAlmostEqual(treatment["mean_additional_route_pp"], 0.2459, places=4)
        self.assertAlmostEqual(treatment["mean_outside_route_pp"], 11.9163, places=4)
        self.assertAlmostEqual(
            self.report["baseline"]["aggregate"]["mean_additional_route_pp"],
            0.6197,
            places=4,
        )

    def test_each_selected_row_satisfies_the_five_formulas(self) -> None:
        for side in ("baseline", "treatment"):
            for row in self.report[side]["per_app"]:
                total = row["total_units"]
                self.assertAlmostEqual(
                    row["observed_pct"], 100.0 * row["covered_units"] / total
                )
                self.assertAlmostEqual(
                    row["additional_route_pp"],
                    100.0
                    * (
                        row["all_route_covered_units"]
                        - row["primary_gui_deep_link_units"]
                    )
                    / total,
                )
                self.assertAlmostEqual(
                    row["outside_route_pp"],
                    100.0
                    * (row["covered_units"] - row["all_route_covered_units"])
                    / total,
                )
                self.assertGreaterEqual(row["additional_route_pp"], 0.0)
                self.assertGreaterEqual(row["outside_route_pp"], 0.0)

    def test_selection_ties_that_change_the_route_counts_are_reported(self) -> None:
        """The maximum is not always a unique row, and that must be visible."""

        ambiguous = [
            item
            for item in self.report["baseline"]["selection_ties"]
            if item["affects_route_decomposition"]
        ]
        self.assertTrue(
            ambiguous,
            "V1 contains apps whose maximum is reached by two runs with different "
            "route attribution",
        )
        for item in ambiguous:
            self.assertGreater(len(item["tied_runs"]), 1)
            self.assertIn(item["selected_run"], item["tied_runs"])

    def test_the_paired_change_uses_only_the_app_intersection(self) -> None:
        intersection = self.report["intersection"]
        self.assertEqual(intersection["n_apps"], 49)
        self.assertEqual(intersection["baseline_apps"], 49)
        self.assertEqual(intersection["treatment_apps"], 50)
        self.assertAlmostEqual(
            intersection["treatment_mean_on_intersection"], 28.8040, places=4
        )
        self.assertAlmostEqual(
            intersection["mean_paired_difference_percentage_points"], 0.7118, places=4
        )
        self.assertEqual(intersection["gains"], 22)
        self.assertEqual(intersection["losses"], 26)
        self.assertEqual(intersection["ties"], 1)

    def test_the_forty_nine_versus_fifty_app_mean_is_explicitly_refused(self) -> None:
        """Subtracting a 49-app mean from a 50-app mean is not an improvement."""

        intersection = self.report["intersection"]
        self.assertTrue(intersection["unpaired_mean_difference_refused"])
        self.assertEqual(
            [item["app"] for item in intersection["excluded_apps"]],
            ["Vinyl-Music-Player"],
        )
        unpaired = (
            self.report["treatment"]["aggregate"]["macro_mean_observed_percent"]
            - self.report["baseline"]["aggregate"]["macro_mean_observed_percent"]
        )
        self.assertNotAlmostEqual(
            unpaired,
            intersection["mean_paired_difference_percentage_points"],
            places=3,
        )

    def test_every_paired_app_has_equal_hashes_and_denominators(self) -> None:
        for pair in self.report["intersection"]["per_app_differences"]:
            self.assertEqual(len(pair["prepared_bundle_sha256"]), 64)
            self.assertEqual(len(pair["universe_sha256"]), 64)
            self.assertGreater(pair["total_units"], 0)
        self.assertEqual(
            [
                item
                for item in self.report["intersection"]["excluded_apps"]
                if item["reason"] == "unequal_prepared_universe_hash_or_denominator"
            ],
            [],
        )

    def test_an_app_whose_denominator_changed_is_excluded_not_averaged(self) -> None:
        work = Path(tempfile.mkdtemp(prefix="valordroid-historical-denom."))
        self.addCleanup(shutil.rmtree, work, True)
        copy = work / "v2"
        shutil.copytree(_V2, copy)
        # Change one app's universe on both sides consistently, so the campaign is
        # internally consistent but no longer measures the same denominator as V1.
        target = next(
            path
            for path in sorted(copy.glob("manifests/*/*.json"))
            if json.loads(path.read_text())["run_id"].startswith("Chess.")
        )
        manifest = json.loads(target.read_text())
        manifest["universe_sha256"] = "f" * 64
        target.write_text(json.dumps(manifest), encoding="utf-8")
        rows_path = copy / "analysis" / "runs.json"
        rows = json.loads(rows_path.read_text())
        for row in rows:
            if f"{row['app']}.{row['arm']}" == manifest["run_id"] and row["wave"] == target.parent.name:
                row["universe_sha256"] = "f" * 64
        rows_path.write_text(json.dumps(rows), encoding="utf-8")
        report = compare_historical_campaigns(_V1, copy)
        excluded = {
            item["app"]: item["reason"] for item in report["intersection"]["excluded_apps"]
        }
        self.assertEqual(
            excluded.get("Chess"), "unequal_prepared_universe_hash_or_denominator"
        )
        self.assertFalse(report["ok"])
        self.assertNotIn(
            "Chess",
            [item["app"] for item in report["intersection"]["per_app_differences"]],
        )

    def test_valid_cells_are_counted_separately_from_run_attempts(self) -> None:
        treatment = self.report["treatment"]["cells"]
        self.assertEqual(treatment["run_attempts"], 111)
        self.assertEqual(treatment["attempted_cells"], 100)
        self.assertEqual(treatment["valid_cells"], 97)
        self.assertEqual(
            treatment["cells_without_a_valid_run"],
            ["RedReader/gemma", "Ultrasonic/gemini", "WordPress/gemma"],
        )

    def test_carried_forward_analysis_is_detected_by_blob_identity(self) -> None:
        carried = self.report["carried_forward_analysis"]
        self.assertTrue(carried["both_present"])
        self.assertTrue(
            carried["identical_blob"],
            "V2 republished V1's blocker diagnosis; that must be detectable",
        )

    def test_absent_historical_ledgers_are_reported_as_an_external_blocker(self) -> None:
        blockers = self.report["external_blockers"]
        self.assertEqual(len(blockers), 2)
        for blocker in blockers:
            self.assertEqual(blocker["kind"], "historical_ledgers_absent")
            self.assertTrue(blocker["external_blocker"])

    def test_requiring_ledgers_fails_honestly(self) -> None:
        report = verify_historical(_V1, _V2, require_ledgers=True)
        self.assertFalse(report["ok"])
        self.assertTrue(report["require_ledgers"])
        self.assertTrue(report["external_blockers"])

    def test_a_single_campaign_report_is_self_contained(self) -> None:
        report = verify_historical(_V1)
        self.assertEqual(report["schema_version"], HISTORICAL_SCHEMA_VERSION)
        self.assertNotIn("_selected_rows", report)
        self.assertTrue(report["ok"])
        self.assertEqual(report["aggregate"]["apps"], 49)

    def test_the_loader_names_its_identity_rule(self) -> None:
        campaign = load_historical_campaign(_V1)
        self.assertIn("run_id", campaign["identity_rule"])
        self.assertEqual(campaign["retained_ledger_files"], [])

    def test_a_campaign_without_a_run_index_is_refused(self) -> None:
        work = Path(tempfile.mkdtemp(prefix="valordroid-historical-empty."))
        self.addCleanup(shutil.rmtree, work, True)
        (work / "analysis").mkdir(parents=True)
        with self.assertRaises(FileNotFoundError):
            load_historical_campaign(work)


if __name__ == "__main__":
    unittest.main()
