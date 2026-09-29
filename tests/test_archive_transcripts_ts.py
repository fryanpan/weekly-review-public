"""The launchd archiver must keep archive-transcripts.sh's no-data-loss guarantees.

The bun rewrite exists only because launchd cannot exec bash on the Data volume;
nothing about WHAT gets archived is allowed to change. Each test pins one
guarantee from the shell script's header.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "archive-transcripts.ts"
BUN = shutil.which("bun") or str(Path.home() / ".bun" / "bin" / "bun")

pytestmark = pytest.mark.skipif(not Path(BUN).exists(), reason="bun not installed")


def _run(tmp_path):
    env = dict(os.environ,
               WEEKLY_REVIEW_CLAUDE_PROJECTS=str(tmp_path / "src"),
               WEEKLY_REVIEW_ARCHIVE=str(tmp_path / "dest"),
               WEEKLY_REVIEW_HISTORY=str(tmp_path / "history"),
               WEEKLY_REVIEW_ARCHIVE_LOG=str(tmp_path / "archive.log"))
    return subprocess.run([BUN, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def _write(path, text, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))


def test_new_file_is_copied_with_its_mtime(tmp_path):
    _write(tmp_path / "src/proj/a.jsonl", "one\n", 1_700_000_000)
    assert _run(tmp_path).returncode == 0
    out = tmp_path / "dest/proj/a.jsonl"
    assert out.read_text() == "one\n"
    # The archive freshness gate reads mtimes; a copy stamped "now" would make a
    # frozen week look live.
    assert int(out.stat().st_mtime) == 1_700_000_000


def test_deleted_source_file_stays_in_archive(tmp_path):
    _write(tmp_path / "src/proj/a.jsonl", "one\n", 1_700_000_000)
    _run(tmp_path)
    (tmp_path / "src/proj/a.jsonl").unlink()
    assert _run(tmp_path).returncode == 0
    assert (tmp_path / "dest/proj/a.jsonl").read_text() == "one\n"


def test_modified_file_replaces_and_old_version_is_kept(tmp_path):
    _write(tmp_path / "src/proj/a.jsonl", "one\n", 1_700_000_000)
    _run(tmp_path)
    _write(tmp_path / "src/proj/a.jsonl", "one\ntwo\n", 1_700_000_100)
    assert _run(tmp_path).returncode == 0
    assert (tmp_path / "dest/proj/a.jsonl").read_text() == "one\ntwo\n"
    kept = list((tmp_path / "history").glob("*/proj/a.jsonl"))
    assert [k.read_text() for k in kept] == ["one\n"]


def test_archive_copy_newer_than_source_is_left_alone(tmp_path):
    _write(tmp_path / "src/proj/a.jsonl", "short\n", 1_700_000_000)
    _write(tmp_path / "dest/proj/a.jsonl", "longer archived copy\n", 1_700_000_500)
    assert _run(tmp_path).returncode == 0
    assert (tmp_path / "dest/proj/a.jsonl").read_text() == "longer archived copy\n"


def test_unchanged_run_copies_nothing_and_leaves_no_history(tmp_path):
    _write(tmp_path / "src/proj/a.jsonl", "one\n", 1_700_000_000)
    _run(tmp_path)
    r = _run(tmp_path)
    assert r.returncode == 0
    assert "copied 0 " in (tmp_path / "archive.log").read_text().splitlines()[-2]
    assert not any((tmp_path / "history").iterdir())
    assert not list((tmp_path / "dest").rglob("*.archive-tmp"))


def test_unreadable_file_is_skipped_logged_and_fails_the_run(tmp_path):
    _write(tmp_path / "src/proj/ok.jsonl", "fine\n", 1_700_000_000)
    bad = tmp_path / "src/proj/bad.jsonl"
    _write(bad, "secret\n", 1_700_000_000)
    bad.chmod(0)
    try:
        r = _run(tmp_path)
    finally:
        bad.chmod(0o644)
    assert (tmp_path / "dest/proj/ok.jsonl").read_text() == "fine\n"
    assert r.returncode == 1
    assert "proj/bad.jsonl" in r.stderr


def test_script_never_shells_out():
    # rsync, ls and bash are exactly the binaries launchd is denied on the Data
    # volume. A child process would reintroduce the failure this file exists to fix.
    src = SCRIPT.read_text()
    for forbidden in ("child_process", "Bun.spawn", "Bun.$", "execSync", "spawnSync"):
        assert forbidden not in src
