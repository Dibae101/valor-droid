"""Rung 7 could not have worked even with its gate open and its targets filled.

Three independent things had to be true for FORCED_ACTIVATION to reach a
non-exported screen, and only two of them were:

1. `discovery.forced_components` is computed in `android/manifest.py`. It is.
2. `cli.py` passes it into the runtime config only when `allow_forced_routes` is
   set. The v2 campaign ran with it false, so the target list was empty and the
   rung fired 0 times in 111 runs.
3. The launch has to be permitted. It was not. `adb shell am start` runs as uid
   2000 and the framework refuses a component the app does not export:

       SecurityException: Permission Denial: starting Intent
       { cmp=com.ichi2.anki/.CardBrowser } from null (uid=2000)
       not exported from uid 10214

Measured on the v2 campaign: 1,974 declared activities, 352 exported (17.8%),
1,298 not (65.8%). Akinotcho et al. (ICSE 2025) report 16.8% exported and state
that this limits "the applicability of tools that rely on this type of
activation". So enabling the gate alone would have produced a rung that failed on
roughly four out of five of its targets.

Every container in this fleet ships /system/xbin/su. Verified directly on
127.0.0.1:5561 with the instrumented AnkiDroid bundle: CardBrowser, Reviewer,
Statistics and StudyOptionsActivity are all refused to shell and all resume
correctly under `su 0`, a root-only gain of 4 of the 8 activities parsed.

The root path is reachable from FORCED_ACTIVATION only. An exported component
does not need it, and allowing it there would let an ordinary intent route
silently do something no peer app could do. Forced launches carry
Provenance.FORCED, which invariant I4 keeps out of the `gui union deep_link`
headline, so a reported coverage number still means reachable.
"""
from __future__ import annotations

import unittest

from valordroid.android.adb import AdbError
from valordroid.android.session import AndroidSession


DENIAL = (
    "Starting: Intent { cmp=com.example.app/.Internal }\n"
    "Exception occurred while executing 'start':\n"
    "java.lang.SecurityException: Permission Denial: starting Intent "
    "{ flg=0x10000000 cmp=com.example.app/.Internal } from null "
    "(pid=1234, uid=2000) not exported from uid 10214\n"
)
OK = "Starting: Intent { cmp=com.example.app/.Internal }\nStatus: ok\n"


class _Result:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.stderr = ""


class _Adb:
    """Refuses a shell `am start`, permits the same command under `su 0`.

    The refusal is *raised*, not returned, because that is what the real client
    does: `am` exits non-zero on a SecurityException and `AdbClient.run` with
    check=True turns a non-zero exit into

        AdbError("ADB command failed (<argv>): <stderr>")

    The first version of this fake returned the denial as stdout instead. Every
    test passed and the fallback was dead code on a device: all 13 rung-7
    launches in the fab-on arm failed with "ADB command failed (... shell am
    start ...)" and `su 0` was never reached. A fake that cannot fail the way the
    device fails does not test anything.
    """

    def __init__(self, root_succeeds: bool = True) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.root_succeeds = root_succeeds

    def shell(self, *args: str, check: bool = True, **_kwargs: object) -> _Result:
        self.calls.append(tuple(str(a) for a in args))
        rooted = args[:2] == ("su", "0")
        body = OK if (rooted and self.root_succeeds) else DENIAL
        if "Status: ok" not in body and check:
            raise AdbError(
                f"ADB command failed ({' '.join(str(a) for a in args)}): {body}"
            )
        return _Result(body)


COMPONENT = "com.example.app/com.example.app.Internal"


class _Config:
    launch_timeout_seconds = 60.0
    adb_timeout_seconds = 40.0
    route_foreground_timeout_seconds = 4.0
    component_extras: dict = {}


def _session(root_succeeds: bool = True) -> AndroidSession:
    session = AndroidSession.__new__(AndroidSession)
    session.adb = _Adb(root_succeeds)  # type: ignore[assignment]
    session.config = _Config()  # type: ignore[assignment]
    session.package = "com.example.app"
    session.last_launch_used_root = False
    # The foreground wait is a device concern and not what these tests are about.
    session.wait_for_package = lambda **_kwargs: None  # type: ignore[method-assign]
    return session


class ForcedActivationRootLaunchTest(unittest.TestCase):
    def test_a_shell_denial_is_retried_as_root_when_allowed(self) -> None:
        session = _session()
        session._start("-n", "com.example.app/.Internal", allow_root=True)  # noqa: SLF001
        kinds = [c[:2] for c in session.adb.calls]  # type: ignore[attr-defined]
        self.assertEqual(kinds[0], ("am", "start"))
        self.assertEqual(kinds[1], ("su", "0"))
        self.assertTrue(session.last_launch_used_root)

    def test_without_allow_root_the_denial_propagates(self) -> None:
        """Rung 5 must not silently become a root launch."""
        session = _session()
        with self.assertRaises(AdbError):
            session._start("-n", "com.example.app/.Internal")  # noqa: SLF001
        self.assertEqual(len(session.adb.calls), 1)  # type: ignore[attr-defined]
        self.assertFalse(session.last_launch_used_root)

    def test_a_failure_that_is_not_a_permission_denial_is_not_retried(self) -> None:
        """An unresolvable intent exits zero, so it arrives as output, not a raise."""
        session = _session()

        class _Other(_Adb):
            def shell(self, *args: str, check: bool = True, **_kwargs: object):
                self.calls.append(tuple(str(a) for a in args))
                return _Result("Error: Activity not started, unable to resolve Intent")

        session.adb = _Other()  # type: ignore[assignment]
        with self.assertRaises(AdbError):
            session._start("-n", "com.example.app/.Missing", allow_root=True)  # noqa: SLF001
        self.assertEqual(len(session.adb.calls), 1)  # type: ignore[attr-defined]

    def test_a_raised_denial_still_reaches_the_root_retry(self) -> None:
        """The regression that made the fallback dead code on a real device."""
        session = _session()
        session._start("-n", COMPONENT, allow_root=True)  # noqa: SLF001
        calls = session.adb.calls  # type: ignore[attr-defined]
        self.assertEqual(len(calls), 2, "the raised refusal must not end the launch")
        self.assertEqual(calls[1][:2], ("su", "0"))
        self.assertTrue(session.last_launch_used_root)

    def test_a_raised_non_denial_error_propagates_untouched(self) -> None:
        session = _session()

        class _Broken(_Adb):
            def shell(self, *args: str, check: bool = True, **_kwargs: object):
                self.calls.append(tuple(str(a) for a in args))
                raise AdbError("ADB command failed (adb shell am start): device offline")

        session.adb = _Broken()  # type: ignore[assignment]
        with self.assertRaises(AdbError) as caught:
            session._start("-n", COMPONENT, allow_root=True)  # noqa: SLF001
        self.assertIn("device offline", str(caught.exception))
        self.assertEqual(len(session.adb.calls), 1)  # type: ignore[attr-defined]

    def test_a_root_launch_that_still_fails_reports_both_attempts(self) -> None:
        session = _session(root_succeeds=False)
        with self.assertRaises(AdbError) as caught:
            session._start("-n", "com.example.app/.Internal", allow_root=True)  # noqa: SLF001
        self.assertIn("as shell or as root", str(caught.exception))

    def test_start_component_defaults_to_no_root(self) -> None:
        """The default has to stay the conservative one."""
        session = _session()
        with self.assertRaises(AdbError):
            session.start_component(COMPONENT)
        self.assertEqual(len(session.adb.calls), 1)  # type: ignore[attr-defined]

    def test_start_component_passes_allow_root_through(self) -> None:
        session = _session()
        session.start_component(COMPONENT, allow_root=True)
        self.assertTrue(session.last_launch_used_root)

    def test_the_denial_marker_matches_the_real_framework_message(self) -> None:
        self.assertIn(AndroidSession._NOT_EXPORTED, DENIAL)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
