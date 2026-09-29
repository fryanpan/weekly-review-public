"""The convention that keeps board-shaped ids out of tracked test files."""
import re
from pathlib import Path
from tests.fakeids import did, tid, wid


TESTS = Path(__file__).parent
# the same three shapes the pre-push scrub gate refuses
BOARD_ID = re.compile(r"\b[tdw]-[A-Za-z0-9_-]{10,}\b")


def test_helpers_join_the_prefix_at_run_time():
    assert tid("parserxxxxxxx1") == "t-" + "parserxxxxxxx1"
    assert did("Qr4StUvWxYz5") == "d-" + "Qr4StUvWxYz5"
    assert wid("not-a-card") == "w-" + "not-a-card"
    assert tid("-vwXyz01AbCd").startswith("t--")       # a suffix may start with a hyphen


def test_no_literal_board_ids_in_tests():
    """Every id a test needs is built by tests/fakeids.py, never spelled out.

    The scrub gate scans whole files on push, so one literal blocks every
    later push touching that file; this catches it at test time instead."""
    hits = []
    for path in sorted(TESTS.rglob("*.py")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if BOARD_ID.search(line):
                hits.append(f"{path.relative_to(TESTS.parent)}:{n}: {line.strip()}")
    assert not hits, "literal board-shaped ids in tests (use tests/fakeids.py):\n" + "\n".join(hits)
