"""Standalone runner for the mechanical validity checks.

Thin wrapper over weekly_review.validate.run_checks (the canonical
implementation, also wired into `weekly-review --validate`).

Usage: uv run python3 scripts/mechanical_checks.py --run-id <run> [--avail-h N]
"""
from __future__ import annotations
import argparse
from weekly_review.validate import run_checks


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--avail-h", type=float, default=None,
                   help="Stated hands-on availability for the window (for check 3)")
    a = p.parse_args()
    run_checks(a.run_id, a.avail_h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
