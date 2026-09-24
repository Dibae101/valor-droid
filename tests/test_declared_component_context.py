"""A declared component context has to reach the runtime, and be enforced there.

`SetupProfile.validate_component_extras` and
`coverage_context.validate_component_prerequisites` both existed and neither had
a production caller, so a profile could declare the intent context a component
reads and nothing ever checked that the run carried it. Nothing merged it in
either. The Amaze fixture has declared

    file:///sdcard/valordroid/amaze-notes.txt   for  .activities.TextReader

since it was written, and no generated runtime ever carried it. TextReader was
launched bare every time, which is the failure this machinery exists to prevent
and which the module measured on Chess at 28.70% down to 5.93%.

Two halves, tested here together because either alone is useless:

* the experiment driver merges the profile's declared context into the context
  the static analysis derived, refusing a conflict rather than picking a winner;
* the runner refuses, in preparation, a run whose frozen runtime omits the
  declared context or whose declaration names a component the APK does not
  expose as an ordinary launch.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from valordroid.coverage_context import derive_coverage_context
from valordroid.runner import AndroidRunner, RunAbort
from valordroid.setup_profile import SetupProfile
from valordroid.universe import CoverageUniverse

_REPOSITORY = Path(__file__).resolve().parents[1]
_FIXTURES = Path(__file__).resolve().parents[0] / "fixtures" / "setup-profiles"
_TOOLS = Path(__file__).resolve().parents[1] / "app" / "tools"
_AMAZE = _FIXTURES / "Amaze.json"


def _driver():
    spec = importlib.util.spec_from_file_location(
        "_driver_under_test", _TOOLS / "run_coverage_experiment.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DriverMergesDeclaredContextTest(unittest.TestCase):
    """The reproduction: Amaze's URI never survived config generation."""

    def setUp(self) -> None:
        self.driver = _driver()
        self.driver.log = lambda message: None
        self.profile = SetupProfile.load(_AMAZE)
        self.component = next(iter(self.profile.component_intent_context))

    def test_the_declared_uri_survives_config_generation(self) -> None:
        merged = self.driver.merge_declared_component_context(
            {}, _AMAZE, app="Amaze"
        )
        config = self.driver.runtime_config(
            serial="127.0.0.1:5560",
            launcher=None,
            max_seconds=600.0,
            max_actions=100,
            per_field=True,
            component_extras=merged,
        )
        # The check that used to fail against every generated runtime.
        self.profile.validate_component_extras(config["component_extras"])
        self.assertIn(self.component, config["component_extras"])
        self.assertIn(
            ["", "uri", "file:///sdcard/valordroid/amaze-notes.txt"],
            [list(entry) for entry in config["component_extras"][self.component]],
        )

    def test_analyzed_extras_are_preserved_alongside_the_declaration(self) -> None:
        analyzed = {self.component: [["EXTRA_KEY", "string", "valordroid"]]}
        merged = self.driver.merge_declared_component_context(
            analyzed, _AMAZE, app="Amaze"
        )
        entries = [list(entry) for entry in merged[self.component]]
        self.assertIn(["EXTRA_KEY", "string", "valordroid"], entries)
        self.assertIn(
            ["", "uri", "file:///sdcard/valordroid/amaze-notes.txt"], entries
        )

    def test_a_conflicting_value_is_refused_rather_than_resolved(self) -> None:
        """Preferring either side silently publishes a launch nobody declared."""

        analyzed = {self.component: [["", "uri", "file:///sdcard/other.txt"]]}
        with self.assertRaises(SystemExit) as caught:
            self.driver.merge_declared_component_context(
                analyzed, _AMAZE, app="Amaze"
            )
        self.assertIn("resolve the conflict", str(caught.exception))

    def test_an_absent_profile_leaves_the_analysis_untouched(self) -> None:
        analyzed = {"pkg/pkg.A": [["k", "string", "v"]]}
        merged = self.driver.merge_declared_component_context(
            analyzed, _FIXTURES / "does-not-exist.json", app="Nothing"
        )
        self.assertEqual(merged, {"pkg/pkg.A": [["k", "string", "v"]]})

    def test_an_identical_declaration_is_not_duplicated(self) -> None:
        analyzed = {
            self.component: [
                ["", "uri", "file:///sdcard/valordroid/amaze-notes.txt"]
            ]
        }
        merged = self.driver.merge_declared_component_context(
            analyzed, _AMAZE, app="Amaze"
        )
        self.assertEqual(len(merged[self.component]), 1)


PACKAGE = "com.example.app"
EXPORTED = f"{PACKAGE}/{PACKAGE}.ExportedActivity"
FORCED = f"{PACKAGE}/{PACKAGE}.InternalActivity"
_OBJECT = "/sdcard/valordroid/amaze-notes.txt"
_PAYLOAD_SHA256 = (
    "68d272f7f90314ddf72cc1eaab0734e27f325cc8a5ff0f25152a5921f24fdc0f"
)


def _profile(component: str, *, intent_context) -> SetupProfile:
    return SetupProfile.from_mapping(
        {
            "schema": 1,
            "package": PACKAGE,
            "profile": "declared-context",
            "persona": "local-fixture-only",
            "timeout_seconds": 60.0,
            "allowed_foreign_packages": [],
            "secret_environment": {},
            "steps": [
                # The schema requires a declared URI to name a file a required
                # push_file step actually writes, so a declaration cannot point
                # at an object that will not exist. Mirrors the Amaze fixture.
                {
                    "id": "seed-the-object",
                    "kind": "push_file",
                    "required": True,
                    "timeout_seconds": 60.0,
                    "parameters": {
                        "source": "payloads/amaze-notes.txt",
                        "destination": _OBJECT,
                        "sha256": _PAYLOAD_SHA256,
                    },
                },
                {
                    "id": "launch-it",
                    "kind": "launch_component",
                    "required": True,
                    "timeout_seconds": 30.0,
                    "parameters": {"component": component},
                },
            ],
            "success_predicates": [
                {
                    "kind": "package_foreground",
                    "timeout_seconds": 30.0,
                    "parameters": {"package": PACKAGE},
                }
            ],
            "component_context": [
                {
                    "component": component,
                    "requires": ["seed-the-object"],
                    "intent_context": intent_context,
                }
            ],
        }
    )


def _context():
    universe = CoverageUniverse.create(
        package_name=PACKAGE,
        metric="method",
        backend="regression-suite",
        apk_sha256="a" * 64,
        unit_ids=(
            f"<{PACKAGE}.ExportedActivity: void onCreate(android.os.Bundle)>",
            f"<{PACKAGE}.InternalActivity: void onCreate(android.os.Bundle)>",
        ),
    )
    return derive_coverage_context(
        universe,
        discovery=None,
        deep_links=(),
        exported_components=(EXPORTED,),
        forced_components=(FORCED,),
    )


def _runner(profile: SetupProfile, extras, *, context) -> AndroidRunner:
    runner = AndroidRunner.__new__(AndroidRunner)
    runner.setup = type("S", (), {"profile": profile})()
    runner.runtime = type("R", (), {"component_extras": extras})()
    runner.coverage_context = context
    runner.recorded = []
    runner._lifecycle = lambda phase, event, status, **k: runner.recorded.append(
        (event, str(status), k.get("error"))
    )
    return runner


class RunnerEnforcesDeclaredContextTest(unittest.TestCase):
    DECLARED = [["", "uri", f"file://{_OBJECT}"]]

    def test_a_runtime_that_omits_the_declared_context_is_refused(self) -> None:
        """The whole point: launching bare is worse than not launching."""

        runner = _runner(
            _profile(EXPORTED, intent_context=self.DECLARED), {}, context=_context()
        )
        with self.assertRaises(RunAbort) as caught:
            runner._verify_declared_component_context()
        self.assertEqual(
            caught.exception.reason, "declared_component_context_unsatisfied"
        )
        events = [item[0] for item in runner.recorded]
        self.assertIn("declared_component_context_refused", events)

    def test_a_runtime_carrying_the_context_is_verified(self) -> None:
        runner = _runner(
            _profile(EXPORTED, intent_context=self.DECLARED),
            {EXPORTED: tuple(tuple(entry) for entry in self.DECLARED)},
            context=_context(),
        )
        runner._verify_declared_component_context()
        events = [item[0] for item in runner.recorded]
        self.assertIn("declared_component_context_verified", events)
        self.assertNotIn("declared_component_context_refused", events)

    def test_a_forced_only_component_is_not_accepted_as_an_ordinary_launch(self) -> None:
        """A `launch_component` step runs as the shell user, with no root path."""

        runner = _runner(
            _profile(FORCED, intent_context=self.DECLARED),
            {FORCED: tuple(tuple(entry) for entry in self.DECLARED)},
            context=_context(),
        )
        with self.assertRaises(RunAbort):
            runner._verify_declared_component_context()
        errors = [item[2] for item in runner.recorded if item[2]]
        self.assertTrue(any("only reachable as" in str(e) for e in errors), errors)

    def test_a_declaration_with_no_route_evidence_cannot_be_verified(self) -> None:
        """An unverifiable prerequisite must not read as a satisfied one."""

        runner = _runner(
            _profile(EXPORTED, intent_context=self.DECLARED),
            {EXPORTED: tuple(tuple(entry) for entry in self.DECLARED)},
            context=None,
        )
        with self.assertRaises(RunAbort):
            runner._verify_declared_component_context()
        errors = [item[2] for item in runner.recorded if item[2]]
        self.assertTrue(
            any("no coverage context" in str(e) for e in errors), errors
        )

    def test_a_profile_declaring_nothing_is_left_alone(self) -> None:
        """Most profiles declare no component context and must be unaffected."""

        profile = SetupProfile.load(_FIXTURES / "Omni-Notes-Alpha.json")
        self.assertEqual(profile.component_intent_context, {})
        runner = _runner(profile, {}, context=None)
        runner._verify_declared_component_context()
        self.assertEqual(runner.recorded, [])


if __name__ == "__main__":
    unittest.main()
