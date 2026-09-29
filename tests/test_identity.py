"""The identity rule: who counts as the data subject, and where that is decided.

The defect this replaces was two filters that drifted apart — one allowlist
that never matched the subject's own main handle, and one path with no
allowlist at all. So the tests that matter are the ones that would fail if the
rule were re-split, or if an era quietly widened to recover hours.
"""
import os
from datetime import date, datetime, timezone

import pytest

from weekly_review.identity import (
    AnonEra,
    SubjectIdentity,
    load_subject_identity,
)

SUBJECT = SubjectIdentity(
    name_fragments=("alex", "arivera"),
    anon_eras=(AnonEra(date(2026, 6, 29), date(2026, 8, 10), "handles unnamed"),),
)


# --- the rule ---------------------------------------------------------------

def test_the_era_has_two_hard_edges():
    """A day either side is the whole point: the edge is a fact about the store.

    An era that can be nudged outward to recover hours is not evidence, it is a
    dial. Both boundaries are inclusive and nothing beyond them counts.
    """
    anon = dict(name="Anon-tnd4hs", kind="anon")
    assert SUBJECT.is_subject(**anon, ts="2026-06-28T23:59:59Z") is False
    assert SUBJECT.is_subject(**anon, ts="2026-06-29T00:00:00Z") is True
    assert SUBJECT.is_subject(**anon, ts="2026-08-10T23:59:59Z") is True
    assert SUBJECT.is_subject(**anon, ts="2026-08-11T00:00:00Z") is False


def test_a_real_name_is_never_covered_by_an_era():
    """The era covers handles the store CANNOT resolve, not people it can.

    This is what excludes an identified collaborator without the repo holding
    their name — the alternative, a denylist of real people, is both somebody
    else's data and a list that goes stale.
    """
    assert SUBJECT.is_subject(name="Some Person", kind="user",
                              ts="2026-07-15T10:00:00Z") is False


def test_a_machine_is_never_the_subject_whatever_it_calls_itself():
    """`actor == "person"` does not mean a person.

    The activity store files "Claude", "Live Feedback" and "Chat Audit" under
    it, distinguished only by their actorId prefix.
    """
    for kind in ("agent", "live", "chat", "test", "bot"):
        assert SUBJECT.is_subject(name="Alex", kind=kind,
                                  ts="2026-07-15T10:00:00Z") is False, kind


def test_the_owner_flag_answers_without_a_name_list():
    assert SUBJECT.is_subject(name="Anon-zzz", kind="anon", is_owner=True,
                              ts="2026-09-01T10:00:00Z") is True


def test_a_blank_name_is_malformed_input_not_anonymity():
    """Every person record in the store carries a name.

    Crediting a blank one makes a parse failure read as the subject's time,
    which is the quietest possible way to inflate an hours figure.
    """
    assert SUBJECT.is_subject(name="", kind="user", ts="2026-07-15T10:00:00Z") is False
    assert SUBJECT.is_subject(name="   ", kind="", ts="2026-07-15T10:00:00Z") is False


def test_an_unconfigured_identity_counts_everyone_rather_than_no_one():
    """A fresh install must not report a working week as zero hours.

    Zero is indistinguishable from a quiet week, so the failure mode has to be
    visibly wrong instead of invisibly wrong. The run warns; see cli.
    """
    blank = SubjectIdentity()
    assert blank.configured is False
    assert blank.is_subject(name="Anyone At All", kind="user") is True
    assert blank.is_subject(name="Live Feedback", kind="agent") is False


def test_every_handle_family_matches_one_fragment_list():
    for name in ("Alex", "Alex Rivera", "Arivera", "ALEX RIVERA"):
        assert SUBJECT.is_subject(name=name, kind="known") is True, name


def test_a_near_miss_handle_does_not_match():
    """Substring matching is generous, and that is a risk worth testing.

    The real store holds a handle one letter off the subject's own (surfaced to
    him as an open question rather than decided here). It is not on the
    fragment list, so it does not count — and if it turns out to be his, that
    is a ruling he makes, not a regex accident.
    """
    assert SUBJECT.is_subject(name="Fryanpen", kind="user",
                              ts="2026-09-01T10:00:00Z") is False


def test_timestamps_arrive_in_three_shapes_and_all_three_work():
    """Comments carry epoch ms, the activity stream carries ISO, tests carry
    datetimes. An era that silently fails to parse one of them would drop a
    whole source without erroring."""
    anon = dict(name="Anon-a", kind="anon")
    assert SUBJECT.is_subject(**anon, ts="1784500000000") is True  # 2026-07-19
    assert SUBJECT.is_subject(**anon, ts="2026-07-19T10:00:00.000Z") is True
    assert SUBJECT.is_subject(
        **anon, ts=datetime(2026, 7, 19, 10, tzinfo=timezone.utc)) is True
    assert SUBJECT.is_subject(**anon, ts="not-a-date") is False
    assert SUBJECT.is_subject(**anon, ts=None) is False


# --- the config -------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("WEEKLY_REVIEW_CONFIG", str(tmp_path))
    monkeypatch.delenv("WEEKLY_REVIEW_WORKSPACE_NAMES", raising=False)
    import importlib

    from weekly_review import config as config_mod
    importlib.reload(config_mod)
    import weekly_review.identity as identity_mod
    importlib.reload(identity_mod)
    yield tmp_path, identity_mod
    monkeypatch.delenv("WEEKLY_REVIEW_CONFIG", raising=False)
    importlib.reload(config_mod)
    importlib.reload(identity_mod)


def test_the_rule_comes_from_config_not_source(cfg):
    """A public repo may not hardcode one user's handles.

    Another user configures their own identity without editing Python — that is
    the whole of the review doc (2026-09-12).
    """
    tmp, mod = cfg
    (tmp / "identity.yaml").write_text(
        "identity:\n"
        "  workspace_names: [Alex, arivera]\n"
        "  anon_eras:\n"
        "    - start: 2026-01-01\n"
        "      end: 2026-02-01\n"
        "      reason: the store did not resolve handles yet\n"
    )
    ident = mod.load_subject_identity()
    assert ident.name_fragments == ("alex", "arivera")
    assert ident.anon_eras[0].start == date(2026, 1, 1)
    assert ident.anon_eras[0].reason.startswith("the store")
    assert ident.is_subject(name="Alex Rivera", kind="user") is True
    assert ident.is_subject(name="Some Person", kind="user",
                            ts="2026-01-15T10:00:00Z") is False


def test_a_missing_config_is_unconfigured_not_an_error(cfg):
    _, mod = cfg
    assert mod.load_subject_identity().configured is False


def test_a_half_written_era_is_dropped_rather_than_guessed(cfg):
    """An era missing an edge would widen to everything or narrow to nothing.

    Neither is what the person editing the file asked for, so it is refused and
    the remaining well-formed eras still load.
    """
    tmp, mod = cfg
    (tmp / "identity.yaml").write_text(
        "identity:\n"
        "  workspace_names: [alex]\n"
        "  anon_eras:\n"
        "    - start: 2026-01-01\n"          # no end
        "    - end: 2026-03-01\n"            # no start
        "    - start: 2026-05-01\n"
        "      end: 2026-04-01\n"            # backwards
        "    - start: 2026-06-01\n"
        "      end: 2026-06-30\n"            # the only good one
    )
    eras = mod.load_subject_identity().anon_eras
    assert len(eras) == 1
    assert (eras[0].start, eras[0].end) == (date(2026, 6, 1), date(2026, 6, 30))


def test_the_env_var_overrides_the_file(cfg, monkeypatch):
    tmp, mod = cfg
    (tmp / "identity.yaml").write_text("identity:\n  workspace_names: [alex]\n")
    monkeypatch.setenv("WEEKLY_REVIEW_WORKSPACE_NAMES", "Alex, Arivera")
    ident = mod.load_subject_identity()
    assert ident.name_fragments == ("alex", "arivera")


def test_a_broken_anon_pattern_falls_back_rather_than_matching_nothing(cfg):
    """A regex typo must not silently reclassify every generated handle as a
    real name, which would drop an entire era without a word of warning."""
    tmp, mod = cfg
    (tmp / "identity.yaml").write_text(
        "identity:\n"
        "  workspace_names: [alex]\n"
        "  anon_name_pattern: '[unclosed'\n"
        "  anon_eras:\n"
        "    - start: 2026-06-01\n"
        "      end: 2026-06-30\n"
    )
    ident = mod.load_subject_identity()
    assert ident.is_subject(name="Anon-xy", kind="anon",
                            ts="2026-06-15T10:00:00Z") is True


# --- the call sites ---------------------------------------------------------

def test_one_rule_decides_all_three_workspace_sources():
    """Review doc, 2026-09-12. Two filters that can drift apart WILL drift apart — that
    is exactly how one path lost 29% of his comments while another counted
    other people's reading. Assert the call sites, because the defect is never
    in the function."""
    import inspect

    from weekly_review.extractors import live_feedback as lf

    for fn in (lf._iter_review_sessions, lf._iter_activity_touches):
        src = inspect.getsource(fn)
        assert "identity.is_subject(" in src, fn.__name__
    assert "identity.is_subject(" in inspect.getsource(lf._is_subject)
    # And the comment path goes through the same helper.
    assert "_is_subject(" in inspect.getsource(lf._comment_event)
    # One load per run, handed down — not three modules each reading config.
    entry = inspect.getsource(lf.extract_live_feedback)
    assert entry.count("load_subject_identity()") == 1


def test_the_run_states_which_identity_it_used():
    """An hours figure is 'time THIS person spent'. A reader who cannot see
    whose time it was cannot check it — and the unconfigured case has to be
    loud, because it silently counts everyone."""
    import inspect

    from weekly_review import cli

    src = inspect.getsource(cli)
    assert "workspace identity:" in src
    assert "no workspace identity configured" in src


def test_the_old_single_fragment_constant_is_gone():
    """`HUMAN_NAME_FRAGMENT = "alex"` did not match "Arivera". Leaving it in
    place for compatibility would leave a second place to decide identity,
    which is the thing being fixed."""
    from weekly_review.extractors import live_feedback as lf

    assert not hasattr(lf, "HUMAN_NAME_FRAGMENT")
