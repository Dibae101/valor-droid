"""The campaign driver has to be able to switch on the mechanisms that exist.

`run_coverage_experiment.runtime_config` accepted semantic refinement,
UIAutomator2 and WebView and nothing else. It hardcoded `capture_screenshots` to
False, and `core_config` emitted no visual budget at all. So a campaign could not
authorize a foreign workflow or a visual escape however completely the runtime
and the runner implemented them: generating a config with every supported flag on
still produced

    {"foreign_workflows": [], "visual_escape_enabled": false,
     "capture_screenshots": false, "max_visual_escapes": 0}

A file rather than flags, because a foreign workflow is structured data: an entry
action, the packages it may enter, the action kinds it may use, its own action and
time bounds, and a return predicate. None of that survives a command line.

Everything stays off unless a file asks for it, and an invalid request fails
before any device is touched rather than on the device an hour later.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from valordroid.config import CoreConfig
from valordroid.runtime_config import RuntimeConfig

_TOOLS = Path(__file__).resolve().parents[1] / "app" / "tools"

PACKAGE = "de.k3b.android.androFotoFinder"


def _driver():
    spec = importlib.util.spec_from_file_location(
        "_enhancements_driver_under_test", _TOOLS / "run_coverage_experiment.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.log = lambda message: None
    return module


def _workflow(**overrides) -> dict:
    workflow = {
        "workflow_id": "pick_one_image",
        # target_id omitted on purpose: it is derived from the selector, and the
        # loader fills it so an author never hand-computes a digest.
        "entry_action": {
            "kind": "tap",
            "parameters": {
                "selector": {
                    "package": PACKAGE,
                    "resource_id": f"{PACKAGE}:id/cmd_gallery",
                    "class_name": "android.widget.ImageButton",
                    "content_description": "",
                    "text": "",
                    "tree_path": "0/0/1",
                },
                "expected_state_id": "0" * 64,
            },
        },
        "allowed_foreign_packages": ["com.android.documentsui"],
        "allowed_action_kinds": ["long_press", "tap"],
        "max_actions": 8,
        "max_seconds": 45.0,
        "return_predicate": {
            "kind": "node_present",
            "timeout_seconds": 30.0,
            "parameters": {
                "selector": {
                    "package": PACKAGE,
                    "resource_id": f"{PACKAGE}:id/image_view",
                }
            },
        },
    }
    workflow.update(overrides)
    return workflow


class LoaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = _driver()
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _write(self, document) -> Path:
        path = self.root / "enhancements.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def _entry(self, **app) -> dict:
        return {"schema": 1, "apps": {"A-Photo-Manager": app}}

    def test_no_file_means_no_enhancements(self) -> None:
        self.assertEqual(self.driver.load_app_enhancements(None), {})

    def test_a_supplied_workflow_is_loaded(self) -> None:
        path = self._write(self._entry(foreign_workflows=[_workflow()]))
        loaded = self.driver.load_app_enhancements(path)
        entry = loaded["A-Photo-Manager"]
        self.assertEqual(len(entry.foreign_workflows), 1)
        self.assertEqual(
            entry.foreign_workflows[0]["workflow_id"], "pick_one_image"
        )

    def test_the_derived_target_id_is_filled_in(self) -> None:
        """It equals stable_hash(selector); an author must not hand-compute it."""

        from valordroid.foreign_workflow import _selector
        from valordroid.models import stable_hash

        path = self._write(self._entry(foreign_workflows=[_workflow()]))
        entry = self.driver.load_app_enhancements(path)["A-Photo-Manager"]
        action = entry.foreign_workflows[0]["entry_action"]
        expected = stable_hash(
            _selector(action["parameters"]["selector"], "selector")
        )
        self.assertEqual(action["target_id"], expected)

    def test_a_stale_target_id_is_still_refused(self) -> None:
        """Filling an absent value must not mean correcting a wrong one."""

        workflow = _workflow()
        workflow["entry_action"]["target_id"] = "b" * 64
        with self.assertRaises(SystemExit) as caught:
            self.driver.load_app_enhancements(
                self._write(self._entry(foreign_workflows=[workflow]))
            )
        self.assertIn("not canonical", str(caught.exception))

    def test_an_unknown_schema_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self.driver.load_app_enhancements(
                self._write({"schema": 99, "apps": {"A": {}}})
            )

    def test_an_unknown_key_is_refused_rather_than_ignored(self) -> None:
        """A typo that is silently dropped is a mechanism that never ran."""

        with self.assertRaises(SystemExit) as caught:
            self.driver.load_app_enhancements(
                self._write(self._entry(visual_escape=True))
            )
        self.assertIn("unknown enhancement keys", str(caught.exception))

    def test_a_budget_without_the_switch_is_refused(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.driver.load_app_enhancements(
                self._write(self._entry(max_visual_escapes=2))
            )
        self.assertIn("without visual_escape_enabled", str(caught.exception))

    def test_the_switch_without_a_budget_is_refused(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.driver.load_app_enhancements(
                self._write(
                    self._entry(
                        visual_escape_enabled=True, capture_screenshots=True
                    )
                )
            )
        self.assertIn("zero budget", str(caught.exception))

    def test_a_visual_escape_without_screenshots_is_refused(self) -> None:
        """The runtime's own rule, enforced before the device sees it."""

        with self.assertRaises(SystemExit) as caught:
            self.driver.load_app_enhancements(
                self._write(
                    self._entry(
                        visual_escape_enabled=True, max_visual_escapes=2
                    )
                )
            )
        self.assertIn("requires capture_screenshots", str(caught.exception))

    def test_a_bare_foreground_return_is_refused(self) -> None:
        """The object is the point; returning to the app is not a postcondition."""

        workflow = _workflow(
            return_predicate={
                "kind": "package_foreground",
                "timeout_seconds": 30.0,
                "parameters": {"package": PACKAGE},
            }
        )
        with self.assertRaises(SystemExit) as caught:
            self.driver.load_app_enhancements(
                self._write(self._entry(foreign_workflows=[workflow]))
            )
        self.assertIn("package_foreground", str(caught.exception))

    def test_an_unusable_workflow_fails_before_any_device_work(self) -> None:
        workflow = _workflow(allowed_foreign_packages=[])
        with self.assertRaises(SystemExit):
            self.driver.load_app_enhancements(
                self._write(self._entry(foreign_workflows=[workflow]))
            )


class GeneratedConfigurationTest(unittest.TestCase):
    """What the driver hands the runner, which is what the review checked."""

    def setUp(self) -> None:
        self.driver = _driver()

    def _runtime(self, **overrides):
        arguments = {
            "serial": "127.0.0.1:5560",
            "launcher": None,
            "max_seconds": 3600.0,
            "max_actions": 1500,
            "per_field": True,
        }
        arguments.update(overrides)
        return self.driver.runtime_config(**arguments)

    def test_the_default_configuration_enables_nothing(self) -> None:
        value = self._runtime()
        self.assertNotIn("foreign_workflows", value)
        self.assertNotIn("visual_escape_enabled", value)
        self.assertFalse(value["capture_screenshots"])
        self.assertEqual(
            self.driver.core_config(
                model=True, max_model_calls=10, max_seconds=3600.0
            )["max_visual_escapes"],
            0,
        )

    def test_an_authorized_app_carries_every_setting(self) -> None:
        value = self._runtime(
            foreign_workflows=(_workflow(),),
            capture_screenshots=True,
            visual_escape_enabled=True,
        )
        self.assertTrue(value["capture_screenshots"])
        self.assertTrue(value["visual_escape_enabled"])
        self.assertEqual(
            [item["workflow_id"] for item in value["foreign_workflows"]],
            ["pick_one_image"],
        )

    def test_the_runtime_accepts_the_generated_pair(self) -> None:
        """The retained configuration has to be a real RuntimeConfig."""

        driver = self.driver
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "e.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "apps": {
                            "A-Photo-Manager": {
                                "foreign_workflows": [_workflow()],
                                "capture_screenshots": True,
                                "visual_escape_enabled": True,
                                "max_visual_escapes": 2,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            entry = driver.load_app_enhancements(path)["A-Photo-Manager"]
        runtime = RuntimeConfig.from_mapping(
            self._runtime(
                foreign_workflows=entry.foreign_workflows,
                capture_screenshots=entry.capture_screenshots,
                visual_escape_enabled=entry.visual_escape_enabled,
            )
        )
        core = CoreConfig.from_mapping(
            driver.core_config(
                model=True,
                max_model_calls=400,
                max_seconds=3600.0,
                max_visual_escapes=entry.max_visual_escapes,
            )
        )
        self.assertEqual(
            [item.workflow_id for item in runtime.foreign_workflows],
            ["pick_one_image"],
        )
        self.assertTrue(runtime.visual_escape_enabled)
        self.assertEqual(core.max_visual_escapes, 2)

    def test_a_deterministic_arm_cannot_spend_a_visual_budget(self) -> None:
        """The mechanism needs a model, so a baseline arm zeroes it."""

        self.assertEqual(
            self.driver.core_config(
                model=False,
                max_model_calls=0,
                max_seconds=3600.0,
                max_visual_escapes=4,
            )["max_visual_escapes"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
