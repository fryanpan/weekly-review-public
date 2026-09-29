// Periodic archiver: mirror ~/.claude/projects/ to ~/dev/weekly-review-transcripts/mac_mini/.
//
// This is the launchd twin of archive-transcripts.sh, with the same guarantees:
//   - Files deleted from SRC  -> stay in DEST        (no delete pass).
//   - Files modified in SRC   -> new version in DEST, OLD version moved to
//                                mac_mini_history/<ts>/<same relative path>, so an
//                                overwrite never destroys content.
//   - DEST newer than SRC     -> left alone           (rsync --update).
//   - Files never modified    -> no-op.
//   - One unreadable file     -> logged, skipped, the rest still copied (--ignore-errors),
//                                and the run exits 1 so the failure is visible.
//
// Why bun and not bash: under launchd, Apple-signed /bin/bash, /bin/ls and /usr/bin/rsync
// are denied every operation on a second volume, where ~/.claude and ~/dev resolve. The job
// died at exec for the third time after the 2026-09-11 reboot. A launchd-spawned process
// reaches that volume only if ITS OWN EXECUTABLE is on the boot disk and holds Full Disk
// Access, so the plist runs a boot-disk bun and this file does every file operation itself.
// Shelling out to rsync from here would reintroduce the exact failure.
//
// Install: see scripts/com.weekly-review.archive.plist.

import {
  copyFileSync, existsSync, lstatSync, mkdirSync, readdirSync, readlinkSync,
  renameSync, rmdirSync, statSync, symlinkSync, utimesSync, appendFileSync,
} from "node:fs";
import { dirname, join, relative } from "node:path";
import { homedir } from "node:os";

const HOME = homedir();
const SRC = process.env.WEEKLY_REVIEW_CLAUDE_PROJECTS ?? join(HOME, ".claude/projects");
const DEST = process.env.WEEKLY_REVIEW_ARCHIVE ?? join(HOME, "dev/weekly-review-transcripts/mac_mini");
const HISTORY = process.env.WEEKLY_REVIEW_HISTORY ?? join(HOME, "dev/weekly-review-transcripts/mac_mini_history");
const LOG = process.env.WEEKLY_REVIEW_ARCHIVE_LOG ?? join(HOME, "dev/weekly-review-transcripts/archive.log");

const now = new Date();
const stamp = now.toISOString().replace(/\.\d{3}Z$/, "Z");
const BACKUP_DIR = join(HISTORY, stamp.replace(/[-:]/g, ""));

const lines: string[] = [];
const log = (s: string) => lines.push(s);

// rsync compares modification times in whole seconds on this platform; do the same so a
// sub-second difference introduced by the copy itself never re-copies a file every hour.
const sec = (ms: number) => Math.floor(ms / 1000);

let copied = 0, replaced = 0, errors = 0;

function copyPreservingTimes(src: string, dest: string, atimeMs: number, mtimeMs: number) {
  // Write beside the destination and rename into place, as rsync does, so a reader never
  // sees a half-written transcript and an interrupted run leaves the old copy intact.
  const tmp = join(dirname(dest), `.${Math.random().toString(36).slice(2)}.archive-tmp`);
  copyFileSync(src, tmp);
  utimesSync(tmp, atimeMs / 1000, mtimeMs / 1000);
  return tmp;
}

function syncFile(src: string, rel: string) {
  const dest = join(DEST, rel);
  const s = statSync(src);
  let d: ReturnType<typeof lstatSync> | null = null;
  try { d = lstatSync(dest); } catch { d = null; }

  if (d) {
    if (sec(d.mtimeMs) > sec(s.mtimeMs)) return;                          // --update
    if (sec(d.mtimeMs) === sec(s.mtimeMs) && d.size === s.size) return;   // unchanged
  }
  mkdirSync(dirname(dest), { recursive: true });
  const tmp = copyPreservingTimes(src, dest, s.atimeMs, s.mtimeMs);
  if (d) {
    const kept = join(BACKUP_DIR, rel);
    mkdirSync(dirname(kept), { recursive: true });
    renameSync(dest, kept);
    replaced++;
  }
  renameSync(tmp, dest);
  copied++;
}

function walk(dir: string) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch (e) {
    errors++; log(`error: cannot list ${dir}: ${(e as Error).message}`);
    return;
  }
  for (const ent of entries) {
    const path = join(dir, ent.name);
    const rel = relative(SRC, path);
    try {
      if (ent.isDirectory()) {
        mkdirSync(join(DEST, rel), { recursive: true });
        walk(path);
      } else if (ent.isSymbolicLink()) {
        const dest = join(DEST, rel);
        if (!existsSync(dest)) {
          mkdirSync(dirname(dest), { recursive: true });
          symlinkSync(readlinkSync(path), dest);
          copied++;
        }
      } else if (ent.isFile()) {
        syncFile(path, rel);
      }
    } catch (e) {
      errors++; log(`error: ${rel}: ${(e as Error).message}`);
    }
  }
}

log(`=== archive-transcripts.ts ${stamp} ===`);
log(`src:  ${SRC}`);
log(`dest: ${DEST}`);
let code = 0;
try {
  mkdirSync(DEST, { recursive: true });
  mkdirSync(HISTORY, { recursive: true });
  if (!existsSync(SRC)) {
    log("source dir does not exist; nothing to do");
  } else {
    walk(SRC);
    if (existsSync(BACKUP_DIR)) {
      try { rmdirSync(BACKUP_DIR); } catch { log(`note: preserved overwritten files in ${BACKUP_DIR}`); }
    }
    log(`copied ${copied} (${replaced} replaced, old versions kept), ${errors} error(s)`);
    code = errors ? 1 : 0;
    log(errors ? "done with errors" : "done");
  }
} catch (e) {
  log(`fatal: ${(e as Error).message}`);
  code = 2;
}

const text = lines.join("\n") + "\n";
try {
  mkdirSync(dirname(LOG), { recursive: true });
  appendFileSync(LOG, text);
} catch {
  // The log is on the same volume as the archive; if it cannot be written, stderr is the
  // only place left, and launchd's StandardErrorPath is on the boot disk.
}
if (code) process.stderr.write(text);
process.exit(code);
