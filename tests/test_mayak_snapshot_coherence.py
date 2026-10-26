"""Regression: one MAYAK snapshot must not read two different trade revisions."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bybit_workbench.mayak.compact_analytics import compact_market_minute_rows
from bybit_workbench.mayak.compact_reader import compact_panel_projection
from bybit_workbench.mayak.core.live import LiveMayakEngine, TradeWindow


def test_frozen_window_is_not_modified_by_later_socket_trades() -> None:
    window = TradeWindow()
    now = 1800000000.0
    window.add(now - 120, 100.0, 100.0)
    window.add(now - 45, 200.0, 110.0)
    view = window.frozen_view()
    assert view is not window
    old_return = view.metrics(now, 300)["return_pct"]
    window.add(now - 5, 300.0, 190.0)
    assert view.metrics(now, 300)["return_pct"] == old_return
    assert window.metrics(now, 300)["return_pct"] != old_return


def test_one_snapshot_has_matching_price_flow_and_compact_return_after_socket_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = LiveMayakEngine(("BTCUSDT",))
    now = datetime.now(UTC)
    second = now.timestamp()
    engine.on_trade("spot", "BTCUSDT", second - 120, "Buy", 100.0, 100.0)
    engine.on_trade("linear", "BTCUSDT", second - 120, "Buy", 100.0, 100.0)
    engine.on_trade("linear", "BTCUSDT", second - 45, "Buy", 110.0, 100.0)

    active_linear = engine.trades[("linear", "BTCUSDT")]
    original = TradeWindow.frozen_view
    changed = False

    def freeze_and_simulate_socket_arrival(self: TradeWindow) -> TradeWindow:
        nonlocal changed
        frozen = original(self)
        if self is active_linear and not changed:
            changed = True
            engine.on_trade("linear", "BTCUSDT", second - 5, "Buy", 190.0, 100.0)
        return frozen

    monkeypatch.setattr(TradeWindow, "frozen_view", freeze_and_simulate_socket_arrival)
    snapshot = engine.snapshot(now)
    assert changed is True
    payload = snapshot["coin_market_contexts"]["BTCUSDT"]["payload"]
    price_returns = payload["price"]["returns"]
    derivatives_flow = payload["money"]["derivatives"]
    for horizon in ("1m", "5m", "15m", "30m", "60m"):
        assert price_returns[horizon] == derivatives_flow[horizon]["return_pct"]
    assert price_returns["5m"] == pytest.approx(10.0)
    assert active_linear.metrics(second, 300)["return_pct"] != price_returns["5m"]

    rows = compact_market_minute_rows(1, snapshot)
    projected = compact_panel_projection(rows)["BTCUSDT"]
    for horizon in ("1m", "5m", "15m", "30m", "60m"):
        assert projected["price"]["returns"][horizon] == price_returns[horizon]

    # A subsequent snapshot may use newer trades, but the first one is immutable.
    later = engine.snapshot(now)
    assert later["coin_market_contexts"]["BTCUSDT"]["payload"]["price"]["returns"]["5m"] != (
        price_returns["5m"]
    )
