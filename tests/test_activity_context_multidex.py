"""The intent-context analyzer has to read every dex, or it reports nothing.

`IntentContextAnalyzer` asks Soot for the application classes and keeps the ones
whose superclass chain reaches `android.app.Activity`. It never set
`process_multiple_dex`, and Soot reads only `classes.dex` without it. Every APK in
this dataset is multi-dex, so the analysis saw a fraction of each app.

Measured on the shipped Chess APK, which carries twelve dex files:

    single dex   3,324 classes    4 activities   all AndroidX base classes
    multi dex    3,858 classes   24 activities   plus the app's own

The four base classes read nothing with a literal key and call no `getData`, so
the report came back `{"activities":{}}`. That is what `component_extras: {}` in
all 111 published V2 manifests records: not an app that reads nothing from its
Intent, but an analysis that never saw the app. With the option set, the three
activities this module's own docstring names -- PlayActivity, AdvancedActivity and
ImportActivity, each reading a data URI -- are found.

`analyze` deliberately swallows every failure and returns an empty report, so the
difference between "cannot run" and "found nothing" is invisible at runtime. That
is why these checks are here rather than left to a campaign to notice.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1] / "app" / "tools"
_ANALYZER = _TOOLS / "activity_context"
_SOURCE = _ANALYZER / "IntentContextAnalyzer.java"
_COMPILED = _ANALYZER / "IntentContextAnalyzer.class"
_APK = Path(__file__).resolve().parents[1] / "dataset" / "apks" / "Chess.apk"

# Soot's own setter name, which appears in the class constant pool once the call
# is compiled in. Checking the bytes catches a stale checked-in class as well as
# a source regression, and the class is what actually runs.
_OPTION = b"set_process_multiple_dex"


def _toolchain() -> tuple[Path, Path] | None:
    """The AndroLog jar and android-platforms directory, or None."""

    jar = os.environ.get("VD_ANDROLOG_JAR")
    platforms = os.environ.get("VD_ANDROID_PLATFORMS")
    candidates = [
        (Path(jar), Path(platforms)) if jar and platforms else None,
        (
            Path.home() / "test-jacoco/AndroLog/target/androlog-0.1-jar-with-dependencies.jar",
            Path.home() / "android-platforms",
        ),
    ]
    for candidate in candidates:
        if candidate and candidate[0].is_file() and candidate[1].is_dir():
            return candidate
    return None


class AnalyzerReadsEveryDexTest(unittest.TestCase):
    """Offline checks. These run everywhere and need no Android toolchain."""

    def test_the_source_asks_soot_for_every_dex(self) -> None:
        source = _SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            "set_process_multiple_dex(true)",
            source.replace(" ", ""),
            "without this option Soot reads only classes.dex and the analysis "
            "reports an empty result for every multi-dex APK in the dataset",
        )

    def test_the_compiled_class_carries_the_call(self) -> None:
        """A stale class is as harmful as a missing option, and just as quiet."""

        self.assertTrue(_COMPILED.is_file(), f"{_COMPILED} is missing")
        self.assertIn(
            _OPTION,
            _COMPILED.read_bytes(),
            "the checked-in IntentContextAnalyzer.class predates the multi-dex "
            "option; recompile it, because the class is what actually runs",
        )

    def test_the_dataset_apk_really_is_multi_dex(self) -> None:
        """Pins the premise, so this suite explains itself if the APK changes."""

        if not _APK.is_file():
            self.skipTest("dataset APKs are not present")
        import zipfile

        with zipfile.ZipFile(_APK) as archive:
            dexes = [n for n in archive.namelist() if n.endswith(".dex")]
        self.assertGreater(
            len(dexes), 1, f"expected a multi-dex APK, found {dexes}"
        )


@unittest.skipUnless(_APK.is_file(), "dataset APKs are not present")
@unittest.skipUnless(shutil.which("java"), "java is not available")
@unittest.skipUnless(_toolchain(), "the AndroLog jar or android-platforms is absent")
class AnalyzerFindsTheAppsOwnActivitiesTest(unittest.TestCase):
    """Runs the real analyzer. Skipped wherever the Android toolchain is absent."""

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(_TOOLS))
        import activity_context

        jar, platforms = _toolchain()
        cls.report = activity_context.analyze(
            _APK, androlog_jar=jar, platforms=platforms, timeout_seconds=900.0
        )
        cls.module = activity_context

    def test_the_report_is_not_empty(self) -> None:
        activities = self.report.get("activities") or {}
        self.assertTrue(
            activities,
            "the analyzer returned no activities; `analyze` hides its failures "
            "behind an empty report, so check java, the jar and the platforms",
        )

    def test_the_apps_own_activities_are_seen_not_only_framework_bases(self) -> None:
        """Single-dex mode found only AndroidX bases. Those are not the app."""

        activities = self.report.get("activities") or {}
        owned = [name for name in activities if name.startswith("jwtc.android.chess")]
        self.assertTrue(
            owned,
            f"only framework classes were analysed: {sorted(activities)}",
        )

    def test_the_three_documented_activities_read_a_data_uri(self) -> None:
        """The module docstring names these, measured at 28.70% -> 5.93% blind."""

        activities = self.report.get("activities") or {}
        for name in (
            "jwtc.android.chess.play.PlayActivity",
            "jwtc.android.chess.tools.AdvancedActivity",
            "jwtc.android.chess.tools.ImportActivity",
        ):
            with self.subTest(activity=name):
                self.assertIn(name, activities)
                self.assertTrue(
                    activities[name].get("reads_data_uri"),
                    f"{name} reads a data URI and launching it without one is "
                    "what drained the exploration budget",
                )

    def test_a_data_uri_turns_the_report_into_component_extras(self) -> None:
        extras = self.module.component_extras(
            self.report,
            package="jwtc.android.chess",
            data_uri="file:///sdcard/valordroid/fixture.pgn",
        )
        self.assertGreaterEqual(len(extras), 3, extras)
        for component, entries in extras.items():
            self.assertTrue(component.startswith("jwtc.android.chess/"))
            self.assertTrue(entries)


if __name__ == "__main__":
    unittest.main()
