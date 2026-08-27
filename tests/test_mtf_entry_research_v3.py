from __future__ import annotations

import csv
import gzip
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bybit_workbench.domain import Candle
from bybit_workbench.research.mtf_entry_v3 import (
    EntryResearchV3Config,
    EntrySignalV3,
    _flow_metrics_for_signal,
    _precompute_post_shock_zones,
    _write_json,
    aggregate_public_trade_archives,
    run_local_mtf_research,
)


def candle(
    *, timeframe: str, index: int, start: datetime, open_: str, high: str, low: str, close: str
) -> Candle:
    minutes = {"5": 5, "15": 15, "60": 60}[timeframe]
    opened = start + timedelta(minutes=index * minutes)
    return Candle(
        "UNIUSDT",
        timeframe,
        opened,
        opened + timedelta(minutes=minutes),
        Decimal(open_),
        Decimal(high),
        Decimal(low),
        Decimal(close),
        Decimal("100"),
    )


class EntryResearchV3Tests(unittest.TestCase):
    def test_defaults_are_90_days_and_exact_30_minute_cooldown(self) -> None:
        config = EntryResearchV3Config()
        self.assertEqual(config.days, 90)
        self.assertEqual(config.cooldown_minutes, 30)

    def test_post_shock_zone_excludes_the_shock_candle(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        history = [
            candle(
                timeframe="5",
                index=i,
                start=start,
                open_="100",
                high="101",
                low="99",
                close="100",
            )
            for i in range(20)
        ]
        shock = candle(
            timeframe="5", index=20, start=start, open_="100", high="120", low="80", close="90"
        )
        history.append(shock)
        for i in range(21, 34):
            history.append(
                candle(
                    timeframe="5",
                    index=i,
                    start=start,
                    open_="90",
                    high="92",
                    low="88",
                    close="90",
                )
            )
        zones = _precompute_post_shock_zones(
            tuple(history),
            timeframe="5",
            lookback=30,
            atr_period=3,
            width_atr=Decimal("0.25"),
            shock_atr_period=5,
            shock_atr_multiple=Decimal("3"),
            minimum_regime_bars=12,
        )
        zone = zones[len(history)]
        self.assertIsNotNone(zone)
        assert zone is not None
        self.assertEqual(zone.effective_lookback, 13)
        self.assertEqual(zone.range_low, Decimal("88"))
        self.assertEqual(zone.range_high, Decimal("92"))
        self.assertEqual(zone.regime_reset_at, shock.closed_at)

    def test_hourly_context_does_not_filter_opposite_local_entry(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        hourly = tuple(
            candle(
                timeframe="60",
                index=i,
                start=start,
                open_=str(100 + i),
                high=str(105 + i),
                low=str(95 + i),
                close=str(101 + i),
            )
            for i in range(20)
        )
        fifteen = tuple(
            candle(
                timeframe="15",
                index=i,
                start=start,
                open_="100",
                high="110",
                low="90",
                close="100",
            )
            for i in range(80)
        )
        five = [
            candle(
                timeframe="5",
                index=i,
                start=start,
                open_="100",
                high="104",
                low="96",
                close="100",
            )
            for i in range(240)
        ]
        # Touch local resistance while 1h context is rising.
        # P30 must still retain the short candidate.
        five[180] = candle(
            timeframe="5", index=180, start=start, open_="100", high="110", low="99", close="100"
        )
        config = EntryResearchV3Config(
            days=30,
            warmup_days=0,
            five_minute_lookback=6,
            fifteen_minute_lookback=6,
            hourly_lookback=3,
            atr_period=3,
            zone_half_width_atr=Decimal("0.25"),
            confluence_max_gap_percent=Decimal("5"),
            cooldown_minutes=0,
            horizons_minutes=(30,),
            shock_atr_period=1000,
            embargo_minutes_after_shock=0,
        )
        result = run_local_mtf_research(
            tuple(five), fifteen, hourly, config, evaluation_start=start
        )
        shorts = [signal for signal in result.signals if signal.direction == "Short"]
        self.assertTrue(shorts)
        self.assertTrue(any(signal.hourly_alignment == "opposed" for signal in shorts))

    def test_public_trade_archive_aggregates_taker_side(self) -> None:
        start = datetime(2026, 8, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "UNIUSDT2026-08-01.csv.gz"
            with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["timestamp", "symbol", "side", "size", "price"]
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "timestamp": str(start.timestamp() + 1),
                        "symbol": "UNIUSDT",
                        "side": "Buy",
                        "size": "2",
                        "price": "3",
                    }
                )
                writer.writerow(
                    {
                        "timestamp": str(start.timestamp() + 2),
                        "symbol": "UNIUSDT",
                        "side": "Sell",
                        "size": "1",
                        "price": "3",
                    }
                )
            buckets = aggregate_public_trade_archives(
                (path,), start_at=start, end_at=start + timedelta(minutes=1)
            )
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0].buy_notional, Decimal("6"))
        self.assertEqual(buckets[0].sell_notional, Decimal("3"))

    def test_json_writer_serializes_date_and_datetime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            _write_json(
                path,
                {
                    "day": date(2026, 8, 15),
                    "at": datetime(2026, 8, 16, tzinfo=UTC),
                    "value": Decimal("1.25"),
                },
            )
            text = path.read_text(encoding="utf-8")
        self.assertIn('"day": "2026-08-15"', text)
        self.assertIn('"at": "2026-08-16T00:00:00+00:00"', text)
        self.assertIn('"value": "1.25"', text)

    def test_fixed_trade_day_override_is_recorded_in_config(self) -> None:
        config = EntryResearchV3Config(
            latest_trade_day_override=date(2026, 8, 15),
        )
        self.assertEqual(config.latest_trade_day_override, date(2026, 8, 15))

    def test_flow_metric_is_directional_for_short(self) -> None:
        start = datetime(2026, 8, 1, 0, 5, tzinfo=UTC)
        signal = EntrySignalV3(
            symbol="UNIUSDT",
            direction="Short",
            entry_at=start,
            entry_price=Decimal("3"),
            hourly_context="Long",
            hourly_return_percent=Decimal("2"),
            hourly_alignment="opposed",
            fifteen_zone_low=Decimal("3"),
            fifteen_zone_high=Decimal("3.1"),
            five_zone_low=Decimal("3"),
            five_zone_high=Decimal("3.1"),
            zone_gap_percent=Decimal("0"),
            hourly_effective_lookback=10,
            fifteen_effective_lookback=10,
            five_effective_lookback=10,
            hourly_regime_reset_at=None,
            fifteen_regime_reset_at=None,
            five_regime_reset_at=None,
            outcome_metrics={},
        )
        from bybit_workbench.research.mtf_entry_v3 import FlowBucket

        bucket = FlowBucket(
            opened_at=start - timedelta(minutes=1),
            buy_notional=Decimal("25"),
            sell_notional=Decimal("75"),
        )
        metrics = _flow_metrics_for_signal(signal, {bucket.opened_at: bucket}, (1,))
        self.assertEqual(metrics["flow_1m_delta_pct"], Decimal("-50"))
        self.assertEqual(metrics["flow_1m_directional_delta_pct"], Decimal("50"))


if __name__ == "__main__":
    unittest.main()
