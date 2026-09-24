"""The component-launch escape was gated on a trigger that almost never fires.

`_component_launches_earned()` withholds deep links, exported intents and forced
activation until 60% of the budget. `launch_on_frontier_exhaustion` was added to
release them earlier on evidence instead, but the evidence test also required the
recovery trigger to be `screen_exhausted`.

Trigger distribution over the v2 campaign, from routes.jsonl:

    coverage_plateau        38,938   93%
    screen_exhausted           437    4%
    temporarily_no_eligible     43

So the escape was reachable on 4% of recovery events. Rung 7 fired 0 times in 111
runs, and in the follow-up A/B it fired only after the 60% clock opened, which at
3600s leaves the treatment the last 24 minutes of the run. 38 of 98 v2 runs (39%)
recorded their last coverage gain *before* the gate opened, a mean 32% of budget
spent with nothing left to gain and activation still withheld.

Whether anything is left to find is decided by the other two conditions -- no
untried action on this screen, and no recorded route to useful work. The trigger
says why recovery was invoked, not whether the frontier is spent. This widens the
test to the plateau trigger and keeps `temporarily_no_eligible` out, because that
one is transient by construction: the candidate returns after its cooldown.
"""
from __future__ import annotations

import inspect
import unittest

from valordroid.runner import AndroidRunner


class LaunchGateReleaseTest(unittest.TestCase):
    """Asserted against the gate's source, which is where the condition lives."""

    def setUp(self) -> None:
        self.source = inspect.getsource(AndroidRunner)

    def _gate_block(self) -> str:
        marker = "frontier_exhausted = ("
        start = self.source.index(marker)
        return self.source[start : start + 340]

    def test_the_plateau_trigger_can_release_a_component_launch(self) -> None:
        block = self._gate_block()
        self.assertIn(
            "coverage_plateau",
            block,
            "93% of recovery events are coverage_plateau; excluding it made the "
            "evidence-based escape unreachable and left rung 7 on the 60% clock",
        )

    def test_screen_exhausted_still_releases_it(self) -> None:
        self.assertIn("screen_exhausted", self._gate_block())

    def test_the_transient_trigger_does_not_release_it(self) -> None:
        """A cooldown is not exhaustion; the candidate comes back."""
        self.assertNotIn("temporarily_no_eligible", self._gate_block())

    def test_exhaustion_still_requires_no_untried_action_and_no_plan(self) -> None:
        """Widening the trigger must not weaken what exhaustion means."""
        block = self._gate_block()
        self.assertIn("context.untried is None", block)
        self.assertIn("context.plan is None", block)

    def test_forced_activation_remains_behind_its_own_flag(self) -> None:
        """Releasing the gate must not also permit forcing."""
        self.assertIn(
            "rung is RecoveryRung.FORCED_ACTIVATION and not self.core.config.allow_forced_routes",
            self.source,
        )

    def test_exported_intents_do_not_need_the_option(self) -> None:
        """An exported activity is a normal Android entry point."""
        self.assertIn("rung is RecoveryRung.EXPORTED_INTENT", self.source)


if __name__ == "__main__":
    unittest.main()
