"""The pre-push gate must refuse a leak on a real `git push`, not only when called by hand.

A hook that exits 0 without scanning looks exactly like a clean push, so these
tests push through `.githooks/pre-push` into a local bare remote. They use
fixture pattern sources, so they behave the same with or without the fleet
config on the machine. The hostname is invented.
"""
from __future__ import annotations

import getpass
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FAKE_HOST = "build-box.tail0a1b2c.ts.net"
DENYLIST = "/\\b[a-z0-9-]+\\.tail[0-9a-f]{6}\\.ts\\.net\\b/\n"
REGISTRY = ("projects:\n  scrubfixture:\n    path: ~/dev/scrubfixture\n"
            "  zephyr-private-proj:\n    path: ~/dev/zephyr-private-proj\n")


def _git(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)


def _env(tmp: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "SCRUB_"))}
    env.update(SCRUB_REGISTRY=str(tmp / "registry.yaml"), SCRUB_DENYLIST=str(tmp / "denylist.txt"),
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return env


@pytest.fixture
def fixture_repo(tmp_path: Path):
    (tmp_path / "registry.yaml").write_text(REGISTRY)
    (tmp_path / "denylist.txt").write_text(DENYLIST)
    env = _env(tmp_path)
    remote, work = tmp_path / "remote.git", tmp_path / "scrubfixture"
    assert _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote)).returncode == 0
    assert _git(tmp_path, "init", "-q", "-b", "main", str(work)).returncode == 0
    (work / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "scrub-check.py", work / "scripts" / "scrub-check.py")
    (work / ".githooks").mkdir()
    shutil.copy(REPO / ".githooks" / "pre-push", work / ".githooks" / "pre-push")
    (work / "README.md").write_text("fixture\n")
    _git(work, "add", "-A", env=env)
    assert _git(work, "commit", "-qm", "base", env=env).returncode == 0
    _git(work, "remote", "add", "origin", str(remote))
    assert _git(work, "push", "-q", "origin", "main", env=env).returncode == 0
    _git(work, "config", "core.hooksPath", ".githooks")
    return work, env


def _push_file(work: Path, env: dict, name: str, body: str, marker: bool) -> subprocess.CompletedProcess:
    _git(work, "switch", "-qc", name.replace(".", "-"), "main")
    if marker:
        (work / ".scrub-as-public").write_text("flip pending\n")
    (work / name).write_text(body)
    _git(work, "add", "-A", env=env)
    _git(work, "commit", "-qm", "add notes", env=env)
    return _git(work, "push", "origin", "HEAD", env=env)


def test_repo_ships_an_active_gate():
    assert os.access(REPO / ".githooks" / "pre-push", os.X_OK)
    assert (REPO / "scripts" / "scrub-check.py").is_file()
    assert (REPO / ".scrub-as-public").is_file()


def test_push_carrying_a_tailnet_hostname_is_rejected(fixture_repo):
    work, env = fixture_repo
    r = _push_file(work, env, "notes.md", f"Open http://{FAKE_HOST}:8787/ to review.\n", marker=True)
    assert r.returncode != 0
    assert "Push blocked" in r.stderr and "notes.md" in r.stderr


def test_clean_push_goes_through(fixture_repo):
    work, env = fixture_repo
    r = _push_file(work, env, "notes.md", "Open the local server to review.\n", marker=True)
    assert r.returncode == 0, r.stderr


def test_private_repo_without_the_marker_skips_the_denylist(fixture_repo):
    # The fleet gate only enforces the denylist on public repos. The marker is
    # what makes a pre-flip private repo scan as public; without it this leaks.
    work, env = fixture_repo
    r = _push_file(work, env, "notes.md", f"Open http://{FAKE_HOST}:8787/ to review.\n", marker=False)
    assert r.returncode == 0, r.stderr


def test_src_carries_no_machine_home_path():
    hits = _git(REPO, "grep", "-n", f"/Users/{getpass.getuser()}", "--", "src/")
    assert hits.stdout == ""
