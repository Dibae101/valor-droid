#!/usr/bin/env python3
"""Measure an external GUI explorer against our own APK, universe and denominator.

Every published coverage number for Monkey, Fastbot, APE and the LLM tools was
measured on a different app set, a different budget and often a different metric.
`AGAINST-THE-LITERATURE.md` shows what that costs: it inferred a three-to-fivefold
activity-coverage gap against APE/FastBot2 from cross-paper numbers, which is
enough to know something is wrong and not enough to know what.

This removes the inference. The instrumented APK logs every method it executes as
`<log_tag>: <method_prefix><unit_id>`, so *any* process driving that APK produces
coverage in the same units, against the same frozen universe, with the same
denominator. The explorer becomes the only variable.

Explorers supported here:

  monkey   the platform's own UI exerciser, present on every Android image, and
           the baseline every paper in this area reports against
  external  any command line, given `--explorer-command`, so Fastbot2 or APE can
            be dropped in without changing this file

Read-only with respect to the bundle: the APK is installed and uninstalled, never
rewritten, so the measured binary is the shipped binary.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# `am start`/`dumpsys` spell a resumed activity in several forms; this catches the
# component in each of them so activity reach is comparable across explorers.
_RESUMED = re.compile(r"([A-Za-z][\w.]*)/([\w.$]+)")


def run(arguments: list[str], *, timeout: float = 120.0, check: bool = False):
    return subprocess.run(
        arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=timeout, check=check,
    )


class Device:
    def __init__(self, adb: str, serial: str) -> None:
        self.adb, self.serial = adb, serial

    def shell(self, *command: str, timeout: float = 120.0, check: bool = False):
        return run([self.adb, "-s", self.serial, "shell", *command],
                   timeout=timeout, check=check)

    def ready(self) -> bool:
        """Boot completed and the package service actually answering."""
        boot = self.shell("getprop", "sys.boot_completed").stdout.strip()
        pkg = self.shell("cmd", "package", "path", "android").stdout.strip()
        return boot == "1" and bool(pkg) and "find service" not in pkg

    def install(self, apk: Path) -> None:
        done = run([self.adb, "-s", self.serial, "install", "-r", "-g", str(apk)],
                   timeout=600.0)
        if "Success" not in done.stdout:
            raise RuntimeError(f"install failed: {done.stdout.strip()} {done.stderr.strip()}")

    def uninstall(self, package: str) -> None:
        run([self.adb, "-s", self.serial, "uninstall", package], timeout=180.0)

    def foreground_activity(self) -> str | None:
        out = self.shell("dumpsys", "activity", "activities").stdout
        for line in out.splitlines():
            if "ResumedActivity" in line or "mResumedActivity" in line:
                match = _RESUMED.search(line)
                if match:
                    return f"{match.group(1)}/{match.group(2)}"
        return None


def load_bundle(prepared: Path) -> dict:
    manifest = json.loads((prepared / "prepared.json").read_text())
    universe = json.loads((prepared / "universe.json").read_text())
    apk = prepared / "instrumented.apk"
    if not apk.is_file():
        raise SystemExit(f"{prepared}: instrumented.apk is missing")
    return {
        "apk": apk,
        "package": manifest["package_name"],
        "log_tag": manifest["log_tag"],
        "method_prefix": manifest["method_prefix"],
        "metric": manifest["metric"],
        "backend": manifest["backend"],
        "inclusion_policy": manifest["inclusion_policy"],
        "universe_sha256": universe["universe_sha256"],
        "unit_ids": frozenset(universe["unit_ids"]),
        "launch_candidates": manifest.get("launch_candidates") or [],
    }


def start_logcat(device: Device, destination: Path):
    """Stream the whole buffer to a file. Filtering happens offline.

    The tag filter is applied when parsing rather than to logcat itself, so a
    misconfigured filter cannot silently produce zero coverage -- a failure mode
    that would look exactly like an explorer that reached nothing.
    """
    device.shell("logcat", "-c", timeout=60.0)
    handle = destination.open("wb")
    process = subprocess.Popen(
        [device.adb, "-s", device.serial, "logcat", "-v", "brief"],
        stdout=handle, stderr=subprocess.DEVNULL,
    )
    return process, handle


def parse_coverage(log: Path, bundle: dict) -> dict:
    """Observed units and activity reach, from the retained log only."""
    tag, prefix = bundle["log_tag"], bundle["method_prefix"]
    observed: set[str] = set()
    unknown = 0
    activities: set[str] = set()
    package = bundle["package"]
    with log.open("r", errors="replace") as handle:
        for line in handle:
            if tag in line and prefix in line:
                unit = line.split(prefix, 1)[1].strip()
                if unit in bundle["unit_ids"]:
                    observed.add(unit)
                elif unit:
                    unknown += 1
            # Activity reach, from the platform's own lifecycle logging.
            if "Displayed " in line and package in line:
                match = _RESUMED.search(line[line.index("Displayed "):])
                if match and match.group(1).startswith(package.split(".")[0]):
                    activities.add(f"{match.group(1)}/{match.group(2)}")
    total = len(bundle["unit_ids"])
    return {
        "observed_covered_units": len(observed),
        "total_units": total,
        "observed_coverage_percent": 100.0 * len(observed) / total if total else 0.0,
        "unknown_unit_lines": unknown,
        "activities_reached": sorted(activities),
        "activities_reached_count": len(activities),
    }


def explore_monkey(device: Device, bundle: dict, seconds: float, *, seed: int) -> dict:
    """The platform UI exerciser, restarted until the budget is spent.

    Monkey takes an event count rather than a duration, and it exits early when it
    leaves the package or hits its own limits. Restarting with a fresh seed until
    the clock runs out is how the literature gives it a time budget, and the
    restarts are recorded because they are part of what it did.
    """
    throttle_ms = 200
    deadline = time.monotonic() + seconds
    restarts, events, cut_short = 0, 0, 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 5.0:
            break
        # Size the batch to the time left, so Monkey normally finishes on its own
        # rather than being cut off. The timeout is the backstop, not the plan.
        batch = max(50, int(remaining * 1000 / throttle_ms))
        restarts += 1
        try:
            done = device.shell(
                "monkey", "-p", bundle["package"],
                "-s", str(seed + restarts),
                "--throttle", str(throttle_ms),
                "--ignore-crashes", "--ignore-timeouts",
                "--ignore-security-exceptions",
                "--pct-syskeys", "0",
                str(batch),
                timeout=remaining,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # Reaching the deadline mid-batch is a normal ending. Stop the
            # on-device process so it cannot keep injecting into the next app.
            cut_short += 1
            device.shell("pkill", "-f", "com.android.commands.monkey", timeout=30.0)
            break
        matched = re.search(r"Events injected: (\d+)", done.stdout or "")
        if matched:
            events += int(matched.group(1))
        time.sleep(1.0)
    return {
        "explorer": "monkey",
        "restarts": restarts,
        "events_injected": events,
        "batches_cut_short": cut_short,
        "throttle_ms": throttle_ms,
    }


def explore_external(device: Device, bundle: dict, seconds: float, command: str) -> dict:
    """Any explorer, given a command line. {serial} and {package} are substituted."""
    rendered = command.format(serial=device.serial, package=bundle["package"],
                              seconds=int(seconds))
    process = subprocess.Popen(rendered, shell=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, preexec_fn=None)
    try:
        out, _ = process.communicate(timeout=seconds)
        code = process.returncode
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGINT)
        try:
            out, _ = process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            out = ""
        code = None
    return {"explorer": "external", "command": rendered,
            "exit_code": code, "tail": (out or "")[-2000:]}


def measure(
    prepared: Path, *, adb: str, serial: str, seconds: float, output: Path,
    explorer: str, explorer_command: str | None, seed: int, launcher: str | None,
) -> dict:
    bundle = load_bundle(prepared)
    device = Device(adb, serial)
    if not device.ready():
        raise SystemExit(f"{serial}: device framework is not ready")
    output.mkdir(parents=True, exist_ok=True)
    log = output / "logcat.txt"

    started_at = time.time()
    device.uninstall(bundle["package"])
    device.install(bundle["apk"])
    process, handle = start_logcat(device, log)
    launch_error = None
    try:
        target = launcher or (
            bundle["launch_candidates"][0]
            if len(bundle["launch_candidates"]) == 1 else None
        )
        if target:
            device.shell("am", "start", "-W", "-n", target, timeout=120.0)
        else:
            # Ambiguous or absent launcher: let the platform choose, and record
            # that we did, because it changes what the explorer starts from.
            device.shell("monkey", "-p", bundle["package"], "-v", "1", timeout=120.0)
            launch_error = (
                "ambiguous_launcher_used_monkey_launch"
                if len(bundle["launch_candidates"]) != 1 else None
            )
        time.sleep(3.0)
        if explorer == "monkey":
            detail = explore_monkey(device, bundle, seconds, seed=seed)
        else:
            if not explorer_command:
                raise SystemExit("--explorer-command is required for an external explorer")
            detail = explore_external(device, bundle, seconds, explorer_command)
    finally:
        time.sleep(3.0)
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
        handle.close()
        device.uninstall(bundle["package"])

    result = {
        "schema": 1,
        "prepared": str(prepared),
        "app": prepared.name,
        "package": bundle["package"],
        "serial": serial,
        "metric": bundle["metric"],
        "backend": bundle["backend"],
        "inclusion_policy": bundle["inclusion_policy"],
        "universe_sha256": bundle["universe_sha256"],
        "budget_seconds": seconds,
        "wall_seconds": round(time.time() - started_at, 1),
        "seed": seed,
        "launch_note": launch_error,
        **detail,
        **parse_coverage(log, bundle),
    }
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="/usr/bin/adb")
    parser.add_argument("--seconds", type=float, default=600.0)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--explorer", default="monkey",
                        choices=("monkey", "external"))
    parser.add_argument("--explorer-command", default=None,
                        help="command line for --explorer external; "
                             "{serial} {package} {seconds} are substituted")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--launcher", default=None,
                        help="explicit entry activity when the APK declares several")
    arguments = parser.parse_args()

    if not shutil.which(arguments.adb) and not Path(arguments.adb).exists():
        raise SystemExit(f"adb not found: {arguments.adb}")
    result = measure(
        arguments.prepared, adb=arguments.adb, serial=arguments.serial,
        seconds=arguments.seconds, output=arguments.output,
        explorer=arguments.explorer, explorer_command=arguments.explorer_command,
        seed=arguments.seed, launcher=arguments.launcher,
    )
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("activities_reached", "tail")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
