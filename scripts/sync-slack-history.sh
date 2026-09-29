#!/usr/bin/env bash
# sync-slack-history.sh — pull Slack channel history into a local archive
# under ~/dev/weekly-review-transcripts/slack/, where the weekly-review
# extractor will read it on the next run.
#
# Designed for a one-shot historical pull (W13-W15 era when the user dispatched
# work via Slack→Linear→agent). Idempotent: skips channels already pulled
# in the date range. Future updates can be incremental by passing --since.
#
# Requires SLACK_USER_TOKEN in env or ~/.config/weekly-review/env
# (a User OAuth Token starting with `xoxp-`, scopes channels:history,
# channels:read, groups:history, groups:read, im:history, im:read,
# mpim:history, mpim:read, users:read).
#
# Usage:
#   sync-slack-history.sh                           # all-time, all channels the user is in
#   sync-slack-history.sh --since 2026-03-25        # only fetch newer
#   sync-slack-history.sh --channels C0EXAMPLE      # specific channel(s)

set -euo pipefail

ROOT="${TRANSCRIPTS_ROOT:-$HOME/dev/weekly-review-transcripts}"
DEST="$ROOT/slack"
TOKEN="${SLACK_USER_TOKEN:-}"
SINCE_ISO="${1:-}"
SINCE_TS=""
CHANNELS_FILTER=""

# Parse flags
while [ $# -gt 0 ]; do
  case "$1" in
    --since) SINCE_ISO="$2"; shift 2 ;;
    --channels) CHANNELS_FILTER="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# Source env file if not in shell env
if [ -z "$TOKEN" ] && [ -f "$HOME/.config/weekly-review/env" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$HOME/.config/weekly-review/env"
  set +a
  TOKEN="${SLACK_USER_TOKEN:-}"
fi

if [ -z "$TOKEN" ]; then
  echo "error: SLACK_USER_TOKEN must be set (in shell or ~/.config/weekly-review/env)" >&2
  exit 2
fi

# Convert --since ISO to Slack ts (epoch float)
if [ -n "$SINCE_ISO" ]; then
  SINCE_TS=$(date -j -f "%Y-%m-%d" "$SINCE_ISO" +%s 2>/dev/null || \
             date -d "$SINCE_ISO" +%s 2>/dev/null)
fi

mkdir -p "$DEST"

call() {
  # POST to Slack Web API. $1 = method, $2... = form data
  local method="$1"; shift
  curl -fsS -X POST "https://slack.com/api/$method" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/x-www-form-urlencoded; charset=utf-8" \
    --data-urlencode "limit=200" \
    "$@"
}

# 1) Cache user list once (resolve U... ids to display names later)
USERS_PATH="$DEST/_users.json"
echo "Fetching user directory ..." >&2
call users.list > "$USERS_PATH"
n_users=$(jq '.members | length' "$USERS_PATH")
echo "  $n_users users cached" >&2

# 2) List channels (public + private + DMs + group DMs the user is in)
echo "Listing channels ..." >&2
CHANNELS_PATH="$DEST/_channels.json"
call conversations.list \
  --data-urlencode "types=public_channel,private_channel,mpim,im" \
  > "$CHANNELS_PATH"
n_channels=$(jq '.channels | length' "$CHANNELS_PATH")
echo "  $n_channels channels visible" >&2

# 3) For each channel, page through history
fetched_msgs=0
fetched_channels=0
total_channels=$n_channels

ids=$(jq -r '.channels[].id' "$CHANNELS_PATH")
if [ -n "$CHANNELS_FILTER" ]; then
  # filter to only the IDs in CHANNELS_FILTER (comma- or space-separated)
  filter_pattern=$(echo "$CHANNELS_FILTER" | tr ',' '|' | tr -d ' ')
  ids=$(echo "$ids" | grep -E "^($filter_pattern)$" || true)
  total_channels=$(echo "$ids" | grep -c . || echo 0)
  echo "Filtered to $total_channels channel(s)" >&2
fi

for cid in $ids; do
  fetched_channels=$((fetched_channels + 1))
  # Resolve a friendly name for the directory
  name=$(jq -r --arg c "$cid" '.channels[] | select(.id==$c) | (.name // (.user // "im")) ' "$CHANNELS_PATH")
  outdir="$DEST/$cid"
  mkdir -p "$outdir"
  out="$outdir/history.jsonl"
  cursor=""
  msgs_in_channel=0
  echo "[$fetched_channels/$total_channels] $cid ($name) ..." >&2

  while :; do
    qs=(--data-urlencode "channel=$cid")
    [ -n "$cursor" ] && qs+=(--data-urlencode "cursor=$cursor")
    [ -n "$SINCE_TS" ] && qs+=(--data-urlencode "oldest=$SINCE_TS")
    page=$(call conversations.history "${qs[@]}")
    ok=$(echo "$page" | jq -r '.ok')
    if [ "$ok" != "true" ]; then
      err=$(echo "$page" | jq -r '.error // "unknown"')
      echo "  warn: $cid history error=$err — skipping" >&2
      break
    fi
    # Append messages as JSONL
    echo "$page" | jq -c '.messages[]?' >> "$out"
    n=$(echo "$page" | jq '.messages | length')
    msgs_in_channel=$((msgs_in_channel + n))
    cursor=$(echo "$page" | jq -r '.response_metadata.next_cursor // ""')
    [ -z "$cursor" ] && break
    # be polite — slack tier-3 method is 50 req/min
    sleep 1.2
  done

  fetched_msgs=$((fetched_msgs + msgs_in_channel))
  echo "  $msgs_in_channel msgs → $out" >&2

  # Fetch thread replies — this is where the actual back-and-forth happens.
  # For each top-level message with reply_count > 0 AND the user in reply_users,
  # call conversations.replies and store under threads/<thread_ts>.jsonl.
  # Skip threads the user didn't participate in (bot-to-bot status updates).
  threads_dir="$outdir/threads"
  mkdir -p "$threads_dir"
  thread_count=0
  thread_msgs=0
  bryan_uid="U0AH7G5GKNW"
  while read -r thread_ts; do
    [ -z "$thread_ts" ] && continue
    out_thread="$threads_dir/$thread_ts.jsonl"
    [ -s "$out_thread" ] && continue  # idempotent
    rcursor=""
    while :; do
      qs2=(--data-urlencode "channel=$cid" --data-urlencode "ts=$thread_ts")
      [ -n "$rcursor" ] && qs2+=(--data-urlencode "cursor=$rcursor")
      rpage=$(call conversations.replies "${qs2[@]}")
      rok=$(echo "$rpage" | jq -r '.ok')
      if [ "$rok" != "true" ]; then break; fi
      echo "$rpage" | jq -c '.messages[]?' >> "$out_thread"
      n_replies=$(echo "$rpage" | jq '.messages | length')
      thread_msgs=$((thread_msgs + n_replies))
      rcursor=$(echo "$rpage" | jq -r '.response_metadata.next_cursor // ""')
      [ -z "$rcursor" ] && break
      sleep 1.2
    done
    thread_count=$((thread_count + 1))
    sleep 1.2
  done < <(jq -r --arg uid "$bryan_uid" 'select(.reply_users // [] | index($uid)) | .thread_ts' "$out" 2>/dev/null | sort -u)
  if [ "$thread_count" -gt 0 ]; then
    echo "    + $thread_count threads, $thread_msgs replies" >&2
    fetched_msgs=$((fetched_msgs + thread_msgs))
  fi
done

echo "" >&2
echo "Done. Fetched $fetched_msgs messages across $fetched_channels channels (incl. threads where the user replied)." >&2
echo "Next: re-run weekly-review and the slack extractor will pick up $DEST/" >&2
