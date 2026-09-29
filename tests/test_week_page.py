"""The one-page week view (method Step 7, board task of 2026-09-18): at most ten items
per project, goals by output, the smallest folded, unlinked units as one
"no goal" item."""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("make_week_page", REPO / "scripts" / "make_week_page.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _unit(tid, bt, est, ho=0.1, kind=""):
    return {"task_id": tid, "project": "p", "title": tid, "unit_kind": kind, "board_task": bt,
            "first_ts": "2026-09-08T10:00:00Z", "last_ts": "2026-09-08T12:00:00Z",
            "user_msg_count": 1, "est": est, "hands_on": ho, "labor": ho}


def test_goals_sorted_by_output_then_no_goal_last():
    m = _mod()
    units = [_unit("a", "t-1", 1.0), _unit("b", "t-2", 5.0), _unit("c", None, 2.0), _unit("d", "t-3", 0.5)]
    goal_of = {"t-1": "g-x", "t-2": "g-y", "t-3": ""}
    titles = {"t-1": ("A", "Goal X", "B"), "t-2": ("B", "Goal Y", "B")}
    items = m.project_items(units, goal_of, titles)
    assert [i["title"] for i in items] == ["Goal Y", "Goal X", "no goal"]
    assert items[2]["est"] == 2.5  # unlinked unit and a board task with no goal
    assert items[0]["board_tasks"] == {"t-2"}


def test_more_than_nine_goals_fold_into_other_and_cap_holds():
    m = _mod()
    units = [_unit(f"u{i}", f"t-{i}", 12 - i) for i in range(12)] + [_unit("z", None, 1.0)]
    goal_of = {f"t-{i}": f"g-{i}" for i in range(12)}
    titles = {f"t-{i}": ("", f"Goal {i}", "B") for i in range(12)}
    items = m.project_items(units, goal_of, titles)
    assert len(items) == m.MAX_ITEMS
    kinds = [i["kind"] for i in items]
    assert kinds.count("goal") == m.MAX_GOALS - 1 and kinds[-2] == "other" and kinds[-1] == "nogoal"
    other = items[-2]
    assert len(other["folded"]) == 12 - (m.MAX_GOALS - 1)
    assert other["est"] == sum(f["est"] for f in other["folded"])
    # nothing lost: every unit's output is in exactly one item
    assert abs(sum(i["est"] for i in items) - sum(u["est"] for u in units)) < 1e-9


def test_nine_goals_or_fewer_are_not_folded():
    m = _mod()
    units = [_unit(f"u{i}", f"t-{i}", 1.0) for i in range(9)]
    items = m.project_items(units, {f"t-{i}": f"g-{i}" for i in range(9)}, {})
    assert len(items) == 9 and all(i["kind"] == "goal" for i in items)
    assert items[0]["title"] == "g-0"  # falls back to the goal id when the store has no title


def test_rendered_page_is_static_html_with_tables_items_and_the_two_questions():
    """The mockup viewer does not run scripts: every table row, bar and unit
    must be in the markup, and no <script> may be needed to show them."""
    m = _mod()
    unit = {**_unit("u1", "t-1", 2.5), "board_title": "Board one", "labor": 3.0,
            "board_url": "/workspaces/w-1?task=t-1", "components": {"prs": 2, "loc": 1210},
            "first_ts": "2026-09-08T10:00:00+00:00", "last_ts": "2026-09-09T10:00:00+00:00"}
    unit2 = {**_unit("u2", None, 0.5, ho=0.5), "board_title": "", "board_url": "", "labor": 0.5,
             "first_ts": "2026-09-10T10:00:00+00:00", "last_ts": "2026-09-10T11:00:00+00:00"}
    item = {"kind": "goal", "key": "g-1", "title": "Goal <one>", "board": "b", "est": 3.0,
            "hands_on": 0.6, "labor": 3.5, "units": [unit, unit2], "board_tasks": 1, "reviews": 2,
            "first_ts": unit["first_ts"], "last_ts": unit2["last_ts"],
            "his_first_ts": unit["first_ts"], "his_last_ts": unit["first_ts"],
            "components": {"prs": 2, "loc": 1210, "commits": 0, "docs_read": 3, "comments": 0, "messages": 1}}
    item2 = {"kind": "nogoal", "key": "nogoal", "title": "no goal", "board": "", "est": 1.0,
             "hands_on": 1.0, "labor": 1.0, "units": [], "board_tasks": 0, "reviews": 0,
             "first_ts": unit["first_ts"], "last_ts": unit["last_ts"], "components": {}}
    project = {"name": "proj", "est": 4.0, "hands_on": 1.6, "labor": 4.5, "units": 3,
               "coord_est": 0.0, "items": [item, item2]}
    model = {"run_id": "run-x", "start": "2026-09-07", "end": "2026-09-13", "days": 7,
             "total_est": 10.0, "total_hands_on": 2.0, "total_coord": 1.0, "multiplier": 5.0,
             "week_speedup": 10 / 56, "units": 3, "projects": [project]}
    page = m.render_html(model)
    assert "<script" not in page
    assert 'data-project="proj"' in page and "36m" in page          # project row, minutes under an hour
    assert "<details" in page and "Goal &lt;one&gt;" in page        # item, escaped
    assert 'class="his"' in page and "Timeline in" in page          # his span; local-time note
    assert '<tr class="total"><td>total</td>' in page and "5.0x" in page
    assert "wall clock speedup" in page and 'class="ref"' in page   # per-project speedup, 1x reference tick
    assert "Productivity Multiplier" in page and "Wall Clock Speedup" in page
    assert 'class="info"' in page and "Coordination share" not in page
    assert 'style="left:20.2%;width:14.3%"' in page                 # 10:00 on day 2, one day wide
    assert "1 board task · 2 review items · 1,210 lines · 3 docs read · 2 PRs" in page  # second label line
    # unit rows read like their parent: linked board task title, the work under it, a bar on the timeline;
    # no task id shown, and no metric columns
    assert 'data-task-id="u1"' in page and '<a href="/workspaces/w-1?task=t-1" target="_top">Board one</a>' in page
    assert "<code>t-1</code>" not in page and "<th" not in page.split('class="units"')[1].split("</details>")[0]
    assert '<div class="urow" style="--o:0;--h:1" data-task-id="u1">' in page   # first by output, second by hands-on
    assert '<div class="urow" style="--o:1;--h:0" data-task-id="u2">' in page
    assert '<small>u1 · 1,210 lines · 2 PRs</small>' in page                    # unit title, then its components
    assert '<span class="vo">2.5h</span><span class="vh">6m</span>' in page      # both figures; CSS shows one
    # the sort toggle is two radios per project and no script
    assert 'name="sort-proj"' in page and 'for="sort-proj-h">hands-on</label>' in page
    assert 'style="--o:0;--h:1" data-item="proj-g-1"' in page and 'style="--o:1;--h:0" data-item="proj-nogoal"' in page
    assert "Does the hands-on split by project feel about right?" in page
    assert "output ÷ 56h" in page


def test_item_span_covers_every_unit_and_his_span_only_the_units_he_touched():
    m = _mod()
    agent = {**_unit("a", "t-1", 1.0, ho=0.0), "first_ts": "2026-09-07T01:00:00Z", "last_ts": "2026-09-07T02:00:00Z"}
    his = {**_unit("b", "t-1", 1.0, ho=0.1), "first_ts": "2026-09-09T01:00:00Z", "last_ts": "2026-09-09T02:00:00Z"}
    items = m.project_items([agent, his], {"t-1": "g-1"}, {})
    it = items[0]
    assert (it["first_ts"], it["last_ts"]) == (agent["first_ts"], agent["last_ts"][:0] + his["last_ts"])
    assert (it["his_first_ts"], it["his_last_ts"]) == (his["first_ts"], his["last_ts"])


def test_every_figure_uses_the_one_unit_its_size_calls_for():
    m = _mod()
    assert m._dur(0.25) == "15m"        # under an hour
    assert m._dur(16.0) == "16.0h"      # hours up to 7 days of 8h
    assert m._dur(55.9) == "55.9h"
    assert m._dur(56.0) == "7.0d"       # 7 days up: days
    assert m._dur(159.9) == "20.0d"     # under 4 weeks of 5 days
    assert m._dur(160.0) == "4.0w"      # 4 weeks up: weeks
    assert m._dur(1202.2) == "30.1w"
    assert m._h is m._dur and m._H is m._dur and m._Hd is m._dur


def test_review_items_counted_per_board_task_from_his_board_actions():
    import json
    import sqlite3
    m = _mod()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE events (event_id TEXT, kind TEXT, metadata_json TEXT)")
    ev = [("board_action", {"actor_kind": "person", "board_event": "decision.answered", "review_item_id": "r-1", "task_id": "t-a"}),
          ("board_action", {"actor_kind": "person", "board_event": "decision.answered", "review_item_id": "r-1", "task_id": "t-a"}),
          ("board_action", {"actor_kind": "person", "board_event": "decision.info_requested", "review_item_id": "r-2", "task_id": "t-a"}),
          ("board_action", {"actor_kind": "agent", "board_event": "decision.answered", "review_item_id": "r-3", "task_id": "t-a"}),
          ("board_action", {"actor_kind": "person", "board_event": "task.transitioned", "task_id": "t-a"}),
          ("user_message", {"review_item_id": "r-9", "task_id": "t-b"})]
    conn.executemany("INSERT INTO events VALUES (?,?,?)", [(f"e{i}", k, json.dumps(md)) for i, (k, md) in enumerate(ev)])
    # the same item answered twice counts once; an agent's answer and a plain transition do not count
    assert m.load_review_items(conn) == {"t-a": {"r-1", "r-2"}}


def test_top_components_picks_the_three_largest_nonzero():
    m = _mod()
    assert m.top_components({"prs": 1, "loc": 0, "commits": 4, "docs_read": 2, "comments": 9, "messages": 3}) \
        == "9 comments · 3 messages · 2 docs read"  # commits hidden: the PR carries them
    assert m.top_components({"prs": 0, "loc": 0, "commits": 0, "docs_read": 0, "comments": 0, "messages": 0}) == ""
    assert m.top_components({"prs": 1, "loc": 1}) == "1 PR · 1 line"
    # squash commits are the PRs again; commits show only when no PR was merged
    assert m.top_components({"prs": 3, "commits": 3}) == "3 PRs"
    assert m.top_components({"prs": 0, "commits": 3}) == "3 commits"


def test_components_count_pr_lines_once_and_fall_back_to_commits(tmp_path):
    import json
    import sqlite3
    m = _mod()
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE events (event_id TEXT, kind TEXT, source_system TEXT, actor TEXT, metadata_json TEXT);
        CREATE TABLE task_events (task_id TEXT, event_id TEXT);
    """)
    ev = [("e1", "pr_merged", "github", "agent", {"repo": "o/r", "number": 7, "additions": 100, "deletions": 20}),
          ("e2", "pr_merged", "github", "agent", {"repo": "o/r", "number": 7, "additions": 100, "deletions": 20}),
          ("e3", "commit", "git", "agent", {"insertions": 5, "deletions": 1}),
          ("e4", "commit", "git", "agent", {"insertions": 30, "deletions": 2, "excluded_insertions": 9000}),
          ("e5", "review_session", "live_feedback", "user", {"doc_id": "d-a"}),
          ("e6", "review_session", "live_feedback", "user", {"doc_id": "d-a"}),
          ("e7", "user_message", "live_feedback", "user", {}),
          ("e8", "user_message", "claude_transcript", "user", {}),
          ("e9", "user_message", "claude_transcript", "agent", {})]
    conn.executemany("INSERT INTO events VALUES (?,?,?,?,?)", [(a, b, c, d, json.dumps(e)) for a, b, c, d, e in ev])
    conn.executemany("INSERT INTO task_events VALUES (?,?)",
                     [("A", "e1"), ("A", "e2"), ("A", "e3"), ("A", "e5"), ("A", "e6"), ("A", "e7"), ("A", "e8"), ("A", "e9"),
                      ("B", "e4")])
    comps = m.load_components(conn)
    # A: the PR counted once (120 lines, not the commit's 6), one doc, one comment, one message of his
    assert comps["A"] == {"prs": 1, "loc": 120, "data_loc": 0, "commits": 1, "docs_read": 1, "comments": 1, "messages": 1}
    # B: no merged PR, so its commit's lines count
    # and the checked-in data its project excludes from pricing shows apart, not as code
    assert comps["B"] == {"prs": 0, "loc": 32, "data_loc": 9000, "commits": 1, "docs_read": 0, "comments": 0, "messages": 0}
    assert m.top_components(comps["B"]) == "9,000 data lines (not priced) · 32 lines · 1 commit"
