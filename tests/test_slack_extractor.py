"""Tests for the Slack archive extractor."""
from __future__ import annotations

import json
from pathlib import Path

from weekly_review.config import ProjectsConfig
from weekly_review.extractors.slack import extract_from_slack_root


def _write_history(root: Path, channel_id: str, messages: list[dict]) -> None:
    cdir = root / channel_id
    cdir.mkdir(parents=True, exist_ok=True)
    out = cdir / "history.jsonl"
    out.write_text("\n".join(json.dumps(m) for m in messages))


def _write_channel_meta(root: Path, channels: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "_channels.json").write_text(json.dumps({"channels": channels}))


def test_human_message_emits_user_message_with_author_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("WEEKLY_REVIEW_CONFIG", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "slack_channels.yaml").write_text("CTEST: product-engineer\n")
    # Re-load CONFIG_DIR
    import importlib
    from weekly_review import config as cfg
    importlib.reload(cfg)
    from weekly_review.extractors import slack as slack_mod
    importlib.reload(slack_mod)

    root = tmp_path / "slack"
    _write_channel_meta(root, [{"id": "CTEST", "name": "productengineer"}])
    _write_history(root, "CTEST", [
        {"type": "message", "ts": "1775500000.123456", "user": "U0AH7G5GKNW",
         "text": "Please clean up stuck agents"},
    ])
    events = list(slack_mod.extract_from_slack_root(root, projects=ProjectsConfig(projects=[])))
    assert len(events) == 1
    e = events[0]
    assert e.kind == "user_message"
    assert e.actor == "user"
    assert e.author_kind == "human"
    assert e.author_id == "U0AH7G5GKNW"
    assert e.project == "product-engineer"
    assert e.source_system == "slack"
    assert "stuck agents" in e.content_snippet
    assert e.metadata["channel_name"] == "productengineer"


def test_bot_message_emits_agent_message(tmp_path, monkeypatch):
    monkeypatch.setenv("WEEKLY_REVIEW_CONFIG", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    import importlib
    from weekly_review import config as cfg
    importlib.reload(cfg)
    from weekly_review.extractors import slack as slack_mod
    importlib.reload(slack_mod)

    root = tmp_path / "slack"
    _write_history(root, "CTEST", [
        {"type": "message", "subtype": "bot_message",
         "ts": "1775500001.0", "bot_id": "B12345",
         "text": "Deploy completed", "username": "github-actions"},
    ])
    events = list(slack_mod.extract_from_slack_root(root, projects=ProjectsConfig(projects=[])))
    assert len(events) == 1
    e = events[0]
    assert e.kind == "agent_message"
    assert e.actor == "agent"
    assert e.author_kind == "bot"
    assert e.author_id == "B12345"


def test_drops_channel_join_and_thread_replies(tmp_path, monkeypatch):
    monkeypatch.setenv("WEEKLY_REVIEW_CONFIG", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    import importlib
    from weekly_review import config as cfg
    importlib.reload(cfg)
    from weekly_review.extractors import slack as slack_mod
    importlib.reload(slack_mod)

    root = tmp_path / "slack"
    _write_history(root, "CTEST", [
        {"type": "message", "subtype": "channel_join",
         "ts": "1775500002.0", "user": "U0AH7G5GKNW", "text": "joined"},
        # thread reply (thread_ts != ts) — skipped
        {"type": "message", "ts": "1775500003.0", "user": "U0AH7G5GKNW",
         "text": "thread reply", "thread_ts": "1775499999.0"},
        # top-level (thread_ts == ts) — kept
        {"type": "message", "ts": "1775500004.0", "user": "U0AH7G5GKNW",
         "text": "top-level with thread", "thread_ts": "1775500004.0"},
    ])
    events = list(slack_mod.extract_from_slack_root(root, projects=ProjectsConfig(projects=[])))
    assert len(events) == 1
    assert events[0].content_snippet == "top-level with thread"
