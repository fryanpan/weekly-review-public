"""Tests for the ~/.config/weekly-review/env auto-loader."""
from __future__ import annotations

import os

import pytest

from weekly_review import config as cfg_mod


@pytest.fixture(autouse=True)
def _clean_env():
    for k in ("WR_TEST_VAR", "WR_TEST_VAR2", "WR_TEST_QUOTED"):
        os.environ.pop(k, None)
    yield
    for k in ("WR_TEST_VAR", "WR_TEST_VAR2", "WR_TEST_QUOTED"):
        os.environ.pop(k, None)


def test_load_env_file_sets_missing_vars(tmp_path):
    env = tmp_path / "env"
    env.write_text("WR_TEST_VAR=hello\n# a comment\nWR_TEST_VAR2=world\n")
    n = cfg_mod.load_env_file(env)
    assert n == 2
    assert os.environ["WR_TEST_VAR"] == "hello"
    assert os.environ["WR_TEST_VAR2"] == "world"


def test_load_env_file_shell_exports_win(tmp_path):
    env = tmp_path / "env"
    env.write_text("WR_TEST_VAR=from_file\n")
    os.environ["WR_TEST_VAR"] = "from_shell"
    cfg_mod.load_env_file(env)
    assert os.environ["WR_TEST_VAR"] == "from_shell"


def test_load_env_file_strips_quotes_and_export(tmp_path):
    env = tmp_path / "env"
    env.write_text('export WR_TEST_QUOTED="quoted value"\n')
    cfg_mod.load_env_file(env)
    assert os.environ["WR_TEST_QUOTED"] == "quoted value"


def test_load_env_file_missing_path_is_noop(tmp_path):
    assert cfg_mod.load_env_file(tmp_path / "does-not-exist") == 0
