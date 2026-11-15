from __future__ import annotations

import ast
import csv
import gzip
import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import bybit_workbench.research.entry_one_minute_displacement_p53 as p53
from bybit_workbench.domain.models import Candle
from bybit_workbench.research.entry_one_minute_displacement_p53 import (
    FrozenZoneConfig,
    OneMinuteZoneEngine,
    _aggregate_trade_archive,
    classify_shift,
    directional_shift_pct,
    validate_five_minute_equivalence,
)


def _candle(
    *,
    timeframe: str,
    opened_at: datetime,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "1",
) -> Candle:
    minutes = int(timeframe)
    return Candle(
        symbol="UNIUSDT",
        timeframe=timeframe,
        opened_at=opened_at,
        closed_at=opened_at + timedelta(minutes=minutes),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        is_closed=True,
    )


def test_directional_shift_has_same_semantics_for_long_and_short() -> None:
    baseline = Decimal("100")
    assert directional_shift_pct("Long", baseline, Decimal("99.7")) == -0.3
    assert directional_shift_pct("Short", baseline, Decimal("100.3")) == -0.3
    assert directional_shift_pct("Long", baseline, Decimal("100.2")) == 0.2
    assert directional_shift_pct("Short", baseline, Decimal("99.8")) == 0.2
    assert classify_shift(-0.3) == "deeper"
    assert classify_shift(0.0) == "same"
    assert classify_shift(0.2) == "outward"
    assert classify_shift(None) == "no_1m_zone"


def test_trade_archive_aggregates_exact_one_minute_ohlc(tmp_path: Path) -> None:
    day = date(2026, 8, 1)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    archive = tmp_path / "UNIUSDT2026-08-01.csv.gz"
    with gzip.open(archive, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "price", "size"])
        writer.writeheader()
        for minute in range(24 * 60):
            base = start + timedelta(minutes=minute)
            writer.writerow(
                {
                    "timestamp": str(base.timestamp() + 1),
                    "price": "100",
                    "size": "1",
                }
            )
            writer.writerow(
                {
                    "timestamp": str(base.timestamp() + 30),
                    "price": "101",
                    "size": "2",
                }
            )
            writer.writerow(
                {
                    "timestamp": str(base.timestamp() + 59),
                    "price": "99",
                    "size": "3",
                }
            )
    candles = _aggregate_trade_archive(archive, symbol="UNIUSDT", day=day)
    assert len(candles) == 1440
    first = candles[0]
    assert first.open == Decimal("100")
    assert first.high == Decimal("101")
    assert first.low == Decimal("99")
    assert first.close == Decimal("99")
    assert first.volume == Decimal("6")
    second = candles[1]
    assert second.open == Decimal("99")
    assert second.high == Decimal("101")
    assert second.low == Decimal("99")
    assert second.close == Decimal("99")


def test_trade_archive_nonempty_minute_uses_previous_close_as_open(tmp_path: Path) -> None:
    day = date(2026, 8, 1)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    archive = tmp_path / "UNIUSDT2026-08-01.csv.gz"
    with gzip.open(archive, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "price", "size"])
        writer.writeheader()
        for minute in range(24 * 60):
            base = start + timedelta(minutes=minute)
            price = "101" if minute == 0 else "100"
            writer.writerow(
                {
                    "timestamp": str(base.timestamp() + 10),
                    "price": price,
                    "size": "1",
                }
            )
    candles = _aggregate_trade_archive(
        archive,
        symbol="UNIUSDT",
        day=day,
        seed_price=Decimal("99"),
    )
    first = candles[0]
    assert first.open == Decimal("99")
    assert first.high == Decimal("101")
    assert first.low == Decimal("99")
    assert first.close == Decimal("101")
    second = candles[1]
    assert second.open == Decimal("101")
    assert second.high == Decimal("101")
    assert second.low == Decimal("100")
    assert second.close == Decimal("100")


def test_one_minute_to_five_minute_equivalence_is_exact() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    one = tuple(
        _candle(
            timeframe="1",
            opened_at=start + timedelta(minutes=index),
            open_=str(100 + index),
            high=str(101 + index),
            low=str(99 + index),
            close=str(100.5 + index),
        )
        for index in range(10)
    )
    five = (
        _candle(
            timeframe="5",
            opened_at=start,
            open_="100",
            high="105",
            low="99",
            close="104.5",
            volume="5",
        ),
        _candle(
            timeframe="5",
            opened_at=start + timedelta(minutes=5),
            open_="105",
            high="110",
            low="104",
            close="109.5",
            volume="5",
        ),
    )
    result = validate_five_minute_equivalence(one, five)
    assert result["compared_5m_candles"] == 2
    assert result["ohlcv_mismatches"] == 0


def test_pre_touch_zone_uses_only_completed_minutes() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    candles = tuple(
        _candle(
            timeframe="1",
            opened_at=start + timedelta(minutes=index),
            open_="100",
            high="101",
            low="99",
            close="100",
        )
        for index in range(30)
    )
    config = FrozenZoneConfig(
        five_minute_lookback=5,
        fifteen_minute_lookback=5,
        atr_period=3,
        zone_half_width_atr=Decimal("0.25"),
        confluence_max_gap_percent=Decimal("0.25"),
        shock_atr_period=100,
        shock_atr_multiple=Decimal("3"),
        embargo_minutes_after_shock=0,
    )
    engine = OneMinuteZoneEngine(candles, config)
    observed = start + timedelta(minutes=10)
    zone = engine.zone_at(observed)
    assert zone is not None
    assert zone.observed_at == observed
    assert zone.effective_lookback == 5


def test_one_minute_shock_maturity_is_minutes_not_five_minute_bars() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    candles: list[Candle] = []
    for index in range(140):
        candles.append(
            _candle(
                timeframe="1",
                opened_at=start + timedelta(minutes=index),
                open_="100",
                high="101",
                low="99",
                close="100",
            )
        )
    candles.append(
        _candle(
            timeframe="1",
            opened_at=start + timedelta(minutes=140),
            open_="100",
            high="120",
            low="80",
            close="100",
        )
    )
    for index in range(141, 230):
        candles.append(
            _candle(
                timeframe="1",
                opened_at=start + timedelta(minutes=index),
                open_="100",
                high="101",
                low="99",
                close="100",
            )
        )
    config = FrozenZoneConfig(
        five_minute_lookback=130,
        fifteen_minute_lookback=130,
        atr_period=3,
        zone_half_width_atr=Decimal("0.25"),
        confluence_max_gap_percent=Decimal("0.25"),
        shock_atr_period=5,
        shock_atr_multiple=Decimal("3"),
        embargo_minutes_after_shock=60,
    )
    engine = OneMinuteZoneEngine(tuple(candles), config)
    assert engine.zone_at(start + timedelta(minutes=190)) is None
    assert engine.zone_at(start + timedelta(minutes=202)) is not None


def test_trade_archive_internal_zero_trade_minute_is_causally_filled(tmp_path: Path) -> None:
    day = date(2026, 8, 1)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    archive = tmp_path / "UNIUSDT2026-08-01.csv.gz"
    with gzip.open(archive, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "price", "size"])
        writer.writeheader()
        for minute in range(24 * 60):
            if minute == 731:
                continue
            base = start + timedelta(minutes=minute)
            price = "105" if minute > 731 else "100"
            writer.writerow(
                {
                    "timestamp": str(base.timestamp() + 1),
                    "price": price,
                    "size": "1",
                }
            )
    candles = _aggregate_trade_archive(archive, symbol="UNIUSDT", day=day)
    assert len(candles) == 1440
    missing = candles[731]
    assert missing.opened_at == start + timedelta(minutes=731)
    assert missing.open == Decimal("100")
    assert missing.high == Decimal("100")
    assert missing.low == Decimal("100")
    assert missing.close == Decimal("100")
    assert missing.volume == Decimal("0")
    assert candles[732].open == Decimal("100")
    assert candles[732].high == Decimal("105")
    assert candles[732].low == Decimal("100")
    assert candles[732].close == Decimal("105")


def test_trade_archive_leading_zero_trade_minute_requires_seed(tmp_path: Path) -> None:
    day = date(2026, 8, 1)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    archive = tmp_path / "UNIUSDT2026-08-01.csv.gz"
    with gzip.open(archive, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "price", "size"])
        writer.writeheader()
        for minute in range(1, 24 * 60):
            base = start + timedelta(minutes=minute)
            writer.writerow(
                {
                    "timestamp": str(base.timestamp() + 1),
                    "price": "100",
                    "size": "1",
                }
            )
    try:
        _aggregate_trade_archive(archive, symbol="UNIUSDT", day=day)
    except ValueError as exc:
        assert "leading zero-trade minute" in str(exc)
        assert "00:00:00+00:00" in str(exc)
    else:
        raise AssertionError("leading zero-trade minute without causal seed must fail closed")

    candles = _aggregate_trade_archive(
        archive,
        symbol="UNIUSDT",
        day=day,
        seed_price=Decimal("99"),
    )
    assert candles[0].open == Decimal("99")
    assert candles[0].close == Decimal("99")
    assert candles[0].volume == Decimal("0")


def test_one_minute_to_five_minute_equivalence_checks_volume() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    one = tuple(
        _candle(
            timeframe="1",
            opened_at=start + timedelta(minutes=index),
            open_="100",
            high="100",
            low="100",
            close="100",
            volume="0" if index == 2 else "1",
        )
        for index in range(5)
    )
    five = (
        _candle(
            timeframe="5",
            opened_at=start,
            open_="100",
            high="100",
            low="100",
            close="100",
            volume="4",
        ),
    )
    result = validate_five_minute_equivalence(one, five)
    assert result["ohlcv_mismatches"] == 0

    wrong_volume = (
        _candle(
            timeframe="5",
            opened_at=start,
            open_="100",
            high="100",
            low="100",
            close="100",
            volume="5",
        ),
    )
    try:
        validate_five_minute_equivalence(one, wrong_volume)
    except ValueError as exc:
        assert "OHLCV" in str(exc)
    else:
        raise AssertionError("volume mismatch must fail the equivalence gate")



def test_trade_derived_continuous_open_reproduces_bybit_style_five_minute(tmp_path: Path) -> None:
    day = date(2026, 8, 1)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    archive = tmp_path / "UNIUSDT2026-08-01.csv.gz"
    with gzip.open(archive, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "price", "size"])
        writer.writeheader()
        prices = ("100", "102", "98", "101", "99")
        for minute in range(24 * 60):
            base = start + timedelta(minutes=minute)
            price = prices[minute % 5]
            writer.writerow(
                {
                    "timestamp": str(base.timestamp() + 10),
                    "price": price,
                    "size": "1",
                }
            )
    one = _aggregate_trade_archive(
        archive,
        symbol="UNIUSDT",
        day=day,
        seed_price=Decimal("97"),
    )
    five = (
        _candle(
            timeframe="5",
            opened_at=start,
            open_="97",
            high="102",
            low="97",
            close="99",
            volume="5",
        ),
    )
    result = validate_five_minute_equivalence(one, five)
    assert result["compared_5m_candles"] == 1
    assert result["ohlcv_mismatches"] == 0

def test_one_minute_engine_matches_frozen_zone_function() -> None:
    from bybit_workbench.research.mtf_entry_v3 import _precompute_post_shock_zones

    start = datetime(2026, 8, 1, tzinfo=UTC)
    candles: list[Candle] = []
    for index in range(180):
        if index == 90:
            high, low = "120", "80"
        else:
            high = str(101 + (index % 7) / 100)
            low = str(99 - (index % 5) / 100)
        candles.append(
            _candle(
                timeframe="1",
                opened_at=start + timedelta(minutes=index),
                open_="100",
                high=high,
                low=low,
                close="100",
            )
        )
    config = FrozenZoneConfig(
        five_minute_lookback=40,
        fifteen_minute_lookback=40,
        atr_period=7,
        zone_half_width_atr=Decimal("0.25"),
        confluence_max_gap_percent=Decimal("0.25"),
        shock_atr_period=10,
        shock_atr_multiple=Decimal("3"),
        embargo_minutes_after_shock=20,
    )
    frozen = _precompute_post_shock_zones(
        tuple(candles),
        timeframe="1",
        lookback=config.five_minute_lookback,
        atr_period=config.atr_period,
        width_atr=config.zone_half_width_atr,
        shock_atr_period=config.shock_atr_period,
        shock_atr_multiple=config.shock_atr_multiple,
        minimum_regime_bars=config.embargo_minutes_after_shock,
    )
    engine = OneMinuteZoneEngine(tuple(candles), config)
    for history_len in (40, 89, 91, 100, 111, 140, 180):
        observed_at = candles[history_len - 1].closed_at
        actual = engine.zone_at(observed_at)
        expected = frozen[history_len]
        assert actual == expected



def test_cache_day_rebuilds_when_causal_seed_differs_from_cached_first_open(tmp_path: Path) -> None:
    day = date(2026, 8, 1)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    archive = tmp_path / "UNIUSDT2026-08-01.csv.gz"
    with gzip.open(archive, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "price", "size"])
        writer.writeheader()
        for minute in range(24 * 60):
            base = start + timedelta(minutes=minute)
            writer.writerow(
                {
                    "timestamp": str(base.timestamp() + 10),
                    "price": "100",
                    "size": "1",
                }
            )

    cache_dir = tmp_path / "cache"
    cache_path = cache_dir / "UNIUSDT" / "2026-08-01.csv.gz"
    meta_path = cache_path.with_suffix(cache_path.suffix + ".json")
    stale = _aggregate_trade_archive(archive, symbol="UNIUSDT", day=day)
    p53._write_candle_cache(cache_path, stale)
    p53._write_json(
        meta_path,
        {
            "cache_version": p53.CACHE_VERSION,
            "manifest_sha256": "manifest",
            "source_archive_sha256": p53._sha256(archive),
            "cache_sha256": p53._sha256(cache_path),
            "rows": 1440,
            "candle_semantics": "previous_close_continuous_open_flat_zero_volume",
            "zero_trade_minutes": 0,
        },
    )
    assert stale[0].open == Decimal("100")

    rebuilt, _ = p53._cache_day(
        archive_path=archive,
        expected_archive_sha256=p53._sha256(archive),
        manifest_sha256="manifest",
        cache_dir=cache_dir,
        symbol="UNIUSDT",
        day=day,
        seed_price=Decimal("99"),
        heartbeat=p53.Heartbeat(999999),
    )
    assert rebuilt[0].open == Decimal("99")
    assert rebuilt[0].high == Decimal("100")
    assert rebuilt[0].low == Decimal("99")
    assert rebuilt[0].close == Decimal("100")


def test_run_p53_seeds_first_minute_from_frozen_five_minute_open() -> None:
    tree = ast.parse(inspect.getsource(p53.run_p53))
    load_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_load_one_minute_dataset"
    ]
    assert len(load_calls) == 1
    keyword = next(
        item for item in load_calls[0].keywords if item.arg == "initial_seed_price"
    )
    assert isinstance(keyword.value, ast.Attribute)
    assert keyword.value.attr == "open"
    assert isinstance(keyword.value.value, ast.Subscript)
    assert isinstance(keyword.value.value.value, ast.Name)
    assert keyword.value.value.value.id == "five"


def test_archive_map_calls_match_single_argument_contract() -> None:
    tree = ast.parse(inspect.getsource(p53))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_archive_map"
    ]
    assert len(calls) == 2
    assert all(len(call.args) == 1 and not call.keywords for call in calls)
