"""R03: exactly one native LINEAR closed-candle writer, Observer read-only.

No real database, trade order, external provider, private account or LIVE ARM
is touched by this bounded source/consumer ownership regression.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data import candle_warmup
from bybit_workbench.market_data.shared_closed_candles import CandleSourceConflict
from operations.monitoring.public_market_source_union_capture import (
    SourceManifestInvalid,
    parse_explicit_source_manifest,
    r03_closed_only_feed_union,
)

_ROOT = Path(__file__).resolve().parents[1]
_SYMBOLS = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")
_MANIFEST = _ROOT / "operations/monitoring/required_public_feeds.json"


def _payload() -> list[dict[str, Any]]:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def test_r03_exact_stage_only_original_trade_and_closed_1_5_15() -> None:
    approved = parse_explicit_source_manifest(
        json.dumps(_payload()), native_symbols=_SYMBOLS,
    )
    closed_only = r03_closed_only_feed_union(
        approved, native_symbols=_SYMBOLS,
    )
    assert len(approved.websocket_topics) == 45
    assert len(closed_only.websocket_topics) == 20
    assert not closed_only.rest_inputs
    assert not closed_only.websocket_batches("SPOT")
    assert {topic.product for topic in closed_only.websocket_topics} == {"LINEAR"}
    assert {topic.dataset_topic for topic in closed_only.websocket_topics} == {
        f"publicTrade.{symbol}" for symbol in _SYMBOLS
    } | {
        f"kline.{tf}.{symbol}" for symbol in _SYMBOLS
        for tf in ("1", "5", "15")
    }
    assert all(
        item.dataset in ("PUBLIC_TRADE", "CLOSED_KLINE")
        and item.product == "LINEAR"
        for item in closed_only.requirements
    )


def test_r03_missing_one_minute_required_source_blocks_before_socket() -> None:
    rows = _payload()
    for row in rows:
        if row["symbol"] == "LTCUSDT" and row["dataset"] == "CLOSED_KLINE" \
                and row["timeframe"] == "1":
            row["required"] = False
    full = parse_explicit_source_manifest(
        json.dumps(rows), native_symbols=_SYMBOLS,
    )
    with pytest.raises(SourceManifestInvalid, match="R03_EXACT_CLOSED_ONLY"):
        r03_closed_only_feed_union(full, native_symbols=_SYMBOLS)


def test_r03_explicit_native_and_observer_units_never_enable_r04_or_r05() -> None:
    native = (_ROOT / "operations/systemd/cripta-native-public-trade-source.service"
              ).read_text(encoding="utf-8")
    observer = (_ROOT / "operations/systemd/cripta-universal-entry-observer.service"
                ).read_text(encoding="utf-8")
    main = (_ROOT / "operations/monitoring/native_public_trade_source.py"
            ).read_text(encoding="utf-8")
    union = (_ROOT / "operations/monitoring/public_market_source_union_capture.py"
             ).read_text(encoding="utf-8")
    assert "Environment=CRIPTA_SOURCE_CLOSED_KLINES=1" in native
    assert "Environment=CRIPTA_SOURCE_KLINE_INTERVALS=1,5,15" in native
    assert (
        "Environment=CRIPTA_SOURCE_UNION_MANIFEST_FILE="
        "/etc/cripta/market-source/required_public_feeds.json"
    ) in native
    assert "Environment=CRIPTA_SOURCE_UNION_CAPTURE=1" not in native
    assert "Environment=CRIPTA_SOURCE_PUBLICATION=1" not in native
    assert "Environment=CRIPTA_R03_NATIVE_CANDLE_READER=ENABLED" in observer
    assert "r03_closed_only_feed_union(" in main
    assert "closed_only=capture_closed" in main
    assert "should_stop=lambda: stopping" in main
    assert "source_stopping = should_stop or" in union
    assert "and not source_stopping():" in union
    assert "and not repair_inflight" in union
    assert "repair_inflight = True" in union
    assert "repair_inflight = False" in union


def _closed() -> Candle:
    finish = datetime(2026, 11, 10, 22, 5, tzinfo=UTC)
    return Candle(
        symbol="LTCUSDT", timeframe="5",
        opened_at=finish - timedelta(minutes=5), closed_at=finish,
        open=Decimal("101"), high=Decimal("103"),
        low=Decimal("100"), close=Decimal("102"),
        volume=Decimal("42"),
    )


@pytest.mark.parametrize("bad", [False, True])
def test_r03_observer_history_never_writes_and_enforces_exact_parity(
    monkeypatch: pytest.MonkeyPatch, bad: bool,
) -> None:
    original = _closed()
    expected = (original,)
    calls: list[str] = []

    class _Query:
        def fetchall(self) -> list[tuple[str, ...]]:
            return [(
                "ms-bybit-mainnet-public-v1", "venue-bybit",
                "inst-bybit-linear-ltcusdt", "LTCUSDT", "LINEAR",
            )]

    class DB:
        def execute(self, _query: str, _args: object) -> _Query:
            return _Query()

    class Store:
        def __init__(self, _db: object, _key: object) -> None:
            pass

        def history(self, *, through: datetime, required: int) -> tuple[Candle, ...]:
            calls.append("history")
            assert through == original.closed_at and required == 1
            if bad:
                return (replace(original, close=Decimal("101")),)
            return expected

        def ingest(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("R03 Observer MUST NOT INSERT canonical candles")

    monkeypatch.setattr(candle_warmup, "SharedClosedCandleStore", Store)
    monkeypatch.setenv("CRIPTA_R03_NATIVE_CANDLE_READER", "ENABLED")

    def invoke() -> dict[str, tuple[Candle, ...]]:
        return candle_warmup.cache_approved_bybit_history(
            DB(), symbol="LTCUSDT", history={"5": expected},
            observed_at=original.closed_at + timedelta(seconds=1),
            producer_release_commit="a" * 40,
        )

    if bad:
        with pytest.raises(CandleSourceConflict):
            invoke()
    else:
        assert invoke() == {"5": expected}
    assert calls == ["history"]


def test_r03_missing_native_close_fails_without_observer_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Query:
        def fetchall(self) -> list[tuple[str, ...]]:
            return [(
                "ms-bybit-mainnet-public-v1", "venue-bybit",
                "inst-bybit-linear-ltcusdt", "LTCUSDT", "LINEAR",
            )]

    class DB:
        def execute(self, _query: str, _args: object) -> Query:
            return Query()

    class Store:
        def __init__(self, *_args: object) -> None:
            pass

        def history(self, **_kwargs: object) -> None:
            raise RuntimeError("CANDLE_WARMUP_INCOMPLETE")

        def ingest(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("missing R03 source never means fallback writer")

    monkeypatch.setattr(candle_warmup, "SharedClosedCandleStore", Store)
    with pytest.raises(RuntimeError, match="CANDLE_WARMUP_INCOMPLETE"):
        candle_warmup.cache_approved_bybit_history(
            DB(), symbol="LTCUSDT", history={"5": (_closed(),)},
            observed_at=_closed().closed_at + timedelta(seconds=1),
            producer_release_commit="a" * 40,
            verify_only=True, source_wait_seconds=0.0,
        )


def test_r03_all_active_observer_recovery_writers_become_read_only() -> None:
    observer = (
        _ROOT / "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    prepare = (
        _ROOT / "operations/monitoring/observer_preparation.py"
    ).read_text(encoding="utf-8")
    recovery = (
        _ROOT / "src/bybit_workbench/universal_entry/p90l90_real_restart_recovery.py"
    ).read_text(encoding="utf-8")
    assert "CRIPTA_R03_NATIVE_CANDLE_READER" in (
        _ROOT / "src/bybit_workbench/market_data/candle_warmup.py"
    ).read_text(encoding="utf-8")
    assert "verify_only=R03_NATIVE_CANDLE_READER" not in observer
    assert observer.count(
        "verify_native_source_only=R03_NATIVE_CANDLE_READER"
    ) == 2
    assert "native_closed_only=R03_NATIVE_CANDLE_READER" not in observer
    assert "verify_only=verify_native_source_only" in prepare
    assert "BYBIT_REST_KLINE_REAL_RESTART" in recovery
    assert "CRIPTA_R03_NATIVE_CANDLE_READER" in (
        _ROOT / "src/bybit_workbench/market_data/candle_warmup.py"
    ).read_text(encoding="utf-8")
    assert "CANDLE_IMMUTABLE_SOURCE_CONFLICT" in (
        _ROOT / "src/bybit_workbench/market_data/shared_closed_candles.py"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "symbol,timeframe",
    (("BTCUSDT", "5"), ("LTCUSDT", "60")),
)
def test_r03_does_not_revoke_other_closed_candle_source_owners(
    monkeypatch: pytest.MonkeyPatch, symbol: str, timeframe: str,
) -> None:
    # R03 reader-only source ownership must not absorb old Observer assets or
    # unrelated intervals (e.g. the ordinary R1 60m context). No REAL DB.
    step = timedelta(minutes=int(timeframe))
    close_at = datetime(2026, 11, 10, 23, 0, tzinfo=UTC)
    candle = Candle(
        symbol=symbol, timeframe=timeframe,
        opened_at=close_at - step, closed_at=close_at,
        open=Decimal("101"), high=Decimal("103"),
        low=Decimal("100"), close=Decimal("102"),
        volume=Decimal("42"),
    )
    writes: list[str] = []

    class Query:
        def fetchall(self) -> list[tuple[str, str, str, str, str]]:
            return [(
                "ms-bybit-mainnet-public-v1", "venue-bybit",
                f"inst-bybit-linear-{symbol.lower()}", symbol, "LINEAR",
            )]

    class DB:
        def execute(self, _query: str, _args: object) -> Query:
            return Query()

    class Store:
        def __init__(self, _db: object, _key: object) -> None:
            pass

        def ingest(self, _rows: object, **_kwargs: object) -> int:
            writes.append("ingest")
            return 1

        def history(self, *, through: datetime, required: int) -> tuple[Candle, ...]:
            assert (through, required) == (close_at, 1)
            return (candle,)

    monkeypatch.setattr(candle_warmup, "SharedClosedCandleStore", Store)
    monkeypatch.setenv("CRIPTA_R03_NATIVE_CANDLE_READER", "ENABLED")
    assert candle_warmup.cache_approved_bybit_history(
        DB(), symbol=symbol, history={timeframe: (candle,)},
        observed_at=close_at + timedelta(seconds=1),
        producer_release_commit="a" * 40,
    ) == {timeframe: (candle,)}
    assert writes == ["ingest"]


def test_r03_canonical_source_scope_is_explicit_for_five_by_three() -> None:
    src = (
        _ROOT / "src/bybit_workbench/market_data/candle_warmup.py"
    ).read_text(encoding="utf-8")
    assert "and symbol in r03_symbols" in src
    assert 'and tf in ("1", "5", "15")' in src
    assert "read_only = verify_only or r03_native_owned" in src
    assert "if read_only:" in src
