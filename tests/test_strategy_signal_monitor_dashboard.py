from __future__ import annotations

from pathlib import Path

from operations.dashboard.app import _legacy_signal_outcome, _signal_monitor_summary

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "operations/dashboard/app.py"
HTML = ROOT / "operations/dashboard/index.html"


def test_signal_summary_counts_lifecycle_and_separates_open_closed_pnl() -> None:
    items = [
        {"result_class": "positive", "pnl_kind": "closed", "pnl_usdt": 1.25},
        {"result_class": "negative", "pnl_kind": "closed", "pnl_usdt": -0.40},
        {"result_class": "open", "pnl_kind": "open", "pnl_usdt": 0.15},
        {"result_class": "open", "pnl_kind": None, "pnl_usdt": None},
        {"result_class": "no_entry", "pnl_kind": None, "pnl_usdt": None},
        {"result_class": "neutral", "pnl_kind": None, "pnl_usdt": None},
    ]
    summary = _signal_monitor_summary(items)
    assert summary["total"] == 6
    assert summary["positive"] == 1
    assert summary["negative"] == 1
    assert summary["open"] == 2
    assert summary["no_entry"] == 1
    assert summary["neutral"] == 1
    assert summary["closed_pnl_usdt"] == 0.85
    assert summary["open_pnl_usdt"] == 0.15
    assert summary["total_pnl_usdt"] == 1.0
    assert summary["closed_pnl_rows"] == 2
    assert summary["open_pnl_rows"] == 1


def test_legacy_signal_outcome_remains_read_only_compatibility() -> None:
    assert _legacy_signal_outcome({"+1.1": 10, "-1.0": 20}, "completed") == (
        "positive",
        "цель +1,10% раньше стопа",
    )
    assert _legacy_signal_outcome({"+1.1": 20, "-1.0": 10}, "completed") == (
        "negative",
        "стоп −1,00% раньше цели",
    )
    assert _legacy_signal_outcome({}, "tracking")[0] == "open"
    assert _legacy_signal_outcome({}, "completed")[0] == "neutral"


def test_signal_monitor_backend_is_rolling_24h_and_strategy_exact() -> None:
    app = APP.read_text(encoding="utf-8")
    assert "def strategy_signal_monitor_state(*, window_hours: int = 24)" in app
    assert "window_start = now - timedelta(hours=window_hours)" in app
    assert "WHERE o.requested_at >= %s" in app
    assert "WHERE signal_at_epoch_ms >= %s" in app
    assert "p.leg_type='PRIMARY'" in app
    assert "a.enabled=true" in app
    assert 'strategy_key = f"{row[2]}::{row[3]}"' in app
    assert '"signal_monitor": strategy_signal_monitor_state()' in app


def test_signal_monitor_ui_has_strategy_filter_stats_colors_and_exact_rows() -> None:
    html = HTML.read_text(encoding="utf-8")
    for token in (
        'id="signalStrategyFilter"',
        'id="signalStats"',
        'id="signalStrategyBreakdown"',
        "Все стратегии",
        "Всего сигналов / сделок",
        "Закрыто в плюс",
        "Закрыто в минус",
        "В работе / ждут входа",
        "PnL закрытых",
        "PnL открытых сейчас",
        "Итого сейчас",
        "signal-row-positive",
        "signal-row-negative",
        "signal-row-open",
        "data-signal-strategy-col",
        "function renderSignalObservation",
        "onSignalStrategyFilterChange",
    ):
        assert token in html
    renderer = html.index("function renderSignalObservation")
    live_renderer = html.index("function renderLiveState(d){")
    assert renderer < live_renderer
    assert "signalGroups=new Map()" not in html
