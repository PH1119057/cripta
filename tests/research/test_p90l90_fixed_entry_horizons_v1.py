"""Tests for frozen Entry time-cap classification without replacement trades."""
from scripts.research.p90l90_fixed_entry_horizons_v1 import (
    END_TS,
    HOURS,
    make_tasks,
)


def _trade(start: float, closed: float):
    return {
        "entry_ts": str(start),
        "exit_ts": str(closed),
        "net_usdt": "0.1",
    }


def test_early_exit_kept_at_every_horizon():
    start = END_TS - 7 * 86400
    entries, existing, tasks = make_tasks([_trade(start, start + 60)], None)
    assert len(entries) == 1
    assert not tasks
    assert all(existing[h][0][0] == "ORIGINAL" for h in HOURS)


def test_trade_after_three_before_six():
    start = END_TS - 7 * 86400
    entries, existing, tasks = make_tasks([_trade(start, start + 5 * 3600)], None)
    assert len(entries) == 1
    assert len(tasks) == 1
    assert tasks[0] == (start + 3 * 3600, 0, 3)
    assert existing[6][0][0] == "ORIGINAL"


def test_censored_trade_without_full_window():
    start = END_TS - 2 * 3600
    entries, existing, tasks = make_tasks([], {"entry_ts": str(start)})
    assert len(entries) == 1
    assert not tasks
    assert all(existing[h][0][0] == "RIGHT_CENSORED" for h in HOURS)


def test_censored_trade_with_some_observable_timeouts():
    start = END_TS - 7 * 3600
    entries, existing, tasks = make_tasks([], {"entry_ts": str(start)})
    assert len(entries) == 1
    assert len(tasks) == 2
    assert [q[2] for q in tasks] == [3, 6]
    assert existing[12][0][0] == "RIGHT_CENSORED"
