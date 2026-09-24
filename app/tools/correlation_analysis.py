#!/usr/bin/env python3
"""Test whether the waste metrics a gap analysis names actually drive coverage.

Written after two campaigns each fixed the waste their predecessor's logs named and
each moved the mean under a point. It answers two questions from retained evidence:

  1. did the metrics move between campaigns
  2. does moving them correlate, per app, with moving coverage

The answer for VALOR-Droid v1 -> v2 was that Back presses, throughput, stall volume
and action count have no useful correlation with coverage, while zero-gain share
does. See WHY-THE-MEAN-DID-NOT-MOVE.md in the v2 results folder.

Usage: correlation_analysis.py <before-sweep.json> <after-sweep.json>
"""
from __future__ import annotations

import json
import statistics as st
import sys

METRICS = (
    ("zero-gain % of actions", "zero_gain_percent"),
    ("no-effect % of actions", "no_effect_percent"),
    ("Back % of actions", "back_percent"),
    ("actions per minute", "actions_per_min"),
    ("in-app % of actions", "in_app_percent"),
    ("budget used %", "used_budget_percent"),
    ("actions taken", "attempts"),
)


def correlation(xs, ys) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = st.mean(xs), st.mean(ys)
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    if sx == 0 or sy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n * sx * sy)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[-1])
        return 2
    before = {r["app"]: r for r in json.load(open(sys.argv[1]))}
    after = {r["app"]: r for r in json.load(open(sys.argv[2]))}
    apps = sorted(set(before) & set(after))
    if not apps:
        print("no apps in common")
        return 1
    print(f"{len(apps)} apps in both\n")
    print("did the metrics move?")
    for label, key in METRICS:
        a = st.mean(before[x][key] for x in apps)
        b = st.mean(after[x][key] for x in apps)
        print(f"  {label:26s} {a:8.2f} -> {b:8.2f}   {b - a:+8.2f}")

    delta_coverage = [after[a]["percent"] - before[a]["percent"] for a in apps]
    print("\ndid moving them move coverage? (per-app correlation)")
    rows = []
    for label, key in METRICS:
        deltas = [after[a][key] - before[a][key] for a in apps]
        rows.append((label, correlation(deltas, delta_coverage)))
    spa = [
        after[a]["stalls"] / max(after[a]["attempts"], 1)
        - before[a]["stalls"] / max(before[a]["attempts"], 1)
        for a in apps
    ]
    rows.append(("stalls per action", correlation(spa, delta_coverage)))
    for label, value in sorted(rows, key=lambda t: -abs(t[1])):
        print(f"  {label:26s} {value:+6.2f}")

    print("\nwhat predicts coverage in the later campaign?")
    cov = [after[a]["percent"] for a in apps]
    cands = [(label, [after[a][key] for a in apps]) for label, key in METRICS]
    cands.append(("app size in methods", [after[a]["total"] for a in apps]))
    cands.append(
        ("route contribution (pts)", [after[a]["percent"] - after[a]["gui_percent"] for a in apps])
    )
    cands.append(("crashes", [after[a]["crashes"] for a in apps]))
    for label, xs in sorted(cands, key=lambda t: -abs(correlation(t[1], cov))):
        print(f"  {label:26s} {correlation(xs, cov):+6.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
