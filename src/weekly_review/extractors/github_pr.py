"""Extract pr_opened / pr_merged events via `gh pr list`."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Collection, Iterator

from ..events import Event


FIELDS = "number,title,createdAt,mergedAt,state,author,additions,deletions,changedFiles,url,body,headRefName"

# A workspace board task id: `t-` and 12 url-safe characters with at least one
# capital or digit, which no branch slug or English word matches. Same shape
# the transcript extractor reads out of dispatch prompts.
_CARD_ID = re.compile(r"(?<![\w'-])t-(?=[\w-]{0,11}[A-Z0-9])[A-Za-z0-9_-]{12}(?!\w)")


def named_board_tasks(pr: dict) -> list[str]:
    """The board task ids a PR's body or branch name, sorted. The body text
    itself is never stored: it can quote anything, and the ids are all the
    goal link needs (method Step 5, 2026-09-17)."""
    text = f"{pr.get('body') or ''}\n{pr.get('headRefName') or ''}"
    return sorted(set(_CARD_ID.findall(text)))

# GitHub's search API returns at most 1,000 results for one query, so a page
# that comes back this full may have been cut. The window is halved until every
# page fits.
SEARCH_CAP = 1000
# Below this a range is not split further; a page still at the cap is reported
# as truncated rather than bisected forever.
MIN_RANGE = timedelta(minutes=1)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _search_page(repo: str, lo: datetime, hi: datetime, limit: int) -> list[dict] | None:
    result = subprocess.run(
        [
            "gh", "pr", "list",
            "-R", repo,
            "--state", "all",
            "--search", f"updated:{_iso(lo)}..{_iso(hi)}",
            "--limit", str(limit),
            "--json", FIELDS,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None


def fetch_prs(
    repo: str,
    since: str,
    until: datetime | None = None,
    limit: int = SEARCH_CAP,
    stats: dict | None = None,
) -> list[dict]:
    """Every PR in `repo` last updated at or after `since`, by date-bisected search.

    `gh pr list --limit 200` without a search returns the newest 200 PRs, so any
    window older than a repo's latest 200 lost its PRs silently: the Sep 1-7
    run held 15 of the 262 PRs claude-workspaces-plugin merged that week.

    Searching on `updated` rather than `created` is what keeps a PR opened
    before the window and merged inside it: updatedAt >= mergedAt >= createdAt,
    so every PR with either event in the window is in the result. The upper
    bound defaults to now, not the window end, because a PR merged in the week
    and commented on later has an updatedAt past the week.

    `stats`, when given, is filled with queries/fetched/truncated so a run can
    record whether the fetch was complete.
    """
    lo = _parse(since)
    hi = until or datetime.now(timezone.utc)
    by_number: dict[int, dict] = {}
    queries = 0
    failed = False
    truncated: list[str] = []
    pending = [(lo, hi)]
    while pending:
        a, b = pending.pop()
        page = _search_page(repo, a, b, limit)
        queries += 1
        if page is None:
            failed = True
            continue
        if len(page) >= limit:
            if b - a > MIN_RANGE:
                mid = a + (b - a) / 2
                pending += [(a, mid), (mid, b)]
                continue
            truncated.append(f"{_iso(a)}..{_iso(b)}")
        for pr in page:
            if pr.get("number") is not None:
                by_number[pr["number"]] = pr
    if truncated:
        print(
            f"WARNING: {repo}: {len(truncated)} search range(s) still hit the "
            f"{limit}-result cap at minimum width; PRs may be missing",
            file=sys.stderr,
        )
    if stats is not None:
        stats.update(
            repo=repo, queries=queries, fetched=len(by_number),
            truncated=truncated, failed=failed,
        )
    return sorted(by_number.values(), key=lambda pr: pr["number"])


def extract_github_prs(
    repo: str,
    project: str,
    since: str,
    source_machine: str,
    limit: int = SEARCH_CAP,
    authors: Collection[str] | None = None,
    stats: dict | None = None,
) -> Iterator[Event]:
    """Emit pr_opened / pr_merged events for PRs with either event at or after `since`.

    `since` is an ISO timestamp; filters by createdAt/mergedAt >= since (client-side).

    `authors`, when given, restricts output to PRs authored by one of those
    GitHub logins (case-insensitive). This is REQUIRED to get honest numbers on
    shared repos: `gh pr list` returns every author's PRs, so without a filter a
    teammate's PR on a client repo is attributed to the
    user as their own shipped output and inflates est_h. None = no filter (every
    author), kept only for tests and single-author personal repos.
    """
    allow = {a.lower() for a in authors} if authors else None
    prs = fetch_prs(repo, since, limit=limit, stats=stats)

    # GitHub flags bot accounts in author.is_bot — but `gh pr list` doesn't
    # expose that field by default. The known-bot login fallback covers the
    # common cases (dependabot, copilot, github-actions, claude bots).
    KNOWN_BOTS = {"dependabot[bot]", "copilot", "github-actions[bot]"}

    seq = 0
    for pr in prs:
        author = (pr.get("author") or {}).get("login") or ""
        if allow is not None and author.lower() not in allow:
            continue  # someone else's PR on a shared repo — not the user's output
        is_bot = author.endswith("[bot]") or author.lower() in KNOWN_BOTS
        author_kind = "bot" if is_bot else ("human" if author else "")

        created = pr.get("createdAt")
        merged = pr.get("mergedAt")
        common = {
            "project": project,
            "session_id": f"github:{repo}",
            "source_system": "github",
            "source_machine": source_machine,
            "content_snippet": (pr.get("title") or "")[:500],
            "author_id": author,
            "author_kind": author_kind,
        }

        def metadata(kind: str) -> dict:
            return {
                "number": pr.get("number"),
                "state": pr.get("state"),
                "author": author,
                "additions": pr.get("additions"),
                "deletions": pr.get("deletions"),
                "changedFiles": pr.get("changedFiles"),
                "url": pr.get("url"),
                "named_tasks": named_board_tasks(pr),
                # Owner/name repo string for the R2-conversation ↔ PR-task
                # join rule (segmentation Rule 3). E.g. "<owner>/family-bike-map".
                "repo": repo,
                # Slug-independent repo identity, matching what git_log stamps,
                # so a PR and the squash commit that closed it resolve to the
                # same artifact even when they land in different projects.
                "repo_name": repo.rstrip("/").split("/")[-1].lower(),
                # The PR's own identity. The id used to be the position in the
                # fetched list, so one new PR shifted every other PR's id and
                # the same PR had different ids in two runs.
                "source_event_id": f"pr:{repo.lower()}:{pr.get('number')}:{kind}",
            }

        if created and created >= since:
            yield Event(ts=created, actor="artifact", kind="pr_opened", seq=seq,
                        metadata=metadata("opened"), **common)
            seq += 1
        if merged and merged >= since:
            yield Event(ts=merged, actor="artifact", kind="pr_merged", seq=seq,
                        metadata=metadata("merged"), **common)
            seq += 1
