"""P90L90 paper native trade routing: exact binding, RAW closed TP, no orders."""
from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MethodType

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.paper_runtime import PaperTradeRuntime

D = Decimal
T = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _AccountReadOnly:
    def __init__(self, rows=()):
        self.rows = rows

    def execute(self, statement, params=()):
        if "FROM strategy_entry.paper_positions p" in statement:
            return _Result(self.rows)
        if "SELECT direction FROM strategy_entry.paper_positions" in statement:
            return _Result([("LONG",)] if params[0] == "paper-main" else [("SHORT",)])
        raise AssertionError("routing attempted unapproved SQL")


def candle(index):
    opened = T - timedelta(minutes=5 * (36-index))
    return Candle(
        symbol="LTCUSDT", timeframe="5", opened_at=opened,
        closed_at=opened+timedelta(minutes=5),
        open=D("100"), high=D("105"), low=D("95"), close=D("100"),
        volume=D("1"), is_closed=True,
    )


def test_p90l90_route_uses_native_cursor_account_mapping_and_raw_opposite():
    data = _AccountReadOnly([
        ("paper-main", "p90l90_legacy_main_ltc_inj", "ta-main"),
        ("paper-sub", "p90l90_legacy_sub_dot_arb_apt", "ta-sub"),
    ])
    runtime = PaperTradeRuntime(data)
    runtime._l53_candles["LTCUSDT"] = deque(candle(i) for i in range(36))
    calls = []

    def capture(_self, **kwargs):
        calls.append(kwargs)
        return None

    runtime.on_p90l90_native_trade = MethodType(capture, runtime)
    routed = runtime.route_p90l90_native_trade(
        symbol="LTCUSDT", exec_id="bybit-id", exchange_sequence=1234,
        event_at=T+timedelta(seconds=1), price=D("100"),
    )
    assert routed == 2
    assert {c["trading_account_id"] for c in calls} == {"ta-main", "ta-sub"}
    assert {c["strategy_id"] for c in calls} == {
        "p90l90_legacy_main_ltc_inj", "p90l90_legacy_sub_dot_arb_apt"
    }
    assert all(c["exec_id"] == "bybit-id" and
               c["exchange_sequence"] == 1234 for c in calls)
    assert calls[0]["geometry_effective_at"] == T
    assert calls[0]["candidate_opposite_raw_tp"] == D("105")
    assert calls[1]["candidate_opposite_raw_tp"] == D("95")


def test_p90l90_route_refuses_ambiguous_account_before_any_paper_write():
    db = _AccountReadOnly([
        ("paper-main", "p90l90_legacy_main_ltc_inj", "ta-main"),
        ("paper-main", "p90l90_legacy_main_ltc_inj", "ta-sub"),
    ])
    runtime = PaperTradeRuntime(db)
    writes = []
    runtime.on_p90l90_native_trade = MethodType(
        lambda _self, **kwargs: writes.append(kwargs), runtime
    )
    with pytest.raises(RuntimeError, match="AMBIGUOUS_ACCOUNT_BINDING"):
        runtime.route_p90l90_native_trade(
            symbol="LTCUSDT", exec_id="id", exchange_sequence=1,
            event_at=T+timedelta(seconds=1), price=D("100"),
        )
    assert writes == []


def test_p90l90_route_missing_closed_geometry_passes_no_lookahead():
    db = _AccountReadOnly([
        ("paper-main", "p90l90_legacy_main_ltc_inj", "ta-main"),
    ])
    runtime = PaperTradeRuntime(db)
    seen = []

    def capture(_self, **kwargs):
        seen.append(kwargs)

    runtime.on_p90l90_native_trade = MethodType(capture, runtime)
    runtime.route_p90l90_native_trade(
        symbol="LTCUSDT", exec_id="id", exchange_sequence=1,
        event_at=T+timedelta(seconds=1), price=D("100"),
    )
    assert len(seen) == 1
    assert seen[0]["geometry_effective_at"] is None
    assert seen[0]["candidate_opposite_raw_tp"] is None
