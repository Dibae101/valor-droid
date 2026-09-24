"""Guards for JaCoCo readiness, reach recording and budget adherence.

Like test_v4_campaign_harness.py, every test here pins a failure that actually
happened and cost a measurement:

* The v4 doctrine arm ran three apps for a full hour each against
  ``bundles-final50``, which carries no ``jacoco-agent.properties``. The agent
  never opened a tcpserver, all 195 dump attempts reported "Socket closed
  unexpectedly", and ``mean_jacoco_probe_percent`` came back null. Nothing could
  have told the operator before the three hours were spent.
* The v4 campaign recorded no screen count at any level, even though
  ``summary.py`` already knows how to compute one, because the campaign never ran
  ``summary`` and ``run.json`` carries no reach field. Reach is the metric the
  three tools are measured on, so its absence made the comparison unanswerable
  from v4 artifacts.
* Trackbook explored for 595 seconds of its declared 3600 and was then read as
  the largest v3->v4 regression at -7.12 points. A truncated run's coverage is
  not a low score.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "app/tools/valordroid_jacoco_smoke.py"
REBUILD = REPO / "app/tools/prepare_jacoco_bundles.py"


def _load(path: Path, name: str):
    sys.path.insert(0, str(REPO / "app/src"))
    sys.path.insert(0, str(REPO / "app/tools/runner"))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_apk(path: Path, *, props: str | None = None, dex: bytes = b"DEX",
              manifest: bytes = b"<manifest/>") -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("classes.dex", dex)
        archive.writestr("AndroidManifest.xml", manifest)
        archive.writestr("META-INF/CERT.RSA", b"signature")
        if props is not None:
            archive.writestr("jacoco-agent.properties", props)
    return path


TCPSERVER_PROPS = "output=tcpserver\naddress=*\nport=6300\ndumponexit=false\n"


class JacocoPreflightTest(unittest.TestCase):
    """A run that cannot produce JaCoCo must say so before it starts."""

    def setUp(self) -> None:
        self.harness = _load(HARNESS, "v4_harness_preflight")
        self.tmp = Path(tempfile.mkdtemp())

    def _bundle(self, **kwargs) -> Path:
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        write_apk(root / "instrumented.apk", **kwargs)
        return root

    def test_a_bundle_without_agent_properties_is_refused(self):
        verdict = self.harness.inspect_jacoco_readiness(self._bundle())
        self.assertFalse(verdict["ready"])
        self.assertFalse(verdict["props_present"])
        # The reason has to name the fix, because the operator reading it is the
        # person who has to decide whether to start a 50-hour campaign.
        self.assertIn("jacoco-agent.properties", verdict["reason"])
        self.assertIn("prepare_jacoco_bundles.py", verdict["reason"])

    def test_file_output_is_refused_because_android_kills_the_process(self):
        verdict = self.harness.inspect_jacoco_readiness(
            self._bundle(props="output=file\ndestfile=jacoco.exec\n"))
        self.assertFalse(verdict["ready"])
        self.assertEqual(verdict["output"], "file")
        self.assertIn("tcpserver", verdict["reason"])

    def test_a_port_the_harness_does_not_forward_is_refused(self):
        verdict = self.harness.inspect_jacoco_readiness(
            self._bundle(props="output=tcpserver\nport=9999\n"))
        self.assertFalse(verdict["ready"])
        self.assertEqual(verdict["agent_port"], 9999)
        self.assertIn(str(self.harness.GUEST_PORT), verdict["reason"])

    def test_a_correctly_configured_bundle_is_ready(self):
        verdict = self.harness.inspect_jacoco_readiness(
            self._bundle(props=TCPSERVER_PROPS,
                         dex=b"DEX\x00Lorg/jacoco/agent/rt/RT;"))
        self.assertTrue(verdict["ready"], verdict["reason"])
        self.assertEqual(verdict["agent_port"], self.harness.GUEST_PORT)
        self.assertTrue(verdict["dex_jacoco_evidence"])
        self.assertIsNone(verdict["reason"])

    def test_properties_without_agent_classes_are_refused(self):
        # The hole this closes: a bundle whose config is correct but whose dex
        # was never instrumented passes every earlier check, and the run then
        # discovers at teardown -- an hour later -- that no tcpserver opened.
        verdict = self.harness.inspect_jacoco_readiness(
            self._bundle(props=TCPSERVER_PROPS))
        self.assertFalse(verdict["ready"])
        self.assertTrue(verdict["props_present"])
        self.assertFalse(verdict["dex_jacoco_evidence"])
        self.assertIn("tcpserver", verdict["reason"])

    def test_dex_evidence_matches_the_runner_heuristic(self):
        # reproduction/runner/run_tool.py gates live collection on the same
        # bytes test; the preflight must agree with it or the two disagree
        # about which bundles are collectable.
        verdict = self.harness.inspect_jacoco_readiness(
            self._bundle(props=TCPSERVER_PROPS, dex=b"\x00JACOCO-PROBE\x00"))
        self.assertTrue(verdict["ready"], verdict["reason"])

    def test_a_missing_bundle_reports_a_reason_rather_than_raising(self):
        verdict = self.harness.inspect_jacoco_readiness(self.tmp / "absent")
        self.assertFalse(verdict["ready"])
        self.assertIn("instrumented.apk", verdict["reason"])

    def test_an_unreadable_apk_reports_a_reason_rather_than_raising(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        (root / "instrumented.apk").write_bytes(b"not a zip at all")
        verdict = self.harness.inspect_jacoco_readiness(root)
        self.assertFalse(verdict["ready"])
        self.assertIsNotNone(verdict["reason"])


class ActivityReachRecordingTest(unittest.TestCase):
    """Reach must land in the run row, using the tool's own definition."""

    def setUp(self) -> None:
        self.harness = _load(HARNESS, "v4_harness_reach")
        self.tmp = Path(tempfile.mkdtemp())

    def _run_dir(self, declared, observations) -> Path:
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        (root / "route-discovery.json").write_text(json.dumps({
            "package_name": "com.example.app",
            "declared_activities": declared,
        }))
        with (root / "actions.jsonl").open("w") as ledger:
            for before, after in observations:
                ledger.write(json.dumps({"payload": {"attempt": {"outcome": {
                    "details": {"before_activity": before, "after_activity": after}
                }}}}) + "\n")
        return root

    def test_entered_counts_only_declared_activities_actually_seen(self):
        reach = self.harness.summarize_activity_reach(self._run_dir(
            [".Main", ".Settings", ".About", ".Never"],
            [("com.example.app/.Main", "com.example.app/.Settings"),
             ("com.example.app/.Settings", "com.example.app/.About")],
        ))
        self.assertEqual(reach["declared"], 4)
        self.assertEqual(reach["entered"], 3)
        self.assertAlmostEqual(reach["share_entered"], 0.75)
        self.assertIsNone(reach["issue"])

    def test_equivalent_activity_spellings_are_one_screen(self):
        # Android reports the same activity as pkg/.A, pkg/A and pkg/pkg.A.
        # Counting those as three screens would inflate reach silently.
        reach = self.harness.summarize_activity_reach(self._run_dir(
            [".Main"],
            [("com.example.app/.Main", "com.example.app/com.example.app.Main")],
        ))
        self.assertEqual(reach["declared"], 1)
        self.assertEqual(reach["entered"], 1)

    def test_a_foreign_screen_is_not_counted_as_reach(self):
        reach = self.harness.summarize_activity_reach(self._run_dir(
            [".Main"], [("com.android.settings/.Wifi", "com.android.settings/.Wifi")],
        ))
        self.assertEqual(reach["entered"], 0)
        self.assertEqual(reach["foreign_observations"], 2)

    def test_a_missing_ledger_reports_an_issue_instead_of_zero(self):
        # Zero-entered and nothing-recorded are different facts, and reporting
        # the second as the first is how a lost measurement becomes a datapoint.
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        (root / "route-discovery.json").write_text(json.dumps(
            {"package_name": "com.example.app", "declared_activities": [".Main"]}))
        reach = self.harness.summarize_activity_reach(root)
        self.assertIsNone(reach["entered"])
        self.assertEqual(reach["issue"], "actions_ledger_absent")

    def test_a_malformed_ledger_line_does_not_lose_the_whole_run(self):
        root = self._run_dir([".Main"], [("com.example.app/.Main", "com.example.app/.Main")])
        with (root / "actions.jsonl").open("a") as ledger:
            ledger.write("{not json at all\n")
        reach = self.harness.summarize_activity_reach(root)
        self.assertEqual(reach["entered"], 1)


class BudgetAdherenceTest(unittest.TestCase):
    """A run that ended early must be visible as short, not as low-coverage."""

    def setUp(self) -> None:
        self.harness = _load(HARNESS, "v4_harness_budget")
        self.tmp = Path(tempfile.mkdtemp())

    def _run_dir(self, exploration_seconds) -> Path:
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        payload = {"status": "finished"}
        if exploration_seconds is not None:
            payload["exploration_seconds"] = exploration_seconds
        (root / "run.json").write_text(json.dumps(payload))
        return root

    def test_a_full_budget_run_is_marked_complete(self):
        budget = self.harness.summarize_budget(self._run_dir(3602.5), 3600.0)
        self.assertTrue(budget["used_full_budget"])
        self.assertGreater(budget["share_spent"], 1.0)

    def test_the_trackbook_shortfall_is_flagged(self):
        # The real number from the v4 campaign: 594.82s of a declared 3600.
        budget = self.harness.summarize_budget(self._run_dir(594.82), 3600.0)
        self.assertFalse(budget["used_full_budget"])
        self.assertAlmostEqual(budget["share_spent"], 0.1652, places=3)

    def test_a_run_just_inside_tolerance_is_not_flagged(self):
        budget = self.harness.summarize_budget(self._run_dir(3430.0), 3600.0)
        self.assertTrue(budget["used_full_budget"])

    def test_a_missing_manifest_reports_an_issue(self):
        budget = self.harness.summarize_budget(self.tmp / "absent", 3600.0)
        self.assertIsNone(budget["used_full_budget"])
        self.assertEqual(budget["issue"], "run_manifest_absent")


class BundleRebuildSafetyTest(unittest.TestCase):
    """Adding the agent config must not be able to change the method universe."""

    def setUp(self) -> None:
        self.rebuild = _load(REBUILD, "prepare_jacoco_bundles")
        self.tmp = Path(tempfile.mkdtemp())

    def test_identical_dex_passes_and_reports_the_delta(self):
        source = write_apk(self.tmp / "source.apk")
        patched = write_apk(self.tmp / "patched.apk", props=TCPSERVER_PROPS)
        delta = self.rebuild.assert_code_unchanged(source, patched)
        self.assertTrue(delta["dex_identical"])
        self.assertEqual(delta["entries_added"], ["jacoco-agent.properties"])
        self.assertEqual(delta["unexpected_entry_changes"], [])

    def test_changed_dex_is_refused(self):
        # vd-dual's universe collapse (And-Bible 128 units against 6721) is what
        # happens when the measured APK stops matching the universe. A rebuild
        # that alters code must not be allowed to produce a bundle at all.
        source = write_apk(self.tmp / "s2.apk", dex=b"ORIGINAL")
        patched = write_apk(self.tmp / "p2.apk", dex=b"REWRITTEN", props=TCPSERVER_PROPS)
        with self.assertRaises(ValueError) as caught:
            self.rebuild.assert_code_unchanged(source, patched)
        self.assertIn("method universe", str(caught.exception))

    def test_an_apk_with_no_dex_is_refused(self):
        empty = self.tmp / "empty.apk"
        with zipfile.ZipFile(empty, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"<manifest/>")
        with self.assertRaises(ValueError):
            self.rebuild.assert_code_unchanged(empty, empty)

    def test_an_unexpected_added_entry_is_surfaced(self):
        source = write_apk(self.tmp / "s3.apk")
        patched = self.tmp / "p3.apk"
        write_apk(patched, props=TCPSERVER_PROPS)
        with zipfile.ZipFile(patched, "a") as archive:
            archive.writestr("assets/unexpected.bin", b"payload")
        delta = self.rebuild.assert_code_unchanged(source, patched)
        self.assertEqual(delta["unexpected_entry_changes"], ["assets/unexpected.bin"])

    def test_reissuing_the_proof_moves_only_the_apk_hash(self):
        source = self.tmp / "proof.json"
        original = {
            "instrumented_apk_sha256": "a" * 64,
            "unit_count": 1690,
            "unit_ids_sha256": "b" * 64,
            "backend": "androlog-logcat-method-v1",
            "package_name": "com.example.app",
        }
        source.write_text(json.dumps(original))
        destination = self.tmp / "proof-out.json"
        self.rebuild.reissue_proof(source, "c" * 64, destination)
        rewritten = json.loads(destination.read_text())
        self.assertEqual(rewritten["instrumented_apk_sha256"], "c" * 64)
        for field in ("unit_count", "unit_ids_sha256", "backend", "package_name"):
            self.assertEqual(rewritten[field], original[field])


class PackagePrefixResolutionTest(unittest.TestCase):
    """The JaCoCo denominator must match the three-tools pipeline's.

    ``run_tool.py`` passes the app's install package to
    ``code_coverage.collect``; the smoke test used to pass ``None``, which
    leaves ``summarise_for_package`` on its unfiltered fallback and counts
    library probes in the denominator. Same explorer, different metric --
    the comparison would have been wrong before the first run started.
    """

    def setUp(self) -> None:
        self.harness = _load(HARNESS, "v4_harness_prefix")
        self.tmp = Path(tempfile.mkdtemp())

    def _bundle(self, manifest: dict | None) -> Path:
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        if manifest is not None:
            (root / "prepared.json").write_text(json.dumps(manifest))
        return root

    def test_install_package_becomes_the_slash_prefix(self):
        bundle = self._bundle({"package_name": "com.example.app"})
        self.assertEqual(
            self.harness._prepared_package_prefix(bundle), "com/example/app")

    def test_a_missing_prepared_json_falls_back_to_detection(self):
        self.assertIsNone(
            self.harness._prepared_package_prefix(self._bundle(None)))

    def test_a_bundle_without_a_package_name_falls_back_to_detection(self):
        self.assertIsNone(
            self.harness._prepared_package_prefix(self._bundle({})))
        self.assertIsNone(
            self.harness._prepared_package_prefix(
                self._bundle({"package_name": ""})))

    def test_a_broken_prepared_json_falls_back_to_detection(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        (root / "prepared.json").write_text("{not json")
        self.assertIsNone(self.harness._prepared_package_prefix(root))


if __name__ == "__main__":
    unittest.main()
