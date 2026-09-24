"""Reach accounting in the run summary.

The share of an app's own declared activities a run entered is the property most
strongly correlated with coverage across the V2 campaign (+0.65, against
-0.08..+0.45 for every efficiency metric two rounds of fixes optimised). It was
computed only by an offline tool, so nothing in the loop could be held to it.

These pin the arithmetic and, more importantly, the cases where reach is *not* a
number: an app that declares nothing, and a run that cannot read what was declared.
Both have to stay distinguishable from a run that entered nothing.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valordroid.android.manifest import RouteDiscovery
from valordroid.summary import ACTIVITY_REACH_RULE, _activity_reach


PACKAGE = "com.example.app"


def _discovery_json(activities: tuple[str, ...]) -> str:
    discovery = RouteDiscovery.create(
        package_name=PACKAGE,
        apk_sha256="0" * 64,
        declared_activities=tuple(sorted(activities)),
        launch_candidates=(),
        exported_components=(),
        forced_components=(),
        deep_links=(),
        deep_link_owners={},
        declared_schemes=(),
        permission_guarded_components=(),
        disabled_components=(),
        host_substitutions=(),
        truncated=False,
    )
    return json.dumps(discovery.to_dict(), sort_keys=True) + "\n"


class _Ledger:
    def __init__(self, details: list[dict]) -> None:
        self._details = details

    def payloads(self) -> list[dict]:
        return [{"attempt": {"outcome": {"details": item}}} for item in self._details]


class _Store:
    """Only the two surfaces `_activity_reach` reads."""

    def __init__(self, path: Path, details: list[dict]) -> None:
        self.route_discovery_path = path
        self._ledger = _Ledger(details)

    def ledger(self, name: str) -> _Ledger:
        assert name == "actions"
        return self._ledger


class ActivityReachTest(unittest.TestCase):
    def _reach(self, activities: tuple[str, ...], details: list[dict]) -> dict:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "route-discovery.json"
            path.write_text(_discovery_json(activities), encoding="utf-8")
            return _activity_reach(_Store(path, details), PACKAGE)

    def test_entered_is_the_intersection_of_declared_and_observed(self) -> None:
        reach = self._reach(
            (
                f"{PACKAGE}/{PACKAGE}.Main",
                f"{PACKAGE}/{PACKAGE}.Second",
                f"{PACKAGE}/{PACKAGE}.Never",
            ),
            [
                {
                    "before_activity": f"{PACKAGE}/.Main",
                    "after_activity": f"{PACKAGE}/.Second",
                }
            ],
        )
        self.assertEqual(reach["declared"], 3)
        self.assertEqual(reach["entered"], 2)
        self.assertAlmostEqual(reach["share_entered"], 2 / 3)
        self.assertIsNone(reach["issue"])
        self.assertEqual(reach["rule"], ACTIVITY_REACH_RULE)

    def test_equivalent_spellings_are_one_activity(self) -> None:
        """pkg/.A, pkg/A and pkg/pkg.A name one screen and must not count as three."""

        reach = self._reach(
            (f"{PACKAGE}/{PACKAGE}.Main",),
            [
                {
                    "before_activity": f"{PACKAGE}/.Main",
                    "after_activity": f"{PACKAGE}/Main",
                },
                {
                    "before_activity": f"{PACKAGE}/{PACKAGE}.Main",
                    "after_activity": f"{PACKAGE}/.Main",
                },
            ],
        )
        self.assertEqual(reach["entered"], 1)
        self.assertAlmostEqual(reach["share_entered"], 1.0)

    def test_an_undeclared_screen_cannot_raise_the_share(self) -> None:
        """It is reported separately rather than inflating a declared denominator."""

        reach = self._reach(
            (f"{PACKAGE}/{PACKAGE}.Main",),
            [
                {
                    "before_activity": f"{PACKAGE}/.Main",
                    "after_activity": f"{PACKAGE}/.Undeclared",
                }
            ],
        )
        self.assertEqual(reach["entered"], 1)
        self.assertAlmostEqual(reach["share_entered"], 1.0)
        self.assertEqual(
            reach["observed_undeclared"], [f"{PACKAGE}/{PACKAGE}.Undeclared"]
        )

    def test_a_foreign_screen_is_counted_as_foreign_not_entered(self) -> None:
        reach = self._reach(
            (f"{PACKAGE}/{PACKAGE}.Main",),
            [
                {
                    "before_activity": "com.android.chrome/.Main",
                    "after_activity": f"{PACKAGE}/.Main",
                }
            ],
        )
        self.assertEqual(reach["entered"], 1)
        self.assertEqual(reach["foreign_observations"], 1)

    def test_an_app_that_declares_nothing_has_no_share(self) -> None:
        """Zero of zero is undefined, and must not read as having reached nothing."""

        reach = self._reach((), [{"before_activity": f"{PACKAGE}/.Main"}])
        self.assertEqual(reach["declared"], 0)
        self.assertIsNone(reach["share_entered"])
        self.assertEqual(reach["issue"], "declared_activities_empty")

    def test_unreadable_route_discovery_is_reported_not_raised(self) -> None:
        """A summary is descriptive; it is still produced for a run without routes."""

        with TemporaryDirectory() as directory:
            reach = _activity_reach(
                _Store(Path(directory) / "absent.json", []), PACKAGE
            )
        self.assertIsNone(reach["declared"])
        self.assertIsNone(reach["share_entered"])
        self.assertIn("route_discovery_unreadable", reach["issue"])

    def test_nothing_entered_is_zero_and_not_an_issue(self) -> None:
        """The honest zero: activities were declared, and none was reached."""

        reach = self._reach((f"{PACKAGE}/{PACKAGE}.Main",), [])
        self.assertEqual(reach["declared"], 1)
        self.assertEqual(reach["entered"], 0)
        self.assertEqual(reach["share_entered"], 0.0)
        self.assertIsNone(reach["issue"])


if __name__ == "__main__":
    unittest.main()
