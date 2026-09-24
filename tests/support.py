"""Shared builders for the regression suite: fake tools, bundles, and campaigns."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from valordroid.config import CoreConfig
from valordroid.models import stable_hash
from valordroid.prepared import (
    PROOF_SCHEMA_VERSION,
    SUPPORTED_BACKEND,
    StaticUnitProof,
    import_prepared_bundle,
)
from valordroid.runtime_config import RuntimeConfig

PACKAGE = "com.example.app"
UNITS = ("com.example.app.A.a()V", "com.example.app.A.b()V")

_FAKE_AAPT = """#!/usr/bin/env python3
print("package: name='com.example.app' versionCode='7' versionName='1.2.3'")
print("launchable-activity: name='com.example.app.MainActivity'  label='' icon=''")
"""

# Reports two ready devices with matching identity properties, and fails every
# other shell command so a child run-android process terminates deterministically.
_FAKE_ADB = """#!/usr/bin/env python3
import sys
PROPS = {
    "sys.boot_completed": "1",
    "ro.build.fingerprint": "redroid/fake/14:user/release",
    "ro.build.version.release": "14",
    "ro.build.version.sdk": "34",
    "ro.product.cpu.abi": "arm64-v8a",
    "ro.serialno": "fake-serial",
}
args = sys.argv[1:]
if args and args[0] == "-s":
    args = args[2:]
if not args:
    raise SystemExit(1)
if args[0] == "connect":
    print("connected to " + args[1]); raise SystemExit(0)
if args[0] == "disconnect":
    raise SystemExit(0)
if args[0] == "devices":
    print("List of devices attached")
    print("127.0.0.1:5555\\tdevice product:redroid model:fake device:fake")
    print("127.0.0.1:5557\\tdevice product:redroid model:fake device:fake")
    raise SystemExit(0)
if args[0] == "get-state":
    print("device"); raise SystemExit(0)
if args[0] == "shell" and args[1:2] == ["getprop"] and len(args) > 2:
    print(PROPS.get(args[2], "")); raise SystemExit(0)
sys.stderr.write("fake adb: unsupported command\\n")
raise SystemExit(1)
"""

# Never reports a booted device, forcing a failure before device_ready.
_FAKE_ADB_NEVER_READY = """#!/usr/bin/env python3
import sys
args = sys.argv[1:]
if args and args[0] == "-s":
    args = args[2:]
if args[:1] == ["connect"]:
    print("connected"); raise SystemExit(0)
if args[:1] == ["disconnect"]:
    raise SystemExit(0)
if args[:1] == ["devices"]:
    print("List of devices attached"); raise SystemExit(0)
sys.stderr.write("fake adb: no device\\n")
raise SystemExit(1)
"""

_FAKE_DOCKER = "#!/bin/sh\nexit 1\n"


def _write_executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def fake_tools(root: Path) -> dict[str, str]:
    """Create the fake aapt/adb/docker executables used across the suite."""

    root.mkdir(parents=True, exist_ok=True)
    return {
        "aapt": str(_write_executable(root / "fake-aapt", _FAKE_AAPT)),
        "adb": str(_write_executable(root / "fake-adb", _FAKE_ADB)),
        "adb_never_ready": str(
            _write_executable(root / "fake-adb-never-ready", _FAKE_ADB_NEVER_READY)
        ),
        "docker": str(_write_executable(root / "fake-docker", _FAKE_DOCKER)),
    }


def instrumenter_provenance(
    work: Path,
    *,
    original_sha256: str,
    instrumented_sha256: str,
    break_field: str | None = None,
) -> dict[str, Any]:
    """A faithful config/receipt/invocation triple, optionally tampered."""

    tool_path = str(work / "transformer.jar")
    (work / "transformer.jar").write_bytes(b"transformer")
    declared_environment = {"JAVA_HOME": "/opt/jdk", "PATH": "/opt/jdk/bin:/usr/bin"}
    environment = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC", **declared_environment}
    command = [
        "/usr/bin/java",
        "-jar",
        tool_path,
        "--protocol",
        "valordroid-instrumenter-v1",
    ]
    verified = {"executable": "a" * 64, "transformer": "b" * 64}
    config = {
        "protocol": "valordroid-instrumenter-v1",
        "backend_version": "1.0",
        "inclusion_policy": "all-app-methods",
        "log_tag": "AndroLog",
        "method_prefix": "METHOD=",
        "command": [
            "/usr/bin/java",
            "-jar",
            "{tool:transformer}",
            "--protocol",
            "valordroid-instrumenter-v1",
        ],
        "executable_sha256": "a" * 64,
        "environment": dict(declared_environment),
        "toolchain": [
            {"name": "transformer", "path": tool_path, "sha256": "b" * 64}
        ],
    }
    if break_field == "command":
        command = [
            "/usr/bin/java",
            "-jar",
            "/tmp/other.jar",
            "--protocol",
            "valordroid-instrumenter-v1",
        ]
    if break_field == "environment":
        environment = {**environment, "PATH": "/attacker/bin"}
    if break_field == "artifacts":
        verified = {"executable": "c" * 64, "transformer": "b" * 64}

    invocation = {
        "protocol": "valordroid-instrumenter-v1",
        "command": command,
        "environment": environment,
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "verified_artifacts": verified,
    }
    invocation["invocation_sha256"] = stable_hash(
        {
            "command": command,
            "environment": environment,
            "returncode": 0,
            "stdout_sha256": stable_hash(""),
            "stderr_sha256": stable_hash(""),
            "verified_artifacts": verified,
        }
    )
    receipt = {
        "protocol": "valordroid-instrumenter-v1",
        "backend": SUPPORTED_BACKEND,
        "backend_version": "1.0",
        "inclusion_policy": "all-app-methods",
        "log_tag": "AndroLog",
        "method_prefix": "METHOD=",
        "original_apk_sha256": original_sha256,
        "instrumented_apk_sha256": instrumented_sha256,
        "unit_count": len(UNITS),
        "unit_ids_sha256": stable_hash(sorted(UNITS)),
        "instrumentation_complete": True,
        "enumeration_complete": True,
        "one_probe_per_unit": True,
    }
    return {
        "instrumenter_config": config,
        "instrumenter_receipt": receipt,
        "instrumenter_invocation": invocation,
    }


def build_prepared(
    work: Path,
    aapt: str,
    *,
    name: str = "prepared",
    with_provenance: bool = False,
    break_field: str | None = None,
) -> Path:
    """Import a minimal but fully valid prepared bundle."""

    work.mkdir(parents=True, exist_ok=True)
    original = work / "original.apk"
    instrumented = work / "instrumented.apk"
    original.write_bytes(b"original-apk-bytes")
    instrumented.write_bytes(b"instrumented-apk-bytes")
    units_path = work / "units.json"
    units_path.write_text(json.dumps(list(UNITS)))
    original_sha256 = hashlib.sha256(original.read_bytes()).hexdigest()
    instrumented_sha256 = hashlib.sha256(instrumented.read_bytes()).hexdigest()
    proof = StaticUnitProof(
        schema_version=PROOF_SCHEMA_VERSION,
        producer="regression-suite",
        producer_version="1",
        original_apk_sha256=original_sha256,
        original_package_name=PACKAGE,
        original_version_code="7",
        original_version_name="1.2.3",
        instrumented_apk_sha256=instrumented_sha256,
        package_name=PACKAGE,
        version_code="7",
        version_name="1.2.3",
        backend=SUPPORTED_BACKEND,
        backend_version="1.0",
        inclusion_policy="all-app-methods",
        unit_count=len(UNITS),
        unit_ids_sha256=stable_hash(sorted(UNITS)),
    )
    proof_path = work / "static-unit-proof.json"
    proof_path.write_text(json.dumps(proof.to_dict()))
    provenance: dict[str, Any] = {}
    if with_provenance:
        provenance = instrumenter_provenance(
            work,
            original_sha256=original_sha256,
            instrumented_sha256=instrumented_sha256,
            break_field=break_field,
        )
    output = work / name
    import_prepared_bundle(
        original_apk=original,
        instrumented_apk=instrumented,
        units_path=units_path,
        proof_path=proof_path,
        output=output,
        backend_version="1.0",
        inclusion_policy="all-app-methods",
        log_tag="AndroLog",
        method_prefix="METHOD=",
        aapt=aapt,
        **provenance,
    )
    return output


def write_runtime_and_core(work: Path, serial: str = "127.0.0.1:5555") -> tuple[Path, Path]:
    runtime = RuntimeConfig.from_mapping(
        {
            "schema_version": RuntimeConfig.SCHEMA_VERSION,
            "serial": serial,
            "launcher": f"{PACKAGE}/.MainActivity",
            "max_actions": 2,
            "max_seconds": 20.0,
            "action_timeout_seconds": 5.0,
            "adb_timeout_seconds": 5.0,
            "capture_screenshots": False,
            "install_timeout_seconds": 10.0,
            "launch_timeout_seconds": 5.0,
            "observation_timeout_seconds": 5.0,
            "route_foreground_timeout_seconds": 2.0,
            "per_field_input_enabled": True,
            "pid_poll_seconds": 0.2,
            "post_action_delay_seconds": 0.1,
            "reset_seed_enabled": False,
            "suppress_soft_keyboard": False,
            "route_discovery_sha256": None,
            "component_extras": {},
            "text_input_value": "valordroid",
            "uninstall_after_run": True,
            "exported_components": [],
            "forced_components": [],
            "deep_links": [],
        }
    )
    runtime_path = work / "runtime.json"
    runtime_path.write_text(json.dumps(runtime.to_dict()))
    core_path = work / "core.json"
    core_path.write_text(json.dumps(CoreConfig().to_dict()))
    return runtime_path, core_path


def attach_fleet_config(
    work: Path,
    prepared: Path,
    tools: dict[str, str],
    *,
    lease_root: Path,
    adb: str | None = None,
    jobs: int = 2,
    campaign_id: str = "regression",
) -> Path:
    """A schema-2 attach-mode fleet configuration over loopback serials."""

    runtime_path, core_path = write_runtime_and_core(work)
    ports = [5555, 5557, 5559, 5561]

    def device(index: int) -> dict[str, Any]:
        return {
            "device_id": f"slot-{index}",
            "serial": f"127.0.0.1:{ports[index]}",
            "container_name": None,
            "data_root": None,
            "cpus": None,
            "memory": None,
            "androidboot_args": [],
            "expected_properties": {"ro.build.version.sdk": "34"},
        }

    config = {
        "schema_version": 2,
        "campaign_id": campaign_id,
        "mode": "attach",
        "max_parallel": jobs,
        "adb_executable": adb or tools["adb"],
        "docker_executable": tools["docker"],
        "aapt_executable": tools["aapt"],
        "image": None,
        "owner_label": "regression",
        "lease_root": str(lease_root),
        "readiness_timeout_seconds": 3.0,
        "poll_interval_seconds": 0.05,
        "command_timeout_seconds": 10.0,
        "attempt_timeout_seconds": 90.0,
        "interrupt_grace_seconds": 1.0,
        "terminate_grace_seconds": 1.0,
        "kill_grace_seconds": 1.0,
        "remove_owned_containers": False,
        "reclaim_abandoned_containers": False,
        "devices": [device(index) for index in range(jobs)],
        "jobs": [
            {
                "job_id": f"job-{index}",
                "run_id": f"run-{index}",
                "prepared": str(prepared),
                "runtime_config": str(runtime_path),
                "core_config": str(core_path),
                "max_attempts": 1,
            }
            for index in range(jobs)
        ],
    }
    config_path = work / "fleet-config.json"
    config_path.write_text(json.dumps(config))
    return config_path


def event(
    event_type: str,
    observed_at: float,
    *,
    job_id: str | None = None,
    device_id: str | None = None,
    attempt: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_type": event_type,
        "observed_at": observed_at,
        "job_id": job_id,
        "device_id": device_id,
        "attempt": attempt,
        "details": dict(details or {}),
    }


def attach_identity(serial: str = "127.0.0.1:5555") -> dict[str, Any]:
    return {
        "serial": serial,
        "properties": {
            "ro.build.fingerprint": "redroid/fake/14:user/release",
            "ro.build.version.release": "14",
            "ro.build.version.sdk": "34",
            "ro.product.cpu.abi": "arm64-v8a",
            "ro.serialno": "fake-serial",
        },
        "image": None,
        "container": None,
        "data_directory": None,
    }


def read_events(campaign: Path) -> list[dict[str, Any]]:
    entries = []
    for line in (campaign / "campaign-events.jsonl").read_text().splitlines():
        entries.append(json.loads(line))
    return entries


def write_events(campaign: Path, entries: list[dict[str, Any]]) -> None:
    path = campaign / "campaign-events.jsonl"
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n")


def free_port_serials(count: int) -> list[str]:
    """Loopback serials on ports nothing is listening on, for lease tests."""

    import socket

    serials = []
    sockets = []
    try:
        for _ in range(count):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            sockets.append(probe)
            serials.append(f"127.0.0.1:{probe.getsockname()[1]}")
    finally:
        for probe in sockets:
            probe.close()
    return serials


def suppress_environment_noise() -> None:
    """Keep test child processes from inheriting an ADB server address."""

    os.environ.pop("ANDROID_SERIAL", None)
    os.environ.pop("ADB_SERVER_SOCKET", None)
