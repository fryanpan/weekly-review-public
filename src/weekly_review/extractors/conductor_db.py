"""Extract events from Conductor.build's SQLite database.

Conductor stores root-session conversations in
`~/Library/Application Support/com.conductor.app/conductor.db`. The
`session_messages` table holds role/content/sent_at per turn; joining
through `sessions` → `workspaces` → `repos` gives us a cwd we can route
through the projects registry.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from ..config import ProjectsConfig
from ..events import Event


SNIPPET_LIMIT = 500
USER_SNIPPET_LIMIT = 2000
SOURCE_SYSTEM = "conductor_db"


QUERY = """
SELECT
    sm.id            AS msg_id,
    sm.session_id    AS session_id,
    sm.role          AS role,
    sm.content       AS content,
    sm.sent_at       AS sent_at,
    sm.model         AS model,
    s.workspace_id   AS workspace_id,
    w.directory_name AS workspace_dir,
    r.name           AS repo_name,
    r.root_path      AS repo_root_path
FROM session_messages sm
LEFT JOIN sessions   s ON s.id = sm.session_id
LEFT JOIN workspaces w ON w.id = s.workspace_id
LEFT JOIN repos      r ON r.id = w.repository_id
WHERE sm.sent_at IS NOT NULL
ORDER BY sm.sent_at
"""


def _derive_cwd(repo_name: str | None, workspace_dir: str | None, repo_root_path: str | None, home: Path) -> str:
    """Construct a cwd that will match projects.yaml patterns.

    Prefer `<HOME>/conductor/workspaces/<repo>/<workspace_dir>` which is
    the canonical Conductor workspace path. Fall back to `repo_root_path`
    if metadata is incomplete.
    """
    if repo_name and workspace_dir:
        return f"{home}/conductor/workspaces/{repo_name}/{workspace_dir}"
    if repo_root_path:
        return repo_root_path
    return ""


def extract_from_conductor_db(
    db_path: Path,
    projects: ProjectsConfig,
    source_machine: str,
    home_for_cwd: Path | None = None,
) -> Iterator[Event]:
    """Yield Events from a conductor.db file.

    `home_for_cwd` is the home directory the database was written under
    (default: current $HOME). Used to reconstruct the cwd for project
    routing; conductor.db itself doesn't store the absolute path.
    """
    home = home_for_cwd or Path.home()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(QUERY)
        for i, row in enumerate(cur):
            role = row["role"]
            if role not in ("user", "assistant"):
                continue
            ts = row["sent_at"]
            if not ts:
                continue
            session_id = row["session_id"] or f"conductor:{row['workspace_id']}"
            cwd = _derive_cwd(
                row["repo_name"],
                row["workspace_dir"],
                row["repo_root_path"],
                home,
            )
            project = projects.slug_for(cwd) or "unknown"
            content = row["content"] or ""
            words = len(content.split())

            if role == "user":
                yield Event(
                    ts=ts,
                    actor="user",
                    project=project,
                    session_id=session_id,
                    source_system=SOURCE_SYSTEM,
                    source_machine=source_machine,
                    kind="user_message",
                    seq=i,
                    content_snippet=content[:USER_SNIPPET_LIMIT],
                    metadata={
                        "words": words,
                        "cwd": cwd,
                        "conductor_msg_id": row["msg_id"],
                        "conductor_workspace_id": row["workspace_id"],
                        "repo_name": row["repo_name"],
                        "workspace_dir": row["workspace_dir"],
                    },
                )
            else:
                yield Event(
                    ts=ts,
                    actor="agent",
                    project=project,
                    session_id=session_id,
                    source_system=SOURCE_SYSTEM,
                    source_machine=source_machine,
                    kind="agent_message",
                    seq=i,
                    content_snippet=content[:SNIPPET_LIMIT],
                    metadata={
                        "words": words,
                        "cwd": cwd,
                        "model": row["model"],
                        "conductor_msg_id": row["msg_id"],
                        "conductor_workspace_id": row["workspace_id"],
                        "repo_name": row["repo_name"],
                        "workspace_dir": row["workspace_dir"],
                    },
                )
    finally:
        conn.close()
