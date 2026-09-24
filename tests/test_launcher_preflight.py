"""An unchoosable entry activity must fail at plan time, not on a device.

`AndroidSession._select_launcher` refuses to guess between several launch
candidates. That is right: picking the wrong entry point spends a whole run
somewhere the app does not start. But it refused on the device, after the slot was
provisioned, the APK installed and the collector attached, with a message naming
neither the app nor the candidates.

A-Photo-Manager declares two launchable activities, FotoGalleryActivity and
locationmap.MapGeoPickerActivity, and produced no valid run in either arm of the
smoke campaign for exactly this reason. One app of fifty, invisible until a real
device rejected it an hour in.

`create_fleet_plan` already holds the verified prepared bundle and the frozen
runtime, and already cross-checks forced_components against allow_forced_routes,
so the same check belongs beside it where it costs nothing.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from valordroid.fleet_plan import _check_launcher_is_resolvable
from valordroid.runtime_config import RuntimeConfig

PACKAGE = "com.example.app"
FIRST = f"{PACKAGE}/{PACKAGE}.FirstActivity"
SECOND = f"{PACKAGE}/{PACKAGE}.SecondActivity"


class _Manifest:
    def __init__(self, candidates, package: str = PACKAGE) -> None:
        self.package_name = package
        self.launch_candidates = tuple(candidates)


class _Bundle:
    def __init__(self, candidates, package: str = PACKAGE) -> None:
        self.manifest = _Manifest(candidates, package)


def _runtime(launcher: str | None) -> RuntimeConfig:
    return RuntimeConfig.from_mapping(
        {
            "schema_version": 4,
            "serial": "127.0.0.1:5560",
            "launcher": launcher,
            "max_actions": 10,
            "max_seconds": 60.0,
            "adb_timeout_seconds": 40.0,
            "install_timeout_seconds": 300.0,
            "launch_timeout_seconds": 60.0,
            "observation_timeout_seconds": 60.0,
            "route_foreground_timeout_seconds": 4.0,
            "action_timeout_seconds": 40.0,
            "post_action_delay_seconds": 0.6,
            "pid_poll_seconds": 0.25,
            "uninstall_after_run": True,
            "capture_screenshots": False,
            "text_input_value": "valordroid",
            "per_field_input_enabled": True,
            "deep_links": [],
            "exported_components": [],
            "forced_components": [],
            "reset_seed_enabled": True,
            "suppress_soft_keyboard": True,
            "route_discovery_sha256": None,
            "component_extras": {},
        }
    )


class LauncherPreflightTest(unittest.TestCase):
    def test_a_single_candidate_needs_no_override(self) -> None:
        _check_launcher_is_resolvable("job", _runtime(None), _Bundle([FIRST]))

    def test_two_candidates_without_an_override_are_refused(self) -> None:
        """The reproduction, as A-Photo-Manager hit it."""

        with self.assertRaises(ValueError) as caught:
            _check_launcher_is_resolvable(
                "a-photo-manager-r1", _runtime(None), _Bundle([FIRST, SECOND])
            )
        message = str(caught.exception)
        self.assertIn("a-photo-manager-r1", message)
        self.assertIn("2 launch candidates", message)
        # Actionable: the message has to name what to choose between.
        self.assertIn(FIRST, message)
        self.assertIn(SECOND, message)

    def test_a_supplied_override_resolves_the_ambiguity(self) -> None:
        _check_launcher_is_resolvable(
            "job", _runtime(FIRST), _Bundle([FIRST, SECOND])
        )

    def test_an_override_that_is_not_a_candidate_is_refused(self) -> None:
        """A renamed or mistyped activity is otherwise silent until launch."""

        with self.assertRaises(ValueError) as caught:
            _check_launcher_is_resolvable(
                "job",
                _runtime(f"{PACKAGE}/{PACKAGE}.GoneActivity"),
                _Bundle([FIRST, SECOND]),
            )
        self.assertIn("is not a launch candidate", str(caught.exception))

    def test_a_short_override_spelling_is_accepted(self) -> None:
        """A manifest spells its own activity without the package prefix."""

        _check_launcher_is_resolvable(
            "job", _runtime(".FirstActivity"), _Bundle([FIRST, SECOND])
        )

    def test_no_candidates_at_all_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            _check_launcher_is_resolvable("job", _runtime(None), _Bundle([]))


class RealBundleTest(unittest.TestCase):
    """The actual app that aborted, if its prepared bundle is on this host."""

    BUNDLES = Path("/home/ubuntu/vd-campaign/bundles-final50/prepared")

    def setUp(self) -> None:
        manifest = self.BUNDLES / "A-Photo-Manager" / "prepared.json"
        if not manifest.is_file():
            self.skipTest("the prepared A-Photo-Manager bundle is not on this host")
        document = json.loads(manifest.read_text())
        self.candidates = tuple(document["launch_candidates"])
        self.package = document["package_name"]

    def _bundle(self) -> _Bundle:
        return _Bundle(self.candidates, self.package)

    def test_the_app_really_declares_two_launchers(self) -> None:
        self.assertEqual(len(self.candidates), 2, self.candidates)

    def test_the_preflight_refuses_it_without_an_override(self) -> None:
        with self.assertRaises(ValueError):
            _check_launcher_is_resolvable(
                "a-photo-manager-r1",
                _runtime(None),
                self._bundle(),
            )

    def test_the_gallery_activity_resolves_it(self) -> None:
        gallery = next(
            name for name in self.candidates if "FotoGalleryActivity" in name
        )
        _check_launcher_is_resolvable(
            "a-photo-manager-r1", _runtime(gallery), self._bundle()
        )


if __name__ == "__main__":
    unittest.main()
