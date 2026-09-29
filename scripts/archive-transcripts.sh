#!/usr/bin/env bash
# Periodic archiver: mirror ~/.claude/projects/ to ~/dev/weekly-review-transcripts/mac_mini/.
#
# Guarantees against data loss:
#   - Files deleted from SRC  -> stay in DEST      (no --delete).
#   - Files modified in SRC   -> new version in DEST, OLD version moved to mac_mini_history/<ts>/
#                                 so overwrites never destroy content  (--backup + --backup-dir).
#   - Files appended in SRC   -> newer version copied; no data loss because the newer version
#                                 includes all previous content.
#   - Files never modified    -> no-op (--update skips up-to-date files).
#
# Net effect: the archive is a monotonically-growing union of every state we've ever seen.
# Runs hourly via ~/Library/LaunchAgents/com.weekly-review.archive.plist.

set -euo pipefail

SRC="${WEEKLY_REVIEW_CLAUDE_PROJECTS:-$HOME/.claude/projects}"
DEST="${WEEKLY_REVIEW_ARCHIVE:-$HOME/dev/weekly-review-transcripts/mac_mini}"
HISTORY="${WEEKLY_REVIEW_HISTORY:-$HOME/dev/weekly-review-transcripts/mac_mini_history}"
LOG="${WEEKLY_REVIEW_ARCHIVE_LOG:-$HOME/dev/weekly-review-transcripts/archive.log}"

mkdir -p "$DEST" "$HISTORY" "$(dirname "$LOG")"

{
  echo "=== archive-transcripts.sh $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "src:  $SRC"
  echo "dest: $DEST"
  if [ ! -d "$SRC" ]; then
    echo "source dir does not exist; nothing to do"
    exit 0
  fi

  TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP_DIR="$HISTORY/$TIMESTAMP"

  rsync -a --update \
        --backup --backup-dir="$BACKUP_DIR" \
        --ignore-errors \
        "$SRC/" "$DEST/"

  # Remove empty backup-dir when no overwrites happened (the common case).
  if [ -d "$BACKUP_DIR" ] && [ -z "$(ls -A "$BACKUP_DIR" 2>/dev/null)" ]; then
    rmdir "$BACKUP_DIR" 2>/dev/null || true
  elif [ -d "$BACKUP_DIR" ]; then
    echo "note: preserved overwritten files in $BACKUP_DIR"
  fi

  echo "done"
} >> "$LOG" 2>&1
