"""BI export: truncation guard."""
import pytest



# --- BI table must never be truncated by a narrow run -----------------------

def test_export_refuses_to_drop_weeks_already_in_the_table(tmp_path, monkeypatch):
    """A --since-limited run destroyed 13 weeks of history on 2026-07-12.
    The export must now refuse rather than silently truncate."""
    import csv as _csv
    from weekly_review import export as ex

    csv_path = tmp_path / "task_facts.csv"
    with open(csv_path, "w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=ex.COLUMNS)
        w.writeheader()
        for wk in ("2026-04-13", "2026-05-04", "2026-06-29"):
            row = {c: "" for c in ex.COLUMNS}
            row["week"] = wk
            w.writerow(row)

    # New run only covers the most recent week.
    narrow = [{c: "" for c in ex.COLUMNS} | {"week": "2026-06-29"}]
    monkeypatch.setattr(ex, "build_rows", lambda rid: narrow)

    with pytest.raises(ex.ExportWouldTruncate) as err:
        ex.export_task_facts("run-narrow", out_dir=tmp_path)
    assert "2026-04-13" in str(err.value)

    # The existing export is untouched — still all three weeks.
    with open(csv_path, newline="") as f:
        weeks = {r["week"] for r in _csv.DictReader(f)}
    assert weeks == {"2026-04-13", "2026-05-04", "2026-06-29"}


def test_export_allows_a_full_run_and_an_explicit_shrink(tmp_path, monkeypatch):
    from weekly_review import export as ex

    rows = [{c: "" for c in ex.COLUMNS} | {"week": w, "task_id": w}
            for w in ("2026-04-13", "2026-06-29")]
    monkeypatch.setattr(ex, "build_rows", lambda rid: rows)
    _c, _s, n = ex.export_task_facts("run-full", out_dir=tmp_path)
    assert n == 2

    narrow = [{c: "" for c in ex.COLUMNS} | {"week": "2026-06-29", "task_id": "x"}]
    monkeypatch.setattr(ex, "build_rows", lambda rid: narrow)
    # Refused by default...
    with pytest.raises(ex.ExportWouldTruncate):
        ex.export_task_facts("run-narrow", out_dir=tmp_path)
    # ...but permitted when asked for deliberately.
    _c, _s, n = ex.export_task_facts("run-narrow", out_dir=tmp_path, allow_shrink=True)
    assert n == 1
