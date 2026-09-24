"""Regression coverage for identity-bound, canonical screen-reach evidence."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tests import fake_device
from tests.test_activity_activation import _Attempt, _ReachRunner
from tests.test_android_loop import _core, _prepared, _runtime
from valordroid.activity_reach import canonical_activities_from_details
from valordroid.android.manifest import RouteDiscovery
from valordroid.ledger import HashChainLedger, ZERO_HASH
from valordroid.prepared import verify_prepared_bundle
from valordroid.runner import AndroidRunner
from valordroid.store import RunStore
from valordroid.validator import validate_run

_TOOLS = Path(__file__).resolve().parents[1] / "app" / "tools"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "_screen_reach_under_test", _TOOLS / "screen_reach.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _different_digest(value: str) -> str:
    replacement = "1" if value[0] != "1" else "2"
    return replacement + value[1:]


class ScreenReachEvidenceTest(unittest.TestCase):
    """Use one real finished run, then damage exact evidence copies per test."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_tool()
        cls._class_temporary = tempfile.TemporaryDirectory()
        cls.class_root = Path(cls._class_temporary.name)
        cls.tools = fake_device.install_fake_device(cls.class_root / "tools")
        cls._old_device_state = os.environ.get("FAKE_DEVICE_STATE")
        os.environ["FAKE_DEVICE_STATE"] = cls.tools["state"]

        bundle_path = _prepared(cls.class_root, cls.tools)
        bundle = verify_prepared_bundle(bundle_path, aapt=cls.tools["aapt"])
        package = fake_device.PACKAGE
        main = f"{package}/{package}.MainActivity"
        detail = f"{package}/{package}.DetailActivity"
        deep = f"{package}/{package}.DeepActivity"
        internal = f"{package}/{package}.InternalActivity"
        discovery = RouteDiscovery.create(
            package_name=package,
            apk_sha256=bundle.manifest.instrumented_apk_sha256,
            declared_activities=tuple(sorted((main, detail, deep, internal))),
            launch_candidates=(main,),
            exported_components=tuple(sorted((detail, deep))),
            forced_components=(internal,),
            deep_links=("fakeapp://deep",),
            deep_link_owners={"fakeapp://deep": deep},
            declared_schemes=("fakeapp",),
            permission_guarded_components=(),
            disabled_components=(),
            host_substitutions=(),
            truncated=False,
        )
        runtime = _runtime(
            route_discovery_sha256=discovery.discovery_sha256,
            deep_links=list(discovery.deep_links),
            exported_components=list(discovery.exported_components),
            forced_components=[],
        )
        core = _core()
        store = RunStore.create(
            cls.class_root / "valid-run",
            bundle.universe,
            run_id="screen-reach-fixture",
            configuration=core.to_dict(),
            runtime_configuration=runtime.to_dict(),
            prepared_bundle_sha256=bundle.manifest.bundle_sha256,
            prepared_manifest=bundle.manifest.to_dict(),
        )
        store.route_discovery_path.write_text(
            json.dumps(discovery.to_dict(), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result = AndroidRunner(
            store,
            bundle,
            runtime,
            core,
            adb_executable=cls.tools["adb"],
        ).run()
        if result.status != "finished":
            raise AssertionError(f"fixture run did not finish: {result.stop_reason}")
        validation = validate_run(store.root)
        if not validation.ok:
            raise AssertionError(
                [issue.__dict__ for issue in validation.issues]
            )
        cls.valid_run = store.root

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._old_device_state is None:
            os.environ.pop("FAKE_DEVICE_STATE", None)
        else:
            os.environ["FAKE_DEVICE_STATE"] = cls._old_device_state
        cls._class_temporary.cleanup()

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.work = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.run = self._copy_run("run")

    def _copy_run(self, name: str) -> Path:
        target = self.work / name
        shutil.copytree(self.valid_run, target)
        return target

    def _rewrite_activity_details(self, run: Path, mutate) -> None:
        """Rewrite canonical/action projections together and rebind their heads."""

        heads: dict[str, dict[str, object]] = {}
        for ledger_name, filename in (
            ("transactions", "transactions.jsonl"),
            ("actions", "actions.jsonl"),
        ):
            path = run / filename
            values = [json.loads(line) for line in path.read_text().splitlines()]
            previous = ZERO_HASH
            for sequence, entry in enumerate(values, 1):
                payload = entry["payload"]
                if ledger_name == "actions":
                    mutate(payload["attempt"]["outcome"]["details"])
                elif payload["record_type"] == "action_transaction":
                    mutate(payload["action_attempt"]["outcome"]["details"])
                entry["sequence"] = sequence
                entry["previous_hash"] = previous
                entry["entry_hash"] = HashChainLedger._entry_hash(
                    sequence, previous, payload
                )
                previous = entry["entry_hash"]
            path.write_text(
                "".join(
                    json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
                    for value in values
                ),
                encoding="utf-8",
            )
            heads[ledger_name] = {
                "count": len(values),
                "terminal_hash": previous,
            }
        manifest_path = run / "run.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["evidence_heads"].update(heads)
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        # Activity spellings do not change frontier statistics, but rewriting the
        # canonical WAL changes its terminal hash. Keep the derived snapshot
        # bound to that new head so this fixture remains an independently valid
        # run and screen-reach—not the run validator—owns the incompleteness
        # rejection exercised below.
        frontier_path = run / "frontier.json"
        frontier = json.loads(frontier_path.read_text())
        frontier["applied_transaction_head"] = heads["transactions"][
            "terminal_hash"
        ]
        frontier_path.write_text(
            json.dumps(frontier, sort_keys=True) + "\n", encoding="utf-8"
        )
        validation = validate_run(run)
        self.assertTrue(
            validation.ok, [issue.__dict__ for issue in validation.issues]
        )

    def _selection_row(self, run: Path | None = None) -> dict[str, object]:
        selected = self.run if run is None else run
        manifest = json.loads((selected / "run.json").read_text())
        discovery = json.loads((selected / "route-discovery.json").read_text())
        actions = manifest["evidence_heads"]["actions"]
        return {
            "app": "Fixture App",
            "run_path": str(selected),
            "run_id": manifest["run_id"],
            "prepared_bundle_sha256": manifest["prepared_bundle_sha256"],
            "universe_sha256": manifest["universe_sha256"],
            "route_discovery_sha256": discovery["discovery_sha256"],
            "actions_count": actions["count"],
            "actions_terminal_hash": actions["terminal_hash"],
            "status": "finished",
            "validation_ok": True,
            "observed_covered_units": manifest["covered_units"],
            "total_units": manifest["total_units"],
            "observed_coverage_percent": manifest["observed_coverage_percent"],
            "arm": "fixture",
            "wave": "test",
        }

    def _load_rows(self, rows, *, strict: bool = True):
        path = self.work / "selection.json"
        path.write_text(json.dumps({"selections": rows}), encoding="utf-8")
        return self.module.load_selections(path, strict=strict)

    def _report(self, row: dict[str, object] | None = None):
        selection = self._selection_row() if row is None else row
        return self.module.build_report(self._load_rows([selection]))

    def test_a_short_form_alias_is_canonicalised_and_its_spelling_kept(self) -> None:
        """The alias rule itself, exercised where the expansion happens.

        A device can report `pkg/.Name` for what the manifest declares as
        `pkg/pkg.Name`. Counting those as two screens would inflate reach, so the
        observation must canonicalise while still recording what was seen.
        """

        package = fake_device.PACKAGE
        observations = self.module.activity_observations_from_details(
            package,
            {
                "before_activity": f"{package}/.MainActivity",
                "after_activity": f"{package}/{package}.MainActivity",
            },
        )
        canonical = {item.canonical for item in observations}
        self.assertEqual(canonical, {f"{package}/{package}.MainActivity"})
        self.assertEqual(
            {item.raw for item in observations},
            {f"{package}/.MainActivity", f"{package}/{package}.MainActivity"},
            "the spelling actually observed must be retained alongside the canonical form",
        )
        self.assertTrue(all(item.issue is None for item in observations))

    def test_aliases_are_canonical_and_only_declared_intersection_is_counted(self) -> None:
        report = self._report()
        self.assertEqual(report["included_row_count"], 1)
        row = report["rows"][0]
        package = fake_device.PACKAGE
        self.assertIn(
            f"{package}/{package}.MainActivity", row["observed_app_activities"]
        )
        # Retained evidence carries activity components already expanded, so a
        # short `pkg/.Name` spelling cannot reach the report through this path.
        # Every spelling the report sees is therefore fully qualified, and the
        # alias handling is asserted directly below against the function that
        # performs it.
        raw = {item["raw"] for item in row["observed_activity_spellings"]}
        self.assertIn(f"{package}/{package}.MainActivity", raw)
        self.assertTrue(
            all("/." not in spelling for spelling in raw),
            f"unexpected short-form spelling in retained evidence: {sorted(raw)}",
        )
        self.assertEqual(
            set(row["entered_declared_activities"]),
            set(row["declared_activities"]) & set(row["observed_app_activities"]),
        )
        self.assertEqual(
            row["entered_declared_activity_count"],
            len(row["entered_declared_activities"]),
        )
        self.assertNotIn(
            f"{package}/{package}.LeafActivity",
            row["entered_declared_activities"],
        )
        if f"{package}/{package}.LeafActivity" in row["observed_app_activities"]:
            self.assertIn(
                f"{package}/{package}.LeafActivity",
                row["observed_undeclared_activities"],
            )
        declared, helper_package = self.module.declared_and_package(self.run)
        self.assertEqual(helper_package, package)
        self.assertEqual(set(declared), set(row["declared_activities"]))
        self.assertEqual(
            self.module.entered(self.run, package),
            set(row["observed_app_activities"]),
        )

    def test_selection_run_identity_mismatch_rejects_the_row(self) -> None:
        selected = self._selection_row()
        selected["run_id"] = "another-execution"
        row = self._report(selected)["rows"][0]
        self.assertEqual(row["result"], "rejected")
        self.assertIn(
            "selection_run_id_mismatch",
            {issue["code"] for issue in row["issues"]},
        )

    def test_every_selected_hash_is_cross_checked(self) -> None:
        fields = (
            "prepared_bundle_sha256",
            "universe_sha256",
            "route_discovery_sha256",
            "actions_terminal_hash",
        )
        for field in fields:
            with self.subTest(field=field):
                selected = self._selection_row()
                selected[field] = _different_digest(str(selected[field]))
                row = self._report(selected)["rows"][0]
                self.assertEqual(row["result"], "rejected")
                self.assertIn(
                    f"selection_{field}_mismatch",
                    {issue["code"] for issue in row["issues"]},
                )

    def test_coverage_fields_are_not_joined_from_another_selection(self) -> None:
        selected = self._selection_row()
        selected["observed_coverage_percent"] = (
            float(selected["observed_coverage_percent"]) + 1.0
        )
        row = self._report(selected)["rows"][0]
        self.assertEqual(row["result"], "rejected")
        self.assertIn(
            "selection_observed_coverage_percent_mismatch",
            {issue["code"] for issue in row["issues"]},
        )

    def test_missing_identity_is_rejected_by_default(self) -> None:
        selected = self._selection_row()
        selected.pop("run_id")
        with self.assertRaises(self.module.ScreenReachError) as caught:
            self._load_rows([selected])
        self.assertEqual(caught.exception.code, "selection_identity_missing")
        # Compatibility mode may derive omitted attestations, but it still uses
        # the exact selected path and full run validator.
        selections = self._load_rows([selected], strict=False)
        with self.assertRaises(self.module.ScreenReachError) as caught:
            self.module.build_report(selections)
        self.assertEqual(caught.exception.code, "selection_identity_missing")
        report = self.module.build_report(selections, strict=False)
        self.assertEqual(report["included_row_count"], 1)

    def test_duplicate_and_ambiguous_selected_rows_are_rejected(self) -> None:
        first = self._selection_row()
        duplicate = dict(first)
        duplicate["run_path"] = str(self._copy_run("other-path"))
        with self.assertRaises(self.module.ScreenReachError) as caught:
            self._load_rows([first, duplicate])
        self.assertEqual(caught.exception.code, "duplicate_selected_app")

        ambiguous = dict(first)
        ambiguous["run"] = str(self.work / "different-run")
        with self.assertRaises(self.module.ScreenReachError) as caught:
            self._load_rows([ambiguous])
        self.assertEqual(caught.exception.code, "selection_ambiguous")

    def test_missing_route_prepared_or_action_evidence_is_not_zero_reach(self) -> None:
        for filename in (
            "route-discovery.json",
            "prepared.json",
            "actions.jsonl",
        ):
            with self.subTest(filename=filename):
                damaged = self._copy_run(f"missing-{filename}")
                selected = self._selection_row(damaged)
                (damaged / filename).unlink()
                report = self.module.build_report(self._load_rows([selected]))
                row = report["rows"][0]
                self.assertEqual(row["result"], "rejected")
                self.assertEqual(report["included_row_count"], 0)
                self.assertTrue(
                    any(issue["severity"] == "error" for issue in row["issues"])
                )
                self.assertNotIn("activity_reach_fraction", row)

    def test_corrupt_and_incompatible_route_or_prepared_evidence_is_rejected(self) -> None:
        cases = (
            ("route-discovery.json", "run_validation_route_discovery"),
            ("prepared.json", "run_validation_prepared_binding"),
        )
        for filename, expected_code in cases:
            with self.subTest(filename=filename):
                damaged = self._copy_run(f"corrupt-{filename}")
                selected = self._selection_row(damaged)
                (damaged / filename).write_text("{not-json", encoding="utf-8")
                selections = self._load_rows([selected])
                report = self.module.build_report(selections)
                row = report["rows"][0]
                self.assertEqual(row["result"], "rejected")
                self.assertIn(
                    expected_code, {issue["code"] for issue in row["issues"]}
                )
                self.assertNotIn("activity_reach_fraction", row)
                output = self.work / f"{filename}.report.json"
                self.assertEqual(
                    self.module.main(
                        [
                            "--selection",
                            str(self.work / "selection.json"),
                            "--output",
                            str(output),
                        ]
                    ),
                    1,
                )

        incompatible = self._copy_run("incompatible-route")
        selected = self._selection_row(incompatible)
        original = self.module.route_discovery_from_mapping(
            json.loads((incompatible / "route-discovery.json").read_text())
        )
        replacement = RouteDiscovery.create(
            package_name="other.package",
            apk_sha256=original.apk_sha256,
            declared_activities=original.declared_activities,
            launch_candidates=original.launch_candidates,
            exported_components=original.exported_components,
            forced_components=original.forced_components,
            deep_links=original.deep_links,
            deep_link_owners=original.deep_link_owners,
            declared_schemes=original.declared_schemes,
            permission_guarded_components=original.permission_guarded_components,
            disabled_components=original.disabled_components,
            host_substitutions=original.host_substitutions,
            truncated=original.truncated,
        )
        (incompatible / "route-discovery.json").write_text(
            json.dumps(replacement.to_dict(), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        row = self.module.build_report(self._load_rows([selected]))["rows"][0]
        self.assertEqual(row["result"], "rejected")
        self.assertIn(
            "run_validation_route_discovery",
            {issue["code"] for issue in row["issues"]},
        )
        self.assertNotIn("activity_reach_fraction", row)

    def test_missing_or_partial_activity_fields_are_not_numeric_zero(self) -> None:
        for mode in ("missing", "partial"):
            with self.subTest(mode=mode):
                damaged = self._copy_run(f"activity-{mode}")

                def mutate(details):
                    details.pop("after_activity", None)
                    if mode == "missing":
                        details.pop("before_activity", None)
                        details.pop("activity", None)

                self._rewrite_activity_details(damaged, mutate)
                selected = self._selection_row(damaged)
                row = self.module.build_report(
                    self._load_rows([selected])
                )["rows"][0]
                self.assertEqual(row["result"], "rejected")
                self.assertIn(
                    "activity_observation_incomplete",
                    {issue["code"] for issue in row["issues"]},
                )
                if mode == "missing":
                    self.assertIn(
                        "activity_observations_unavailable",
                        {issue["code"] for issue in row["issues"]},
                    )
                self.assertNotIn("activity_reach_fraction", row)

    def test_torn_and_hash_corrupt_action_ledgers_are_rejected(self) -> None:
        torn = self._copy_run("torn")
        torn_selection = self._selection_row(torn)
        with (torn / "actions.jsonl").open("a", encoding="utf-8") as stream:
            stream.write('{"sequence":999')
        row = self.module.build_report(
            self._load_rows([torn_selection])
        )["rows"][0]
        self.assertEqual(row["result"], "rejected")
        self.assertTrue(
            any("ledger_actions" in issue["code"] for issue in row["issues"])
        )

        corrupt = self._copy_run("hash-corrupt")
        corrupt_selection = self._selection_row(corrupt)
        lines = (corrupt / "actions.jsonl").read_text().splitlines()
        entry = json.loads(lines[0])
        entry["payload"]["attempt"]["outcome"]["details"][
            "before_activity"
        ] = f"{fake_device.PACKAGE}/.ForgedActivity"
        lines[0] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        (corrupt / "actions.jsonl").write_text("\n".join(lines) + "\n")
        row = self.module.build_report(
            self._load_rows([corrupt_selection])
        )["rows"][0]
        self.assertEqual(row["result"], "rejected")
        self.assertTrue(
            any("ledger_actions" in issue["code"] for issue in row["issues"])
        )

    def test_action_head_change_after_validation_is_rejected(self) -> None:
        selected = self._selection_row()
        real_validate = self.module.validate_run
        changed = False

        def validating_then_change(path):
            nonlocal changed
            result = real_validate(path)
            if not changed:
                changed = True
                actions_path = Path(path) / "actions.jsonl"
                values = [
                    json.loads(line) for line in actions_path.read_text().splitlines()
                ]
                values[0]["payload"]["attempt"]["outcome"]["details"][
                    "before_activity"
                ] = f"{fake_device.PACKAGE}/.ChangedAfterValidation"
                previous = ZERO_HASH
                for sequence, entry in enumerate(values, 1):
                    entry["sequence"] = sequence
                    entry["previous_hash"] = previous
                    entry["entry_hash"] = HashChainLedger._entry_hash(
                        sequence, previous, entry["payload"]
                    )
                    previous = entry["entry_hash"]
                actions_path.write_text(
                    "".join(
                        json.dumps(value, sort_keys=True, separators=(",", ":"))
                        + "\n"
                        for value in values
                    ),
                    encoding="utf-8",
                )
            return result

        self.module.validate_run = validating_then_change
        self.addCleanup(setattr, self.module, "validate_run", real_validate)
        row = self.module.build_report(self._load_rows([selected]))["rows"][0]
        self.assertEqual(row["result"], "rejected")
        self.assertIn(
            "actions_evidence_changed",
            {issue["code"] for issue in row["issues"]},
        )
        self.assertNotIn("activity_reach_fraction", row)

    def test_nonfinished_or_invalid_run_evidence_is_rejected(self) -> None:
        selected = self._selection_row()
        manifest_path = self.run / "run.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["status"] = "running"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        row = self.module.build_report(self._load_rows([selected]))["rows"][0]
        self.assertEqual(row["result"], "rejected")
        self.assertTrue(
            any(issue["severity"] == "error" for issue in row["issues"])
        )

    def test_undefined_correlation_is_null_not_zero(self) -> None:
        self.assertIsNone(self.module.correlation([0.1, 0.2], [10.0, 20.0]))
        self.assertIsNone(
            self.module.correlation([0.5, 0.5, 0.5], [10.0, 20.0, 30.0])
        )
        report = self._report()
        self.assertEqual(report["correlation"]["status"], "undefined")
        self.assertIsNone(report["correlation"]["coefficient"])
        self.assertEqual(report["correlation"]["reason"], "insufficient_pairs")

    def test_live_and_offline_use_identical_alias_and_legacy_key_semantics(self) -> None:
        details = (
            {"before_activity": "app/.Same", "after_activity": "app/Same"},
            {"activity": "app/app.Same"},
            {"after_activity": "android/.Foreign"},
        )
        live = _ReachRunner([_Attempt(details=item) for item in details])
        offline = frozenset(
            component
            for item in details
            for component in canonical_activities_from_details("app", item)
        )
        self.assertEqual(live._entered_activities(), offline)
        self.assertEqual(offline, frozenset({"app/app.Same"}))

    def test_explicit_cli_paths_and_deterministic_json_need_no_host_default(self) -> None:
        output = self.work / "reports" / "reach.json"
        exit_code = self.module.main(
            ["--run", f"Fixture App={self.run}", "--output", str(output)]
        )
        self.assertEqual(exit_code, 0)
        first = output.read_text()
        document = json.loads(first)
        self.assertTrue(document["strict"])
        self.assertTrue(document["rows"][0]["selection"]["identity_bound"])
        self.assertEqual(
            document["rows"][0]["selection"]["source"],
            "command_line_run_path_identity_snapshot",
        )
        self.assertEqual(
            document["rows"][0]["run_identity"]["run_path"], str(self.run)
        )
        self.assertEqual(
            document["rows"][0]["observed_coverage_percent"],
            json.loads((self.run / "run.json").read_text())[
                "observed_coverage_percent"
            ],
        )
        self.assertEqual(
            self.module.main(
                ["--run", f"Fixture App={self.run}", "--output", str(output)]
            ),
            0,
        )
        self.assertEqual(output.read_text(), first)


if __name__ == "__main__":
    unittest.main()
