from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.market_watch import (
    compute_r1_l53_stability_diagnostic,
)


ROOT = Path(__file__).resolve().parents[1]


def _candles(count: int = 205) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[Candle] = []
    for index in range(count):
        opened = start + timedelta(minutes=5 * index)
        high = Decimal("110") if index % 10 == 0 else Decimal("101")
        low = Decimal("90") if index % 10 == 5 else Decimal("99")
        rows.append(
            Candle(
                symbol="APTUSDT",
                timeframe="5",
                opened_at=opened,
                closed_at=opened + timedelta(minutes=5),
                open=Decimal("100"),
                high=high,
                low=low,
                close=Decimal("100"),
                volume=Decimal("1"),
            )
        )
    return rows


def test_r1_stability_diagnostic_reaches_six_of_six() -> None:
    diagnostic = compute_r1_l53_stability_diagnostic(_candles())
    assert diagnostic.history_ready is True
    assert diagnostic.available_states == 6
    assert diagnostic.strict_stable_states == 6
    assert diagnostic.low_stable_states == 6
    assert diagnostic.high_stable_states == 6
    assert diagnostic.width_ready is True
    assert diagnostic.reset_reason is None


def test_r1_stability_diagnostic_explains_boundary_reset() -> None:
    rows = _candles()
    last = rows[-1]
    rows[-1] = Candle(
        symbol=last.symbol,
        timeframe=last.timeframe,
        opened_at=last.opened_at,
        closed_at=last.closed_at,
        open=last.open,
        high=Decimal("111"),
        low=last.low,
        close=last.close,
        volume=last.volume,
    )
    diagnostic = compute_r1_l53_stability_diagnostic(rows)
    assert diagnostic.strict_stable_states == 1
    assert diagnostic.low_stable_states == 6
    assert diagnostic.high_stable_states == 1
    assert diagnostic.reset_reason == "HIGH_CHANGED"


def test_strategy_monitor_renders_r1_stability_progress() -> None:
    source = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "STAY ${strict}/${need}" in source
    assert "LOW ${low}/${need}" in source
    assert "HIGH ${high}/${need}" in source
    assert "сброс: изменилась верхняя граница" in source
    assert "width ${width} / ≥${min}" in source


def test_observer_exposes_r1_stability_telemetry() -> None:
    source = (
        ROOT / "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    for field in (
        '"required_states"',
        '"strict_stable_states"',
        '"low_stable_states"',
        '"high_stable_states"',
        '"working_width_pct"',
        '"reset_reason"',
    ):
        assert field in source
