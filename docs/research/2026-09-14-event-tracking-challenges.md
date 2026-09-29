# Event-tracking challenges: where the data had to be forced into shape

A running log, started 2026-09-14 at Bryan's request. Each entry is something
the pipeline has to infer, repair or guess at because the event stream does
not record it directly. Each entry names the change at the source that would
make the workaround unnecessary. When the Apr–Sep re-run is done, this goes to
the claude-workspaces board as proposals.

The columns: **Cost** is what the workaround costs today, or what went wrong
before it existed. **Source fix** is the smallest instrumentation change that
removes the guess.

## 1. Which task a Claude session's turn belongs to

- **What we infer:** the segmenter rebuilds task boundaries from message
  timing, topic cues, issue refs and PR sealing. Nothing in a transcript says
  "this turn serves workspace task t-…".
- **Cost:**
  - Pass 3 attached agent turns to the project's most recent task, not their own
    session's task, so parallel worktrees stole each other's work. 818k of
    1.07M agent events (77%) sat on a task that did not hold their session's
    messages.
  - 585 tasks (Σest 1,284h) came out as agent-only "delegated" fragments with
    zero hands-on.
  - One orchestrator session ran for a week across ~260 PRs and stayed one task
    titled "Review of PR #556", estimated at 9h.
- **Workaround (Sep 14):** attach agent turns to their own session's most
  recent task. Hand a turn to no other session's task, even when its own
  conversation went quiet more than 6h ago. On Aug 24–Sep 13 this cut
  cross-session events from 90% to 37%. The remainder is agent work more than
  6h after his last message, rebuilt as delegated tasks. That remainder is
  exactly where "is this still his ask, or a new agent-triggered task?" cannot
  be answered from the stream.
- **Source fix:** stamp the workspace `taskId` (and dispatch id) onto the
  session whenever a task is claimed or a dispatch registered, and onto every
  subagent the session spawns. A turn would then carry its task, and
  segmentation becomes a join instead of a guess.

## 2. A PR opened in one worktree seals every task in the project

- **What we infer:** a `pr_opened` closes the "current" task, so the next
  message starts a new one. With many parallel worktrees, one session's PR
  closes every other session's task too.
- **Cost:** the more complete the PR data, the more over-fragmented the tasks.
  With the full PR fetch (1,530 PR events for Aug 24–Sep 13 instead of 465),
  tasks holding his messages went from 370 to 518.
- **Workaround (Sep 14):** a PR seals only the tasks of sessions whose
  transcript links its URL; a PR nobody links still seals the project. That
  brought 518 back to 408. It works only because `gh pr create` happens to
  print the URL.
- **Source fix:** the same task stamp as #1. A PR created by a session carrying
  a task id names its task, and seals nothing else.

## 3. PR history silently capped at the newest 200

- **What we infer:** nothing. `gh pr list --limit 200` returned each repo's
  newest 200 PRs and exited 0.
- **Cost:** the Sep 1–7 week held 15 of 262 merged PRs, and every audit passed.
- **Fix (done, PR #33):** date-bisected search, a `pr_fetch.json` record, and
  two audits. Not a workspace issue; recorded because a fetch that drops rows
  without saying so is the pattern to design out everywhere.

## 4. Comments carry no duration and no "what was I reading"

- **What we infer:** each comment's typing time is modelled from word count at
  40 wpm, plus 1 minute of assumed reading when no read session or doc-open on
  the same doc sits within a minute before it.
- **Cost:**
  - Before the model, comments were instants, so 15 comments on one doc scored
    0.04h.
  - Now 785 of 3,079 comments use the assumed minute, because there is no
    read tracking around them (e.g. a mockup doc on Aug 10–11).
- **Source fix:** record `composeStartedAt` on each comment (first keystroke in
  the box), and a read session for any doc open long enough to comment on.

## 5. The same comment recorded twice

- **What we infer:** the activity stream repeats each comment as an
  `activity_type: comment` record next to the comment itself. Some comments
  also appear twice with the same word count, seconds or minutes apart.
- **Cost:** the activity copy made 3,059 of 3,079 comments look "led to" by
  prior activity, until copies were excluded by type. Near-duplicates are
  rare: 20 pairs in 3,079 comments with the same doc, words and text within
  10 minutes. 5 are one thread's comment posted twice seconds apart (a double
  submit, different comment ids). 15 are the same short text ("looks good")
  on different threads. Their windows overlap, so the union absorbs them.
- **Source fix:** one record per comment with a stable id. Where a copy must
  exist, give it a `copyOf` pointer.

## 6. Polling keeps an agent "busy"

- **What we infer:** wall clock joins agent events under 30 minutes apart. A
  background benchmark posting progress every 20 minutes looks like
  continuous work.
- **Cost:** a539856708 counted about 1h of pure polling inside a 4h burst.
- **Source fix:** mark waits explicitly, e.g. a `wait`/`poll` kind on the
  tool call, or `declare_wait` on the board, so a reader can tell watching from
  working.

## 7. Board events without a doc id

- **What we infer:** board actions carry `taskId` but not `docId`, so 1,779
  events across 511 workspace tasks had no sibling to join to.
- **Source fix:** carry `docId` on board events (already filed on 2026-09-12).

## 8. One person, four handles

- **What we infer:** Bryan appears as "Bryan" (`known-*`), "Bryan Chan"
  (`anon-*`), "Fryanpan" (`user-*`), and generated `Anon-xxxxxx` handles.
  `actor: "person"` also covers agents like "Claude" and "Chat Audit".
- **Cost:** 29% of his own comments had never counted, and machine comments
  counted as his.
- **Source fix:** a stable account id on every record, plus `actorKind:
  human|agent` set by the server rather than inferred from a name.

## 9. Transcripts hide who typed a message

- **What we infer:**
  - Peer relays arrive as ordinary `user` records with no marker.
  - Subagent records carry the parent's `sessionId`, and `seq` restarts per file.
- **Cost:** 132k of 155k "typed" words in one week were relays. Subagent id
  collisions destroyed 80% of one session's events.
- **Source fix (Claude Code, not the workspace):** `promptSource` on every
  user record, including relays, and a lane id on every subagent record.

## 10. What started an agent's work when he had not typed yet

- **What we infer:** a session can work for days on loop ticks, board wakes
  and peer relays before he types in it. The stream shows agent turns with
  no ask in front of them, so the segmenter guesses which conversation they
  lead into.
- **Cost:** a bug in that guess attached 1,219 agent events from Aug 24 to a
  conversation that began Sep 8, which made one task 15 days long (fixed in
  PR #35). Such work now becomes its own delegated task, titled from its
  first agent line rather than from the ask that caused it.
- **Source fix:** every wake a session receives (loop tick, board event, peer
  message) records its trigger: task id, event type and sender. Delegated
  work then names its cause instead of floating.

## 11. Which conversation made a commit

- **What we infer:** a commit carries a sha, a time and a subject line. The
  segmenter guesses its conversation from shared title words and nearness in
  time. When no conversation matches, commits matched each other instead, so
  one commit collected days of later commits by words like "server" or "task".
- **Cost:** 54 tasks built this way spanned more than a day and held 1,756h,
  26% of estimated output, most with under 15 minutes of hands-on. An 80h
  "Yea!" task was three days of commits joined to a five-minute chat. Fixed
  (PR #37): a task of artifacts alone takes only its own PR and squash
  commit. The cost of the fix is 273 more commits and PRs standing alone
  with no measured time.
- **Source fix:** the session that made a commit is already written into it.
  Claude Code commits end with a `Claude-Session:` trailer, and PR bodies
  carry the same link. Reading it (the extractor reads only the subject
  today) joins most agent commits exactly. For commits made elsewhere, a
  session id in the branch name or a trailer added by a hook does the same.
