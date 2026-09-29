from pathlib import Path
from weekly_review.config import load_projects


def test_load_projects_maps_cwd_to_slug(tmp_path: Path):
    cfg_file = tmp_path / "projects.yaml"
    cfg_file.write_text(
        """
projects:
  - slug: bike-map
    cwd_patterns:
      - "/Users/*/dev/family-bike-map"
      - "/Users/*/dev/family-bike-map/**"
      - "/Users/*/dev/family-bike-map--claude-worktrees-*"
  - slug: weekly-review
    cwd_patterns:
      - "/Users/*/dev/weekly-review"
      - "/Users/*/dev/weekly-review/**"
"""
    )
    projects = load_projects(cfg_file)
    assert projects.slug_for("/Users/me/dev/family-bike-map") == "bike-map"
    assert projects.slug_for("/Users/me/dev/family-bike-map--claude-worktrees-fix") == "bike-map"
    assert projects.slug_for("/Users/me/dev/weekly-review/subdir") == "weekly-review"
    assert projects.slug_for("/Users/me/dev/unknown-project") is None


def test_load_projects_missing_file_returns_empty(tmp_path: Path):
    projects = load_projects(tmp_path / "nope.yaml")
    assert projects.projects == []


def test_slug_for_normalizes_volumes_data_prefix(tmp_path: Path):
    # A home directory on a second volume: transcripts emit
    # /Volumes/Data/Users/me/dev/<project> rather than /Users/me/dev/<project>.
    cfg_file = tmp_path / "projects.yaml"
    cfg_file.write_text(
        """
projects:
  - slug: bike-map
    cwd_patterns:
      - "/Users/*/dev/family-bike-map"
      - "/Users/*/dev/family-bike-map/**"
"""
    )
    projects = load_projects(cfg_file)
    # Normalized path matches the existing /Users/* pattern.
    assert projects.slug_for("/Volumes/Data/Users/me/dev/family-bike-map") == "bike-map"
    assert projects.slug_for("/Volumes/Data/Users/me/dev/family-bike-map/sub/dir") == "bike-map"
    # Non-Volumes/Data paths still route normally.
    assert projects.slug_for("/Users/me/dev/family-bike-map") == "bike-map"
    # Paths that don't start with /Volumes/Data/Users/ are NOT stripped — e.g. /Volumes/Other
    # should not silently route to a "/Users/..." pattern.
    assert projects.slug_for("/Volumes/Other/Users/me/dev/family-bike-map") is None


def test_project_client_field(tmp_path):
    from weekly_review.config import load_projects
    y = tmp_path / "projects.yaml"
    y.write_text(
        "projects:\n"
        "- slug: client-a\n"
        "  client: CA\n"
        "  cwd_patterns: ['/Users/*/dev/wrapper-project/**']\n"
        "- slug: family-bike-map\n"
        "  cwd_patterns: ['/Users/*/dev/family-bike-map/**']\n"
    )
    cfg = load_projects(y)
    assert cfg.client_for("client-a") == "CA"
    assert cfg.client_for("family-bike-map") is None
    assert cfg.client_for("nonexistent") is None


def test_project_billable_flag(tmp_path):
    from weekly_review.config import load_projects
    y = tmp_path / "projects.yaml"
    y.write_text(
        "projects:\n"
        "- slug: client-a\n"
        "  client: CA\n"
        "  billable: false\n"
        "  cwd_patterns: ['/x/**']\n"
        "- slug: paid-client-proj\n"
        "  client: PaidClient\n"
        "  cwd_patterns: ['/y/**']\n"
    )
    cfg = load_projects(y)
    volunteer = next(p for p in cfg.projects if p.slug == "client-a")
    paid = next(p for p in cfg.projects if p.slug == "paid-client-proj")
    assert volunteer.billable is False   # volunteer: tracked, never revenue
    assert paid.billable is True    # default


def test_ignore_task_flag(tmp_path):
    from weekly_review.config import load_projects
    y = tmp_path / "projects.yaml"
    y.write_text(
        "projects:\n"
        "- slug: rare-disease-benchmark\n"
        "  ignore_task: true\n"
        "  cwd_patterns: ['/x/**']\n"
        "- slug: health-tool\n"
        "  cwd_patterns: ['/y/**']\n"
    )
    cfg = load_projects(y)
    harness = next(p for p in cfg.projects if p.slug == "rare-disease-benchmark")
    normal = next(p for p in cfg.projects if p.slug == "health-tool")
    assert harness.ignore_task is True
    assert normal.ignore_task is False  # default


def test_diffstat_exclude_field(tmp_path):
    from weekly_review.config import load_projects
    cfg = tmp_path / "projects.yaml"
    cfg.write_text(
        "projects:\n"
        "- slug: client-b\n"
        "  diffstat_exclude:\n"
        "  - data/*\n"
        "  cwd_patterns:\n"
        "  - /Users/*/dev/client-b\n"
        "- slug: other\n"
        "  cwd_patterns:\n"
        "  - /Users/*/dev/other\n"
    )
    by = {p.slug: p for p in load_projects(cfg).projects}
    assert by["client-b"].diffstat_exclude == ["data/*"]
    assert by["other"].diffstat_exclude == []


def test_doc_keywords_field_routes_a_doc_and_is_empty_by_default(tmp_path):
    """Keyword routing words live in projects.yaml, not in source: a project with
    no `doc_keywords` never claims a doc by name, whatever its slug."""
    from weekly_review.config import load_projects
    from weekly_review.extractors.live_feedback import _keyword_routes, _route_doc
    cfg = tmp_path / "projects.yaml"
    cfg.write_text(
        "projects:\n"
        "- slug: maps-app\n"
        "  doc_keywords: [\"Tiles-\", \"bbox\"]\n"
        "  cwd_patterns:\n"
        "  - /Users/*/dev/maps-app\n"
        "- slug: notes\n"
        "  cwd_patterns:\n"
        "  - /Users/*/dev/notes\n"
    )
    projects = load_projects(cfg)
    by = {p.slug: p for p in projects.projects}
    assert by["maps-app"].doc_keywords == ["Tiles-", "bbox"]
    assert by["notes"].doc_keywords == []
    assert _keyword_routes(projects) == [("maps-app", ("tiles-", "bbox"))]
    assert _route_doc(projects, "", "d-1", "Tiles-Preview v2") == "maps-app"
    # The slug alone is not a keyword.
    assert _route_doc(projects, "", "d-2", "notes on the launch") == "unknown"
