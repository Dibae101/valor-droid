#!/usr/bin/env python3
"""Generate a checksum-pinned schema-3 config for the smali fallback."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise SystemExit(f"{label} is not a regular resolved file: {resolved}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True, type=Path)
    parser.add_argument("--valordroid-repository", required=True, type=Path)
    parser.add_argument("--apktool-jar", required=True, type=Path)
    parser.add_argument("--build-tools", required=True, type=Path)
    parser.add_argument("--keystore", required=True, type=Path)
    parser.add_argument("--java", default="/usr/bin/java", type=Path)
    parser.add_argument("--aapt", default="/usr/bin/aapt", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", default=1800.0, type=float)
    parser.add_argument("--backend-version", default="1.0")
    parser.add_argument(
        "--inclusion-policy",
        choices=("app-package-methods-v1", "app-classes-methods-v1"),
        default="app-classes-methods-v1",
    )
    parser.add_argument("--log-tag", default="VALORDROID")
    parser.add_argument("--method-prefix", default="METHOD=")
    parser.add_argument("--keystore-password", default="android")
    parser.add_argument("--keystore-alias", default="android")
    arguments = parser.parse_args()
    if arguments.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")

    adapter = require(arguments.adapter, "adapter")
    apktool = require(arguments.apktool_jar, "Apktool jar")
    java = require(arguments.java, "Java executable")
    aapt = require(arguments.aapt, "aapt")
    zipalign = require(arguments.build_tools / "zipalign", "zipalign")
    apksigner = require(arguments.build_tools / "apksigner", "apksigner")
    keystore = require(arguments.keystore, "keystore")

    revision = subprocess.run(
        ["git", "-C", str(arguments.valordroid_repository), "rev-parse", "HEAD"],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise SystemExit(f"unexpected VALOR-Droid revision: {revision!r}")

    paths = {
        "aapt": aapt,
        "apktool": apktool,
        "apksigner": apksigner,
        "java": java,
        "keystore": keystore,
        "zipalign": zipalign,
    }
    toolchain = [
        {"name": name, "path": str(path), "sha256": sha256_file(path)}
        for name, path in sorted(paths.items())
    ]
    config = {
        "schema_version": 3,
        "protocol": "valordroid-instrumenter-v1",
        "producer": "valordroid-smali-fallback",
        "producer_version": "1.0",
        "source_revision": revision,
        "command": [
            str(adapter),
            "--java",
            "{tool:java}",
            "--apktool-jar",
            "{tool:apktool}",
            "--zipalign",
            "{tool:zipalign}",
            "--apksigner",
            "{tool:apksigner}",
            "--keystore",
            "{tool:keystore}",
            "--aapt",
            "{tool:aapt}",
        ],
        "executable_sha256": sha256_file(adapter),
        "toolchain": toolchain,
        "timeout_seconds": arguments.timeout_seconds,
        "backend": "smali-logcat-method-v1",
        "backend_version": arguments.backend_version,
        "inclusion_policy": arguments.inclusion_policy,
        "log_tag": arguments.log_tag,
        "method_prefix": arguments.method_prefix,
        "environment": {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "VD_KEYSTORE_ALIAS": arguments.keystore_alias,
            "VD_KEYSTORE_PASSWORD": arguments.keystore_password,
            "VD_PRODUCER": "valordroid-smali-fallback",
            "VD_PRODUCER_VERSION": "1.0",
            "VD_SOURCE_REVISION": revision,
            "VD_STEP_TIMEOUT_SECONDS": str(int(arguments.timeout_seconds)),
        },
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {arguments.output}")
    print(f"VALOR-Droid revision {revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
