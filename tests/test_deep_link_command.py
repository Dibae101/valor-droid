"""No deep link resolved in either campaign, and nothing noticed.

Rung 4 of the recovery ladder failed 497 of 497 attempts in v1 and 365 of 365 in
v2 -- a 100% failure rate over two full campaigns -- while deep links are half of
the route mechanism whose contribution correlates with coverage at +0.45, the
second strongest signal in the 50-app dataset.

Every failure recorded the same thing: the package name in the data slot and no
package at all.

    Starting: Intent { act=android.intent.action.VIEW dat=<package> pkg= }
    Error: Activity not started, unable to resolve Intent

The cause is the remote command shape, the same defect `test_secret_input.py`
records for `input_secret`, left unfixed here for longer. `adb shell sh -c
<script>` does not put the script on the device as one word: adb joins the remote
argv with spaces, so the device shell parses

    sh -c IFS= read -r VALORDROID_PACKAGE || exit 64; IFS= read -r VALORDROID_URI ...

as `sh -c IFS=` -- a no-op with `read`, `-r` and the variable name as positional
arguments -- followed by the rest running in the *outer* shell. There the first
`read` consumes the package into VALORDROID_URI, VALORDROID_PACKAGE is never set,
and `am start -d "$VALORDROID_URI" -p "$VALORDROID_PACKAGE"` becomes
`-d <package> -p ""`.

Confirmed on a redroid device, with the script reduced to echoing what it read:

    adb shell sh -c '<script>'  <<< "pkg\nuri"  ->  PKG=[]    URI=[pkg]
    adb shell '<script>'        <<< "pkg\nuri"  ->  PKG=[pkg] URI=[uri]

No test could catch it because the only coverage stubbed the adb call out
entirely, and `tests/fake_device.py` matched the broken `["sh", "-c", script]`
shape and answered as though it had worked.
"""
from __future__ import annotations

import unittest

from valordroid.android.adb import AdbClient


class DeepLinkCommandShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = AdbClient("127.0.0.1:5555", executable="adb")

    def test_the_script_is_the_whole_remote_command(self) -> None:
        """The defect, stated as the assertion that would have caught it."""
        command = self.client.command(["shell", AdbClient._VIEW_INTENT_SCRIPT])
        remote = command[command.index("shell") + 1 :]
        self.assertEqual(
            remote,
            [AdbClient._VIEW_INTENT_SCRIPT],
            "the script must reach the device as a single remote word; as "
            "`sh -c <script>` adb re-splits it and the am start line runs in "
            "the outer shell with the package in the URI slot",
        )

    def test_no_sh_dash_c_wrapper_is_used(self) -> None:
        command = self.client.command(["shell", AdbClient._VIEW_INTENT_SCRIPT])
        remote = command[command.index("shell") + 1 :]
        self.assertNotIn("sh", remote)
        self.assertNotIn("-c", remote)

    def test_both_stdin_lines_are_still_required(self) -> None:
        """A missing line must fail loudly rather than launch something wrong."""
        self.assertEqual(AdbClient._VIEW_INTENT_SCRIPT.count("exit 64"), 2)
        self.assertEqual(AdbClient._VIEW_INTENT_SCRIPT.count("IFS= read -r"), 2)

    def test_the_package_is_read_before_the_uri(self) -> None:
        """Order is the contract the caller writes its two stdin lines in."""
        script = AdbClient._VIEW_INTENT_SCRIPT
        self.assertLess(
            script.index("VALORDROID_PACKAGE"),
            script.index("VALORDROID_URI"),
            "start_view_intent_stdin writes package then uri",
        )

    def test_both_values_stay_quoted_so_metacharacters_are_data(self) -> None:
        """A URI carries '&' and '?'; neither may become shell syntax."""
        script = AdbClient._VIEW_INTENT_SCRIPT
        self.assertIn('-d "$VALORDROID_URI"', script)
        self.assertIn('-p "$VALORDROID_PACKAGE"', script)

    def test_a_uri_with_whitespace_is_refused_before_the_device(self) -> None:
        with self.assertRaises(ValueError):
            self.client.start_view_intent_stdin("com.example.app", "https://x.test/a b")

    def test_an_empty_package_is_refused_before_the_device(self) -> None:
        with self.assertRaises(ValueError):
            self.client.start_view_intent_stdin("", "https://x.test")

    def test_an_empty_uri_is_refused_before_the_device(self) -> None:
        with self.assertRaises(ValueError):
            self.client.start_view_intent_stdin("com.example.app", "")


if __name__ == "__main__":
    unittest.main()
