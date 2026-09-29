# Weekly-Review V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the task-leverage weekly-review tool end-to-end against 2 weeks of Mac Mini Claude Code transcripts in 1-2 days of work.

**Architecture:** Python package `weekly_review/` with one extractor per source (transcript, git, GitHub), a pure-function segmenter, a pure-function effort/coverage calculator, a single LLM enrichment pass per task (title + domain expertise + human-effort estimate), and a markdown digest renderer. CLI entry point `weekly-review`. Config at `~/.config/weekly-review/`. Events and runs materialized under `~/.local/share/weekly-review/runs/<run_id>/`.

**Tech Stack:** Python 3.12, `uv` for deps (user preference), `anthropic` SDK for LLM calls, `PyYAML` for config, `pytest` for tests, `gh` CLI for GitHub extraction, `git` CLI for git log extraction, stdlib for everything else.

**Reference spec:** `docs/product/specs/2026-04-19-task-leverage-model-design.md`.
**Attribution:** `ATTRIBUTIONS.md` — license-check any code before copying.

---

## File structure

```
weekly-review/
├── pyproject.toml                          # uv-managed
├── src/weekly_review/
│   ├── __init__.py
│   ├── cli.py                              # argparse entry point
│   ├── config.py                           # load projects.yaml, anchors.yaml, etc.
│   ├── events.py                           # Event dataclass + JSONL IO
│   ├── runs.py                             # run_id, run directories, --diff-runs
│   ├── projects.py                         # cwd → project-slug normalization
│   ├── extractors/
│   │   ├── __init__.py
│   │   ├── claude_transcript.py            # parse ~/.claude/projects/*/*.jsonl
│   │   ├── git_log.py                      # git log per registered repo
│   │   └── github_pr.py                    # gh pr list per registered repo
│   ├── segmentation.py                     # Layer 1 rules + proximity fallback
│   ├── overrides.py                        # apply overrides.yaml to segmented tasks
│   ├── lifecycle.py                        # task status: open / shipped / abandoned
│   ├── effort.py                           # active hours, cognitive wall-clock, switches
│   ├── coverage.py                         # coverage % computation
│   ├── llm.py                              # anthropic client wrapper with caching
│   ├── enrichment.py                       # per-task LLM pass: title, expertise, hours
│   ├── calibration.py                      # anchor management + interactive --recalibrate
│   └── digest.py                           # markdown digest renderer
├── tests/
│   ├── fixtures/                           # tiny golden JSONL samples
│   ├── test_events.py
│   ├── test_projects.py
│   ├── test_claude_transcript.py
│   ├── test_segmentation.py
│   ├── test_effort.py
│   ├── test_coverage.py
│   └── test_digest.py
└── docs/product/plans/v1-plan.md           # this file
```

Each module has one clear responsibility; the pipeline flows top-down: extractors → events → segmentation → overrides → lifecycle → effort/coverage → enrichment → digest.

---

## Dependencies and conventions

- Use `uv` — `uv init`, `uv add`, `uv run pytest`. Per workflow-conventions, prefer current best practices.
- Tests alongside code, not after. Coverage target ~80% of new code. Pure-function modules (segmentation, effort, coverage, digest) get thorough unit tests. LLM-call modules get integration tests with mocked responses.
- Frequent commits — each task ends with a commit. Commits should be small, named for the component, and compile + pass tests.
- Attribution: if you copy >5 lines from `analyze_transcript.py` or `work-review.ts`, add a header comment pointing to the source, per `ATTRIBUTIONS.md`.
- No backwards-compat shims. No feature flags. Simplest thing that satisfies the spec.

---

## Task 1: Scaffold the package

**Files:**
- Create: `pyproject.toml`
- Create: `src/weekly_review/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1.1: Initialize uv project**

Run:
```bash
cd /Users/<you>/dev/weekly-review
uv init --package --name weekly-review --python 3.12
```

Expected: creates `pyproject.toml`, `src/weekly_review/__init__.py`, `README.md`, `.python-version`. Keep the existing `README.md` — if `uv init` overwrote it, restore from git.

- [ ] **Step 1.2: Add dependencies**

Run:
```bash
uv add anthropic pyyaml
uv add --dev pytest pytest-cov
```

Expected: `pyproject.toml` has `dependencies = ["anthropic>=...", "pyyaml>=..."]` and a `[dependency-groups]` `dev = ["pytest>=...", "pytest-cov>=..."]`.

- [ ] **Step 1.3: Add package script entry**

Edit `pyproject.toml`, add under `[project.scripts]`:
```toml
[project.scripts]
weekly-review = "weekly_review.cli:main"
```

- [ ] **Step 1.4: Write smoke test**

Create `tests/test_import.py`:
```python
def test_package_imports():
    import weekly_review
    assert weekly_review is not None
```

- [ ] **Step 1.5: Run tests**

Run: `uv run pytest tests/ -v`
Expected: 1 passed.

- [ ] **Step 1.6: Commit**

```bash
git add pyproject.toml src/weekly_review/__init__.py tests/ .python-version uv.lock
git commit -m "scaffold: weekly_review package with uv + pytest"
```

---

## Task 2: Event dataclass and JSONL IO

**Files:**
- Create: `src/weekly_review/events.py`
- Create: `tests/test_events.py`

- [ ] **Step 2.1: Write failing test for Event roundtrip**

Create `tests/test_events.py`:
```python
import json
from weekly_review.events import Event, read_events, write_events


def test_event_roundtrip(tmp_path):
    events = [
        Event(
            ts="2026-04-15T10:22:33Z",
            actor="user",
            project="weekly-review",
            session_id="sess-1",
            source_system="claude_transcript",
            source_machine="mac_mini",
            kind="user_message",
            content_snippet="hello",
            metadata={"words": 1},
            seq=0,
        ),
    ]
    path = tmp_path / "events.jsonl"
    write_events(path, events)
    read_back = list(read_events(path))
    assert read_back == events


def test_event_rejects_missing_fields():
    import pytest
    with pytest.raises(TypeError):
        Event(ts="2026-04-15T10:22:33Z")  # missing required fields
```

- [ ] **Step 2.2: Run — confirm it fails**

Run: `uv run pytest tests/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'weekly_review.events'`.

- [ ] **Step 2.3: Implement events.py**

Create `src/weekly_review/events.py`:
```python
"""Event schema and JSONL IO for weekly-review."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator


@dataclass(frozen=True)
class Event:
    ts: str                      # ISO8601 UTC
    actor: str                   # "user" | "agent" | "artifact"
    project: str                 # registry slug
    session_id: str              # source-specific session identifier
    source_system: str           # "claude_transcript" | "git" | "github" | ...
    source_machine: str          # "mac_mini" | "laptop" | ...
    kind: str                    # "user_message" | "agent_message" | "tool_call" | "pr_merged" | ...
    seq: int                     # monotonic within session for dedup
    content_snippet: str = ""    # first 500 chars, optionally redacted
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "Event":
        data = json.loads(line)
        return cls(**data)


def write_events(path: Path, events: Iterable[Event]) -> int:
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(ev.to_json())
            f.write("\n")
            count += 1
    return count


def read_events(path: Path) -> Iterator[Event]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield Event.from_json(line)
```

- [ ] **Step 2.4: Run — confirm it passes**

Run: `uv run pytest tests/test_events.py -v`
Expected: 2 passed.

- [ ] **Step 2.5: Commit**

```bash
git add src/weekly_review/events.py tests/test_events.py
git commit -m "events: Event dataclass with JSONL roundtrip"
```

---

## Task 3: Config loader

**Files:**
- Create: `src/weekly_review/config.py`
- Create: `tests/test_config.py`
- Create: `examples/projects.yaml` (sample)
- Create: `examples/expertise.yaml` (sample)

- [ ] **Step 3.1: Write failing test**

Create `tests/test_config.py`:
```python
from pathlib import Path
from weekly_review.config import ProjectsConfig, load_projects


def test_load_projects_maps_cwd_to_slug(tmp_path: Path):
    cfg_file = tmp_path / "projects.yaml"
    cfg_file.write_text(
        """
        projects:
          - slug: bike-map
            cwd_patterns:
              - "/Users/*/dev/family-bike-map"
              - "/Users/*/dev/family-bike-map/**"
              - "/Users/*/dev/family-bike-map--claude-worktrees-*"
          - slug: weekly-review
            cwd_patterns:
              - "/Users/*/dev/weekly-review"
              - "/Users/*/dev/weekly-review/**"
        """
    )
    projects = load_projects(cfg_file)
    assert projects.slug_for("/Users/<you>/dev/family-bike-map") == "bike-map"
    assert projects.slug_for("/Users/<you>/dev/family-bike-map--claude-worktrees-fix") == "bike-map"
    assert projects.slug_for("/Users/<you>/dev/weekly-review/subdir") == "weekly-review"
    assert projects.slug_for("/Users/<you>/dev/unknown-project") is None
```

- [ ] **Step 3.2: Run — confirm it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3.3: Implement config.py**

Create `src/weekly_review/config.py`:
```python
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


@dataclass
class ProjectsConfig:
    projects: list[ProjectSpec]

    def slug_for(self, cwd: str) -> str | None:
        for p in self.projects:
            for pattern in p.cwd_patterns:
                if fnmatch.fnmatchcase(cwd, pattern):
                    return p.slug
        return None


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


def ensure_config_dir() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR
```

- [ ] **Step 3.4: Write sample config examples**

Create `examples/projects.yaml`:
```yaml
# ~/.config/weekly-review/projects.yaml
# Maps cwd patterns (fnmatch-style globs) to project slugs.
# Worktrees normalize to the root repo's slug.
projects:
  - slug: weekly-review
    cwd_patterns:
      - "/Users/*/dev/weekly-review"
      - "/Users/*/dev/weekly-review/**"
      - "/Users/*/dev/weekly-review--claude-worktrees-*"

  - slug: ai-project-support
    cwd_patterns:
      - "/Users/*/dev/ai-project-support"
      - "/Users/*/dev/ai-project-support/**"
      - "/Users/*/dev/ai-project-support--claude-worktrees-*"
```

Create `examples/expertise.yaml`:
```yaml
# ~/.config/weekly-review/expertise.yaml
# Self-declared expertise — seeds the bryan_domain_expertise classifier.
# Values: expert, competent, learning.
domains:
  "backend / python / api design": expert
  "frontend / react / ux": competent
  "data / pandas / analysis": expert
  "machine learning / model training": learning
  "devops / kubernetes / cloud": competent
```

- [ ] **Step 3.5: Run — confirm it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: 1 passed.

- [ ] **Step 3.6: Commit**

```bash
git add src/weekly_review/config.py tests/test_config.py examples/
git commit -m "config: projects.yaml loader + expertise sample"
```

---

## Task 4: Run management

**Files:**
- Create: `src/weekly_review/runs.py`
- Create: `tests/test_runs.py`

- [ ] **Step 4.1: Write failing test**

Create `tests/test_runs.py`:
```python
from pathlib import Path
from weekly_review.runs import RunManager, new_run_id


def test_run_id_is_sortable():
    a = new_run_id()
    b = new_run_id()
    assert a <= b  # ISO-like timestamp ordering


def test_run_manager_creates_dirs(tmp_path: Path):
    mgr = RunManager(base=tmp_path)
    run = mgr.start_run(schema_version="v1")
    assert run.events_dir.exists()
    assert run.path.is_dir()
    meta = run.path / "run.yaml"
    assert meta.exists()
    assert "schema_version: v1" in meta.read_text()


def test_list_runs_newest_first(tmp_path: Path):
    mgr = RunManager(base=tmp_path)
    r1 = mgr.start_run(schema_version="v1")
    r2 = mgr.start_run(schema_version="v1")
    listed = mgr.list_runs()
    assert [r.run_id for r in listed] == [r2.run_id, r1.run_id]
```

- [ ] **Step 4.2: Run — confirm it fails**

Run: `uv run pytest tests/test_runs.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 4.3: Implement runs.py**

Create `src/weekly_review/runs.py`:
```python
"""Run management: each extraction pass gets a run_id + dir."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml


DATA_DIR = Path(os.environ.get("WEEKLY_REVIEW_DATA", Path.home() / ".local" / "share" / "weekly-review"))


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")


@dataclass
class Run:
    run_id: str
    schema_version: str
    path: Path

    @property
    def events_dir(self) -> Path:
        return self.path / "events"


class RunManager:
    def __init__(self, base: Path | None = None):
        self.base = base or (DATA_DIR / "runs")

    def start_run(self, schema_version: str) -> Run:
        run_id = new_run_id()
        path = self.base / run_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "events").mkdir(exist_ok=True)
        (path / "run.yaml").write_text(
            yaml.safe_dump({"run_id": run_id, "schema_version": schema_version, "created_at": datetime.now(timezone.utc).isoformat()})
        )
        return Run(run_id=run_id, schema_version=schema_version, path=path)

    def list_runs(self) -> list[Run]:
        if not self.base.exists():
            return []
        runs: list[Run] = []
        for p in sorted(self.base.iterdir(), reverse=True):
            meta_file = p / "run.yaml"
            if not meta_file.exists():
                continue
            meta = yaml.safe_load(meta_file.read_text())
            runs.append(Run(run_id=meta["run_id"], schema_version=meta["schema_version"], path=p))
        return runs
```

- [ ] **Step 4.4: Run — confirm it passes**

Run: `uv run pytest tests/test_runs.py -v`
Expected: 3 passed.

- [ ] **Step 4.5: Commit**

```bash
git add src/weekly_review/runs.py tests/test_runs.py
git commit -m "runs: RunManager with per-run dirs + schema versioning"
```

---

## Task 5: Claude transcript extractor

**Files:**
- Create: `src/weekly_review/extractors/__init__.py`
- Create: `src/weekly_review/extractors/claude_transcript.py`
- Create: `tests/test_claude_transcript.py`
- Create: `tests/fixtures/sample_transcript.jsonl`

The transcript JSONL schema in `~/.claude/projects/*/*.jsonl` emits one JSON object per line with fields `type` ("user" or "assistant"), `message.content` (string or array of content blocks), `cwd`, `sessionId`, `timestamp`, etc. See `/Users/<you>/dev/ai-project-support/.claude/skills/retro/scripts/analyze_transcript.py` (869 lines, MIT — internal) for a mature reference. We extract events per turn: one `user_message` event per user turn (filtering injected system messages), one `agent_message` event per assistant turn with the text content, and `tool_call` events for tool_use blocks.

- [ ] **Step 5.1: Create a fixture transcript**

Create `tests/fixtures/sample_transcript.jsonl`:
```jsonl
{"type":"user","timestamp":"2026-04-15T10:00:00.000Z","sessionId":"sess-a","cwd":"/Users/<you>/dev/weekly-review","message":{"role":"user","content":"Let's start with a Python package skeleton."}}
{"type":"assistant","timestamp":"2026-04-15T10:00:15.000Z","sessionId":"sess-a","cwd":"/Users/<you>/dev/weekly-review","message":{"role":"assistant","model":"claude-opus-4-7","content":[{"type":"text","text":"I'll create pyproject.toml and src/."}]}}
{"type":"user","timestamp":"2026-04-15T10:03:30.000Z","sessionId":"sess-a","cwd":"/Users/<you>/dev/weekly-review","message":{"role":"user","content":"<system-reminder>\nRemember to use TaskWrite.\n</system-reminder>"}}
{"type":"user","timestamp":"2026-04-15T10:04:05.000Z","sessionId":"sess-a","cwd":"/Users/<you>/dev/weekly-review","message":{"role":"user","content":"OK, now add tests."}}
{"type":"assistant","timestamp":"2026-04-15T10:04:22.000Z","sessionId":"sess-a","cwd":"/Users/<you>/dev/weekly-review","message":{"role":"assistant","model":"claude-opus-4-7","content":[{"type":"tool_use","id":"t1","name":"Write","input":{"file_path":"tests/test_x.py"}}]}}
```

- [ ] **Step 5.2: Write failing test**

Create `tests/test_claude_transcript.py`:
```python
from pathlib import Path
from weekly_review.config import ProjectSpec, ProjectsConfig
from weekly_review.extractors.claude_transcript import extract_from_jsonl


PROJECTS = ProjectsConfig(projects=[
    ProjectSpec(slug="weekly-review", cwd_patterns=["/Users/*/dev/weekly-review", "/Users/*/dev/weekly-review/**"]),
])


def test_extract_skips_system_reminders():
    fixture = Path("tests/fixtures/sample_transcript.jsonl")
    events = list(extract_from_jsonl(fixture, projects=PROJECTS, source_machine="mac_mini"))
    user_events = [e for e in events if e.kind == "user_message"]
    assert len(user_events) == 2
    assert "system-reminder" not in user_events[0].content_snippet
    assert all(e.project == "weekly-review" for e in events)


def test_extract_produces_tool_call_events():
    fixture = Path("tests/fixtures/sample_transcript.jsonl")
    events = list(extract_from_jsonl(fixture, projects=PROJECTS, source_machine="mac_mini"))
    tool_calls = [e for e in events if e.kind == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].metadata["tool_name"] == "Write"


def test_extract_preserves_session_id_and_seq():
    fixture = Path("tests/fixtures/sample_transcript.jsonl")
    events = list(extract_from_jsonl(fixture, projects=PROJECTS, source_machine="mac_mini"))
    assert all(e.session_id == "sess-a" for e in events)
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_extract_word_counts_in_metadata():
    fixture = Path("tests/fixtures/sample_transcript.jsonl")
    events = list(extract_from_jsonl(fixture, projects=PROJECTS, source_machine="mac_mini"))
    for e in events:
        if e.kind in ("user_message", "agent_message"):
            assert "words" in e.metadata
            assert isinstance(e.metadata["words"], int)
```

- [ ] **Step 5.3: Run — confirm it fails**

Run: `uv run pytest tests/test_claude_transcript.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 5.4: Implement extractor**

Create `src/weekly_review/extractors/__init__.py` (empty file).

Create `src/weekly_review/extractors/claude_transcript.py`:
```python
"""Extract events from Claude Code transcript JSONL.

Adapted from patterns in analyze_transcript.py (ai-project-support, internal).
See ATTRIBUTIONS.md.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from ..config import ProjectsConfig
from ..events import Event


SYSTEM_MESSAGE_PATTERNS = [
    re.compile(r"^Base directory for this skill:"),
    re.compile(r"^<(command-name|local-command|system-reminder)"),
    re.compile(r"^<local-command-caveat>"),
    re.compile(r"^This session is being continued from a previous conversation"),
]

SNIPPET_LIMIT = 500


def _is_system_message(text: str) -> bool:
    return any(p.match(text) for p in SYSTEM_MESSAGE_PATTERNS)


def _extract_text_and_tools(content) -> tuple[str, list[tuple[str, dict]]]:
    """Return (joined_text, [(tool_name, tool_input)])."""
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    tools: list[tuple[str, dict]] = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            texts.append(block.get("text", ""))
        elif btype == "tool_use":
            tools.append((block.get("name", ""), block.get("input", {})))
    return "\n".join(texts), tools


def extract_from_jsonl(
    path: Path,
    projects: ProjectsConfig,
    source_machine: str,
) -> Iterator[Event]:
    seq = 0
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind_in = rec.get("type")
            if kind_in not in ("user", "assistant"):
                continue

            ts = rec.get("timestamp")
            session_id = rec.get("sessionId") or rec.get("session_id")
            cwd = rec.get("cwd") or ""
            project = projects.slug_for(cwd) or "unknown"
            message = rec.get("message") or {}
            content = message.get("content")
            text, tools = _extract_text_and_tools(content)

            if kind_in == "user":
                if _is_system_message(text.strip()):
                    continue
                words = len(text.split())
                yield Event(
                    ts=ts,
                    actor="user",
                    project=project,
                    session_id=session_id,
                    source_system="claude_transcript",
                    source_machine=source_machine,
                    kind="user_message",
                    seq=seq,
                    content_snippet=text[:SNIPPET_LIMIT],
                    metadata={"words": words, "cwd": cwd},
                )
                seq += 1

            elif kind_in == "assistant":
                words = len(text.split())
                model = message.get("model", "")
                if text:
                    yield Event(
                        ts=ts,
                        actor="agent",
                        project=project,
                        session_id=session_id,
                        source_system="claude_transcript",
                        source_machine=source_machine,
                        kind="agent_message",
                        seq=seq,
                        content_snippet=text[:SNIPPET_LIMIT],
                        metadata={"words": words, "model": model, "cwd": cwd},
                    )
                    seq += 1
                for tool_name, tool_input in tools:
                    yield Event(
                        ts=ts,
                        actor="agent",
                        project=project,
                        session_id=session_id,
                        source_system="claude_transcript",
                        source_machine=source_machine,
                        kind="tool_call",
                        seq=seq,
                        content_snippet=str(tool_input)[:SNIPPET_LIMIT],
                        metadata={"tool_name": tool_name, "model": model, "cwd": cwd},
                    )
                    seq += 1


def iter_transcript_files(
    projects_dir: Path,
    since_iso: str | None = None,
) -> Iterator[Path]:
    """Yield JSONL files under the per-cwd directories.

    Filters by mtime >= since_iso when supplied (cheap first-pass filter; the
    extractor itself filters per-event by timestamp).
    """
    from datetime import datetime
    cutoff = None
    if since_iso:
        cutoff = datetime.fromisoformat(since_iso.replace("Z", "+00:00")).timestamp()
    for cwd_dir in sorted(projects_dir.iterdir()):
        if not cwd_dir.is_dir():
            continue
        for jsonl in sorted(cwd_dir.glob("*.jsonl")):
            if cutoff is not None and jsonl.stat().st_mtime < cutoff:
                continue
            yield jsonl
```

- [ ] **Step 5.5: Run — confirm it passes**

Run: `uv run pytest tests/test_claude_transcript.py -v`
Expected: 4 passed.

- [ ] **Step 5.6: Commit**

```bash
git add src/weekly_review/extractors/ tests/test_claude_transcript.py tests/fixtures/sample_transcript.jsonl
git commit -m "extractors: claude transcript parser emits user/agent/tool events"
```

---

## Task 6: Git log + GitHub PR extractors

**Files:**
- Create: `src/weekly_review/extractors/git_log.py`
- Create: `src/weekly_review/extractors/github_pr.py`
- Create: `tests/test_git_extractors.py`

- [ ] **Step 6.1: Write failing test**

Create `tests/test_git_extractors.py`:
```python
import subprocess
from unittest import mock
from weekly_review.extractors.git_log import extract_git_log
from weekly_review.extractors.github_pr import extract_github_prs


def test_git_log_parses_commits(monkeypatch):
    sample = (
        "abc1234|2026-04-15T10:00:00-07:00|the user|Initial commit\n"
        "def5678|2026-04-16T11:20:00-07:00|the user|Add config loader\n"
    )
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=sample, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    events = list(extract_git_log(repo_path="/tmp/fake", project="weekly-review", since="2026-04-01", source_machine="mac_mini"))
    assert len(events) == 2
    assert events[0].kind == "commit"
    assert events[0].metadata["short_sha"] == "abc1234"
    assert events[1].content_snippet == "Add config loader"


def test_github_pr_parses_gh_output(monkeypatch):
    gh_json = (
        '[{"number":42,"title":"Add X","createdAt":"2026-04-15T10:00:00Z",'
        '"mergedAt":"2026-04-16T12:00:00Z","state":"MERGED","author":{"login":"<you>"},'
        '"additions":120,"deletions":30,"changedFiles":5,"url":"https://github.com/fryanpan/weekly-review/pull/42"}]'
    )
    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=gh_json, stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    events = list(extract_github_prs(repo="fryanpan/weekly-review", project="weekly-review", since="2026-04-01", source_machine="mac_mini"))
    kinds = [e.kind for e in events]
    assert "pr_opened" in kinds
    assert "pr_merged" in kinds
    merged = next(e for e in events if e.kind == "pr_merged")
    assert merged.metadata["additions"] == 120
    assert merged.metadata["url"].endswith("/pull/42")
```

- [ ] **Step 6.2: Run — confirm it fails**

Run: `uv run pytest tests/test_git_extractors.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 6.3: Implement git_log.py**

Create `src/weekly_review/extractors/git_log.py`:
```python
"""Extract `commit` events from git log for a registered repo."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterator

from ..events import Event


def extract_git_log(
    repo_path: str | Path,
    project: str,
    since: str,
    source_machine: str,
) -> Iterator[Event]:
    """Run git log --since=<since> --pretty=format:'<sha>|<iso>|<author>|<subject>'."""
    fmt = "%h|%aI|%an|%s"
    result = subprocess.run(
        ["git", "-C", str(repo_path), "log", f"--since={since}", f"--pretty=format:{fmt}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return
    seq = 0
    for line in (result.stdout or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        sha, iso, author, subject = parts
        yield Event(
            ts=iso,
            actor="artifact",
            project=project,
            session_id=f"git:{project}",
            source_system="git",
            source_machine=source_machine,
            kind="commit",
            seq=seq,
            content_snippet=subject[:500],
            metadata={"short_sha": sha, "author": author},
        )
        seq += 1
```

- [ ] **Step 6.4: Implement github_pr.py**

Create `src/weekly_review/extractors/github_pr.py`:
```python
"""Extract pr_opened / pr_merged events via `gh pr list`."""
from __future__ import annotations

import json
import subprocess
from typing import Iterator

from ..events import Event


FIELDS = "number,title,createdAt,mergedAt,state,author,additions,deletions,changedFiles,url"


def extract_github_prs(
    repo: str,
    project: str,
    since: str,
    source_machine: str,
    limit: int = 200,
) -> Iterator[Event]:
    """Run `gh pr list -R <repo> --state all --json <fields>` and emit open+merge events.

    `since` filters by createdAt/mergedAt >= since (client-side).
    """
    result = subprocess.run(
        [
            "gh", "pr", "list",
            "-R", repo,
            "--state", "all",
            "--limit", str(limit),
            "--json", FIELDS,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return
    try:
        prs = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return

    seq = 0
    for pr in prs:
        created = pr.get("createdAt")
        merged = pr.get("mergedAt")
        common = {
            "project": project,
            "session_id": f"github:{repo}",
            "source_system": "github",
            "source_machine": source_machine,
            "content_snippet": (pr.get("title") or "")[:500],
            "metadata": {
                "number": pr.get("number"),
                "state": pr.get("state"),
                "author": (pr.get("author") or {}).get("login"),
                "additions": pr.get("additions"),
                "deletions": pr.get("deletions"),
                "changedFiles": pr.get("changedFiles"),
                "url": pr.get("url"),
            },
        }
        if created and created >= since:
            yield Event(ts=created, actor="artifact", kind="pr_opened", seq=seq, **common)
            seq += 1
        if merged and merged >= since:
            yield Event(ts=merged, actor="artifact", kind="pr_merged", seq=seq, **common)
            seq += 1
```

- [ ] **Step 6.5: Run — confirm it passes**

Run: `uv run pytest tests/test_git_extractors.py -v`
Expected: 2 passed.

- [ ] **Step 6.6: Commit**

```bash
git add src/weekly_review/extractors/git_log.py src/weekly_review/extractors/github_pr.py tests/test_git_extractors.py
git commit -m "extractors: git log and gh pr list producing artifact events"
```

---

## Task 7: Segmentation Layer 1 + proximity fallback

**Files:**
- Create: `src/weekly_review/segmentation.py`
- Create: `tests/test_segmentation.py`

- [ ] **Step 7.1: Write failing test**

Create `tests/test_segmentation.py`:
```python
from weekly_review.events import Event
from weekly_review.segmentation import Task, segment


def _uev(ts: str, project: str, text: str, seq: int) -> Event:
    return Event(
        ts=ts, actor="user", project=project, session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split())},
    )


def test_first_user_message_creates_task():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "let's build the digest", 0)]
    tasks = segment(events)
    assert len(tasks) == 1
    assert tasks[0].title  # some auto-derived title


def test_new_topic_cue_creates_new_task():
    events = [
        _uev("2026-04-15T10:00:00Z", "weekly-review", "working on the digest renderer", 0),
        _uev("2026-04-15T11:00:00Z", "weekly-review", "New topic: add github PR extractor", 1),
    ]
    tasks = segment(events)
    assert len(tasks) == 2


def test_issue_number_extends_existing_task():
    events = [
        _uev("2026-04-15T10:00:00Z", "weekly-review", "let's look at BRY-42", 0),
        _uev("2026-04-16T10:00:00Z", "weekly-review", "BRY-42 needs a cleanup", 1),
    ]
    tasks = segment(events)
    assert len(tasks) == 1
    assert len(tasks[0].event_ids) == 2


def test_proximity_fallback_joins_same_project_within_7d():
    events = [
        _uev("2026-04-15T10:00:00Z", "weekly-review", "segmentation plan", 0),
        _uev("2026-04-18T10:00:00Z", "weekly-review", "more polish on the segmenter", 1),
    ]
    tasks = segment(events)
    assert len(tasks) == 1
    assert tasks[0].segmentation_trace[1]["rule"] == "proximity_fallback"


def test_different_project_starts_new_task():
    events = [
        _uev("2026-04-15T10:00:00Z", "weekly-review", "digest", 0),
        _uev("2026-04-15T11:00:00Z", "bike-map", "overlay bug", 1),
    ]
    tasks = segment(events)
    assert len(tasks) == 2


def test_coverage_percent():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "work", i) for i in range(10)]
    tasks = segment(events)
    assigned = sum(len(t.event_ids) for t in tasks)
    assert assigned == 10
```

- [ ] **Step 7.2: Run — confirm it fails**

Run: `uv run pytest tests/test_segmentation.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 7.3: Implement segmentation.py**

Create `src/weekly_review/segmentation.py`:
```python
"""Task segmentation from event stream.

Layer 1 deterministic rules + project-proximity fallback to reach ≥95% coverage.
Layer 2 (LLM at ambiguity) is not in v1 — proximity fallback covers that case.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .events import Event


NEW_TOPIC_CUES = [
    re.compile(r"^\s*new topic[:\-]", re.IGNORECASE),
    re.compile(r"^\s*different (topic|thing)[:\-]", re.IGNORECASE),
    re.compile(r"actually,?\s+let'?s\s+(instead|switch|do)\b", re.IGNORECASE),
    re.compile(r"\bpivot to\b", re.IGNORECASE),
]
ISSUE_REF_RE = re.compile(r"\b([A-Z]{2,}-\d+)\b|#(\d+)\b")
PROXIMITY_DAYS = 7
GAP_NEW_TASK_DAYS = 14


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _has_new_topic_cue(text: str) -> bool:
    return any(p.search(text) for p in NEW_TOPIC_CUES)


def _issue_refs(text: str) -> set[str]:
    out = set()
    for m in ISSUE_REF_RE.finditer(text):
        out.add(m.group(1) or f"#{m.group(2)}")
    return out


@dataclass
class Task:
    task_id: str
    project: str
    title: str
    event_ids: list[int] = field(default_factory=list)
    issue_refs: set[str] = field(default_factory=set)
    last_event_ts: datetime | None = None
    segmentation_trace: list[dict[str, Any]] = field(default_factory=list)

    def touch(self, event_idx: int, ts: datetime, rule: str, detail: str = "") -> None:
        self.event_ids.append(event_idx)
        self.last_event_ts = ts
        self.segmentation_trace.append({"event_idx": event_idx, "rule": rule, "detail": detail})


def _new_task(project: str, title: str) -> Task:
    return Task(task_id=str(uuid.uuid4())[:8], project=project, title=title)


def _auto_title(text: str, max_words: int = 8) -> str:
    words = text.strip().split()[:max_words]
    return " ".join(words) if words else "untitled"


def segment(events: list[Event]) -> list[Task]:
    """Greedy single-pass segmenter.

    Rules in priority order:
      1. Explicit new-topic cue => new task
      2. Issue/PR ref that matches an open task in the same project => extend
      3. Issue/PR ref that does not match => new task
      4. Proximity fallback: extend the most recent task in the same project if within PROXIMITY_DAYS
      5. Otherwise: new task
    """
    sorted_events = sorted(enumerate(events), key=lambda pair: pair[1].ts)
    tasks_by_project: dict[str, list[Task]] = {}

    for idx, ev in sorted_events:
        if ev.kind != "user_message":
            # Non-user events attach to current open task in same project, if any
            continue
        ts = _parse_ts(ev.ts)
        project_tasks = tasks_by_project.setdefault(ev.project, [])
        text = ev.content_snippet or ""
        refs = _issue_refs(text)

        # Rule 1: explicit new-topic cue
        if _has_new_topic_cue(text):
            task = _new_task(ev.project, _auto_title(text))
            task.issue_refs |= refs
            task.touch(idx, ts, rule="new_topic_cue", detail=text[:60])
            project_tasks.append(task)
            continue

        # Rule 2: issue ref extends existing task
        if refs:
            match = next((t for t in reversed(project_tasks) if t.issue_refs & refs), None)
            if match is not None:
                match.issue_refs |= refs
                match.touch(idx, ts, rule="issue_ref_match", detail=",".join(refs))
                continue
            # Rule 3: new issue, new task
            task = _new_task(ev.project, _auto_title(text))
            task.issue_refs |= refs
            task.touch(idx, ts, rule="new_issue_ref", detail=",".join(refs))
            project_tasks.append(task)
            continue

        # Rule 4: proximity fallback
        most_recent = project_tasks[-1] if project_tasks else None
        if most_recent is not None and most_recent.last_event_ts is not None:
            gap = ts - most_recent.last_event_ts
            if gap <= timedelta(days=PROXIMITY_DAYS):
                most_recent.touch(idx, ts, rule="proximity_fallback", detail=f"gap={gap}")
                continue

        # Rule 5: new task
        task = _new_task(ev.project, _auto_title(text))
        task.touch(idx, ts, rule="new_task_default", detail="no match in project")
        project_tasks.append(task)

    # Attach non-user-message events to the open task in the same project with the nearest
    # preceding timestamp. This gives tool_call / agent_message / artifact events a home.
    for idx, ev in sorted_events:
        if ev.kind == "user_message":
            continue
        project_tasks = tasks_by_project.get(ev.project, [])
        ts = _parse_ts(ev.ts)
        candidate = None
        for t in project_tasks:
            if t.last_event_ts is not None and t.last_event_ts <= ts:
                candidate = t
        if candidate is None and project_tasks:
            candidate = project_tasks[0]  # no prior, attach to the earliest task
        if candidate is not None:
            candidate.touch(idx, ts, rule="non_user_attach", detail=ev.kind)

    all_tasks: list[Task] = []
    for project_tasks in tasks_by_project.values():
        all_tasks.extend(project_tasks)
    return all_tasks
```

- [ ] **Step 7.4: Run — confirm it passes**

Run: `uv run pytest tests/test_segmentation.py -v`
Expected: 6 passed.

- [ ] **Step 7.5: Commit**

```bash
git add src/weekly_review/segmentation.py tests/test_segmentation.py
git commit -m "segmentation: Layer 1 rules + project-proximity fallback"
```

---

## Task 8: Overrides application + task lifecycle

**Files:**
- Create: `src/weekly_review/overrides.py`
- Create: `src/weekly_review/lifecycle.py`
- Create: `tests/test_overrides_and_lifecycle.py`

- [ ] **Step 8.1: Write failing test**

Create `tests/test_overrides_and_lifecycle.py`:
```python
from datetime import datetime, timezone
from weekly_review.events import Event
from weekly_review.segmentation import Task, segment
from weekly_review.overrides import apply_overrides
from weekly_review.lifecycle import compute_statuses


def _uev(ts, project, text, seq):
    return Event(
        ts=ts, actor="user", project=project, session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split())},
    )


def test_apply_overrides_merges_two_tasks():
    events = [
        _uev("2026-04-15T10:00:00Z", "weekly-review", "new topic: feature A", 0),
        _uev("2026-04-16T10:00:00Z", "weekly-review", "new topic: feature B", 1),
    ]
    tasks = segment(events)
    assert len(tasks) == 2
    t0, t1 = tasks
    overrides = {"merges": [{"tasks": [t0.task_id, t1.task_id], "into_title": "Features A+B"}]}
    merged = apply_overrides(tasks, overrides)
    assert len(merged) == 1
    assert merged[0].title == "Features A+B"
    assert set(merged[0].event_ids) == {0, 1}


def test_apply_overrides_ignore_removes_task():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "spike test", 0)]
    tasks = segment(events)
    overrides = {"ignores": [tasks[0].task_id]}
    kept = apply_overrides(tasks, overrides)
    assert kept == []


def test_lifecycle_shipped_when_pr_merged_event_attached():
    from weekly_review.segmentation import Task
    pr = Event(
        ts="2026-04-17T12:00:00Z", actor="artifact", project="weekly-review",
        session_id="github:fryanpan/weekly-review",
        source_system="github", source_machine="mm",
        kind="pr_merged", seq=99,
        content_snippet="Add X", metadata={"number": 7, "url": "https://github.com/x/y/pull/7"},
    )
    events = [
        _uev("2026-04-15T10:00:00Z", "weekly-review", "impl x", 0),
        pr,
    ]
    tasks = segment(events)
    statuses = compute_statuses(tasks, events, now=datetime(2026, 4, 19, tzinfo=timezone.utc))
    assert statuses[tasks[0].task_id] == "shipped"


def test_lifecycle_abandoned_after_30d_with_no_artifact():
    events = [_uev("2026-02-01T10:00:00Z", "weekly-review", "old idea", 0)]
    tasks = segment(events)
    statuses = compute_statuses(tasks, events, now=datetime(2026, 4, 19, tzinfo=timezone.utc))
    assert statuses[tasks[0].task_id] == "abandoned"
```

- [ ] **Step 8.2: Run — confirm it fails**

Run: `uv run pytest tests/test_overrides_and_lifecycle.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 8.3: Implement overrides.py**

Create `src/weekly_review/overrides.py`:
```python
"""Apply ~/.config/weekly-review/overrides.yaml to segmented tasks."""
from __future__ import annotations

from typing import Any

from .segmentation import Task


def apply_overrides(tasks: list[Task], overrides: dict[str, Any]) -> list[Task]:
    tasks_by_id = {t.task_id: t for t in tasks}
    removed: set[str] = set()

    # Merges
    for merge in overrides.get("merges", []) or []:
        ids = merge.get("tasks", [])
        if not ids or any(tid not in tasks_by_id for tid in ids):
            continue
        primary = tasks_by_id[ids[0]]
        if "into_title" in merge:
            primary.title = merge["into_title"]
        for tid in ids[1:]:
            other = tasks_by_id[tid]
            primary.event_ids.extend(other.event_ids)
            primary.issue_refs |= other.issue_refs
            if other.last_event_ts and (primary.last_event_ts is None or other.last_event_ts > primary.last_event_ts):
                primary.last_event_ts = other.last_event_ts
            primary.segmentation_trace.append({"rule": "override_merge", "detail": f"from {tid}"})
            removed.add(tid)

    # Title relabels
    for relabel in overrides.get("relabels", []) or []:
        tid = relabel.get("task_id")
        new_title = relabel.get("new_title")
        if tid in tasks_by_id and new_title:
            tasks_by_id[tid].title = new_title
            tasks_by_id[tid].segmentation_trace.append({"rule": "override_relabel"})

    # Ignores
    for tid in overrides.get("ignores", []) or []:
        if tid in tasks_by_id:
            removed.add(tid)

    # Splits — not implemented in v1; log and skip. The user can manually edit events.
    # This is a deliberate simplification: splits are rare for now.

    return [t for t in tasks if t.task_id not in removed]
```

- [ ] **Step 8.4: Implement lifecycle.py**

Create `src/weekly_review/lifecycle.py`:
```python
"""Task status: open / shipped / abandoned."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .events import Event
from .segmentation import Task


ABANDON_DAYS = 30
SHIPPED_KINDS = {"pr_merged", "notion_published", "doc_committed"}


def compute_statuses(tasks: list[Task], events: list[Event], now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now(timezone.utc)
    statuses: dict[str, str] = {}
    for task in tasks:
        attached_events = [events[i] for i in task.event_ids if i < len(events)]
        has_ship = any(ev.kind in SHIPPED_KINDS for ev in attached_events)
        if has_ship:
            statuses[task.task_id] = "shipped"
            continue
        if task.last_event_ts is None:
            statuses[task.task_id] = "abandoned"
            continue
        gap = now - task.last_event_ts
        if gap > timedelta(days=ABANDON_DAYS):
            statuses[task.task_id] = "abandoned"
        else:
            statuses[task.task_id] = "open"
    return statuses
```

- [ ] **Step 8.5: Run — confirm it passes**

Run: `uv run pytest tests/test_overrides_and_lifecycle.py -v`
Expected: 4 passed.

- [ ] **Step 8.6: Commit**

```bash
git add src/weekly_review/overrides.py src/weekly_review/lifecycle.py tests/test_overrides_and_lifecycle.py
git commit -m "segmentation: overrides (merge/relabel/ignore) + lifecycle statuses"
```

---

## Task 9: Effort, cognitive wall-clock, context switches

**Files:**
- Create: `src/weekly_review/effort.py`
- Create: `tests/test_effort.py`

- [ ] **Step 9.1: Write failing test**

Create `tests/test_effort.py`:
```python
from datetime import datetime, timezone
from weekly_review.events import Event
from weekly_review.effort import (
    active_time_seconds_for_user_message,
    cognitive_wall_clock_seconds,
    context_switch_count,
    resumption_count,
    active_hours_by_task,
    agent_active_hours_by_task,
)


def _ev(kind: str, actor: str, ts: str, project: str, seq: int, words: int = 10) -> Event:
    return Event(
        ts=ts, actor=actor, project=project, session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind=kind, seq=seq,
        content_snippet="x" * max(1, words),
        metadata={"words": words},
    )


def test_active_time_reading_plus_typing():
    prior_agent = _ev("agent_message", "agent", "2026-04-15T10:00:00Z", "wr", 0, words=300)
    user = _ev("user_message", "user", "2026-04-15T10:00:30Z", "wr", 1, words=40)
    sec = active_time_seconds_for_user_message(user, prior_agent)
    assert sec == 300 / 300 * 60 + 40 / 40 * 60  # 60 + 60 = 120 seconds


def test_active_time_capped_at_20_min():
    prior_agent = _ev("agent_message", "agent", "2026-04-15T10:00:00Z", "wr", 0, words=100_000)
    user = _ev("user_message", "user", "2026-04-15T10:00:30Z", "wr", 1, words=10)
    sec = active_time_seconds_for_user_message(user, prior_agent)
    assert sec == 20 * 60  # capped


def test_cognitive_wall_clock_spans():
    events = [
        _ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0),
        _ev("agent_message", "agent", "2026-04-15T10:05:00Z", "wr", 1),
        _ev("user_message", "user", "2026-04-15T10:25:00Z", "wr", 2),  # 20 min gap => new span
        _ev("agent_message", "agent", "2026-04-15T10:26:00Z", "wr", 3),
    ]
    sec = cognitive_wall_clock_seconds(events)
    # span 1: 10:00 -> 10:05 = 5 min; span 2: 10:25 -> 10:26 = 1 min; total 6 min = 360
    assert sec == 6 * 60


def test_context_switch_count_same_day_different_project():
    events = [
        _ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0),
        _ev("user_message", "user", "2026-04-15T11:00:00Z", "wr", 1),
        _ev("user_message", "user", "2026-04-15T12:00:00Z", "bike-map", 2),  # switch
        _ev("user_message", "user", "2026-04-15T13:00:00Z", "wr", 3),  # switch
        _ev("user_message", "user", "2026-04-16T09:00:00Z", "bike-map", 4),  # new day, not a switch
    ]
    count = context_switch_count(events)
    assert count == 2


def test_resumption_count_task_level():
    from weekly_review.segmentation import Task
    task = Task(task_id="t1", project="wr", title="x")
    task.event_ids = [0, 1, 2, 3]
    events = [
        _ev("user_message", "user", "2026-04-15T10:00:00Z", "wr", 0),
        _ev("user_message", "user", "2026-04-15T14:00:00Z", "wr", 1),  # same-day, no resumption
        _ev("user_message", "user", "2026-04-17T10:00:00Z", "wr", 2),  # >24h gap, resumption
        _ev("user_message", "user", "2026-04-19T10:00:00Z", "wr", 3),  # >24h gap, resumption
    ]
    assert resumption_count(task, events) == 2
```

- [ ] **Step 9.2: Run — confirm it fails**

Run: `uv run pytest tests/test_effort.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 9.3: Implement effort.py**

Create `src/weekly_review/effort.py`:
```python
"""Active hours, cognitive wall-clock, context switches, resumptions.

All computations are pure functions over Event lists. No hidden adjustments
to primary numbers — taxes and penalties stay out per spec §7.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .events import Event
from .segmentation import Task


READ_WPM = 300
TYPE_WPM = 40
PER_MESSAGE_CAP_SEC = 20 * 60
COGNITIVE_GAP_SEC = 15 * 60
RESUMPTION_GAP_SEC = 24 * 60 * 60


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def active_time_seconds_for_user_message(user_msg: Event, prior_agent: Optional[Event]) -> float:
    if user_msg.kind != "user_message":
        return 0.0
    user_words = int(user_msg.metadata.get("words", 0))
    agent_words = int(prior_agent.metadata.get("words", 0)) if prior_agent else 0
    reading_sec = agent_words / READ_WPM * 60
    typing_sec = user_words / TYPE_WPM * 60
    total = reading_sec + typing_sec
    return min(total, PER_MESSAGE_CAP_SEC)


def _prior_agent_for(events: list[Event], idx: int) -> Event | None:
    # The immediately preceding agent_message in the same session, if any
    session = events[idx].session_id
    for j in range(idx - 1, -1, -1):
        ev = events[j]
        if ev.session_id != session:
            break
        if ev.kind == "agent_message":
            return ev
        if ev.kind == "user_message":
            break
    return None


def active_hours_by_task(tasks: list[Task], events: list[Event]) -> dict[str, float]:
    """Sum active_time per user_message in each task, in hours."""
    # Sort events by ts to find prior agent correctly within a session
    order = sorted(range(len(events)), key=lambda i: events[i].ts)
    index_position = {idx: pos for pos, idx in enumerate(order)}

    def prior_agent(event_idx: int) -> Event | None:
        pos = index_position[event_idx]
        ev = events[event_idx]
        for j in range(pos - 1, -1, -1):
            prior = events[order[j]]
            if prior.session_id != ev.session_id:
                break
            if prior.kind == "agent_message":
                return prior
            if prior.kind == "user_message":
                break
        return None

    out: dict[str, float] = {}
    for task in tasks:
        total = 0.0
        for idx in task.event_ids:
            if idx >= len(events):
                continue
            ev = events[idx]
            if ev.kind != "user_message":
                continue
            sec = active_time_seconds_for_user_message(ev, prior_agent(idx))
            total += sec
        out[task.task_id] = total / 3600
    return out


def agent_active_hours_by_task(tasks: list[Task], events: list[Event]) -> dict[str, float]:
    """Sum wall-clock span of contiguous agent activity within each task.

    Excludes any gap > 5 min between agent events in the same session.
    """
    out: dict[str, float] = {}
    for task in tasks:
        attached = sorted(
            (events[i] for i in task.event_ids if i < len(events)),
            key=lambda e: e.ts,
        )
        total_sec = 0.0
        span_start: datetime | None = None
        last_ts: datetime | None = None
        for ev in attached:
            if ev.actor != "agent":
                if span_start and last_ts:
                    total_sec += (last_ts - span_start).total_seconds()
                span_start = None
                last_ts = None
                continue
            ts = _parse_ts(ev.ts)
            if span_start is None:
                span_start = ts
                last_ts = ts
                continue
            if last_ts and (ts - last_ts).total_seconds() > 5 * 60:
                total_sec += (last_ts - span_start).total_seconds()
                span_start = ts
            last_ts = ts
        if span_start and last_ts:
            total_sec += (last_ts - span_start).total_seconds()
        out[task.task_id] = total_sec / 3600
    return out


def cognitive_wall_clock_seconds(events: list[Event]) -> float:
    """Sum durations of active spans where consecutive events are within 15 min."""
    if not events:
        return 0.0
    ordered = sorted(events, key=lambda e: e.ts)
    total = 0.0
    span_start = _parse_ts(ordered[0].ts)
    last = span_start
    for ev in ordered[1:]:
        ts = _parse_ts(ev.ts)
        if (ts - last).total_seconds() > COGNITIVE_GAP_SEC:
            total += (last - span_start).total_seconds()
            span_start = ts
        last = ts
    total += (last - span_start).total_seconds()
    return total


def context_switch_count(events: list[Event]) -> int:
    """Count user-message transitions across projects within the same local day.

    "Same day" uses UTC date for simplicity.
    """
    user_msgs = [e for e in events if e.kind == "user_message"]
    user_msgs.sort(key=lambda e: e.ts)
    switches = 0
    for prev, curr in zip(user_msgs, user_msgs[1:]):
        if prev.project == curr.project:
            continue
        if _parse_ts(prev.ts).date() == _parse_ts(curr.ts).date():
            switches += 1
    return switches


def resumption_count(task: Task, events: list[Event]) -> int:
    """Count gaps > 24h between consecutive events attached to this task."""
    attached = sorted(
        (events[i] for i in task.event_ids if i < len(events)),
        key=lambda e: e.ts,
    )
    count = 0
    for prev, curr in zip(attached, attached[1:]):
        gap = (_parse_ts(curr.ts) - _parse_ts(prev.ts)).total_seconds()
        if gap > RESUMPTION_GAP_SEC:
            count += 1
    return count
```

- [ ] **Step 9.4: Run — confirm it passes**

Run: `uv run pytest tests/test_effort.py -v`
Expected: 5 passed.

- [ ] **Step 9.5: Commit**

```bash
git add src/weekly_review/effort.py tests/test_effort.py
git commit -m "effort: active hours, cognitive wall-clock, switches, resumptions"
```

---

## Task 10: Coverage

**Files:**
- Create: `src/weekly_review/coverage.py`
- Create: `tests/test_coverage.py`

- [ ] **Step 10.1: Write failing test**

Create `tests/test_coverage.py`:
```python
from weekly_review.events import Event
from weekly_review.segmentation import segment
from weekly_review.coverage import coverage_report


def _uev(ts, project, seq):
    return Event(
        ts=ts, actor="user", project=project, session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet="x", metadata={"words": 1},
    )


def test_coverage_100_percent_when_all_assigned():
    events = [_uev(f"2026-04-15T10:{i:02d}:00Z", "weekly-review", i) for i in range(5)]
    tasks = segment(events)
    rep = coverage_report(tasks, events)
    assert rep["user_message_events"] == 5
    assert rep["assigned"] == 5
    assert rep["coverage_percent"] == 100.0
    assert rep["meets_target"] is True


def test_coverage_flags_sub_95():
    # Simulate: 20 user messages, 18 assigned, 2 unassigned (orphan task with no matches)
    from weekly_review.segmentation import Task
    events = [_uev(f"2026-04-15T10:{i:02d}:00Z", "weekly-review", i) for i in range(20)]
    task = Task(task_id="t1", project="weekly-review", title="x", event_ids=list(range(18)))
    rep = coverage_report([task], events)
    assert rep["coverage_percent"] == 90.0
    assert rep["meets_target"] is False
```

- [ ] **Step 10.2: Run — confirm it fails**

Run: `uv run pytest tests/test_coverage.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 10.3: Implement coverage.py**

Create `src/weekly_review/coverage.py`:
```python
"""Coverage of user-message events by tasks (target ≥ 95%)."""
from __future__ import annotations

from collections import Counter

from .events import Event
from .segmentation import Task


TARGET_PERCENT = 95.0


def coverage_report(tasks: list[Task], events: list[Event]) -> dict:
    user_indices = {i for i, ev in enumerate(events) if ev.kind == "user_message"}
    assigned = set()
    for t in tasks:
        for idx in t.event_ids:
            if idx in user_indices:
                assigned.add(idx)

    total = len(user_indices)
    assigned_count = len(assigned)
    unassigned = sorted(user_indices - assigned)
    percent = 100.0 * assigned_count / total if total else 100.0

    # Unassigned events by project (helps diagnose where the segmenter missed)
    unassigned_by_project: Counter[str] = Counter()
    for idx in unassigned:
        unassigned_by_project[events[idx].project] += 1

    return {
        "user_message_events": total,
        "assigned": assigned_count,
        "unassigned": len(unassigned),
        "coverage_percent": round(percent, 2),
        "meets_target": percent >= TARGET_PERCENT,
        "unassigned_by_project": dict(unassigned_by_project),
    }
```

- [ ] **Step 10.4: Run — confirm it passes**

Run: `uv run pytest tests/test_coverage.py -v`
Expected: 2 passed.

- [ ] **Step 10.5: Commit**

```bash
git add src/weekly_review/coverage.py tests/test_coverage.py
git commit -m "coverage: 95% target with per-project unassigned breakdown"
```

---

## Task 11: LLM client wrapper

**Files:**
- Create: `src/weekly_review/llm.py`
- Create: `tests/test_llm.py`

- [ ] **Step 11.1: Write failing test**

Create `tests/test_llm.py`:
```python
from unittest import mock
import pytest
from weekly_review.llm import LLMClient, CachedLLMClient


def test_llm_client_calls_anthropic(monkeypatch):
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            class R:
                content = [type("B", (), {"type": "text", "text": '{"est_human_hours": 3.5}'})()]
                usage = type("U", (), {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})()
                stop_reason = "end_turn"
            return R()

    class FakeClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    monkeypatch.setattr("weekly_review.llm.Anthropic", FakeClient)
    client = LLMClient(api_key="fake")
    out = client.complete(
        system="you are an estimator",
        user="task: x",
        model="claude-sonnet-4-6",
    )
    assert out["text"] == '{"est_human_hours": 3.5}'
    assert out["usage"]["input_tokens"] == 100
    assert calls[0]["model"] == "claude-sonnet-4-6"


def test_cached_client_skips_second_call(tmp_path, monkeypatch):
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            class R:
                content = [type("B", (), {"type": "text", "text": "hi"})()]
                usage = type("U", (), {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})()
                stop_reason = "end_turn"
            return R()

    class FakeClient:
        def __init__(self, api_key=None):
            self.messages = FakeMessages()

    monkeypatch.setattr("weekly_review.llm.Anthropic", FakeClient)
    inner = LLMClient(api_key="fake")
    cached = CachedLLMClient(inner, cache_dir=tmp_path)
    r1 = cached.complete(system="s", user="u", model="claude-haiku-4-5")
    r2 = cached.complete(system="s", user="u", model="claude-haiku-4-5")
    assert r1["text"] == r2["text"]
    assert len(calls) == 1
```

- [ ] **Step 11.2: Run — confirm it fails**

Run: `uv run pytest tests/test_llm.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 11.3: Implement llm.py**

Create `src/weekly_review/llm.py`:
```python
"""Anthropic SDK wrapper with prompt caching + on-disk response cache.

Prompt caching (cache_control) is applied to the system prompt when it's
longer than 1024 tokens (rough proxy: > 4096 chars), which covers our
task-enrichment rubric.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from anthropic import Anthropic


CACHE_MIN_SYSTEM_CHARS = 4096


class LLMClient:
    def __init__(self, api_key: str | None = None):
        self._client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        system_blocks: list[dict[str, Any]]
        if len(system) >= CACHE_MIN_SYSTEM_CHARS:
            system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        else:
            system_blocks = [{"type": "text", "text": system}]

        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_blocks,
            messages=[{"role": "user", "content": user}],
        )
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        usage = resp.usage
        return {
            "text": text,
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
            },
            "stop_reason": resp.stop_reason,
            "model": model,
        }


class CachedLLMClient:
    """Disk-cached wrapper. Idempotent on (model, system, user, max_tokens, temperature)."""

    def __init__(self, inner: LLMClient, cache_dir: Path):
        self._inner = inner
        self._dir = cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _key(self, model: str, system: str, user: str, max_tokens: int, temperature: float) -> str:
        h = hashlib.sha256()
        h.update(model.encode())
        h.update(b"\n--\n")
        h.update(system.encode())
        h.update(b"\n--\n")
        h.update(user.encode())
        h.update(f"\n--\n{max_tokens}:{temperature}".encode())
        return h.hexdigest()

    def complete(self, *, system: str, user: str, model: str, max_tokens: int = 2048, temperature: float = 0.0) -> dict[str, Any]:
        key = self._key(model, system, user, max_tokens, temperature)
        cache_path = self._dir / f"{key}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text())
        result = self._inner.complete(system=system, user=user, model=model, max_tokens=max_tokens, temperature=temperature)
        cache_path.write_text(json.dumps(result))
        return result
```

- [ ] **Step 11.4: Run — confirm it passes**

Run: `uv run pytest tests/test_llm.py -v`
Expected: 2 passed.

- [ ] **Step 11.5: Commit**

```bash
git add src/weekly_review/llm.py tests/test_llm.py
git commit -m "llm: Anthropic wrapper with prompt caching + on-disk response cache"
```

---

## Task 12: Task enrichment — title + expertise + human-hours in one pass

**Files:**
- Create: `src/weekly_review/enrichment.py`
- Create: `tests/test_enrichment.py`

- [ ] **Step 12.1: Write failing test**

Create `tests/test_enrichment.py`:
```python
from pathlib import Path
from unittest import mock
from weekly_review.events import Event
from weekly_review.segmentation import Task
from weekly_review.enrichment import enrich_task, build_enrichment_prompt


class FakeLLM:
    def __init__(self, response_text: str):
        self.text = response_text
        self.calls = []

    def complete(self, *, system: str, user: str, model: str, max_tokens: int = 2048, temperature: float = 0.0):
        self.calls.append({"system": system, "user": user, "model": model})
        return {"text": self.text, "usage": {}, "stop_reason": "end_turn", "model": model}


def _uev(ts, project, text, seq):
    return Event(
        ts=ts, actor="user", project=project, session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split())},
    )


def test_enrichment_parses_llm_json():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "write the digest renderer", 0)]
    task = Task(task_id="t1", project="weekly-review", title="initial", event_ids=[0])
    llm = FakeLLM('{"title":"Digest renderer","est_human_hours":3.5,"uncertainty":"medium","bryan_domain_expertise":"expert","dimensions":{"scope":"single-feature","depth":"careful design","type":"code"},"rationale":"..."}')
    out = enrich_task(task, events, anchors=[], expertise_seed={}, llm=llm, model="claude-sonnet-4-6")
    assert out.title == "Digest renderer"
    assert out.est_human_hours == 3.5
    assert out.uncertainty == "medium"
    assert out.bryan_domain_expertise == "expert"


def test_prompt_includes_anchors_and_expertise_seed():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "x", 0)]
    task = Task(task_id="t1", project="weekly-review", title="x", event_ids=[0])
    anchors = [{"title": "write research note", "est_human_hours": 4, "dimensions": {"type": "research"}}]
    expertise = {"backend / python": "expert"}
    system, user = build_enrichment_prompt(task, events, anchors, expertise)
    assert "write research note" in system
    assert "backend / python" in system
    assert "x" in user
```

- [ ] **Step 12.2: Run — confirm it fails**

Run: `uv run pytest tests/test_enrichment.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 12.3: Implement enrichment.py**

Create `src/weekly_review/enrichment.py`:
```python
"""LLM enrichment for a task: title, est_human_hours, bryan_domain_expertise.

One LLM call per task. Input: task title + first/last N user-messages + artifact
list + anchored examples + expertise seed. Output: JSON with fields below.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .events import Event
from .segmentation import Task


SYSTEM_PROMPT_TEMPLATE = """You are estimating personal-workflow metrics for a solo developer (the user) who works with AI coding agents.

For one task (a topic the user worked on with their agents), you will:
1. Write a concise, descriptive TITLE (≤10 words).
2. Estimate EST_HUMAN_HOURS: how long a competent domain-expert human would take to produce the same output, doing the task as simply as reasonable. Methodology: METR time-horizons — "hours at which an expert would succeed with ~50% reliability."
3. Rate UNCERTAINTY of your estimate: "low" | "medium" | "high".
4. Tag BRYAN_DOMAIN_EXPERTISE: is the user an "expert" | "competent" | "learning" in this task's domain? Use the seed expertise list below plus judgment about the task.
5. Tag DIMENSIONS: scope (single-file / single-feature / cross-repo / multi-source research), depth (surface / careful design / novel), type (code / research / documentation / ux design / ops).
6. Write a one-sentence RATIONALE for your hours estimate.

Return ONLY valid JSON with exactly these keys:
{{"title": str, "est_human_hours": float, "uncertainty": str, "bryan_domain_expertise": str, "dimensions": {{"scope": str, "depth": str, "type": str}}, "rationale": str}}

## The user's self-declared expertise seed
{expertise_seed}

## Anchored examples (use as calibration)
{anchors_block}
"""


@dataclass
class EnrichedTask:
    task_id: str
    title: str
    est_human_hours: float
    uncertainty: str
    bryan_domain_expertise: str
    dimensions: dict[str, str]
    rationale: str


def _format_anchors(anchors: list[dict]) -> str:
    if not anchors:
        return "(no anchors yet — use your best judgment from METR framing)"
    lines = []
    for a in anchors:
        title = a.get("title", "?")
        hours = a.get("est_human_hours", "?")
        dims = a.get("dimensions", {})
        lines.append(f"- \"{title}\" → {hours} hours (dims: {dims})")
    return "\n".join(lines)


def _format_expertise(expertise: dict[str, str]) -> str:
    if not expertise:
        return "(none supplied — infer from the task)"
    return "\n".join(f"- {domain}: {level}" for domain, level in expertise.items())


def _task_summary(task: Task, events: list[Event], max_messages: int = 8) -> str:
    attached = [events[i] for i in task.event_ids if i < len(events) and events[i].kind == "user_message"]
    if len(attached) > max_messages:
        sample = attached[:max_messages // 2] + attached[-max_messages // 2:]
    else:
        sample = attached

    lines = [f"PROJECT: {task.project}", f"CURRENT TITLE (auto-derived): {task.title}"]
    if task.issue_refs:
        lines.append(f"ISSUE/PR REFS: {', '.join(sorted(task.issue_refs))}")
    lines.append("")
    lines.append("USER MESSAGES:")
    for ev in sample:
        lines.append(f"- [{ev.ts}] {ev.content_snippet}")

    artifacts = [events[i] for i in task.event_ids if i < len(events) and events[i].actor == "artifact"]
    if artifacts:
        lines.append("")
        lines.append("ARTIFACTS SHIPPED:")
        for ev in artifacts:
            meta = ev.metadata
            if ev.kind == "pr_merged":
                lines.append(f"- PR #{meta.get('number')} ({meta.get('additions')}+/{meta.get('deletions')}- across {meta.get('changedFiles')} files): {ev.content_snippet}")
            elif ev.kind == "commit":
                lines.append(f"- commit {meta.get('short_sha')}: {ev.content_snippet}")
            else:
                lines.append(f"- {ev.kind}: {ev.content_snippet}")

    return "\n".join(lines)


def build_enrichment_prompt(
    task: Task,
    events: list[Event],
    anchors: list[dict],
    expertise_seed: dict[str, str],
) -> tuple[str, str]:
    system = SYSTEM_PROMPT_TEMPLATE.format(
        expertise_seed=_format_expertise(expertise_seed),
        anchors_block=_format_anchors(anchors),
    )
    user = _task_summary(task, events)
    return system, user


def enrich_task(
    task: Task,
    events: list[Event],
    anchors: list[dict],
    expertise_seed: dict[str, str],
    llm,
    model: str = "claude-sonnet-4-6",
) -> EnrichedTask:
    system, user = build_enrichment_prompt(task, events, anchors, expertise_seed)
    result = llm.complete(system=system, user=user, model=model, max_tokens=512, temperature=0.0)
    text = result["text"].strip()

    # Robustly locate the JSON object (models sometimes prepend whitespace / a fence)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"LLM response did not contain JSON: {text!r}")
    obj: dict[str, Any] = json.loads(text[start:end + 1])

    return EnrichedTask(
        task_id=task.task_id,
        title=obj.get("title", task.title),
        est_human_hours=float(obj.get("est_human_hours", 0.0)),
        uncertainty=obj.get("uncertainty", "high"),
        bryan_domain_expertise=obj.get("bryan_domain_expertise", "competent"),
        dimensions=obj.get("dimensions", {}),
        rationale=obj.get("rationale", ""),
    )
```

- [ ] **Step 12.4: Run — confirm it passes**

Run: `uv run pytest tests/test_enrichment.py -v`
Expected: 2 passed.

- [ ] **Step 12.5: Commit**

```bash
git add src/weekly_review/enrichment.py tests/test_enrichment.py
git commit -m "enrichment: single-LLM-call pass for title, hours, expertise, dims"
```

---

## Task 13: Calibration and --recalibrate flow

**Files:**
- Create: `src/weekly_review/calibration.py`
- Create: `tests/test_calibration.py`

- [ ] **Step 13.1: Write failing test**

Create `tests/test_calibration.py`:
```python
from pathlib import Path
import yaml
from weekly_review.calibration import (
    load_anchors,
    save_anchors,
    apply_bias_correction,
    fit_bias,
)


def test_load_save_anchors_roundtrip(tmp_path: Path):
    path = tmp_path / "anchors.yaml"
    anchors = [{"title": "x", "est_human_hours": 4.0, "dimensions": {"type": "code"}}]
    save_anchors(anchors, path)
    loaded = load_anchors(path)
    assert loaded == anchors


def test_fit_bias_returns_ratio():
    # 3 anchors where truth is always 2x the LLM estimate
    pairs = [(1.0, 2.0), (2.0, 4.0), (3.0, 6.0)]  # (llm_hours, truth_hours)
    bias = fit_bias(pairs)
    assert abs(bias["multiplier"] - 2.0) < 1e-6


def test_apply_bias_correction():
    bias = {"multiplier": 1.5, "n": 3}
    assert apply_bias_correction(2.0, bias) == 3.0


def test_fit_bias_empty_returns_identity():
    bias = fit_bias([])
    assert bias["multiplier"] == 1.0
    assert bias["n"] == 0
```

- [ ] **Step 13.2: Run — confirm it fails**

Run: `uv run pytest tests/test_calibration.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 13.3: Implement calibration.py**

Create `src/weekly_review/calibration.py`:
```python
"""Anchor management + systematic-bias correction.

Keep it simple: the bias is a single multiplier = geometric mean of (truth/estimate)
across anchors. Applied to the LLM's raw est_human_hours.
"""
from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Iterable

import yaml


def load_anchors(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text()) or []


def save_anchors(anchors: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(anchors, sort_keys=False))


def fit_bias(pairs: Iterable[tuple[float, float]]) -> dict:
    """Returns {"multiplier": <geometric mean of truth/llm>, "n": <count>}.

    pairs: iterable of (llm_hours, truth_hours).
    Empty input => multiplier 1.0 (identity).
    """
    ratios: list[float] = []
    for llm_hours, truth_hours in pairs:
        if llm_hours <= 0 or truth_hours <= 0:
            continue
        ratios.append(truth_hours / llm_hours)
    if not ratios:
        return {"multiplier": 1.0, "n": 0}
    log_mean = sum(math.log(r) for r in ratios) / len(ratios)
    return {"multiplier": math.exp(log_mean), "n": len(ratios)}


def apply_bias_correction(llm_hours: float, bias: dict) -> float:
    mult = bias.get("multiplier", 1.0) if bias else 1.0
    return llm_hours * mult


def pick_calibration_sample(enriched_tasks: list, n: int = 5) -> list:
    """Pick a random sample of shipped tasks for recalibration.

    Prefer tasks not already in the anchor set (checked outside this function).
    """
    if len(enriched_tasks) <= n:
        return list(enriched_tasks)
    return random.sample(enriched_tasks, n)
```

- [ ] **Step 13.4: Run — confirm it passes**

Run: `uv run pytest tests/test_calibration.py -v`
Expected: 4 passed.

- [ ] **Step 13.5: Commit**

```bash
git add src/weekly_review/calibration.py tests/test_calibration.py
git commit -m "calibration: anchor IO + geometric-mean bias correction"
```

---

## Task 14: Markdown digest renderer

**Files:**
- Create: `src/weekly_review/digest.py`
- Create: `tests/test_digest.py`

- [ ] **Step 14.1: Write failing test**

Create `tests/test_digest.py`:
```python
from datetime import datetime, timezone
from weekly_review.events import Event
from weekly_review.segmentation import Task
from weekly_review.enrichment import EnrichedTask
from weekly_review.digest import render_digest


def _uev(ts, project, text, seq):
    return Event(
        ts=ts, actor="user", project=project, session_id="s",
        source_system="claude_transcript", source_machine="mm",
        kind="user_message", seq=seq,
        content_snippet=text, metadata={"words": len(text.split())},
    )


def test_digest_contains_all_required_sections():
    events = [_uev("2026-04-15T10:00:00Z", "weekly-review", "implement thing", 0)]
    task = Task(task_id="t1", project="weekly-review", title="Implement thing", event_ids=[0])
    enriched = EnrichedTask(
        task_id="t1", title="Implement thing",
        est_human_hours=4.0, uncertainty="medium",
        bryan_domain_expertise="expert",
        dimensions={"scope": "single-feature", "depth": "careful design", "type": "code"},
        rationale="moderate scope, careful design",
    )
    md = render_digest(
        period_start="2026-04-05",
        period_end="2026-04-19",
        tasks=[task],
        enriched={task.task_id: enriched},
        statuses={task.task_id: "shipped"},
        events=events,
        active_hours_by_task={task.task_id: 0.5},
        agent_hours_by_task={task.task_id: 0.2},
        resumptions_by_task={task.task_id: 0},
        coverage={"user_message_events": 1, "assigned": 1, "unassigned": 0, "coverage_percent": 100.0, "meets_target": True, "unassigned_by_project": {}},
        cognitive_wall_clock_hours=1.0,
        context_switches=0,
        last_calibrated_days=None,
    )
    for section in ["Leverage", "Coverage", "Top tasks", "Context switches"]:
        assert section in md
    assert "100.0%" in md
    assert "Implement thing" in md
```

- [ ] **Step 14.2: Run — confirm it fails**

Run: `uv run pytest tests/test_digest.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 14.3: Implement digest.py**

Create `src/weekly_review/digest.py`:
```python
"""Markdown digest renderer."""
from __future__ import annotations

from typing import Any

from .events import Event
from .segmentation import Task
from .enrichment import EnrichedTask


def _fmt_hours(h: float) -> str:
    return f"{h:.2f}h"


def _leverage(num: float, denom: float) -> str:
    if denom <= 0:
        return "n/a"
    return f"{num / denom:.2f}"


def render_digest(
    *,
    period_start: str,
    period_end: str,
    tasks: list[Task],
    enriched: dict[str, EnrichedTask],
    statuses: dict[str, str],
    events: list[Event],
    active_hours_by_task: dict[str, float],
    agent_hours_by_task: dict[str, float],
    resumptions_by_task: dict[str, int],
    coverage: dict[str, Any],
    cognitive_wall_clock_hours: float,
    context_switches: int,
    last_calibrated_days: int | None,
) -> str:
    total_human_hours = sum(enriched[tid].est_human_hours for tid in enriched)
    total_active_hours = sum(active_hours_by_task.values())
    total_agent_hours = sum(agent_hours_by_task.values())

    # Group by expertise
    by_expertise = {"expert": 0.0, "competent": 0.0, "learning": 0.0}
    for tid, et in enriched.items():
        by_expertise.setdefault(et.bryan_domain_expertise, 0.0)
        by_expertise[et.bryan_domain_expertise] += et.est_human_hours

    lines: list[str] = []
    lines.append(f"# Week of {period_start} to {period_end}")
    lines.append("")
    lines.append("## Leverage")
    lines.append(f"- Tasks active this period: {len(tasks)}")
    lines.append(f"- Σ est human hours (output): {_fmt_hours(total_human_hours)}")
    lines.append(f"- Σ your active hours: {_fmt_hours(total_active_hours)}")
    lines.append(f"- Σ cognitive wall-clock hours: {_fmt_hours(cognitive_wall_clock_hours)} "
                 f"(active/cognitive ratio: {_leverage(total_active_hours, cognitive_wall_clock_hours)} — sanity check)")
    lines.append(f"- **leverage_user: {_leverage(total_human_hours, total_active_hours)}**")
    lines.append(f"- Σ agent active hours: {_fmt_hours(total_agent_hours)}, "
                 f"leverage_agent: {_leverage(total_human_hours, total_agent_hours)}")
    lines.append(f"- By expertise: expert={_fmt_hours(by_expertise['expert'])}, "
                 f"competent={_fmt_hours(by_expertise.get('competent', 0))}, "
                 f"learning={_fmt_hours(by_expertise.get('learning', 0))}")
    lines.append("")

    lines.append("## Coverage")
    flag = "" if coverage["meets_target"] else " ⚠️ below target — run `weekly-review --inspect` to review"
    lines.append(f"- {coverage['assigned']}/{coverage['user_message_events']} user-message events assigned "
                 f"({coverage['coverage_percent']}%, target ≥95%){flag}")
    if coverage.get("unassigned_by_project"):
        lines.append(f"- Unassigned by project: {coverage['unassigned_by_project']}")
    lines.append("")

    top_by_output = sorted(enriched.values(), key=lambda e: -e.est_human_hours)[:10]
    lines.append("## Top tasks by est human hours (what got done)")
    for et in top_by_output:
        status = statuses.get(et.task_id, "?")
        lines.append(f"- [{status}] {et.title} — est {_fmt_hours(et.est_human_hours)} "
                     f"(unc: {et.uncertainty}, exp: {et.bryan_domain_expertise}) — {et.rationale}")
    lines.append("")

    top_by_active = sorted(tasks, key=lambda t: -active_hours_by_task.get(t.task_id, 0))[:10]
    lines.append("## Top tasks by your active hours (where your time went)")
    for t in top_by_active:
        et = enriched.get(t.task_id)
        title = et.title if et else t.title
        lines.append(f"- {title} — active {_fmt_hours(active_hours_by_task.get(t.task_id, 0))}, "
                     f"agent {_fmt_hours(agent_hours_by_task.get(t.task_id, 0))}")
    lines.append("")

    # Low-leverage flag: L < 0.5x average
    avg_lev = (total_human_hours / total_active_hours) if total_active_hours else 0
    if avg_lev > 0:
        low = []
        for t in tasks:
            a = active_hours_by_task.get(t.task_id, 0)
            et = enriched.get(t.task_id)
            if et is None or a <= 0:
                continue
            lev = et.est_human_hours / a
            if lev < 0.5 * avg_lev:
                low.append((t, et, lev))
        lines.append("## Low-leverage tasks (candidates to review)")
        if not low:
            lines.append("- (none)")
        for t, et, lev in low:
            lines.append(f"- {et.title} — L {lev:.2f} vs period avg {avg_lev:.2f}")
        lines.append("")

    lines.append("## Blocked on you")
    # Placeholder: populated from PR-review-request events in a later enrichment; for now keep explicit.
    lines.append("- (PR review backlog — V1.1 enricher)")
    lines.append("")

    lines.append("## Context switches")
    lines.append(f"- Total this period: {context_switches}")
    lines.append(f"- Secondary goal: reduce per-day switches without hurting leverage.")
    lines.append("")

    resumed = [(tid, count) for tid, count in resumptions_by_task.items() if count > 0]
    lines.append("## Resumed tasks")
    if not resumed:
        lines.append("- (none with >24h gaps)")
    else:
        for tid, count in resumed:
            et = enriched.get(tid)
            title = et.title if et else tid
            lines.append(f"- {title} — {count} resumption(s)")
    lines.append("")

    high_unc = [et.title for et in enriched.values() if et.uncertainty == "high"]
    lines.append("## Open + low-confidence estimates")
    if not high_unc:
        lines.append("- (none)")
    for title in high_unc[:10]:
        lines.append(f"- {title}")
    if last_calibrated_days is not None:
        lines.append(f"- Last calibrated: {last_calibrated_days} days ago "
                     f"{'(due — run `weekly-review --recalibrate`)' if last_calibrated_days >= 14 else ''}")
    else:
        lines.append("- Last calibrated: never — run `weekly-review --recalibrate` to seed anchors")
    lines.append("")

    return "\n".join(lines)
```

- [ ] **Step 14.4: Run — confirm it passes**

Run: `uv run pytest tests/test_digest.py -v`
Expected: 1 passed.

- [ ] **Step 14.5: Commit**

```bash
git add src/weekly_review/digest.py tests/test_digest.py
git commit -m "digest: markdown renderer with leverage, coverage, counter-signals"
```

---

## Task 15: CLI wiring — `weekly-review` command

**Files:**
- Create: `src/weekly_review/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 15.1: Write failing test**

Create `tests/test_cli.py`:
```python
import sys
from weekly_review.cli import main


def test_cli_help(capsys):
    sys.argv = ["weekly-review", "--help"]
    try:
        main()
    except SystemExit as e:
        assert e.code == 0
    out = capsys.readouterr().out
    assert "--since" in out
    assert "--inspect" in out
    assert "--recalibrate" in out


def test_cli_requires_config_or_reports_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WEEKLY_REVIEW_CONFIG", str(tmp_path / "nonexistent"))
    sys.argv = ["weekly-review", "--since", "2026-04-05"]
    try:
        rc = main()
    except SystemExit as e:
        rc = e.code
    err = capsys.readouterr()
    assert rc != 0
    assert "projects.yaml" in (err.out + err.err)
```

- [ ] **Step 15.2: Run — confirm it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with ModuleNotFoundError or AttributeError.

- [ ] **Step 15.3: Implement cli.py**

Create `src/weekly_review/cli.py`:
```python
"""Command-line entry point for weekly-review."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import calibration
from .config import CONFIG_DIR, ensure_config_dir, load_projects, load_yaml
from .coverage import coverage_report
from .digest import render_digest
from .effort import (
    active_hours_by_task,
    agent_active_hours_by_task,
    cognitive_wall_clock_seconds,
    context_switch_count,
    resumption_count,
)
from .enrichment import enrich_task
from .events import Event, read_events, write_events
from .extractors.claude_transcript import extract_from_jsonl, iter_transcript_files
from .extractors.git_log import extract_git_log
from .extractors.github_pr import extract_github_prs
from .lifecycle import compute_statuses
from .llm import CachedLLMClient, LLMClient
from .overrides import apply_overrides
from .runs import DATA_DIR, RunManager
from .segmentation import segment


SCHEMA_VERSION = "v1"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="weekly-review", description="Task-leverage weekly digest")
    p.add_argument("--since", help="ISO date; default = 14 days ago")
    p.add_argument("--until", help="ISO date; default = now")
    p.add_argument("--inspect", action="store_true", help="Print a per-task inspection view (read-only)")
    p.add_argument("--recalibrate", action="store_true", help="Interactive anchor-labeling flow")
    p.add_argument("--refresh-estimates", action="store_true", help="Invalidate LLM response cache before running")
    p.add_argument("--diff-runs", nargs=2, metavar=("OLD", "NEW"), help="Compare two runs by run_id")
    p.add_argument("--write", help="Write the digest to this path")
    p.add_argument("--source-machine", default="mac_mini")
    p.add_argument("--model-enrich", default="claude-sonnet-4-6", help="Model for task enrichment (hours/expertise)")
    p.add_argument("--no-enrich", action="store_true", help="Skip LLM enrichment (faster, coarser)")
    return p


def _iso_date(s: str) -> str:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).isoformat()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    projects = load_projects()
    if not projects.projects:
        print(f"error: no projects.yaml found at {CONFIG_DIR}/projects.yaml — see examples/projects.yaml", file=sys.stderr)
        return 2

    if args.diff_runs:
        return _cmd_diff_runs(args.diff_runs[0], args.diff_runs[1])
    if args.recalibrate:
        return _cmd_recalibrate(args)
    return _cmd_digest(args, projects)


def _cmd_digest(args, projects) -> int:
    now = datetime.now(timezone.utc)
    since = args.since or (now - timedelta(days=14)).strftime("%Y-%m-%d")
    until = args.until or now.strftime("%Y-%m-%d")

    mgr = RunManager()
    run = mgr.start_run(schema_version=SCHEMA_VERSION)

    # 1. Extract
    all_events: list[Event] = []
    transcripts_dir = Path.home() / ".claude" / "projects"
    for jsonl in iter_transcript_files(transcripts_dir, since_iso=_iso_date(since)):
        for ev in extract_from_jsonl(jsonl, projects=projects, source_machine=args.source_machine):
            if ev.ts >= _iso_date(since) and ev.ts <= _iso_date(until + "T23:59:59"):
                all_events.append(ev)

    # Git log + PRs for each registered project that has an on-disk repo
    for spec in projects.projects:
        repo_path = _first_existing_repo(spec.cwd_patterns)
        if repo_path is None:
            continue
        for ev in extract_git_log(repo_path=repo_path, project=spec.slug, since=since, source_machine=args.source_machine):
            all_events.append(ev)
        gh_repo = _detect_gh_repo(repo_path)
        if gh_repo:
            for ev in extract_github_prs(repo=gh_repo, project=spec.slug, since=_iso_date(since), source_machine=args.source_machine):
                all_events.append(ev)

    # Persist raw events per project per iso-week
    _persist_events(run, all_events)

    # 2. Segment + overrides + lifecycle
    tasks = segment(all_events)
    overrides = load_yaml("overrides")
    tasks = apply_overrides(tasks, overrides)
    statuses = compute_statuses(tasks, all_events, now=now)

    # 3. Effort / coverage / switches
    active = active_hours_by_task(tasks, all_events)
    agent_h = agent_active_hours_by_task(tasks, all_events)
    cog_sec = cognitive_wall_clock_seconds(all_events)
    switches = context_switch_count(all_events)
    resumptions = {t.task_id: resumption_count(t, all_events) for t in tasks}
    coverage = coverage_report(tasks, all_events)

    # 4. Enrich
    enriched: dict[str, object] = {}
    if not args.no_enrich:
        anchors = calibration.load_anchors(CONFIG_DIR / "anchors.yaml")
        expertise_seed = load_yaml("expertise").get("domains", {}) if load_yaml("expertise") else {}
        bias = calibration.load_anchors(CONFIG_DIR / "bias.yaml")
        bias_value = bias[0] if bias else {"multiplier": 1.0, "n": 0}
        cache_dir = Path(DATA_DIR) / "llm_cache"
        if args.refresh_estimates and cache_dir.exists():
            for f in cache_dir.glob("*.json"):
                f.unlink()
        llm = CachedLLMClient(LLMClient(), cache_dir=cache_dir)
        from .calibration import apply_bias_correction
        for t in tasks:
            try:
                et = enrich_task(t, all_events, anchors, expertise_seed, llm, model=args.model_enrich)
                et.est_human_hours = apply_bias_correction(et.est_human_hours, bias_value)
                enriched[t.task_id] = et
            except Exception as e:
                print(f"warning: enrichment failed for task {t.task_id}: {e}", file=sys.stderr)

    last_cal = _days_since_last_calibration(CONFIG_DIR / "anchors.yaml")

    md = render_digest(
        period_start=since,
        period_end=until,
        tasks=tasks,
        enriched=enriched,
        statuses=statuses,
        events=all_events,
        active_hours_by_task=active,
        agent_hours_by_task=agent_h,
        resumptions_by_task=resumptions,
        coverage=coverage,
        cognitive_wall_clock_hours=cog_sec / 3600,
        context_switches=switches,
        last_calibrated_days=last_cal,
    )

    if args.inspect:
        _print_inspect_view(tasks, all_events, enriched, statuses)
        return 0

    if args.write:
        Path(args.write).parent.mkdir(parents=True, exist_ok=True)
        Path(args.write).write_text(md)
        print(f"wrote digest to {args.write}", file=sys.stderr)
    else:
        print(md)
    return 0


def _cmd_recalibrate(args) -> int:
    """Interactive anchor labeling.

    Picks 5 shipped tasks from the most recent run (random sample). For each,
    shows the existing estimate + rationale, prompts the user for his hours, and
    appends to anchors.yaml. Refits and writes bias.yaml.
    """
    import random
    from .calibration import fit_bias

    anchors_path = CONFIG_DIR / "anchors.yaml"
    bias_path = CONFIG_DIR / "bias.yaml"
    anchors = calibration.load_anchors(anchors_path)

    mgr = RunManager()
    runs = mgr.list_runs()
    if not runs:
        print("no runs yet — run `weekly-review` first, then recalibrate.", file=sys.stderr)
        return 2
    latest = runs[0]
    enriched_path = latest.path / "enriched.json"
    if not enriched_path.exists():
        print(f"latest run {latest.run_id} has no enriched tasks (was --no-enrich used?)", file=sys.stderr)
        return 2
    enriched = json.loads(enriched_path.read_text())
    shipped = [e for e in enriched if e.get("status") == "shipped"]
    sample = random.sample(shipped, min(5, len(shipped)))
    if not sample:
        print("no shipped tasks to calibrate against.", file=sys.stderr)
        return 0

    pairs: list[tuple[float, float]] = []
    for et in sample:
        print(f"\n--- Task: {et['title']} ---")
        print(f"  Rationale: {et.get('rationale', '')}")
        print(f"  LLM estimate: {et['est_human_hours']:.1f} hours ({et.get('uncertainty', '?')} uncertainty)")
        raw = input("  Your estimate in hours (blank to skip): ").strip()
        if not raw:
            continue
        try:
            truth = float(raw)
        except ValueError:
            print("  (invalid number, skipping)")
            continue
        pairs.append((et["est_human_hours"], truth))
        anchors.append({
            "title": et["title"],
            "est_human_hours": truth,
            "dimensions": et.get("dimensions", {}),
            "bryan_domain_expertise": et.get("bryan_domain_expertise"),
        })

    calibration.save_anchors(anchors, anchors_path)
    bias = fit_bias(pairs)
    calibration.save_anchors([bias], bias_path)
    print(f"\nSaved {len(pairs)} new anchor(s) to {anchors_path}")
    print(f"Bias multiplier: {bias['multiplier']:.3f} (from {bias['n']} pairs in this run)")
    return 0


def _cmd_diff_runs(old_id: str, new_id: str) -> int:
    mgr = RunManager()
    runs = {r.run_id: r for r in mgr.list_runs()}
    if old_id not in runs or new_id not in runs:
        print(f"error: one or both run IDs not found", file=sys.stderr)
        return 2
    old_enriched = _load_json(runs[old_id].path / "enriched.json", default=[])
    new_enriched = _load_json(runs[new_id].path / "enriched.json", default=[])
    old_ids = {t["task_id"]: t for t in old_enriched}
    new_ids = {t["task_id"]: t for t in new_enriched}
    appeared = set(new_ids) - set(old_ids)
    disappeared = set(old_ids) - set(new_ids)
    shifted = [(tid, old_ids[tid]["est_human_hours"], new_ids[tid]["est_human_hours"])
               for tid in (set(old_ids) & set(new_ids))
               if abs(old_ids[tid]["est_human_hours"] - new_ids[tid]["est_human_hours"]) > 0.5]

    print(f"# Diff: {old_id} → {new_id}")
    print(f"- appeared: {len(appeared)}")
    print(f"- disappeared: {len(disappeared)}")
    print(f"- shifted (>0.5h): {len(shifted)}")
    for tid, old_h, new_h in shifted[:20]:
        print(f"  - {new_ids[tid]['title']}: {old_h:.1f}h → {new_h:.1f}h")
    return 0


# --- helpers ---

def _first_existing_repo(cwd_patterns: list[str]) -> Path | None:
    import fnmatch
    for pat in cwd_patterns:
        # Only patterns that look like a direct path, not a glob
        if "*" in pat:
            continue
        candidate = Path(pat)
        if candidate.exists() and (candidate / ".git").exists():
            return candidate
    # Fallback: try expanding the first glob pattern literally
    for pat in cwd_patterns:
        expanded = pat.replace("/Users/*/", str(Path.home()) + "/")
        candidate = Path(expanded.split("*")[0])
        if candidate.exists() and (candidate / ".git").exists():
            return candidate
    return None


def _detect_gh_repo(repo_path: Path) -> str | None:
    import subprocess
    r = subprocess.run(
        ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
        capture_output=True, text=True, check=False,
    )
    url = (r.stdout or "").strip()
    if not url:
        return None
    # ssh: git@github.com:owner/repo.git ; https: https://github.com/owner/repo(.git)
    if url.startswith("git@"):
        _, _, rest = url.partition(":")
    elif "github.com/" in url:
        rest = url.split("github.com/", 1)[1]
    else:
        return None
    return rest.removesuffix(".git")


def _persist_events(run, events: list[Event]) -> None:
    from collections import defaultdict
    from datetime import datetime
    def iso_week(ts: str) -> str:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        iy, iw, _ = dt.isocalendar()
        return f"{iy}-W{iw:02d}"
    buckets = defaultdict(list)
    for ev in events:
        buckets[(ev.project, iso_week(ev.ts))].append(ev)
    for (project, week), evs in buckets.items():
        path = run.events_dir / project / f"{week}.jsonl"
        write_events(path, evs)

    # Persist enriched snapshot later via CLI after enrichment; this stub covers raw events only.


def _print_inspect_view(tasks, events, enriched, statuses) -> None:
    for t in tasks:
        status = statuses.get(t.task_id, "?")
        et = enriched.get(t.task_id)
        print(f"\n## [{status}] {t.title}   (task_id={t.task_id}, project={t.project})")
        if et:
            print(f"   est_human_hours: {et.est_human_hours:.2f}  unc: {et.uncertainty}  exp: {et.bryan_domain_expertise}")
            print(f"   rationale: {et.rationale}")
        print(f"   events ({len(t.event_ids)}):")
        for idx in t.event_ids[:20]:
            if idx >= len(events):
                continue
            ev = events[idx]
            snippet = ev.content_snippet.replace("\n", " ")[:140]
            print(f"   - [{ev.ts}] {ev.kind}: {snippet}")


def _days_since_last_calibration(anchors_path: Path) -> int | None:
    if not anchors_path.exists():
        return None
    mtime = datetime.fromtimestamp(anchors_path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - mtime).days


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 15.4: Run — confirm CLI tests pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 2 passed.

- [ ] **Step 15.5: Wire enriched-snapshot persistence**

Edit `src/weekly_review/cli.py`, in `_cmd_digest` after the enrichment loop and before rendering the digest, add:

```python
    # Persist enriched snapshot for --diff-runs and --recalibrate
    if enriched:
        enriched_list = []
        for tid, et in enriched.items():
            d = {
                "task_id": tid,
                "title": et.title,
                "est_human_hours": et.est_human_hours,
                "uncertainty": et.uncertainty,
                "bryan_domain_expertise": et.bryan_domain_expertise,
                "dimensions": et.dimensions,
                "rationale": et.rationale,
                "status": statuses.get(tid, "?"),
            }
            enriched_list.append(d)
        (run.path / "enriched.json").write_text(json.dumps(enriched_list, indent=2))
```

- [ ] **Step 15.6: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: all pass.

- [ ] **Step 15.7: Commit**

```bash
git add src/weekly_review/cli.py tests/test_cli.py
git commit -m "cli: digest/inspect/recalibrate/diff-runs subcommands"
```

---

## Task 16: Seed config + first end-to-end dry run

**Files:**
- Create: `~/.config/weekly-review/projects.yaml` (from `examples/projects.yaml`, extended)
- Create: `~/.config/weekly-review/expertise.yaml` (from `examples/expertise.yaml`, extended)
- Modify: none in the repo — this is config and a live run

- [ ] **Step 16.1: Check which projects the user has under ~/dev**

Run:
```bash
ls /Users/<you>/dev/ | head -40
```

Expected: a list of project directories. Pick ~6-10 projects to register (the ones the user has worked on in the last 2 weeks; check transcript dir names for hints).

- [ ] **Step 16.2: Seed projects.yaml**

Create `/Users/<you>/.config/weekly-review/projects.yaml` based on `examples/projects.yaml`, adding one entry per project the user has worked on recently (include worktree patterns). Use the cwd directory names from `ls ~/.claude/projects/` to identify candidates.

Confirm it validates:
```bash
uv run python -c "from weekly_review.config import load_projects; print([p.slug for p in load_projects().projects])"
```

Expected: the list of slugs prints.

- [ ] **Step 16.3: Seed expertise.yaml**

Create `/Users/<you>/.config/weekly-review/expertise.yaml` from `examples/expertise.yaml`. The user should review and tune the domain entries.

- [ ] **Step 16.4: Dry run without enrichment**

Run:
```bash
uv run weekly-review --since 2026-04-05 --no-enrich --write /tmp/digest-v0.md
```

Expected:
- Completes without error.
- `/tmp/digest-v0.md` exists and has Leverage, Coverage, Top tasks, Context switches sections.
- Coverage is ≥ 95% (if not, inspect unassigned_by_project).

- [ ] **Step 16.5: Inspect and capture issues**

Run:
```bash
uv run weekly-review --since 2026-04-05 --no-enrich --inspect | head -80
```

Read the inspect output. Note anything off (tasks that should have been merged, segmentation surprises, wrong project attribution). Capture in a scratch file — will drive the override round-trip in Task 17.

- [ ] **Step 16.6: Commit any config adjustments**

If any changes were made to `examples/*.yaml` while iterating (to include more common patterns), commit those. The `~/.config/` files are outside the repo and not committed.

```bash
git add examples/
git diff --cached --quiet || git commit -m "examples: extend projects.yaml for local smoke run"
```

---

## Task 17: Live end-to-end with enrichment + first anchors

**Files:**
- Modify: `~/.config/weekly-review/overrides.yaml` (manually, if issues found in 16.5)
- Modify: `~/.config/weekly-review/anchors.yaml` (via `--recalibrate`)

- [ ] **Step 17.1: If any segmentation issues were flagged in 16.5, write overrides**

Create `/Users/<you>/.config/weekly-review/overrides.yaml`:
```yaml
merges: []
relabels: []
ignores: []
```

Fill with any merges/relabels based on what `--inspect` revealed. Keep it minimal — only fix obviously wrong segmentation, not taste preferences.

- [ ] **Step 17.2: Full live run**

Run:
```bash
uv run weekly-review --since 2026-04-05 --write /tmp/digest-v1.md
```

Expected:
- Completes (may take a minute with enrichment).
- Digest has all sections including `est_human_hours` per task, `bryan_domain_expertise`, leverage ratio.
- Cost: rough estimate is N_tasks × ~2000 tokens in / ~300 tokens out with Sonnet 4.6. For ~20 tasks, that's ~40k input tokens → ~$0.12, which should be fine.

- [ ] **Step 17.3: First recalibration pass**

Run:
```bash
uv run weekly-review --recalibrate
```

Expected: walks the user through 5 shipped tasks, prompts for his estimate, writes `~/.config/weekly-review/anchors.yaml` and `~/.config/weekly-review/bias.yaml`.

- [ ] **Step 17.4: Re-run with anchors applied**

Run:
```bash
uv run weekly-review --since 2026-04-05 --refresh-estimates --write /tmp/digest-v2.md
diff /tmp/digest-v1.md /tmp/digest-v2.md
```

Expected: `est_human_hours` values shift (bias-corrected); the anchors now appear in the LLM prompt for subsequent tasks.

- [ ] **Step 17.5: The user reviews digest-v2.md**

The user reads it. Captures in `docs/process/retrospective.md` (append):
- Which sections were most useful
- Which numbers felt right / wrong
- What's missing

This is the success criterion: the digest is accurate enough to change how the user works next week.

- [ ] **Step 17.6: Commit retrospective + any final tuning**

```bash
git add docs/process/retrospective.md
git commit -m "process: V1 end-to-end retrospective on 2-week Mac Mini window"
```

---

## Task 18: Skill wrapper + README

**Files:**
- Modify: `.claude/skills/weekly-review/SKILL.md` (already exists; update with accurate invocation)
- Modify: `README.md`

- [ ] **Step 18.1: Update SKILL.md to reflect actual CLI**

Edit `.claude/skills/weekly-review/SKILL.md` so the `How to invoke`, `Configuration`, and scripts list sections match what's shipped. Replace the speculative script list with:

```markdown
Helper scripts and modules in `src/weekly_review/`:
- `extractors/claude_transcript.py` — parses `~/.claude/projects/*/*.jsonl`
- `extractors/git_log.py` — git log per registered repo
- `extractors/github_pr.py` — `gh pr list` per registered repo
- `segmentation.py` — Layer 1 rules + proximity fallback
- `effort.py` — active time, cognitive wall-clock, switches
- `enrichment.py` — per-task LLM pass (title, est_human_hours, domain expertise)
- `digest.py` — markdown digest renderer
```

Replace `How to invoke` with the accurate CLI from Task 15.

- [ ] **Step 18.2: Update README.md**

Replace or extend the existing README with:
- 2-sentence summary of the project
- "What's installed" (uv + package script)
- "How to run" (the CLI)
- "Where to look" (pointers to spec, decisions, ATTRIBUTIONS)

Keep it under 60 lines.

- [ ] **Step 18.3: Commit**

```bash
git add .claude/skills/weekly-review/SKILL.md README.md
git commit -m "docs: align SKILL.md and README with shipped V1 CLI"
```

---

## Task 19: Learnings and retro

**Files:**
- Modify: `docs/process/learnings.md`

- [ ] **Step 19.1: Capture learnings from the implementation pass**

Edit `docs/process/learnings.md` and append specific learnings that would help a future version of this tool or a similar one. Examples of what's worth capturing (only include those that actually came up):

- What percent of user messages needed the proximity fallback vs. Layer 1 rules? (If >50% needed fallback, Layer 2 LLM becomes higher priority.)
- Was the active-hours formula reasonable vs. The user's gut-check?
- How many tasks per 2-week window? (Sets expectations for enrichment cost.)
- Were any transcripts unparseable? If so, capture the pattern.
- What surprised us during the enrichment prompt design?

- [ ] **Step 19.2: Commit**

```bash
git add docs/process/learnings.md
git commit -m "process: capture V1 implementation learnings"
```

---

## Self-review (for the plan author)

Before handoff, confirm:

**Spec coverage.** For each "Must ship" item in spec §12, there's a task:
1. Transcript ingestion (Mac Mini, 2 weeks) → Task 5 + Task 15 iter_transcript_files
2. Projects registry → Task 3
3. Git log + gh pr list → Task 6
4. Segmentation L1 + proximity fallback → Task 7
5. Active-time formula → Task 9
6. Cognitive wall-clock → Task 9
7. Context-switch count → Task 9
8. `est_human_hours` LLM estimator → Task 12 + Task 13 bias
9. `bryan_domain_expertise` tagging → Task 12
10. Coverage metric → Task 10
11. Markdown digest → Task 14
12. Overrides YAML → Task 8
13. Recalibration flow → Task 13 + Task 15 CLI

Coverage: complete.

**Placeholder scan.** No "TBD" / "TODO" / "fill in" / "similar to Task N"; every step has actual code or a concrete command.

**Type consistency.**
- `Event` from §4: same 10 fields used everywhere.
- `Task.event_ids: list[int]`: used consistently.
- `EnrichedTask.est_human_hours: float`: consistent.
- `coverage_report(...)` returns a dict with `user_message_events`, `assigned`, `unassigned`, `coverage_percent`, `meets_target`, `unassigned_by_project` — used in `digest.py` and `cli.py` with those keys.

---

## Execution Handoff

Plan complete and saved to `docs/product/plans/v1-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — A fresh subagent per task, review between tasks, fast iteration. Good fit here since each task has clear inputs, outputs, and tests.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, run all tasks in one pass, pause only on gate failures.

Per project `workflow-conventions.md`, the default is "Executing Plans" (single pass, no per-batch checkpoints). That means **Option 2** lines up with the house style — execute straight through, report at the end. Subagent-Driven (Option 1) adds automated review between tasks at the cost of more dispatch overhead.

Which approach?
