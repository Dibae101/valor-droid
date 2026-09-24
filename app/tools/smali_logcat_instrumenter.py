#!/usr/bin/env python3
"""Auditable all-DEX smali fallback for ``valordroid-instrumenter-v1``.

The transformer is deliberately narrow. It supports two explicit app-namespace
policies, rewrites every selected concrete method or fails the entire invocation,
adds one bounded/thread-safe non-recursive logging helper, then re-decodes the
final signed APK and proves exact class/method preservation and one physical
probe callsite per emitted unit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PROTOCOL = "valordroid-instrumenter-v1"
BACKEND = "smali-logcat-method-v1"
RECEIPT_SCHEMA_VERSION = 2
AUDIT_SCHEMA_VERSION = 1
APP_PACKAGE_POLICY = "app-package-methods-v1"
APP_CLASSES_POLICY = "app-classes-methods-v1"
SUPPORTED_POLICIES = frozenset((APP_PACKAGE_POLICY, APP_CLASSES_POLICY))
# Library and framework roots bundled into an app. They are decoded alongside the
# app's own classes, so a namespace tally has to exclude them or the largest
# "app" namespace becomes whichever dependency shipped the most code. Mirrors
# LIBRARY_ROOTS in reproduction/runner/code_coverage.py, in descriptor form.
# R8 desugaring polyfills. Named `$r8$...` at the top level, they stand in for
# JDK methods on older API levels and R8 duplicates them across dex files as
# needed. Not app code, and not uniquely located.
_R8_SYNTHETIC = re.compile(r"L\$r8\$")

_LIBRARY_DESCRIPTORS = (
    "Landroid/", "Landroidx/", "Lcom/google/", "Lcom/android/", "Lkotlin/",
    "Lkotlinx/", "Ljava/", "Ljavax/", "Lorg/jetbrains/", "Lorg/intellij/",
    "Lorg/apache/", "Lorg/json/", "Lorg/w3c/", "Lorg/xml/", "Lorg/xmlpull/",
    "Lorg/slf4j/", "Lorg/reactivestreams/", "Lokhttp3/", "Lokio/", "Lretrofit2/",
    "Lio/reactivex/", "Ldagger/", "Ljavassist/", "Lcom/squareup/",
    "Lcom/facebook/", "Lcom/bumptech/", "Lcom/airbnb/", "Lcom/jakewharton/",
    "Lcom/crashlytics/", "Lio/fabric/", "Lbutterknife/", "Lrx/", "Lhu/akarnokd/",
    "Lcom/afollestad/", "Lcom/mikepenz/", "Lcom/nineoldandroids/", "Lorg/koin/",
    # Added after the dominant-namespace fallback selected the bundled Dropbox
    # SDK as Orgzly-Revived's application namespace and then failed on a
    # 488-local library method. Any SDK large enough to outnumber the app's own
    # classes has to be listed, or "dominant" means "biggest dependency".
    "Lcom/dropbox/", "Lcom/fasterxml/", "Lcom/bugsnag/", "Lcom/evernote/",
    "Lorg/greenrobot/", "Lorg/joda/", "Lorg/bouncycastle/", "Lorg/eclipse/",
    "Lnet/sqlcipher/", "Lcom/j256/", "Lorg/kxml2/", "Lorg/simpleframework/",
)

_VARIANT_SUFFIXES = (
    ".debug",
    ".dev",
    ".alpha",
    ".beta",
    ".nightly",
    ".free",
    ".fdroid",
    ".test",
)

_PACKAGE_LINE = re.compile(r"^package:\s+(?P<attributes>.+)$", re.MULTILINE)
_ATTRIBUTE = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9]*)='(?P<value>[^']*)'")
_DEX_NAME = re.compile(r"classes(?:(?P<number>(?:[2-9]|[1-9][0-9]+)))?\.dex\Z")
_SMALI_DIR = re.compile(r"smali(?:_classes(?P<number>(?:[2-9]|[1-9][0-9]+)))?\Z")
_REGISTER_DIRECTIVE = re.compile(
    r"^(?P<prefix>\s*\.(?P<kind>locals|registers)\s+)"
    r"(?P<value>(?:0x[0-9a-fA-F]+|[0-9]+))"
    r"(?P<suffix>\s*(?:#.*)?(?:\r?\n)?)$"
)
_INVOKE_HELPER = re.compile(
    r"^invoke-static(?P<range>/range)?\s+\{(?P<registers>[^}]*)\},\s+"
    r"(?P<target>L[^;]+;->hit\(Ljava/lang/String;\)V)\s*(?:#.*)?$"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SUBANNOTATION_OPEN = re.compile(r"(?:^|=\s*)\.subannotation(?:\s|$)")


class InstrumentationError(RuntimeError):
    pass


def fail(message: str) -> "None":
    sys.stderr.write(f"smali-logcat-instrumenter: {message}\n")
    raise SystemExit(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run(command: list[str], *, timeout: float, label: str) -> str:
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise InstrumentationError(f"{label}: executable not found: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise InstrumentationError(f"{label}: timed out after {timeout}s") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise InstrumentationError(
            f"{label}: exit {completed.returncode}: {detail[:4000]}"
        )
    return completed.stdout


def dex_sort_key(name: str) -> int:
    match = _DEX_NAME.fullmatch(name)
    if match is None:
        raise InstrumentationError(f"invalid root dex filename: {name!r}")
    return 1 if match.group("number") is None else int(match.group("number"))


def dex_files(apk: Path) -> tuple[str, ...]:
    try:
        with zipfile.ZipFile(apk) as archive:
            all_names = archive.namelist()
            if len(all_names) != len(set(all_names)):
                raise InstrumentationError("APK has duplicate ZIP entry names")
            names = [name for name in all_names if _DEX_NAME.fullmatch(name)]
    except (OSError, zipfile.BadZipFile) as error:
        raise InstrumentationError(f"cannot read APK ZIP: {error}") from error
    ordered = tuple(sorted(names, key=dex_sort_key))
    if not ordered or ordered[0] != "classes.dex":
        raise InstrumentationError("APK has no primary classes.dex")
    return ordered


def smali_directory(dex_file: str) -> str:
    number = dex_sort_key(dex_file)
    return "smali" if number == 1 else f"smali_classes{number}"


def decoded_dex_directories(root: Path) -> tuple[str, ...]:
    names = []
    for item in root.iterdir():
        if item.is_dir() and _SMALI_DIR.fullmatch(item.name):
            names.append(item.name)
    return tuple(
        sorted(
            names,
            key=lambda name: 1
            if name == "smali"
            else int(_SMALI_DIR.fullmatch(name).group("number")),  # type: ignore[union-attr]
        )
    )


def require_exact_dex_directories(root: Path, dex: tuple[str, ...]) -> None:
    expected = tuple(smali_directory(name) for name in dex)
    actual = decoded_dex_directories(root)
    if actual != expected:
        raise InstrumentationError(
            f"decoded smali directories differ from APK dex set: expected={expected}, actual={actual}"
        )


def apk_package(aapt: str, apk: Path, *, timeout: float) -> str:
    output = run([aapt, "dump", "badging", str(apk)], timeout=timeout, label="aapt badging")
    match = _PACKAGE_LINE.search(output)
    if match is None:
        raise InstrumentationError(f"aapt reported no package for {apk}")
    attributes = {
        item.group("name"): item.group("value")
        for item in _ATTRIBUTE.finditer(match.group("attributes"))
    }
    package = attributes.get("name", "").strip()
    if not package:
        raise InstrumentationError(f"aapt reported an empty package for {apk}")
    return package


def parse_descriptor_parameters(signature: str, *, is_static: bool) -> list[tuple[int, str]]:
    left = signature.find("(")
    right = signature.find(")", left + 1)
    if left <= 0 or right < 0 or right == len(signature) - 1:
        raise InstrumentationError(f"invalid smali method signature: {signature!r}")
    descriptor = signature[left + 1 : right]
    result: list[tuple[int, str]] = []
    slot = 0
    if not is_static:
        result.append((slot, "object"))
        slot += 1
    cursor = 0
    while cursor < len(descriptor):
        character = descriptor[cursor]
        if character == "[":
            while cursor < len(descriptor) and descriptor[cursor] == "[":
                cursor += 1
            if cursor >= len(descriptor):
                raise InstrumentationError(f"truncated array parameter in {signature!r}")
            if descriptor[cursor] == "L":
                end = descriptor.find(";", cursor + 1)
                if end < 0:
                    raise InstrumentationError(f"truncated object parameter in {signature!r}")
                cursor = end + 1
            elif descriptor[cursor] in "ZBSCIFJD":
                cursor += 1
            else:
                raise InstrumentationError(f"invalid array parameter in {signature!r}")
            result.append((slot, "object"))
            slot += 1
        elif character == "L":
            end = descriptor.find(";", cursor + 1)
            if end < 0:
                raise InstrumentationError(f"truncated object parameter in {signature!r}")
            result.append((slot, "object"))
            slot += 1
            cursor = end + 1
        elif character in "JD":
            result.append((slot, "wide"))
            slot += 2
            cursor += 1
        elif character in "ZBSCIF":
            result.append((slot, "scalar"))
            slot += 1
            cursor += 1
        else:
            raise InstrumentationError(f"invalid parameter descriptor in {signature!r}")
    return result


def smali_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


@dataclass(frozen=True)
class MethodRecord:
    dex_file: str
    path: Path
    class_descriptor: str
    signature: str
    flags: frozenset[str]
    start: int
    end: int
    register_line: int | None
    register_kind: str | None
    register_value: int | None
    parameter_moves: tuple[tuple[int, str], ...]

    @property
    def code_bearing(self) -> bool:
        return "abstract" not in self.flags and "native" not in self.flags

    @property
    def unit_id(self) -> str:
        return f"{self.class_descriptor}->{self.signature}"

    @property
    def inventory_entry(self) -> str:
        kind = "code" if self.code_bearing else "declaration"
        flags = ",".join(sorted(self.flags))
        return (
            f"{self.dex_file}\t{self.class_descriptor}\t{self.signature}"
            f"\t{kind}\t{flags}"
        )


@dataclass
class ClassRecord:
    dex_file: str
    path: Path
    descriptor: str
    lines: list[str]
    methods: list[MethodRecord]

    @property
    def inventory_entry(self) -> str:
        return f"{self.dex_file}\t{self.descriptor}"


def method_header(line: str) -> tuple[str, frozenset[str]]:
    stripped = line.strip()
    if not stripped.startswith(".method"):
        raise InstrumentationError("internal method parser mismatch")
    body = stripped[len(".method") :].strip()
    if "#" in body:
        body = body.split("#", 1)[0].rstrip()
    tokens = body.split()
    if not tokens:
        raise InstrumentationError("empty .method declaration")
    signature = tokens[-1]
    if any(character.isspace() for character in signature) or "(" not in signature:
        raise InstrumentationError(f"unsupported method name encoding: {signature!r}")
    parse_descriptor_parameters(signature, is_static="static" in tokens[:-1])
    return signature, frozenset(tokens[:-1])


def parse_smali(path: Path, dex_file: str) -> ClassRecord:
    if path.is_symlink() or not path.is_file():
        raise InstrumentationError(f"smali input is missing or a symlink: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise InstrumentationError(f"smali is not UTF-8: {path}: {error}") from error
    if "\x00" in text:
        raise InstrumentationError(f"smali contains NUL: {path}")
    lines = text.splitlines(keepends=True)
    class_descriptors: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(".class"):
            declaration = stripped.split("#", 1)[0].strip().split()
            if not declaration or not declaration[-1].startswith("L") or not declaration[-1].endswith(";"):
                raise InstrumentationError(f"cannot parse class declaration in {path}")
            class_descriptors.append(declaration[-1])
    if len(class_descriptors) != 1:
        raise InstrumentationError(f"expected one .class declaration in {path}")
    descriptor = class_descriptors[0]

    methods: list[MethodRecord] = []
    cursor = 0
    signatures: set[str] = set()
    while cursor < len(lines):
        if not lines[cursor].strip().startswith(".method"):
            cursor += 1
            continue
        start = cursor
        signature, flags = method_header(lines[start])
        if signature in signatures:
            raise InstrumentationError(f"duplicate method {descriptor}->{signature}")
        signatures.add(signature)
        cursor += 1
        while cursor < len(lines) and lines[cursor].strip() != ".end method":
            if lines[cursor].strip().startswith(".method"):
                raise InstrumentationError(f"nested .method in {path}")
            cursor += 1
        if cursor >= len(lines):
            raise InstrumentationError(f"unterminated method {descriptor}->{signature}")
        end = cursor
        code_bearing = "abstract" not in flags and "native" not in flags
        directives: list[tuple[int, re.Match[str]]] = []
        for index in range(start + 1, end):
            match = _REGISTER_DIRECTIVE.fullmatch(lines[index])
            if match is not None:
                directives.append((index, match))
        if code_bearing and len(directives) != 1:
            raise InstrumentationError(
                f"concrete method {descriptor}->{signature} has {len(directives)} register directives"
            )
        if not code_bearing and directives:
            raise InstrumentationError(
                f"abstract/native method {descriptor}->{signature} has registers"
            )
        register_line: int | None = None
        register_kind: str | None = None
        register_value: int | None = None
        if directives:
            register_line, match = directives[0]
            register_kind = match.group("kind")
            register_value = int(match.group("value"), 0)
        parameter_moves = tuple(
            parse_descriptor_parameters(signature, is_static="static" in flags)
        )
        methods.append(
            MethodRecord(
                dex_file=dex_file,
                path=path,
                class_descriptor=descriptor,
                signature=signature,
                flags=flags,
                start=start,
                end=end,
                register_line=register_line,
                register_kind=register_kind,
                register_value=register_value,
                parameter_moves=parameter_moves,
            )
        )
        cursor = end + 1
    return ClassRecord(dex_file, path, descriptor, lines, methods)


def load_classes(root: Path, dex: tuple[str, ...]) -> list[ClassRecord]:
    require_exact_dex_directories(root, dex)
    classes: list[ClassRecord] = []
    descriptors: set[str] = set()
    for dex_file in dex:
        directory = root / smali_directory(dex_file)
        paths = sorted(directory.rglob("*.smali"), key=lambda item: item.as_posix())
        if not paths:
            raise InstrumentationError(f"decoded {dex_file} has no smali classes")
        for path in paths:
            record = parse_smali(path, dex_file)
            if record.descriptor in descriptors:
                # R8 emits its desugaring polyfills into every dex that needs
                # them, so the same synthetic legitimately appears more than once
                # -- Eventyay-Attendee carries two copies of
                # `L$r8$java8methods$utility$Long$hashCode$IJ;`, a backport of
                # `Long.hashCode`. Duplicated *app* classes stay fatal, because
                # then it is genuinely ambiguous which copy a probe belongs to.
                # These are skipped instead: they are compiler-supplied stand-ins
                # for JDK methods, not code anyone wrote or can navigate to, so
                # they are neither instrumented nor counted.
                if _R8_SYNTHETIC.match(record.descriptor):
                    continue
                raise InstrumentationError(
                    f"class descriptor appears in more than one dex: {record.descriptor}"
                )
            descriptors.add(record.descriptor)
            classes.append(record)
    return classes


def class_prefix(package: str) -> str:
    if (
        not package
        or package.startswith(".")
        or package.endswith(".")
        or ".." in package
        or any(not segment for segment in package.split("."))
        or _CONTROL.search(package)
    ):
        raise InstrumentationError(f"invalid application package: {package!r}")
    return "L" + package.replace(".", "/") + "/"


def select_class_prefix(
    classes: list[ClassRecord], package: str, inclusion_policy: str
) -> tuple[str, str]:
    """Resolve the one auditable namespace selected by the policy.

    ``app-classes-methods-v1`` deliberately considers only two app-specific
    signals: the declared application id and that id with one known build
    variant suffix removed. It never guesses a dominant unrelated namespace.
    Candidates are ranked by class count, then specificity, then lexical order.
    """

    declared = class_prefix(package)
    if inclusion_policy == APP_PACKAGE_POLICY:
        if not any(item.descriptor.startswith(declared) for item in classes):
            raise InstrumentationError(
                "declared application package contains no decoded classes"
            )
        return declared, "declared-application-id"
    if inclusion_policy != APP_CLASSES_POLICY:
        raise InstrumentationError(f"unsupported inclusion policy: {inclusion_policy!r}")

    candidates: list[tuple[str, str]] = [(declared, "declared-application-id")]
    for suffix in _VARIANT_SUFFIXES:
        if package.endswith(suffix):
            stripped = class_prefix(package[: -len(suffix)])
            if stripped != declared:
                candidates.append((stripped, f"variant-suffix-stripped:{suffix}"))
            break

    measured = [
        (
            sum(1 for item in classes if item.descriptor.startswith(prefix)),
            prefix,
            rule,
        )
        for prefix, rule in candidates
    ]
    measured = [item for item in measured if item[0] > 0]
    if not measured:
        # The declared id matched nothing and no single known suffix explains it.
        # Two shapes cause this, and both are real in the Themis corpus:
        #
        #   stacked suffixes   org.mozilla.rocket.debug.ting  -> org/mozilla/rocket/
        #   unrelated id       com.eventyay.attendee ships    -> org/fossasia/openevent/
        #
        # Six of fifty apps failed here, all of which had been measured
        # successfully before, so refusing them loses real coverage rather than
        # protecting the metric. `code_coverage.summarise_for_package` already
        # resolves the first shape by dropping trailing components one at a time
        # and the second by falling back to the namespaces actually present; this
        # mirrors that so both places treat the same corpus the same way.
        #
        # Both fallbacks stay auditable: the returned rule records which one
        # applied and how many classes it found, so a bundle never hides the fact
        # that its namespace was inferred rather than declared.
        parts = [item for item in declared.strip("L").split("/") if item]
        # At least two components must survive. `min(3, len(parts) - 1)` alone is
        # not a sufficient bound: `com.orgzlyrevived` has two components, so one
        # reduction yields a bare `Lcom/`, which selected the bundled Dropbox SDK
        # as the app and then failed on a 488-local library method. A single
        # top-level component is never an application namespace.
        for cut in range(1, min(3, len(parts) - 2) + 1):
            candidate = class_prefix(".".join(parts[:-cut]))
            found = sum(1 for item in classes if item.descriptor.startswith(candidate))
            if found:
                measured = [(found, candidate, f"trailing-components-dropped:{cut}")]
                break

    if not measured:
        # Nothing derived from the declared id matches. Fall back to the dominant
        # namespace in the decoded classes, excluding bundled libraries -- they are
        # instrumented along with the app and would otherwise swamp the count.
        tally: dict[str, int] = {}
        for item in classes:
            path = item.descriptor.strip("L;").split("/")
            if len(path) < 3 or item.descriptor.startswith(_LIBRARY_DESCRIPTORS):
                continue
            key = "/".join(path[:3])
            tally[key] = tally.get(key, 0) + 1
        if tally:
            best = max(tally.items(), key=lambda pair: (pair[1], pair[0]))
            measured = [(best[1], f"L{best[0]}/", "dominant-app-namespace")]

    if not measured:
        raise InstrumentationError(
            "no decoded classes match the declared application id, its known "
            "variant-suffix-stripped candidate, any bounded trailing-component "
            "reduction of it, or a dominant non-library namespace"
        )
    count, prefix, rule = sorted(
        measured,
        key=lambda item: (-item[0], -len(item[1]), item[1]),
    )[0]
    return prefix, f"largest-class-bearing-candidate:{rule}:classes={count}"


# Methods that exist only because a coverage tool rewrote the APK before we saw
# it. They are not part of the app under test, so counting them measures the
# instrumentation rather than the program.
#
# This matters on the Themis corpus, whose APKs ship already JaCoCo-instrumented:
# JaCoCo adds a `$jacocoInit()[Z` to every class it touches, and that method runs
# on the class's first use, so it is far cheaper to reach than real code. Measured
# across the 50-app v3 campaign it was 39,358 units, and it was covered at 41.69%
# against 27.76% for real methods -- lifting reported per-app coverage by 1.39pp
# and pooled coverage by 1.60pp purely as an artifact.
#
# Compiler-generated members (`$r8`, `$values`, `$default`) are deliberately kept.
# They ship in the app whether or not anyone measures it, so they are part of the
# program; `$jacocoInit` is not.
INSTRUMENTATION_ARTIFACT_METHODS = ("$jacocoInit",)


def instrumentation_artifact(method: MethodRecord) -> bool:
    """Whether this method was injected by another coverage tool, not the app."""

    name = method.signature.split("(", 1)[0]
    return name in INSTRUMENTATION_ARTIFACT_METHODS


def selected_method(method: MethodRecord, selected_class_prefix: str, helper: str) -> bool:
    if not method.code_bearing or method.class_descriptor == helper:
        return False
    if instrumentation_artifact(method):
        return False
    return method.class_descriptor.startswith(selected_class_prefix)


def leading_code_index(lines: list[str], method: MethodRecord) -> int:
    assert method.register_line is not None
    annotation_depth = 0
    harmless = (
        ".param",
        ".end param",
        ".parameter",
        ".prologue",
        ".line",
        ".local",
        ".end local",
        ".restart local",
        ".source",
    )
    for index in range(method.register_line + 1, method.end):
        stripped = lines[index].strip()
        if annotation_depth:
            if stripped.startswith((".end annotation", ".end subannotation")):
                annotation_depth -= 1
            elif stripped.startswith(".annotation") or _SUBANNOTATION_OPEN.search(stripped):
                annotation_depth += 1
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(".annotation") or _SUBANNOTATION_OPEN.search(stripped):
            annotation_depth = 1
            continue
        if stripped.startswith(harmless):
            continue
        if stripped.startswith("."):
            raise InstrumentationError(
                f"unsupported leading directive {stripped!r} in {method.unit_id}"
            )
        return index
    raise InstrumentationError(f"concrete method has no executable body: {method.unit_id}")


def local_register_count(method: MethodRecord) -> tuple[int, int]:
    assert method.register_value is not None and method.register_kind is not None
    parameter_slots = 0
    for slot, kind in method.parameter_moves:
        parameter_slots = max(parameter_slots, slot + (2 if kind == "wide" else 1))
    if method.register_kind == "locals":
        locals_count = method.register_value
        total = locals_count + parameter_slots
    else:
        total = method.register_value
        locals_count = total - parameter_slots
    if locals_count < 0:
        raise InstrumentationError(f"parameter registers exceed total in {method.unit_id}")
    if locals_count > 255:
        raise InstrumentationError(
            f"{method.unit_id} has {locals_count} locals; const-string supports at most v255"
        )
    if total >= 65535:
        raise InstrumentationError(f"register limit prevents instrumenting {method.unit_id}")
    return locals_count, total


def rewrite_parameter_aliases(
    line: str,
    *,
    locals_count: int,
    parameter_slots: int,
    unit_id: str,
) -> str:
    """Keep original parameter registers at their pre-growth physical slots.

    Growing a method shifts every symbolic ``pN`` alias by one. Besides changing
    mixed ``p``/``v`` semantics, that can push a previously encodable short-form
    operand from v15 to v16. Rewrite register operands (but never strings,
    comments, labels, fields, or method names) to their old physical ``v`` slot;
    the injected typed moves populate those slots before original code executes.
    """

    result: list[str] = []
    index = 0
    in_string = False
    while index < len(line):
        character = line[index]
        if not in_string and character == "#":
            result.append(line[index:])
            break
        if character == '"':
            in_string = not in_string
            result.append(character)
            index += 1
            continue
        if in_string and character == "\\" and index + 1 < len(line):
            result.append(line[index : index + 2])
            index += 2
            continue
        if not in_string and character == "p":
            end = index + 1
            while end < len(line) and line[end].isdigit():
                end += 1
            previous = line[index - 1] if index else ""
            following = line[end] if end < len(line) else ""
            previous_ok = index == 0 or previous.isspace() or previous in "{,"
            following_ok = end == len(line) or following.isspace() or following in ",}"
            if end > index + 1 and previous_ok and following_ok:
                slot = int(line[index + 1 : end])
                if slot >= parameter_slots:
                    raise InstrumentationError(
                        f"parameter alias p{slot} exceeds the signature slots in {unit_id}"
                    )
                result.append(f"v{locals_count + slot}")
                index = end
                continue
        result.append(character)
        index += 1
    return "".join(result)


def instrument_class(
    record: ClassRecord,
    selected: set[str],
    *,
    helper: str,
    method_prefix: str,
) -> None:
    lines = record.lines
    methods = [method for method in record.methods if method.unit_id in selected]
    for method in sorted(methods, key=lambda item: item.start, reverse=True):
        assert method.register_line is not None
        locals_count, total = local_register_count(method)
        directive = _REGISTER_DIRECTIVE.fullmatch(lines[method.register_line])
        if directive is None:
            raise InstrumentationError(f"register directive changed in {method.unit_id}")
        replacement_value = (
            method.register_value + 1
            if method.register_kind == "locals"
            else total + 1
        )
        if not lines[method.register_line].endswith(("\n", "\r")):
            raise InstrumentationError(f"register directive has no line ending in {method.unit_id}")
        lines[method.register_line] = (
            directive.group("prefix")
            + str(replacement_value)
            + directive.group("suffix")
        )
        insert_at = leading_code_index(lines, method)
        parameter_slots = total - locals_count
        for line_index in range(method.register_line + 1, method.end):
            if lines[line_index].lstrip().startswith("."):
                continue
            lines[line_index] = rewrite_parameter_aliases(
                lines[line_index],
                locals_count=locals_count,
                parameter_slots=parameter_slots,
                unit_id=method.unit_id,
            )
        message = method_prefix + method.unit_id
        temporary = f"v{locals_count}"
        invocation = (
            f"    invoke-static {{{temporary}}}, {helper}->hit(Ljava/lang/String;)V\n"
            if locals_count <= 15
            else f"    invoke-static/range {{{temporary} .. {temporary}}}, "
            f"{helper}->hit(Ljava/lang/String;)V\n"
        )
        injected = [
            f"    const-string/jumbo {temporary}, {smali_string(message)}\n",
            invocation,
        ]
        for slot, kind in method.parameter_moves:
            opcode = {
                "object": "move-object/16",
                "wide": "move-wide/16",
                "scalar": "move/16",
            }[kind]
            injected.append(
                f"    {opcode} v{locals_count + slot}, p{slot}\n"
            )
        lines[insert_at:insert_at] = injected
    record.path.write_text("".join(lines), encoding="utf-8")


def helper_source(helper: str, *, limit: int, log_tag: str) -> str:
    if limit < 1 or limit > 0x7FFFFFFF:
        raise InstrumentationError("helper probe limit is outside signed 32-bit range")
    return f""".class public final {helper}
.super Ljava/lang/Object;
.source \"VALORDroidMethodProbe\"

.field private static final LIMIT:I = {limit}
.field private static final SEEN:Ljava/util/HashSet;

.method static constructor <clinit>()V
    .locals 1

    new-instance v0, Ljava/util/HashSet;
    invoke-direct {{v0}}, Ljava/util/HashSet;-><init>()V
    sput-object v0, {helper}->SEEN:Ljava/util/HashSet;
    return-void
.end method

.method public static declared-synchronized hit(Ljava/lang/String;)V
    .locals 2

    :try_start_0
    sget-object v0, {helper}->SEEN:Ljava/util/HashSet;
    invoke-virtual {{v0}}, Ljava/util/HashSet;->size()I
    move-result v0
    sget v1, {helper}->LIMIT:I
    if-ge v0, v1, :return

    sget-object v0, {helper}->SEEN:Ljava/util/HashSet;
    invoke-virtual {{v0, p0}}, Ljava/util/HashSet;->add(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :return

    const-string/jumbo v0, {smali_string(log_tag)}
    invoke-static {{v0, p0}}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    :try_end_0
    .catch Ljava/lang/Throwable; {{:try_start_0 .. :try_end_0}} :catch_0

    :return
    return-void

    :catch_0
    move-exception v0
    return-void
.end method
"""


def write_helper(root: Path, helper: str, *, limit: int, log_tag: str) -> Path:
    relative = helper[1:-1] + ".smali"
    destination = root / "smali" / relative
    if destination.exists():
        raise InstrumentationError(f"helper class path already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        helper_source(helper, limit=limit, log_tag=log_tag), encoding="utf-8"
    )
    return destination


def inventory(classes: Iterable[ClassRecord], *, exclude: str | None = None) -> tuple[list[str], list[str]]:
    selected = [item for item in classes if item.descriptor != exclude]
    class_entries = sorted(item.inventory_entry for item in selected)
    method_entries = sorted(
        method.inventory_entry for item in selected for method in item.methods
    )
    return class_entries, method_entries


def code_method_count(classes: Iterable[ClassRecord]) -> int:
    return sum(method.code_bearing for item in classes for method in item.methods)


def decode_const_string(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    match = re.match(r"^const-string(?:/jumbo)?\s+(v[0-9]+),\s*(.*)$", stripped)
    if match is None:
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(match.group(2))
    except json.JSONDecodeError:
        return None
    remainder = match.group(2)[end:].strip()
    if type(value) is not str or (remainder and not remainder.startswith("#")):
        return None
    return match.group(1), value


def helper_invocations(method: MethodRecord, lines: list[str], helper: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    target = helper + "->hit(Ljava/lang/String;)V"
    for index in range(method.start + 1, method.end):
        stripped = lines[index].strip()
        match = _INVOKE_HELPER.fullmatch(stripped)
        if match is None or match.group("target") != target:
            continue
        register_text = match.group("registers").strip()
        if match.group("range"):
            range_match = re.fullmatch(r"(v[0-9]+)\s+\.\.\s+(v[0-9]+)", register_text)
            if range_match is None or range_match.group(1) != range_match.group(2):
                raise InstrumentationError(f"malformed helper range invocation in {method.unit_id}")
            register = range_match.group(1)
        else:
            if re.fullmatch(r"v[0-9]+", register_text) is None:
                raise InstrumentationError(f"malformed helper invocation in {method.unit_id}")
            register = register_text
        previous = index - 1
        while previous > method.start and (
            not lines[previous].strip() or lines[previous].lstrip().startswith("#")
        ):
            previous -= 1
        constant = decode_const_string(lines[previous])
        if constant is None or constant[0] != register:
            raise InstrumentationError(
                f"helper invocation is not immediately bound to one string in {method.unit_id}"
            )
        result.append((index, constant[1]))
    return result


def verify_helper(record: ClassRecord, *, helper: str, log_tag: str) -> None:
    signatures = {method.signature for method in record.methods}
    if signatures != {"<clinit>()V", "hit(Ljava/lang/String;)V"}:
        raise InstrumentationError("final helper method set is not exact")
    hit = next(method for method in record.methods if method.signature.startswith("hit("))
    if not {"public", "static", "declared-synchronized"} <= hit.flags:
        raise InstrumentationError("final helper hit method is not public static declared-synchronized")
    text = "".join(record.lines)
    required = (
        "Ljava/util/HashSet;->add(Ljava/lang/Object;)Z",
        "Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I",
        smali_string(log_tag),
        f"{helper}->LIMIT:I",
        f"{helper}->SEEN:Ljava/util/HashSet;",
    )
    if any(item not in text for item in required):
        raise InstrumentationError("final helper implementation is incomplete")
    if text.count("Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I") != 1:
        raise InstrumentationError("final helper does not contain exactly one log call")
    if f"{helper}->hit(Ljava/lang/String;)V" in text:
        raise InstrumentationError("final helper is recursive")


def build_audit(
    *,
    original_classes: list[ClassRecord],
    final_classes: list[ClassRecord],
    original_dex: tuple[str, ...],
    final_dex: tuple[str, ...],
    helper: str,
    helper_path: Path,
    selected_class_prefix: str,
    selection_rule: str,
    units: tuple[str, ...],
    backend_version: str,
    inclusion_policy: str,
    log_tag: str,
    method_prefix: str,
    original_apk_sha256: str,
    instrumented_apk_sha256: str,
) -> dict[str, object]:
    if final_dex != original_dex:
        raise InstrumentationError("final signed APK dex set differs from original")
    original_class_inventory, original_method_inventory = inventory(original_classes)
    final_class_inventory, final_method_inventory = inventory(final_classes, exclude=helper)
    if original_class_inventory != final_class_inventory:
        raise InstrumentationError("final signed APK changed original class inventory")
    if original_method_inventory != final_method_inventory:
        raise InstrumentationError("final signed APK changed original method inventory")

    helpers = [item for item in final_classes if item.descriptor == helper]
    if len(helpers) != 1 or helpers[0].dex_file != "classes.dex":
        raise InstrumentationError("final helper is not exactly once in primary dex")
    verify_helper(helpers[0], helper=helper, log_tag=log_tag)

    expected_units = {
        method.unit_id
        for item in final_classes
        for method in item.methods
        if selected_method(method, selected_class_prefix, helper)
    }
    if tuple(sorted(expected_units)) != units:
        raise InstrumentationError("final policy-selected methods differ from emitted units")

    sites: list[tuple[str, str]] = []
    for item in final_classes:
        for method in item.methods:
            invocations = helper_invocations(method, item.lines, helper)
            if method.unit_id in expected_units:
                if len(invocations) != 1:
                    raise InstrumentationError(
                        f"expected one final probe in {method.unit_id}, found {len(invocations)}"
                    )
                expected_message = method_prefix + method.unit_id
                if invocations[0][1] != expected_message:
                    raise InstrumentationError(f"final probe string mismatch in {method.unit_id}")
                sites.append((method.dex_file, method.unit_id))
            elif invocations:
                raise InstrumentationError(f"probe exists outside selected universe: {method.unit_id}")
    sites.sort(key=lambda item: (dex_sort_key(item[0]), item[1]))
    if len(sites) != len(units) or len(sites) < 1:
        raise InstrumentationError("final audit did not verify a positive one-to-one probe set")

    per_dex: list[dict[str, object]] = []
    for dex_file in original_dex:
        original_here = [item for item in original_classes if item.dex_file == dex_file]
        final_here = [item for item in final_classes if item.dex_file == dex_file]
        selected_here = sum(1 for site_dex, _unit in sites if site_dex == dex_file)
        per_dex.append(
            {
                "dex_file": dex_file,
                "original_class_count": len(original_here),
                "final_class_count": len(final_here),
                "original_code_method_count": code_method_count(original_here),
                "final_code_method_count": code_method_count(final_here),
                "selected_method_count": selected_here,
                "verified_probe_count": selected_here,
            }
        )

    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "backend": BACKEND,
        "backend_version": backend_version,
        "inclusion_policy": inclusion_policy,
        "selected_class_prefix": selected_class_prefix,
        "selection_rule": selection_rule,
        "log_tag": log_tag,
        "method_prefix": method_prefix,
        "original_apk_sha256": original_apk_sha256,
        "instrumented_apk_sha256": instrumented_apk_sha256,
        "original_dex_files": list(original_dex),
        "final_dex_files": list(final_dex),
        "helper_class": helper,
        "helper_smali_sha256": sha256_file(helper_path),
        "original_classes_sha256": canonical_hash(original_class_inventory),
        "final_original_classes_sha256": canonical_hash(final_class_inventory),
        "original_methods_sha256": canonical_hash(original_method_inventory),
        "final_original_methods_sha256": canonical_hash(final_method_inventory),
        "original_class_count": len(original_classes),
        "final_class_count": len(final_classes),
        "original_code_method_count": code_method_count(original_classes),
        "final_code_method_count": code_method_count(final_classes),
        "selected_method_count": len(units),
        "verified_probe_count": len(sites),
        "unit_ids_sha256": canonical_hash(list(units)),
        "probe_sites": [
            {"dex_file": dex_file, "unit_id": unit_id}
            for dex_file, unit_id in sites
        ],
        "per_dex": per_dex,
        "zipalign_verified": True,
        "signature_verified": True,
    }


def require_regular(path: Path, name: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise InstrumentationError(f"{name} is not a regular non-symlink file: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--input-apk", required=True, type=Path)
    parser.add_argument("--output-apk", required=True, type=Path)
    parser.add_argument("--units-output", required=True, type=Path)
    parser.add_argument("--receipt-output", required=True, type=Path)
    parser.add_argument("--structural-audit-output", required=True, type=Path)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--backend-version", required=True)
    parser.add_argument("--inclusion-policy", required=True)
    parser.add_argument("--log-tag", required=True)
    parser.add_argument("--method-prefix", required=True)
    parser.add_argument("--java", required=True)
    parser.add_argument("--apktool-jar", required=True, type=Path)
    parser.add_argument("--zipalign", required=True)
    parser.add_argument("--apksigner", required=True)
    parser.add_argument("--keystore", required=True, type=Path)
    parser.add_argument("--aapt", required=True)
    arguments = parser.parse_args()

    try:
        if arguments.protocol != PROTOCOL:
            raise InstrumentationError(f"unsupported protocol: {arguments.protocol}")
        if arguments.backend != BACKEND:
            raise InstrumentationError(f"unsupported backend: {arguments.backend}")
        if arguments.inclusion_policy not in SUPPORTED_POLICIES:
            raise InstrumentationError(
                f"unsupported inclusion policy: {arguments.inclusion_policy!r}; "
                f"expected one of {sorted(SUPPORTED_POLICIES)!r}"
            )
        for value, name in (
            (arguments.backend_version, "backend version"),
            (arguments.log_tag, "log tag"),
            (arguments.method_prefix, "method prefix"),
        ):
            if not value or _CONTROL.search(value):
                raise InstrumentationError(f"{name} must be nonempty and contain no controls")
        if len(arguments.log_tag.encode("utf-8")) > 23:
            raise InstrumentationError("log tag exceeds the portable 23-byte Android limit")
        for path, name in (
            (arguments.input_apk, "input APK"),
            (arguments.apktool_jar, "Apktool jar"),
            (arguments.keystore, "keystore"),
            (Path(arguments.java), "Java executable"),
            (Path(arguments.zipalign), "zipalign"),
            (Path(arguments.apksigner), "apksigner"),
            (Path(arguments.aapt), "aapt"),
        ):
            require_regular(path, name)
        outputs = (
            arguments.output_apk,
            arguments.units_output,
            arguments.receipt_output,
            arguments.structural_audit_output,
        )
        if len(set(outputs)) != len(outputs):
            raise InstrumentationError("output paths must be distinct")
        if any(path.exists() for path in outputs):
            raise InstrumentationError("an output path already exists")

        producer = os.environ.get("VD_PRODUCER", "").strip()
        producer_version = os.environ.get("VD_PRODUCER_VERSION", "").strip()
        source_revision = os.environ.get("VD_SOURCE_REVISION", "").strip()
        if not producer or not producer_version or re.fullmatch(r"[0-9a-f]{40}", source_revision) is None:
            raise InstrumentationError(
                "VD_PRODUCER, VD_PRODUCER_VERSION, and 40-hex VD_SOURCE_REVISION are required"
            )
        timeout = float(os.environ.get("VD_STEP_TIMEOUT_SECONDS", "1800"))
        if timeout <= 0:
            raise InstrumentationError("VD_STEP_TIMEOUT_SECONDS must be positive")
        keystore_password = os.environ.get("VD_KEYSTORE_PASSWORD", "android")
        keystore_alias = os.environ.get("VD_KEYSTORE_ALIAS", "android")

        original_dex = dex_files(arguments.input_apk)
        original_sha256 = sha256_file(arguments.input_apk)
        package = apk_package(arguments.aapt, arguments.input_apk, timeout=120.0)
        helper = f"Lio/valordroid/runtime/MethodProbe_{original_sha256[:16]};"

        workspace = Path(tempfile.mkdtemp(prefix="smali-logcat-instrumenter."))
        try:
            decoded = workspace / "decoded"
            frameworks = workspace / "frameworks"
            frameworks.mkdir()
            run(
                [
                    arguments.java,
                    "-jar",
                    str(arguments.apktool_jar),
                    "d",
                    "-f",
                    "-r",
                    "-p",
                    str(frameworks),
                    "-o",
                    str(decoded),
                    str(arguments.input_apk),
                ],
                timeout=timeout,
                label="Apktool decode original",
            )
            original_classes = load_classes(decoded, original_dex)
            if any(item.descriptor == helper for item in original_classes):
                raise InstrumentationError("derived helper class collides with input class")
            for item in original_classes:
                if helper in "".join(item.lines):
                    raise InstrumentationError("input already references the derived helper")

            selected_class_prefix, selection_rule = select_class_prefix(
                original_classes,
                package,
                arguments.inclusion_policy,
            )
            selected_records = [
                method
                for item in original_classes
                for method in item.methods
                if selected_method(method, selected_class_prefix, helper)
            ]
            units = tuple(sorted(method.unit_id for method in selected_records))
            if not units or len(set(units)) != len(units):
                raise InstrumentationError(
                    "policy selected no methods or produced duplicate unit IDs"
                )
            for unit in units:
                if len((arguments.method_prefix + unit).encode("utf-8")) > 4000:
                    raise InstrumentationError(f"probe message exceeds 4000 bytes: {unit}")
            selected_ids = set(units)
            for item in original_classes:
                if any(method.unit_id in selected_ids for method in item.methods):
                    instrument_class(
                        item,
                        selected_ids,
                        helper=helper,
                        method_prefix=arguments.method_prefix,
                    )
            write_helper(decoded, helper, limit=len(units), log_tag=arguments.log_tag)

            unsigned = workspace / "unsigned.apk"
            run(
                [
                    arguments.java,
                    "-jar",
                    str(arguments.apktool_jar),
                    "b",
                    "-f",
                    "-p",
                    str(frameworks),
                    "-o",
                    str(unsigned),
                    str(decoded),
                ],
                timeout=timeout,
                label="Apktool rebuild",
            )
            aligned = workspace / "aligned.apk"
            run(
                [arguments.zipalign, "-p", "-f", "4", str(unsigned), str(aligned)],
                timeout=timeout,
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
                timeout=timeout,
                label="apksigner sign",
            )
            run(
                [arguments.zipalign, "-c", "-p", "4", str(signed)],
                timeout=timeout,
                label="zipalign verify",
            )
            run(
                [arguments.apksigner, "verify", str(signed)],
                timeout=timeout,
                label="apksigner verify",
            )
            if apk_package(arguments.aapt, signed, timeout=120.0) != package:
                raise InstrumentationError("instrumented package differs from original")

            final_dex = dex_files(signed)
            audited = workspace / "audited"
            audit_frameworks = workspace / "audit-frameworks"
            audit_frameworks.mkdir()
            run(
                [
                    arguments.java,
                    "-jar",
                    str(arguments.apktool_jar),
                    "d",
                    "-f",
                    "-r",
                    "-p",
                    str(audit_frameworks),
                    "-o",
                    str(audited),
                    str(signed),
                ],
                timeout=timeout,
                label="Apktool decode final signed APK",
            )
            final_classes = load_classes(audited, final_dex)
            helper_records = [item for item in final_classes if item.descriptor == helper]
            if len(helper_records) != 1:
                raise InstrumentationError("final signed APK does not contain one helper")
            audit = build_audit(
                original_classes=original_classes,
                final_classes=final_classes,
                original_dex=original_dex,
                final_dex=final_dex,
                helper=helper,
                helper_path=helper_records[0].path,
                selected_class_prefix=selected_class_prefix,
                selection_rule=selection_rule,
                units=units,
                backend_version=arguments.backend_version,
                inclusion_policy=arguments.inclusion_policy,
                log_tag=arguments.log_tag,
                method_prefix=arguments.method_prefix,
                original_apk_sha256=original_sha256,
                instrumented_apk_sha256=sha256_file(signed),
            )

            for path in outputs:
                path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(signed, arguments.output_apk)
            arguments.units_output.write_text(
                json.dumps(list(units), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            arguments.structural_audit_output.write_text(
                json.dumps(audit, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            receipt = {
                "schema_version": RECEIPT_SCHEMA_VERSION,
                "protocol": PROTOCOL,
                "producer": producer,
                "producer_version": producer_version,
                "source_revision": source_revision,
                "backend": BACKEND,
                "backend_version": arguments.backend_version,
                "inclusion_policy": arguments.inclusion_policy,
                "log_tag": arguments.log_tag,
                "method_prefix": arguments.method_prefix,
                "original_apk_sha256": original_sha256,
                "instrumented_apk_sha256": sha256_file(arguments.output_apk),
                "unit_count": len(units),
                "unit_ids_sha256": canonical_hash(list(units)),
                "instrumentation_complete": True,
                "enumeration_complete": True,
                "one_probe_per_unit": True,
                "verified_probe_count": len(units),
                "structural_audit_sha256": sha256_file(arguments.structural_audit_output),
            }
            arguments.receipt_output.write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            sys.stderr.write(
                f"smali-logcat-instrumenter: {package}: {len(original_dex)} dex, "
                f"{len(units)} verified method probes; selected "
                f"{selected_class_prefix} via {selection_rule}\n"
            )
            return 0
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
    except (InstrumentationError, OSError, ValueError, zipfile.BadZipFile) as error:
        fail(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
