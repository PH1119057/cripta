"""Bounded public REST pagination for exact P90L90 seven-day seed."""
from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.domain.models import Candle
from operations.monitoring import universal_entry_shadow as observer


def _mock_pages(monkeypatch, *, count: int):
    end = datetime(2026, 10, 28, tzinfo=UTC)
    bars = {}
    for n in range(count):
        opened = end - timedelta(minutes=5 * (count - n))
        bar = Candle(
            symbol="LTCUSDT", timeframe="5",
            opened_at=opened, closed_at=opened + timedelta(minutes=5),
            open=observer.Decimal("100"), high=observer.Decimal("110"),
            low=observer.Decimal("90"), close=observer.Decimal("100"),
            volume=observer.Decimal("1"),
        )
        bars[int(opened.timestamp() * 1000)] = bar
    calls = []

    def fake_get_json(_path, params):
        calls.append(dict(params))
        cutoff = int(params.get("end", int(end.timestamp() * 1000)))
        selected = sorted((ts for ts in bars if ts <= cutoff), reverse=True)[:1000]
        return {"result": {"list": [[str(ts)] for ts in selected]}}

    def fake_mapper(rows, *, symbol, interval, observed_at):
        assert symbol == "LTCUSDT" and interval == "5"
        return tuple(bars[int(row[0])] for row in rows)

    monkeypatch.setattr(observer, "_get_json", fake_get_json)
    monkeypatch.setattr(observer, "map_rest_klines", fake_mapper)
    return end, calls


def test_p90l90_prev7d_seed_paginates_only_when_requested(monkeypatch):
    observed_at, calls = _mock_pages(monkeypatch, count=2700)
    history = observer._fetch_history(
        "LTCUSDT", observed_at, ("5",), p90l90_full_prev7d=True
    )
    five = history["5"]
    assert len(five) == 2600
    assert len(calls) == 3
    assert "end" not in calls[0]
    assert int(calls[1]["end"]) < int(calls[0].get("end", observed_at.timestamp() * 1000))
    assert len({x.opened_at for x in five}) == 2600
    assert all(
        b.closed_at - a.closed_at == timedelta(minutes=5)
        for a, b in zip(five, five[1:], strict=True)
    )

    calls.clear()
    normal = observer._fetch_history("LTCUSDT", observed_at, ("5",))
    assert len(normal["5"]) == 1000
    assert len(calls) == 1


def test_p90l90_no_short_history_as_false_readiness(monkeypatch):
    observed_at, _ = _mock_pages(monkeypatch, count=1800)
    with pytest.raises(RuntimeError, match="P90L90_7D_PLUS_ATR200_SEED_INCOMPLETE"):
        observer._fetch_history(
            "LTCUSDT", observed_at, ("5",), p90l90_full_prev7d=True
        )
