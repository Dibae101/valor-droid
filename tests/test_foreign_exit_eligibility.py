"""A control that keeps leaving the app must stop being offered.

Containment already returns a run to the app under test, but returning is a cure
rather than a deterrent. Launching another app changes the foreground, so the
outcome verifier records a real EFFECT and the zero-gain rule -- which requires
the action to stop having any effect at all -- never retires it. A retained run
showed one control chosen 22 times from a single screen, spending 31 of 36 actions
on leave-and-return while crediting zero coverage units.

These tests pin the rule that fixes that, and equally pin what it must not do:
a handoff that actually yields coverage stays eligible, and the first excursion is
still allowed because some features legitimately need one.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valordroid.config import CoreConfig
from valordroid.frontier import (
    FOREIGN_EXIT_EXHAUSTED_REASON,
    MAX_UNPRODUCTIVE_FOREIGN_EXITS,
    PERMANENT_EXHAUSTION_REASONS,
    ZERO_GAIN_EXHAUSTED_REASON,
    FrontierStore,
    RankedCandidate,
    is_permanently_exhausted,
    usable_fallback,
)
from valordroid.models import (
    ActionAttempt,
    ActionCandidate,
    ActionSpec,
    ExecutionRecord,
    ExecutionStatus,
    OutcomeKind,
    OutcomeRecord,
    Provenance,
)

APP = "com.example.app"
FOREIGN = "com.android.settings"
STATE = "s1"


def _spec() -> ActionSpec:
    return ActionSpec(
        "tap",
        "activate",
        parameters={
            "selector": {"package": APP, "resource_id": f"{APP}:id/handoff"},
            "expected_state_id": STATE,
        },
    )


def _attempt(
    *,
    sequence: int,
    landed_package: str,
    units: tuple[str, ...] = (),
    changed: bool = True,
) -> ActionAttempt:
    spec = _spec()
    after_state = f"{landed_package}-screen" if changed else STATE
    return ActionAttempt(
        attempt_id=f"a-{sequence:08d}",
        sequence=sequence,
        state_id=STATE,
        provenance=Provenance.GUI,
        requested=spec,
        execution=ExecutionRecord(
            status=ExecutionStatus.EXECUTED,
            action=spec,
            started_at=float(sequence),
            ended_at=float(sequence) + 0.5,
        ),
        outcome=OutcomeRecord(
            OutcomeKind.EFFECT if changed else OutcomeKind.NO_EFFECT,
            state_changed=changed,
            details={
                "before_activity": f"{APP}/.MainActivity",
                "after_activity": f"{landed_package}/.Landed",
                "before_state_id": STATE,
                "after_state_id": after_state,
            },
        ),
        window_started_at=float(sequence),
        window_ended_at=float(sequence) + 2.5,
        associated_unit_ids=units,
    )


class ForeignExitEligibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        # Retirement is opt-in, so these tests ask for it explicitly. The default
        # is asserted separately below.
        self.config = CoreConfig(foreign_exit_retirement_enabled=True)

    def _replay(self, attempts: list[ActionAttempt]) -> FrontierStore:
        store = FrontierStore(self.root / "frontier.json", self.config)
        store.reconcile(
            tuple((f"{index:064d}", item) for index, item in enumerate(attempts, 1))
        )
        return store

    @staticmethod
    def _only_entry(store: FrontierStore):
        entries = store.entries()
        assert len(entries) == 1, entries
        return entries[0]

    def _rank(self, store: FrontierStore, now: float):
        return store.rank([ActionCandidate(STATE, _spec())], now=now)[0]

    def test_an_unproductive_exit_is_counted_but_tolerated_once(self) -> None:
        store = self._replay([_attempt(sequence=1, landed_package=FOREIGN)])
        entry = self._only_entry(store)
        self.assertEqual(entry.foreign_exits, 1)
        self.assertEqual(entry.effects, 1, "leaving the app still reads as an effect")
        self.assertTrue(
            self._rank(store, now=2.0).eligible,
            "a feature may legitimately need one handoff, so the first is allowed",
        )

    def test_a_repeatedly_unproductive_exit_is_retired(self) -> None:
        store = self._replay([
            _attempt(sequence=index, landed_package=FOREIGN)
            for index in range(1, MAX_UNPRODUCTIVE_FOREIGN_EXITS + 1)
        ])
        entry = self._only_entry(store)
        self.assertEqual(entry.foreign_exits, MAX_UNPRODUCTIVE_FOREIGN_EXITS)
        self.assertEqual(entry.associated_units, 0)
        verdict = self._rank(store, now=99.0)
        self.assertFalse(verdict.eligible)
        self.assertEqual(verdict.reason, FOREIGN_EXIT_EXHAUSTED_REASON)

    def test_a_productive_handoff_is_not_retired(self) -> None:
        """Coverage is the point; an exit that still produces it stays available."""

        store = self._replay([
            _attempt(sequence=index, landed_package=FOREIGN, units=(f"u{index}",))
            for index in range(1, MAX_UNPRODUCTIVE_FOREIGN_EXITS + 3)
        ])
        verdict = self._rank(store, now=99.0)
        self.assertTrue(verdict.eligible)
        self.assertNotEqual(verdict.reason, FOREIGN_EXIT_EXHAUSTED_REASON)

    def test_an_early_success_does_not_buy_permanent_immunity(self) -> None:
        """Recent yield decides, not lifetime yield.

        Judging on lifetime `associated_units` let a control that credited two
        units on an early exit leave the app another 102 times in a retained run,
        because one ancient success made it immune forever.
        """

        attempts = [_attempt(sequence=1, landed_package=FOREIGN, units=("u1", "u2"))]
        attempts += [
            _attempt(sequence=index, landed_package=FOREIGN)
            for index in range(2, 10)
        ]
        store = self._replay(attempts)
        entry = self._only_entry(store)
        self.assertGreater(entry.associated_units, 0, "lifetime yield is nonzero")
        verdict = self._rank(store, now=99.0)
        self.assertFalse(
            verdict.eligible,
            "a handoff that has stopped paying must be retired despite an early gain",
        )
        self.assertEqual(verdict.reason, FOREIGN_EXIT_EXHAUSTED_REASON)

    def test_a_retired_control_is_kept_out_of_the_last_resort_pool(self) -> None:
        """Callers fall back to ineligible candidates; retired ones must not return.

        This is the defect that let one control be chosen 134 times: the screen had
        nothing eligible, so the fallback re-offered everything, including entries
        the frontier had already retired on execution evidence.
        """

        store = self._replay([
            _attempt(sequence=index, landed_package=FOREIGN)
            for index in range(1, MAX_UNPRODUCTIVE_FOREIGN_EXITS + 1)
        ])
        retired = self._rank(store, now=99.0)
        self.assertTrue(is_permanently_exhausted(retired))
        self.assertEqual(usable_fallback([retired]), (retired,),
                         "with nothing else available a caller must still get a choice")

        cooling = RankedCandidate(
            ActionCandidate(STATE, _spec()), False, -1.0, "cooling down, not permanently removed"
        )
        self.assertEqual(
            usable_fallback([retired, cooling]),
            (cooling,),
            "a temporarily suppressed candidate is a fair last resort; a retired one is not",
        )

    def test_a_cooling_candidate_is_not_treated_as_permanent(self) -> None:
        cooling = RankedCandidate(
            ActionCandidate(STATE, _spec()), False, -1.0, "cooling down, not permanently removed"
        )
        self.assertFalse(is_permanently_exhausted(cooling))

    def test_evidence_predating_the_counter_still_reconstructs(self) -> None:
        """Adding a field must not retroactively invalidate retained runs.

        Bumping the frontier schema broke validation of eleven already-qualified
        runs, because reconstruction emitted a field their snapshots could not
        contain. Rebuilding against the retained snapshot's own version fixes it.
        """

        attempts = [
            _attempt(sequence=index, landed_package=FOREIGN)
            for index in range(1, 3)
        ]
        history = tuple(
            (f"{index:064d}", item) for index, item in enumerate(attempts, 1)
        )
        current = FrontierStore.expected_snapshot(history)
        older = FrontierStore.expected_snapshot(history, schema_version=3)

        self.assertEqual(current["schema_version"], FrontierStore.SCHEMA_VERSION)
        self.assertEqual(older["schema_version"], 3)
        for entry in current["entries"].values():
            self.assertIn("foreign_exits", entry)
        for entry in older["entries"].values():
            self.assertNotIn("foreign_exits", entry)
        self.assertEqual(
            {
                key: {k: v for k, v in entry.items() if k != "foreign_exits"}
                for key, entry in current["entries"].items()
            },
            older["entries"],
            "only the newer field may differ between schema versions",
        )

    def test_a_future_schema_cannot_be_rebuilt(self) -> None:
        with self.assertRaises(ValueError):
            FrontierStore.expected_snapshot(
                (), schema_version=FrontierStore.SCHEMA_VERSION + 1
            )

    def test_retirement_is_off_unless_asked_for(self) -> None:
        """The default must reproduce the behaviour measured before the rule.

        Enabling this unmeasured cost 13 mean coverage points across 17 apps, with
        the worst losses on apps whose productive path runs through a system
        handoff. So the counter still accrues -- it is useful evidence -- but it
        changes no decision until a run opts in.
        """

        self.assertFalse(CoreConfig().foreign_exit_retirement_enabled)
        # The same history the immunity test retires with the flag on: one early
        # gain then repeated unproductive exits. The early gain keeps the separate
        # zero-gain rule from firing, so the flag is the only variable.
        attempts = [_attempt(sequence=1, landed_package=FOREIGN, units=("u1", "u2"))]
        attempts += [
            _attempt(sequence=index, landed_package=FOREIGN)
            for index in range(2, 10)
        ]
        store = FrontierStore(self.root / "default.json", CoreConfig())
        store.reconcile(
            tuple((f"{index:064d}", item) for index, item in enumerate(attempts, 1))
        )
        entry = store.entries()[0]
        self.assertGreaterEqual(entry.foreign_exits, MAX_UNPRODUCTIVE_FOREIGN_EXITS)
        verdict = store.rank([ActionCandidate(STATE, _spec())], now=99.0)[0]
        self.assertTrue(
            verdict.eligible,
            "with the flag off, repeated app exits must not retire the control",
        )
        self.assertNotEqual(verdict.reason, FOREIGN_EXIT_EXHAUSTED_REASON)

    def test_both_retirement_reasons_are_permanent(self) -> None:
        self.assertEqual(
            PERMANENT_EXHAUSTION_REASONS,
            {ZERO_GAIN_EXHAUSTED_REASON, FOREIGN_EXIT_EXHAUSTED_REASON},
        )

    def test_staying_in_the_app_never_counts_as_an_exit(self) -> None:
        store = self._replay([
            _attempt(sequence=index, landed_package=APP) for index in range(1, 4)
        ])
        self.assertEqual(self._only_entry(store).foreign_exits, 0)

    def test_an_in_app_zero_gain_action_still_uses_the_original_rule(self) -> None:
        """The new branch must not shadow the existing zero-gain retirement."""

        store = self._replay([
            _attempt(sequence=index, landed_package=APP, changed=False)
            for index in range(1, self.config.max_zero_gain_candidate_attempts + 1)
        ])
        verdict = self._rank(store, now=99.0)
        self.assertFalse(verdict.eligible)
        self.assertEqual(verdict.reason, ZERO_GAIN_EXHAUSTED_REASON)

    def test_the_count_survives_a_replay_of_the_transaction_log(self) -> None:
        """Offline validation rebuilds the frontier from transactions alone."""

        attempts = [
            _attempt(sequence=index, landed_package=FOREIGN)
            for index in range(1, MAX_UNPRODUCTIVE_FOREIGN_EXITS + 1)
        ]
        self._replay(attempts)
        history = tuple(
            (f"{index:064d}", item) for index, item in enumerate(attempts, 1)
        )
        expected = FrontierStore.expected_snapshot(history)
        retained = json.loads((self.root / "frontier.json").read_text(encoding="utf-8"))
        self.assertEqual(expected["entries"], retained["entries"])
        only = next(iter(expected["entries"].values()))
        self.assertEqual(only["foreign_exits"], MAX_UNPRODUCTIVE_FOREIGN_EXITS)


if __name__ == "__main__":
    unittest.main()
