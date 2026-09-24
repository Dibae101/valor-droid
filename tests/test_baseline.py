"""Matched-control comparison regressions.

Pinned finding:
- review 1 #12: campaign aggregation grouped only by prepared/universe checksum
  and could average runs produced under different controls.

`campaign-compare` is the other half of that: it must refuse to report a
difference between two campaigns unless they are genuinely comparable.

Section 5 of the gap audit adds the statistics that comparison is allowed to
report. The primary figure is the mean of an app's predeclared repetitions, then
the mean of per-app paired differences over the app intersection. The specific
error being guarded against is the audit's own: V2's 50-app mean minus V1's 49-app
mean was reported as a paired improvement, when the actual paired change on the
shared 49 apps was a different number.
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from valordroid.baseline import (
    BASELINE_SCHEMA_VERSION,
    DEFAULT_MINIMUM_REPETITIONS,
    DEFAULT_WALL_CLOCK_TOLERANCE_SECONDS,
    DESCRIPTIVE_ONLY,
    _distribution,
    _wall_clock_comparison,
    compare_campaigns,
    paired_app_summary,
    student_t_quantile,
)
from valordroid.fleet_plan import create_fleet_plan
from valordroid.fleet_runner import FleetRunner

from . import support


class BaselineComparisonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        support.suppress_environment_noise()
        cls._root = Path(tempfile.mkdtemp(prefix="valordroid-baseline."))
        cls.tools = support.fake_tools(cls._root / "tools")
        cls.prepared = support.build_prepared(cls._root / "work", cls.tools["aapt"])
        cls.first = cls._campaign("baseline")
        cls.second = cls._campaign("treatment")

    @classmethod
    def _campaign(cls, name: str) -> Path:
        work = cls._root / name
        work.mkdir(parents=True, exist_ok=True)
        config_path = support.attach_fleet_config(
            work,
            cls.prepared,
            cls.tools,
            lease_root=cls._root / "leases",
            jobs=1,
            campaign_id=name,
        )
        campaign = cls._root / f"campaign-{name}"
        create_fleet_plan(config_path, campaign)
        FleetRunner(campaign).run()
        return campaign

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._root, ignore_errors=True)

    def test_comparing_a_campaign_with_itself_is_refused(self) -> None:
        report = compare_campaigns(self.first, self.first)
        self.assertFalse(report["matched"])
        self.assertTrue(report["unmatched_reasons"])

    def test_campaigns_without_valid_finished_runs_are_refused(self) -> None:
        """No successful run means there is nothing to compare."""

        report = compare_campaigns(self.first, self.second)
        self.assertFalse(report["matched"])
        self.assertEqual(report["coverage_gain_vs_baseline"], "not_established")
        self.assertTrue(
            any(
                "no independently valid finished run" in reason
                for reason in report["unmatched_reasons"]
            ),
            report["unmatched_reasons"],
        )

    def test_report_never_claims_a_gain_when_unmatched(self) -> None:
        report = compare_campaigns(self.first, self.second)
        self.assertEqual(report["coverage_gain_vs_baseline"], "not_established")

    def test_invalid_campaign_directory_is_rejected(self) -> None:
        missing = self._root / "does-not-exist"
        with self.assertRaises(Exception):
            compare_campaigns(self.first, missing)

    def test_the_report_states_the_standard_it_was_held_to(self) -> None:
        report = compare_campaigns(self.first, self.second)
        self.assertEqual(report["schema_version"], BASELINE_SCHEMA_VERSION)
        self.assertEqual(report["minimum_repetitions"], DEFAULT_MINIMUM_REPETITIONS)
        self.assertEqual(
            report["wall_clock_tolerance_seconds"],
            DEFAULT_WALL_CLOCK_TOLERANCE_SECONDS,
        )

    def test_the_report_states_how_much_of_each_arm_completed(self) -> None:
        """A partially completed arm must not look complete."""

        report = compare_campaigns(self.first, self.second)
        for side in ("baseline", "treatment"):
            completion = report["completion"][side]
            self.assertEqual(completion["valid_cells"], 0)
            self.assertEqual(completion["invalid_cells"], completion["planned_cells"])
            self.assertEqual(completion["completion_rate"], 0.0)
            self.assertEqual(
                len(completion["invalid_cell_details"]), completion["planned_cells"]
            )

    def test_a_negative_wall_clock_tolerance_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            compare_campaigns(
                self.first, self.second, wall_clock_tolerance_seconds=-1.0
            )


class PairedStatisticTest(unittest.TestCase):
    """The paired summary, exercised directly on per-app repetition values."""

    @staticmethod
    def _values(count: int, value: float, repetitions: int = 3) -> dict[str, list[float]]:
        return {f"app{index:02d}": [value] * repetitions for index in range(count)}

    def test_the_mean_over_repetitions_comes_before_the_difference(self) -> None:
        summary = paired_app_summary(
            {"appone": [10.0, 20.0, 30.0]},
            {"appone": [21.0, 21.0, 21.0]},
        )
        self.assertEqual(summary["n_apps"], 1)
        self.assertEqual(summary["per_app_differences"][0]["baseline_mean"], 20.0)
        self.assertEqual(summary["per_app_differences"][0]["treatment_mean"], 21.0)
        self.assertEqual(summary["mean_difference_percentage_points"], 1.0)

    def test_fifty_baseline_apps_against_forty_nine_treatment_apps(self) -> None:
        """The audit's exact error: never subtract means over different app sets."""

        baseline = self._values(50, 10.0)
        treatment = self._values(49, 11.0)
        # The unpaired difference of whole-set means is not the paired difference.
        baseline_mean = sum(sum(v) / len(v) for v in baseline.values()) / 50
        treatment_mean = sum(sum(v) / len(v) for v in treatment.values()) / 49
        summary = paired_app_summary(baseline, treatment)
        self.assertEqual(summary["n_apps"], 49)
        self.assertEqual(
            [item["app_id"] for item in summary["excluded_apps"]], ["app49"]
        )
        self.assertEqual(
            summary["excluded_apps"][0]["reason"], "app_measured_in_only_one_arm"
        )
        self.assertEqual(summary["mean_difference_percentage_points"], 1.0)
        self.assertNotIn("app49", summary["app_ids"])
        self.assertAlmostEqual(treatment_mean - baseline_mean, 1.0)

    def test_the_excluded_app_never_contributes_to_either_side(self) -> None:
        baseline = {**self._values(2, 10.0), "lonely": [99.0, 99.0, 99.0]}
        summary = paired_app_summary(baseline, self._values(2, 12.0))
        self.assertEqual(summary["n_apps"], 2)
        self.assertEqual(summary["mean_difference_percentage_points"], 2.0)
        self.assertNotIn(
            99.0,
            [
                value
                for item in summary["per_app_differences"]
                for value in item["baseline_values"]
            ],
        )

    def test_unequal_repetition_counts_are_refused(self) -> None:
        summary = paired_app_summary(
            {"appone": [10.0, 10.0, 10.0]}, {"appone": [11.0, 11.0]}
        )
        self.assertEqual(summary["n_apps"], 0)
        self.assertEqual(
            summary["excluded_apps"][0]["reason"], "unequal_repetition_counts"
        )
        self.assertIsNone(summary["mean_difference_percentage_points"])

    def test_below_minimum_repetition_counts_are_refused(self) -> None:
        summary = paired_app_summary(
            {"appone": [10.0, 10.0]}, {"appone": [11.0, 11.0]}
        )
        self.assertEqual(summary["n_apps"], 0)
        self.assertEqual(
            summary["excluded_apps"][0]["reason"], "below_minimum_repetitions"
        )
        self.assertEqual(summary["excluded_apps"][0]["minimum_repetitions"], 3)

    def test_a_lower_declared_minimum_admits_fewer_repetitions(self) -> None:
        summary = paired_app_summary(
            {"appone": [10.0, 10.0]},
            {"appone": [11.0, 11.0]},
            minimum_repetitions=2,
        )
        self.assertEqual(summary["n_apps"], 1)
        self.assertEqual(summary["minimum_repetitions"], 2)

    def test_wins_losses_and_ties_are_counted_per_app(self) -> None:
        summary = paired_app_summary(
            {
                "gain": [10.0] * 3,
                "loss": [10.0] * 3,
                "tie": [10.0] * 3,
            },
            {
                "gain": [12.0] * 3,
                "loss": [8.0] * 3,
                "tie": [10.0] * 3,
            },
        )
        self.assertEqual((summary["wins"], summary["losses"], summary["ties"]), (1, 1, 1))
        self.assertEqual(summary["mean_difference_percentage_points"], 0.0)

    def test_the_paired_interval_respects_pairing_and_its_own_arithmetic(self) -> None:
        baseline = {f"app{index}": [10.0] * 3 for index in range(5)}
        treatment = {
            "app0": [11.0] * 3,
            "app1": [12.0] * 3,
            "app2": [9.0] * 3,
            "app3": [13.0] * 3,
            "app4": [10.0] * 3,
        }
        summary = paired_app_summary(baseline, treatment)
        differences = [1.0, 2.0, -1.0, 3.0, 0.0]
        mean = sum(differences) / len(differences)
        variance = sum((value - mean) ** 2 for value in differences) / (
            len(differences) - 1
        )
        stdev = math.sqrt(variance)
        critical = student_t_quantile(0.975, len(differences) - 1)
        half = critical * stdev / math.sqrt(len(differences))
        interval = summary["paired_confidence_interval"]
        self.assertAlmostEqual(summary["mean_difference_percentage_points"], mean)
        self.assertAlmostEqual(summary["difference_sample_stdev"], stdev)
        self.assertEqual(interval["degrees_of_freedom"], 4)
        self.assertAlmostEqual(interval["low"], mean - half)
        self.assertAlmostEqual(interval["high"], mean + half)
        self.assertEqual(summary["significance_claim"], "none")

    def test_one_app_has_no_interval_rather_than_a_zero_width_one(self) -> None:
        summary = paired_app_summary({"appone": [10.0] * 3}, {"appone": [11.0] * 3})
        self.assertEqual(summary["n_apps"], 1)
        self.assertIsNone(summary["paired_confidence_interval"])
        self.assertIsNone(summary["difference_sample_stdev"])

    def test_the_primary_statistic_is_named_and_is_not_a_best_of(self) -> None:
        summary = paired_app_summary({"appone": [10.0] * 3}, {"appone": [11.0] * 3})
        self.assertIn("mean of predeclared repetitions", summary["primary_statistic"])
        self.assertNotIn("max", summary["primary_statistic"])

    def test_an_invalid_minimum_repetition_count_is_refused(self) -> None:
        for value in (0, -1):
            with self.assertRaises(ValueError):
                paired_app_summary({}, {}, minimum_repetitions=value)


class DescriptiveLabellingTest(unittest.TestCase):
    def test_best_of_figures_are_labelled_descriptive_only(self) -> None:
        distribution = _distribution([1.0, 5.0, 3.0])
        self.assertEqual(distribution[DESCRIPTIVE_ONLY], ["minimum", "maximum"])
        self.assertEqual(distribution["primary_statistic"], "mean")
        self.assertEqual(distribution["maximum"], 5.0)
        self.assertEqual(distribution["mean"], 3.0)


class WallClockToleranceTest(unittest.TestCase):
    """Equal declared budgets do not make two arms comparable."""

    @staticmethod
    def _group(totals: list[float], declared: float | None = 3600.0) -> dict:
        return {
            "runs": [
                {
                    "total_wall_clock_seconds": value,
                    "budget_declared_seconds": declared,
                }
                for value in totals
            ]
        }

    def test_comparable_wall_clock_is_within_tolerance(self) -> None:
        result = _wall_clock_comparison(
            self._group([3500.0, 3550.0]),
            self._group([3520.0, 3560.0]),
            tolerance_seconds=300.0,
        )
        self.assertTrue(result["within_tolerance"])
        self.assertTrue(result["declared_budgets_match"])

    def test_matching_declared_budgets_do_not_excuse_unequal_spending(self) -> None:
        """A 25-minute arm and a 60-minute arm both declaring an hour is refused."""

        result = _wall_clock_comparison(
            self._group([1500.0, 1500.0]),
            self._group([3600.0, 3600.0]),
            tolerance_seconds=300.0,
        )
        self.assertFalse(result["within_tolerance"])
        self.assertTrue(result["declared_budgets_match"])
        self.assertEqual(result["difference_seconds"], 2100.0)

    def test_a_union_of_two_hours_is_not_an_hour(self) -> None:
        result = _wall_clock_comparison(
            self._group([3600.0]),
            self._group([7200.0]),
            tolerance_seconds=300.0,
        )
        self.assertFalse(result["within_tolerance"])
        self.assertEqual(result["difference_seconds"], 3600.0)

    def test_incomplete_accounting_is_refused_rather_than_assumed_equal(self) -> None:
        left = self._group([3600.0])
        left["runs"].append(
            {"total_wall_clock_seconds": None, "budget_declared_seconds": 3600.0}
        )
        result = _wall_clock_comparison(
            left, self._group([3600.0]), tolerance_seconds=300.0
        )
        self.assertFalse(result["within_tolerance"])
        self.assertEqual(result["runs_missing_accounting"], 1)
        self.assertIn("incomplete", result["reason"])


class MultiArtifactComparisonTest(unittest.TestCase):
    """A multi-app experiment has one artifact digest pair per app, not one.

    Every app is a different APK, so it has a different prepared-bundle and
    universe digest and therefore its own aggregation group. Pairing inside a
    single group and then treating a second group as a second experiment arm
    refuses the normal case: a 50-app campaign would produce fifty groups and no
    paired statistic at all. These tests drive the real `compare_campaigns` with
    two distinct prepared artifacts, which is the integration that direct tests
    of `paired_app_summary` cannot reach.
    """

    APPS = ("AppOne", "AppTwo")

    def setUp(self) -> None:
        self._root = Path(tempfile.mkdtemp(prefix="valordroid-multiartifact."))
        self.addCleanup(shutil.rmtree, self._root, True)
        self.baseline_root = self._campaign_dir("baseline", "execution-" + "a" * 32)
        self.treatment_root = self._campaign_dir("treatment", "execution-" + "b" * 32)
        self._summaries: dict[Path, dict] = {}
        # `compare_campaigns` reads its inputs through `summarize_campaign`; the
        # substitution is at that boundary only, so the comparison logic under
        # test is the production implementation.
        import valordroid.baseline as baseline_module

        self._original = baseline_module.summarize_campaign
        baseline_module.summarize_campaign = lambda root: self._summaries[Path(root)]
        self.addCleanup(
            setattr, baseline_module, "summarize_campaign", self._original
        )

    def _campaign_dir(self, name: str, execution_id: str) -> Path:
        root = self._root / name
        root.mkdir(parents=True)
        (root / "campaign.json").write_text(
            json.dumps({"execution_id": execution_id}), encoding="utf-8"
        )
        return root

    ARM = "shared"

    @staticmethod
    def _group(
        app_id: str,
        values: list[float],
        *,
        index: int,
        arm: str = "shared",
        cells: tuple[int, ...] | None = None,
        attempts: int = 1,
    ) -> dict:
        # Which planned cell each valid run answers. Defaults to the first N
        # repetitions, which is what a campaign that lost its later cells looks
        # like; `cells` names them explicitly when the identities matter.
        ordinals = tuple(range(1, len(values) + 1)) if cells is None else cells
        assert len(ordinals) == len(values)
        return {
            # Distinct digests per app, exactly as distinct APKs produce.
            "prepared_bundle_sha256": f"{index:064x}",
            "universe_sha256": f"{index + 100:064x}",
            "control_sha256": "c" * 64,
            "control_descriptor": {"run": {"configuration": {"arm": "shared"}}},
            "valid_finished_runs": len(values),
            "observed_coverage_percent": {
                "mean": sum(values) / len(values) if values else 0.0,
                "minimum": min(values) if values else 0.0,
                "maximum": max(values) if values else 0.0,
                "descriptive_only": ["minimum", "maximum"],
                "primary_statistic": "mean",
            },
            "per_app_observed_coverage_percent": {
                app_id: {
                    "repetitions": len(values),
                    "mean": sum(values) / len(values) if values else 0.0,
                    "values": sorted(values),
                }
            },
            "runs": [
                {
                    "job_id": f"{app_id}-r{ordinal}",
                    "run_id": f"{app_id}.r{ordinal}",
                    "arm": arm,
                    "app_id": app_id,
                    "repetition": ordinal,
                    "attempts_used": attempts,
                    "retried": attempts > 1,
                    "observed_coverage_percent": value,
                    "total_wall_clock_seconds": 3600.0,
                    "budget_declared_seconds": 3600.0,
                }
                for ordinal, value in zip(ordinals, values)
            ],
        }

    def _summary(
        self,
        campaign_id: str,
        per_app: dict[str, list[float]],
        *,
        planned: tuple[str, ...],
        repetitions: int = 3,
        valid_cells: dict[str, tuple[int, ...]] | None = None,
        attempts: dict[str, int] | None = None,
        planned_cells: list[dict] | None = None,
        arm: str | None = None,
    ) -> dict:
        arm = self.ARM if arm is None else arm
        groups = [
            self._group(
                app_id,
                values,
                index=index,
                arm=arm,
                cells=(valid_cells or {}).get(app_id),
                attempts=(attempts or {}).get(app_id, 1),
            )
            for index, (app_id, values) in enumerate(sorted(per_app.items()), 1)
            if values
        ]
        # The immutable plan's own cell identities, as `summarize_campaign`
        # emits them: one entry per planned (arm, app, repetition).
        cells = (
            [
                {
                    "job_id": f"{app_id}-r{ordinal}",
                    "arm": arm,
                    "app_id": app_id,
                    "repetition": ordinal,
                }
                for app_id in sorted(planned)
                for ordinal in range(1, repetitions + 1)
            ]
            if planned_cells is None
            else planned_cells
        )
        return {
            "campaign_id": campaign_id,
            "status": "finished",
            "counts": {"succeeded": sum(len(v) for v in per_app.values())},
            "assignment_version": "seeded-round-robin-v2",
            "assignment_seed": "seed",
            "declared_repetitions": repetitions,
            "planned_apps": sorted(planned),
            "planned_cells_by_app": {app: repetitions for app in planned},
            "planned_cells": cells,
            "valid_finished_runs": sum(len(v) for v in per_app.values()),
            "completion": {
                "planned_cells": repetitions * len(planned),
                "valid_cells": sum(len(v) for v in per_app.values()),
                "invalid_cells": repetitions * len(planned)
                - sum(len(v) for v in per_app.values()),
                "retried_cells": 0,
            },
            "aggregation_groups": groups,
            "coverage_gain_vs_baseline": "not_established",
        }

    def _compare(
        self,
        baseline_per_app: dict[str, list[float]],
        treatment_per_app: dict[str, list[float]],
        *,
        planned: tuple[str, ...] | None = None,
        minimum_repetitions: int = 3,
        repetitions: int = 3,
        baseline_kwargs: dict | None = None,
        treatment_kwargs: dict | None = None,
    ) -> dict:
        cohort = self.APPS if planned is None else planned
        self._summaries = {
            self.baseline_root: self._summary(
                "base",
                baseline_per_app,
                planned=cohort,
                repetitions=repetitions,
                **(baseline_kwargs or {}),
            ),
            self.treatment_root: self._summary(
                "treat",
                treatment_per_app,
                planned=cohort,
                repetitions=repetitions,
                **(treatment_kwargs or {}),
            ),
        }
        return compare_campaigns(
            self.baseline_root,
            self.treatment_root,
            minimum_repetitions=minimum_repetitions,
        )

    def test_two_apks_per_arm_produce_one_paired_summary(self) -> None:
        """The defect: two prepared artifacts read as two experiment arms."""

        report = self._compare(
            {"AppOne": [10.0, 11.0, 12.0], "AppTwo": [20.0, 21.0, 22.0]},
            {"AppOne": [13.0, 14.0, 15.0], "AppTwo": [26.0, 27.0, 28.0]},
        )
        self.assertEqual(report["unmatched_reasons"], [])
        self.assertTrue(report["matched"])
        self.assertIsNotNone(report["paired_summary"])
        paired = report["paired_summary"]
        self.assertEqual(paired["n_apps"], 2)
        self.assertEqual(paired["app_ids"], ["AppOne", "AppTwo"])
        # AppOne +3.0, AppTwo +6.0 -> mean of per-app differences is 4.5.
        self.assertAlmostEqual(paired["mean_difference_percentage_points"], 4.5)
        self.assertEqual(paired["wins"], 2)
        self.assertEqual(report["coverage_gain_vs_baseline"], "observed_difference_reported")

    def test_both_apks_are_still_compared_individually(self) -> None:
        report = self._compare(
            {"AppOne": [10.0, 11.0, 12.0], "AppTwo": [20.0, 21.0, 22.0]},
            {"AppOne": [13.0, 14.0, 15.0], "AppTwo": [26.0, 27.0, 28.0]},
        )
        self.assertEqual(len(report["comparisons"]), 2)
        for comparison in report["comparisons"]:
            self.assertTrue(comparison["matched"])
            self.assertEqual(comparison["unexpected_control_changes"], [])

    def test_the_paired_cohort_is_the_planned_cohort(self) -> None:
        report = self._compare(
            {"AppOne": [10.0, 11.0, 12.0], "AppTwo": [20.0, 21.0, 22.0]},
            {"AppOne": [13.0, 14.0, 15.0], "AppTwo": [26.0, 27.0, 28.0]},
        )
        cohort = report["fixed_cohort"]
        self.assertTrue(cohort["complete"])
        self.assertEqual(cohort["planned_apps"], 2)
        self.assertEqual(cohort["paired_apps"], 2)
        self.assertEqual(cohort["unpaired_apps"], [])
        self.assertEqual(report["paired_summary"]["cohort"], "fixed_planned_cohort")

    def test_an_app_that_failed_in_both_arms_is_named_not_dropped(self) -> None:
        """The second defect: a hole in the fixed dataset must stay visible.

        AppTwo's cells fail in both arms, so it appears in no successful
        aggregation group. A cohort inferred from those groups cannot see it, and
        the comparison then reads as a complete result over one app.
        """

        report = self._compare(
            {"AppOne": [10.0, 11.0, 12.0], "AppTwo": []},
            {"AppOne": [13.0, 14.0, 15.0], "AppTwo": []},
        )
        cohort = report["fixed_cohort"]
        self.assertFalse(cohort["complete"])
        self.assertEqual(cohort["planned_apps"], 2)
        self.assertEqual(cohort["paired_apps"], 1)
        self.assertEqual(
            [(item["app_id"], item["reason"]) for item in cohort["unpaired_apps"]],
            [("AppTwo", "no_valid_run_in_either_arm")],
        )
        # The available-app statistic still exists, explicitly as a secondary.
        self.assertEqual(
            report["paired_summary"]["cohort"], "available_apps_only_secondary"
        )
        self.assertEqual(report["paired_summary"]["n_apps"], 1)
        # And the headline claim is withheld.
        self.assertFalse(report["matched"])
        self.assertEqual(report["coverage_gain_vs_baseline"], "not_established")
        self.assertTrue(
            any("fixed cohort is incomplete" in r for r in report["unmatched_reasons"])
        )

    def test_missing_coverage_is_never_replaced_with_zero(self) -> None:
        report = self._compare(
            {"AppOne": [10.0, 11.0, 12.0], "AppTwo": []},
            {"AppOne": [13.0, 14.0, 15.0], "AppTwo": []},
        )
        paired = report["paired_summary"]
        self.assertNotIn(
            "AppTwo", [item["app_id"] for item in paired["per_app_differences"]]
        )
        self.assertAlmostEqual(paired["mean_difference_percentage_points"], 3.0)
        self.assertTrue(report["fixed_cohort"]["missing_coverage_is_never_zero"])

    def test_an_app_missing_from_one_arm_only_is_named(self) -> None:
        report = self._compare(
            {"AppOne": [10.0, 11.0, 12.0], "AppTwo": [20.0, 21.0, 22.0]},
            {"AppOne": [13.0, 14.0, 15.0], "AppTwo": []},
        )
        cohort = report["fixed_cohort"]
        self.assertFalse(cohort["complete"])
        reasons = {item["app_id"]: item["reason"] for item in cohort["unpaired_apps"]}
        self.assertEqual(reasons["AppTwo"], "no_valid_run_in_treatment")
        self.assertFalse(report["matched"])

    def test_unequal_repetitions_across_apks_are_still_refused(self) -> None:
        """The multi-artifact fix must not weaken the repetition rule."""

        report = self._compare(
            {"AppOne": [10.0, 11.0, 12.0], "AppTwo": [20.0, 21.0]},
            {"AppOne": [13.0, 14.0, 15.0], "AppTwo": [26.0, 27.0, 28.0]},
        )
        self.assertFalse(report["matched"])
        reasons = {
            item["app_id"]: item["reason"]
            for item in report["paired_summary"]["excluded_apps"]
        }
        self.assertEqual(reasons["AppTwo"], "unequal_repetition_counts")

    def test_a_below_minimum_cohort_is_refused_across_apks(self) -> None:
        report = self._compare(
            {"AppOne": [10.0], "AppTwo": [20.0]},
            {"AppOne": [13.0], "AppTwo": [26.0]},
        )
        self.assertFalse(report["matched"])
        self.assertEqual(report["paired_summary"]["n_apps"], 0)
        reasons = {
            item["reason"] for item in report["paired_summary"]["excluded_apps"]
        }
        self.assertEqual(reasons, {"below_minimum_repetitions"})

    # ---- planned repetition completeness -------------------------------------
    #
    # The defect: the comparator called an experiment complete whenever the two
    # arms lost the same number of repetitions. Four planned per arm with three
    # valid in each pairs cleanly, satisfies a minimum of three, and satisfies
    # neither plan. Equal observed counts are not completeness, so the plan has
    # to be consulted for every app -- including the apps the numerical pairing
    # already accepted, which is exactly the check that was being skipped.

    def _deficit(self, report: dict, app_id: str) -> dict:
        found = [
            item
            for item in report["planned_repetitions"]["deficient_apps"]
            if item["app_id"] == app_id
        ]
        self.assertEqual(len(found), 1, report["planned_repetitions"])
        return found[0]

    def test_three_valid_of_four_planned_in_both_arms_is_incomplete(self) -> None:
        """The reproduction. Equal losses in both arms are still losses."""

        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0]},
            {"AppA": [25.0, 25.0, 25.0]},
            planned=("AppA",),
            minimum_repetitions=3,
            repetitions=4,
        )
        self.assertFalse(report["matched"])
        self.assertEqual(report["coverage_gain_vs_baseline"], "not_established")
        self.assertFalse(report["fixed_cohort"]["complete"])

        deficit = self._deficit(report, "AppA")
        self.assertEqual(deficit["reason"], "incomplete_planned_repetitions")
        self.assertEqual(deficit["baseline_planned_repetitions"], 4)
        self.assertEqual(deficit["baseline_valid_repetitions"], 3)
        self.assertEqual(deficit["treatment_planned_repetitions"], 4)
        self.assertEqual(deficit["treatment_valid_repetitions"], 3)
        self.assertEqual(deficit["baseline_missing_repetitions"], [4])
        self.assertEqual(deficit["treatment_missing_repetitions"], [4])

        # The app is named in the cohort with the same numbers, and it is named
        # even though the pairing accepted it.
        self.assertEqual(report["fixed_cohort"]["paired_apps"], 1)
        named = report["fixed_cohort"]["unpaired_apps"]
        self.assertEqual([item["app_id"] for item in named], ["AppA"])
        self.assertEqual(named[0]["reason"], "incomplete_planned_repetitions")
        self.assertEqual(named[0]["baseline_planned_repetitions"], 4)
        self.assertEqual(named[0]["treatment_valid_repetitions"], 3)

    def test_the_incomplete_difference_stays_visible_but_secondary(self) -> None:
        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0]},
            {"AppA": [25.0, 25.0, 25.0]},
            planned=("AppA",),
            repetitions=4,
        )
        paired = report["paired_summary"]
        # Still computed, and still honest about what it is.
        self.assertAlmostEqual(paired["mean_difference_percentage_points"], 5.0)
        self.assertEqual(paired["cohort"], "available_observations_only_secondary")
        self.assertIn("planned repetitions are missing", paired["cohort_note"])
        self.assertIn("AppA", paired["cohort_note"])
        # And never presented as the predeclared result.
        self.assertNotEqual(paired["cohort"], "fixed_planned_cohort")
        self.assertEqual(report["coverage_gain_vs_baseline"], "not_established")

    def test_the_missing_cell_is_never_replaced_with_zero(self) -> None:
        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0]},
            {"AppA": [25.0, 25.0, 25.0]},
            planned=("AppA",),
            repetitions=4,
        )
        difference = report["paired_summary"]["per_app_differences"][0]
        self.assertEqual(difference["baseline_values"], [20.0, 20.0, 20.0])
        self.assertEqual(difference["baseline_mean"], 20.0)
        self.assertEqual(difference["repetitions"], 3)
        self.assertTrue(report["fixed_cohort"]["missing_coverage_is_never_zero"])

    def test_four_valid_of_four_planned_is_complete(self) -> None:
        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0, 21.0]},
            {"AppA": [25.0, 25.0, 25.0, 26.0]},
            planned=("AppA",),
            minimum_repetitions=3,
            repetitions=4,
        )
        self.assertEqual(report["unmatched_reasons"], [])
        self.assertTrue(report["matched"])
        self.assertTrue(report["fixed_cohort"]["complete"])
        self.assertEqual(report["planned_repetitions"]["deficient_apps"], [])
        self.assertEqual(report["paired_summary"]["cohort"], "fixed_planned_cohort")
        self.assertEqual(
            report["coverage_gain_vs_baseline"], "observed_difference_reported"
        )
        self.assertEqual(
            report["fixed_cohort"]["planned_repetitions_by_app"]["AppA"],
            {
                "baseline_planned": 4,
                "baseline_valid": 4,
                "treatment_planned": 4,
                "treatment_valid": 4,
            },
        )

    def test_a_deficit_in_one_arm_only_is_identified(self) -> None:
        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0, 21.0]},
            {"AppA": [25.0, 25.0, 25.0]},
            planned=("AppA",),
            minimum_repetitions=3,
            repetitions=4,
        )
        self.assertFalse(report["matched"])
        self.assertFalse(report["fixed_cohort"]["complete"])
        deficit = self._deficit(report, "AppA")
        self.assertEqual(deficit["baseline_valid_repetitions"], 4)
        self.assertEqual(deficit["baseline_missing_repetitions"], [])
        self.assertEqual(deficit["treatment_valid_repetitions"], 3)
        self.assertEqual(deficit["treatment_missing_repetitions"], [4])

    def test_the_requested_minimum_cannot_waive_the_plan(self) -> None:
        """Five planned, four valid in each arm, minimum four.

        The minimum is satisfied and the plan is not. A minimum is a floor the
        analysis adds, never a ceiling that releases the predeclaration.
        """

        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0, 21.0]},
            {"AppA": [25.0, 25.0, 25.0, 26.0]},
            planned=("AppA",),
            minimum_repetitions=4,
            repetitions=5,
        )
        self.assertFalse(report["matched"])
        self.assertFalse(report["fixed_cohort"]["complete"])
        self.assertEqual(report["coverage_gain_vs_baseline"], "not_established")
        deficit = self._deficit(report, "AppA")
        self.assertEqual(deficit["baseline_planned_repetitions"], 5)
        self.assertEqual(deficit["baseline_valid_repetitions"], 4)
        self.assertEqual(deficit["baseline_missing_repetitions"], [5])
        self.assertEqual(deficit["treatment_missing_repetitions"], [5])

    def test_a_satisfied_plan_below_the_requested_minimum_is_still_refused(self) -> None:
        """Three planned and three valid, but the caller asked for four."""

        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0]},
            {"AppA": [25.0, 25.0, 25.0]},
            planned=("AppA",),
            minimum_repetitions=4,
            repetitions=3,
        )
        self.assertFalse(report["matched"])
        self.assertEqual(report["coverage_gain_vs_baseline"], "not_established")
        # The plan itself is complete, so this is the minimum rule and not a
        # planned-repetition deficit.
        self.assertEqual(report["planned_repetitions"]["deficient_apps"], [])
        self.assertEqual(
            {item["reason"] for item in report["paired_summary"]["excluded_apps"]},
            {"below_minimum_repetitions"},
        )
        self.assertFalse(report["fixed_cohort"]["complete"])

    def test_one_deficient_app_makes_the_whole_cohort_incomplete(self) -> None:
        report = self._compare(
            {"AppOne": [10.0, 11.0, 12.0, 13.0], "AppTwo": [20.0, 21.0, 22.0]},
            {"AppOne": [14.0, 15.0, 16.0, 17.0], "AppTwo": [26.0, 27.0, 28.0]},
            minimum_repetitions=3,
            repetitions=4,
        )
        self.assertFalse(report["matched"])
        self.assertFalse(report["fixed_cohort"]["complete"])
        self.assertEqual(
            [item["app_id"] for item in report["planned_repetitions"]["deficient_apps"]],
            ["AppTwo"],
        )
        named = {item["app_id"]: item for item in report["fixed_cohort"]["unpaired_apps"]}
        self.assertEqual(list(named), ["AppTwo"])
        self.assertEqual(named["AppTwo"]["treatment_missing_repetitions"], [4])
        # AppOne completed its plan and is still counted as paired.
        self.assertIn("AppOne", report["fixed_cohort"]["paired_app_ids"])

    def test_two_apks_with_every_planned_repetition_are_complete(self) -> None:
        report = self._compare(
            {"AppOne": [10.0, 11.0, 12.0, 13.0], "AppTwo": [20.0, 21.0, 22.0, 23.0]},
            {"AppOne": [14.0, 15.0, 16.0, 17.0], "AppTwo": [26.0, 27.0, 28.0, 29.0]},
            minimum_repetitions=3,
            repetitions=4,
        )
        self.assertEqual(report["unmatched_reasons"], [])
        self.assertTrue(report["matched"])
        self.assertTrue(report["fixed_cohort"]["complete"])
        self.assertEqual(report["paired_summary"]["n_apps"], 2)
        self.assertEqual(report["paired_summary"]["cohort"], "fixed_planned_cohort")

    def test_an_app_absent_from_both_arms_keeps_its_own_refusal(self) -> None:
        """The missing-app finding is stronger and must not be relabelled."""

        report = self._compare(
            {"AppOne": [10.0, 11.0, 12.0, 13.0], "AppTwo": []},
            {"AppOne": [14.0, 15.0, 16.0, 17.0], "AppTwo": []},
            repetitions=4,
        )
        self.assertFalse(report["matched"])
        self.assertEqual(
            [(item["app_id"], item["reason"]) for item in report["fixed_cohort"]["unpaired_apps"]],
            [("AppTwo", "no_valid_run_in_either_arm")],
        )
        # Not also reported as a repetition deficit, which would say the same
        # thing twice and bury the stronger finding.
        self.assertEqual(report["planned_repetitions"]["deficient_apps"], [])

    def test_a_retry_does_not_manufacture_a_repetition(self) -> None:
        """A cell that needed two attempts is one repetition, not two.

        Three valid cells of four planned, where one of them only succeeded on
        its second attempt. Counting attempts would report four and call the
        experiment complete.
        """

        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0]},
            {"AppA": [25.0, 25.0, 25.0]},
            planned=("AppA",),
            repetitions=4,
            baseline_kwargs={"attempts": {"AppA": 2}},
        )
        self.assertFalse(report["fixed_cohort"]["complete"])
        deficit = self._deficit(report, "AppA")
        self.assertEqual(deficit["baseline_valid_repetitions"], 3)
        self.assertEqual(deficit["baseline_missing_repetitions"], [4])
        # The retry history is still on the evidence.
        runs = self._summaries[self.baseline_root]["aggregation_groups"][0]["runs"]
        self.assertTrue(all(item["attempts_used"] == 2 for item in runs))
        self.assertTrue(all(item["retried"] for item in runs))

    def test_a_cell_that_succeeded_on_retry_still_completes_the_plan(self) -> None:
        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0, 21.0]},
            {"AppA": [25.0, 25.0, 25.0, 26.0]},
            planned=("AppA",),
            repetitions=4,
            baseline_kwargs={"attempts": {"AppA": 3}},
        )
        self.assertEqual(report["unmatched_reasons"], [])
        self.assertTrue(report["fixed_cohort"]["complete"])
        self.assertTrue(report["matched"])

    def test_the_surviving_cells_are_the_ones_the_plan_named(self) -> None:
        """A late cell present and an early one missing is still incomplete."""

        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0]},
            {"AppA": [25.0, 25.0, 25.0]},
            planned=("AppA",),
            repetitions=4,
            # Repetition 2 never produced a valid result; 4 did.
            baseline_kwargs={"valid_cells": {"AppA": (1, 3, 4)}},
            treatment_kwargs={"valid_cells": {"AppA": (1, 3, 4)}},
        )
        deficit = self._deficit(report, "AppA")
        self.assertEqual(deficit["baseline_missing_repetitions"], [2])
        self.assertEqual(deficit["treatment_missing_repetitions"], [2])
        self.assertFalse(report["fixed_cohort"]["complete"])

    def test_a_cross_arm_total_is_not_one_arms_requirement(self) -> None:
        """Two arms in the plan, four repetitions each, eight jobs for the app.

        `planned_cells_by_app` is that total. Judging the compared arm against
        eight would report a complete arm as incomplete, so the requirement has
        to be read per arm.
        """

        both_arms = [
            {
                "job_id": f"AppA-{arm}-r{ordinal}",
                "arm": arm,
                "app_id": "AppA",
                "repetition": ordinal,
            }
            for arm in ("shared", "other")
            for ordinal in range(1, 5)
        ]
        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0, 21.0]},
            {"AppA": [25.0, 25.0, 25.0, 26.0]},
            planned=("AppA",),
            repetitions=4,
            baseline_kwargs={"planned_cells": both_arms},
            treatment_kwargs={"planned_cells": both_arms},
        )
        self.assertEqual(
            self._summaries[self.baseline_root]["planned_cells_by_app"]["AppA"], 4
        )
        self.assertEqual(len(both_arms), 8)
        self.assertEqual(report["unmatched_reasons"], [])
        self.assertTrue(report["fixed_cohort"]["complete"])
        deficit = report["planned_repetitions"]["by_app"]["AppA"]
        self.assertEqual(deficit["baseline_planned_repetitions"], 4)

    def test_absent_plan_metadata_cannot_establish_completeness(self) -> None:
        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0]},
            {"AppA": [25.0, 25.0, 25.0]},
            planned=("AppA",),
            repetitions=3,
            baseline_kwargs={"planned_cells": []},
            treatment_kwargs={"planned_cells": []},
        )
        self.assertFalse(report["matched"])
        self.assertFalse(report["fixed_cohort"]["complete"])
        self.assertFalse(report["fixed_cohort"]["planned_repetitions_established"])
        self.assertEqual(report["coverage_gain_vs_baseline"], "not_established")
        self.assertTrue(
            any("no per-arm planned cells" in r for r in report["unmatched_reasons"]),
            report["unmatched_reasons"],
        )

    def test_a_plan_that_names_a_different_arm_cannot_establish_completeness(self) -> None:
        elsewhere = [
            {
                "job_id": f"AppA-other-r{ordinal}",
                "arm": "other",
                "app_id": "AppA",
                "repetition": ordinal,
            }
            for ordinal in range(1, 4)
        ]
        report = self._compare(
            {"AppA": [20.0, 20.0, 20.0]},
            {"AppA": [25.0, 25.0, 25.0]},
            planned=("AppA",),
            repetitions=3,
            baseline_kwargs={"planned_cells": elsewhere},
            treatment_kwargs={"planned_cells": elsewhere},
        )
        self.assertFalse(report["matched"])
        self.assertFalse(report["fixed_cohort"]["complete"])
        self.assertTrue(
            any("declares no cells for arm" in r for r in report["unmatched_reasons"]),
            report["unmatched_reasons"],
        )

    def test_runs_without_an_arm_cannot_establish_completeness(self) -> None:
        """Historical summaries predate the per-arm cell identities."""

        self._summaries = {
            self.baseline_root: self._summary(
                "base", {"AppA": [20.0, 20.0, 20.0]}, planned=("AppA",)
            ),
            self.treatment_root: self._summary(
                "treat", {"AppA": [25.0, 25.0, 25.0]}, planned=("AppA",)
            ),
        }
        for summary in self._summaries.values():
            summary.pop("planned_cells")
            for group in summary["aggregation_groups"]:
                for run in group["runs"]:
                    run.pop("arm")
                    run.pop("repetition")
        report = compare_campaigns(self.baseline_root, self.treatment_root)
        self.assertFalse(report["matched"])
        self.assertFalse(report["fixed_cohort"]["complete"])
        self.assertEqual(report["coverage_gain_vs_baseline"], "not_established")

    def test_compared_runs_spanning_two_arms_are_refused(self) -> None:
        """No single planned repetition count applies to a mixed group."""

        self._summaries = {
            self.baseline_root: self._summary(
                "base", {"AppA": [20.0, 20.0, 20.0, 21.0]}, planned=("AppA",),
                repetitions=4,
            ),
            self.treatment_root: self._summary(
                "treat", {"AppA": [25.0, 25.0, 25.0, 26.0]}, planned=("AppA",),
                repetitions=4,
            ),
        }
        runs = self._summaries[self.baseline_root]["aggregation_groups"][0]["runs"]
        runs[-1]["arm"] = "other"
        report = compare_campaigns(self.baseline_root, self.treatment_root)
        self.assertFalse(report["matched"])
        self.assertFalse(report["fixed_cohort"]["complete"])
        self.assertTrue(
            any("span arms" in r for r in report["unmatched_reasons"]),
            report["unmatched_reasons"],
        )

    def test_plans_declaring_different_cohorts_are_not_complete(self) -> None:
        self._summaries = {
            self.baseline_root: self._summary(
                "base",
                {"AppOne": [10.0, 11.0, 12.0]},
                planned=("AppOne",),
            ),
            self.treatment_root: self._summary(
                "treat",
                {"AppOne": [13.0, 14.0, 15.0]},
                planned=("AppOne", "AppTwo"),
            ),
        }
        report = compare_campaigns(self.baseline_root, self.treatment_root)
        cohort = report["fixed_cohort"]
        self.assertFalse(cohort["plans_declare_the_same_cohort"])
        self.assertFalse(cohort["complete"])
        self.assertFalse(report["matched"])


if __name__ == "__main__":
    unittest.main()
