"""A device that is momentarily busy must not cost the whole run.

The lock is an `flock`, so it is released when the owning process exits. That is
correct, but acquisition failed instantly, and a few seconds of overlap between a
winding-down campaign and a starting one destroyed entire measurements: 6 of 10 apps
in one 3600s validation aborted with "device is already owned by another VALOR-Droid
run" within three minutes, and 3 of 4 in a later smoke.

Waiting costs seconds. Aborting costs every unit the run would have covered. A
holder that never releases still fails, just after the wait rather than instead of
it, and the message says how long it waited so the two cases are distinguishable in
evidence.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from valordroid.android.adb import DeviceLock


SERIAL = "127.0.0.1:5560"


class DeviceLockWaitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _lock(self) -> DeviceLock:
        return DeviceLock(SERIAL, root=self.root)

    def test_a_free_device_is_acquired_at_once(self) -> None:
        lock = self._lock()
        began = time.monotonic()
        lock.acquire()
        self.addCleanup(lock.release)
        self.assertLess(time.monotonic() - began, 1.0)

    def test_a_device_released_during_the_wait_is_acquired(self) -> None:
        """The case that was losing runs: the previous owner is still exiting."""

        holder = self._lock()
        holder.acquire()
        released = threading.Event()

        def release_soon() -> None:
            time.sleep(0.6)
            holder.release()
            released.set()

        thread = threading.Thread(target=release_soon)
        thread.start()
        self.addCleanup(thread.join)

        waiter = self._lock()
        waiter.acquire(wait_seconds=10.0)
        self.addCleanup(waiter.release)
        self.assertTrue(released.is_set())

    def test_a_holder_that_never_releases_still_fails(self) -> None:
        """Waiting is not a way to hide a genuinely stuck device."""

        holder = self._lock()
        holder.acquire()
        self.addCleanup(holder.release)
        waiter = self._lock()
        with self.assertRaises(RuntimeError) as caught:
            waiter.acquire(wait_seconds=0.2)
        self.assertIn("already owned", str(caught.exception))

    def test_the_failure_records_how_long_it_waited(self) -> None:
        """So a contended device is distinguishable from a stuck one in evidence."""

        holder = self._lock()
        holder.acquire()
        self.addCleanup(holder.release)
        waiter = self._lock()
        with self.assertRaises(RuntimeError) as caught:
            waiter.acquire(wait_seconds=0.0)
        self.assertIn("waited 0s", str(caught.exception))

    def test_the_default_wait_is_long_enough_to_cover_a_handover(self) -> None:
        self.assertGreaterEqual(DeviceLock.ACQUIRE_WAIT_SECONDS, 30.0)

    def test_acquiring_twice_from_one_object_is_still_refused(self) -> None:
        lock = self._lock()
        lock.acquire()
        self.addCleanup(lock.release)
        with self.assertRaises(RuntimeError):
            lock.acquire()

    def test_release_is_idempotent(self) -> None:
        lock = self._lock()
        lock.acquire()
        lock.release()
        lock.release()

    def test_the_context_manager_still_holds_and_frees(self) -> None:
        with self._lock():
            other = self._lock()
            with self.assertRaises(RuntimeError):
                other.acquire(wait_seconds=0.0)
        reacquired = self._lock()
        reacquired.acquire(wait_seconds=0.0)
        reacquired.release()


if __name__ == "__main__":
    unittest.main()
