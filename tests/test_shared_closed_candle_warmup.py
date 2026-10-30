"""On-demand common candle warmup: neither wait nor duplicate fetch."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.candle_warmup import ensure_closed_history
from bybit_workbench.market_data.shared_closed_candles import MarketCandleKey

KEY = MarketCandleKey(
    "ms-bybit-mainnet-public-v1", "venue-bybit",
    "inst-bybit-linear-ltcusdt", "LTCUSDT", "LINEAR", "5",
)
WHEN = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SHA = "a" * 40


def bars() -> tuple[Candle, ...]:
    return tuple(
        Candle(
            "LTCUSDT", "5",
            WHEN-timedelta(minutes=5*(2-i)),
            WHEN-timedelta(minutes=5*(1-i)),
            Decimal("1"), Decimal("2"), Decimal("0.5"), Decimal("1.5"),
            Decimal("100"),
        )
        for i in range(2)
    )


def test_cache_hit_requires_no_vendor_request() -> None:
    expected = bars()
    with patch("bybit_workbench.market_data.candle_warmup.SharedClosedCandleStore") as cls:
        cls.return_value.history.return_value = expected
        callback = MagicMock(side_effect=AssertionError("must not fetch again"))
        got = ensure_closed_history(
            MagicMock(), KEY, through=WHEN+timedelta(minutes=5),
            required=2, source_fetch=callback,
            source_transport="BYBIT_REST_KLINE",
            producer_release_commit=SHA, observed_at=WHEN,
        )
        assert got == expected
        callback.assert_not_called()
        cls.return_value.ingest.assert_not_called()


def test_gap_refetch_requires_exact_complete_source_window() -> None:
    with patch("bybit_workbench.market_data.candle_warmup.SharedClosedCandleStore") as cls:
        cls.return_value.history.side_effect = RuntimeError("CANDLE_WARMUP_INCOMPLETE")
        with pytest.raises(RuntimeError, match="WARMUP_INCOMPLETE"):
            ensure_closed_history(
                MagicMock(), KEY, through=WHEN,
                required=3, source_fetch=lambda *_: bars(),
                source_transport="BYBIT_REST_KLINE",
                producer_release_commit=SHA, observed_at=WHEN,
            )
        cls.return_value.ingest.assert_not_called()


def test_unexpected_fault_never_turns_into_retry() -> None:
    with patch("bybit_workbench.market_data.candle_warmup.SharedClosedCandleStore") as cls:
        cls.return_value.history.side_effect = RuntimeError("UNATTESTED_SOURCE")
        callback = MagicMock()
        with pytest.raises(RuntimeError, match="UNATTESTED_SOURCE"):
            ensure_closed_history(
                MagicMock(), KEY, through=WHEN,
                required=2, source_fetch=callback,
                source_transport="BYBIT_REST_KLINE",
                producer_release_commit=SHA, observed_at=WHEN,
            )
        callback.assert_not_called()
