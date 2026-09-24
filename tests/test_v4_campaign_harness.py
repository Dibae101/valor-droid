"""Regression guards for the v4 campaign harness and its fixtures.

Each test here corresponds to a failure that actually happened and cost a
measurement, not to a hypothetical:

* Money-Manager-Ex aborted mid-campaign with "setup fixture is missing or a
  symlink" because the campaign profile directory was assembled from the profile
  JSON files without the ``fixtures/`` directory they resolve against.
* And-Bible reported 0.00% coverage over 227 dispatched actions while JaCoCo
  independently recorded 1520 probes executing, and that zero was averaged into
  the campaign mean as though it were a coverage result.
* Fifteen runtime and core fields had drifted from the v3 baseline the campaign is
  measured against, including timeouts 2-4x tighter than v3's and an
  ``association_settle_seconds`` of 2.0 against v3's 0.3.

The point of these is that none of the three announced itself. They were all found
by comparing against v3 by hand, so they are pinned here instead.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from valordroid.setup_profile import SetupProfile

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "app/tools/valordroid_jacoco_smoke.py"
PROFILES = REPO / "reproduction/fixtures/v4-jacoco-smoke/campaign-profiles"
FIXTURES = REPO / "reproduction/fixtures/v4-jacoco-smoke"


def load_harness():
    spec = importlib.util.spec_from_file_location("v4_harness", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(PROFILES.is_dir(), "campaign profiles are not present")
class CampaignSetupProfileFixtures(unittest.TestCase):
    """Every campaign profile must resolve before the campaign is launched.

    Resolution is what reads the pushed fixtures off disk and checksums them, so a
    profile that cannot resolve aborts its app. Doing this as a test means the whole
    set is checked in seconds rather than one app discovering it an hour into a wave.
    """

    def test_every_campaign_profile_resolves(self) -> None:
        environment = dict(os.environ)
        environment.setdefault("VALORDROID_ULTRASONIC_USER", "valordroid")
        password_file = Path.home() / ".vd-ultrasonic-pw"
        if password_file.is_file():
            environment.setdefault(
                "VALORDROID_ULTRASONIC_PASSWORD",
                password_file.read_text(encoding="utf-8").strip(),
            )

        profiles = sorted(PROFILES.glob("*.json"))
        self.assertTrue(profiles, "expected at least one campaign profile")
        for path in profiles:
            with self.subTest(profile=path.stem):
                profile = SetupProfile.load(path)
                # Skip profiles whose credentials are not available in this
                # environment; a missing secret is an operator condition, not drift.
                required = set(profile.secret_environment.values())
                if any(not environment.get(name) for name in required):
                    self.skipTest(f"credentials absent for {path.stem}")
                profile.resolve(environment)

    def test_pushed_fixtures_are_present_real_files(self) -> None:
        """A push_file step needs a real file: the loader rejects symlinks."""

        checked = 0
        for path in sorted(PROFILES.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for step in payload.get("steps") or []:
                if step.get("kind") != "push_file":
                    continue
                source = (step.get("parameters") or {})["source"]
                target = PROFILES / source
                self.assertTrue(
                    target.is_file(),
                    f"{path.stem} pushes {source}, which is not on disk",
                )
                self.assertFalse(
                    target.is_symlink(),
                    f"{path.stem} pushes {source} as a symlink, which is refused",
                )
                checked += 1
        self.assertGreater(checked, 0, "expected at least one push_file fixture")

    def test_missing_fixture_is_rejected(self) -> None:
        """The guard above is only meaningful if absence actually fails."""

        staging = Path(tempfile.mkdtemp()) / "profiles"
        shutil.copytree(PROFILES, staging)
        pushed = []
        for path in sorted(staging.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for step in payload.get("steps") or []:
                if step.get("kind") == "push_file":
                    pushed.append((path, staging / (step["parameters"]["source"])))
        if not pushed:
            self.skipTest("no push_file fixtures to negate")
        profile_path, fixture_path = pushed[0]
        fixture_path.unlink()
        with self.assertRaises(ValueError):
            SetupProfile.load(profile_path).resolve({})


class LostMethodMetricGuard(unittest.TestCase):
    """A zero that means "not measured" must not be averaged as "no coverage"."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = load_harness()

    def test_jacoco_contradiction_is_flagged(self) -> None:
        # The observed And-Bible failure: no methods recorded, but JaCoCo saw the
        # app's own probes execute.
        self.assertTrue(self.harness.lost_method_metric({
            "valordroid_method": {"coverage_percent": 0, "actions": 227,
                                  "total_units": 128},
            "jacoco": {"covered_probes": 1520},
        }))

    def test_flagged_without_jacoco_corroboration(self) -> None:
        # The v3-comparable bundles carry no jacoco-agent.properties, so JaCoCo is
        # absent for every row and cannot corroborate anything. A long run that
        # recorded not one method is still a lost stream.
        self.assertTrue(self.harness.lost_method_metric({
            "valordroid_method": {"coverage_percent": 0, "actions": 300,
                                  "total_units": 6721},
            "jacoco": {},
        }))

    def test_run_that_never_started_is_a_real_zero(self) -> None:
        self.assertFalse(self.harness.lost_method_metric({
            "valordroid_method": {"coverage_percent": 0, "actions": 0,
                                  "total_units": 6721},
            "jacoco": {},
        }))

    def test_early_bounce_is_not_flagged(self) -> None:
        self.assertFalse(self.harness.lost_method_metric({
            "valordroid_method": {"coverage_percent": 0, "actions": 12,
                                  "total_units": 6721},
            "jacoco": {},
        }))

    def test_low_but_nonzero_coverage_is_trusted(self) -> None:
        self.assertFalse(self.harness.lost_method_metric({
            "valordroid_method": {"coverage_percent": 0.5, "actions": 300,
                                  "total_units": 6721},
            "jacoco": {},
        }))


class V3ConfigParity(unittest.TestCase):
    """Pin the v3 baseline's own values, recovered from /home/ubuntu/vd-ctx.

    These are not arbitrary preferences. The harness previously ran its timeouts
    2-4x tighter than the baseline it is compared against, which turned a slow
    observation on a heavy screen into an aborted action, and settled for 2.0s per
    action against the baseline's 0.3s -- about 21 minutes of a 60 minute budget
    spent waiting.
    """

    V3_RUNTIME = {
        "adb_timeout_seconds": 40.0,
        "install_timeout_seconds": 300.0,
        "launch_timeout_seconds": 60.0,
        "observation_timeout_seconds": 60.0,
        "action_timeout_seconds": 40.0,
        "route_foreground_timeout_seconds": 4.0,
        "post_action_delay_seconds": 0.2,
        "pid_poll_seconds": 0.25,
        "reset_seed_enabled": True,
    }
    V3_CORE = {
        "association_settle_seconds": 0.3,
        "allow_forced_routes": True,
        "launch_on_frontier_exhaustion": True,
        "max_synthesized_deep_links": 12,
        "stall_seconds": 90.0,
        "stall_actions": 40,
        "initial_no_yield_attempts": 2,
        "max_zero_gain_candidate_attempts": 3,
        "max_llm_candidates": 20,
        "max_consecutive_foreign_actions": 2,
        "foreign_actions_before_giving_up": 4,
    }

    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = load_harness()

    def test_runtime_matches_v3_baseline(self) -> None:
        runtime = self.harness.runtime_config(
            "127.0.0.1:5560", 3600.0, 100000, False, None, "Muzei")
        for key, expected in self.V3_RUNTIME.items():
            with self.subTest(field=key):
                self.assertEqual(runtime[key], expected)

    def test_observation_extras_default_off(self) -> None:
        """Both are off in v3, so a reproduction must not enable them silently."""

        runtime = self.harness.runtime_config(
            "127.0.0.1:5560", 3600.0, 100000, False, None, "Muzei")
        self.assertFalse(runtime["webview_enabled"])
        self.assertFalse(runtime["semantic_state_enabled"])
        self.assertEqual(runtime["webview_url_patterns"], [])

    def test_observation_extras_are_reachable(self) -> None:
        runtime = self.harness.runtime_config(
            "127.0.0.1:5560", 3600.0, 100000, False, None, "Muzei",
            webview_enabled=True, semantic_state_enabled=True)
        self.assertTrue(runtime["webview_enabled"])
        self.assertTrue(runtime["semantic_state_enabled"])
        self.assertTrue(runtime["webview_url_patterns"])

    def test_shell_observer_app_keeps_uiautomator2_off(self) -> None:
        for app in sorted(self.harness.SHELL_OBSERVER_APPS):
            with self.subTest(app=app):
                runtime = self.harness.runtime_config(
                    "127.0.0.1:5560", 3600.0, 100000, False, None, app)
                self.assertFalse(runtime["uiautomator2_primary_enabled"])
                self.assertFalse(runtime["uiautomator2_fallback_enabled"])

    def test_core_fixtures_carry_v3_values(self) -> None:
        for name in ("core-model-enabled", "core-stall-only"):
            path = FIXTURES / f"{name}.json"
            if not path.is_file():
                self.skipTest(f"{name}.json is not present")
            core = json.loads(path.read_text(encoding="utf-8"))
            for key, expected in self.V3_CORE.items():
                with self.subTest(config=name, field=key):
                    self.assertEqual(core.get(key), expected)

    def test_core_fixtures_are_accepted_by_coreconfig(self) -> None:
        """An unknown or renamed key would otherwise be silently ineffective."""

        from valordroid.config import CoreConfig

        for name in ("core-model-enabled", "core-stall-only"):
            path = FIXTURES / f"{name}.json"
            if not path.is_file():
                self.skipTest(f"{name}.json is not present")
            core = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(config=name):
                CoreConfig(**core)

    def test_prepared_root_is_the_v3_comparable_bundle_set(self) -> None:
        """The other set's method universe matches v3 for 0 of 50 apps.

        Measuring against it is what made And-Bible read 13.28% instead of 61.23%,
        because its instrumented universe holds 128 methods against v3's 6721.
        """

        self.assertEqual(
            self.harness.DEFAULT_PREPARED_ROOT,
            Path("/home/ubuntu/vd-campaign/bundles-final50/prepared"),
        )


if __name__ == "__main__":
    unittest.main()


class StopReasonDetection(unittest.TestCase):
    """A failed setup profile must be recognised so the app is not simply lost.

    Money-Manager-Ex and RedReader both aborted with ``setup_profile_failed`` during
    the 50-app campaign. The first detector written for this read ``stop_reason``
    from run.json, where an aborted run in fact records ``abort_reason``, so it
    silently returned None for exactly the runs it existed to catch.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = load_harness()

    def _run_dir(self, payload: dict | None, printed: str | None = None) -> Path:
        root = Path(tempfile.mkdtemp())
        out = root / "valordroid"
        out.mkdir()
        if payload is not None:
            (out / "run.json").write_text(json.dumps(payload), encoding="utf-8")
        if printed is not None:
            (root / "valordroid.stdout.txt").write_text(printed, encoding="utf-8")
        return out

    def test_abort_reason_is_read(self) -> None:
        out = self._run_dir({"status": "aborted",
                             "abort_reason": "setup_profile_failed",
                             "abort_phase": "setup"})
        self.assertEqual(self.harness.stop_reason_of(out), "setup_profile_failed")

    def test_stop_reason_spelling_is_also_read(self) -> None:
        out = self._run_dir({"stop_reason": "setup_profile_failed"})
        self.assertEqual(self.harness.stop_reason_of(out), "setup_profile_failed")

    def test_falls_back_to_printed_summary(self) -> None:
        """run.json is absent if the runtime died before writing it."""

        out = self._run_dir(None, printed="chatter\n" + json.dumps(
            {"status": "aborted", "stop_reason": "setup_profile_failed"}))
        self.assertEqual(self.harness.stop_reason_of(out), "setup_profile_failed")

    def test_healthy_run_does_not_trigger_the_fallback(self) -> None:
        # A run that used its whole budget reports max_seconds, and re-running it
        # without its setup profile would discard a good measurement.
        out = self._run_dir({"status": "finished", "stop_reason": "max_seconds"})
        self.assertNotEqual(self.harness.stop_reason_of(out), "setup_profile_failed")

    def test_absent_evidence_is_not_mistaken_for_failure(self) -> None:
        self.assertIsNone(self.harness.stop_reason_of(self._run_dir(None)))

    def test_corrupt_manifest_does_not_raise(self) -> None:
        root = Path(tempfile.mkdtemp())
        out = root / "valordroid"
        out.mkdir()
        (out / "run.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.harness.stop_reason_of(out))


class NavigationDoctrineSwitch(unittest.TestCase):
    """The doctrine must be withholdable, so its effect can be measured.

    It was added after the v3 baseline and shipped in every prompt without ever being
    compared against its absence. The 50-app campaign then split along the axis that
    decides how often it is read: with ``model_first_on_stall`` on it is consulted at
    every stall, and the apps that never stalled gained 9-22 points while the three
    that stalled 177-471 times each lost about 5. This switch exists so that can be
    tested rather than argued.
    """

    def setUp(self) -> None:
        self._saved = os.environ.get("VD_NAVIGATION_DOCTRINE")

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("VD_NAVIGATION_DOCTRINE", None)
        else:
            os.environ["VD_NAVIGATION_DOCTRINE"] = self._saved

    def _doctrine(self, setting: str | None) -> str:
        from valordroid.llm import navigation

        if setting is None:
            os.environ.pop("VD_NAVIGATION_DOCTRINE", None)
        else:
            os.environ["VD_NAVIGATION_DOCTRINE"] = setting
        return navigation.doctrine(include_vocabulary=True)

    def test_enabled_by_default(self) -> None:
        """Absent the variable, behaviour is exactly what the campaign ran."""

        from valordroid.llm import navigation

        os.environ.pop("VD_NAVIGATION_DOCTRINE", None)
        self.assertTrue(navigation.doctrine_enabled())
        self.assertIn("GOAL.", self._doctrine(None))

    def test_off_withholds_guidance(self) -> None:
        withheld = self._doctrine("off")
        full = self._doctrine(None)
        self.assertLess(len(withheld), len(full))

    def test_off_keeps_the_action_vocabulary(self) -> None:
        """Dropping the vocabulary would change what the model can express.

        That would confound the measurement: the variable under test is the guidance,
        not the set of actions the executor accepts.
        """

        withheld = self._doctrine("off")
        self.assertIn("tap", withheld.lower())
        self.assertIn("scroll_forward", withheld.lower())

    def test_accepted_off_spellings(self) -> None:
        for setting in ("off", "0", "false", "no", "OFF", "False"):
            with self.subTest(setting=setting):
                from valordroid.llm import navigation

                os.environ["VD_NAVIGATION_DOCTRINE"] = setting
                self.assertFalse(navigation.doctrine_enabled())

    def test_other_values_leave_it_enabled(self) -> None:
        for setting in ("on", "1", "true", "", "anything"):
            with self.subTest(setting=setting):
                from valordroid.llm import navigation

                os.environ["VD_NAVIGATION_DOCTRINE"] = setting
                self.assertTrue(navigation.doctrine_enabled())
