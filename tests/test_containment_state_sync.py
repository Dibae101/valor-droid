"""A failed containment must not hand back a state the observer no longer holds.

`_return_to_app` tries Back first. Back can observe the screen successfully and
still fail the return test, because the frame it captured belongs to a foreign
package. That capture has already become the observer's latest. If the following
relaunch then fails, the method returned `before` anyway, and the exploration
loop asked `observer.candidates(before)` for a state the observer no longer has
candidates for. That raises "candidate request does not match the latest
observation", which is classified as an unrecoverable runtime error and aborts
the run.

Observed in the smoke campaign on Trackbook: Back observed
`com.android.quicksearchbox/.SearchActivity`, the relaunch dump timed out, and a
run that had already dispatched four actions and was collecting coverage was
recorded as an invalid cell. Its covered units were discarded because of an
observation failure during recovery.

The opposite case still has to hold: a capture that predates the containment
attempt proves nothing about where the screen is now, so it must not stand in
for a fresh observation. `test_dynamic_routes.ReturnToAppFailurePathTest` pins
that, and the two are distinguished here by whether the capture is newer than
the observation containment started from.
"""

from __future__ import annotations

import unittest

from valordroid.models import StateObservation
from valordroid.runner import AndroidRunner

PACKAGE = "com.example.app"
FOREIGN = "com.android.quicksearchbox/.SearchActivity"


def _observation(
    activity: str | None, *, observed_at: float, state_id: str
) -> StateObservation:
    return StateObservation(
        state_id=state_id,
        observed_at=observed_at,
        activity=activity,
        candidate_ids=(),
        hierarchy_sha256="h" * 64,
        screenshot_sha256=None,
        resumed_activities=((activity,) if activity else ()),
        structure_sha256="t" * 64,
        identity_rule="test",
    )


class _Captured:
    def __init__(self, observation: StateObservation) -> None:
        self.observation = observation
        self.candidates = ()


class _Observer:
    """Publishes each capture as its latest, exactly as the real observer does.

    `latest` is a property on the real observer, so it is a plain attribute here
    and never a call.
    """

    def __init__(
        self,
        published: list[StateObservation],
        *,
        initial: StateObservation,
        raise_after_publishing: int = 0,
    ):
        self._published = list(published)
        self._raise_after_publishing = raise_after_publishing
        # `before` reached the caller through this observer, so its capture is
        # already the latest one when containment begins.
        self.latest: _Captured | None = _Captured(initial)
        self.observe_calls = 0

    def observe(self, *, deadline_monotonic: float | None = None) -> StateObservation:
        self.observe_calls += 1
        if not self._published:
            raise RuntimeError("UIAutomator compressed dump did not return")
        observation = self._published.pop(0)
        # Publication happens before the caller can judge freshness, which is
        # the whole reason the caller's `before` can go stale.
        self.latest = _Captured(observation)
        if 0 < self._raise_after_publishing <= self.observe_calls:
            raise RuntimeError("UIAutomator produced no hierarchy file")
        return observation

    def candidates(self, state: StateObservation):
        """The real guard: candidates belong to the latest observation only."""

        if self.latest is None or self.latest.observation.state_id != state.state_id:
            raise ValueError("candidate request does not match the latest observation")
        return self.latest.candidates


class _Adb:
    def shell(self, *arguments, timeout=None, check=True):
        return None


class _Session:
    package = PACKAGE

    def __init__(self) -> None:
        self.adb = _Adb()
        self.launches = 0

    def launch(self, *, deadline_monotonic: float | None = None):
        self.launches += 1

    def foreground(self, *, deadline_monotonic: float | None = None):
        """Report the app in front, so containment goes on to observe.

        Containment asks the resumed set before paying for a hierarchy dump, and
        these tests are about what happens when the dump disagrees with it: the
        screen moved between the two, so a Back that looked successful publishes
        a foreign frame anyway. That race is what left the exploration loop
        holding a state the observer had no candidates for, and it is only
        reachable when the cheap probe is optimistic.
        """

        return ((f"{PACKAGE}/.MainActivity",), f"{PACKAGE}/.MainActivity")


def _runner(observer: _Observer) -> AndroidRunner:
    runner = AndroidRunner.__new__(AndroidRunner)
    runner.session = _Session()
    runner.observer = observer
    runner.runtime = type(
        "R", (), {"max_actions": 20000, "post_action_delay_seconds": 0.0}
    )()
    runner.core = type(
        "C", (), {"config": type("K", (), {"stall_seconds": 5.0})()}
    )()
    runner._consecutive_foreign_actions = 0
    runner._app_relaunches = 0
    runner._relaunches_without_progress = 0
    runner._lifecycle = lambda *a, **k: None
    return runner


class ContainmentLeavesTheLoopAbleToContinueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.before = _observation(
            FOREIGN, observed_at=100.0, state_id="a" * 64
        )

    def test_the_reproduction_back_observes_a_foreign_frame_then_relaunch_fails(
        self,
    ) -> None:
        """Trackbook, exactly: Back succeeds on a foreign screen, relaunch dies."""

        back_frame = _observation(FOREIGN, observed_at=101.0, state_id="b" * 64)
        observer = _Observer([back_frame], initial=self.before)
        runner = _runner(observer)

        after = runner._return_to_app(self.before)

        self.assertEqual(observer.observe_calls, 2, "Back then relaunch")
        self.assertEqual(
            after.state_id,
            "b" * 64,
            "containment returned a state the observer no longer holds",
        )
        # The consequence that matters: the loop can ask for candidates instead
        # of raising and aborting the run.
        observer.candidates(after)

    def test_a_published_frame_survives_a_later_publish_and_raise(self) -> None:
        back_frame = _observation(FOREIGN, observed_at=101.0, state_id="c" * 64)
        relaunch_frame = _observation(FOREIGN, observed_at=102.0, state_id="d" * 64)
        observer = _Observer(
            [back_frame, relaunch_frame],
            initial=self.before,
            raise_after_publishing=2,
        )
        runner = _runner(observer)

        after = runner._return_to_app(self.before)

        self.assertEqual(after.state_id, "d" * 64)
        observer.candidates(after)

    def test_a_capture_predating_containment_is_still_ignored(self) -> None:
        """A cached frame cannot stand in for a fresh observation.

        Every `observe` here raises, so nothing is published during containment
        and the observer's latest is older than the state containment started
        from. That frame says nothing about where the screen is now, so the
        input observation is retained.
        """

        stale = _observation(FOREIGN, observed_at=1.0, state_id="e" * 64)
        observer = _Observer([], initial=stale)
        runner = _runner(observer)

        after = runner._return_to_app(self.before)

        self.assertEqual(after.state_id, self.before.state_id)
        self.assertNotEqual(after.state_id, "e" * 64)

    def test_a_clean_return_is_unchanged(self) -> None:
        """The success path must keep reporting the fresh app-owned observation."""

        fresh = _observation(
            f"{PACKAGE}/.MainActivity", observed_at=1e12, state_id="9" * 64
        )
        observer = _Observer([fresh], initial=self.before)
        runner = _runner(observer)

        after = runner._return_to_app(self.before)

        self.assertEqual(after.state_id, "9" * 64)
        self.assertEqual(
            observer.observe_calls, 1, "a fresh Back return must not relaunch"
        )
        self.assertEqual(runner.session.launches, 0)
        observer.candidates(after)

    def test_the_failed_verdict_is_not_weakened(self) -> None:
        """Returning the observer's frame must not make containment look successful.

        Only a fresh, app-owned, post-dispatch frame proves containment. The
        frame returned in the reproduction is foreign, so the recorded verdict
        has to stay a failure.
        """

        recorded: list[tuple] = []
        back_frame = _observation(FOREIGN, observed_at=101.0, state_id="b" * 64)
        observer = _Observer([back_frame], initial=self.before)
        runner = _runner(observer)
        runner._lifecycle = lambda phase, event, status, **k: recorded.append(
            (event, str(status), k.get("details", {}).get("fresh_observation"))
        )

        runner._return_to_app(self.before)

        verdicts = [item for item in recorded if item[0] == "returned_to_app"]
        self.assertEqual(len(verdicts), 1, recorded)
        self.assertIn("FAILED", verdicts[0][1].upper())
        self.assertFalse(verdicts[0][2])


if __name__ == "__main__":
    unittest.main()
