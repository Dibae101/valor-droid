"""`stand_down` must work on the real adapter, not just on test doubles.

The primary-observer fallback is covered by `test_primary_observer_fallback`, but
that suite supplies its own adapter with its own `stand_down`, so the real
`PersistentUiAutomator2Adapter.stand_down` was never executed by any test. It
referenced an attribute the constructor does not define, so every call raised
AttributeError -- and because standing down is the handover to the shell dumper,
the failure surfaced as a run-ending `unrecoverable_runtime_error`. A-Photo-Manager
aborted twice on it, at 9 and at 27 minutes, losing both measurements.

These tests therefore drive the real class. The adapter needs no device to
construct, so the handover path can be exercised directly.
"""
from __future__ import annotations

import unittest

from valordroid.android.uiautomator2_fallback import PersistentUiAutomator2Adapter


class StandDownOnTheRealAdapterTest(unittest.TestCase):
    def _adapter(self, *, timeout_seconds: float = 30.0) -> PersistentUiAutomator2Adapter:
        return PersistentUiAutomator2Adapter(
            serial="127.0.0.1:5555", timeout_seconds=timeout_seconds
        )

    def test_stand_down_retires_the_active_generation(self) -> None:
        adapter = self._adapter()
        adapter._active_generation = 1
        seen: list[tuple[float, bool]] = []

        def retire(*, deadline: float, record_evidence: bool) -> None:
            seen.append((deadline, record_evidence))

        adapter._retire_generation = retire  # type: ignore[assignment]

        # The original defect made this raise AttributeError before reaching the
        # retirement at all, so asserting "no exception" is the regression.
        self.assertEqual(adapter.stand_down(), ())
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0][1], "standing down must record its evidence")
        self.assertTrue(adapter.stood_down)

    def test_the_handover_deadline_is_bounded(self) -> None:
        """A handover may not spend a whole capture budget releasing the service."""

        adapter = self._adapter(timeout_seconds=600.0)
        adapter._active_generation = 1
        deadlines: list[float] = []
        adapter._retire_generation = (  # type: ignore[assignment]
            lambda *, deadline, record_evidence: deadlines.append(deadline)
        )
        import time

        before = time.monotonic()
        adapter.stand_down()
        self.assertEqual(len(deadlines), 1)
        # Bounded by the 5s ceiling `close()` uses, not by the 600s timeout.
        self.assertLessEqual(deadlines[0] - before, 5.0 + 0.5)

    def test_a_short_timeout_is_not_widened(self) -> None:
        adapter = self._adapter(timeout_seconds=1.5)
        adapter._active_generation = 1
        deadlines: list[float] = []
        adapter._retire_generation = (  # type: ignore[assignment]
            lambda *, deadline, record_evidence: deadlines.append(deadline)
        )
        import time

        before = time.monotonic()
        adapter.stand_down()
        self.assertLessEqual(deadlines[0] - before, 1.5 + 0.5)

    def test_standing_down_twice_is_a_no_op(self) -> None:
        adapter = self._adapter()
        adapter._active_generation = 1
        calls: list[int] = []
        adapter._retire_generation = (  # type: ignore[assignment]
            lambda *, deadline, record_evidence: calls.append(1)
        )
        adapter.stand_down()
        self.assertEqual(adapter.stand_down(), ())
        self.assertEqual(len(calls), 1, "the service must be retired exactly once")

    def test_no_active_generation_needs_no_retirement(self) -> None:
        adapter = self._adapter()
        self.assertIsNone(adapter._active_generation)
        adapter._retire_generation = (  # type: ignore[assignment]
            lambda **_: self.fail("nothing to retire")
        )
        self.assertEqual(adapter.stand_down(), ())
        self.assertTrue(adapter.stood_down)

    def test_a_failed_retirement_leaves_the_release_unknown(self) -> None:
        """The caller continues, but the shell dumper must not be started."""

        adapter = self._adapter()
        adapter._active_generation = 1

        def explode(*, deadline: float, record_evidence: bool) -> None:
            raise RuntimeError("service did not confirm retirement")

        adapter._retire_generation = explode  # type: ignore[assignment]
        errors = adapter.stand_down()
        self.assertEqual(len(errors), 1)
        self.assertIn("stand down", errors[0])
        self.assertTrue(adapter.release_unknown)


if __name__ == "__main__":
    unittest.main()
