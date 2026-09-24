"""A run must disable the device keyboard and put it back afterwards.

Jellyfin sat at 2.18% of its 27,312 methods because focusing its server-address
field crashed the app outright:

    IllegalArgumentException: Android does not support arbitrary transforms
      at CursorAnchorInfoController.updateCursorAnchorInfo
      at TextInputServiceAndroid$createInputConnection$1.onRequestCursorAnchorInfo
      at RecordingInputConnection.requestCursorUpdates

The defect is in the Compose version the app ships and fires when the IME asks a
Compose text field where its cursor is, so it is reachable by any Compose app
with a text field rather than by this one app. With no IME registered the request
never happens; typing still works because `input text` injects key events.

Verified on a device before writing these tests: with the keyboard enabled the
app died on the first tap of the field, and with it disabled the same sequence
entered a server address and reached the signed-in client.
"""
from __future__ import annotations
import unittest


LATIN = "com.android.inputmethod.latin/.LatinIME"


class _Result:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _Adb:
    def __init__(self, default_method: str = LATIN) -> None:
        self.default_method = default_method
        self.calls: list[tuple[str, ...]] = []

    def run(self, arguments, *, timeout=None, check=True, binary=False):
        self.calls.append(tuple(arguments))
        if arguments[:1] == ["get-state"]:
            return _Result("device\n")
        return _Result("")

    def shell(self, *arguments: str, timeout=None, check=True) -> _Result:
        self.calls.append(arguments)
        if arguments[:1] == ("getprop",):
            return _Result("33\n")
        if arguments[:3] == ("settings", "get", "secure"):
            return _Result(f"{self.default_method}\n")
        return _Result("")

    def ime_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[:1] == ("ime",)]


class _Lock:
    def __init__(self) -> None:
        self.held = False

    def acquire(self) -> None:
        self.held = True

    def release(self) -> None:
        self.held = False


class _Session:
    """`AndroidSession.open`/`cleanup` with only the device replaced.

    The methods under test are taken unbound from the real class so the
    production code is what runs, without needing a prepared bundle on disk.
    """

    def __init__(self, default_method: str = LATIN) -> None:
        from valordroid.android.session import AndroidSession

        self.adb = _Adb(default_method)
        self.lock = _Lock()
        self.config = type(
            "_Config",
            (),
            {
                "adb_timeout_seconds": 5.0,
                "uninstall_after_run": False,
                "serial": "s",
                "suppress_soft_keyboard": True,
            },
        )()
        self._opened = False
        self._restore_input_method: str | None = None
        self._suppress = AndroidSession._suppress_soft_keyboard.__get__(self)
        self._cleanup = AndroidSession.cleanup.__get__(self)

    def force_stop(self) -> None:
        return None


class SoftKeyboardSuppressionTest(unittest.TestCase):
    def test_the_keyboard_is_disabled_for_the_run(self) -> None:
        session = _Session()
        session._suppress()
        self.assertEqual(session.adb.ime_calls(), [("ime", "disable", LATIN)])
        self.assertEqual(session._restore_input_method, LATIN)

    def test_the_keyboard_is_restored_afterwards(self) -> None:
        session = _Session()
        session._suppress()
        session._opened = True
        errors = session._cleanup()
        self.assertEqual(errors, ())
        self.assertEqual(
            session.adb.ime_calls(),
            [("ime", "disable", LATIN), ("ime", "enable", LATIN)],
        )
        self.assertIsNone(session._restore_input_method)

    def test_a_device_with_no_keyboard_is_left_alone(self) -> None:
        session = _Session(default_method="null")
        session._suppress()
        self.assertEqual(session.adb.ime_calls(), [])
        self.assertIsNone(session._restore_input_method)

    def test_a_device_that_refuses_the_change_still_runs(self) -> None:
        """Losing the keyboard is an improvement, not a precondition."""

        from valordroid.android.adb import AdbError

        session = _Session()

        def refuse(*arguments, timeout=None, check=True):
            if arguments[:1] == ("settings",):
                raise AdbError("no shell")
            return _Result("")

        session.adb.shell = refuse
        session._suppress()
        self.assertIsNone(session._restore_input_method)

    def test_the_switch_can_be_turned_off(self) -> None:
        """A run must be able to keep the keyboard, so results can say which."""

        session = _Session()
        session.config.suppress_soft_keyboard = False
        session._suppress()
        self.assertEqual(session.adb.ime_calls(), [])
        self.assertIsNone(session._restore_input_method)

    def test_nothing_is_restored_when_nothing_was_changed(self) -> None:
        session = _Session(default_method="null")
        session._suppress()
        session._opened = True
        self.assertEqual(session._cleanup(), ())
        self.assertEqual(session.adb.ime_calls(), [])


if __name__ == "__main__":
    unittest.main()
