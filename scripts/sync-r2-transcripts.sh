#!/usr/bin/env bash
# sync-r2-transcripts.sh — pull product-engineer agent transcripts from R2
# into ~/dev/weekly-review-transcripts/r2_product_engineer/, where the
# weekly-review CLI's auto-discovery will pick them up on the next run.
#
# Requires PRODUCT_ENGINEER_WORKER_URL and PRODUCT_ENGINEER_API_KEY in env
# (see ~/.config/weekly-review/env — auto-loaded by the CLI; use the same
# file or `export` them in your shell before running this script).
#
# The R2 bucket holds Claude Agent SDK transcript .jsonl files keyed by
# `{agentUuid}-{sessionId}.jsonl`. We mirror the cwd-folder layout
# (-Users-<you>-...) so the existing claude_transcript extractor can
# slug-route them via projects.yaml without modification.

set -euo pipefail

ROOT="${TRANSCRIPTS_ROOT:-$HOME/dev/weekly-review-transcripts}"
DEST="$ROOT/r2_product_engineer"
WORKER="${PRODUCT_ENGINEER_WORKER_URL:-}"
KEY="${PRODUCT_ENGINEER_API_KEY:-}"
LIMIT="${R2_LIMIT:-1000}"

if [ -z "$WORKER" ] || [ -z "$KEY" ]; then
  # Try to source from the weekly-review env file if not in shell env
  if [ -f "$HOME/.config/weekly-review/env" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$HOME/.config/weekly-review/env"
    set +a
    WORKER="${PRODUCT_ENGINEER_WORKER_URL:-}"
    KEY="${PRODUCT_ENGINEER_API_KEY:-}"
  fi
fi

if [ -z "$WORKER" ] || [ -z "$KEY" ]; then
  echo "error: PRODUCT_ENGINEER_WORKER_URL and PRODUCT_ENGINEER_API_KEY must be set" >&2
  echo "       (in ~/.config/weekly-review/env or in your shell env)" >&2
  exit 2
fi

mkdir -p "$DEST"

echo "Listing R2 transcripts from $WORKER ..." >&2
listing=$(curl -fsS -H "X-API-Key: $KEY" "$WORKER/api/transcripts?limit=$LIMIT")

# Each row from /api/transcripts is { r2Key, ... cwd?, sessionId?, ... }
# Schema verified at api/src/observability.ts:listTranscripts.
keys=$(echo "$listing" | jq -r '.transcripts[]?.r2Key // empty')
total=$(echo "$keys" | grep -c . || true)
echo "$total transcripts available" >&2

downloaded=0
skipped=0
for r2_key in $keys; do
  # Filename layout: place under DEST/<r2_key>.jsonl directly. No cwd
  # reconstruction yet — projects.yaml routing happens at extraction time
  # via the cwd recorded INSIDE each transcript line, not the file path.
  out="$DEST/$r2_key"
  case "$out" in
    *.jsonl) ;;
    *) out="$out.jsonl" ;;
  esac
  if [ -s "$out" ]; then
    skipped=$((skipped + 1))
    continue
  fi
  encoded=$(printf '%s' "$r2_key" | jq -sRr @uri)
  if curl -fsS -H "X-API-Key: $KEY" "$WORKER/api/transcripts/$encoded" -o "$out.tmp"; then
    mv "$out.tmp" "$out"
    downloaded=$((downloaded + 1))
  else
    echo "  warn: failed to fetch $r2_key" >&2
    rm -f "$out.tmp"
  fi
done

echo "Done. Downloaded $downloaded, skipped $skipped (already present)." >&2
echo "Next: cd to your weekly-review checkout and re-run, e.g.:" >&2
echo "  weekly-review --since 2026-03-29 --until 2026-04-05 --refresh-estimates" >&2
