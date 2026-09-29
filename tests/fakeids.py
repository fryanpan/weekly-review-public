"""Board-shaped ids for tests, built at run time so no literal id sits in a tracked file.

The pre-push scrub gate (scripts/scrub-check.py) refuses any string shaped like a
board task, doc or workspace id: ``t-``, ``d-`` or ``w-`` followed by ten or more
id characters. Real ids are random, so a literal that matches the shape cannot be
told from a leaked one. The convention that lets tests pass the gate is that they
never write such a literal; they call ``tid``, ``did`` or ``wid`` with the suffix
only, and the prefix is joined here. ``test_no_literal_board_ids_in_tests`` in
``test_fakeids.py`` enforces it independently of the gate.

A suffix is made up by the test and may be any shape the code under test needs
(exactly twelve characters for the id parser, a hyphen inside, upper case). Real
ids are twelve random base-62 characters, so a readable suffix such as
``parserxxxxxxx1`` is not one of them.
"""


def _join(prefix: str, suffix: str) -> str:
    assert suffix, "a fake id needs a suffix"
    return prefix + "-" + suffix


def tid(suffix: str) -> str:
    """A task id: ``t-`` plus the suffix."""
    return _join("t", suffix)


def did(suffix: str) -> str:
    """A doc id: ``d-`` plus the suffix."""
    return _join("d", suffix)


def wid(suffix: str) -> str:
    """A workspace id: ``w-`` plus the suffix."""
    return _join("w", suffix)
