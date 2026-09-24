#!/usr/bin/env python3
"""Shrink a finished run's evidence without making it unverifiable.

A 600s run writes 250-460 MB and a 3600s run 1.5-2.7 GB, dominated by
`coverage_events.jsonl` because every coverage event re-serialises the whole
cumulative observed-unit table plus seven per-unit maps. Fifty apps times two arms
does not fit on this box.

Compressing the ledgers gets 12.7:1, but compressing them *first* is what made the
last sweep worthless: `valordroid summary` reads the ledgers by name and refused
every run in it with `missing transactions.jsonl; missing actions.jsonl; ...`. A run
nobody can re-verify is worth less than no run.

So the order is fixed here, and the order is the whole point:

  1. summarize and validate the run while its ledgers are plain
  2. write `summary.json` and `validation.json` beside them, uncompressed, so the
     verdict survives independently of the evidence it came from
  3. only then compress

`restore` puts a run back exactly as it was, so any archived run can be re-summarized
and re-validated later rather than being taken on trust.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Everything else in a run directory is small.
LEDGERS = (
    "coverage_events.jsonl",
    "coverage.jsonl",
    "transactions.jsonl",
    "actions.jsonl",
    "lifecycle.jsonl",
    "routes.jsonl",
    "associations.jsonl",
    "model_calls.jsonl",
    "crashes.jsonl",
    "raw-logcat.txt",
)


def _cli(repository: Path, command: str, run: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            str(repository / ".venv/bin/python"),
            "-m",
            "valordroid.cli",
            command,
            str(run),
        ],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(repository / "valordroid/src"), "PATH": "/usr/bin:/bin"},
    )


def capture_verdict(run: Path, *, repository: Path) -> dict:
    """Record what this run measured and whether it is admissible, as plain JSON."""

    summary_result = _cli(repository, "summary", run)
    try:
        summary = json.loads(summary_result.stdout)
        refusal = None
    except Exception:
        summary = None
        refusal = (summary_result.stdout or summary_result.stderr or "").strip()[:2000]
    (run / "summary.json").write_text(
        json.dumps(
            {"summary": summary, "refusal": refusal},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    validate_result = _cli(repository, "validate", run)
    (run / "validation.json").write_text(
        json.dumps(
            {
                "exit_code": validate_result.returncode,
                "output": (validate_result.stdout or "").strip()[:8000],
                "error": (validate_result.stderr or "").strip()[:4000],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"summary": summary, "refusal": refusal}


def compress(run: Path) -> dict:
    """Compress the bulky ledgers in place, after the verdict is already recorded."""

    if not (run / "summary.json").is_file():
        raise SystemExit(
            f"refusing to compress {run}: no summary.json, so the verdict would "
            "be lost with the readable evidence"
        )
    before = sum(path.stat().st_size for path in run.rglob("*") if path.is_file())
    for name in LEDGERS:
        path = run / name
        if path.is_file():
            subprocess.run(["gzip", "-6", str(path)], check=False)
    observations = run / "observations"
    if observations.is_dir():
        archive = run / "observations.tar.gz"
        done = subprocess.run(
            ["tar", "-czf", str(archive), "-C", str(run), "observations"],
            capture_output=True,
        )
        if done.returncode == 0 and archive.is_file():
            subprocess.run(["rm", "-rf", str(observations)], check=False)
    after = sum(path.stat().st_size for path in run.rglob("*") if path.is_file())
    return {"bytes_before": before, "bytes_after": after}


def restore(run: Path) -> None:
    """Put the run back the way it was, so it can be re-verified rather than trusted."""

    for name in LEDGERS:
        archived = run / f"{name}.gz"
        if archived.is_file():
            subprocess.run(["gunzip", "-f", str(archived)], check=False)
    archive = run / "observations.tar.gz"
    if archive.is_file() and not (run / "observations").is_dir():
        subprocess.run(
            ["tar", "-xzf", str(archive), "-C", str(run)], check=False
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument(
        "--repository", type=Path, default=Path("/home/ubuntu/llm-gui-testing-research")
    )
    parser.add_argument(
        "--action",
        choices=("retain", "restore", "verdict"),
        default="retain",
        help="retain: record the verdict then compress. restore: undo the compression.",
    )
    arguments = parser.parse_args()
    if arguments.action == "restore":
        restore(arguments.run)
        return 0
    verdict = capture_verdict(arguments.run, repository=arguments.repository)
    if arguments.action == "verdict":
        print(json.dumps(verdict.get("summary") or {"refusal": verdict["refusal"]}))
        return 0
    sizes = compress(arguments.run)
    print(json.dumps({**sizes, "refusal": verdict["refusal"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
