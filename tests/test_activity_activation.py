"""Non-exported activities are two thirds of an app, and we never reached them.

Measured across the 100 v2 campaign runs: 1,974 declared activities, of which
352 (17.8%) are exported and 1,298 (65.8%) are not. Only exported components are
legal targets unless forced routes are enabled, so rung 7 fired **zero times in
111 runs**. Ten apps in the dataset declare no exported activity at all, so for
those the activation mechanism had nothing it was allowed to touch. The 17.8%
figure independently reproduces Akinotcho et al. (ICSE 2025), who report fewer
than 20% of activities exported.

Forced activation stayed off for a measured reason: launching an activity bare
took Chess from 28.70% to 5.93%, because activities that read a data URI they
were never given drew a dead shell and the exploration budget drained into it.
The mitigation is `component_extras`, Delm-style mocked Intent context, produced
by `tools/activity_context`.

Two defects blocked that mitigation, and these tests cover both.

The first is a plumbing bug. `discover_component_extras` looked for the AndroLog
jar in `instrumenter_config[0]`. The campaign passed the smali policy first, that
file lists no `androlog` entry, and the `StopIteration` from `next()` was
swallowed by a bare `except`, so `component_extras` was empty for all 100 runs
while the jar sat in the second config the whole time.

The second is that static analysis cannot certify a launch as safe. The analyzer
reports no Intent reads whatsoever for Chess -- the one app known to fail this
exact way -- so an empty report proves nothing and the screen that actually
appears has to be checked instead. That is also how CovAgent (arXiv:2601.21253)
validates its activations.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from valordroid.models import StateObservation

_TOOLS = Path(__file__).resolve().parents[1] / "app" / "tools"


def _load_experiment_tool():
    """Import the campaign driver by path; it is a script, not a package member."""

    spec = importlib.util.spec_from_file_location(
        "_rce_under_test", _TOOLS / "run_coverage_experiment.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _config(directory: Path, name: str, *, androlog: Path | None, platforms: Path | None):
    toolchain = [{"name": "aapt", "path": "/usr/bin/aapt"}]
    if androlog is not None:
        toolchain.append({"name": "androlog", "path": str(androlog)})
    payload = {
        "toolchain": toolchain,
        "environment": {} if platforms is None else {"ANDROID_PLATFORMS": str(platforms)},
    }
    path = directory / name
    path.write_text(json.dumps(payload))
    return path


class ToolchainLookupTest(unittest.TestCase):
    """The AndroLog jar must be found wherever among the pinned configs it is."""

    def setUp(self) -> None:
        self.module = _load_experiment_tool()
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.jar = self.root / "androlog.jar"
        self.jar.write_bytes(b"not really a jar")
        self.platforms = self.root / "android-platforms"
        self.platforms.mkdir()

    def test_the_jar_is_found_in_the_second_config_not_only_the_first(self) -> None:
        """This is the campaign's exact argument order, and the bug it hid."""

        smali = _config(self.root, "instr-smali.json", androlog=None, platforms=None)
        v9 = _config(self.root, "instr-v9.json", androlog=self.jar, platforms=self.platforms)
        found = self.module._analyzer_toolchain([smali, v9])
        self.assertIsNotNone(
            found,
            "the campaign passed the smali policy first and the androlog jar was in "
            "the second config; searching only the first is what emptied "
            "component_extras for 100 runs",
        )
        self.assertEqual(found, (self.jar, self.platforms))

    def test_a_config_without_the_jar_alone_yields_nothing(self) -> None:
        smali = _config(self.root, "only-smali.json", androlog=None, platforms=None)
        self.assertIsNone(self.module._analyzer_toolchain([smali]))

    def test_a_recorded_path_that_does_not_exist_is_refused(self) -> None:
        """A stale pin must not be reported as usable."""

        stale = _config(
            self.root,
            "stale.json",
            androlog=self.root / "absent.jar",
            platforms=self.platforms,
        )
        self.assertIsNone(self.module._analyzer_toolchain([stale]))

    def test_unreadable_and_empty_inputs_do_not_raise(self) -> None:
        broken = self.root / "broken.json"
        broken.write_text("{not json")
        self.assertIsNone(self.module._analyzer_toolchain([]))
        self.assertIsNone(self.module._analyzer_toolchain([self.root / "absent.json"]))
        self.assertIsNone(self.module._analyzer_toolchain([broken]))

    def test_a_later_usable_config_survives_an_earlier_broken_one(self) -> None:
        broken = self.root / "first.json"
        broken.write_text("{not json")
        good = _config(self.root, "second.json", androlog=self.jar, platforms=self.platforms)
        self.assertEqual(
            self.module._analyzer_toolchain([broken, good]), (self.jar, self.platforms)
        )


class _Session:
    def __init__(self, package: str = "com.example.app") -> None:
        self.package = package


class _Observer:
    def __init__(self, candidates=(), raises: bool = False) -> None:
        self._candidates = tuple(candidates)
        self._raises = raises

    def candidates(self, observation):
        if self._raises:
            raise RuntimeError("candidate cache refused the observation")
        return self._candidates


class _Runner:
    """Just enough of AndroidRunner to exercise the dead-shell judgement."""

    from valordroid.runner import AndroidRunner

    _launch_landed_dead = AndroidRunner._launch_landed_dead

    def __init__(self, session=None, observer=None) -> None:
        self.session = session
        self.observer = observer


def _observation(activity: str | None) -> StateObservation:
    """A real observation. `StateObservation` requires `activity` to be exactly
    the unique resumed component, so an unresolved screen is None with no
    resumed activities rather than an empty string."""

    return StateObservation(
        state_id="s-1",
        activity=activity or None,
        resumed_activities=(activity,) if activity else (),
        hierarchy_sha256="0" * 64,
        structure_sha256="1" * 64,
        screenshot_sha256=None,
        identity_rule="test",
        observed_at=0.0,
    )


class DeadShellDetectionTest(unittest.TestCase):
    """A launch is judged by the screen it produced, not by static analysis."""

    def test_a_launch_with_nothing_to_interact_with_is_dead(self) -> None:
        """The Chess failure: nominally foreground, nothing drawn."""

        runner = _Runner(_Session(), _Observer(candidates=()))
        reason = runner._launch_landed_dead(
            "com.example.app/com.example.app.ImportActivity",
            _observation("com.example.app/com.example.app.ImportActivity"),
        )
        self.assertEqual(reason, "no_interactive_elements")

    def test_a_launch_that_left_the_app_is_dead(self) -> None:
        runner = _Runner(_Session(), _Observer(candidates=("tap",)))
        reason = runner._launch_landed_dead(
            "com.example.app/com.example.app.ShareActivity",
            _observation("com.android.chooser/com.android.chooser.Activity"),
        )
        self.assertEqual(reason, "left_the_app")

    def test_a_live_screen_is_kept(self) -> None:
        """The reach this mechanism exists to buy must not be thrown away."""

        runner = _Runner(_Session(), _Observer(candidates=("tap", "scroll")))
        self.assertIsNone(
            runner._launch_landed_dead(
                "com.example.app/com.example.app.SearchIndex",
                _observation("com.example.app/com.example.app.SearchIndex"),
            )
        )

    def test_a_single_interactive_element_is_still_live(self) -> None:
        """A sparse screen is a real screen; only zero is unambiguous."""

        runner = _Runner(_Session(), _Observer(candidates=("tap",)))
        self.assertIsNone(
            runner._launch_landed_dead(
                "com.example.app/com.example.app.AboutActivity",
                _observation("com.example.app/com.example.app.AboutActivity"),
            )
        )

    def test_an_unresolved_activity_is_ambiguous_not_dead(self) -> None:
        """A blank frame between transitions must not cost a target."""

        runner = _Runner(_Session(), _Observer(candidates=()))
        self.assertIsNone(
            runner._launch_landed_dead("com.example.app/x.Y", _observation(None))
        )

    def test_a_refused_candidate_cache_is_not_evidence_of_death(self) -> None:
        runner = _Runner(_Session(), _Observer(raises=True))
        self.assertIsNone(
            runner._launch_landed_dead(
                "com.example.app/x.Y", _observation("com.example.app/x.Y")
            )
        )

    def test_missing_adapters_never_raise(self) -> None:
        self.assertIsNone(
            _Runner(None, None)._launch_landed_dead("a/b", _observation("a/b"))
        )


if __name__ == "__main__":
    unittest.main()


class _Outcome:
    def __init__(self, details) -> None:
        self.details = details


class _Attempt:
    def __init__(self, before=None, after=None, details=None) -> None:
        if details is None:
            details = {}
            if before is not None:
                details["before_activity"] = before
            if after is not None:
                details["after_activity"] = after
        self.outcome = _Outcome(details)


class _Core:
    def __init__(self, attempts=()) -> None:
        self.attempts = tuple(attempts)


class _ReachRunner:
    """Just enough of AndroidRunner to exercise the entered-activity registry."""

    from valordroid.runner import AndroidRunner

    _entered_activities = AndroidRunner._entered_activities

    def __init__(self, attempts=(), package: str = "app") -> None:
        self.core = _Core(attempts)
        self._activity_package_name = package
        self._entered: set[str] = set()
        self._entered_scan_index = 0


class EnteredActivityRegistryTest(unittest.TestCase):
    """Live and offline reach share one canonical activity interpretation."""

    def test_both_sides_of_a_transition_are_recorded_canonically(self) -> None:
        runner = _ReachRunner(
            [_Attempt(before="app/.Main", after="app/Detail")]
        )
        self.assertEqual(
            runner._entered_activities(),
            frozenset({"app/app.Main", "app/app.Detail"}),
        )

    def test_equivalent_android_spellings_collapse_to_one_activity(self) -> None:
        runner = _ReachRunner(
            [
                _Attempt(before="app/.Main", after="app/Main"),
                _Attempt(before="app/app.Main", after="app/.Main"),
            ]
        )
        self.assertEqual(
            runner._entered_activities(), frozenset({"app/app.Main"})
        )

    def test_live_registry_uses_legacy_activity_key_and_ignores_foreign_owner(self) -> None:
        runner = _ReachRunner(
            [
                _Attempt(details={"activity": "app/.Legacy"}),
                _Attempt(details={"after_activity": "android/.ResolverActivity"}),
            ]
        )
        self.assertEqual(
            runner._entered_activities(), frozenset({"app/app.Legacy"})
        )

    def test_the_scan_is_incremental_and_accumulates(self) -> None:
        """Recovery consults this repeatedly, so it must not rescan every time."""

        runner = _ReachRunner([_Attempt(before="app/.Main", after="app/.Main")])
        self.assertEqual(
            runner._entered_activities(), frozenset({"app/app.Main"})
        )
        self.assertEqual(runner._entered_scan_index, 1)
        runner.core.attempts += (_Attempt(before="app/.Main", after="app/.Second"),)
        self.assertEqual(
            runner._entered_activities(),
            frozenset({"app/app.Main", "app/app.Second"}),
        )
        self.assertEqual(runner._entered_scan_index, 2)

    def test_missing_absent_and_malformed_details_are_skipped(self) -> None:
        runner = _ReachRunner(
            [
                _Attempt(details={}),
                _Attempt(details={"before_activity": None}),
                _Attempt(details={"after_activity": ""}),
                _Attempt(details=["not", "a", "mapping"]),
                _Attempt(before="app/.Real"),
            ]
        )
        self.assertEqual(
            runner._entered_activities(), frozenset({"app/app.Real"})
        )

    def test_no_attempts_yields_nothing(self) -> None:
        self.assertEqual(_ReachRunner([])._entered_activities(), frozenset())


class _CoverageContext:
    """Ranks targets in the order given, so ordering effects are visible."""

    def rank_targets(self, kind, targets, *, covered_unit_ids):
        return tuple(targets)


class _Coverage:
    covered_ids = frozenset()


class _TargetCore:
    def __init__(self, attempts=()) -> None:
        self.attempts = tuple(attempts)
        self.route_records = ()
        self.coverage = _Coverage()


class TargetOrderingTest(unittest.TestCase):
    """Component targets are single-use, so they must be spent on screens the
    GUI never reached rather than on ones it already stood in."""

    def _runner(self, forced, attempts):
        from valordroid.recovery import RecoveryRung
        from valordroid.runner import AndroidRunner

        runner = object.__new__(AndroidRunner)
        runner.core = _TargetCore(attempts)
        runner._activity_package_name = "app"
        runner.coverage_context = _CoverageContext()
        runner._entered = set()
        runner._entered_scan_index = 0

        class _Runtime:
            forced_components = tuple(forced)
            deep_links = ()
            exported_components = ()
            reset_seed_enabled = False

        runner.runtime = _Runtime()
        runner.session = None
        return runner, RecoveryRung

    def test_an_already_entered_activity_is_ordered_last(self) -> None:
        runner, RecoveryRung = self._runner(
            ["app/.Seen", "app/.Unseen"],
            [_Attempt(before="app/.Seen", after="app/.Seen")],
        )
        self.assertEqual(
            runner._untried_recovery_targets(RecoveryRung.FORCED_ACTIVATION),
            ("app/.Unseen", "app/.Seen"),
            "a target the run already stood in must not be preferred over one it "
            "has never reached",
        )

    def test_all_entered_still_returns_the_ranked_list(self) -> None:
        """Reporting the rung exhausted would be worse than trying again."""

        runner, RecoveryRung = self._runner(
            ["app/.A", "app/.B"],
            [_Attempt(before="app/.A", after="app/.B")],
        )
        self.assertEqual(
            runner._untried_recovery_targets(RecoveryRung.FORCED_ACTIVATION),
            ("app/.A", "app/.B"),
        )

    def test_nothing_entered_leaves_the_coverage_ranking_untouched(self) -> None:
        runner, RecoveryRung = self._runner(["app/.A", "app/.B"], [])
        self.assertEqual(
            runner._untried_recovery_targets(RecoveryRung.FORCED_ACTIVATION),
            ("app/.A", "app/.B"),
        )


class LaunchGateTest(unittest.TestCase):
    """Component launches wait for 60% of the budget, and that clock is a proxy.

    Its own rationale is that late in a run the frontier is exhausted so a launch
    costs nothing a tap would have earned. Exhaustion is the real condition;
    the clock only estimates when it arrives, and measured over the 98 v2 runs
    the estimate is poor -- 38 of them recorded their last coverage gain before
    the gate opened, a mean 32% of the budget.

    The consequence was that rung 7 fired twice across six 3600s runs, so forced
    activation could not be evaluated at all: the gate kept it from happening.
    """

    def _choices(self, *, allow_forced, launch_on_exhaustion, earned, trigger,
                 untried=None, plan=None):
        from valordroid.recovery import RecoveryRung
        from valordroid.runner import AndroidRunner

        runner = object.__new__(AndroidRunner)

        class _Config:
            pass

        config = _Config()
        config.allow_forced_routes = allow_forced
        config.launch_on_frontier_exhaustion = launch_on_exhaustion
        config.max_consecutive_back = 100
        config.max_seed_resets = 100
        config.model_assistance_enabled = False

        class _Recovery:
            failed_rungs = ()

        class _Core:
            pass

        core = _Core()
        core.config = config
        core.recovery = _Recovery()
        core.route_records = ()
        core.coverage = _Coverage()
        core.attempts = ()
        runner.core = core
        runner.gateway = None
        runner.coverage_context = None
        runner._consecutive_back = 0
        runner._entered = set()
        runner._entered_scan_index = 0

        class _Runtime:
            deep_links = ("app://one",)
            exported_components = ("app/.Exported",)
            forced_components = ("app/.Forced",)
            reset_seed_enabled = False

        runner.runtime = _Runtime()
        runner.session = None
        runner._component_launches_earned = lambda: earned

        class _Context:
            pass

        context = _Context()
        context.untried = untried
        context.plan = plan
        context.ranked = ()
        return set(runner._recovery_choices(context, trigger=trigger)), RecoveryRung

    def test_exported_escapes_early_on_exhaustion_as_it_always_did(self) -> None:
        choices, RecoveryRung = self._choices(
            allow_forced=False, launch_on_exhaustion=False, earned=False,
            trigger="screen_exhausted",
        )
        self.assertIn(RecoveryRung.EXPORTED_INTENT, choices)
        self.assertNotIn(RecoveryRung.DEEP_LINK, choices)

    def test_the_option_releases_deep_links_and_forcing_on_exhaustion(self) -> None:
        choices, RecoveryRung = self._choices(
            allow_forced=True, launch_on_exhaustion=True, earned=False,
            trigger="screen_exhausted",
        )
        self.assertIn(RecoveryRung.DEEP_LINK, choices)
        self.assertIn(RecoveryRung.FORCED_ACTIVATION, choices)
        self.assertIn(RecoveryRung.EXPORTED_INTENT, choices)

    def test_a_plateau_with_nothing_left_also_releases_the_gate(self) -> None:
        """Widened deliberately. This assertion used to be the opposite.

        It previously required the trigger to be `screen_exhausted`, on the
        reading that `coverage_plateau` means the frontier is not spent. But the
        scenario here has no untried action and no navigation plan, which is what
        "spent" means; only the trigger's name differed.

        Gating on the name made the escape unreachable in practice. Over the v2
        campaign the triggers were 93% `coverage_plateau` against 4%
        `screen_exhausted`, rung 7 fired 0 times in 111 runs, and in the follow-up
        A/B it fired only after the 60% clock opened -- the last 24 minutes of a
        3600s run. 38 of 98 v2 runs recorded their last coverage gain before the
        gate opened, a mean 32% of budget with nothing left to gain.

        The protection that matters is unchanged: an untried action or a recorded
        plan still holds the gate shut, asserted below.
        """
        choices, RecoveryRung = self._choices(
            allow_forced=True, launch_on_exhaustion=True, earned=False,
            trigger="coverage_plateau",
        )
        self.assertIn(RecoveryRung.DEEP_LINK, choices)
        self.assertIn(RecoveryRung.FORCED_ACTIVATION, choices)
        self.assertIn(RecoveryRung.EXPORTED_INTENT, choices)
    def test_a_plateau_with_an_untried_action_keeps_the_gate_shut(self) -> None:
        """The widened trigger must not weaken the exhaustion test itself."""
        choices, RecoveryRung = self._choices(
            allow_forced=True, launch_on_exhaustion=True, earned=False,
            trigger="coverage_plateau", untried="some-candidate",
        )
        self.assertNotIn(RecoveryRung.DEEP_LINK, choices)
        self.assertNotIn(RecoveryRung.FORCED_ACTIVATION, choices)
    def test_a_transient_cooldown_never_releases_the_gate(self) -> None:
        """`temporarily_no_eligible` means a candidate returns after a cooldown."""
        choices, RecoveryRung = self._choices(
            allow_forced=True, launch_on_exhaustion=True, earned=False,
            trigger="temporarily_no_eligible",
        )
        self.assertNotIn(RecoveryRung.DEEP_LINK, choices)
        self.assertNotIn(RecoveryRung.FORCED_ACTIVATION, choices)
        self.assertNotIn(RecoveryRung.EXPORTED_INTENT, choices)
    def test_an_untried_local_action_keeps_the_gate_shut(self) -> None:
        choices, RecoveryRung = self._choices(
            allow_forced=True, launch_on_exhaustion=True, earned=False,
            trigger="screen_exhausted", untried="some-candidate",
        )
        self.assertNotIn(RecoveryRung.FORCED_ACTIVATION, choices)

    def test_a_navigation_plan_keeps_the_gate_shut(self) -> None:
        """A recorded route to useful work beats launching."""

        choices, RecoveryRung = self._choices(
            allow_forced=True, launch_on_exhaustion=True, earned=False,
            trigger="screen_exhausted", plan="a-plan",
        )
        self.assertNotIn(RecoveryRung.FORCED_ACTIVATION, choices)

    def test_forcing_stays_configuration_gated_even_on_exhaustion(self) -> None:
        """The option changes when a launch may happen, never whether it is
        permitted. A coverage number must still be able to mean 'reachable'."""

        choices, RecoveryRung = self._choices(
            allow_forced=False, launch_on_exhaustion=True, earned=False,
            trigger="screen_exhausted",
        )
        self.assertNotIn(RecoveryRung.FORCED_ACTIVATION, choices)
        self.assertIn(RecoveryRung.DEEP_LINK, choices)

    def test_once_the_clock_is_earned_everything_is_available(self) -> None:
        choices, RecoveryRung = self._choices(
            allow_forced=True, launch_on_exhaustion=False, earned=True,
            trigger="coverage_plateau",
        )
        self.assertIn(RecoveryRung.DEEP_LINK, choices)
        self.assertIn(RecoveryRung.FORCED_ACTIVATION, choices)
