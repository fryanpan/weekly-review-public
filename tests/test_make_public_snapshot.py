"""The public snapshot is built from tracked files only, minus the private
prefixes, as one commit with a clean status and a removal note."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_public_snapshot.py"
_spec = importlib.util.spec_from_file_location("make_public_snapshot", _SCRIPT)
snap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snap)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    (src / "README.md").write_text("public\n")
    (src / "src").mkdir()
    (src / "src" / "a.py").write_text("x = 1\n")
    (src / "docs" / "research").mkdir(parents=True)
    (src / "docs" / "research" / "private.md").write_text("client hours\n")
    (src / ".gitignore").write_text("docs/data/\nsecret.txt\n")
    (src / "docs" / "data").mkdir()
    (src / "docs" / "data" / "digest.md").write_text("the user's messages\n")
    (src / "secret.txt").write_text("ignored but present\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "init")
    # An untracked file in the working tree must not reach the snapshot either.
    (src / "untracked.txt").write_text("not added\n")
    return src


def test_snapshot_holds_tracked_files_minus_excluded_prefixes(source_repo, tmp_path):
    dest = tmp_path / "public"
    kept, dropped, status, _ = snap.build(source_repo, dest, exclude=("docs/research/",))

    assert sorted(kept) == [".gitignore", "README.md", "src/a.py"]
    assert dropped == ["docs/research/private.md"]
    assert status.strip() == ""
    assert (dest / "src" / "a.py").read_text() == "x = 1\n"
    assert not (dest / "docs").exists()
    # Gitignored and untracked files in the source tree never enter it.
    assert not (dest / "secret.txt").exists()
    assert not (dest / "untracked.txt").exists()


def test_snapshot_takes_committed_content_not_the_working_tree(source_repo, tmp_path):
    (source_repo / "src" / "a.py").write_text("x = 2  # uncommitted\n")
    (source_repo / "README.md").unlink()  # tracked, deleted on disk, still at HEAD
    dest = tmp_path / "public"
    kept, _, status, _ = snap.build(source_repo, dest, exclude=("docs/research/",))
    assert (dest / "src" / "a.py").read_text() == "x = 1\n"
    assert (dest / "README.md").read_text() == "public\n"
    assert "README.md" in kept and status.strip() == ""


def test_snapshot_is_one_commit_with_no_remote(source_repo, tmp_path):
    dest = tmp_path / "public"
    snap.build(source_repo, dest, exclude=("docs/research/",))
    assert _git(dest, "rev-list", "--count", "HEAD").strip() == "1"
    assert _git(dest, "remote").strip() == ""
    assert _git(dest, "log", "-1", "--format=%an <%ae>").strip() == "weekly-review <noreply@example.invalid>"


def test_removal_note_lists_every_dropped_path(source_repo, tmp_path):
    dest = tmp_path / "public"
    snap.build(source_repo, dest, exclude=("docs/research/",))
    note = (tmp_path / "public-removed.md").read_text()
    assert "`docs/research/`" in note
    assert "- docs/research/private.md" in note
    assert not (dest / "public-removed.md").exists()


def test_refuses_a_non_empty_destination(source_repo, tmp_path):
    dest = tmp_path / "public"
    dest.mkdir()
    (dest / "stale").write_text("")
    with pytest.raises(FileExistsError):
        snap.build(source_repo, dest)


def test_default_exclude_keeps_research_and_drops_data():
    """Research notes ship scrubbed (the owner, 2026-09-18); the data dir never ships."""
    assert "docs/data/" in snap.EXCLUDE and "docs/research/" not in snap.EXCLUDE


def _rules(tmp_path, text):
    f = tmp_path / "subs.txt"
    f.write_text(text)
    return snap.load_substitutions(f)


def test_substitutions_take_the_match_case_and_run_longest_first(tmp_path):
    rules = _rules(tmp_path, "# comment\nacme => client-a\nacme-labs => client-b\nACM => TK\n")
    out, n = snap.substitute("Acme, ACME, acme-labs and ACM-12 at acme.", rules)
    assert out == "Client-a, CLIENT-A, client-b and TK-12 at client-a."
    assert n == 5


def test_regex_rules_and_board_ids(tmp_path):
    from tests.fakeids import tid, wid
    rules = _rules(tmp_path, "/\\bTK-(\\d+)\\b => TICKET-\\1\n")
    out, n = snap.substitute(f"TK-12 on {tid('AbCdEfGhIjKl')} in {wid('AbCdEfGhIjKl')}; t-short stays", rules)
    assert out == "TICKET-12 on t-id in w-id; t-short stays"
    assert n == 3


def test_malformed_rule_is_refused(tmp_path):
    with pytest.raises(ValueError):
        _rules(tmp_path, "no arrow here\n")


def test_build_scrubs_research_in_the_snapshot_and_leaves_the_source_alone(source_repo, tmp_path):
    (source_repo / "docs" / "research" / "private.md").write_text("client hours for acme\n")
    _git(source_repo, "add", "-A")
    _git(source_repo, "commit", "-q", "-m", "research")
    rules = _rules(tmp_path, "acme => client-a\n")
    dest = tmp_path / "public"
    kept, dropped, status, substituted = snap.build(source_repo, dest, exclude=("docs/data/",), rules=rules)
    assert "docs/research/private.md" in kept and dropped == []
    assert (dest / "docs" / "research" / "private.md").read_text() == "client hours for client-a\n"
    assert (source_repo / "docs" / "research" / "private.md").read_text() == "client hours for acme\n"
    assert substituted == {"docs/research/private.md": 1} and status.strip() == ""
    note = (tmp_path / "public-removed.md").read_text()
    assert "1 replacements in 1 files" in note and "acme" not in note   # counts only, never the terms


def test_build_renames_a_path_the_rules_match(source_repo, tmp_path):
    (source_repo / "docs" / "research" / "acme-stats.md").write_text("see [acme](acme-stats.md)\n")
    _git(source_repo, "add", "-A")
    _git(source_repo, "commit", "-q", "-m", "research")
    rules = _rules(tmp_path, "acme => client-a\n")
    dest = tmp_path / "public"
    kept, _, status, substituted = snap.build(source_repo, dest, exclude=("docs/data/",), rules=rules)
    assert "docs/research/client-a-stats.md" in kept and "docs/research/acme-stats.md" not in kept
    assert not (dest / "docs" / "research" / "acme-stats.md").exists()
    assert (dest / "docs" / "research" / "client-a-stats.md").read_text() == "see [client-a](client-a-stats.md)\n"
    assert substituted["docs/research/client-a-stats.md"] == 3 and status.strip() == ""
    note = (tmp_path / "public-removed.md").read_text()
    assert "## Renamed (1 paths)" in note and "acme" not in note
