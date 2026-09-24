"""Recovery must not spend the run exploring somebody else's app.

Ordinary selection already treats a foreign screen as an obstacle. The recovery
ladder had no such rule, and because a coverage stall keeps re-entering recovery,
a run could spend nearly all of its actions outside the app under test: And-Bible
executed 848 of 851 attempts through the ladder with 96.8% of them in the system
Contacts editor, Gallery and launcher, whose classes contribute no units at all.

These tests pin the two rules that close that gap: rung 1 may only offer the
app's own controls, and a foreign foreground is answered by returning to the app
rather than by exploring what happens to be in front.
"""

from __future__ import annotations

import unittest

from valordroid.models import ActionSpec, StateObservation
from valordroid.runner import AndroidRunner


PACKAGE = "com.example.app"


def _observation(activity: str | None) -> StateObservation:
    return StateObservation(
        state_id="s" * 64,
        observed_at=0.0,
        activity=activity,
        candidate_ids=(),
        hierarchy_sha256="h" * 64,
        screenshot_sha256=None,
        resumed_activities=((activity,) if activity else ()),
        structure_sha256="t" * 64,
        identity_rule="test",
    )


class _Session:
    package = PACKAGE


def _runner(max_actions: int = 20000) -> AndroidRunner:
    runner = AndroidRunner.__new__(AndroidRunner)
    runner.session = _Session()
    runner.runtime = type("R", (), {"max_actions": max_actions})()
    runner._app_relaunches = 0
    runner._relaunches_without_progress = 0
    return runner


class OutsideAppTest(unittest.TestCase):
    def test_the_apps_own_activity_is_not_foreign(self):
        runner = _runner()
        self.assertFalse(runner._outside_app(_observation(f"{PACKAGE}/.MainActivity")))

    def test_the_launcher_and_other_apps_are_foreign(self):
        runner = _runner()
        for activity in (
            "com.android.launcher3/.uioverrides.QuickstepLauncher",
            "com.android.contacts/.activities.ContactEditorActivity",
            "com.android.gallery3d/.app.GalleryActivity",
            "org.chromium.webview_shell/.WebViewBrowserActivity",
        ):
            self.assertTrue(
                runner._outside_app(_observation(activity)), activity
            )

    def test_an_unresolved_activity_is_not_treated_as_foreign(self):
        # A blank frame is ambiguous, and calling it foreign would let one bad
        # observation trigger a relaunch storm.
        runner = _runner()
        self.assertFalse(runner._outside_app(_observation(None)))

    def test_a_package_that_merely_shares_a_prefix_is_foreign(self):
        runner = _runner()
        self.assertTrue(
            runner._outside_app(_observation(f"{PACKAGE}.debug/.MainActivity"))
        )


class RelaunchBudgetTest(unittest.TestCase):
    def test_the_budget_scales_with_the_action_budget(self):
        runner = _runner(max_actions=20000)
        runner._app_relaunches = 999
        self.assertTrue(runner._relaunches_remaining())
        runner._app_relaunches = 1000
        self.assertFalse(runner._relaunches_remaining())

    def test_a_small_run_still_gets_a_usable_floor(self):
        runner = _runner(max_actions=10)
        runner._app_relaunches = 19
        self.assertTrue(runner._relaunches_remaining())
        runner._app_relaunches = 20
        self.assertFalse(runner._relaunches_remaining())

    def test_an_app_that_never_holds_the_foreground_stops_relaunching(self):
        # Otherwise the loop that returns to the app becomes the whole run.
        runner = _runner(max_actions=100)
        seen = 0
        while runner._relaunches_remaining():
            runner._app_relaunches += 1
            seen += 1
            self.assertLess(seen, 1000, "relaunching is not bounded")
        self.assertEqual(seen, 20)


class FailedBackObservationTest(unittest.TestCase):
    def test_back_observation_error_still_falls_back_to_one_relaunch(self):
        before = _observation("com.android.documentsui/.FilesActivity")
        returned = _observation(f"{PACKAGE}/.MainActivity")

        class _Adb:
            def __init__(self):
                self.back_calls = 0

            def shell(self, *_args, **_kwargs):
                self.back_calls += 1

        class _SessionWithLaunch:
            package = PACKAGE

            def __init__(self):
                self.adb = _Adb()
                self.launch_calls = 0
                self.foreground_calls = 0

            def launch(self, *, deadline_monotonic=None):
                self.launch_calls += 1

            def foreground(self, *, deadline_monotonic=None):
                # Back leaves the file picker in front, which is the ordinary
                # case: over 10 apps at 600s, Back returned the app 3 times in
                # A-Photo's 45 containments and never in ODK-Collect's 13.
                self.foreground_calls += 1
                return (("com.android.documentsui/.FilesActivity",),
                        "com.android.documentsui/.FilesActivity")

        class _Observer:
            latest = None

            def __init__(self):
                self.observe_calls = 0

            def observe(self, *, deadline_monotonic=None):
                self.observe_calls += 1
                return StateObservation(
                    state_id=returned.state_id,
                    observed_at=__import__("time").time() + 0.001,
                    activity=returned.activity,
                    resumed_activities=returned.resumed_activities,
                    hierarchy_sha256=returned.hierarchy_sha256,
                    screenshot_sha256=returned.screenshot_sha256,
                    structure_sha256=returned.structure_sha256,
                    identity_rule=returned.identity_rule,
                )

        session = _SessionWithLaunch()
        observer = _Observer()
        runner = AndroidRunner.__new__(AndroidRunner)
        runner.session = session
        runner.observer = observer
        runner.core = type(
            "Core", (), {"config": type("Config", (), {"stall_seconds": 1.0})()}
        )()
        runner.runtime = type(
            "Runtime", (), {"post_action_delay_seconds": 0.0}
        )()
        runner._consecutive_foreign_actions = 1
        runner._lifecycle = lambda *_args, **_kwargs: None

        runner.BACK_SETTLE_GRACE_SECONDS = 0.0

        after = runner._return_to_app(before)

        self.assertEqual(after.activity, f"{PACKAGE}/.MainActivity")
        self.assertEqual(session.adb.back_calls, 1)
        self.assertEqual(session.launch_calls, 1)
        # One observation, not two. A Back that did not return the app is now
        # detected from the resumed set, so no hierarchy is dumped for a screen
        # the containment is about to abandon.
        self.assertEqual(observer.observe_calls, 1)
        self.assertGreaterEqual(session.foreground_calls, 1)


if __name__ == "__main__":
    unittest.main()


class UnobservableScreenTest(unittest.TestCase):
    """An app whose screen can never be dumped must not consume the run.

    Trackbook's map redraws continuously, so UIAutomator answers "could not get
    idle state" for its only activity while the launcher dumps fine. Returning to
    the app then observes the launcher, concludes it is outside the app, and
    relaunches. One run did that 57 times and completed four attempts.
    """

    def test_relaunching_stops_once_it_is_shown_not_to_work(self):
        runner = _runner(max_actions=20000)
        launcher = _observation("com.android.launcher3/.uioverrides.QuickstepLauncher")
        rounds = 0
        while runner._relaunches_remaining():
            runner._app_relaunches += 1
            runner._relaunches_without_progress += 1
            runner._note_observation_ownership(launcher)   # never lands in the app
            rounds += 1
            self.assertLess(rounds, 50, "unproductive relaunching is unbounded")
        self.assertEqual(rounds, runner.UNPRODUCTIVE_RELAUNCH_LIMIT)

    def test_landing_in_the_app_restores_the_full_budget(self):
        runner = _runner(max_actions=20000)
        runner._relaunches_without_progress = runner.UNPRODUCTIVE_RELAUNCH_LIMIT
        self.assertFalse(runner._relaunches_remaining())
        runner._note_observation_ownership(_observation(f"{PACKAGE}/.MainActivity"))
        self.assertTrue(runner._relaunches_remaining())

    def test_a_foreign_observation_does_not_restore_the_budget(self):
        runner = _runner(max_actions=20000)
        runner._relaunches_without_progress = runner.UNPRODUCTIVE_RELAUNCH_LIMIT
        runner._note_observation_ownership(_observation("com.android.contacts/.X"))
        self.assertFalse(runner._relaunches_remaining())


class FreshBoundedContainmentTest(unittest.TestCase):
    def _runner(self, observer, session) -> AndroidRunner:
        runner = AndroidRunner.__new__(AndroidRunner)
        runner.session = session
        runner.observer = observer
        runner.core = type(
            "Core", (), {"config": type("Config", (), {"stall_seconds": 2.0})()}
        )()
        runner.runtime = type(
            "Runtime", (), {"post_action_delay_seconds": 0.0}
        )()
        runner._consecutive_foreign_actions = 1
        runner._lifecycle = lambda *_args, **_kwargs: None
        return runner

    def test_stale_cached_aut_frame_cannot_prove_containment(self) -> None:
        before = _observation("com.android.documentsui/.FilesActivity")
        stale = _observation(f"{PACKAGE}/.MainActivity")

        class Adb:
            def shell(self, *_args, **_kwargs):
                return None

        class Session:
            package = PACKAGE

            def __init__(self):
                self.adb = Adb()
                self.launch_calls = 0

            def launch(self, *, deadline_monotonic=None):
                self.launch_calls += 1

            def foreground(self, *, deadline_monotonic=None):
                return (("com.android.documentsui/.FilesActivity",),
                        "com.android.documentsui/.FilesActivity")

        class Observer:
            latest = type("Latest", (), {"observation": stale})()

            def __init__(self):
                self.observe_calls = 0

            def observe(self, *, deadline_monotonic=None):
                self.observe_calls += 1
                raise RuntimeError("fresh capture unavailable")

        session = Session()
        observer = Observer()
        runner = self._runner(observer, session)
        runner.BACK_SETTLE_GRACE_SECONDS = 0.0
        after = runner._return_to_app(before)
        self.assertEqual(after, before)
        self.assertEqual(session.launch_calls, 1)
        # Only the relaunch spends an observation now; the cached frame still
        # cannot stand in for it.
        self.assertEqual(observer.observe_calls, 1)

    def test_back_and_relaunch_receive_one_shared_deadline(self) -> None:
        before = _observation("com.android.documentsui/.FilesActivity")
        deadlines = []

        class Adb:
            def shell(self, *_args, timeout=None, **_kwargs):
                self.timeout = timeout
                return None

        class Session:
            package = PACKAGE

            def __init__(self):
                self.adb = Adb()

            def launch(self, *, deadline_monotonic=None):
                deadlines.append(deadline_monotonic)

            def foreground(self, *, deadline_monotonic=None):
                deadlines.append(deadline_monotonic)
                return ((), None)

        class Observer:
            latest = None

            def observe(self, *, deadline_monotonic=None):
                deadlines.append(deadline_monotonic)
                raise RuntimeError("not observable")

        runner = self._runner(Observer(), Session())
        runner.BACK_SETTLE_GRACE_SECONDS = 0.0
        runner._return_to_app(before)
        # The cheap probe is bound by the same deadline as the expensive stages,
        # so it cannot become a way to spend containment time unaccounted for.
        self.assertEqual(len(deadlines), 3)
        self.assertIsNotNone(deadlines[0])
        self.assertTrue(all(value == deadlines[0] for value in deadlines))


class LateContainmentRegressionTest(unittest.TestCase):
    def test_observer_that_ignores_deadline_cannot_prove_return(self) -> None:
        import time

        before = StateObservation(
            state_id="foreign",
            observed_at=time.time() - 1.0,
            activity="com.android.documentsui/.FilesActivity",
            resumed_activities=("com.android.documentsui/.FilesActivity",),
        )

        class Adb:
            def shell(self, *_args, **_kwargs):
                return None

        class Session:
            package = PACKAGE

            def __init__(self):
                self.adb = Adb()
                self.launch_calls = 0

            def launch(self, *, deadline_monotonic=None):
                self.launch_calls += 1

        class Observer:
            latest = None

            def __init__(self):
                self.deadlines = []

            def observe(self, *, deadline_monotonic=None):
                self.deadlines.append(deadline_monotonic)
                time.sleep(0.02)
                component = f"{PACKAGE}/.MainActivity"
                return StateObservation(
                    state_id="late-aut",
                    observed_at=time.time(),
                    activity=component,
                    resumed_activities=(component,),
                )

        runner = AndroidRunner.__new__(AndroidRunner)
        runner.FOREIGN_CONTAINMENT_TIMEOUT_SECONDS = 0.01
        runner.session = Session()
        runner.observer = Observer()
        runner.core = type(
            "Core", (), {"config": type("Config", (), {"stall_seconds": 1.0})()}
        )()
        runner.runtime = type(
            "Runtime", (), {"post_action_delay_seconds": 0.0}
        )()
        runner._consecutive_foreign_actions = 1
        lifecycle = []
        runner._lifecycle = lambda phase, event, status, **kwargs: lifecycle.append(
            (phase, event, status, kwargs)
        )

        after = runner._return_to_app(before)

        self.assertEqual(after, before)
        self.assertEqual(len(runner.observer.deadlines), 1)
        returned = [item for item in lifecycle if item[1] == "returned_to_app"]
        self.assertEqual(len(returned), 1)
        self.assertEqual(returned[0][2].value, "failed")
        self.assertFalse(returned[0][3]["details"]["fresh_observation"])


class CheapContainmentProbeTest(unittest.TestCase):
    """Containment should spend its budget returning the app, not observing.

    `_return_to_app` dispatched Back and then paid for a full observation to find
    out whether Back had worked. Measured over 10 apps at 600s, it usually had
    not: A-Photo-Manager returned by Back 3 times in 45 containments and by
    relaunch 39, ODK-Collect 0 of 13, Omni-Notes 3 of 12. So the common path bought
    a hierarchy dump of a screen it was about to abandon, inside a 15s deadline it
    then ran out of -- ODK's median containment was exactly the 15.0s cap and 7 of
    13 returned nothing at all. Containment cost 44% of A-Photo's wall clock and
    19% of ODK's.

    Whether the app is in front is a question about the resumed set, which is one
    `dumpsys` call. These tests pin that the cheap question is asked first, that
    asking it does not cost Back its chance to work, and that a verdict which
    needs evidence still requires the observation.
    """

    def _parts(self, *, foreground_sequence, observe_activity=None):
        sequence = list(foreground_sequence)

        class Adb:
            def __init__(self):
                self.back_calls = 0

            def shell(self, *_args, **_kwargs):
                self.back_calls += 1
                return None

        class Session:
            package = PACKAGE

            def __init__(self):
                self.adb = Adb()
                self.launch_calls = 0
                self.foreground_calls = 0

            def launch(self, *, deadline_monotonic=None):
                self.launch_calls += 1

            def foreground(self, *, deadline_monotonic=None):
                self.foreground_calls += 1
                activity = (
                    sequence.pop(0) if sequence else "com.android.documentsui/.Files"
                )
                return ((activity,), activity)

        class Observer:
            latest = None

            def __init__(self):
                self.observe_calls = 0

            def observe(self, *, deadline_monotonic=None):
                self.observe_calls += 1
                if observe_activity is None:
                    raise RuntimeError("not observable")
                return StateObservation(
                    state_id="a" * 64,
                    observed_at=__import__("time").time() + 0.001,
                    activity=observe_activity,
                    candidate_ids=(),
                    hierarchy_sha256="h" * 64,
                    screenshot_sha256=None,
                    resumed_activities=(observe_activity,),
                    structure_sha256="t" * 64,
                    identity_rule="test",
                )

        session = Session()
        observer = Observer()
        runner = AndroidRunner.__new__(AndroidRunner)
        runner.session = session
        runner.observer = observer
        runner.core = type(
            "Core", (), {"config": type("Config", (), {"stall_seconds": 1.0})()}
        )()
        runner.runtime = type("Runtime", (), {"post_action_delay_seconds": 0.0})()
        runner._consecutive_foreign_actions = 1
        runner._lifecycle = lambda *_args, **_kwargs: None
        return runner, session, observer

    def test_a_back_that_worked_still_observes_once_and_never_relaunches(self):
        runner, session, observer = self._parts(
            foreground_sequence=[f"{PACKAGE}/.MainActivity"],
            observe_activity=f"{PACKAGE}/.MainActivity",
        )
        after = runner._return_to_app(_observation("com.android.documentsui/.Files"))
        self.assertEqual(after.activity, f"{PACKAGE}/.MainActivity")
        self.assertEqual(session.launch_calls, 0)
        self.assertEqual(observer.observe_calls, 1)

    def test_a_back_that_lands_late_is_still_credited(self):
        """The probe must be no less patient than the observation it replaced."""

        runner, session, observer = self._parts(
            foreground_sequence=[
                "com.android.documentsui/.Files",
                "com.android.documentsui/.Files",
                f"{PACKAGE}/.MainActivity",
            ],
            observe_activity=f"{PACKAGE}/.MainActivity",
        )
        after = runner._return_to_app(_observation("com.android.documentsui/.Files"))
        self.assertEqual(after.activity, f"{PACKAGE}/.MainActivity")
        self.assertEqual(session.launch_calls, 0)
        self.assertGreaterEqual(session.foreground_calls, 3)

    def test_a_back_that_never_lands_costs_no_observation(self):
        runner, session, observer = self._parts(
            foreground_sequence=[], observe_activity=f"{PACKAGE}/.MainActivity"
        )
        runner.BACK_SETTLE_GRACE_SECONDS = 0.0
        runner._return_to_app(_observation("com.android.documentsui/.Files"))
        self.assertEqual(session.adb.back_calls, 1)
        self.assertEqual(session.launch_calls, 1)
        self.assertEqual(observer.observe_calls, 1)

    def test_the_probe_alone_cannot_prove_containment(self):
        """A resumed set is not evidence: the run still needs an observation.

        The probe decides whether observing is worth doing. It may not decide the
        `returned` verdict, because that is recorded as evidence and has to be
        recomputable from a retained frame.
        """

        runner, session, observer = self._parts(
            foreground_sequence=[f"{PACKAGE}/.MainActivity"] * 6,
            observe_activity=None,
        )
        before = _observation("com.android.documentsui/.Files")
        after = runner._return_to_app(before)
        self.assertEqual(after, before)
        self.assertEqual(session.launch_calls, 1)

    def test_an_unresolvable_foreground_is_not_assumed_to_be_ours(self):
        runner, _session, _observer = self._parts(foreground_sequence=[])
        self.assertFalse(runner._foreground_is_owned((), None))

    def test_the_resumed_set_is_read_when_no_single_activity_resolves(self):
        runner, _session, _observer = self._parts(foreground_sequence=[])
        self.assertTrue(
            runner._foreground_is_owned(
                ("com.android.systemui/.Recents", f"{PACKAGE}/.MainActivity"), None
            )
        )
        self.assertFalse(
            runner._foreground_is_owned(("com.android.systemui/.Recents",), None)
        )

    def test_a_resolved_foreign_activity_outranks_a_stale_resumed_entry(self):
        runner, _session, _observer = self._parts(foreground_sequence=[])
        self.assertFalse(
            runner._foreground_is_owned(
                (f"{PACKAGE}/.MainActivity",), "com.android.documentsui/.Files"
            )
        )
