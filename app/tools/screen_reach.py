#!/usr/bin/env python3
"""Measure canonical declared-activity reach for explicitly selected runs.

Coverage and activity reach are always derived from the same validated finished
run.  No run is selected by independently maximizing reach.  Selection files
are identity-bound by default; ad hoc use can explicitly select exact run paths.

Examples:

  screen_reach.py --selection selected-runs.json --output reach.json
  screen_reach.py --run App=/campaign/wave/runs/App.gemma

The historical positional form remains accepted only with an explicit campaign
root and an identity-rich selection file:

  screen_reach.py --campaign-root /campaign 'v2-*' selected-runs.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Keep the checked-in tool directly executable without requiring an installed
# package.  This is a repository-local import path, not a host campaign path.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from valordroid.activity_reach import (  # noqa: E402
    ActivityObservation,
    activity_observations_from_details,
    canonicalize_activity_component,
)
from valordroid.android.manifest import route_discovery_from_mapping  # noqa: E402
from valordroid.artifacts import read_json  # noqa: E402
from valordroid.ledger import ZERO_HASH  # noqa: E402
from valordroid.prepared import verify_prepared_snapshot  # noqa: E402
from valordroid.store import RunStore  # noqa: E402
from valordroid.validator import validate_run  # noqa: E402

REPORT_SCHEMA_VERSION = 1
SELECTION_RULE = "explicit_identity_bound_run_v1"
ACTIVITY_REACH_RULE = "canonical_declared_intersection_v1"
COVERAGE_RULE = "same_fully_validated_finished_run_v1"
VALIDATION_RULE = "valordroid_full_run_validation_v1"


class ScreenReachError(ValueError):
    """A selection or evidence contract failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SelectedRun:
    """One exact execution selected for one report row."""

    selection_index: int
    app: str
    run_path: Path
    source: str
    expected_identity: Mapping[str, object]
    expected_coverage: Mapping[str, object]
    metadata: Mapping[str, object]
    identity_bound: bool


_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _issue(code: str, message: str, *, severity: str = "error") -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _sorted_issues(issues: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    return [
        dict(item)
        for item in sorted(
            issues,
            key=lambda item: (
                _SEVERITY_ORDER.get(str(item.get("severity")), 99),
                str(item.get("code")),
                str(item.get("message")),
            ),
        )
    ]


def _no_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScreenReachError(
                "duplicate_json_key", f"JSON object repeats key {key!r}"
            )
        result[key] = value
    return result


def _read_json_strict(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ScreenReachError(
            "selection_unreadable", f"cannot read selection file {path}: {error}"
        ) from error
    try:
        return json.loads(text, object_pairs_hook=_no_duplicate_object_keys)
    except ScreenReachError:
        raise
    except json.JSONDecodeError as error:
        raise ScreenReachError(
            "selection_invalid_json", f"selection file {path} is invalid JSON: {error}"
        ) from error


def _unique_alias(
    mappings: Sequence[Mapping[str, object]], names: Sequence[str], label: str
) -> object | None:
    found: list[object] = []
    for mapping in mappings:
        for name in names:
            if name in mapping and mapping[name] is not None:
                found.append(mapping[name])
    if not found:
        return None
    first = found[0]
    if any(value != first for value in found[1:]):
        raise ScreenReachError(
            "selection_ambiguous",
            f"selection row has conflicting aliases for {label}",
        )
    return first


def _nonempty_string(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ScreenReachError(
            "selection_identity_invalid", f"{label} must be a nonempty string"
        )
    return value.strip()


def _digest(value: object, label: str) -> str:
    text = _nonempty_string(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ScreenReachError(
            "selection_identity_invalid",
            f"{label} must be a lowercase SHA-256 digest",
        )
    return text


def _number(value: object, label: str) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ScreenReachError(
            "selection_coverage_invalid", f"{label} must be a finite number"
        )
    return value


def _selection_row(
    raw: object,
    *,
    index: int,
    source: str,
    base_dir: Path,
    strict: bool,
) -> SelectedRun:
    if type(raw) is not dict:
        raise ScreenReachError(
            "selection_row_invalid", f"selection row {index} must be a JSON object"
        )
    row: dict[str, object] = raw
    nested = row.get("identity")
    if nested is None:
        identity: Mapping[str, object] = {}
    elif type(nested) is dict:
        identity = nested
    else:
        raise ScreenReachError(
            "selection_identity_invalid",
            f"selection row {index} identity must be an object",
        )
    mappings = (row, identity)

    app_value = _unique_alias(mappings, ("app", "app_name"), "app")
    app = _nonempty_string(app_value, f"selection row {index} app")
    path_value = _unique_alias(
        mappings, ("run_path", "run", "path"), "run path"
    )
    path_text = _nonempty_string(path_value, f"selection row {index} run_path")
    run_path = Path(path_text).expanduser()
    if not run_path.is_absolute():
        run_path = base_dir / run_path
    run_path = run_path.resolve(strict=False)

    expected: dict[str, object] = {}
    identity_fields = {
        "run_id": ("run_id",),
        "prepared_bundle_sha256": ("prepared_bundle_sha256",),
        "universe_sha256": ("universe_sha256",),
        "route_discovery_sha256": ("route_discovery_sha256",),
        "actions_terminal_hash": (
            "actions_terminal_hash",
            "action_terminal_hash",
        ),
    }
    for target, aliases in identity_fields.items():
        value = _unique_alias(mappings, aliases, target)
        if value is not None:
            expected[target] = value

    evidence_heads = _unique_alias(mappings, ("evidence_heads",), "evidence heads")
    if evidence_heads is not None:
        if type(evidence_heads) is not dict:
            raise ScreenReachError(
                "selection_identity_invalid", "evidence_heads must be an object"
            )
        actions = evidence_heads.get("actions")
        if type(actions) is not dict:
            raise ScreenReachError(
                "selection_identity_invalid",
                "evidence_heads.actions must be an object",
            )
        nested_hash = actions.get("terminal_hash")
        if nested_hash is not None:
            existing = expected.get("actions_terminal_hash")
            if existing is not None and existing != nested_hash:
                raise ScreenReachError(
                    "selection_ambiguous",
                    "selection row has conflicting action terminal hashes",
                )
            expected["actions_terminal_hash"] = nested_hash
        if "count" in actions:
            expected["actions_count"] = actions["count"]

    action_count = _unique_alias(mappings, ("actions_count",), "action count")
    if action_count is not None:
        existing_count = expected.get("actions_count")
        if existing_count is not None and existing_count != action_count:
            raise ScreenReachError(
                "selection_ambiguous",
                "selection row has conflicting action counts",
            )
        expected["actions_count"] = action_count

    required = tuple(identity_fields)
    missing = [name for name in required if name not in expected]
    if strict and missing:
        raise ScreenReachError(
            "selection_identity_missing",
            f"selection row {index} is missing identity fields: {', '.join(missing)}",
        )

    if "run_id" in expected:
        expected["run_id"] = _nonempty_string(expected["run_id"], "run_id")
    for name in (
        "prepared_bundle_sha256",
        "universe_sha256",
        "route_discovery_sha256",
        "actions_terminal_hash",
    ):
        if name in expected:
            expected[name] = _digest(expected[name], name)
    if "actions_count" in expected:
        count = expected["actions_count"]
        if type(count) is not int or count < 0:
            raise ScreenReachError(
                "selection_identity_invalid",
                "actions_count must be a nonnegative integer",
            )

    expected_coverage: dict[str, object] = {}
    aliases = {
        "observed_covered_units": ("observed_covered_units", "covered"),
        "total_units": ("total_units", "total"),
        "observed_coverage_percent": ("observed_coverage_percent", "percent"),
    }
    for target, names in aliases.items():
        value = _unique_alias((row,), names, target)
        if value is not None:
            expected_coverage[target] = _number(value, target)
    for name in ("observed_covered_units", "total_units"):
        if name in expected_coverage and (
            type(expected_coverage[name]) is not int or expected_coverage[name] < 0
        ):
            raise ScreenReachError(
                "selection_coverage_invalid",
                f"{name} must be a nonnegative integer",
            )

    status = row.get("status")
    if status is not None:
        expected_coverage["status"] = status
    validation_ok = row.get("validation_ok")
    if validation_ok is not None:
        expected_coverage["validation_ok"] = validation_ok

    metadata = {
        name: row[name]
        for name in ("arm", "wave")
        if name in row and type(row[name]) in (str, int, float, bool)
    }
    return SelectedRun(
        selection_index=index,
        app=app,
        run_path=run_path,
        source=source,
        expected_identity=dict(sorted(expected.items())),
        expected_coverage=dict(sorted(expected_coverage.items())),
        metadata=dict(sorted(metadata.items())),
        identity_bound=not missing,
    )


def _reject_duplicate_selections(selections: Sequence[SelectedRun]) -> None:
    by_app: dict[str, int] = {}
    by_path: dict[str, int] = {}
    by_identity: dict[tuple[object, ...], int] = {}
    for selection in selections:
        if selection.app in by_app:
            raise ScreenReachError(
                "duplicate_selected_app",
                f"app {selection.app!r} is selected by rows "
                f"{by_app[selection.app]} and {selection.selection_index}",
            )
        by_app[selection.app] = selection.selection_index
        path_key = str(selection.run_path)
        if path_key in by_path:
            raise ScreenReachError(
                "duplicate_selected_run",
                f"run path {path_key!r} is selected by rows "
                f"{by_path[path_key]} and {selection.selection_index}",
            )
        by_path[path_key] = selection.selection_index
        if selection.identity_bound:
            identity_key = tuple(
                selection.expected_identity.get(name)
                for name in (
                    "run_id",
                    "prepared_bundle_sha256",
                    "universe_sha256",
                    "route_discovery_sha256",
                    "actions_terminal_hash",
                )
            )
            if identity_key in by_identity:
                raise ScreenReachError(
                    "duplicate_selected_identity",
                    "one execution identity is selected by rows "
                    f"{by_identity[identity_key]} and {selection.selection_index}",
                )
            by_identity[identity_key] = selection.selection_index


def load_selections(
    path: Path, *, base_dir: Path | None = None, strict: bool = True
) -> tuple[SelectedRun, ...]:
    """Load an identity-bound selection document, rejecting ambiguity."""

    source_path = path.expanduser().resolve(strict=False)
    document = _read_json_strict(source_path)
    if type(document) is list:
        rows = document
    elif type(document) is dict:
        rows = document.get("selections")
        if type(rows) is not list:
            raise ScreenReachError(
                "selection_document_invalid",
                "selection object must contain a selections array",
            )
    else:
        raise ScreenReachError(
            "selection_document_invalid",
            "selection document must be an array or an object with selections",
        )
    if not rows:
        raise ScreenReachError(
            "selection_empty", "selection document contains no rows"
        )
    root = (
        base_dir.expanduser().resolve(strict=False)
        if base_dir is not None
        else source_path.parent
    )
    selections = tuple(
        _selection_row(
            row,
            index=index,
            source=str(source_path),
            base_dir=root,
            strict=strict,
        )
        for index, row in enumerate(rows, 1)
    )
    _reject_duplicate_selections(selections)
    return selections


def _snapshot_cli_identity(run_path: Path) -> dict[str, object]:
    """Pin identity fields before validating/deriving an explicit path selection."""

    manifest_value = _read_json_strict(run_path / "run.json")
    if type(manifest_value) is not dict:
        raise ScreenReachError(
            "selection_identity_invalid", "selected run.json must be an object"
        )
    route_value = _read_json_strict(run_path / "route-discovery.json")
    try:
        route = route_discovery_from_mapping(route_value)
    except (TypeError, ValueError) as error:
        raise ScreenReachError(
            "selection_identity_invalid",
            f"selected route-discovery.json is invalid: {error}",
        ) from error
    actual = _actual_identity(manifest_value, route.discovery_sha256)
    expected = {
        name: actual[name]
        for name in (
            "run_id",
            "prepared_bundle_sha256",
            "universe_sha256",
            "route_discovery_sha256",
            "actions_count",
            "actions_terminal_hash",
        )
    }
    expected["run_id"] = _nonempty_string(expected["run_id"], "run_id")
    for name in (
        "prepared_bundle_sha256",
        "universe_sha256",
        "route_discovery_sha256",
        "actions_terminal_hash",
    ):
        expected[name] = _digest(expected[name], name)
    return dict(sorted(expected.items()))


def selections_from_run_arguments(values: Sequence[str]) -> tuple[SelectedRun, ...]:
    """Bind repeated ``APP=RUN_PATH`` values to an immediate identity snapshot."""

    selections: list[SelectedRun] = []
    for index, value in enumerate(values, 1):
        if "=" not in value:
            raise ScreenReachError(
                "run_argument_invalid",
                f"--run value {value!r} must use APP=RUN_PATH",
            )
        app, raw_path = value.split("=", 1)
        app = _nonempty_string(app, "--run app")
        raw_path = _nonempty_string(raw_path, "--run path")
        run_path = Path(raw_path).expanduser().resolve(strict=False)
        selections.append(
            SelectedRun(
                selection_index=index,
                app=app,
                run_path=run_path,
                source="command_line_run_path_identity_snapshot",
                expected_identity=_snapshot_cli_identity(run_path),
                expected_coverage={},
                metadata={},
                identity_bound=True,
            )
        )
    _reject_duplicate_selections(selections)
    return tuple(selections)


def correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Population Pearson coefficient, or ``None`` when mathematically undefined."""

    if len(xs) != len(ys):
        raise ValueError("correlation inputs must have equal length")
    if len(xs) < 3:
        return None
    values_x = [float(value) for value in xs]
    values_y = [float(value) for value in ys]
    if not all(math.isfinite(value) for value in values_x + values_y):
        raise ValueError("correlation inputs must be finite")
    mean_x, mean_y = st.fmean(values_x), st.fmean(values_y)
    variance_x = st.fmean((value - mean_x) ** 2 for value in values_x)
    variance_y = st.fmean((value - mean_y) ** 2 for value in values_y)
    if variance_x == 0.0 or variance_y == 0.0:
        return None
    covariance = st.fmean(
        (x - mean_x) * (y - mean_y)
        for x, y in zip(values_x, values_y)
    )
    return covariance / math.sqrt(variance_x * variance_y)


def _validated_finished_store(run: Path) -> RunStore:
    """Open a fully valid finished run for strict compatibility helpers."""

    root = run.expanduser().resolve(strict=False)
    validation = validate_run(root)
    if not validation.ok:
        detail = "; ".join(
            f"{item.code}: {item.message}" for item in validation.errors
        )
        raise ScreenReachError(
            "run_invalid", f"refusing invalid run evidence at {root}: {detail}"
        )
    store = RunStore.open(root)
    if store.manifest().get("status") != "finished":
        raise ScreenReachError(
            "run_not_finished", f"selected run at {root} is not finished"
        )
    return store


def declared_and_package(run: Path) -> tuple[tuple[str, ...], str]:
    """Strict form of the historical helper, returning canonical declarations.

    Missing or invalid evidence now raises ``ScreenReachError`` instead of
    returning ``None`` and being mistaken for a zero-reach app.
    """

    store = _validated_finished_store(run)
    manifest = store.manifest()
    package = str(manifest["package_name"])
    discovery = route_discovery_from_mapping(read_json(store.route_discovery_path))
    declared = tuple(
        sorted(
            {
                canonicalize_activity_component(package, activity)
                for activity in discovery.declared_activities
            }
        )
    )
    if not declared:
        raise ScreenReachError(
            "declared_activities_empty", "selected run declares no activities"
        )
    return declared, package


def entered(run: Path, package: str) -> set[str]:
    """Strict form of the historical helper, returning canonical observations."""

    store = _validated_finished_store(run)
    if store.manifest().get("package_name") != package:
        raise ScreenReachError(
            "package_identity_mismatch",
            "requested package differs from the selected run",
        )
    entries = store.ledger("actions").entries()
    expected = store.manifest()["evidence_heads"]["actions"]
    derived_head = entries[-1].entry_hash if entries else ZERO_HASH
    if len(entries) != expected["count"] or derived_head != expected["terminal_hash"]:
        raise ScreenReachError(
            "actions_evidence_changed",
            "actions consumed for reach differ from the selected terminal head",
        )
    observations: list[ActivityObservation] = []
    incomplete = 0
    for entry in entries:
        details = entry.payload["attempt"]["outcome"]["details"]
        if not (
            "before_activity" in details and "after_activity" in details
        ):
            incomplete += 1
        observations.extend(activity_observations_from_details(package, details))
    if incomplete:
        raise ScreenReachError(
            "activity_observation_incomplete",
            f"{incomplete} action attempts lack a complete activity pair",
        )
    malformed = [
        item
        for item in observations
        if item.issue in {"malformed_component", "non_string_activity"}
    ]
    if malformed:
        raise ScreenReachError(
            "malformed_activity_observation",
            f"{len(malformed)} retained activity observations are malformed",
        )
    well_formed = [
        item
        for item in observations
        if item.canonical is not None or item.issue == "foreign_package"
    ]
    if not well_formed:
        raise ScreenReachError(
            "activity_observations_unavailable",
            "the action ledger contains no well-formed activity observation",
        )
    return {
        item.canonical for item in observations if item.canonical is not None
    }


def _correlation_report(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    pairs = [
        (
            float(row["activity_reach_fraction"]),
            float(row["observed_coverage_percent"]),
        )
        for row in rows
        if row.get("result") == "included"
    ]
    result: dict[str, object] = {
        "method": "pearson_population",
        "paired_run_count": len(pairs),
        "coefficient": None,
        "status": "undefined",
        "reason": None,
    }
    if len(pairs) < 3:
        result["reason"] = "insufficient_pairs"
        return result
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    if len(set(xs)) == 1 and len(set(ys)) == 1:
        result["reason"] = "constant_reach_and_coverage"
        return result
    if len(set(xs)) == 1:
        result["reason"] = "constant_reach"
        return result
    if len(set(ys)) == 1:
        result["reason"] = "constant_coverage"
        return result
    coefficient = correlation(xs, ys)
    if coefficient is None:
        result["reason"] = "zero_numeric_variance"
        return result
    result["coefficient"] = coefficient
    result["status"] = "defined"
    return result


def _base_row(selection: SelectedRun) -> dict[str, object]:
    return {
        "app": selection.app,
        "selection_index": selection.selection_index,
        "result": "rejected",
        "selected_run_path": str(selection.run_path),
        "selection": {
            "source": selection.source,
            "identity_bound": selection.identity_bound,
            "expected_identity": dict(selection.expected_identity),
            "expected_coverage": dict(selection.expected_coverage),
            "metadata": dict(selection.metadata),
        },
        "run_identity": None,
        "provenance": {
            "selection_rule": SELECTION_RULE,
            "validation_rule": VALIDATION_RULE,
            "activity_reach_rule": ACTIVITY_REACH_RULE,
            "coverage_rule": COVERAGE_RULE,
        },
        "issues": [],
    }


def _actual_identity(
    manifest: Mapping[str, object], route_hash: str
) -> dict[str, object]:
    heads = manifest.get("evidence_heads")
    actions = heads.get("actions") if type(heads) is dict else None
    if type(actions) is not dict:
        raise ScreenReachError(
            "actions_identity_missing",
            "finished run.json lacks an actions evidence head",
        )
    count = actions.get("count")
    terminal_hash = actions.get("terminal_hash")
    if type(count) is not int or count < 0:
        raise ScreenReachError(
            "actions_identity_invalid", "run.json actions count is invalid"
        )
    normalized_heads: dict[str, dict[str, object]] = {}
    for name in sorted(heads):
        head = heads[name]
        if type(name) is not str or type(head) is not dict:
            raise ScreenReachError(
                "evidence_identity_invalid",
                "run.json evidence heads are malformed",
            )
        normalized_heads[name] = dict(head)
    return {
        "run_id": manifest.get("run_id"),
        "run_path": None,
        "status": manifest.get("status"),
        "prepared_bundle_sha256": manifest.get("prepared_bundle_sha256"),
        "universe_sha256": manifest.get("universe_sha256"),
        "route_discovery_sha256": route_hash,
        "actions_count": count,
        "actions_terminal_hash": terminal_hash,
        "evidence_heads": normalized_heads,
    }


def _identity_issues(
    expected: Mapping[str, object], actual: Mapping[str, object]
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for name, expected_value in expected.items():
        actual_value = actual.get(name)
        if actual_value != expected_value:
            issues.append(
                _issue(
                    f"selection_{name}_mismatch",
                    f"selected {name}={expected_value!r}, run evidence has {actual_value!r}",
                )
            )
    return issues


def _coverage_issues(
    expected: Mapping[str, object], actual: Mapping[str, object]
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for name, expected_value in expected.items():
        if name == "validation_ok":
            if expected_value is not True:
                issues.append(
                    _issue(
                        "selection_validation_incompatible",
                        "selection row does not attest validation_ok=true",
                    )
                )
            continue
        if name == "status":
            if expected_value != "finished":
                issues.append(
                    _issue(
                        "selection_status_incompatible",
                        f"selection row status is {expected_value!r}, not 'finished'",
                    )
                )
            continue
        actual_value = actual.get(name)
        matches = (
            math.isclose(
                float(expected_value),
                float(actual_value),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            if name == "observed_coverage_percent" and actual_value is not None
            else expected_value == actual_value
        )
        if not matches:
            issues.append(
                _issue(
                    f"selection_{name}_mismatch",
                    f"selected {name}={expected_value!r}, run evidence has {actual_value!r}",
                )
            )
    return issues


def _observation_dicts(
    observations: Iterable[ActivityObservation], *, issue: str | None = None
) -> list[dict[str, object]]:
    unique = {
        (
            observation.source_key,
            observation.raw,
            observation.canonical,
            observation.issue,
            observation.value_type,
        ): observation
        for observation in observations
        if issue is None or observation.issue == issue
    }
    return [unique[key].to_dict() for key in sorted(unique, key=lambda item: tuple("" if value is None else str(value) for value in item))]


def analyze_selected_run(
    selection: SelectedRun, *, strict: bool = True
) -> dict[str, object]:
    """Derive reach and coverage from one exact run, or return a rejected row."""

    row = _base_row(selection)
    issues: list[dict[str, str]] = []
    validation = validate_run(selection.run_path)
    issues.extend(
        _issue(
            f"run_validation_{item.code}",
            item.message,
            severity=item.severity,
        )
        for item in validation.issues
    )
    if not validation.ok:
        row["issues"] = _sorted_issues(issues)
        return row

    try:
        store = RunStore.open(selection.run_path)
        manifest = store.manifest()
        if manifest.get("status") != "finished":
            raise ScreenReachError(
                "run_not_finished",
                f"selected run status is {manifest.get('status')!r}, not 'finished'",
            )
        route = route_discovery_from_mapping(read_json(store.route_discovery_path))
        route_hash = route.discovery_sha256
        actual_identity = _actual_identity(manifest, route_hash)
        actual_identity["run_path"] = str(selection.run_path)
        row["run_identity"] = actual_identity

        issues.extend(_identity_issues(selection.expected_identity, actual_identity))
        coverage = {
            "observed_covered_units": manifest.get("covered_units"),
            "total_units": manifest.get("total_units"),
            "observed_coverage_percent": manifest.get("observed_coverage_percent"),
        }
        issues.extend(_coverage_issues(selection.expected_coverage, coverage))

        package = manifest.get("package_name")
        if type(package) is not str or not package:
            raise ScreenReachError(
                "package_identity_missing", "validated run has no package identity"
            )
        prepared = verify_prepared_snapshot(
            read_json(store.prepared_manifest_path), store.universe()
        )
        if prepared.bundle_sha256 != manifest.get("prepared_bundle_sha256"):
            raise ScreenReachError(
                "prepared_evidence_changed",
                "prepared.json no longer matches the selected run identity",
            )
        if prepared.package_name != package or route.package_name != package:
            raise ScreenReachError(
                "package_identity_mismatch",
                "prepared or route evidence differs from the selected package",
            )
        runtime = manifest.get("runtime_configuration")
        if type(runtime) is not dict or runtime.get("route_discovery_sha256") != route_hash:
            raise ScreenReachError(
                "route_evidence_changed",
                "route-discovery.json no longer matches the selected run identity",
            )

        declared_map: list[dict[str, str]] = []
        declared: set[str] = set()
        for raw in route.declared_activities:
            try:
                canonical = canonicalize_activity_component(package, raw)
            except ValueError as error:
                raise ScreenReachError(
                    "declared_activity_invalid",
                    f"route discovery contains invalid activity {raw!r}: {error}",
                ) from error
            declared.add(canonical)
            declared_map.append({"raw": raw, "canonical": canonical})
        if not declared:
            raise ScreenReachError(
                "declared_activities_empty",
                "activity reach is undefined because no activities are declared",
            )

        action_entries = store.ledger("actions").entries()
        derived_actions_count = len(action_entries)
        derived_actions_head = (
            action_entries[-1].entry_hash if action_entries else ZERO_HASH
        )
        if (
            derived_actions_count != actual_identity["actions_count"]
            or derived_actions_head != actual_identity["actions_terminal_hash"]
        ):
            raise ScreenReachError(
                "actions_evidence_changed",
                "actions consumed for reach differ from the selected terminal head",
            )

        observations: list[ActivityObservation] = []
        complete_transitions = 0
        partial_transitions = 0
        legacy_attempts = 0
        missing_attempts = 0
        for entry in action_entries:
            attempt = entry.payload["attempt"]
            details = attempt["outcome"]["details"]
            has_before = "before_activity" in details
            has_after = "after_activity" in details
            if has_before and has_after:
                complete_transitions += 1
            elif has_before or has_after:
                partial_transitions += 1
            elif "activity" in details:
                legacy_attempts += 1
            else:
                missing_attempts += 1
            observations.extend(
                activity_observations_from_details(package, details)
            )
        malformed = [
            item
            for item in observations
            if item.issue in {"malformed_component", "non_string_activity"}
        ]
        foreign = [item for item in observations if item.issue == "foreign_package"]
        well_formed = [
            item
            for item in observations
            if item.canonical is not None or item.issue == "foreign_package"
        ]
        incomplete_count = partial_transitions + legacy_attempts + missing_attempts
        if incomplete_count:
            issues.append(
                _issue(
                    "activity_observation_incomplete",
                    f"{incomplete_count} action attempts lack a complete before/after activity pair",
                    severity="error" if strict else "warning",
                )
            )
        if legacy_attempts and not strict:
            issues.append(
                _issue(
                    "legacy_activity_observation",
                    f"{legacy_attempts} action attempts use the historical activity field",
                    severity="warning",
                )
            )
        if not well_formed:
            issues.append(
                _issue(
                    "activity_observations_unavailable",
                    "the action ledger contains no well-formed activity observation",
                )
            )
        if malformed:
            issues.append(
                _issue(
                    "malformed_activity_observation",
                    f"{len(malformed)} retained activity observations are malformed",
                    severity="error" if strict else "warning",
                )
            )
        if foreign:
            issues.append(
                _issue(
                    "foreign_activity_observed",
                    f"{len(foreign)} retained observations belong to another package",
                    severity="info",
                )
            )

        observed = {
            item.canonical
            for item in observations
            if item.canonical is not None
        }
        entered_declared = declared & observed
        undeclared = observed - declared

        row["provenance"] = {
            **row["provenance"],
            "run_manifest": str(store.manifest_path),
            "prepared_manifest": str(store.prepared_manifest_path),
            "route_discovery": {
                "path": str(store.route_discovery_path),
                "discovery_sha256": route_hash,
                "apk_sha256": route.apk_sha256,
            },
            "actions": {
                "path": str(store.root / store.LEDGER_FILES["actions"]),
                "count": derived_actions_count,
                "terminal_hash": derived_actions_head,
            },
            "activity_observations": {
                "complete_transition_attempts": complete_transitions,
                "partial_transition_attempts": partial_transitions,
                "legacy_activity_attempts": legacy_attempts,
                "missing_activity_attempts": missing_attempts,
                "well_formed_values": len(well_formed),
            },
            "coverage": {
                "source": "run.json after full independent replay validation",
                "covered_field": "covered_units",
                "total_field": "total_units",
                "percent_field": "observed_coverage_percent",
            },
        }
        measurement = {
            "package_name": package,
            "raw_declared_activities": list(route.declared_activities),
            "declared_activity_spellings": sorted(
                declared_map, key=lambda item: (item["canonical"], item["raw"])
            ),
            "declared_activities": sorted(declared),
            "observed_activity_spellings": _observation_dicts(observations),
            "observed_app_activities": sorted(observed),
            "entered_declared_activities": sorted(entered_declared),
            "observed_undeclared_activities": sorted(undeclared),
            "foreign_activity_observations": _observation_dicts(
                observations, issue="foreign_package"
            ),
            "malformed_activity_observations": _observation_dicts(malformed),
            "declared_activity_count": len(declared),
            "observed_app_activity_count": len(observed),
            "entered_declared_activity_count": len(entered_declared),
            "observed_undeclared_activity_count": len(undeclared),
            "activity_reach_fraction": len(entered_declared) / len(declared),
            "activity_reach_percent": 100.0 * len(entered_declared) / len(declared),
            **coverage,
        }

        # Revalidate after consuming the artifacts and require the immutable
        # manifest/route/prepared snapshots to still be exactly those used for
        # the row.  This closes the validation-to-derivation race without
        # rewriting or locking retained evidence.
        final_validation = validate_run(selection.run_path)
        if not final_validation.ok:
            detail = "; ".join(
                f"{item.code}: {item.message}" for item in final_validation.errors
            )
            raise ScreenReachError(
                "run_evidence_changed",
                f"run evidence changed during analysis: {detail}",
            )
        if store.manifest() != manifest:
            raise ScreenReachError(
                "run_manifest_changed",
                "run.json changed during screen-reach analysis",
            )
        final_route = route_discovery_from_mapping(
            read_json(store.route_discovery_path)
        )
        if final_route.to_dict() != route.to_dict():
            raise ScreenReachError(
                "route_evidence_changed",
                "route-discovery.json changed during screen-reach analysis",
            )
        final_prepared = verify_prepared_snapshot(
            read_json(store.prepared_manifest_path), store.universe()
        )
        if final_prepared.to_dict() != prepared.to_dict():
            raise ScreenReachError(
                "prepared_evidence_changed",
                "prepared.json changed during screen-reach analysis",
            )
        if not any(item["severity"] == "error" for item in issues):
            row.update(measurement)
    except ScreenReachError as error:
        issues.append(_issue(error.code, str(error)))
    except (KeyError, OSError, TypeError, ValueError) as error:
        issues.append(_issue("reach_evidence_invalid", str(error)))

    if any(item["severity"] == "error" for item in issues):
        row["issues"] = _sorted_issues(issues)
        return row
    row["result"] = "included"
    row["issues"] = _sorted_issues(issues)
    return row


def build_report(
    selections: Sequence[SelectedRun], *, strict: bool = True
) -> dict[str, object]:
    """Build one deterministic report over explicit selections."""

    _reject_duplicate_selections(selections)
    if strict:
        unbound = [item for item in selections if not item.identity_bound]
        if unbound:
            indexes = ", ".join(str(item.selection_index) for item in unbound)
            raise ScreenReachError(
                "selection_identity_missing",
                f"strict reporting requires identity hashes for selection rows: {indexes}",
            )
    rows = [
        analyze_selected_run(selection, strict=strict)
        for selection in sorted(
            selections, key=lambda item: (item.app, str(item.run_path), item.selection_index)
        )
    ]
    included = sum(row["result"] == "included" for row in rows)
    rejected = len(rows) - included
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "strict": strict,
        "selection_rule": SELECTION_RULE,
        "activity_reach_rule": ACTIVITY_REACH_RULE,
        "coverage_rule": COVERAGE_RULE,
        "validation_rule": VALIDATION_RULE,
        "selected_row_count": len(rows),
        "included_row_count": included,
        "rejected_row_count": rejected,
        "correlation": _correlation_report(rows),
        "issues": [],
        "rows": rows,
    }


def _error_report(error: ScreenReachError, *, strict: bool) -> dict[str, object]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "strict": strict,
        "selection_rule": SELECTION_RULE,
        "activity_reach_rule": ACTIVITY_REACH_RULE,
        "coverage_rule": COVERAGE_RULE,
        "validation_rule": VALIDATION_RULE,
        "selected_row_count": 0,
        "included_row_count": 0,
        "rejected_row_count": 0,
        "correlation": {
            "method": "pearson_population",
            "paired_run_count": 0,
            "coefficient": None,
            "status": "undefined",
            "reason": "no_valid_selections",
        },
        "issues": [_issue(error.code, str(error))],
        "rows": [],
    }


def _render_table(report: Mapping[str, object]) -> str:
    lines = [
        f"{'app':24s}{'declared':>10}{'entered':>9}{'share':>9}{'coverage':>11}{'result':>11}",
        "-" * 74,
    ]
    for raw in report.get("rows", []):
        row = raw if isinstance(raw, Mapping) else {}
        if row.get("result") == "included":
            lines.append(
                f"{str(row.get('app', ''))[:24]:24s}"
                f"{int(row['declared_activity_count']):>10}"
                f"{int(row['entered_declared_activity_count']):>9}"
                f"{float(row['activity_reach_percent']):>8.1f}%"
                f"{float(row['observed_coverage_percent']):>10.2f}%"
                f"{'included':>11}"
            )
        else:
            codes = ",".join(
                str(issue.get("code"))
                for issue in row.get("issues", [])
                if isinstance(issue, Mapping) and issue.get("severity") == "error"
            )
            lines.append(
                f"{str(row.get('app', ''))[:24]:24s}"
                f"{'-':>10}{'-':>9}{'-':>9}{'-':>11}{'rejected':>11} {codes}"
            )
    correlation_value = report.get("correlation")
    correlation_data = correlation_value if isinstance(correlation_value, Mapping) else {}
    if correlation_data.get("status") == "defined":
        lines.append(
            "correlation: "
            f"{float(correlation_data['coefficient']):+.6f} "
            f"(n={correlation_data['paired_run_count']})"
        )
    else:
        lines.append(
            "correlation: undefined "
            f"({correlation_data.get('reason')}, n={correlation_data.get('paired_run_count', 0)})"
        )
    for issue in report.get("issues", []):
        if isinstance(issue, Mapping):
            lines.append(
                f"{issue.get('severity', 'error')}: "
                f"{issue.get('code', 'unknown')}: {issue.get('message', '')}"
            )
    return "\n".join(lines) + "\n"


def _within_wave_selection(
    selections: Sequence[SelectedRun], campaign_root: Path, pattern: str
) -> None:
    waves = [path.resolve(strict=False) for path in sorted(campaign_root.glob(pattern))]
    if not waves:
        raise ScreenReachError(
            "legacy_wave_not_found",
            f"campaign root {campaign_root} has no waves matching {pattern!r}",
        )
    for selection in selections:
        if not any(
            selection.run_path == wave
            or wave in selection.run_path.parents
            for wave in waves
        ):
            raise ScreenReachError(
                "selection_outside_wave",
                f"selected run {selection.run_path} is outside waves matching {pattern!r}",
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_pattern", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("legacy_selection", nargs="?", help=argparse.SUPPRESS)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--selection",
        type=Path,
        help="JSON selections with exact run path, IDs, and evidence hashes",
    )
    group.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="APP=RUN_PATH",
        help="select one exact run path directly; repeat for multiple apps",
    )
    parser.add_argument(
        "--selection-base",
        type=Path,
        help="base for relative run paths (default: selection file directory)",
    )
    parser.add_argument(
        "--campaign-root",
        type=Path,
        help="explicit root required only by the historical positional form",
    )
    parser.add_argument("--output", type=Path, help="write output to this path")
    parser.add_argument(
        "--format", choices=("json", "table"), default="json"
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help=(
            "allow a selection file to omit external identity attestations; "
            "run evidence is still fully validated and mismatches still reject"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    strict = not arguments.no_strict
    report: dict[str, object]
    try:
        if arguments.legacy_pattern is not None:
            if arguments.selection is not None or arguments.run:
                raise ScreenReachError(
                    "selection_arguments_ambiguous",
                    "positional selection cannot be combined with --selection or --run",
                )
            if arguments.legacy_selection is None:
                raise ScreenReachError(
                    "selection_missing",
                    "the positional wave form requires an identity-rich selection file",
                )
            if arguments.campaign_root is None:
                raise ScreenReachError(
                    "campaign_root_missing",
                    "the positional wave form requires --campaign-root; no host path is assumed",
                )
            campaign_root = arguments.campaign_root.expanduser().resolve(strict=False)
            selection_path = Path(arguments.legacy_selection).expanduser()
            if not selection_path.is_absolute():
                selection_path = campaign_root / selection_path
            selections = load_selections(
                selection_path,
                base_dir=arguments.selection_base,
                strict=strict,
            )
            _within_wave_selection(
                selections, campaign_root, arguments.legacy_pattern
            )
        elif arguments.selection is not None:
            selections = load_selections(
                arguments.selection,
                base_dir=arguments.selection_base,
                strict=strict,
            )
        elif arguments.run:
            selections = selections_from_run_arguments(arguments.run)
        else:
            raise ScreenReachError(
                "selection_missing", "provide --selection or at least one --run APP=RUN_PATH"
            )
        report = build_report(selections, strict=strict)
    except ScreenReachError as error:
        report = _error_report(error, strict=strict)

    rendered = (
        json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        if arguments.format == "json"
        else _render_table(report)
    )
    if arguments.output is None:
        sys.stdout.write(rendered)
    else:
        try:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(rendered, encoding="utf-8")
        except OSError as error:
            sys.stderr.write(f"cannot write {arguments.output}: {error}\n")
            return 2
    return 1 if report["issues"] or report["rejected_row_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
