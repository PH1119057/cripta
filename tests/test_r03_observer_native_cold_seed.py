"""R03 native-owned verified cold seed: zero redundant REST and no second writer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data import candle_warmup
from operations.monitoring import r03_observer_closed_seed as seed

_NOW = datetime(2026, 11, 11, 6, 40, 10, tzinfo=UTC)
_ROOT = Path(__file__).resolve().parents[1]


def _bars(symbol: str, tf: str, *, through: datetime, n: int) -> tuple[Candle, ...]:
    step = timedelta(minutes=int(tf))
    return tuple(
        Candle(
            symbol=symbol, timeframe=tf,
            opened_at=through - step * (n-i),
            closed_at=through - step * (n-i-1),
            open=Decimal("101"), high=Decimal("103"),
            low=Decimal("100"), close=Decimal("102"),
            volume=Decimal("42"),
        )
        for i in range(n)
    )


def test_r03_provider_grace_history_uses_exact_active_identity_and_no_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[tuple[str, int, datetime]] = []

    class Query:
        def fetchall(self) -> list[tuple[str, ...]]:
            return [(
                "ms-bybit-mainnet-public-v1", "venue-bybit",
                "inst-bybit-linear-ltcusdt", "LTCUSDT", "LINEAR",
            )]

    class DB:
        def execute(self, sql: str, params: object) -> Query:
            assert "s.status='ACTIVE'" in sql
            assert params == ("LTCUSDT",)
            return Query()

    class Store:
        def __init__(self, db: object, key: Any) -> None:
            assert isinstance(db, DB)
            assert key.external_symbol == "LTCUSDT"
            assert key.product_category == "LINEAR"
            self.key = key

        def history(self, *, through: datetime, required: int) -> tuple[Candle, ...]:
            requested.append((self.key.timeframe, required, through))
            return _bars("LTCUSDT", self.key.timeframe, through=through, n=required)

        def ingest(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("canonical R03 reader never writes")

    monkeypatch.setattr(candle_warmup, "SharedClosedCandleStore", Store)
    rows = candle_warmup.load_verified_r03_native_closed_seed(
        DB(), symbol="LTCUSDT", required_counts={"5": 6, "15": 3},
        observed_at=_NOW,
    )
    assert {key: len(value) for key, value in rows.items()} == {"5": 6, "15": 3}
    assert requested == [
        ("5", 6, datetime(2026, 11, 11, 6, 35, tzinfo=UTC)),
        ("15", 3, datetime(2026, 11, 11, 6, 30, tzinfo=UTC)),
    ]


@pytest.mark.parametrize("symbol,required,at,reason", [
    ("BTCUSDT", {"5": 6}, _NOW, "R03_NATIVE_CLOSED_SEED_SCOPE_INVALID"),
    ("LTCUSDT", {"60": 6}, _NOW, "R03_NATIVE_CLOSED_SEED_SCOPE_INVALID"),
    ("LTCUSDT", {"1": 0}, _NOW, "R03_NATIVE_CLOSED_SEED_SCOPE_INVALID"),
    ("LTCUSDT", {"5": 10001}, _NOW, "R03_NATIVE_CLOSED_SEED_SCOPE_INVALID"),
    (
        "LTCUSDT", {"5": 6}, datetime(2026, 11, 11, 6, 40, 10),
        "R03_NATIVE_CLOSED_SEED_CLOCK_NOT_UTC",
    ),
])
def test_r03_rejects_unowned_scope_and_unproven_clock(
    symbol: str, required: dict[str, int], at: datetime, reason: str,
) -> None:
    with pytest.raises(ValueError, match=reason):
        candle_warmup.load_verified_r03_native_closed_seed(
            None, symbol=symbol, required_counts=required, observed_at=at,
        )


def test_r03_missing_committed_candle_fails_closed_not_rest_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Query:
        def fetchall(self) -> list[tuple[str, ...]]:
            return [(
                "ms-bybit-mainnet-public-v1", "venue-bybit",
                "inst-bybit-linear-ltcusdt", "LTCUSDT", "LINEAR",
            )]

    class DB:
        def execute(self, *_args: object) -> Query:
            return Query()

    class Store:
        def __init__(self, *_args: object) -> None:
            pass

        def history(self, **_kwargs: object) -> tuple[Candle, ...]:
            raise RuntimeError("CANDLE_HISTORY_GAP_OR_DUPLICATE")

        def ingest(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("missing R03 history never authorizes INSERT")

    monkeypatch.setattr(candle_warmup, "SharedClosedCandleStore", Store)
    called_rest: list[str] = []

    def forbidden_rest(*_args: object, **_kwargs: object) -> dict[str, tuple[Candle, ...]]:
        called_rest.append("REST")
        raise AssertionError("R03 native-owned 5/15 must not fetch REST")

    with pytest.raises(RuntimeError, match="CANDLE_HISTORY_GAP_OR_DUPLICATE"):
        seed.prepare_initial_r03_observer_history(
            DB(), symbol="LTCUSDT", observed_at=_NOW,
            intervals=("5", "15"), p90l90_full_prev7d=True,
            p90_gap_from=None, native_candle_reader=True,
            source_fetch=forbidden_rest,
        )
    assert called_rest == []


def test_r03_flat_p90_2600_and_1000_seed_has_zero_rest_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[dict[str, int]] = []

    def snapshot(_connection: object, *, symbol: str, required_counts: dict[str, int],
                 observed_at: datetime) -> dict[str, tuple[Candle, ...]]:
        assert (symbol, observed_at) == ("INJUSDT", _NOW)
        called.append(required_counts)
        return {tf: () for tf in required_counts}

    def no_rest(*_args: object, **_kwargs: object) -> dict[str, tuple[Candle, ...]]:
        raise AssertionError("No REST for already committed R03 5/15")

    monkeypatch.setattr(seed, "load_verified_r03_native_closed_seed", snapshot)
    result = seed.prepare_initial_r03_observer_history(
        None, symbol="INJUSDT", observed_at=_NOW,
        intervals=("5", "15"), p90l90_full_prev7d=True,
        p90_gap_from=None, native_candle_reader=True,
        source_fetch=no_rest,
    )
    assert result == {"5": (), "15": ()}
    assert called == [{"5": 2600, "15": 1000}]


def test_r03_unrelated_60m_retains_provider_and_native_5m_is_not_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        seed, "load_verified_r03_native_closed_seed",
        lambda *_args, **_kwargs: {"5": ()},
    )

    def source(symbol: str, observed: datetime, intervals: tuple[str, ...],
               **_kwargs: object) -> dict[str, tuple[Candle, ...]]:
        assert (symbol, observed) == ("APTUSDT", _NOW)
        seen.append(intervals)
        return {"60": ()}

    result = seed.prepare_initial_r03_observer_history(
        None, symbol="APTUSDT", observed_at=_NOW,
        intervals=("5", "60"), p90l90_full_prev7d=False,
        p90_gap_from=None, native_candle_reader=True,
        source_fetch=source,
    )
    assert result == {"5": (), "60": ()}
    assert seen == [("60",)]


@pytest.mark.parametrize("symbol,native_enabled,recovery", [
    ("BTCUSDT", True, None),
    ("LTCUSDT", False, None),
    ("LTCUSDT", True, {"5": _NOW - timedelta(hours=1)}),
])
def test_r03_other_owners_and_open_recovery_unchanged(
    monkeypatch: pytest.MonkeyPatch, symbol: str, native_enabled: bool,
    recovery: dict[str, datetime] | None,
) -> None:
    def prohibited_native(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Native fast path may not replace P90 OPEN recovery")

    monkeypatch.setattr(seed, "load_verified_r03_native_closed_seed", prohibited_native)
    captured: list[object] = []

    def source(got: str, observed: datetime, intervals: tuple[str, ...],
               **kwargs: object) -> dict[str, tuple[Candle, ...]]:
        captured.append((got, observed, intervals, kwargs))
        return {"5": ()}

    assert seed.prepare_initial_r03_observer_history(
        None, symbol=symbol, observed_at=_NOW,
        intervals=("5",), p90l90_full_prev7d=True,
        p90_gap_from=recovery, native_candle_reader=native_enabled,
        source_fetch=source,
    ) == {"5": ()}
    assert captured == [(
        symbol, _NOW, ("5",),
        {"p90l90_full_prev7d": True, "p90_gap_from": recovery},
    )]


def test_r03_observer_wires_cold_seed_without_touching_protected_exit() -> None:
    text = (
        _ROOT / "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    assert "prepare_initial_r03_observer_history(" in text
    assert "native_candle_reader=R03_NATIVE_CANDLE_READER" in text
    assert "source_fetch=_fetch_history" in text
    assert "p90_gap_from=recovery_from" in text
    assert "BYBIT_REST_KLINE_REAL_RESTART" in (
        _ROOT / "src/bybit_workbench/universal_entry/p90l90_real_restart_recovery.py"
    ).read_text(encoding="utf-8")
