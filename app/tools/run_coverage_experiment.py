#!/usr/bin/env python3
"""Matched-control coverage experiment across apps, devices, and model providers.

Purpose: answer "does this cover more code?" with a comparison that is actually
valid, which is the point of the gap-5/gap-6 measurement work.

What makes it valid:

- Every arm for one app uses the **same prepared bundle**, so the instrumented
  APK bytes and the frozen coverage universe are byte-identical and the
  percentages share one denominator.
- Arms differ in exactly one dimension, recorded per run.
- Coverage is read from each run's own validated summary, never scraped from
  stdout, so an invalid run contributes nothing rather than a wrong number.

Arms:
  baseline   no manifest route discovery, one constant typed into every field.
             The closest available approximation of the earlier behavior.
  routes     manifest-derived deep links and components, per-field input values.
  gemma      routes, plus a bounded Gemma fallback when the loop stalls.
  gemini     routes, plus a bounded Gemini fallback when the loop stalls.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE_ROOT / "src"
UIAUTOMATOR2_VERSION = "3.7.0"  # Must match valordroid[uiautomator2].

# arm -> (route discovery, per-field input, model provider or None)
# Floor below which a run is skipped rather than started, so a full disk stops
# the campaign cleanly instead of corrupting its tail.
MIN_FREE_BYTES = 8 * 1024**3

ARMS: dict[str, tuple[bool, bool, str | None]] = {
    "baseline": (False, False, None),
    "routes": (True, True, None),
    "gemma": (True, True, "bedrock_mantle"),
    "gemini": (True, True, "vertex_ai"),
}


def log(message: str) -> None:
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {message}\n")
    sys.stderr.flush()


# Faults that belong to the fleet rather than to the app. These are matched
# against the run's own stop reason and error text, so an app that genuinely
# fails to start is never retried on this path.
INFRASTRUCTURE_FAULT_MARKERS = (
    "device offline",
    "device not found",
    "coverage_collection_failed",
    "adb command failed",
    "device is already owned",
    "closed",
    # A dying container answers adb by never answering. One lane lost seven apps
    # in eight minutes this way, each reported as a timeout rather than as an
    # offline device, so the original marker list did not match any of them.
    "timeoutexpired",
    "adb command timed out",
    "timed out after",
    # The framework is not up, so no package can be installed. adb still answers
    # and sys.boot_completed is already 1, so the device looks healthy while
    # nothing can run on it. One lane lost six apps in nineteen seconds this way,
    # each recorded as an app fault on a healthy device.
    "can't find service",
    "cannot find service",
)

# These reasons have two possible authors. The observer gives up when adb stops
# responding, and an app that will not reach the foreground fails the same way,
# so they are only retried once the device has been shown to be unhealthy.
# Amaze fails like this on a perfectly healthy device, and retrying it would just
# spend a second run reaching the same conclusion.
AMBIGUOUS_FAULT_MARKERS = (
    "screen_never_idle_unrecoverable",
    "unrecoverable_runtime_error",
    "screen_never_idle_repeated",
)
DEVICE_RECOVERY_SECONDS = 180.0


def fault_kind(record: dict) -> str | None:
    """Classify a failed run as a fleet fault, an ambiguous one, or neither."""

    if isinstance(record.get("observed_coverage_percent"), (int, float)):
        return None
    haystack = " ".join(
        str(record.get(key) or "") for key in ("stop_reason", "error")
    ).lower()
    if any(marker in haystack for marker in INFRASTRUCTURE_FAULT_MARKERS):
        return "infrastructure"
    if any(marker in haystack for marker in AMBIGUOUS_FAULT_MARKERS):
        return "ambiguous"
    return None


def infrastructure_fault(record: dict) -> bool:
    """Whether this run failed for a fleet reason and deserves one retry."""

    return fault_kind(record) == "infrastructure"


# Every coverage sample records the whole observed unit set, not just what was
# newly covered, so a ledger grows with samples times units. One hour-long run
# produced a 1.6 GiB coverage_events.jsonl, the host disk filled, and nineteen
# runs were skipped or truncated. Compacting each run as it finishes keeps a
# campaign inside its disk, and gzip is how this evidence is published anyway.
COMPACTABLE_LEDGERS = (
    "coverage_events.jsonl",
    "coverage.jsonl",
    "transactions.jsonl",
    "raw-logcat.txt",
)
COMPACT_ABOVE_BYTES = 16 * 1024 * 1024
# How long a lane will wait for another lane's run to finish and be compacted
# before giving up on an app. Long enough to outlast one 60-minute run, because
# that is what frees the space.
DISK_WAIT_SECONDS = 75 * 60


def compact_run_evidence(directory: Path) -> int:
    """Gzip this run's bulky ledgers in place, returning the bytes reclaimed.

    Reversible, and the run manifest still names the universe and bundle hashes,
    so a compacted run can be validated after `gunzip`.
    """

    reclaimed = 0
    for name in COMPACTABLE_LEDGERS:
        path = directory / name
        if not path.is_file():
            continue
        try:
            before = path.stat().st_size
            if before < COMPACT_ABOVE_BYTES:
                continue
            completed = subprocess.run(
                ["gzip", "-1", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=600,
            )
            if completed.returncode == 0:
                after = path.with_suffix(path.suffix + ".gz")
                reclaimed += before - (after.stat().st_size if after.is_file() else 0)
        except Exception:  # noqa: BLE001 - housekeeping must not fail a campaign
            continue
    return reclaimed


def next_attempt_index(*roots: Path) -> int:
    """The next unused attempt ordinal for one cell, across every given root.

    Derived from what is already on disk, so a resumed or re-invoked driver
    appends an attempt instead of overwriting one. An older single-directory
    layout under the same root is left exactly where it is.

    Every root that records attempts for the cell has to be consulted, because
    they are not created by the same process. The driver writes the frozen
    configuration itself, while the run output directory is created by the
    runner subprocess. When the runner never starts -- which is precisely the
    infrastructure fault that earns a retry -- only the configuration side
    exists, and an ordinal taken from the run side alone would be handed out
    twice and overwrite the first attempt's configuration.
    """

    highest = 0
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir() or not child.name.startswith("attempt-"):
                continue
            ordinal = child.name[len("attempt-") :]
            if ordinal.isdigit():
                highest = max(highest, int(ordinal))
    return highest + 1


def terminal_error(directory: Path) -> str:
    """The sentence a run recorded when it gave up, or an empty string.

    Read from the run's own lifecycle ledger because the driver's JSON summary
    carries only a category. Failures here are silent: a missing or unreadable
    ledger simply leaves the classifier with what it had.
    """

    path = directory / "lifecycle.jsonl"
    if not path.is_file():
        return ""
    latest = ""
    try:
        with path.open("rt", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)["payload"]["event"]
                except Exception:  # noqa: BLE001 - a torn line is not fatal
                    continue
                error = event.get("error")
                if isinstance(error, str) and error.strip():
                    latest = error.strip()
    except OSError:
        return ""
    return latest[:400]


def device_healthy(serial: str) -> bool:
    """Whether an ambiguous failure may be blamed on the app.

    This asks the same question as `device_ready`, because `sys.boot_completed`
    alone is not an answer to it. In the v2 campaign device 5562 lost its package
    service at 07:25 and reported boot_completed=1 throughout; three apps in a row
    -- Phonograph, Vespucci and ownCloud -- were logged as "failed on a healthy
    device" and written off, when every one of them had failed on
    `cmd: Can't find service: package`. ownCloud was the run carrying the
    campaign's login fixture, so the cost of the weaker probe was the app that
    most needed to be measured.
    """

    return device_ready(serial)


def device_ready(serial: str) -> bool:
    """Whether the device can actually run something, not merely answer adb.

    `sys.boot_completed` turns 1 well before the package service is published,
    and a framework that is restarting keeps adb responsive throughout. A device
    in that state accepts every command and refuses every install with
    "cmd: Can't find service: package", which reads as an app fault. Asking the
    package manager a trivial question is the cheapest proof it is really up.
    """

    for arguments in (
        ["shell", "getprop", "sys.boot_completed"],
        ["shell", "cmd", "package", "path", "android"],
    ):
        try:
            probe = subprocess.run(
                ["adb", "-s", serial, *arguments],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return False
        if probe.returncode != 0:
            return False
        output = probe.stdout.strip()
        if not output or "find service" in output:
            return False
        if arguments[-1] == "sys.boot_completed" and output != "1":
            return False
    return True


def wait_for_device(serial: str, *, timeout: float) -> bool:
    """Block until the device is usable again, or give up."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if device_ready(serial):
            return True
        time.sleep(5.0)
    return False


def run_cli(arguments: list[str], *, timeout: float):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SOURCE)
    return subprocess.run(
        [sys.executable, "-m", "valordroid", *arguments],
        cwd=str(PACKAGE_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout,
    )


@dataclass
class AppResult:
    name: str
    package: str | None = None
    units: int | None = None
    prepared: str | None = None
    policy: str | None = None
    prepare_error: str | None = None
    # Intent context each directly-launchable activity reads, so the ladder can
    # mock it instead of firing a bare intent into an activity that needs one.
    component_extras: dict = field(default_factory=dict)
    runs: dict = field(default_factory=dict)


def runtime_config(
    *,
    serial: str,
    launcher: str | None,
    max_seconds: float,
    max_actions: int,
    per_field: bool,
    component_extras: dict | None = None,
    suppress_soft_keyboard: bool = True,
    webview_url_patterns: Sequence[str] = (),
    semantic_state_enabled: bool = False,
    uiautomator2_fallback_enabled: bool = False,
    foreign_workflows: Sequence[Mapping[str, Any]] = (),
    capture_screenshots: bool = False,
    visual_escape_enabled: bool = False,
) -> dict:
    value = {
        "schema_version": 4,
        "serial": serial,
        "launcher": launcher,
        "max_actions": max_actions,
        "max_seconds": max_seconds,
        "adb_timeout_seconds": 40.0,
        "install_timeout_seconds": 300.0,
        "launch_timeout_seconds": 60.0,
        # A route that does not foreground quickly has not matched. Waiting the
        # cold-start timeout for it wastes the whole budget.
        "route_foreground_timeout_seconds": 4.0,
        "observation_timeout_seconds": 60.0,
        "action_timeout_seconds": 40.0,
        "post_action_delay_seconds": 0.6,
        "pid_poll_seconds": 0.25,
        "uninstall_after_run": True,
        # Off unless an app's enhancement entry asks for it. A screenshot is the
        # one artifact that can hold a secret, and the visual escape refuses
        # itself outright for a run that ever held one, so this is never enabled
        # campaign-wide.
        "capture_screenshots": capture_screenshots,
        "text_input_value": "valordroid",
        "per_field_input_enabled": per_field,
        "deep_links": [],
        "exported_components": [],
        "forced_components": [],
        "reset_seed_enabled": True,
        "suppress_soft_keyboard": suppress_soft_keyboard,
        "route_discovery_sha256": None,
        "component_extras": component_extras or {},
    }
    if semantic_state_enabled:
        value["semantic_state_enabled"] = True
    if uiautomator2_fallback_enabled:
        value["uiautomator2_fallback_enabled"] = True
    patterns = sorted(set(webview_url_patterns))
    if patterns:
        value["webview_enabled"] = True
        value["webview_url_patterns"] = patterns
    # Emitted only when present, so a campaign that asks for neither produces
    # byte-identical configuration to before.
    if foreign_workflows:
        value["foreign_workflows"] = [dict(item) for item in foreign_workflows]
    if visual_escape_enabled:
        value["visual_escape_enabled"] = True
    return value


def core_config(
    *,
    model: bool,
    max_model_calls: int,
    max_seconds: float,
    allow_forced: bool = False,
    launch_on_exhaustion: bool = False,
    max_visual_escapes: int = 0,
) -> dict:
    return {
        # Scaled to the budget: a one-hour run should not declare a stall as
        # eagerly as a four-minute one.
        "stall_seconds": max(20.0, min(90.0, max_seconds / 40.0)),
        "stall_actions": 12,
        "association_settle_seconds": 1.2,
        "retry_cooldown_seconds": 45.0,
        "initial_no_yield_attempts": 2,
        "productive_yield_floor": 0.05,
        # Rung 7 launches components the manifest does not export, which no
        # real user or peer app could reach. Off by default so a coverage number
        # means "reachable"; opt in to measure how much code is gated behind
        # screens the GUI cannot get to. The summary keeps these separate as
        # all_route_* versus primary_gui_deep_link_*.
        "allow_forced_routes": allow_forced,
        # Release component launches when the frontier is measurably
        # exhausted rather than at 60% of the clock. 39% of v2 runs recorded
        # their last coverage gain before the clock gate opened.
        "launch_on_frontier_exhaustion": launch_on_exhaustion,
        "max_llm_candidates": 20,
        "max_navigation_depth": 4,
        "max_ladder_resets": 500,
        "model_assistance_enabled": model,
        "max_model_calls": max_model_calls if model else 0,
        # In a model arm the ladder position is the gate, so the model is
        # actually reached instead of sitting below five deterministic rungs.
        "model_first_on_stall": model,
        # Model arms decide at step level. As a recovery rung alone the model was
        # consulted about seven times in a 70-action run and the arms measured
        # within 0.05pp of the deterministic one.
        "model_step_selection": model,
        "model_step_stride": 1,
        # Zero unless an app's enhancement entry asks for a budget. The
        # mechanism additionally needs visual_escape_enabled in the runtime
        # configuration and a provider whose protocol can carry an image, and it
        # refuses itself for any run that has held a secret.
        "max_visual_escapes": max_visual_escapes if model else 0,
    }


def model_config(*, provider: str, model_name: str, max_calls: int, project: str, region: str) -> dict:
    vertex = provider == "vertex_ai"
    return {
        "schema_version": 1,
        "enabled": True,
        "provider": provider,
        "model": model_name,
        "base_url": None,
        "region": region if vertex else "us-east-1",
        "project": project if vertex else None,
        "timeout_seconds": 60.0,
        "max_calls_per_run": max_calls,
        "max_calls_per_purpose": max_calls,
        "max_prompt_characters": 8000,
        "max_candidates_offered": 15,
        "max_response_characters": 4000,
        "max_output_tokens": 256,
        "use_application_default_credentials": vertex,
        # The ladder position gates the model, so no extra threshold is needed.
        "recovery_escalation_threshold": 0,
        "field_retry_threshold": 1,
    }


# Launcher activities that belong to bundled diagnostic tooling rather than the
# app. LeakCanary registers a launcher that does not exist in a release-shaped
# runtime, and picking it alphabetically made `am start` fail with "Activity
# class does not exist".
_NON_APP_LAUNCHER_ROOTS = (
    "com.squareup.leakcanary",
    "leakcanary",
    "androidx.test",
)


def _analyzer_toolchain(configs: Sequence[Path]) -> tuple[Path, Path] | None:
    """The androlog jar and android-platforms directory, from any pinned config.

    Every config is searched rather than the first, because a campaign pins one
    config per instrumentation policy and only the one that actually uses
    AndroLog carries the jar. The 50-app v2 campaign passed the smali policy
    first, that file lists no `androlog` entry, and the resulting `StopIteration`
    was swallowed by a bare `except` in the caller. `component_extras` came back
    empty for all 100 runs, so every non-exported activity stayed unreachable and
    rung 7 never fired once. The data was in the second config the whole time.
    """

    for config in configs:
        try:
            pinned = json.loads(Path(config).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        jar = next(
            (
                Path(item["path"])
                for item in pinned.get("toolchain") or ()
                if type(item) is dict and item.get("name") == "androlog"
            ),
            None,
        )
        platforms = (pinned.get("environment") or {}).get("ANDROID_PLATFORMS")
        if jar is None or not platforms:
            continue
        if jar.is_file() and Path(platforms).is_dir():
            return jar, Path(platforms)
    return None


def discover_component_extras(
    apk: Path, *, package: str, configs: Sequence[Path]
) -> dict:
    """What each activity reads from its Intent, as `component_extras` entries.

    Read-only static analysis: the APK is not modified, so the measured binary
    stays the shipped binary. A failure returns nothing and the ladder launches
    components exactly as it did before -- but it now says so, because a silent
    empty result here is indistinguishable from an app that reads nothing, and
    that ambiguity hid the bug above for a whole campaign.
    """

    sys.path.insert(0, str(PACKAGE_ROOT / "tools"))
    try:
        import activity_context
    except Exception:  # noqa: BLE001 - optional enrichment, never fatal
        print(f"    activity-context: import failed for {package}", flush=True)
        return {}
    toolchain = _analyzer_toolchain(configs)
    if toolchain is None:
        print(
            "    activity-context: no pinned config carries both an androlog jar "
            f"and ANDROID_PLATFORMS, skipping {package}",
            flush=True,
        )
        return {}
    jar, platforms = toolchain
    report = activity_context.analyze(apk, androlog_jar=jar, platforms=platforms)
    extras = activity_context.component_extras(report, package=package)
    analysed = len(report.get("activities") or {}) if type(report) is dict else 0
    print(
        f"    activity-context: {package} -> {analysed} activities read their "
        f"Intent, {len(extras)} received mocked context",
        flush=True,
    )
    return extras


APP_ENHANCEMENT_SCHEMA = 1
_ENHANCEMENT_KEYS = frozenset(
    {
        "foreign_workflows",
        "capture_screenshots",
        "visual_escape_enabled",
        "max_visual_escapes",
    }
)


@dataclass(frozen=True)
class AppEnhancements:
    """The per-app opt-in mechanisms the driver could not previously express.

    `runtime_config` accepted semantic refinement, UIAutomator2 and WebView and
    nothing else, hardcoded `capture_screenshots` to False, and `core_config`
    emitted no visual budget. So a campaign could not switch on a foreign
    workflow or a visual escape at all, however completely the runtime and the
    runner implemented them.

    A file rather than flags, because a foreign workflow is structured data: an
    entry action, the packages it may enter, the action kinds it may use, its own
    action and time bounds, and a return predicate tied to the object it went to
    fetch. None of that survives a command line.
    """

    foreign_workflows: tuple[Mapping[str, Any], ...] = ()
    capture_screenshots: bool = False
    visual_escape_enabled: bool = False
    max_visual_escapes: int = 0


def load_app_enhancements(path: Path | None) -> dict[str, AppEnhancements]:
    """Read and fully validate a per-app enhancement file, or return nothing.

    Validation happens here, before any device is touched, by building the real
    `RuntimeConfig` and `CoreConfig` from a generated pair. Those classes own the
    rules -- a visual escape needs screenshot capture, a visual budget needs
    model assistance and cannot exceed the model call budget, a foreign workflow
    needs a non-empty sorted package list and a bounded return predicate -- and
    restating them here would let the two drift. A campaign that would be refused
    on the device is refused now instead, at the cost of nothing.
    """

    if path is None:
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if type(document) is not dict:
        raise SystemExit(f"{path}: app enhancements must be a JSON object")
    if document.get("schema") != APP_ENHANCEMENT_SCHEMA:
        raise SystemExit(
            f"{path}: unsupported app enhancement schema {document.get('schema')!r}"
        )
    apps = document.get("apps")
    if type(apps) is not dict or not apps:
        raise SystemExit(f"{path}: app enhancements must name at least one app")
    loaded: dict[str, AppEnhancements] = {}
    for app, raw in sorted(apps.items()):
        if type(raw) is not dict:
            raise SystemExit(f"{path}: {app} enhancements must be a JSON object")
        unknown = sorted(set(raw) - _ENHANCEMENT_KEYS)
        if unknown:
            raise SystemExit(
                f"{path}: {app} declares unknown enhancement keys {unknown}; "
                f"supported keys are {sorted(_ENHANCEMENT_KEYS)}"
            )
        workflows = raw.get("foreign_workflows", [])
        if type(workflows) is not list or any(
            type(item) is not dict for item in workflows
        ):
            raise SystemExit(
                f"{path}: {app} foreign_workflows must be an array of objects"
            )
        entry = AppEnhancements(
            foreign_workflows=tuple(
                _with_canonical_target(dict(item), app=app, source=path)
                for item in workflows
            ),
            capture_screenshots=bool(raw.get("capture_screenshots", False)),
            visual_escape_enabled=bool(raw.get("visual_escape_enabled", False)),
            max_visual_escapes=int(raw.get("max_visual_escapes", 0) or 0),
        )
        _validate_enhancements(app, entry, source=path)
        loaded[str(app)] = entry
    log(
        f"app enhancements: {len(loaded)} apps from {path.name} "
        f"({sum(len(item.foreign_workflows) for item in loaded.values())} "
        "foreign workflows)"
    )
    return loaded


def _with_canonical_target(
    workflow: dict, *, app: str, source: Path
) -> dict:
    """Fill an omitted `entry_action.target_id`, which is derived from the selector.

    `ForeignWorkflow` requires `target_id == stable_hash(canonical_selector)`, so
    the value is determined entirely by the selector already in the file. Making
    an author compute a SHA-256 by hand adds a way to get it wrong and no way to
    get anything else right. A value that is present is left alone and still
    checked, so a stale one is still refused rather than quietly corrected.
    """

    sys.path.insert(0, str(SOURCE))
    from valordroid.foreign_workflow import _selector
    from valordroid.models import stable_hash

    action = workflow.get("entry_action")
    if type(action) is not dict or action.get("target_id"):
        return workflow
    try:
        selector = _selector(
            action.get("selector") or (action.get("parameters") or {}).get("selector"),
            "foreign workflow entry_action.parameters.selector",
        )
    except (TypeError, ValueError) as error:
        raise SystemExit(
            f"{source}: {app} foreign workflow "
            f"{workflow.get('workflow_id')!r} has an unusable selector: {error}"
        )
    resolved = dict(action)
    resolved["target_id"] = stable_hash(selector)
    return {**workflow, "entry_action": resolved}


def _validate_enhancements(
    app: str, entry: AppEnhancements, *, source: Path
) -> None:
    """Build the real configuration objects so an invalid campaign fails now."""

    sys.path.insert(0, str(SOURCE))
    from valordroid.config import CoreConfig
    from valordroid.runtime_config import RuntimeConfig

    runtime = runtime_config(
        serial="127.0.0.1:5555",
        launcher=None,
        max_seconds=60.0,
        max_actions=10,
        per_field=True,
        foreign_workflows=entry.foreign_workflows,
        capture_screenshots=entry.capture_screenshots,
        visual_escape_enabled=entry.visual_escape_enabled,
    )
    # A visual budget is only reachable in a model arm, so it is validated
    # against one; a deterministic arm zeroes it in `core_config`.
    core = core_config(
        model=True,
        max_model_calls=max(1, entry.max_visual_escapes),
        max_seconds=60.0,
        max_visual_escapes=entry.max_visual_escapes,
    )
    try:
        RuntimeConfig.from_mapping(runtime)
        CoreConfig.from_mapping(core)
    except (TypeError, ValueError) as error:
        raise SystemExit(f"{source}: {app} enhancements are invalid: {error}")
    if entry.max_visual_escapes and not entry.visual_escape_enabled:
        raise SystemExit(
            f"{source}: {app} sets max_visual_escapes without "
            "visual_escape_enabled, so the budget could never be spent"
        )
    if entry.visual_escape_enabled and not entry.max_visual_escapes:
        raise SystemExit(
            f"{source}: {app} enables the visual escape with a zero budget, "
            "which reads as enabled and does nothing"
        )
    for workflow in entry.foreign_workflows:
        predicate = workflow.get("return_predicate") or {}
        if predicate.get("kind") == "package_foreground":
            # A campaign policy, stricter than the runtime's. The point of a
            # picker episode is the object it brings back, and the app returning
            # to the foreground says only that the episode ended. A workflow that
            # cannot tell "picked an image" from "pressed Back" has no
            # postcondition worth measuring.
            raise SystemExit(
                f"{source}: {app} foreign workflow "
                f"{workflow.get('workflow_id')!r} returns on package_foreground "
                "alone; bind the predicate to the object the workflow fetched, "
                "for example node_present on the element that now shows it"
            )


def merge_declared_component_context(
    analyzed: Mapping[str, Sequence[Sequence[str]]],
    profile_path: Path,
    *,
    app: str,
) -> dict:
    """Add a setup profile's declared intent context to the analyzed context.

    The analyzer reports what each activity *reads*; a profile declares the
    concrete object it should read, because only the profile knows which file it
    pushed. Neither half is sufficient alone: the analyzer cannot invent a URI
    that points at real bytes, and the profile does not enumerate every activity.

    Without this merge the declaration is inert. The Amaze fixture has declared
    `file:///sdcard/valordroid/amaze-notes.txt` for TextReader since it was
    written, and no generated runtime has ever carried it, so the runner's new
    preparation check would refuse every run that uses that profile.

    A conflict is refused rather than resolved. If the analyzer and the profile
    disagree on the value for one key, silently preferring either one publishes a
    launch nobody declared.
    """

    if not profile_path.is_file():
        return {key: [list(entry) for entry in value] for key, value in analyzed.items()}
    sys.path.insert(0, str(SOURCE))
    from valordroid.setup_profile import SetupProfile

    profile = SetupProfile.load(profile_path)
    merged = {key: [list(entry) for entry in value] for key, value in analyzed.items()}
    added = 0
    for component, declared in sorted(profile.component_intent_context.items()):
        entries = merged.setdefault(component, [])
        by_key = {(str(item[0]), str(item[1])): str(item[2]) for item in entries}
        for key, kind, value in declared:
            existing = by_key.get((str(key), str(kind)))
            if existing is not None and existing != str(value):
                raise SystemExit(
                    f"{app}: setup profile declares {kind}:{key}={value} for "
                    f"{component} but the static analysis derived {existing}; "
                    "resolve the conflict rather than letting one win silently"
                )
            if existing is None:
                entries.append([str(key), str(kind), str(value)])
                by_key[(str(key), str(kind))] = str(value)
                added += 1
    log(
        f"  declared-context: {app} -> merged {added} entries from "
        f"{profile_path.name} across {len(profile.component_intent_context)} components"
    )
    return merged


def launcher_for(apk: Path, package: str) -> str | None:
    completed = subprocess.run(
        ["aapt", "dump", "badging", str(apk)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    names = sorted(
        {
            line.split("name='", 1)[1].split("'", 1)[0]
            for line in completed.stdout.splitlines()
            if line.startswith("launchable-activity:")
        }
    )
    if not names:
        # Apps that declare their entry point via <activity-alias> report no
        # launchable-activity at all under this aapt, so fall back to the
        # manifest. Without this, multi-candidate alias apps reach the runner
        # with no override and the validator rejects the ambiguous launch.
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
            from valordroid.android.apk import _launchers_from_manifest

            names = sorted(
                set(
                    _launchers_from_manifest(
                        apk, aapt="aapt", timeout_seconds=60.0
                    )
                )
            )
        except Exception:  # noqa: BLE001 - detection stays best-effort
            names = []
    if len(names) <= 1:
        # One candidate (or none) needs no override; the runner uses it directly.
        return None
    # Drop diagnostic launchers, then prefer one under the app's own package.
    app_owned = [
        name
        for name in names
        if not name.startswith(_NON_APP_LAUNCHER_ROOTS)
        and name.startswith(package.rsplit(".debug", 1)[0].rsplit(".", 1)[0])
    ]
    real = [name for name in names if not name.startswith(_NON_APP_LAUNCHER_ROOTS)]
    chosen = app_owned or real or names
    return chosen[0]


def existing_bundle(apk: Path, output: Path) -> AppResult | None:
    """Reuse an already-verified bundle so iteration does not re-instrument.

    Instrumenting fifty apps costs most of an hour, and a prepared bundle is
    immutable and self-verifying, so reusing one is safe.
    """

    manifest = output / "prepared.json"
    if not manifest.is_file():
        return None
    try:
        document = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    result = AppResult(name=apk.stem)
    result.package = document.get("package_name")
    result.units = document.get("unit_count")
    result.policy = document.get("inclusion_policy")
    result.prepared = str(output)
    return result


def prepare_once(apk: Path, output: Path, config: Path, timeout: float) -> AppResult:
    result = AppResult(name=apk.stem)
    if output.exists():
        shutil.rmtree(output)
    try:
        completed = run_cli(
            [
                "prepare-apk",
                "--original-apk",
                str(apk),
                "--instrumenter-config",
                str(config),
                "--output",
                str(output),
            ],
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result.prepare_error = f"prepare-apk timed out after {timeout}s"
        return result
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        result.prepare_error = detail[-1][:300] if detail else f"exit {completed.returncode}"
        return result
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result.prepare_error = "prepare-apk produced unparsable output"
        return result
    result.package = document.get("package_name")
    result.units = document.get("unit_count")
    result.policy = document.get("inclusion_policy")
    result.prepared = str(output)
    return result


def prepare(
    apk: Path, output: Path, configs: list[Path], timeout: float, *, reuse: bool = False
) -> AppResult:
    """Try each pinned configuration in order; keep the first that succeeds."""

    if reuse:
        cached = existing_bundle(apk, output)
        if cached is not None:
            return cached
    last = AppResult(name=apk.stem, prepare_error="no instrumenter configuration supplied")
    for config in configs:
        candidate = prepare_once(apk, output, config, timeout)
        if candidate.prepared:
            return candidate
        last = candidate
    return last


def execute_run(
    *,
    app: AppResult,
    arm: str,
    serial: str,
    work: Path,
    apk: Path,
    max_seconds: float,
    max_actions: int,
    models: dict[str, str],
    max_model_calls: int,
    project: str,
    region: str,
    allow_forced: bool = False,
    launch_on_exhaustion: bool = False,
    component_extras: dict | None = None,
    setup_profile_dir: Path | None = None,
    suppress_soft_keyboard: bool = True,
    webview_url_patterns: Sequence[str] = (),
    semantic_state_enabled: bool = False,
    uiautomator2_fallback_enabled: bool = False,
    enhancements: AppEnhancements | None = None,
) -> dict:
    assert app.prepared is not None
    discover, per_field, provider = ARMS[arm]
    enhancements = enhancements or AppEnhancements()
    tag = f"{app.name}.{arm}"
    # Immutable per-attempt directories, never reused and never deleted. The
    # previous layout wrote every attempt of a cell to one path and removed it
    # first, so an infrastructure retry destroyed the evidence of the attempt it
    # was retrying -- and that first attempt's terminal record is the only
    # account of why a retry happened at all. Reusing the work directory for a
    # second execution took the same path.
    run_root = work / "runs" / tag
    run_root.mkdir(parents=True, exist_ok=True)
    config_root = work / "configs" / tag
    attempt_index = next_attempt_index(run_root, config_root)
    directory = run_root / f"attempt-{attempt_index:04d}"
    configs = config_root / f"attempt-{attempt_index:04d}"
    configs.mkdir(parents=True, exist_ok=True)

    runtime_path = configs / "runtime.json"
    runtime_path.write_text(
        json.dumps(
            runtime_config(
                serial=serial,
                launcher=launcher_for(apk, app.package or ""),
                component_extras=component_extras,
                max_seconds=max_seconds,
                max_actions=max_actions,
                per_field=per_field,
                suppress_soft_keyboard=suppress_soft_keyboard,
                webview_url_patterns=webview_url_patterns,
                semantic_state_enabled=semantic_state_enabled,
                uiautomator2_fallback_enabled=uiautomator2_fallback_enabled,
                foreign_workflows=enhancements.foreign_workflows,
                capture_screenshots=enhancements.capture_screenshots,
                visual_escape_enabled=enhancements.visual_escape_enabled,
            ),
            indent=2,
        )
    )
    core_path = configs / "core.json"
    core_path.write_text(
        json.dumps(
            core_config(
                model=provider is not None,
                max_model_calls=max_model_calls,
                max_seconds=max_seconds,
                allow_forced=allow_forced,
                launch_on_exhaustion=launch_on_exhaustion,
                max_visual_escapes=enhancements.max_visual_escapes,
            ),
            indent=2,
        )
    )
    arguments = [
        "run-android",
        "--prepared",
        app.prepared,
        "--runtime-config",
        str(runtime_path),
        "--core-config",
        str(core_path),
        "--run-id",
        tag,
        "--output",
        str(directory),
        "--adb",
        "adb",
    ]
    if discover:
        arguments.append("--discover-routes")
    # A setup profile is per app and optional: `<dir>/<app>.json`. Apps needing a
    # server address or an account get one, everything else is unaffected, so a
    # campaign can mix prepared and unprepared apps in the same wave.
    if setup_profile_dir is not None:
        profile = setup_profile_dir / f"{app.name}.json"
        if profile.is_file():
            arguments += ["--setup-profile", str(profile)]
    if provider is not None:
        model_path = configs / "model.json"
        model_path.write_text(
            json.dumps(
                model_config(
                    provider=provider,
                    model_name=models[arm],
                    max_calls=max_model_calls,
                    project=project,
                    region=region,
                ),
                indent=2,
            )
        )
        arguments += ["--model-config", str(model_path)]

    # Refuse to start a run when the disk is nearly full. An hour of logcat can
    # exhaust the last few gigabytes, and once that happens every subsequent run
    # aborts in a second, silently invalidating the tail of the campaign.
    # Wait for space rather than writing the app off. The coverage ledgers grow
    # with samples times units, so a wave of concurrent runs can approach the
    # floor and then recover as each finishing run is compacted. v1 skipped
    # instead of waiting and that is the whole reason its campaign covered 49 of
    # 50 apps: Vinyl-Music-Player was abandoned at "only 0 GiB free", never
    # measured, and the app itself was fine -- it reached 19.79% the first time it
    # was given a device with room.
    usage = shutil.disk_usage(str(work))
    if usage.free < MIN_FREE_BYTES:
        log(
            f"  [{serial}] {app.name} waiting for disk: "
            f"{usage.free // (1024**3)} GiB free, need "
            f"{MIN_FREE_BYTES // (1024**3)} GiB"
        )
        deadline = time.monotonic() + DISK_WAIT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(30.0)
            usage = shutil.disk_usage(str(work))
            if usage.free >= MIN_FREE_BYTES:
                break
    if usage.free < MIN_FREE_BYTES:
        return {
            "arm": arm,
            "serial": serial,
            "error": f"skipped: only {usage.free // (1024**3)} GiB free, below the "
            f"{MIN_FREE_BYTES // (1024**3)} GiB floor after waiting "
            f"{DISK_WAIT_SECONDS / 60:.0f} min",
        }
    # Do not hand an app to a device that cannot install one. Waiting here turns
    # a lane that would have burned its whole app list in seconds into a lane
    # that pauses and then works, and it costs nothing when the device is fine.
    if not device_ready(serial) and not wait_for_device(
        serial, timeout=DEVICE_RECOVERY_SECONDS
    ):
        return {
            "arm": arm,
            "serial": serial,
            "error": "skipped: device framework never became ready",
        }

    # Uninstall the target package from this device before the run. An earlier
    # aborted run can leave its app installed, and a leftover foreign app has
    # been observed keeping its activity resolvable in a way that confuses the
    # next launch. The runner also fresh-installs, but this guarantees a clean
    # slate even when the previous run died before its own cleanup.
    if app.package:
        subprocess.run(
            ["adb", "-s", serial, "uninstall", app.package],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=120,
        )

    started = time.monotonic()
    record: dict = {
        "arm": arm,
        "serial": serial,
        "run": str(directory),
        "run_root": str(run_root),
        "attempt": attempt_index,
        "runtime_config": str(runtime_path),
    }
    if provider is not None:
        record["provider"] = provider
        record["model"] = models[arm]
    try:
        completed = run_cli(arguments, timeout=max_seconds + 1800.0)
    except subprocess.TimeoutExpired:
        record["error"] = "run-android timed out"
        return record
    record["exit_code"] = completed.returncode
    record["wall_seconds"] = round(time.monotonic() - started, 1)
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError:
        tail = (completed.stderr or completed.stdout).strip().splitlines()
        record["error"] = tail[-1][:300] if tail else "unparsable run-android output"
        return record
    for key in ("status", "stop_reason", "attempts", "validation_ok", "model_calls"):
        record[key] = document.get(key)
    # An aborted run reports a generic stop_reason such as
    # "unrecoverable_runtime_error", and the sentence naming the actual cause is
    # only in the run's own lifecycle ledger. Without it the classifier had
    # nothing to match: ownCloud aborted on "cmd: Can't find service: package"
    # and was recorded as an app fault because the driver never saw that text.
    if not record.get("error") and record.get("status") != "finished":
        record["error"] = terminal_error(directory)
    record["route_targets"] = document.get("discovered_route_targets")
    record["issues"] = [item.get("code") for item in document.get("issues") or []]
    summary = document.get("summary")
    if isinstance(summary, dict):
        for key in (
            "run_id",
            "total_units",
            "observed_covered_units",
            "observed_coverage_percent",
            "primary_gui_deep_link_units",
            "primary_gui_deep_link_coverage_percent",
            "all_route_covered_units",
            "all_route_coverage_percent",
            "additional_intent_forced_association_units",
            "additional_intent_forced_association_percent",
            "outside_route_association_units",
            "outside_route_association_percent",
            "route_association_rule",
            "first_collected_by_provenance",
            "unattributed_units",
            "webview_observation",
            "uiautomator2_fallback",
            "action_attempts",
            "app_owned_crash_events",
            "prompt_tokens",
            "completion_tokens",
        ):
            record[key] = summary.get(key)
    return record


def install_child_reaper() -> None:
    """Take every `run-android` child down with the driver.

    A child holds its device's flock for as long as it lives, and flock is only
    released when the holder dies. Stopping a campaign with `pkill -f
    run_coverage_experiment.py` matches the driver and not its children, so an
    orphan survives and owns a device: one held 127.0.0.1:5561 for 45 minutes,
    and every app handed to that lane afterwards aborted in seconds with
    "device is already owned by another VALOR-Droid run".

    Children inherit this process group, so signalling the group reaches them.
    The handler resets itself to the default first, otherwise signalling the
    group re-enters it.
    """

    def reap(signum: int, _frame: object) -> None:
        with contextlib.suppress(Exception):
            signal.signal(signum, signal.SIG_DFL)
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(0), signal.SIGTERM)
        with contextlib.suppress(Exception):
            os.kill(os.getpid(), signum)

    for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        with contextlib.suppress(ValueError, OSError):
            signal.signal(name, reap)


def main() -> int:
    install_child_reaper()
    parser = argparse.ArgumentParser()
    parser.add_argument("--apks", nargs="+", required=True)
    parser.add_argument("--serials", nargs="+", required=True)
    parser.add_argument("--instrumenter-config", nargs="+", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--dataset", type=Path, default=REPOSITORY / "dataset" / "apks")
    # Directory of optional per-app setup profiles, named "<app>.json". Present
    # for the apps that need a server address or an account, absent for the rest.
    parser.add_argument("--setup-profile-dir", type=Path, default=None)
    # Per-app opt-in mechanisms that cannot be expressed as flags: foreign
    # workflow authorizations, screenshot capture and the visual escape budget.
    # Validated in full before any device is touched.
    parser.add_argument(
        "--app-enhancements",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "JSON file of per-app foreign workflows and visual-escape settings; "
            "see AppEnhancements. Absent means every mechanism stays disabled"
        ),
    )
    parser.add_argument(
        "--webview-url-pattern",
        action="append",
        default=[],
        metavar="APP=URL_GLOB",
        help=(
            "enable DOM observation for one app and allow only matching page URLs; "
            "repeat for multiple patterns, for example "
            "Jellyfin-Android=https://media.example/*"
        ),
    )
    parser.add_argument(
        "--semantic-state",
        action="store_true",
        help=(
            "split coarse states on bounded checked/selected/editable/list semantics; "
            "recorded in each run's runtime configuration"
        ),
    )
    parser.add_argument(
        "--uiautomator2-fallback",
        action="store_true",
        help=(
            "after repeated native idle refusals, latch to the pinned persistent "
            "UIAutomator2 hierarchy service for the rest of each run"
        ),
    )
    # The keyboard is disabled by default because it crashes some Compose text
    # fields and covers the buttons a dump just located. Kept for attribution.
    parser.add_argument("--keep-soft-keyboard", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=3600.0)
    parser.add_argument("--max-actions", type=int, default=20000)
    parser.add_argument("--arms", nargs="+", default=["baseline", "gemma", "gemini"])
    parser.add_argument("--gemma-model", default="google.gemma-3-4b-it")
    parser.add_argument("--gemini-model", default="gemini-2.5-flash")
    parser.add_argument("--vertex-project", default="")
    parser.add_argument("--vertex-region", default="us-central1")
    parser.add_argument("--max-model-calls", type=int, default=120)
    parser.add_argument("--prepare-timeout", type=float, default=2700.0)
    parser.add_argument("--prepare-workers", type=int, default=5)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reuse-prepared", action="store_true")
    parser.add_argument(
        "--no-mock-activity-context",
        dest="mock_activity_context",
        action="store_false",
        help="skip the read-only analysis of what activities read from their "
             "Intent, and launch them bare as before",
    )
    parser.add_argument(
        "--launch-on-exhaustion",
        action="store_true",
        help="release component launches as soon as the frontier is exhausted "
             "instead of waiting for 60%% of the budget",
    )
    parser.add_argument(
        "--allow-forced-routes",
        action="store_true",
        help="permit rung 7 to launch non-exported components; reported separately "
             "as all_route_* because it is not GUI-reachable",
    )
    parser.add_argument("--prepare-only", action="store_true")
    arguments = parser.parse_args()

    collected_webview_patterns: dict[str, list[str]] = {}
    for specification in arguments.webview_url_pattern:
        if "=" not in specification:
            raise SystemExit(
                "--webview-url-pattern must be APP=URL_GLOB"
            )
        app_name, pattern = specification.split("=", 1)
        app_name, pattern = app_name.strip(), pattern.strip()
        if not app_name or not pattern:
            raise SystemExit(
                "--webview-url-pattern must contain a nonempty app and pattern"
            )
        collected_webview_patterns.setdefault(app_name, []).append(pattern)
    unknown_webview_apps = sorted(
        set(collected_webview_patterns) - set(arguments.apks)
    )
    if unknown_webview_apps:
        raise SystemExit(
            "WebView patterns name apps outside --apks: "
            + ", ".join(unknown_webview_apps)
        )
    webview_patterns = {
        app_name: tuple(sorted(set(patterns)))
        for app_name, patterns in collected_webview_patterns.items()
    }
    # Loaded and fully validated before any device is touched, so a campaign
    # that the runtime would refuse on the device is refused here.
    enhancements = load_app_enhancements(arguments.app_enhancements)
    unknown_enhancement_apps = sorted(set(enhancements) - set(arguments.apks))
    if unknown_enhancement_apps:
        raise SystemExit(
            "app enhancements name apps outside --apks: "
            + ", ".join(unknown_enhancement_apps)
        )
    if webview_patterns:
        try:
            import websocket  # noqa: F401
        except ImportError as error:
            raise SystemExit(
                "WebView observation needs the pinned valordroid[webview] extra"
            ) from error
    if arguments.uiautomator2_fallback:
        try:
            import uiautomator2  # noqa: F401

            installed_uiautomator2 = importlib.metadata.version("uiautomator2")
        except (ImportError, importlib.metadata.PackageNotFoundError) as error:
            raise SystemExit(
                "persistent fallback needs the pinned valordroid[uiautomator2] extra"
            ) from error
        if installed_uiautomator2 != UIAUTOMATOR2_VERSION:
            raise SystemExit(
                "persistent fallback requires exactly "
                f"uiautomator2=={UIAUTOMATOR2_VERSION}; found "
                f"{installed_uiautomator2}"
            )

    for arm in arguments.arms:
        if arm not in ARMS:
            raise SystemExit(f"unknown arm: {arm}; expected {sorted(ARMS)}")
    models = {"gemma": arguments.gemma_model, "gemini": arguments.gemini_model}
    if "gemini" in arguments.arms and not arguments.prepare_only and not arguments.vertex_project:
        raise SystemExit("the gemini arm needs --vertex-project")
    arguments.work.mkdir(parents=True, exist_ok=True)

    apks = [arguments.dataset / f"{name}.apk" for name in arguments.apks]
    missing = [str(path) for path in apks if not path.is_file()]
    if missing:
        raise SystemExit(f"missing APKs: {missing}")

    log(f"instrumenting {len(apks)} apps with {arguments.prepare_workers} workers")
    results: dict[str, AppResult] = {}
    with concurrent.futures.ThreadPoolExecutor(arguments.prepare_workers) as pool:
        futures = {
            pool.submit(
                prepare,
                apk,
                arguments.work / "prepared" / apk.stem,
                list(arguments.instrumenter_config),
                arguments.prepare_timeout,
                reuse=arguments.reuse_prepared,
            ): apk
            for apk in apks
        }
        for future in concurrent.futures.as_completed(futures):
            apk = futures[future]
            result = future.result()
            if result.prepared and result.package and arguments.mock_activity_context:
                result.component_extras = discover_component_extras(
                    Path(result.prepared) / "instrumented.apk",
                    package=result.package,
                    configs=list(arguments.instrumenter_config),
                )
            # A profile's declared context is merged even when the static
            # analysis is off, because the profile is the only source that knows
            # which object it actually pushed to the device.
            if result.prepared and arguments.setup_profile_dir is not None:
                result.component_extras = merge_declared_component_context(
                    result.component_extras,
                    arguments.setup_profile_dir / f"{result.name}.json",
                    app=result.name,
                )
            results[result.name] = result
            state = (
                f"{result.units} units ({result.policy})"
                if result.prepared
                else f"FAILED: {result.prepare_error}"
            )
            log(f"  prepared {apk.stem}: {state}")

    usable = [item for item in results.values() if item.prepared]
    log(f"{len(usable)}/{len(apks)} apps instrumented")

    def snapshot(include_runs: bool) -> dict:
        return {
            "generated_at": time.time(),
            "max_seconds": arguments.max_seconds,
            "max_actions": arguments.max_actions,
            "arms": list(arguments.arms),
            "semantic_state_enabled": arguments.semantic_state,
            "uiautomator2_fallback_enabled": arguments.uiautomator2_fallback,
            "models": models,
            "serials": list(arguments.serials),
            "webview_url_patterns": {
                app_name: list(patterns)
                for app_name, patterns in sorted(webview_patterns.items())
            },
            # Which apps were authorized for which opt-in mechanism, so a result
            # can be read without the enhancement file beside it.
            "app_enhancements": {
                app_name: {
                    "foreign_workflow_ids": [
                        str(item.get("workflow_id"))
                        for item in entry.foreign_workflows
                    ],
                    "capture_screenshots": entry.capture_screenshots,
                    "visual_escape_enabled": entry.visual_escape_enabled,
                    "max_visual_escapes": entry.max_visual_escapes,
                }
                for app_name, entry in sorted(enhancements.items())
            },
            "apps": {
                name: {
                    "package": item.package,
                    "units": item.units,
                    "inclusion_policy": item.policy,
                    "prepared": item.prepared,
                    "prepare_error": item.prepare_error,
                    **({"runs": item.runs} if include_runs else {}),
                }
                for name, item in sorted(results.items())
            },
        }

    # One device lane per thread, and every lane snapshots the report after each
    # run, so the write has to be serialised. Sharing a single `.partial` path
    # across threads meant one lane could rename the file away while another was
    # about to rename the same path, and the loser died with FileNotFoundError.
    # That killed a campaign driver with five apps still to run.
    write_lock = threading.Lock()

    def write(include_runs: bool = True) -> None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        with write_lock:
            temporary = arguments.output.with_suffix(
                f".partial-{threading.get_ident():x}"
            )
            temporary.write_text(
                json.dumps(snapshot(include_runs), indent=2, sort_keys=True)
            )
            os.replace(temporary, arguments.output)

    if arguments.prepare_only:
        write(include_runs=False)
        log(f"prepare-only: wrote {arguments.output}")
        return 0

    # One job per (app, arm), pinned to a device by index so no two concurrent
    # runs contend for one serial, which the device lock forbids.
    jobs = [(app, arm) for app in usable for arm in arguments.arms]
    serials = list(arguments.serials)
    lanes: list[list[tuple]] = [[] for _ in serials]
    for index, job in enumerate(jobs):
        lanes[index % len(serials)].append(job)
    log(f"{len(jobs)} runs over {len(serials)} devices, {arguments.max_seconds:.0f}s each")

    def drive(lane_index: int) -> None:
        serial = serials[lane_index]
        for app, arm in lanes[lane_index]:
            def attempt(target_serial: str) -> dict:
                try:
                    return execute_run(
                        app=app,
                        arm=arm,
                        serial=target_serial,
                        work=arguments.work,
                        apk=arguments.dataset / f"{app.name}.apk",
                        max_seconds=arguments.max_seconds,
                        max_actions=arguments.max_actions,
                        models=models,
                        max_model_calls=arguments.max_model_calls,
                        project=arguments.vertex_project,
                        region=arguments.vertex_region,
                        allow_forced=arguments.allow_forced_routes,
                        launch_on_exhaustion=arguments.launch_on_exhaustion,
                        component_extras=app.component_extras,
                        setup_profile_dir=arguments.setup_profile_dir,
                        suppress_soft_keyboard=not arguments.keep_soft_keyboard,
                        webview_url_patterns=webview_patterns.get(app.name, ()),
                        semantic_state_enabled=arguments.semantic_state,
                        uiautomator2_fallback_enabled=arguments.uiautomator2_fallback,
                        enhancements=enhancements.get(app.name),
                    )
                except Exception as error:  # noqa: BLE001 - one run must not end the lane
                    return {
                        "arm": arm,
                        "serial": target_serial,
                        "error": f"{type(error).__name__}: {error}",
                    }

            log(f"  [{serial}] start {app.name} / {arm}")
            record = attempt(serial)
            # A device that drops off the bus loses the measurement for a reason
            # that has nothing to do with the app. One such abort was observed
            # costing a whole run, and the retry then produced 36.59%. The retry
            # is bounded to one and only for faults that are demonstrably the
            # fleet's, so a genuinely broken app still fails once and moves on.
            kind = fault_kind(record)
            if kind == "ambiguous" and device_healthy(serial):
                # The device is fine, so the app is what failed. Reported as-is
                # rather than retried, which keeps a genuinely broken app from
                # consuming a second run to reach the same answer.
                log(f"  [{serial}] {app.name} / {arm} failed on a healthy device; "
                    f"treated as an app fault, not retried")
                kind = None
            if kind is not None:
                fault = record.get("stop_reason") or record.get("error")
                if wait_for_device(serial, timeout=DEVICE_RECOVERY_SECONDS):
                    log(f"  [{serial}] {app.name} / {arm} infrastructure fault "
                        f"({fault}); device recovered, retrying once")
                    retry = attempt(serial)
                else:
                    log(f"  [{serial}] {app.name} / {arm} infrastructure fault "
                        f"({fault}); device did not come back, not retried")
                    retry = None
                if retry is not None:
                    retry["retried_after_infrastructure_fault"] = fault
                    # The superseded attempt is carried forward whole, so the
                    # reported cell says what it cost to obtain rather than
                    # keeping only the fault string of a run that was erased.
                    retry["preceding_attempts"] = [
                        *record.get("preceding_attempts", []),
                        {
                            key: value
                            for key, value in record.items()
                            if key != "preceding_attempts"
                        },
                    ]
                    record = retry
            app.runs[arm] = record
            # After the summary has been captured, so compacting cannot affect
            # what this run reports. Every retained attempt is compacted, not
            # only the last, because every one of them is still evidence.
            reclaimed = 0
            for attempt_record in (
                *record.get("preceding_attempts", []),
                record,
            ):
                run_directory = attempt_record.get("run")
                if run_directory:
                    reclaimed += compact_run_evidence(Path(run_directory))
            if reclaimed > 512 * 1024 * 1024:
                log(f"  [{serial}] compacted evidence, reclaimed "
                    f"{reclaimed / 1024**3:.1f} GiB")
            percent = record.get("observed_coverage_percent")
            detail = (
                f"{record.get('observed_covered_units')}/{record.get('total_units')}"
                f" = {percent:.2f}%"
                if isinstance(percent, (int, float))
                else f"no coverage ({record.get('status')}/"
                f"{record.get('stop_reason') or record.get('error')})"
            )
            # Forced activation reaches components no user or peer app can, so
            # the GUI/deep-link figure is reported beside the total rather than
            # letting one number stand for both.
            reachable = record.get("primary_gui_deep_link_coverage_percent")
            if isinstance(percent, (int, float)) and isinstance(reachable, (int, float)):
                detail += f" (gui+deeplink {reachable:.2f}%)"
            calls = record.get("model_calls")
            log(f"  [{serial}] {app.name} / {arm} -> {detail}" + (f", {calls} model calls" if calls else ""))
            # Written after every run so an interrupted campaign still reports.
            write()

    with concurrent.futures.ThreadPoolExecutor(len(serials)) as pool:
        list(pool.map(drive, range(len(serials))))

    write()
    log(f"wrote {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
