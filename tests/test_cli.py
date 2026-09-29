import sys
import pytest
from weekly_review.cli import build_parser, main


def test_cli_help(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    out = capsys.readouterr().out
    assert "--since" in out
    assert "--inspect" in out
    assert "--recalibrate" in out
    assert "--diff-runs" in out


def test_cli_missing_config_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WEEKLY_REVIEW_CONFIG", str(tmp_path / "nonexistent"))
    # Need to reimport config so CONFIG_DIR picks up env var
    import importlib, weekly_review.config, weekly_review.cli
    importlib.reload(weekly_review.config)
    importlib.reload(weekly_review.cli)
    from weekly_review.cli import main as fresh_main
    sys.argv = ["weekly-review", "--since", "2026-04-05"]
    rc = fresh_main()
    err = capsys.readouterr()
    assert rc == 2
    assert "projects.yaml" in (err.out + err.err)


def test_presence_stitch_flag_defaults_to_the_module_constant():
    """The tolerance is a knob the user asked to see, so it must be settable."""
    from weekly_review.effort import PRESENCE_STITCH_SEC

    parser = build_parser()
    assert parser.parse_args(["--since", "2026-04-05"]).presence_stitch_min == PRESENCE_STITCH_SEC / 60
    assert parser.parse_args(["--since", "2026-04-05", "--presence-stitch-min", "30"]).presence_stitch_min == 30


def test_pipeline_sources_hands_on_from_the_union_not_one_instrument():
    """Regression: presence_hours_by_task existed, was tested, and nothing called it.

    The run wrote `active_hours_by_task` -- the per-message keystroke model the user
    rejected -- into the hands-on slot, so every published leverage number used a
    metric that had already been thrown out. A function can be correct, covered,
    and entirely unreachable; only the call site proves the pipeline uses it.

    Updated 2026-09-12: hands-on is now the UNION of two instruments on two
    tolerances (transcripts at 10min, workspace at 60s). The same defect would
    now look like the union function shipping while `active` kept sourcing from
    one side, so the assertion moved with the metric rather than being deleted.
    """
    import inspect
    from weekly_review import cli

    src = inspect.getsource(cli)
    assert "hands_on_hours_by_task(" in src, "pipeline no longer computes the union"
    assert "presence_hours_by_task(" in src, (
        "the run must still compute presence-only, so it can name what the "
        "published figure replaces"
    )
    hands_on_assign = [
        ln for ln in src.splitlines()
        if ln.strip().startswith("active = ")
    ]
    assert hands_on_assign, "could not find the hands-on assignment"
    assert all("hands_on_hours_by_task" in ln for ln in hands_on_assign), (
        f"hands-on must come from the union, got: {hands_on_assign}"
    )


def test_the_run_prints_union_and_sum_together():
    """Review doc, 2026-09-12: the anti-double-count claim has to be checkable.

    "Unioned, never added" is unfalsifiable from a single printed total. Printing
    both, every run, is what turns it into something a reader can check -- and
    the same shape as the coverage line that now prints even when healthy.
    """
    import inspect
    from weekly_review import cli

    src = inspect.getsource(cli)
    assert "hands-on union:" in src, "the run must print the union line"
    assert "their SUM is" in src, "the union line must print the sum beside it"


def test_until_is_inclusive_of_the_whole_end_day():
    """`--until` counts its own date through 23:59:59, and that is load-bearing.

    Read as exclusive, a Mon-Sun week gets requested as
    `--since 2026-08-03 --until 2026-08-10`, which measures EIGHT days and makes
    consecutive weeks overlap on the boundary day. That shipped two inflated
    weekly hands-on figures on 2026-08-28 (22.9% and 8.5% of the subject's
    events fell on the surplus day) and nothing in the output revealed it.

    Locking the semantics so a future change has to be deliberate, and so the
    help text and the behaviour can't drift apart again.
    """
    from weekly_review.cli import _iso_date

    since_iso = _iso_date("2026-08-03")
    until_iso = _iso_date("2026-08-09").replace("00:00:00", "23:59:59")

    assert since_iso.startswith("2026-08-03T00:00:00")
    assert until_iso.startswith("2026-08-09T23:59:59")

    # An event late on the end date is INSIDE the window; the next day is out.
    assert not ("2026-08-09T22:10:00.000Z" < since_iso or "2026-08-09T22:10:00.000Z" > until_iso)
    assert "2026-08-10T00:05:00.000Z" > until_iso

    # And the help text must say so, since the flag alone reads as exclusive.
    from weekly_review.cli import build_parser
    help_text = build_parser().format_help()
    assert "INCLUSIVE" in help_text, "--until must document that it is inclusive"


def test_db_readers_run_after_the_db_is_written():
    """Regression: the audits and the BI export both READ run.sqlite.

    They sat above the persist block, so every run printed
    "no such table: run_meta" / "run.sqlite missing" and carried on -- their own
    best-effort `except` turned a dead call site into a warning nobody read. The
    every-pass audits never ran, and the BI export froze at its last good write
    (Jul 10 -> Aug 28) while the pipeline reported success.

    Ordering is the whole contract here, so ordering is what gets asserted.
    """
    import inspect
    from weekly_review import cli

    lines = inspect.getsource(cli).splitlines()

    def last_line_of(needle: str) -> int:
        hits = [i for i, ln in enumerate(lines) if needle in ln]
        assert hits, f"could not find {needle!r} in cli.py"
        return hits[-1]

    # Last occurrence, not first: `--validate` calls run_checks() near the top of
    # main() against an already-written run, which is correct. What must exist is
    # a call site AFTER the pipeline persists its own DB.
    wrote_db = last_line_of("wrote run db:")
    assert last_line_of("run_checks(") > wrote_db, "audits must read the DB after it is written"
    assert last_line_of("export_task_facts(") > wrote_db, "BI export must read the DB after it is written"


def test_archive_window_coverage_counts_only_files_touched_in_window(tmp_path):
    """The gate that would have caught two silent archiver deaths.

    Transcripts are append-only, so a session live during the window has an
    mtime at or after the window start. Zero such files means the archive
    cannot hold the window — which is an error, not a quiet week.
    """
    import os
    from datetime import datetime, timezone
    from weekly_review.cli import archive_window_coverage

    old = tmp_path / "old.jsonl"
    old.write_text("{}\n")
    stamp = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old, (stamp, stamp))

    in_window, newest = archive_window_coverage(tmp_path, "2026-08-17T00:00:00+00:00")
    assert in_window == 0, "a file predating the window must not count as coverage"
    assert newest == datetime(2026, 8, 1, tzinfo=timezone.utc)

    fresh = tmp_path / "nested" / "fresh.jsonl"
    fresh.parent.mkdir()
    fresh.write_text("{}\n")
    stamp = datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp()
    os.utime(fresh, (stamp, stamp))

    in_window, newest = archive_window_coverage(tmp_path, "2026-08-17T00:00:00+00:00")
    assert in_window == 1, "must recurse into project subdirs"
    assert newest == datetime(2026, 8, 20, tzinfo=timezone.utc)


def test_stale_archive_is_an_error_not_an_empty_week():
    """The gate must be on by default — the whole failure mode is that nobody looks."""
    parser = build_parser()
    assert parser.parse_args([]).allow_stale_archive is False
    assert parser.parse_args(["--allow-stale-archive"]).allow_stale_archive is True


def test_the_run_publishes_the_week_figure_with_what_it_replaces():
    """Review doc, 2026-09-12: a moved number that does not name what it moved from
    is indistinguishable from a number nobody changed.

    Both sides have to be measured the same way. A week-wide union against a
    per-task SUM would flatter whichever side was summed, so the comparison
    runs presence over one synthetic whole-week task.
    """
    import inspect
    from weekly_review import cli

    src = inspect.getsource(cli)
    assert "WEEK hands-on:" in src, "the run must publish the weekly figure"
    assert "It REPLACES presence-only on" in src, (
        "the weekly figure must name what it replaces"
    )
    assert '_whole_week = Task(' in src, (
        "the replaced figure must be measured week-wide, not summed per task"
    )


def test_the_window_compares_instants_not_strings():
    """Git stamps commits in local time. A commit at 23:53 Pacific on the last
    day is 06:53 UTC the next day, outside the window; one at 20:00 Pacific the
    evening before the first day is 03:00 UTC on it, inside."""
    from weekly_review.cli import _in_window
    since, until = "2026-09-07T00:00:00+00:00", "2026-09-13T23:59:59+00:00"
    assert not _in_window("2026-09-13T23:53:38-07:00", since, until)
    assert _in_window("2026-09-06T20:00:00-07:00", since, until)
    assert _in_window("2026-09-13T23:59:00.000Z", since, until)
    assert not _in_window("2026-09-14T00:00:01Z", since, until)
    assert _in_window("2026-09-10T12:00:00", since, until)


def test_every_source_uses_the_instant_window_and_excluded_projects_bring_no_commits():
    import inspect
    from weekly_review import cli
    src = inspect.getsource(cli)
    assert "ev.ts < since_iso" not in src and "ev.ts > until_iso" not in src
    loop = src[src.find("for spec in projects.projects:"):src.find("extract_github_prs(")]
    assert "if spec.exclude:" in loop and loop.find("if spec.exclude:") < loop.find("extract_git_log(")
    assert "since=since_iso" in loop
    # The exclusion is per project and only the CLI knows the spec: assert the
    # patterns actually reach the extractor.
    assert "diffstat_exclude=spec.diffstat_exclude" in loop
