#!/usr/bin/env python3
"""Where a run's wall clock goes, derived from retained evidence only.

Gap 8 asked for the loop to be instrumented before tuning
`post_action_delay_seconds`. Instrumenting the hot loop is not necessary and not
free -- the last thing added to that path, a logcat probe, cost measured coverage
-- and everything needed is already recorded:

  model selection   model_calls.jsonl started_at/ended_at
  dispatch          attempt.execution started_at/ended_at
  settle            attempt.window_ended_at - execution.ended_at
  everything else   the gap between one attempt's window closing and the next
                    one starting: the dump, the parse, candidate ranking, and adb
                    round trips

Model calls overlap the gap rather than adding to it, because selection happens
between attempts; the residual is reported with model time subtracted so the two
do not double count.

Usage: throughput_breakdown.py <run-directory> [more run directories]
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path


def opener(path: Path):
    if path.is_file():
        return open(path, "rt", encoding="utf-8", errors="replace")
    gz = Path(str(path) + ".gz")
    if gz.is_file():
        return gzip.open(gz, "rt", encoding="utf-8", errors="replace")
    return None


def records(run: Path, name: str, key: str) -> list[dict]:
    stream = opener(run / name)
    if stream is None:
        return []
    out = []
    with stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)["payload"]
            except Exception:  # noqa: BLE001
                continue
            item = payload.get(key)
            if isinstance(item, dict):
                out.append(item)
    return out


def analyse(run: Path) -> dict | None:
    attempts = records(run, "actions.jsonl", "attempt")
    if not attempts:
        return None
    calls = records(run, "model_calls.jsonl", "call")
    attempts.sort(key=lambda a: a["sequence"])

    dispatch = sum(
        max(0.0, a["execution"]["ended_at"] - a["execution"]["started_at"])
        for a in attempts
    )
    settle = sum(
        max(0.0, a["window_ended_at"] - a["execution"]["ended_at"]) for a in attempts
    )
    model = sum(
        max(0.0, c["ended_at"] - c["started_at"])
        for c in calls
        if isinstance(c.get("started_at"), (int, float))
        and isinstance(c.get("ended_at"), (int, float))
    )
    between = 0.0
    for previous, current in zip(attempts, attempts[1:]):
        between += max(
            0.0, current["execution"]["started_at"] - previous["window_ended_at"]
        )
    span = attempts[-1]["window_ended_at"] - attempts[0]["execution"]["started_at"]
    # Model selection happens inside the between-attempts gap, so it is removed
    # from the residual rather than added alongside it.
    observe = max(0.0, between - model)
    return {
        "app": run.name,
        "attempts": len(attempts),
        "span_minutes": span / 60.0,
        "dispatch": dispatch,
        "settle": settle,
        "model": model,
        "observe_and_rank": observe,
        "model_calls": len(calls),
        "actions_per_minute": len(attempts) / max(span / 60.0, 1e-9),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip().splitlines()[-1])
        return 2
    rows = [analyse(Path(argument)) for argument in sys.argv[1:]]
    rows = [row for row in rows if row]
    if not rows:
        print("no attempts found in the given runs")
        return 1
    header = (
        f"{'run':26s}{'acts':>6}{'min':>7}{'a/min':>7}"
        f"{'dispatch':>10}{'settle':>9}{'model':>8}{'observe+rank':>14}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['app'][:26]:26s}{row['attempts']:>6}{row['span_minutes']:>7.1f}"
            f"{row['actions_per_minute']:>7.1f}"
            f"{row['dispatch']:>9.0f}s{row['settle']:>8.0f}s"
            f"{row['model']:>7.0f}s{row['observe_and_rank']:>13.0f}s"
        )
    print()
    print("share of the measured span:")
    for row in rows:
        total = max(
            row["dispatch"] + row["settle"] + row["model"] + row["observe_and_rank"],
            1e-9,
        )
        print(
            f"  {row['app'][:26]:26s} dispatch {100 * row['dispatch'] / total:5.1f}%"
            f"  settle {100 * row['settle'] / total:5.1f}%"
            f"  model {100 * row['model'] / total:5.1f}%"
            f"  observe+rank {100 * row['observe_and_rank'] / total:5.1f}%"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
