import time
from pathlib import Path
from weekly_review.runs import RunManager, new_run_id


def test_run_id_is_sortable():
    a = new_run_id()
    time.sleep(1.1)  # seconds-precision timestamps
    b = new_run_id()
    assert a < b


def test_run_manager_creates_dirs(tmp_path: Path):
    mgr = RunManager(base=tmp_path)
    run = mgr.start_run(schema_version="v1")
    assert run.events_dir.exists()
    assert run.path.is_dir()
    meta = run.path / "run.yaml"
    assert meta.exists()
    assert "schema_version: v1" in meta.read_text()


def test_list_runs_newest_first(tmp_path: Path):
    mgr = RunManager(base=tmp_path)
    r1 = mgr.start_run(schema_version="v1")
    time.sleep(1.1)
    r2 = mgr.start_run(schema_version="v1")
    listed = mgr.list_runs()
    assert [r.run_id for r in listed] == [r2.run_id, r1.run_id]
