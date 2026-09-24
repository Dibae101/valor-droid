"""The experiment driver must never overwrite a previous attempt.

`run_coverage_experiment.py` wrote every attempt of one app/arm cell to a single
`runs/{app}.{arm}` directory and deleted that directory first. The
infrastructure-retry path calls the same function again, so a retry destroyed the
evidence of the attempt it was retrying -- and that first attempt's terminal
record is the only account of why a retry was needed. Re-invoking the driver over
an existing work directory did the same thing to a completed campaign.

These tests drive the real `execute_run` and stop it before any device command,
which is where the deletion used to have already happened.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1] / "app" / "tools"


def _load_driver():
    spec = importlib.util.spec_from_file_location(
        "_run_coverage_experiment_under_test", _TOOLS / "run_coverage_experiment.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class NextAttemptIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = _load_driver()
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def test_an_empty_cell_starts_at_one(self) -> None:
        self.assertEqual(self.driver.next_attempt_index(self.root), 1)

    def test_a_missing_root_starts_at_one(self) -> None:
        self.assertEqual(
            self.driver.next_attempt_index(self.root / "absent"), 1
        )

    def test_each_existing_attempt_advances_the_ordinal(self) -> None:
        (self.root / "attempt-0001").mkdir()
        self.assertEqual(self.driver.next_attempt_index(self.root), 2)
        (self.root / "attempt-0002").mkdir()
        self.assertEqual(self.driver.next_attempt_index(self.root), 3)

    def test_a_gap_does_not_reuse_a_lower_ordinal(self) -> None:
        """Reusing a freed ordinal would overwrite whatever is still there."""

        (self.root / "attempt-0003").mkdir()
        self.assertEqual(self.driver.next_attempt_index(self.root), 4)

    def test_unrelated_entries_are_ignored(self) -> None:
        (self.root / "attempt-notanumber").mkdir()
        (self.root / "run.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.driver.next_attempt_index(self.root), 1)

    def test_an_ordinal_claimed_in_any_root_is_not_reissued(self) -> None:
        """The runner creates the run directory; the driver creates the configs.

        A run that never started leaves only the configuration side behind, so
        an ordinal taken from the run side alone would be handed out twice.
        """

        runs = self.root / "runs"
        configs = self.root / "configs"
        runs.mkdir()
        (configs / "attempt-0001").mkdir(parents=True)
        self.assertEqual(self.driver.next_attempt_index(runs, configs), 2)

    def test_the_highest_ordinal_across_roots_wins(self) -> None:
        runs = self.root / "runs"
        configs = self.root / "configs"
        (runs / "attempt-0002").mkdir(parents=True)
        (configs / "attempt-0005").mkdir(parents=True)
        self.assertEqual(self.driver.next_attempt_index(runs, configs), 6)
        self.assertEqual(self.driver.next_attempt_index(configs, runs), 6)


class PriorAttemptPreservedTest(unittest.TestCase):
    """The reproduction: a prior attempt must survive a second execution."""

    def setUp(self) -> None:
        self.driver = _load_driver()
        self._temporary = tempfile.TemporaryDirectory()
        self.work = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        # Stop before any device command. `launcher_for` is the first thing
        # `execute_run` needs from the APK, and `run_cli` is the first thing it
        # needs from a device; both are replaced so the test exercises the
        # directory decision and nothing else.
        self.driver.launcher_for = lambda apk, package: f"{package}/.Main"
        # `execute_run` also gates on the device framework being up, and that
        # check shells out to a real `adb`. Left alone it makes this test depend
        # on the host: with no device attached, `device_ready` returns False and
        # `wait_for_device` blocks for DEVICE_RECOVERY_SECONDS (180s) on every
        # call, so the suite appeared to hang for around eighteen minutes. It
        # passed before only because a reDroid container happened to be
        # attached. The subject here is the attempt-directory decision, so the
        # device is declared ready and no adb process is ever started.
        self.driver.device_ready = lambda serial: True
        self.driver.wait_for_device = lambda serial, *, timeout: True

        # No host process may actually start. `execute_run` shells out directly
        # to `adb` to uninstall the target package before the run, and with a
        # device attached the original version of this test performed that
        # uninstall for real. Recorded instead, so the commands are visible to
        # the assertions below and nothing touches a device.
        self.host_commands: list[list[str]] = []
        test = self

        class _RecordedProcesses:
            def __getattr__(self, name):
                return getattr(subprocess, name)

            @staticmethod
            def run(arguments, **keywords):
                test.host_commands.append(list(arguments))
                return subprocess.CompletedProcess(list(arguments), 0, b"", b"")

        self.driver.subprocess = _RecordedProcesses()
        self.calls: list[list[str]] = []

        def refuse(arguments, timeout=None):
            self.calls.append(list(arguments))
            raise TimeoutError("stopped before any device work")

        self.driver.run_cli = refuse
        prepared = self.work / "prepared"
        prepared.mkdir(parents=True)
        self.app = self.driver.AppResult(
            name="Chess",
            package="jwtc.android.chess",
            prepared=str(prepared),
        )

    def _execute(self) -> dict:
        try:
            return self.driver.execute_run(
                app=self.app,
                arm="gemma",
                serial="127.0.0.1:5556",
                work=self.work,
                apk=Path("Chess.apk"),
                max_seconds=60.0,
                max_actions=10,
                models={"gemma": "google.gemma-3-4b-it"},
                max_model_calls=0,
                project="",
                region="",
            )
        except TimeoutError:
            # `run_cli` refused, which is after the directory decision.
            return {}

    def _cell_root(self) -> Path:
        return self.work / "runs" / "Chess.gemma"

    def test_a_prior_attempt_and_its_marker_survive(self) -> None:
        prior = self._cell_root() / "attempt-0001"
        prior.mkdir(parents=True)
        marker = prior / "actions.jsonl"
        marker.write_text('{"prior": true}\n', encoding="utf-8")

        self._execute()

        self.assertTrue(prior.is_dir(), "the prior attempt directory was removed")
        self.assertTrue(marker.is_file(), "the prior attempt's evidence was removed")
        self.assertEqual(marker.read_text(encoding="utf-8"), '{"prior": true}\n')

    def test_the_second_execution_claims_the_next_attempt(self) -> None:
        (self._cell_root() / "attempt-0001").mkdir(parents=True)

        record = self._execute()

        # The record is empty only if run_cli raised before it was built; the
        # directory choice is still observable from the filesystem and the
        # retained per-attempt configuration.
        chosen = self.work / "configs" / "Chess.gemma" / "attempt-0002"
        self.assertTrue(
            chosen.is_dir(), "the second execution reused the first attempt's configs"
        )
        self.assertTrue((chosen / "runtime.json").is_file())
        if record:
            self.assertEqual(record["attempt"], 2)

    def test_the_run_output_directory_is_left_for_the_runner_to_create(self) -> None:
        """`RunStore.create` refuses a non-empty directory, so it must not exist."""

        self._execute()
        self.assertFalse((self._cell_root() / "attempt-0001").exists())
        self.assertTrue((self.work / "configs" / "Chess.gemma" / "attempt-0001").is_dir())

    def test_each_attempt_retains_its_own_configuration(self) -> None:
        """A retry after a run that never started must not reuse the ordinal.

        `run_cli` refuses here, so the runner never creates its output
        directory. Only the frozen configuration marks the first attempt, and
        the second execution has to see it.
        """

        self._execute()
        self._execute()
        first = self.work / "configs" / "Chess.gemma" / "attempt-0001" / "runtime.json"
        second = self.work / "configs" / "Chess.gemma" / "attempt-0002" / "runtime.json"
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())
        # Both are real frozen configurations, not one shared file.
        self.assertEqual(
            json.loads(first.read_text())["serial"], "127.0.0.1:5556"
        )
        self.assertNotEqual(first, second)

    def test_the_only_host_command_before_the_runner_is_the_uninstall(self) -> None:
        """Pins what `execute_run` does to a device before handing over.

        This test is hermetic and must stay that way. It previously reached the
        real host twice: `device_ready` shelled out to `adb` and blocked for
        DEVICE_RECOVERY_SECONDS when no device was attached, and this uninstall
        ran for real against whatever device was. Recording the commands keeps
        both visible instead of letting them depend on the machine.
        """

        self._execute()
        self.assertEqual(
            self.host_commands,
            [["adb", "-s", "127.0.0.1:5556", "uninstall", "jwtc.android.chess"]],
        )

    def test_the_output_path_passed_to_the_runner_is_the_attempt_directory(self) -> None:
        self._execute()
        self.assertTrue(self.calls, "run_cli was never reached")
        arguments = self.calls[0]
        self.assertIn("--output", arguments)
        output = Path(arguments[arguments.index("--output") + 1])
        self.assertEqual(output.name, "attempt-0001")
        self.assertEqual(output.parent, self._cell_root())


if __name__ == "__main__":
    unittest.main()
