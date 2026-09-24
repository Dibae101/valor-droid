"""A secret that does not reach the field must fail, not look like success.

`input_secret` typed an empty string on every setup profile that ever used it,
and reported success. ownCloud's login failed with both credential fields still
showing their placeholders while all eleven preceding steps succeeded, which is
the worst possible shape for a bug in a fixture: the evidence says the procedure
ran.

The cause was the remote command shape. Passed as `adb shell sh -c <script>`, adb
does not forward this process's stdin to the remote shell, so `read` returns
success having read nothing and `input text ""` types nothing. Passed as the whole
remote command it works. Verified on a device:

    adb shell 'IFS= read -r V; echo "[$V]"'   <<< secret1   ->  [secret1]
    adb shell sh -c 'IFS= read -r V; ...'     <<< testsecret ->  []
"""
from __future__ import annotations
import unittest

from valordroid.android.adb import AdbClient, AdbError


class SecretInputCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = AdbClient("127.0.0.1:5555", executable="adb")

    def test_the_script_is_the_whole_remote_command(self) -> None:
        """No `sh -c` wrapper, because stdin does not survive it."""
        command = self.client.command(["shell", AdbClient._SECRET_TEXT_SCRIPT])
        self.assertNotIn("sh", command[command.index("shell") + 1 :])
        self.assertEqual(command[-1], AdbClient._SECRET_TEXT_SCRIPT)

    def test_the_script_refuses_an_empty_secret(self) -> None:
        """A blank value must stop the run rather than type nothing."""
        self.assertIn("exit 65", AdbClient._SECRET_TEXT_SCRIPT)
        self.assertIn("-n", AdbClient._SECRET_TEXT_SCRIPT)

    def test_the_script_still_refuses_a_missing_line(self) -> None:
        self.assertIn("exit 64", AdbClient._SECRET_TEXT_SCRIPT)

    def test_an_empty_secret_is_rejected_before_it_reaches_the_device(self) -> None:
        with self.assertRaises(ValueError):
            self.client.input_secret_stdin("")

    def test_a_secret_with_a_newline_is_rejected(self) -> None:
        """The transport is one line on stdin, so a newline would truncate it."""
        for value in ("a\nb", "a\rb", "a\x00b"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.client.input_secret_stdin(value)

    def test_a_nonzero_remote_status_becomes_an_error(self) -> None:
        """rc 65 from the device must surface, and the secret must not leak."""
        import subprocess

        real = subprocess.run

        def fake(command, **kwargs):
            return subprocess.CompletedProcess(command, 65, "", "swallowed hunter2")

        subprocess.run = fake
        self.addCleanup(setattr, subprocess, "run", real)
        with self.assertRaises(AdbError) as caught:
            self.client.input_secret_stdin("hunter2")
        self.assertNotIn("hunter2", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
