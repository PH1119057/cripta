from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain import Candle
from bybit_workbench.research.mtf_entry import _build_zone, _interval_milliseconds
from bybit_workbench.research.mtf_entry_v2 import (
    EntryResearchV2Config,
    run_mtf15_regime_research,
)


def candle(
    *,
    timeframe: str,
    index: int,
    start: datetime,
    open_: str,
    high: str,
    low: str,
    close: str,
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


class EntryResearchV2Tests(unittest.TestCase):
    def test_15m_interval_is_supported(self) -> None:
        self.assertEqual(_interval_milliseconds("15"), 15 * 60_000)

    def test_default_horizon_covers_five_hour_move(self) -> None:
        config = EntryResearchV2Config()
        self.assertIn(360, config.horizons_minutes)

    def test_hourly_context_filters_direction_but_does_not_need_zone_overlap(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        hourly = tuple(
            candle(
                timeframe="60",
                index=index,
                start=start,
                open_=str(100 + index),
                high=str(111 + index),
                low=str(90 + index),
                close=str(101 + index),
            )
            for index in range(10)
        )
        fifteen = tuple(
            candle(
                timeframe="15",
                index=index,
                start=start,
                open_="100",
                high="110",
                low="90",
                close="100",
            )
            for index in range(40)
        )
        five = [
            candle(
                timeframe="5",
                index=index,
                start=start,
                open_="100",
                high="100",
                low="96",
                close="100",
            )
            for index in range(120)
        ]
        # Known 5m/15m support is touched after enough completed 15m and 1h history.
        five[90] = candle(
            timeframe="5",
            index=90,
            start=start,
            open_="100",
            high="100",
            low="90",
            close="96",
        )
        config = EntryResearchV2Config(
            days=1,
            warmup_days=0,
            five_minute_lookback=6,
            fifteen_minute_lookback=6,
            hourly_lookback=3,
            atr_period=3,
            zone_half_width_atr=Decimal("0.25"),
            confluence_max_gap_percent=Decimal("5"),
            cooldown_bars=0,
            horizons_minutes=(30, 360),
            embargo_minutes_after_shock=0,
        )
        result = run_mtf15_regime_research(
            tuple(five), fifteen, hourly, config
        )
        self.assertTrue(any(item.direction == "Long" for item in result.signals))
        self.assertTrue(all(item.hourly_bias == item.direction for item in result.signals))

    def test_post_shock_zone_waits_for_one_hour_of_new_5m_data(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        history: list[Candle] = []
        for index in range(20):
            history.append(
                candle(
                    timeframe="5",
                    index=index,
                    start=start,
                    open_="100",
                    high="101",
                    low="99",
                    close="100",
                )
            )
        history.append(
            candle(
                timeframe="5",
                index=20,
                start=start,
                open_="100",
                high="112",
                low="88",
                close="90",
            )
        )
        for index in range(21, 31):
            history.append(
                candle(
                    timeframe="5",
                    index=index,
                    start=start,
                    open_="90",
                    high="91",
                    low="89",
                    close="90",
                )
            )
        before = _build_zone(
            tuple(history),
            observed_at=history[-1].closed_at,
            timeframe="5",
            lookback=30,
            atr_period=3,
            width_atr=Decimal("0.25"),
            variant="adaptive",
            shock_atr_period=5,
            shock_atr_multiple=Decimal("3"),
            minimum_regime_bars=12,
        )
        self.assertIsNone(before)
        history.append(
            candle(
                timeframe="5",
                index=31,
                start=start,
                open_="90",
                high="91",
                low="89",
                close="90",
            )
        )
        after = _build_zone(
            tuple(history),
            observed_at=history[-1].closed_at,
            timeframe="5",
            lookback=30,
            atr_period=3,
            width_atr=Decimal("0.25"),
            variant="adaptive",
            shock_atr_period=5,
            shock_atr_multiple=Decimal("3"),
            minimum_regime_bars=12,
        )
        self.assertIsNotNone(after)
        assert after is not None
        self.assertEqual(after.effective_lookback, 12)
        self.assertEqual(after.regime_reset_at, history[20].opened_at)


if __name__ == "__main__":
    unittest.main()
