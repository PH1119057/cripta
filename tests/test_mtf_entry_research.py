from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain import Candle
from bybit_workbench.research.mtf_entry import (
    EntryResearchConfig,
    _confluence_score,
    _first_hit,
    _zone_gap_percent,
    run_entry_research,
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
    minutes = 5 if timeframe == "5" else 60
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


class EntryResearchHelpersTests(unittest.TestCase):
    def test_zone_gap_and_score_reward_overlap(self) -> None:
        self.assertEqual(
            _zone_gap_percent(
                Decimal("99"),
                Decimal("101"),
                Decimal("100"),
                Decimal("102"),
                Decimal("100"),
            ),
            Decimal("0"),
        )
        self.assertEqual(_confluence_score(Decimal("0"), Decimal("0.25")), Decimal("100"))
        self.assertEqual(_confluence_score(Decimal("0.125"), Decimal("0.25")), Decimal("50.0"))

    def test_first_hit_is_conservative_when_both_levels_cross_same_bar(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        future = (
            candle(
                timeframe="5",
                index=1,
                start=start,
                open_="100",
                high="101",
                low="99",
                close="100",
            ),
        )
        self.assertEqual(
            _first_hit("Long", Decimal("100"), future, Decimal("0.5"), Decimal("0.5")),
            "ambiguous_same_bar",
        )


class EntryResearchCausalityTests(unittest.TestCase):
    def test_future_hourly_candle_cannot_create_earlier_signal(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        hourly = tuple(
            candle(
                timeframe="60",
                index=index,
                start=start,
                open_="100",
                high="110" if index % 2 == 0 else "109",
                low="90",
                close="100",
            )
            for index in range(12)
        )
        five = tuple(
            candle(
                timeframe="5",
                index=index,
                start=start,
                open_="100",
                high="102",
                low="90" if index == 16 else "99",
                close="100",
            )
            for index in range(40)
        )
        config = EntryResearchConfig(
            days=1,
            warmup_days=0,
            five_minute_lookback=10,
            hourly_lookback=3,
            atr_period=3,
            zone_half_width_atr=Decimal("0.5"),
            confluence_max_gap_percent=Decimal("1"),
            cooldown_bars=0,
            horizons_minutes=(30,),
        )
        first = run_entry_research(five, hourly, config)
        changed = list(hourly)
        last = changed[-1]
        changed[-1] = Candle(
            last.symbol,
            last.timeframe,
            last.opened_at,
            last.closed_at,
            Decimal("100"),
            Decimal("500"),
            Decimal("1"),
            Decimal("100"),
            last.volume,
        )
        second = run_entry_research(five, tuple(changed), config)
        early_first = tuple(item for item in first.signals if item.entry_at < last.opened_at)
        early_second = tuple(item for item in second.signals if item.entry_at < last.opened_at)
        self.assertEqual(early_first, early_second)

    def test_baseline_uses_limit_touch_without_waiting_for_delayed_diamond(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        hourly = tuple(
            candle(
                timeframe="60",
                index=index,
                start=start,
                open_="100",
                high="110",
                low="90",
                close="100",
            )
            for index in range(12)
        )
        five_items = [
            candle(
                timeframe="5",
                index=index,
                start=start,
                open_="100",
                high="110",
                low="90",
                close="100",
            )
            for index in range(40)
        ]
        # At the start of this bar the prior 5m and completed 1h support zones already overlap.
        five_items.append(
            candle(
                timeframe="5",
                index=40,
                start=start,
                open_="100",
                high="100",
                low="90",
                close="96",
            )
        )
        five_items.extend(
            candle(
                timeframe="5",
                index=index,
                start=start,
                open_="96",
                high="102",
                low="95",
                close="101",
            )
            for index in range(41, 50)
        )
        config = EntryResearchConfig(
            days=1,
            warmup_days=0,
            five_minute_lookback=10,
            hourly_lookback=3,
            atr_period=3,
            zone_half_width_atr=Decimal("0.25"),
            confluence_max_gap_percent=Decimal("5"),
            cooldown_bars=0,
            horizons_minutes=(30,),
        )
        result = run_entry_research(tuple(five_items), hourly, config)
        self.assertTrue(any(item.direction == "Long" for item in result.signals))


if __name__ == "__main__":
    unittest.main()
