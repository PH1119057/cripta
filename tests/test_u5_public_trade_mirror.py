from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from bybit_workbench.universal_entry.transport_continuity import (
    ContinuityNotProvable,
    TradeCursor,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 11, 0, 0, tzinfo=UTC)
SYMBOLS = ("UNIUSDT", "XRPUSDT")


def _message(symbol: str, rows: list[dict[str, object]]) -> dict[str, object]:
    return {"topic": f"publicTrade.{symbol}", "type": "snapshot", "data": rows}


def _trade(
    symbol: str,
    exec_id: str,
    seq: int,
    at: datetime,
    *,
    price: str,
    size: str,
    side: str,
) -> dict[str, object]:
    return {
        "s": symbol,
        "i": exec_id,
        "seq": seq,
        "T": int(at.timestamp() * 1000),
        "p": price,
        "v": size,
        "S": side,
    }


def test_mirror_preserves_received_order_for_ambiguous_same_timestamp_seq() -> None:
    mirror = PublicTradeMirrorBuffer(SYMBOLS, retention_seconds=600)
    mirror.start_epoch(NOW)
    mirror.record_message(
        _message(
            "UNIUSDT",
            [_trade("UNIUSDT", "anchor", 100, NOW, price="10", size="1", side="Buy")],
        ),
        received_at=NOW,
    )
    tied = NOW + timedelta(seconds=1)
    mirror.record_message(
        _message(
            "UNIUSDT",
            [
                _trade("UNIUSDT", "first", 101, tied, price="10.1", size="2", side="Sell"),
                _trade("UNIUSDT", "second", 101, tied, price="10.2", size="3", side="Buy"),
            ],
        ),
        received_at=tied + timedelta(milliseconds=50),
    )
    mirror.record_message(
        _message(
            "XRPUSDT",
            [_trade("XRPUSDT", "x-anchor", 200, NOW, price="1", size="4", side="Buy")],
        ),
        received_at=NOW + timedelta(milliseconds=1),
    )
    recovered = mirror.recover_after(
        {
            "UNIUSDT": TradeCursor("UNIUSDT", "anchor", 100, NOW),
            "XRPUSDT": TradeCursor("XRPUSDT", "x-anchor", 200, NOW),
        },
        cutoff_at=NOW + timedelta(seconds=2),
    )
    assert [item.exec_id for item in recovered] == ["first", "second"]


def test_mirror_deduplicates_exact_exec_id_without_reordering() -> None:
    mirror = PublicTradeMirrorBuffer(("UNIUSDT",), retention_seconds=600)
    mirror.start_epoch(NOW)
    msg = _message(
        "UNIUSDT",
        [
            _trade("UNIUSDT", "a", 100, NOW, price="10", size="1", side="Buy"),
            _trade(
                "UNIUSDT", "b", 101, NOW + timedelta(seconds=1), price="11", size="1", side="Buy"
            ),
        ],
    )
    mirror.record_message(msg, received_at=NOW + timedelta(seconds=1))
    mirror.record_message(msg, received_at=NOW + timedelta(seconds=2))
    status = mirror.snapshot()
    assert status["events"] == 2
    assert status["duplicates"] == 2


def test_mirror_new_epoch_never_glues_old_gap() -> None:
    mirror = PublicTradeMirrorBuffer(("UNIUSDT",), retention_seconds=600)
    mirror.start_epoch(NOW)
    mirror.record_message(
        _message(
            "UNIUSDT",
            [_trade("UNIUSDT", "anchor", 100, NOW, price="10", size="1", side="Buy")],
        ),
        received_at=NOW,
    )
    mirror.mark_gap("synthetic")
    mirror.start_epoch(NOW + timedelta(seconds=10))
    with pytest.raises(ContinuityNotProvable, match="mirror exact anchor missing"):
        mirror.recover_after(
            {"UNIUSDT": TradeCursor("UNIUSDT", "anchor", 100, NOW)},
            cutoff_at=NOW + timedelta(seconds=11),
        )


def test_mirror_exposes_current_exact_cursors_and_same_seq_ids() -> None:
    mirror = PublicTradeMirrorBuffer(("UNIUSDT",), retention_seconds=600)
    mirror.start_epoch(NOW)
    tied = NOW + timedelta(seconds=1)
    mirror.record_message(
        _message(
            "UNIUSDT",
            [
                _trade("UNIUSDT", "a", 101, tied, price="10", size="1", side="Buy"),
                _trade("UNIUSDT", "b", 101, tied, price="11", size="2", side="Sell"),
            ],
        ),
        received_at=tied,
    )
    cursors, ids = mirror.current_cursors()
    assert cursors["UNIUSDT"].exec_id == "b"
    assert cursors["UNIUSDT"].seq == 101
    assert ids["UNIUSDT"] == {"a", "b"}


def test_runtime_mirror_is_public_trade_only_and_recovery_only() -> None:
    source = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")
    assert "PublicTradeMirrorBuffer" in source
    assert "_run_public_trade_mirror" in source
    assert "MIRROR_RETENTION_SECONDS = 600" in source
    assert "trade_recovery_source" in source
    assert '"MIRROR_WS"' in source
    assert 'f"publicTrade.{symbol}"' in source
    forbidden = ("api_key", "api_secret", "runtime.trade_commands", "runtime.executions")
    lowered = source.lower()
    for token in forbidden:
        assert token not in lowered


def test_runtime_recovery_override_does_not_call_rest_trade_or_reorder_tied_trades(
    monkeypatch,
) -> None:
    from decimal import Decimal

    from bybit_workbench.domain.models import Candle
    from bybit_workbench.universal_entry import FrozenPolicy, MarketFactEnvelope
    from operations.monitoring import universal_entry_shadow as runtime

    symbol = "UNIUSDT"
    anchor = TradeCursor(symbol, "anchor", 100, NOW)
    tied = NOW + timedelta(seconds=1)

    def trade_fact(exec_id: str, price: str, side: str) -> tuple[MarketFactEnvelope, TradeCursor]:
        fact = MarketFactEnvelope(
            fact_id="trade-" + exec_id,
            event_kind="PUBLIC_TRADE",
            symbol=symbol,
            observed_at=tied,
            event_at=tied,
            received_at=tied + timedelta(milliseconds=50),
            source_refs=(f"mirror:{exec_id}",),
            attributes=FrozenPolicy.from_mapping({"price": price, "size": "1", "taker_side": side}),
        )
        return fact, TradeCursor(symbol, exec_id, 101, tied)

    override = (
        trade_fact("first", "10.1", "Sell"),
        trade_fact("second", "10.2", "Buy"),
    )
    current_5 = Candle(
        symbol=symbol,
        timeframe="5",
        opened_at=NOW,
        closed_at=NOW + timedelta(minutes=5),
        open=Decimal("10"),
        high=Decimal("10.3"),
        low=Decimal("9.9"),
        close=Decimal("10.2"),
        volume=Decimal("100"),
        is_closed=False,
    )

    monkeypatch.setattr(runtime, "FACT_SOURCE_ID", runtime.REST_OI30S_SOURCE_ID)

    def no_rest_trade(path: str, _params):
        if path == "/v5/market/recent-trade":
            raise AssertionError("REST trade fallback must not run when mirror override is proven")
        raise AssertionError(path)

    monkeypatch.setattr(runtime, "_get_json", no_rest_trade)
    monkeypatch.setattr(
        runtime,
        "_fetch_gap_candles",
        lambda _symbol, timeframe, _ready: (current_5,) if timeframe == "5" else (),
    )

    replay, counts = runtime._recover_public_gap(
        symbols=(symbol,),
        ready_local_at=NOW + timedelta(seconds=2),
        ready_server_at=NOW + timedelta(seconds=2),
        trade_cursors={symbol: anchor},
        oi_cursors={},
        closed_boundaries={(symbol, "5"): NOW, (symbol, "15"): NOW, (symbol, "60"): NOW},
        bar_open_boundaries={symbol: NOW},
        known_trade_exec_ids={symbol: {"anchor"}},
        trade_replay_override=override,
    )
    trades = [fact.fact_id for fact, _cursor in replay if fact.event_kind == "PUBLIC_TRADE"]
    assert trades == ["trade-first", "trade-second"]
    assert counts["PUBLIC_TRADE"] == 2


def test_recovery_merge_preserves_mirror_receive_order_for_tied_trade_time(monkeypatch) -> None:
    from decimal import Decimal

    from bybit_workbench.domain.models import Candle
    from bybit_workbench.universal_entry import FrozenPolicy, MarketFactEnvelope
    from operations.monitoring import universal_entry_shadow as runtime

    symbol = "UNIUSDT"
    anchor = TradeCursor(symbol, "anchor", 100, NOW)
    tied = NOW + timedelta(seconds=1)

    def fact(exec_id: str, seq: int, price: str, side: str):
        envelope = MarketFactEnvelope(
            fact_id=f"trade-{exec_id}",
            event_kind="PUBLIC_TRADE",
            symbol=symbol,
            observed_at=tied,
            event_at=tied,
            received_at=tied + timedelta(milliseconds=50),
            source_refs=(f"mirror:{exec_id}",),
            attributes=FrozenPolicy.from_mapping({"price": price, "size": "1", "taker_side": side}),
        )
        return envelope, TradeCursor(symbol, exec_id, seq, tied)

    override = (fact("first", 102, "10.2", "Sell"), fact("second", 101, "10.1", "Buy"))
    current_5 = Candle(
        symbol=symbol,
        timeframe="5",
        opened_at=NOW,
        closed_at=NOW + timedelta(minutes=5),
        open=Decimal("10"),
        high=Decimal("10.3"),
        low=Decimal("9.9"),
        close=Decimal("10.2"),
        volume=Decimal("100"),
        is_closed=False,
    )
    monkeypatch.setattr(runtime, "FACT_SOURCE_ID", runtime.REST_OI30S_SOURCE_ID)
    monkeypatch.setattr(
        runtime,
        "_fetch_gap_candles",
        lambda _symbol, timeframe, _ready: (current_5,) if timeframe == "5" else (),
    )
    replay, _counts = runtime._recover_public_gap(
        symbols=(symbol,),
        ready_local_at=NOW + timedelta(seconds=2),
        ready_server_at=NOW + timedelta(seconds=2),
        trade_cursors={symbol: anchor},
        oi_cursors={},
        closed_boundaries={(symbol, "5"): NOW, (symbol, "15"): NOW, (symbol, "60"): NOW},
        bar_open_boundaries={symbol: NOW},
        known_trade_exec_ids={symbol: {"anchor"}},
        trade_replay_override=override,
    )
    trade_ids = [f.fact_id for f, _cursor in replay if f.event_kind == "PUBLIC_TRADE"]
    assert trade_ids == ["trade-first", "trade-second"]


def test_runtime_does_not_rest_audit_a_proven_mirror_before_recovery() -> None:
    source = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")
    block = source.split("mirror_cursors, mirror_seq_ids = trade_mirror.current_cursors()", 1)[1]
    block = block.split("mirror_events = trade_mirror.recover_after(", 1)[0]
    assert "_audit_public_trade_silence" not in block


def test_mirror_replay_uses_receive_time_and_keeps_ws_order_even_if_trade_time_regresses(
    monkeypatch,
) -> None:
    from bybit_workbench.universal_entry import FrozenPolicy, MarketFactEnvelope
    from operations.monitoring import universal_entry_shadow as runtime

    symbol = "UNIUSDT"
    first_trade_time = NOW + timedelta(seconds=2)
    second_trade_time = NOW + timedelta(seconds=1)
    first_received = NOW + timedelta(seconds=3)
    second_received = NOW + timedelta(seconds=3, milliseconds=1)

    def item(exec_id: str, seq: int, event_at: datetime, received_at: datetime):
        fact = MarketFactEnvelope(
            fact_id=f"trade-{exec_id}",
            event_kind="PUBLIC_TRADE",
            symbol=symbol,
            observed_at=event_at,
            event_at=event_at,
            received_at=received_at,
            source_refs=(f"mirror:{exec_id}",),
            attributes=FrozenPolicy.from_mapping({"price": "10", "size": "1", "taker_side": "Buy"}),
        )
        return fact, TradeCursor(symbol, exec_id, seq, event_at)

    override = (
        item("first", 101, first_trade_time, first_received),
        item("second", 102, second_trade_time, second_received),
    )
    monkeypatch.setattr(runtime, "FACT_SOURCE_ID", runtime.REST_OI30S_SOURCE_ID)
    monkeypatch.setattr(runtime, "_fetch_gap_candles", lambda *_args, **_kwargs: ())
    replay, _counts = runtime._recover_public_gap(
        symbols=(),
        ready_local_at=NOW + timedelta(seconds=4),
        ready_server_at=NOW + timedelta(seconds=4),
        trade_cursors={},
        oi_cursors={},
        closed_boundaries={},
        bar_open_boundaries={},
        trade_replay_override=override,
    )
    assert [fact.fact_id for fact, _cursor in replay] == ["trade-first", "trade-second"]


def test_oi_gap_merge_never_reorders_existing_replay() -> None:
    from bybit_workbench.universal_entry import FrozenPolicy, MarketFactEnvelope
    from operations.monitoring import universal_entry_shadow as runtime

    symbol = "UNIUSDT"
    tied = NOW + timedelta(seconds=1)
    first = MarketFactEnvelope(
        fact_id="trade-first",
        event_kind="PUBLIC_TRADE",
        symbol=symbol,
        observed_at=tied,
        event_at=tied,
        received_at=tied,
        source_refs=("mirror:first",),
        attributes=FrozenPolicy.from_mapping({"price": "10", "size": "1", "taker_side": "Buy"}),
    )
    second = MarketFactEnvelope(
        fact_id="trade-second",
        event_kind="PUBLIC_TRADE",
        symbol=symbol,
        observed_at=tied,
        event_at=tied,
        received_at=tied + timedelta(milliseconds=2),
        source_refs=("mirror:second",),
        attributes=FrozenPolicy.from_mapping({"price": "11", "size": "1", "taker_side": "Sell"}),
    )
    oi = MarketFactEnvelope(
        fact_id="oi-mid",
        event_kind="OPEN_INTEREST",
        symbol=symbol,
        observed_at=tied,
        event_at=tied,
        received_at=tied + timedelta(milliseconds=1),
        source_refs=("oi:mid",),
        attributes=FrozenPolicy.from_mapping({"open_interest": "100"}),
    )
    replay = (
        (first, TradeCursor(symbol, "first", 101, tied)),
        (second, TradeCursor(symbol, "second", 102, tied)),
    )
    merged = runtime._merge_gap_oi_without_reordering_replay(replay, [oi])
    trade_ids = [fact.fact_id for fact, _cursor in merged if fact.event_kind == "PUBLIC_TRADE"]
    assert trade_ids == ["trade-first", "trade-second"]
    assert [fact.fact_id for fact, _cursor in merged] == ["trade-first", "oi-mid", "trade-second"]


def test_runtime_has_clean_continuity_stop_before_generic_crash_handler() -> None:
    source = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")
    clean = source.index("except ContinuityNotProvable as exc:")
    generic = source.index("except Exception as exc:", clean)
    assert clean < generic
    block = source[clean:generic]
    assert 'status="NOT_COMPARABLE"' in block
    assert "return" in block
    assert "raise" not in block
