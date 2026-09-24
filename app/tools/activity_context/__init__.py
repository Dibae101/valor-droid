"""Mock the intent context an activity reads, so launching it directly works.

Launching an activity bare is worse than not launching it at all. Measured on
Chess, enabling blind forced activation moved coverage from 28.70% to 5.93%: its
PlayActivity, AdvancedActivity and ImportActivity each read a data URI they were
never given, rendered a dead shell, and the exploration budget drained into it.

Following Delm (arXiv:2404.19307), which reports +21.13% method coverage by
mocking activity contexts rather than only firing entry points, this module reads
what each activity actually takes from its Intent and produces concrete values
for it. The analysis is read-only: the APK is never rewritten, so the binary that
gets measured stays the binary that ships.

Values are deliberately boring and deterministic. They are recorded in the frozen
runtime configuration and hashed into the run manifest, so offline validation can
re-derive the exact launch from evidence alone.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Mapping

_ANALYZER = Path(__file__).resolve().parent
_CLASS = "IntentContextAnalyzer"

# One value per supported type. `am start` carries these as typed flags, so the
# value has to survive a shell argument list and satisfy the runtime config's
# safety rule: 1-128 characters of [A-Za-z0-9._@+-].
#
# The integer-ish defaults are 1 rather than 0 because 0 is frequently a sentinel
# meaning "nothing selected", which sends an activity straight back down the
# empty path we are trying to escape.
_DEFAULTS: Mapping[str, str] = {
    "string": "valordroid",
    "int": "1",
    "long": "1",
    "boolean": "true",
    "float": "1",
}


def analyze(
    apk: Path, *, androlog_jar: Path, platforms: Path, timeout_seconds: float = 420.0
) -> dict:
    """Return the analyzer's raw report, or an empty report if it cannot run.

    A failure here must never fail a campaign: without the report the ladder
    simply launches components the way it did before.
    """

    command = [
        "java",
        "-Xmx6g",
        "-cp",
        f"{androlog_jar}:{_ANALYZER}",
        _CLASS,
        str(apk),
        str(platforms),
    ]
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout_seconds
        )
    except (subprocess.TimeoutExpired, OSError):
        return {"activities": {}}
    if done.returncode != 0 or not done.stdout.strip():
        return {"activities": {}}
    try:
        report = json.loads(done.stdout)
    except json.JSONDecodeError:
        return {"activities": {}}
    return report if type(report) is dict else {"activities": {}}


def component_extras(
    report: Mapping, *, package: str, data_uri: str | None = None
) -> dict[str, list[list[str]]]:
    """Turn a report into the `component_extras` shape the runtime config takes.

    Keyed by the fully qualified component so it matches what `am start -n`
    receives after normalisation.
    """

    activities = report.get("activities") if type(report) is dict else None
    if type(activities) is not dict:
        return {}
    result: dict[str, list[list[str]]] = {}
    for activity, detail in sorted(activities.items()):
        if type(detail) is not dict:
            continue
        entries: list[list[str]] = []
        for item in detail.get("extras") or ():
            if type(item) is not dict:
                continue
            key, kind = item.get("key"), item.get("type")
            if type(key) is not str or kind not in _DEFAULTS:
                continue
            entries.append([key, kind, _DEFAULTS[kind]])
        # A URI is what an importer or viewer activity almost always wants, and
        # it is the difference between reaching that screen and reaching a
        # NullPointerException. Only supplied when the caller has a real one.
        if detail.get("reads_data_uri") and data_uri:
            entries.append(["", "uri", data_uri])
        if entries:
            result[f"{package}/{activity}"] = entries
    return result
