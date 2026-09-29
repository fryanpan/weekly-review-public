"""Run management: each extraction pass gets a run_id + dir."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml


DATA_DIR = Path(os.environ.get("WEEKLY_REVIEW_DATA", Path.home() / ".local" / "share" / "weekly-review"))


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")


@dataclass
class Run:
    run_id: str
    schema_version: str
    path: Path

    @property
    def events_dir(self) -> Path:
        return self.path / "events"


class RunManager:
    def __init__(self, base: Path | None = None):
        self.base = base or (DATA_DIR / "runs")

    def start_run(self, schema_version: str) -> Run:
        run_id = new_run_id()
        path = self.base / run_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "events").mkdir(exist_ok=True)
        (path / "run.yaml").write_text(
            yaml.safe_dump({
                "run_id": run_id,
                "schema_version": schema_version,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        )
        return Run(run_id=run_id, schema_version=schema_version, path=path)

    def list_runs(self) -> list[Run]:
        if not self.base.exists():
            return []
        runs: list[Run] = []
        for p in sorted(self.base.iterdir(), reverse=True):
            meta_file = p / "run.yaml"
            if not meta_file.exists():
                continue
            meta = yaml.safe_load(meta_file.read_text())
            runs.append(Run(run_id=meta["run_id"], schema_version=meta["schema_version"], path=p))
        return runs
