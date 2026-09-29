"""CLI wrapper for the task_facts export (logic lives in weekly_review.export).

Usage:
  uv run python3 scripts/export_task_facts.py [--run-id run-...] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from weekly_review.export import export_task_facts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()
    csv_path, sq_path, n = export_task_facts(args.run_id, args.out_dir)
    print(f"exported {n} tasks")
    print(f"  {csv_path}   (Tableau: Connect > Text file)")
    print(f"  {sq_path}   (DuckDB/sqlite3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
