"""A check that did not run must not report as PASS, and DATA must follow the env var.

Both defects were found by an independent review of the failure-modes doc
(2026-09-28): `est_h double-count` reported `status: "PASS"` with detail
"SKIP — no epics.json" in every run db on disk, and `validate.DATA` hardcoded
`Path.home()` so a run written under WEEKLY_REVIEW_DATA could not be audited.
"""
import importlib
import os
from pathlib import Path


def test_skipped_check_is_not_pass():
    """report(skipped=True) must emit SKIP, so an absent input cannot read clean."""
    from weekly_review import validate

    results = []

    # Same shape as the closure in run_checks.
    def report(name, ok, detail="", items=None, skipped=False):
        status = "SKIP" if skipped else ("PASS" if ok else "REVIEW")
        results.append(status)

    report("ran and held", True)
    report("ran and failed", False)
    report("did not run", True, "no input", skipped=True)
    assert results == ["PASS", "REVIEW", "SKIP"]

    # And the real one agrees: no source line may report a skip as a pass.
    src = Path(validate.__file__).read_text()
    assert 'status = "SKIP" if skipped' in src, "report() lost its SKIP branch"
    for line in src.splitlines():
        if "SKIP —" in line and "report(" in line:
            raise AssertionError(f"skip spelled into a PASS detail: {line.strip()}")


def test_data_dir_follows_env(tmp_path, monkeypatch):
    """A second hardcoded runs path sends the CLI and the auditor to different dirs."""
    monkeypatch.setenv("WEEKLY_REVIEW_DATA", str(tmp_path))
    import weekly_review.runs as runs
    import weekly_review.validate as validate

    importlib.reload(runs)
    importlib.reload(validate)
    try:
        assert validate.DATA == tmp_path / "runs"
    finally:
        monkeypatch.delenv("WEEKLY_REVIEW_DATA", raising=False)
        importlib.reload(runs)
        importlib.reload(validate)


def test_labor_invariant_is_not_counted_as_a_pass():
    """The ≥ direction is true by construction; it may not inflate the PASS count."""
    import re

    src = Path(__import__("weekly_review.validate", fromlist=["x"]).__file__).read_text()
    # EVERY report() call for this invariant must pass skipped=True -- checking a
    # window around one of them passes even when that one loses the flag, because
    # the sibling branch still carries it.
    calls = re.findall(r"report\(\s*\"labor ≥ hands-on invariant\"(?:[^()]|\([^()]*\))*\)", src, re.S)
    assert calls, "check disappeared"
    for call in calls:
        assert "skipped=True" in call, f"structural check reported as a real PASS: {call[:120]}"


def test_excluded_projects_failure_is_reported():
    """B7 used to `except Exception: pass`, so a broken check read as no finding."""
    src = Path(__import__("weekly_review.validate", fromlist=["x"]).__file__).read_text()
    i = src.index("excluded projects leak no events")
    j = src.index("excluded projects leak no events", i + 1)
    assert "could not run" in src[j - 400:j + 400], "B7 still swallows its exception"
