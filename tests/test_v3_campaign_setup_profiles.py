"""The v3 campaign must preflight and isolate setup-profile execution.

These tests are hermetic: they dynamically import the historical campaign
script, replace all process boundaries, and never invoke adb, a sidecar, or a
real run.  The parent-side checks matter because run-android's own validation
happens after this campaign used to create output and start an ADB-forwarding
JaCoCo sidecar.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from valordroid.setup_profile import SetupProfile

_REPOSITORY = Path(__file__).resolve().parents[1]
_CAMPAIGN = (
    Path(__file__).resolve().parents[1] / "app" / "tools" / "v3_campaign.py"
)
_AMAZE_PROFILE = (
    Path(__file__).resolve().parents[0] / "fixtures" / "setup-profiles" / "Amaze.json"
)


def _driver():
    name = f"_v3_campaign_under_test_{id(object())}"
    spec = importlib.util.spec_from_file_location(name, _CAMPAIGN)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _profile_document(
    package: str,
    *,
    secret_environment: dict[str, str] | None = None,
) -> dict:
    secret_environment = secret_environment or {}
    steps: list[dict] = []
    for index, slot in enumerate(sorted(secret_environment), 1):
        steps.append(
            {
                "id": f"enter-secret-{index}",
                "kind": "input_secret",
                "required": True,
                "timeout_seconds": 10.0,
                "parameters": {
                    "secret_slot": slot,
                    "clear": True,
                    "selector": {
                        "package": package,
                        "resource_id": f"{package}:id/field_{index}",
                    },
                },
            }
        )
    if not steps:
        steps.append(
            {
                "id": "settle",
                "kind": "wait",
                "required": True,
                "timeout_seconds": 10.0,
                "parameters": {"seconds": 0.0},
            }
        )
    return {
        "schema": 1,
        "package": package,
        "profile": "campaign-test-profile",
        "persona": "test-persona",
        "timeout_seconds": 60.0,
        "allowed_foreign_packages": [],
        "secret_environment": secret_environment,
        "steps": steps,
        "success_predicates": [
            {
                "kind": "package_foreground",
                "timeout_seconds": 10.0,
                "parameters": {"package": package},
            }
        ],
    }


def _write_profile(
    directory: Path,
    app: str,
    package: str,
    *,
    secret_environment: dict[str, str] | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{app}.json"
    path.write_text(
        json.dumps(
            _profile_document(
                package, secret_environment=secret_environment
            )
        ),
        encoding="utf-8",
    )
    return path


def _write_bundle(root: Path, app: str, package: str) -> Path:
    directory = root / app
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "prepared.json").write_text(
        json.dumps({"package_name": package}), encoding="utf-8"
    )
    return directory


class ParentPreflightTest(unittest.TestCase):
    APP = "Fixture-App"
    PACKAGE = "com.example.fixture"
    ENVIRONMENT = "VALORDROID_FIXTURE_PASSWORD"
    SECRET = "do-not-retain-this-value"

    def setUp(self) -> None:
        self.driver = _driver()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.bundles = self.root / "bundles"
        self.profiles = self.root / "profiles"
        _write_bundle(self.bundles, self.APP, self.PACKAGE)
        self.driver.BUNDLES = self.bundles

    def test_missing_profile_is_an_exact_noop(self) -> None:
        self.profiles.mkdir()
        self.assertEqual(
            self.driver.preflight_setup_profiles(
                [self.APP], self.profiles, {"UNRELATED": "value"}
            ),
            {},
        )
        self.assertEqual(self.driver.child_environment(None), self.driver.CHILD_ENV)

    def test_only_the_declared_secret_reaches_that_apps_child(self) -> None:
        _write_profile(
            self.profiles,
            self.APP,
            self.PACKAGE,
            secret_environment={"login-password": self.ENVIRONMENT},
        )
        setups = self.driver.preflight_setup_profiles(
            [self.APP, self.APP],
            self.profiles,
            {self.ENVIRONMENT: self.SECRET, "UNRELATED": "must-not-leak"},
        )
        setup = setups[self.APP]
        child = self.driver.child_environment(setup)
        self.assertEqual(
            child,
            {**self.driver.CHILD_ENV, self.ENVIRONMENT: self.SECRET},
        )
        self.assertNotIn("UNRELATED", child)
        self.assertNotIn(self.SECRET, repr(setup))
        self.assertNotIn(self.SECRET, json.dumps(setup.verification_details))

    def test_missing_secret_fails_before_an_output_root_is_created(self) -> None:
        _write_profile(
            self.profiles,
            self.APP,
            self.PACKAGE,
            secret_environment={"login-password": self.ENVIRONMENT},
        )
        output = self.root / "must-not-exist"
        argv = [
            "campaign.py",
            "--output",
            str(output),
            "--bundles",
            str(self.bundles),
            "--setup-profile-dir",
            str(self.profiles),
            "--apps",
            self.APP,
        ]
        with mock.patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as caught:
                self.driver.main()
        self.assertIn(self.ENVIRONMENT, str(caught.exception))
        self.assertFalse(output.exists())

    def test_profile_package_must_match_the_prepared_bundle(self) -> None:
        _write_profile(self.profiles, self.APP, "com.example.other")
        with self.assertRaisesRegex(ValueError, "does not match prepared package"):
            self.driver.preflight_setup_profiles(
                [self.APP], self.profiles, {}
            )

    def test_profile_symlink_is_refused(self) -> None:
        target = self.root / "real-profile.json"
        target.write_text(
            json.dumps(_profile_document(self.PACKAGE)), encoding="utf-8"
        )
        self.profiles.mkdir()
        (self.profiles / f"{self.APP}.json").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "regular non-symlink"):
            self.driver.preflight_setup_profiles(
                [self.APP], self.profiles, {}
            )

    def test_a_secret_cannot_replace_path_home_or_pythonpath(self) -> None:
        _write_profile(
            self.profiles,
            self.APP,
            self.PACKAGE,
            secret_environment={"login-password": "PATH"},
        )
        with self.assertRaisesRegex(ValueError, "collides with campaign baseline"):
            self.driver.preflight_setup_profiles(
                [self.APP], self.profiles, {"PATH": self.SECRET}
            )


class DeclaredComponentContextMergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = _driver()
        self.profile = SetupProfile.load(_AMAZE_PROFILE)
        self.component = next(iter(self.profile.component_intent_context))
        self.short = self.component.split("/", 1)[-1]
        self.declared = [
            "",
            "uri",
            "file:///sdcard/valordroid/amaze-notes.txt",
        ]

    def test_analyzed_entries_survive_and_declared_context_is_added(self) -> None:
        analyzed = {self.component: [["EXTRA", "string", "retained"]]}
        merged = self.driver.merge_profile_component_context(
            analyzed, self.profile
        )
        self.assertIn(["EXTRA", "string", "retained"], merged[self.component])
        self.assertIn(self.declared, merged[self.component])
        self.profile.validate_component_extras(merged)

    def test_identical_context_is_not_duplicated(self) -> None:
        merged = self.driver.merge_profile_component_context(
            {self.component: [self.declared]}, self.profile
        )
        self.assertEqual(merged[self.component].count(self.declared), 1)

    def test_full_and_short_aliases_cannot_diverge(self) -> None:
        merged = self.driver.merge_profile_component_context(
            {
                self.component: [["A", "string", "one"]],
                self.short: [["B", "string", "two"]],
            },
            self.profile,
        )
        self.assertEqual(merged[self.component], merged[self.short])
        self.assertIn(["A", "string", "one"], merged[self.component])
        self.assertIn(["B", "string", "two"], merged[self.component])
        self.assertIn(self.declared, merged[self.component])

    def test_conflicting_uri_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "component context conflict"):
            self.driver.merge_profile_component_context(
                {
                    self.short: [
                        ["", "uri", "file:///sdcard/other.txt"]
                    ]
                },
                self.profile,
            )

    def test_conflict_fails_before_config_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = root / "ctx"
            context.mkdir()
            (context / "runtime-blind-Amaze.json").write_text(
                json.dumps(
                    {
                        "component_extras": {
                            self.component: [
                                ["", "uri", "file:///sdcard/other.txt"]
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            (context / "core.json").write_text("{}", encoding="utf-8")
            self.driver.CTX = context
            output = root / "config-output"
            setup = self.driver.CampaignSetup(
                path=_AMAZE_PROFILE,
                profile=self.profile,
                child_secrets={},
                secret_values=(),
            )
            with self.assertRaisesRegex(ValueError, "component context conflict"):
                self.driver.configs(
                    "Amaze",
                    "127.0.0.1:5560",
                    output,
                    60,
                    "blind",
                    setup=setup,
                )
            self.assertFalse(output.exists())


class ModelConfigRegressionTest(unittest.TestCase):
    def test_model_arm_always_gets_its_model_config(self) -> None:
        driver = _driver()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = root / "ctx"
            context.mkdir()
            (context / "runtime-blind-Chess.json").write_text(
                json.dumps({"component_extras": {}}), encoding="utf-8"
            )
            (context / "core.json").write_text("{}", encoding="utf-8")
            driver.CTX = context
            output = root / "configs"
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("VD_LOW_REACH_TARGETS", None)
                _, _, model = driver.configs(
                    "Chess", "127.0.0.1:5560", output, 60, "gemini"
                )
            self.assertEqual(model, output / "model.json")
            self.assertTrue(model.is_file())
            self.assertEqual(
                json.loads(model.read_text(encoding="utf-8"))["model"],
                "gemini-2.5-flash",
            )


class ChildBoundaryTest(unittest.TestCase):
    APP = "Fixture-App"
    PACKAGE = "com.example.fixture"
    ENVIRONMENT = "VALORDROID_FIXTURE_PASSWORD"
    SECRET = "child-boundary-secret"

    def setUp(self) -> None:
        self.driver = _driver()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.bundles = self.root / "bundles"
        self.profiles = self.root / "profiles"
        _write_bundle(self.bundles, self.APP, self.PACKAGE)
        _write_profile(
            self.profiles,
            self.APP,
            self.PACKAGE,
            secret_environment={"login-password": self.ENVIRONMENT},
        )
        self.driver.BUNDLES = self.bundles
        self.setup = self.driver.preflight_setup_profiles(
            [self.APP], self.profiles, {self.ENVIRONMENT: self.SECRET}
        )[self.APP]
        self.calls: list[tuple[list[str], dict]] = []
        self.sidecars: list[dict] = []
        self.child_digest = self.setup.profile.profile_sha256

        def fake_configs(app, serial, directory, seconds, arm, setup=None):
            directory.mkdir(parents=True, exist_ok=True)
            runtime = directory / "runtime.json"
            core = directory / "core.json"
            runtime.write_text("{}", encoding="utf-8")
            core.write_text("{}", encoding="utf-8")
            return runtime, core, None

        self.driver.configs = fake_configs
        self.driver._package_name = lambda app: self.PACKAGE
        self.driver._jacoco_summary = lambda directory: {
            "dump_succeeded": True,
            "probe_coverage_percent": 5.0,
            "covered_probes": 1,
            "total_probes": 20,
            "classes_observed": 2,
        }
        self.driver.retain = lambda run: {
            "summary": {
                "observed_coverage_percent": 12.5,
                "observed_covered_units": 5,
                "total_units": 40,
                "action_attempts": 2,
                "total_wall_clock_seconds": 1.0,
                "activity_reach": {},
            }
        }
        test = self

        class FakeSidecar:
            def __init__(self, arguments, **keywords):
                test.sidecars.append(
                    {"arguments": list(arguments), "keywords": keywords}
                )

            def terminate(self):
                return None

            def wait(self, timeout=None):
                return 0

            def kill(self):
                return None

        class FakeSubprocess:
            DEVNULL = object()
            TimeoutExpired = TimeoutError
            Popen = FakeSidecar

            @staticmethod
            def run(arguments, **keywords):
                arguments = list(arguments)
                test.calls.append((arguments, keywords))
                if arguments and arguments[0] == "rm":
                    return types.SimpleNamespace(
                        returncode=0, stdout="", stderr=""
                    )
                return types.SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {"setup_profile_sha256": test.child_digest}
                    ),
                    stderr=f"diagnostic {test.SECRET}",
                )

        self.driver.subprocess = FakeSubprocess

    def _run(self) -> dict:
        return self.driver.one(
            self.APP,
            "blind",
            "127.0.0.1:5560",
            self.root / "output",
            60,
            setup=self.setup,
        )

    def test_profile_argument_environment_identity_and_redaction(self) -> None:
        row = self._run()
        child_arguments, child_keywords = self.calls[-1]
        self.assertIn("--setup-profile", child_arguments)
        self.assertEqual(
            Path(child_arguments[child_arguments.index("--setup-profile") + 1]),
            self.setup.path,
        )
        self.assertEqual(
            child_keywords["env"],
            {**self.driver.CHILD_ENV, self.ENVIRONMENT: self.SECRET},
        )
        self.assertEqual(
            self.sidecars[0]["keywords"]["env"], self.driver.CHILD_ENV
        )
        self.assertEqual(
            row["setup_profile"]["setup_profile_sha256"], self.child_digest
        )
        self.assertEqual(row["coverage_percent"], 12.5)
        self.assertNotIn(self.SECRET, json.dumps(row))
        driver_log = (
            self.root
            / "output"
            / self.APP
            / "blind"
            / "driver.log"
        ).read_text(encoding="utf-8")
        self.assertNotIn(self.SECRET, driver_log)
        self.assertIn("[REDACTED]", driver_log)

    def test_child_profile_digest_mismatch_invalidates_the_row(self) -> None:
        self.child_digest = "0" * 64
        row = self._run()
        self.assertIsNone(row["coverage_percent"])
        self.assertIn("identity mismatch", row["refusal"])
        self.assertEqual(
            row["setup_profile"]["setup_profile_sha256"],
            self.setup.profile.profile_sha256,
        )


class RetentionEnvironmentTest(unittest.TestCase):
    def test_retention_does_not_inherit_parent_secrets(self) -> None:
        driver = _driver()
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "summary.json").write_text("{}", encoding="utf-8")
            captured: dict = {}

            class FakeSubprocess:
                @staticmethod
                def run(arguments, **keywords):
                    captured.update(keywords)
                    return types.SimpleNamespace(returncode=0)

            driver.subprocess = FakeSubprocess
            driver.retain(run)
            self.assertEqual(captured["env"], driver.CHILD_ENV)


if __name__ == "__main__":
    unittest.main()
