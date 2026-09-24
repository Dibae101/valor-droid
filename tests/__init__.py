"""Regression tests for VALOR-Droid.

Every test here pins a defect found during semantic review, so a future change
cannot silently reintroduce it. The suite uses only the standard library, matching
the package's empty dependency set:

    PYTHONPATH=app/src python3 -m unittest discover -s tests -t .

No test requires Docker, a device, or a pinned instrumenter artifact.
The campaign driver refuses to start a run with under 8 GiB free, so point
TMPDIR at a roomy filesystem (e.g. TMPDIR=~/workspace/.tmp-test) on small
containers where /tmp is a tiny tmpfs.
"""
