#!/usr/bin/env python3
"""AndroLog adapter for the `valordroid-instrumenter-v1` protocol.

VALOR-Droid never instruments an APK itself. It shells out to one pinned
external transformer over a fixed contract and then re-verifies everything the
transformer claims. This adapter is that transformer for AndroLog:

    original APK  ->  AndroLog (Soot)  ->  zipalign  ->  apksigner
                  ->  exact unit list extracted from the produced dex
                  ->  receipt binding both APK digests to the unit list

Two properties matter for the frozen coverage universe (gap 6):

1. The unit list is read back out of the *produced* dex string table, not from
   AndroLog's own bookkeeping, so the enumeration describes exactly the probes
   that shipped in the APK that will be installed.
2. Nothing is inferred. If a step fails, the adapter exits non-zero and writes
   no artifacts, so `prepare-apk` fails closed rather than producing a bundle
   whose denominator cannot be trusted.

Standard library only, so it can be pinned by checksum without a build step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROTOCOL = "valordroid-instrumenter-v1"
RECEIPT_SCHEMA_VERSION = 1

def well_formed_signature(unit: str) -> bool:
    """Structural check for a Soot method signature.

    Deliberately not a name pattern. R8/D8 emit synthetic members whose names
    legitimately contain `-`, `$`, and surrounding single quotes
    (`'-$$Nest$mfoo'`, `$r8$lambda$qhR-Qm...`), and constructors are named
    `<init>`/`<clinit>`. Over-fitting a name regex here would silently drop real
    methods from the coverage denominator, so only the frame is checked:

        <declaring.Class: ReturnType name(ArgType,...)>
    """

    if not unit.startswith("<") or not unit.endswith(">"):
        return False
    body = unit[1:-1]
    class_part, separator, member = body.partition(": ")
    if not separator or not class_part or not member:
        return False
    if "(" not in member or not member.endswith(")"):
        return False
    head, _, arguments = member.partition("(")
    if arguments.count("(") or arguments.count(")") != 1:
        return False
    return bool(head.strip()) and " " in head.strip()
_PACKAGE_LINE = re.compile(r"^package:\s+(?P<attributes>.+)$", re.MULTILINE)
_ATTRIBUTE = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9]*)='(?P<value>[^']*)'")


def fail(message: str) -> "None":
    sys.stderr.write(f"androlog-instrumenter: {message}\n")
    raise SystemExit(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: object) -> str:
    """Match `valordroid.models.stable_hash` exactly."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run(command: list[str], *, timeout: float, label: str) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        fail(f"{label}: executable not found: {command[0]}")
    except subprocess.TimeoutExpired:
        fail(f"{label}: timed out after {timeout}s")
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        fail(f"{label}: exit {completed.returncode}: {detail[:2000]}")
    return completed.stdout


def try_run(command: list[str], *, timeout: float) -> tuple[bool, str]:
    """Run a command without aborting the process, reporting failure detail."""

    try:
        completed = subprocess.run(
            command,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, f"executable not found: {command[0]}"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        return False, f"exit {completed.returncode}: {detail[:2000]}"
    return True, completed.stdout


# Caps so a genuinely broken input cannot be stubbed into meaninglessness.
MAX_STUBBED_METHODS = 24
MAX_STUBBED_PREFIXES = 3
# Failures in one library package before the whole package is stubbed instead of
# one method per pass. My-Expenses produced seven distinct kotlinx.html failures,
# each costing a full instrumentation pass to find, so one-at-a-time never
# converges on a generated DSL.
PACKAGE_ESCALATION_THRESHOLD = 3
_UNSERIALISABLE_METHOD = re.compile(r"Error while processing method (<[^>]+>)")


def unserialisable_method(detail: str) -> str | None:
    """The Soot method signature named by a dex-writer failure, if any."""

    match = _UNSERIALISABLE_METHOD.search(detail)
    return match.group(1) if match is not None else None


def declaring_package(signature: str) -> str:
    """The package of the class declaring a Soot method signature."""

    declaring = signature.lstrip("<").split(":", 1)[0].strip()
    return declaring.rsplit(".", 1)[0] if "." in declaring else declaring


def owned_by_app(signature: str, app_root: str) -> bool:
    """Whether a Soot method signature belongs to the app under test.

    Stubbing app code would silently shrink what the run can cover, so those
    signatures are refused and the app is reported as failed instead.
    """

    declaring = signature.lstrip("<").split(":", 1)[0].strip()
    root = app_root.strip()
    if not root:
        return True
    return declaring == root or declaring.startswith(root + ".")


def soot_strategies() -> list[tuple[int, bool]]:
    """`(target API, older type assigner)` pairs to try when writing the dex.

    Soot's dex writer fails in two unrelated ways on this dataset and no single
    setting clears both:

      * below API 21 it refuses to split multidex, so any app whose instrumented
        form exceeds 65536 methods dies with "Dex file overflow" -- that is every
        large app here, WordPress and SkyTube included;
      * its default (fast) type assigner can leave a local holding an internal
        integer-range type, so the writer asks PrimitiveType.getByName("[0..1]")
        and throws "not found: [0..1]". That hit androidx.preference in
        LibreTorrent and kotlinx.html in My-Expenses. The older type assigner
        narrows those locals to concrete types.

    Ordering keeps an app in the dataset instead of dropping it. Each app is
    instrumented once, so its universe is fixed for every run that measures it,
    and the winning strategy is written to VD_TARGET_API_LOG for the record.
    """

    configured = os.environ.get("VD_SOOT_STRATEGIES", "").strip()
    if configured:
        strategies = []
        for item in configured.replace(",", " ").split():
            level, _, flag = item.partition(":")
            strategies.append((int(level), flag.strip().lower() == "older"))
    else:
        # 34 first: it matches the API the devices run, so the emitted dex can
        # express every platform method the app overrides. Lower levels emit an
        # older dex format whose verifier rejects newer signatures -- Jellyfin
        # fails to link Service.startForeground(int, Notification, int) at both
        # 26 and 23. The lower rungs remain because Soot's writer has
        # level-dependent bugs of its own: LibreTorrent only serialises at 22.
        strategies = [
            (34, False),
            (34, True),
            (30, False),
            (26, False),
            (22, False),
            (22, True),
        ]
    seen: set[tuple[int, bool]] = set()
    ordered: list[tuple[int, bool]] = []
    for strategy in strategies:
        if strategy not in seen:
            seen.add(strategy)
            ordered.append(strategy)
    return ordered


def apk_package(aapt: str, apk: Path, *, timeout: float) -> str:
    output = run([aapt, "dump", "badging", str(apk)], timeout=timeout, label="aapt badging")
    match = _PACKAGE_LINE.search(output)
    if match is None:
        fail(f"aapt reported no package for {apk}")
    attributes = {
        item.group("name"): item.group("value")
        for item in _ATTRIBUTE.finditer(match.group("attributes"))
    }
    name = attributes.get("name", "").strip()
    if not name:
        fail(f"aapt reported an empty package name for {apk}")
    return name


def _uleb128(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("truncated ULEB128")
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, offset
        shift += 7
        if shift > 35:
            raise ValueError("ULEB128 is too long")


def dex_strings(data: bytes) -> list[str]:
    """Read a dex string table exactly, rather than scanning for printable runs.

    `strings(1)` truncates and merges entries, which would silently corrupt the
    coverage denominator.
    """

    if len(data) < 112 or data[:4] != b"dex\n":
        raise ValueError("not a dex file")
    (string_ids_size,) = struct.unpack_from("<I", data, 56)
    (string_ids_off,) = struct.unpack_from("<I", data, 60)
    result: list[str] = []
    for index in range(string_ids_size):
        (data_off,) = struct.unpack_from("<I", data, string_ids_off + 4 * index)
        _length, cursor = _uleb128(data, data_off)
        end = data.index(b"\x00", cursor)
        # Method signatures are ASCII; `replace` keeps a stray non-MUTF-8 byte
        # from aborting the whole enumeration.
        result.append(data[cursor:end].decode("utf-8", errors="replace"))
    return result


# Roots belonging to the platform or to bundled libraries. Instrumenting them
# inflates the denominator by an order of magnitude (one app yielded 138,631
# units) without describing the code under test.
_LIBRARY_ROOTS = (
    "android", "androidx", "kotlin", "kotlinx", "java", "javax", "sun", "dalvik",
    "org/apache", "org/json", "org/jetbrains", "org/intellij", "org/w3c", "org/xml",
    "org/slf4j", "org/reactivestreams", "org/checkerframework", "org/objectweb",
    "org/bouncycastle", "org/greenrobot", "org/koin", "com/google", "com/android",
    "com/squareup", "com/bumptech", "com/fasterxml", "com/airbnb", "com/jakewharton",
    "com/dropbox", "com/facebook", "okhttp3", "okio", "retrofit2", "dagger", "hilt",
    "io/reactivex", "io/grpc", "io/netty", "j$", "_COROUTINE", "coil", "coil3",
)


def dex_defined_classes(data: bytes) -> list[str]:
    """Class descriptors *defined* in this dex, not merely referenced."""

    if len(data) < 112 or data[:4] != b"dex\n":
        raise ValueError("not a dex file")
    (string_ids_size,) = struct.unpack_from("<I", data, 56)
    (string_ids_off,) = struct.unpack_from("<I", data, 60)
    (type_ids_size,) = struct.unpack_from("<I", data, 64)
    (type_ids_off,) = struct.unpack_from("<I", data, 68)
    (class_defs_size,) = struct.unpack_from("<I", data, 96)
    (class_defs_off,) = struct.unpack_from("<I", data, 100)

    def string_at(index: int) -> str:
        if index >= string_ids_size:
            raise ValueError("string index out of range")
        (data_off,) = struct.unpack_from("<I", data, string_ids_off + 4 * index)
        _length, cursor = _uleb128(data, data_off)
        end = data.index(b"\x00", cursor)
        return data[cursor:end].decode("utf-8", errors="replace")

    result: list[str] = []
    for index in range(class_defs_size):
        # A class_def_item begins with class_idx (u4) into type_ids; the item is
        # 32 bytes wide.
        (class_idx,) = struct.unpack_from("<I", data, class_defs_off + 32 * index)
        if class_idx >= type_ids_size:
            continue
        (descriptor_idx,) = struct.unpack_from("<I", data, type_ids_off + 4 * class_idx)
        descriptor = string_at(descriptor_idx)
        if descriptor.startswith("L") and descriptor.endswith(";"):
            result.append(descriptor[1:-1])
    return result


def application_class_roots(apk: Path) -> list[tuple[str, int]]:
    """Non-library root packages defined in the dex, most classes first."""

    counts: dict[str, int] = {}
    with zipfile.ZipFile(apk) as archive:
        names = [
            name for name in archive.namelist()
            if name.startswith("classes") and name.endswith(".dex")
        ]
        for name in sorted(names):
            try:
                classes = dex_defined_classes(archive.read(name))
            except ValueError:
                continue
            for descriptor in classes:
                if descriptor.startswith(_LIBRARY_ROOTS):
                    continue
                segments = descriptor.split("/")
                for depth in (2, 3):
                    if len(segments) > depth:
                        prefix = "/".join(segments[:depth])
                        counts[prefix] = counts.get(prefix, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def classes_under(apk: Path, prefix: str) -> int:
    """Count classes defined under `prefix`, ignoring the library filter.

    The filter has to be bypassed here because an app is allowed to live inside
    a namespace the filter calls a library: Muzei's own code is
    `com.google.android.apps.muzei`, so filtering `com/google` discarded the
    entire app and left a universe of 221 generated data-binding methods.
    """

    if not prefix:
        return 0
    needle = prefix if prefix.endswith("/") else prefix + "/"
    total = 0
    with zipfile.ZipFile(apk) as archive:
        for name in sorted(
            item for item in archive.namelist()
            if item.startswith("classes") and item.endswith(".dex")
        ):
            try:
                classes = dex_defined_classes(archive.read(name))
            except ValueError:
                continue
            total += sum(1 for item in classes if item.startswith(needle))
    return total


def _launchable_activities(aapt: str, apk: Path, *, timeout: float) -> list[str]:
    output = run([aapt, "dump", "badging", str(apk)], timeout=timeout, label="aapt badging")
    names: list[str] = []
    for line in output.splitlines():
        if line.startswith(("launchable-activity:", "leanback-launchable-activity:")):
            marker = "name='"
            start = line.find(marker)
            if start >= 0:
                names.append(line[start + len(marker):].split("'", 1)[0])
    return names


def select_class_root(apk: Path, package: str, aapt: str, *, timeout: float = 120.0):
    """Choose the root package to instrument: (root, rule, candidates).

    Ordered by how much each signal proves:

    1. The declared application id, when classes actually live under it.
    2. The launcher activity's own class name. That is the app's code by
       definition, and it survives `.debug`/`.dev` application-id suffixes --
       the reason four of fourteen apps produced zero probes.
    3. The application id with variant suffixes stripped and trailing segments
       progressively dropped.
    4. The dominant non-library root by class count. Last, because a bundled
       library can out-number the app: one app's largest root was `com.dropbox`
       with 5,250 classes.
    """

    roots = application_class_roots(apk)
    present = {prefix for prefix, _ in roots}
    declared = package.replace(".", "/")

    # Every namespace that could plausibly be the app's own, paired with the rule
    # that proposed it. Both signals are app-specific by construction: the
    # application id is the app's declared namespace, and the package holding a
    # launcher activity is the app's code by definition.
    candidates: list[tuple[str, str]] = []
    stripped = package
    for suffix in (".debug", ".dev", ".alpha", ".beta", ".nightly", ".free", ".fdroid", ".test"):
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)]
    if declared in present:
        candidates.append((declared, "declared-application-id"))
    segments = stripped.replace(".", "/").split("/")
    while len(segments) >= 2:
        candidate = "/".join(segments)
        if candidate in present and all(candidate != item for item, _ in candidates):
            candidates.append((candidate, "application-id-prefix"))
        segments.pop()
    for activity in _launchable_activities(aapt, apk, timeout=timeout):
        owner = activity.replace(".", "/").rsplit("/", 1)[0]
        if not owner:
            continue
        # The launcher's own package is always a candidate, even when it sits
        # inside a namespace the library filter would reject: Muzei's code really
        # is com.google.android.apps.muzei and Sunflower's is
        # com.google.samples.apps.sunflower.
        if all(owner != item for item, _ in candidates):
            candidates.append((owner, "launcher-activity-package"))
        # Ancestors matter when the launcher sits in a subpackage: Orgzly launches
        # from com.orgzly.android.ui but its code is com.orgzly, and Open-Food-Facts
        # launches from a 32-class splash package while its 2,029 classes live
        # further up. Library namespaces are not walked into, so ascending from
        # com.google.android.apps.muzei never reaches com.google.
        segments = owner.split("/")
        while len(segments) > 2:
            segments.pop()
            ancestor = "/".join(segments)
            if ancestor.startswith(_LIBRARY_ROOTS):
                continue
            if all(ancestor != item for item, _ in candidates):
                candidates.append((ancestor, "launcher-activity-ancestor"))

    # Pick the candidate holding the most classes, preferring the more specific
    # namespace on a tie. Size is the deciding signal because both failure modes
    # seen in the dataset are size errors: Wikipedia's launcher sits in
    # org.wikipedia.main, whose 150 classes stood in for org.wikipedia's 8,022,
    # and Muzei's application id net.nurik.roman.muzei holds only generated
    # data-binding while its 1,000-plus real classes live elsewhere.
    measured = [
        (candidate, rule, classes_under(apk, candidate))
        for candidate, rule in candidates
    ]
    measured = [item for item in measured if item[2] > 0]
    if measured:
        best = max(measured, key=lambda item: (item[2], len(item[0])))
        return best[0], best[1], roots
    if roots:
        return roots[0][0], "dominant-non-library-root", roots
    return declared, "declared-application-id-fallback", roots


def extract_units(apk: Path, method_prefix: str) -> tuple[str, ...]:
    units: set[str] = set()
    skipped = 0
    examples: list[str] = []
    with zipfile.ZipFile(apk) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.startswith("classes") and name.endswith(".dex")
        ]
        if not names:
            fail(f"instrumented APK contains no dex: {apk}")
        for name in sorted(names):
            try:
                strings = dex_strings(archive.read(name))
            except ValueError as error:
                fail(f"cannot read the dex string table of {name}: {error}")
            for value in strings:
                if not value.startswith(method_prefix):
                    continue
                unit = value[len(method_prefix):].strip()
                if not unit:
                    continue
                if not well_formed_signature(unit):
                    # An app can ship its own constant starting with the probe
                    # prefix (one app ships `METHOD=(NONE|AES-128|...)`). The
                    # signature shape identifies a probe, not the prefix alone.
                    skipped += 1
                    examples.append(unit[:160])
                    continue
                units.add(unit)
    if not units:
        fail(
            "no probe strings found in the instrumented APK; the transformer "
            f"produced nothing matching {method_prefix!r}"
        )
    if skipped:
        sys.stderr.write(
            f"androlog-instrumenter: skipped {skipped} prefix-matching strings that are "
            f"not method signatures; first examples: {examples[:2]}\n"
        )
    return tuple(sorted(units))


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--input-apk", required=True, type=Path)
    parser.add_argument("--output-apk", required=True, type=Path)
    parser.add_argument("--units-output", required=True, type=Path)
    parser.add_argument("--receipt-output", required=True, type=Path)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--backend-version", required=True)
    parser.add_argument("--inclusion-policy", required=True)
    parser.add_argument("--log-tag", required=True)
    parser.add_argument("--method-prefix", required=True)
    # Pinned toolchain, supplied by the instrumenter configuration.
    parser.add_argument("--androlog-jar", required=True, type=Path)
    parser.add_argument("--zipalign", required=True)
    parser.add_argument("--apksigner", required=True)
    parser.add_argument("--keystore", required=True, type=Path)
    parser.add_argument("--aapt", default="aapt")
    arguments = parser.parse_args()

    if arguments.protocol != PROTOCOL:
        fail(f"unsupported protocol: {arguments.protocol}")
    if arguments.backend != "androlog-logcat-method-v1":
        fail(f"unsupported backend: {arguments.backend}")

    platforms = os.environ.get("ANDROID_PLATFORMS", "").strip()
    if not platforms or not Path(platforms).is_dir():
        fail("ANDROID_PLATFORMS must name the android-platforms directory")
    producer = os.environ.get("VD_PRODUCER", "").strip()
    producer_version = os.environ.get("VD_PRODUCER_VERSION", "").strip()
    source_revision = os.environ.get("VD_SOURCE_REVISION", "").strip()
    if not producer or not producer_version or not source_revision:
        fail("VD_PRODUCER, VD_PRODUCER_VERSION and VD_SOURCE_REVISION must be set")
    keystore_password = os.environ.get("VD_KEYSTORE_PASSWORD", "android")
    keystore_alias = os.environ.get("VD_KEYSTORE_ALIAS", "androidkey")
    heap = os.environ.get("VD_ANDROLOG_HEAP", "6g")
    step_timeout = float(os.environ.get("VD_STEP_TIMEOUT_SECONDS", "1800"))
    threads = os.environ.get("VD_SOOT_THREADS", "1").strip()
    if not threads.isdigit() or int(threads) < 1:
        fail("VD_SOOT_THREADS must be a positive integer")

    original = arguments.input_apk
    if not original.is_file():
        fail(f"input APK does not exist: {original}")
    if arguments.output_apk.exists():
        fail(f"output APK already exists: {arguments.output_apk}")

    package = apk_package(arguments.aapt, original, timeout=120.0)
    # Three declared policies. The default trusts the application id; the
    # `app-classes` policy resolves the root package the app's classes actually
    # use; `all-methods` instruments everything including libraries. The policy
    # is pinned in the configuration and recorded in the receipt, so every
    # bundle states which one produced its universe.
    if arguments.inclusion_policy == "app-package-methods-v1":
        package_selection = ["-pkg", package]
    elif arguments.inclusion_policy == "app-classes-methods-v1":
        root, rule, roots = select_class_root(original, package, arguments.aapt)
        sys.stderr.write(
            f"androlog-instrumenter: class root {root.replace('/', '.')} via {rule} "
            f"(declared {package}); top candidates "
            f"{[(p.replace('/', '.'), n) for p, n in roots[:3]]}\n"
        )
        package_selection = ["-pkg", root.replace("/", ".")]
    elif arguments.inclusion_policy == "all-methods-v1":
        package_selection = []
    else:
        fail(
            f"unsupported inclusion policy: {arguments.inclusion_policy!r}; expected "
            "app-package-methods-v1, app-classes-methods-v1, or all-methods-v1"
        )
        package_selection = []
    workspace = Path(tempfile.mkdtemp(prefix="androlog-adapter."))
    try:
        # AndroLog treats `-o` as a directory and writes <input name> inside it.
        staged = workspace / "androlog-output"
        ladder = soot_strategies()
        chosen: tuple[int, bool] | None = None
        failures: list[str] = []
        # Signatures of library methods Soot cannot serialise back to dex. Each
        # one is discovered from the failure itself, never guessed, and app code
        # is refused outright so the measured universe cannot be affected.
        # Methods the caller has explicitly asked to neutralise so the app can run
        # at all. Unlike the stubs discovered below, these may belong to the app,
        # because their effect is a crash on the emulator rather than a Soot
        # failure: Amaze calls UsbManager.getDeviceList() on a device that has no
        # USB service and dies before its first screen, with or without our
        # instrumentation. Returning false from isUsbDeviceConnected is the answer
        # the platform would have given. Every entry is recorded in the sidecar so
        # a result built on one can be reported as such.
        unblocked = [
            item.strip()
            for item in os.environ.get("VD_UNBLOCK_METHODS", "").split(";")
            if item.strip()
        ]
        if unblocked:
            sys.stderr.write(
                f"androlog-instrumenter: neutralising {len(unblocked)} "
                f"startup-blocking method(s) by request\n"
            )
        stubs: list[str] = list(unblocked)
        stub_prefixes: list[str] = []
        package_failures: dict[str, int] = {}
        app_root = package_selection[1] if len(package_selection) > 1 else package
        for attempt, (level, older) in enumerate(ladder, start=1):
            label = f"api {level}{' older-type-assigner' if older else ''}"
            while True:
                if staged.exists():
                    shutil.rmtree(staged)
                staged.mkdir()
                succeeded, detail = try_run(
                    [
                        "java",
                        f"-Xmx{heap}",
                        f"-Dandrospecter.targetApi={level}",
                        f"-Dandrospecter.olderTypeAssigner={'true' if older else 'false'}",
                        f"-Dandrospecter.stubMethods={';'.join(stubs)}",
                        f"-Dandrospecter.stubPrefixes={';'.join(stub_prefixes)}",
                        "-jar",
                        str(arguments.androlog_jar),
                        "-p",
                        platforms,
                        "-a",
                        str(original),
                        "-o",
                        str(staged),
                        "-m",
                        "-l",
                        arguments.log_tag,
                        *package_selection,
                        # Soot's multi-threaded dex writer intermittently throws
                        # DexPrinterException on the same input, so instrumentation
                        # is single-threaded. That also makes the produced unit list
                        # reproducible, which the checksum-bound universe depends on.
                        "-t",
                        threads,
                    ],
                    timeout=step_timeout,
                )
                if succeeded and any(staged.rglob("*.apk")):
                    chosen = (level, older)
                    break
                reason = detail if not succeeded else "produced no APK"
                signature = unserialisable_method(reason)
                if (
                    signature is not None
                    and signature not in stubs
                    and len(stubs) < MAX_STUBBED_METHODS
                    and not owned_by_app(signature, app_root)
                ):
                    stubs.append(signature)
                    owner = declaring_package(signature)
                    package_failures[owner] = package_failures.get(owner, 0) + 1
                    # Once a library package has failed repeatedly, stub the whole
                    # package: a generated DSL has too many broken bodies to find
                    # one instrumentation pass at a time.
                    if (
                        package_failures[owner] >= PACKAGE_ESCALATION_THRESHOLD
                        and owner not in stub_prefixes
                        and len(stub_prefixes) < MAX_STUBBED_PREFIXES
                        and not owned_by_app(f"<{owner}.X: void x()>", app_root)
                    ):
                        stub_prefixes.append(owner)
                        sys.stderr.write(
                            f"androlog-instrumenter: {label} has failed "
                            f"{package_failures[owner]} times inside {owner}; "
                            f"stubbing that package and retrying\n"
                        )
                    else:
                        sys.stderr.write(
                            f"androlog-instrumenter: {label} cannot serialise "
                            f"{signature}; stubbing it and retrying\n"
                        )
                    continue
                failures.append(f"{label}: {reason}")
                sys.stderr.write(
                    f"androlog-instrumenter: {label} failed, trying next strategy\n"
                )
                break
            if chosen is not None:
                if attempt > 1 or stubs:
                    sys.stderr.write(
                        f"androlog-instrumenter: {label} succeeded after "
                        f"{attempt - 1} failed strategy(ies) and "
                        f"{len(stubs)} stubbed library method(s)\n"
                    )
                break
        if chosen is None:
            fail("androlog: every Soot strategy failed: " + " | ".join(failures)[:2000])
        target_api, used_older_assigner = chosen
        # The receipt has an exact field set, so provenance for the level that
        # actually worked goes to an opt-in sidecar the campaign can read.
        api_log = os.environ.get("VD_TARGET_API_LOG", "").strip()
        if api_log:
            try:
                with open(api_log, "a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "apk": original.name,
                                "target_api": target_api,
                                "older_type_assigner": used_older_assigner,
                                "stubbed_methods": stubs,
                                "stubbed_prefixes": stub_prefixes,
                                "unblocked_methods": unblocked,
                                "attempts": len(failures) + 1,
                                "failed_strategies": failures,
                            }
                        )
                        + "\n"
                    )
            except OSError:
                pass
        produced = sorted(staged.rglob("*.apk"))
        # AndroLog's own signing step writes extra `_aligned`/`_signed` copies
        # when its bundled toolchain is present; the transformed APK is the one
        # named after the input.
        exact = [item for item in produced if item.name == original.name]
        candidate = exact[0] if exact else (produced[0] if produced else None)
        if candidate is None:
            fail("AndroLog produced no APK")

        aligned = workspace / "aligned.apk"
        run(
            [arguments.zipalign, "-p", "-f", "4", str(candidate), str(aligned)],
            timeout=step_timeout,
            label="zipalign",
        )
        signed = workspace / "signed.apk"
        run(
            [
                arguments.apksigner,
                "sign",
                "--ks",
                str(arguments.keystore),
                "--ks-pass",
                f"pass:{keystore_password}",
                "--ks-key-alias",
                keystore_alias,
                "--key-pass",
                f"pass:{keystore_password}",
                "--out",
                str(signed),
                str(aligned),
            ],
            timeout=step_timeout,
            label="apksigner sign",
        )
        run(
            [arguments.apksigner, "verify", str(signed)],
            timeout=step_timeout,
            label="apksigner verify",
        )

        units = extract_units(signed, arguments.method_prefix)
        instrumented_package = apk_package(arguments.aapt, signed, timeout=120.0)
        if instrumented_package != package:
            fail(
                f"instrumented package {instrumented_package!r} differs from "
                f"original {package!r}"
            )

        arguments.output_apk.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(signed, arguments.output_apk)
        arguments.units_output.write_text(
            json.dumps(list(units), indent=2), encoding="utf-8"
        )
        receipt = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "producer": producer,
            "producer_version": producer_version,
            "source_revision": source_revision,
            "backend": arguments.backend,
            "backend_version": arguments.backend_version,
            "inclusion_policy": arguments.inclusion_policy,
            "log_tag": arguments.log_tag,
            "method_prefix": arguments.method_prefix,
            "original_apk_sha256": sha256_file(original),
            "instrumented_apk_sha256": sha256_file(arguments.output_apk),
            "unit_count": len(units),
            "unit_ids_sha256": canonical_hash(list(units)),
            "instrumentation_complete": True,
            "enumeration_complete": True,
            "one_probe_per_unit": True,
        }
        arguments.receipt_output.write_text(
            json.dumps(receipt, indent=2), encoding="utf-8"
        )
        sys.stderr.write(
            f"androlog-instrumenter: {package} instrumented, {len(units)} units\n"
        )
        return 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
