import sqlite3
from pathlib import Path

from weekly_review.config import ProjectSpec, ProjectsConfig
from weekly_review.extractors.conductor_db import extract_from_conductor_db


def _build_test_db(path: Path) -> None:
    """Create a minimal SQLite DB mimicking conductor.db's schema."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE repos (
            id TEXT PRIMARY KEY,
            name TEXT,
            root_path TEXT
        );
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY,
            repository_id TEXT,
            directory_name TEXT
        );
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            workspace_id TEXT
        );
        CREATE TABLE session_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            content TEXT,
            sent_at TEXT,
            model TEXT
        );

        INSERT INTO repos VALUES
            ('r1', 'cycling-tool', '/Users/me/dev/cycling-tool');
        INSERT INTO workspaces VALUES
            ('w1', 'r1', 'edinburgh-v1');
        INSERT INTO sessions VALUES
            ('s1', 'w1');
        INSERT INTO session_messages VALUES
            ('m1', 's1', 'user', 'Please add route preferences',
             '2026-02-10T10:00:00.000Z', NULL);
        INSERT INTO session_messages VALUES
            ('m2', 's1', 'assistant', 'I will add them now',
             '2026-02-10T10:00:30.000Z', 'claude-opus-4-7');
        INSERT INTO session_messages VALUES
            ('m3', 's1', 'user', '',
             NULL, NULL);  -- missing sent_at, should skip
        """
    )
    conn.commit()
    conn.close()


def test_conductor_db_emits_user_and_agent_events(tmp_path: Path):
    db = tmp_path / "conductor.db"
    _build_test_db(db)

    projects = ProjectsConfig(projects=[
        ProjectSpec(slug="cycling-tool", cwd_patterns=[
            "/Users/*/conductor/workspaces/cycling-tool/*",
        ]),
    ])

    events = list(extract_from_conductor_db(
        db, projects=projects, source_machine="laptop",
        home_for_cwd=Path("/Users/dev"),
    ))
    assert len(events) == 2

    user = [e for e in events if e.kind == "user_message"]
    agent = [e for e in events if e.kind == "agent_message"]
    assert len(user) == 1
    assert len(agent) == 1

    assert user[0].project == "cycling-tool"
    assert user[0].source_system == "conductor_db"
    assert user[0].source_machine == "laptop"
    assert user[0].metadata["conductor_msg_id"] == "m1"
    assert user[0].metadata["workspace_dir"] == "edinburgh-v1"
    assert agent[0].metadata["model"] == "claude-opus-4-7"


def test_conductor_db_skips_messages_with_no_sent_at(tmp_path: Path):
    db = tmp_path / "conductor.db"
    _build_test_db(db)

    projects = ProjectsConfig(projects=[
        ProjectSpec(slug="cycling-tool", cwd_patterns=["/Users/*/conductor/workspaces/cycling-tool/*"]),
    ])
    events = list(extract_from_conductor_db(
        db, projects=projects, source_machine="laptop",
        home_for_cwd=Path("/Users/dev"),
    ))
    # m3 has NULL sent_at — should be excluded
    ids = [e.metadata["conductor_msg_id"] for e in events]
    assert "m3" not in ids


def test_conductor_db_unknown_project_when_no_pattern_matches(tmp_path: Path):
    db = tmp_path / "conductor.db"
    _build_test_db(db)

    projects = ProjectsConfig(projects=[])  # no patterns at all

    events = list(extract_from_conductor_db(
        db, projects=projects, source_machine="laptop",
        home_for_cwd=Path("/Users/dev"),
    ))
    assert all(e.project == "unknown" for e in events)
