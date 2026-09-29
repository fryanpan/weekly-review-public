"""Concurrent enrichment must not change what a run records, only how long it takes."""
import threading
import time

from weekly_review.cli import map_in_order


def _slow_inverse(x):
    # Later items finish FIRST, so completion order is the reverse of input order.
    time.sleep((5 - x) * 0.01)
    if x == 2:
        raise ValueError("bad task")
    return x * 10


def test_results_come_back_in_input_order_even_when_completion_is_reversed():
    items = [0, 1, 2, 3, 4]
    serial = list(map_in_order(items, _slow_inverse, workers=1))
    parallel = list(map_in_order(items, _slow_inverse, workers=5))
    assert [i for i, _ in parallel] == items
    assert [(i, r if not isinstance(r, Exception) else str(r)) for i, r in parallel] == \
           [(i, r if not isinstance(r, Exception) else str(r)) for i, r in serial]


def test_one_failure_is_returned_not_raised_and_the_rest_still_run():
    out = dict(map_in_order([1, 2, 3], _slow_inverse, workers=3))
    assert out[1] == 10 and out[3] == 30
    assert isinstance(out[2], ValueError)


def test_workers_actually_run_concurrently():
    active, peak, lock = [0], [0], threading.Lock()

    def track(_):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1

    list(map_in_order(range(8), track, workers=4))
    assert peak[0] > 1
