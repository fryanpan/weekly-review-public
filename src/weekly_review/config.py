"""Configuration loading for weekly-review.

Config lives at ~/.config/weekly-review/:
  - projects.yaml
  - anchors.yaml
  - expertise.yaml
  - overrides.yaml
  - effort.yaml
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


CONFIG_DIR = Path(os.environ.get("WEEKLY_REVIEW_CONFIG", Path.home() / ".config" / "weekly-review"))


@dataclass
class ProjectSpec:
    slug: str
    cwd_patterns: list[str]
    exclude: bool = False  # if true, events in this project are dropped entirely
    # Client this project's time is tracked against (e.g. "Client A").
    # None = personal/unbilled. Enables per-client time rollups (the user 2026-07-08).
    client: str | None = None
    # Only meaningful when client is set. False = volunteer/pro-bono (e.g. Client A):
    # hours are tracked per-client but must display as unbilled — a volunteer
    # client must never read as revenue. Paid engagements (a paid client) keep
    # the default True. (Registry check by the lead agent, 2026-07-09.)
    billable: bool = True
    # GitHub repos (owner/name) to pull PR artifacts from. When set, OVERRIDES
    # the single auto-detected origin remote — needed for org projects whose
    # work spans several repos (Client A: client-a-app + wrapper-project +
    # plugin-examples + common-agent-setup). One detected remote missed ~90%
    # of Client A PRs (2026-07-09 gap analysis).
    repos: list[str] = field(default_factory=list)
    # Harness/eval projects (e.g. rare-disease-benchmark): agent-only events
    # that never attach to a task are EXPECTED (synthetic benchmark runs, not
    # work output). Audit B1 excludes this project's orphaned events from its
    # >95%-attached denominator so real attribution gaps stay visible. Does NOT
    # exclude anything from the DB or metrics. (the user 2026-07-09.)
    ignore_task: bool = False
    # Path globs (fnmatch against the path in `git log --numstat`, so `*`
    # crosses directories: `data/*` matches `data/legistar/x.json`). Files
    # matching any pattern are dropped from a commit's files_changed /
    # insertions / deletions before the estimator sees them, and reported
    # beside the commit as excluded. For checked-in data, benchmark output and
    # other generated files that were never authored: one 118,663-line commit
    # of scraped records priced a weekend at 38h. (the user 2026-09-16.)
    diffstat_exclude: list[str] = field(default_factory=list)
    # Lowercase substrings that route a review doc to this project when nothing
    # structural (board, source path, doc root) resolves it: matched against the
    # doc's lowercased sourceUrl + docId + title + board name. Last resort only;
    # projects are checked in config order and the first match wins. Per-user
    # because the words are the user's own project vocabulary.
    doc_keywords: list[str] = field(default_factory=list)


@dataclass
class ProjectsConfig:
    projects: list[ProjectSpec]

    def client_for(self, slug: str) -> str | None:
        for p in self.projects:
            if p.slug == slug:
                return p.client
        return None

    def slug_for(self, cwd: str) -> str | None:
        # A home directory on a second volume sits under `/Volumes/Data/Users/...`
        # while every other source ingests through `/Users/...`. Normalize so a
        # single pattern set covers both.
        candidates = [cwd]
        if cwd.startswith("/Volumes/Data/Users/"):
            candidates.append(cwd[len("/Volumes/Data"):])
        for c in candidates:
            for p in self.projects:
                for pattern in p.cwd_patterns:
                    if fnmatch.fnmatchcase(c, pattern):
                        return p.slug
        return None

    def is_excluded(self, slug: str) -> bool:
        for p in self.projects:
            if p.slug == slug:
                return p.exclude
        return False


def load_projects(path: Path | None = None) -> ProjectsConfig:
    path = path or (CONFIG_DIR / "projects.yaml")
    if not path.exists():
        return ProjectsConfig(projects=[])
    data = yaml.safe_load(path.read_text()) or {}
    specs = [ProjectSpec(**p) for p in data.get("projects", [])]
    return ProjectsConfig(projects=specs)


def load_yaml(name: str) -> dict[str, Any]:
    """Load any of the config YAMLs by filename (without extension)."""
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def github_usernames() -> list[str]:
    """The GitHub login(s) that count as 'the user' for authored-PR attribution.

    Source order: WEEKLY_REVIEW_GITHUB_USER env var (comma-separated), else the
    `identity.github_users` list in identity.yaml. Empty means "no filter" — on
    shared repos that attributes every teammate's PR to the user, so a run with
    no identity configured warns (see cli).
    """
    env = os.environ.get("WEEKLY_REVIEW_GITHUB_USER", "").strip()
    if env:
        return [u.strip() for u in env.split(",") if u.strip()]
    ident = load_yaml("identity").get("identity", {}) or {}
    users = ident.get("github_users") or ident.get("github_user") or []
    if isinstance(users, str):
        users = [users]
    return [str(u).strip() for u in users if str(u).strip()]


def ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def load_env_file(path: Path | None = None) -> int:
    """Load KEY=VALUE lines from ~/.config/weekly-review/env into os.environ.

    Existing env vars win — shell exports override the file. Lines that start
    with # or are blank are skipped. Values can be optionally double- or
    single-quoted; quotes are stripped. Returns the count of vars applied.
    """
    path = path or (CONFIG_DIR / "env")
    if not path.exists():
        return 0
    applied = 0
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            applied += 1
    return applied
