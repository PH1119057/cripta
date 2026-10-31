from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import inspect
from pathlib import Path

from operations.dashboard import app as dashboard


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Connection:
    def execute(self, query, params=()):
        del params
        sql = " ".join(str(query).split())
        if "strategy_execution_bindings" in sql:
            return _Result([
                (dashboard.P90_MAIN_ID, "main-account"),
                (dashboard.P90_SUB_ID, "sub-account"),
            ])
        if "p90l90_real_source_epochs" in sql:
            return _Result([
                ("epoch-1", "release-1", datetime(2026, 10, 31, 0, 0, tzinfo=UTC))
            ])
        if "p90l90_real_causal_checkpoints" in sql:
            def checkpoint(symbol, price, geometry):
                return (
                    symbol,
                    datetime(2026, 10, 31, 0, 5, tzinfo=UTC),
                    {
                        "seen": [],
                        "previous_price": price,
                        "geometry": {
                            "effective_at": "2026-10-31T00:05:00+00:00",
                            "working_width_pct": "1.0",
                            "previous_seven_day_median": "1.0",
                            **geometry,
                        },
                    },
                )
            return _Result([
                checkpoint(
                    "LTCUSDT",
                    "100.4",
                    {"l53_low_raw": "100", "long_depth_pct": "1"},
                ),
                checkpoint(
                    "INJUSDT",
                    "100.5",
                    {"l53_low_raw": "100", "long_depth_pct": "1"},
                ),
                checkpoint("DOTUSDT", "5", {}),
                checkpoint(
                    "ARBUSDT",
                    "99.7",
                    {"l53_high_raw": "100", "short_depth_pct": "1"},
                ),
                checkpoint("APTUSDT", "10.2", {}),
            ])
        if "FROM runtime.hot_positions" in sql:
            return _Result([
                (
                    "sub-account",
                    "APTUSDT",
                    0,
                    "Buy",
                    "1",
                    "10",
                    "1",
                    1234567890,
                    {"markPrice": "10.2"},
                )
            ])
        if "FROM runtime.position_ownership" in sql:
            return _Result([
                ("sub-account", "APTUSDT", 0, "Buy", Decimal("0.01"))
            ])
        raise AssertionError(sql)


def test_p90_rail_has_five_symbols_and_requested_priority():
    state = dashboard.p90_live_rail_state(
        _Connection(),
        tickers={
            "APTUSDT": {
                "bid_price": "10.2",
                "ask_price": "10.21",
                "last_price": "10.2",
                "mark_price": "10.2",
            }
        },
    )
    rows = state["items"]
    assert [row["symbol"] for row in rows] == [
        "APTUSDT",
        "ARBUSDT",
        "LTCUSDT",
        "INJUSDT",
        "DOTUSDT",
    ]
    assert [row["state"] for row in rows] == [
        "OPEN",
        "NEAR",
        "NEAR",
        "FAR",
        "FAR",
    ]
    assert rows[1]["nearest_entry"]["side"] == "SHORT"
    assert Decimal(rows[1]["nearest_entry"]["distance_pct"]) == Decimal("0.3")
    assert Decimal(rows[2]["nearest_entry"]["distance_pct"]) == Decimal("0.4")
    assert Decimal(rows[3]["nearest_entry"]["distance_pct"]) == Decimal("0.5")
    assert rows[4]["nearest_entry"] is None


def test_open_position_uses_bound_sub_account_and_after_fee_pnl():
    state = dashboard.p90_live_rail_state(
        _Connection(),
        tickers={"APTUSDT": {"bid_price": "10.2", "mark_price": "10.2"}},
    )
    row = state["items"][0]
    assert row["symbol"] == "APTUSDT"
    assert row["trading_account_id"] == "sub-account"
    assert row["state"] == "OPEN"
    position = row["position"]
    assert position["pnl_basis"] == "AFTER_COMMISSIONS_ACTUAL_ENTRY_FEE"
    assert abs(position["net_pnl_to_close"] - 0.18439) < 1e-9


def test_p90_rail_projection_is_read_only():
    source = inspect.getsource(dashboard.p90_live_rail_state)
    upper = source.upper()
    assert "INSERT " not in upper
    assert "UPDATE " not in upper
    assert "DELETE " not in upper
    assert "P90L90_REAL_CAUSAL_CHECKPOINTS" in upper
    assert "RUNTIME.HOT_POSITIONS" in upper


def test_left_rail_frontend_uses_p90_projection_and_requested_colors():
    index = (
        Path(__file__).resolve().parents[1] / "operations/dashboard/index.html"
    ).read_text(encoding="utf-8")
    assert "Открытые сделки / P90" in index
    assert "жёлтый — до Entry &lt; 0,5%" in index
    assert "p90-open" in index
    assert "p90-near" in index
    assert "p90-far" in index
    assert "renderOpenRailPositions(lt.p90_rail?.items||[],positions)" in index
