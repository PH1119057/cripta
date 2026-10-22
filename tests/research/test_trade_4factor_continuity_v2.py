"""Tests of exact event-time minute physical factors and midnight continuity."""
import datetime as dt
import importlib.util
import statistics
from collections import deque
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "research/microstructure/experiments"


def module(name):
    path = RESEARCH / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


primary = module("trade_4factor_continuous_30day_v2")
independent = module("trade_4factor_continuous_independent_qc_v2")


def test_prior60_carries_across_midnight():
    history = deque(maxlen=60)
    for minute in range(1439):
        history.append([10.0 + minute % 5] * 5)
    assert len(history) == 60
    first = primary.minute_state()
    first["fill_count"] = 3
    first["amounts"] = [20.0, 50.0, 5.0]
    first["buy"] = 70.0
    first["sell"] = 5.0
    first["max_run"] = 2
    first["last_price"] = 100.0
    first["gaps"] = [100.0, 300.0]
    row = primary.factors_for_minute(first, history)
    assert row["prior60_n"] == 300
    assert row["factor_4_large_trade_share"] is not None
    assert row["factor_8_max_timestamp_side_group_streak"] == 2
    assert row["factor_27_median_positive_gap_ms"] == 200.0
    assert row["factor_28_minute_fill_notional_p90_usdt"] == 20.0
    assert row["factor_5_delta_usdt"] == 65.0

    prior = [
        [Decimal(str(v)) for v in minute]
        for minute in history
    ]
    start = Decimal(int(dt.datetime(2026, 5, 19, tzinfo=dt.UTC).timestamp()))
    prints = [
        (start + Decimal("1"), "Buy", Decimal("20"), Decimal("100")),
        (start + Decimal("1.1"), "Buy", Decimal("50"), Decimal("100")),
        (start + Decimal("1.4"), "Sell", Decimal("5"), Decimal("100")),
    ]
    rec = independent.recompute(prints, deque(prior, maxlen=60))
    assert float(rec["share"]) == row["factor_4_large_trade_share"]
    assert rec["streak"] == 2
    assert rec["duration"] == Decimal("200")
    assert rec["p90"] == Decimal("20")
    assert rec["delta"] == Decimal("65")


def test_missing_minute_and_warmup_semantics():
    empty = primary.minute_state()
    r = primary.factors_for_minute(empty, deque(maxlen=60))
    for key in primary.KEYS:
        assert r[key] is None
    assert r["quality"] == "NO_DATA"
    assert r["input_fill_count"] == 0
    assert independent.recompute([], deque(maxlen=60))["share"] is None
    initial = primary.minute_state()
    initial["amounts"] = [10]
    initial["fill_count"] = 1
    initial["max_run"] = 1
    assert primary.factors_for_minute(initial, deque(maxlen=60))[
        "factor_4_large_trade_share"
    ] is None


def test_p90_order_statistic_and_empty_gap():
    state = primary.minute_state()
    state["fill_count"] = 4
    state["amounts"] = [10.0, 40.0, 15.0, 5.0]
    state["max_run"] = 3
    row = primary.factors_for_minute(state, deque([[9.0] * 10] * 60, maxlen=60))
    assert row["factor_28_minute_fill_notional_p90_usdt"] == 15.0
    assert row["factor_27_median_positive_gap_ms"] is None
    assert row["factor_4_large_trade_share"] == 1.0
    assert statistics.median([100, 300]) == 200
