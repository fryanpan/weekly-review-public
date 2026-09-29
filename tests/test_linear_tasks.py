"""Tests for the product-engineer DB-dump extractor."""
from __future__ import annotations

import json
from pathlib import Path

from weekly_review.config import ProjectSpec, ProjectsConfig
from weekly_review.extractors.linear_tasks import extract_from_pe_db_dump


PROJECTS = ProjectsConfig(projects=[
    ProjectSpec(slug="notes-repo", cwd_patterns=["/workspace/notes-repo", "/workspace/notes-repo/**"]),
    ProjectSpec(slug="health-tool", cwd_patterns=["/workspace/health-tool", "/workspace/health-tool/**"]),
])


def test_extract_emits_user_message_per_task(tmp_path: Path):
    dump = {
        "tasks": [
            {
                "task_uuid": "uuid-1",
                "task_id": "BC-208",
                "product": "notes-repo",
                "title": "Curate daily AI/ML digest for product leaders",
                "created_at": "2026-03-29 15:00:34",
                "slack_channel": "C0EXAMPLE",
                "slack_thread_ts": "0000.0001",
                "status": "closed",
            },
            {
                "task_uuid": "uuid-2",
                "task_id": "BC-211",
                "product": "health-tool",
                "title": "Fix broken health tool deploys",
                "created_at": "2026-03-30 12:28:02",
                "status": "open",
            },
            # Empty title — should be filtered (webhook-only / scheduled).
            {
                "task_uuid": "uuid-3",
                "product": "health-tool",
                "title": "",
                "created_at": "2026-03-30 12:30:00",
            },
            # Missing product — filtered.
            {
                "task_uuid": "uuid-4",
                "title": "Orphan task",
                "created_at": "2026-03-30 12:31:00",
            },
        ]
    }
    path = tmp_path / "full_db_dump.json"
    path.write_text(json.dumps(dump))

    events = list(extract_from_pe_db_dump(path, projects=PROJECTS))
    assert len(events) == 2

    e1 = events[0]
    assert e1.kind == "user_message"
    assert e1.actor == "user"
    assert e1.source_system == "linear_task"
    assert e1.project == "notes-repo"
    assert e1.session_id == "uuid-1"
    assert e1.ts == "2026-03-29T15:00:34Z"
    assert e1.content_snippet == "Curate daily AI/ML digest for product leaders"
    assert e1.metadata["words"] == 7
    assert e1.metadata["linear_task_id"] == "BC-208"
    assert e1.metadata["slack_channel"] == "C0EXAMPLE"
    assert e1.metadata["slack_thread_ts"] == "0000.0001"
    assert e1.metadata["cwd"] == "/workspace/notes-repo"

    e2 = events[1]
    assert e2.project == "health-tool"
    assert e2.metadata["linear_task_id"] == "BC-211"


def test_extract_skips_unknown_products(tmp_path: Path):
    dump = {
        "tasks": [
            {
                "task_uuid": "u",
                "product": "no-such-project",
                "title": "Some title",
                "created_at": "2026-03-30 12:00:00",
            }
        ]
    }
    path = tmp_path / "dump.json"
    path.write_text(json.dumps(dump))
    events = list(extract_from_pe_db_dump(path, projects=PROJECTS))
    # Project routes to "unknown" — still emitted, since the events extractor
    # doesn't filter by project (excludes are applied at CLI ingestion time).
    assert len(events) == 1
    assert events[0].project == "unknown"
