#!/usr/bin/env python3
"""Generate a checksum-pinned instrumenter configuration for the AndroLog adapter.

`prepare-apk` refuses to run unless every tool it will invoke is pinned by
digest. This writes that configuration from the files actually present on this
host, so the pinning describes reality instead of being copied from an example.
"""

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
    """Resolve to the real file, because a pinned digest must name real bytes.

    Distribution SDK layouts reach `zipalign`/`apksigner` through symlinks, and
    `InstrumenterConfig` refuses a symlinked tool path so that a pin cannot be
    redirected after it was recorded. Resolving here records the actual target.
    """

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"{label} is not a regular file: {resolved}")
    if resolved.is_symlink():
        raise SystemExit(f"{label} still resolves to a symlink: {resolved}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True, type=Path)
    parser.add_argument("--androlog-jar", required=True, type=Path)
    parser.add_argument("--androlog-repository", required=True, type=Path)
    parser.add_argument("--build-tools", required=True, type=Path)
    parser.add_argument("--keystore", required=True, type=Path)
    parser.add_argument("--platforms", required=True, type=Path)
    parser.add_argument("--aapt", default="/usr/bin/aapt", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--log-tag", default="VALORDROID")
    parser.add_argument("--method-prefix", default="METHOD=")
    parser.add_argument("--inclusion-policy", default="app-package-methods-v1")
    parser.add_argument("--backend-version", default="0.1")
    parser.add_argument("--keystore-password", default="android")
    parser.add_argument("--keystore-alias", default="android")
    parser.add_argument("--heap", default="6g")
    parser.add_argument("--soot-threads", type=int, default=1)
    arguments = parser.parse_args()
    if arguments.soot_threads < 1:
        raise SystemExit("--soot-threads must be positive")

    adapter = require(arguments.adapter, "adapter")
    jar = require(arguments.androlog_jar, "AndroLog jar")
    zipalign = require(arguments.build_tools / "zipalign", "zipalign")
    apksigner = require(arguments.build_tools / "apksigner", "apksigner")
    keystore = require(arguments.keystore, "keystore")
    aapt = require(arguments.aapt, "aapt")
    platforms = arguments.platforms.expanduser()
    if not platforms.is_dir():
        raise SystemExit(f"platforms is not a directory: {platforms}")

    revision = subprocess.run(
        ["git", "-C", str(arguments.androlog_repository), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    if len(revision) != 40:
        raise SystemExit(f"unexpected AndroLog revision: {revision!r}")

    toolchain = [
        {"name": "aapt", "path": str(aapt), "sha256": sha256_file(aapt)},
        {"name": "androlog", "path": str(jar), "sha256": sha256_file(jar)},
        {"name": "apksigner", "path": str(apksigner), "sha256": sha256_file(apksigner)},
        {"name": "keystore", "path": str(keystore), "sha256": sha256_file(keystore)},
        {"name": "zipalign", "path": str(zipalign), "sha256": sha256_file(zipalign)},
    ]
    toolchain.sort(key=lambda item: item["name"])

    config = {
        "schema_version": 3,
        "protocol": "valordroid-instrumenter-v1",
        "producer": "androlog",
        "producer_version": "0.1",
        "source_revision": revision,
        "command": [
            str(adapter),
            "--androlog-jar",
            "{tool:androlog}",
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
        "backend": "androlog-logcat-method-v1",
        "backend_version": arguments.backend_version,
        "inclusion_policy": arguments.inclusion_policy,
        "log_tag": arguments.log_tag,
        "method_prefix": arguments.method_prefix,
        # The adapter receives no ambient environment, so everything it needs is
        # declared here and therefore covered by the configuration checksum.
        "environment": {
            "ANDROID_PLATFORMS": str(platforms),
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "VD_ANDROLOG_HEAP": arguments.heap,
            "VD_KEYSTORE_ALIAS": arguments.keystore_alias,
            "VD_KEYSTORE_PASSWORD": arguments.keystore_password,
            "VD_PRODUCER": "androlog",
            "VD_PRODUCER_VERSION": "0.1",
            "VD_SOOT_THREADS": str(arguments.soot_threads),
            "VD_SOURCE_REVISION": revision,
            "VD_STEP_TIMEOUT_SECONDS": str(int(arguments.timeout_seconds)),
        },
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {arguments.output}")
    print(f"AndroLog revision {revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
