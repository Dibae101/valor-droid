#!/usr/bin/env python3
"""Turn a coverage-experiment result file into a table and paired comparisons.

Only a run that finished *and* passed independent validation contributes a
percentage. An aborted or invalid run is unavailable, never 0%, which is the
gap-5 rule applied to the experiment itself.

A per-app difference is reported only when both arms produced a valid run over
the same denominator, so paired numbers are comparable by construction.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def usable(record: dict | None) -> bool:
    return bool(
        record
        and record.get("status") == "finished"
        and record.get("validation_ok")
        and isinstance(record.get("observed_coverage_percent"), (int, float))
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--baseline", default="baseline")
    parser.add_argument("--markdown", type=Path)
    arguments = parser.parse_args()

    document = json.loads(arguments.results.read_text())
    apps = document["apps"]
    arms = list(document.get("arms") or [])
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    models = document.get("models") or {}
    emit(
        f"budget {document.get('max_seconds', 0):.0f}s per run, "
        f"max {document.get('max_actions')} actions"
    )
    emit(f"arms: {', '.join(arms)}")
    for arm, name in sorted(models.items()):
        if arm in arms:
            emit(f"  {arm}: {name}")
    emit()

    header = ["app", "units", "policy"] + [f"{arm} %" for arm in arms] + ["attempts", "model calls"]
    emit("| " + " | ".join(header) + " |")
    emit("|" + "|".join("---" for _ in header) + "|")

    prepared = 0
    per_arm: dict[str, list[float]] = {arm: [] for arm in arms}
    paired: dict[str, list[tuple[str, float, float]]] = {
        arm: [] for arm in arms if arm != arguments.baseline
    }
    at_or_above_40: dict[str, int] = {arm: 0 for arm in arms}

    for name, app in sorted(apps.items()):
        if not app.get("prepared"):
            reason = (app.get("prepare_error") or "instrumentation failed")[:60]
            emit(f"| {name} | — | — | " + " | ".join("—" for _ in arms) + f" | — | {reason} |")
            continue
        prepared += 1
        policy = (app.get("inclusion_policy") or "?").replace("-methods-v1", "")
        cells = [name, str(app.get("units")), policy]
        attempts: list[str] = []
        calls: list[str] = []
        values: dict[str, float | None] = {}
        for arm in arms:
            record = (app.get("runs") or {}).get(arm)
            if usable(record):
                percent = float(record["observed_coverage_percent"])
                values[arm] = percent
                per_arm[arm].append(percent)
                if percent >= 40.0:
                    at_or_above_40[arm] += 1
                cells.append(f"{percent:.2f}")
            else:
                values[arm] = None
                status = (record or {}).get("status") or "missing"
                reason = (record or {}).get("stop_reason") or (record or {}).get("error") or "no run"
                cells.append(f"n/a ({status if status != 'finished' else reason})"[:26])
            attempts.append(str((record or {}).get("attempts", "—")))
            calls.append(str((record or {}).get("model_calls", "—")))
        cells.append("/".join(attempts))
        cells.append("/".join(calls))
        emit("| " + " | ".join(cells) + " |")
        base = values.get(arguments.baseline)
        for arm in paired:
            other = values.get(arm)
            if base is not None and other is not None:
                paired[arm].append((name, base, other))

    emit()
    emit(f"apps instrumented: {prepared}/{len(apps)}")
    emit()
    emit("| arm | valid runs | median % | mean % | min % | max % | runs >= 40% |")
    emit("|---|---|---|---|---|---|---|")
    for arm in arms:
        values = per_arm[arm]
        if values:
            emit(
                f"| {arm} | {len(values)} | {statistics.median(values):.2f} | "
                f"{statistics.fmean(values):.2f} | {min(values):.2f} | {max(values):.2f} | "
                f"{at_or_above_40[arm]}/{len(values)} |"
            )
        else:
            emit(f"| {arm} | 0 | — | — | — | — | 0 |")

    for arm, rows in paired.items():
        emit()
        emit(f"### {arm} vs {arguments.baseline}, paired on the same app and denominator")
        if not rows:
            emit("No app produced a valid run in both arms, so no comparison is possible.")
            continue
        emit("| app | baseline % | treatment % | difference |")
        emit("|---|---|---|---|")
        differences = []
        for name, base, other in rows:
            differences.append(other - base)
            emit(f"| {name} | {base:.2f} | {other:.2f} | {other - base:+.2f} |")
        wins = sum(1 for value in differences if value > 0)
        losses = sum(1 for value in differences if value < 0)
        ties = sum(1 for value in differences if value == 0)
        emit()
        emit(
            f"pairs: {len(differences)} | higher: {wins} | lower: {losses} | equal: {ties} | "
            f"median difference: {statistics.median(differences):+.2f} points | "
            f"mean: {statistics.fmean(differences):+.2f} points"
        )
        emit(
            "This is a descriptive paired difference over one run per cell. It is not a "
            "significance test and does not establish a coverage gain."
        )

    if arguments.markdown:
        arguments.markdown.parent.mkdir(parents=True, exist_ok=True)
        arguments.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
