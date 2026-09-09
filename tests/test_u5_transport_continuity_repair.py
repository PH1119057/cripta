from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.transport_continuity import (
    ContinuityAction,
    ContinuityNotProvable,
    ExactFactDeduper,
    OiSampleCursor,
    TradeCursor,
    assess_oi30s_continuity,
    build_exact_trade_replay,
    expected_boundaries,
    resolve_continuity_action,
)

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "operations/monitoring/universal_entry_shadow.py"
CONTRACT = ROOT / "docs/UNIVERSAL_STRATEGY_ENTRY_IMPLEMENTATION_RU.md"
NOW = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)


def test_transport_repair_contract_is_frozen_before_source() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    assert "### 20.9 U5 public transport continuity repair" in text
    assert "BYBIT_PUBLIC_NORMALIZED_U5_V1_OI30S" in text
    assert "5m OI is NOT semantically substitutable" in text
    assert "same process/service_instance_id/parity_run_id/started_at" in text
    assert "reconnect alone is never proof of continuity" in text


def test_short_reconnect_before_all_oi30s_deadlines_is_proven() -> None:
    cursors = {
        "UNIUSDT": OiSampleCursor("UNIUSDT", NOW, 101),
        "XRPUSDT": OiSampleCursor("XRPUSDT", NOW + timedelta(seconds=2), 202),
    }
    result = assess_oi30s_continuity(
        cursors,
        required_symbols=("UNIUSDT", "XRPUSDT"),
        subscription_ready_server_at=NOW + timedelta(seconds=20),
        sample_seconds=30,
    )
    assert result.proven is True
    assert result.earliest_deadline == NOW + timedelta(seconds=30)


def test_crossed_oi30s_deadline_is_not_provable_and_never_uses_5m_history() -> None:
    cursors = {"UNIUSDT": OiSampleCursor("UNIUSDT", NOW, 101)}
    result = assess_oi30s_continuity(
        cursors,
        required_symbols=("UNIUSDT",),
        subscription_ready_server_at=NOW + timedelta(seconds=30),
        sample_seconds=30,
    )
    assert result.proven is False
    assert "OI30S" in result.reason
    assert "5m" in result.reason
    assert resolve_continuity_action(result) is ContinuityAction.NOT_COMPARABLE


def test_missing_exact_oi_cursor_is_not_provable() -> None:
    result = assess_oi30s_continuity(
        {},
        required_symbols=("UNIUSDT",),
        subscription_ready_server_at=NOW + timedelta(seconds=1),
        sample_seconds=30,
    )
    assert result.proven is False
    assert "cursor" in result.reason.lower()


def test_exact_trade_replay_requires_exec_id_and_seq_anchor() -> None:
    anchor = TradeCursor("UNIUSDT", "trade-a", 100, NOW)
    rows = [
        {
            "symbol": "UNIUSDT",
            "execId": "trade-c",
            "seq": "102",
            "time": str(int((NOW + timedelta(seconds=2)).timestamp() * 1000)),
            "price": "10.2",
            "size": "2",
            "side": "Buy",
        },
        {
            "symbol": "UNIUSDT",
            "execId": "trade-b",
            "seq": "101",
            "time": str(int((NOW + timedelta(seconds=1)).timestamp() * 1000)),
            "price": "10.1",
            "size": "1",
            "side": "Sell",
        },
        {
            "symbol": "UNIUSDT",
            "execId": "trade-a",
            "seq": "100",
            "time": str(int(NOW.timestamp() * 1000)),
            "price": "10.0",
            "size": "1",
            "side": "Buy",
        },
    ]
    replay = build_exact_trade_replay(
        rows,
        anchor=anchor,
        cutoff_at=NOW + timedelta(seconds=3),
    )
    assert [row.exec_id for row in replay] == ["trade-b", "trade-c"]
    assert [row.seq for row in replay] == [101, 102]


def test_trade_replay_without_exact_anchor_is_fail_closed() -> None:
    anchor = TradeCursor("UNIUSDT", "missing", 100, NOW)
    with pytest.raises(ContinuityNotProvable, match="anchor"):
        build_exact_trade_replay(
            [
                {
                    "symbol": "UNIUSDT",
                    "execId": "trade-b",
                    "seq": "101",
                    "time": str(int((NOW + timedelta(seconds=1)).timestamp() * 1000)),
                    "price": "10",
                    "size": "1",
                    "side": "Buy",
                }
            ],
            anchor=anchor,
            cutoff_at=NOW + timedelta(seconds=2),
        )


def test_same_seq_trade_group_must_be_semantically_commutative() -> None:
    anchor = TradeCursor("UNIUSDT", "trade-a", 100, NOW)
    ambiguous = [
        {
            "symbol": "UNIUSDT",
            "execId": "x2",
            "seq": "101",
            "time": str(int((NOW + timedelta(seconds=1)).timestamp() * 1000)),
            "price": "10.2",
            "size": "1",
            "side": "Buy",
        },
        {
            "symbol": "UNIUSDT",
            "execId": "x1",
            "seq": "101",
            "time": str(int((NOW + timedelta(seconds=1)).timestamp() * 1000)),
            "price": "10.1",
            "size": "1",
            "side": "Buy",
        },
        {
            "symbol": "UNIUSDT",
            "execId": "trade-a",
            "seq": "100",
            "time": str(int(NOW.timestamp() * 1000)),
            "price": "10",
            "size": "1",
            "side": "Buy",
        },
    ]
    with pytest.raises(ContinuityNotProvable, match="same-seq"):
        build_exact_trade_replay(ambiguous, anchor=anchor, cutoff_at=NOW + timedelta(seconds=2))


def test_exact_boundary_generation_for_closed_candles_and_bar_open() -> None:
    assert expected_boundaries(
        NOW,
        NOW + timedelta(minutes=16),
        timeframe_minutes=5,
    ) == (
        NOW + timedelta(minutes=5),
        NOW + timedelta(minutes=10),
        NOW + timedelta(minutes=15),
    )
    assert expected_boundaries(
        NOW,
        NOW + timedelta(minutes=16),
        timeframe_minutes=15,
    ) == (NOW + timedelta(minutes=15),)


def test_exact_fact_deduper_rejects_only_same_source_identity() -> None:
    deduper = ExactFactDeduper()
    assert deduper.accept("trade-exact-id") is True
    assert deduper.accept("trade-exact-id") is False
    assert deduper.accept("trade-other-id") is True


def test_recoverable_disconnect_keeps_same_run_identity_and_started_at() -> None:
    proven = assess_oi30s_continuity(
        {"UNIUSDT": OiSampleCursor("UNIUSDT", NOW, 10)},
        required_symbols=("UNIUSDT",),
        subscription_ready_server_at=NOW + timedelta(seconds=5),
        sample_seconds=30,
    )
    action = resolve_continuity_action(proven)
    assert action is ContinuityAction.RESUME_SAME_RUN
    parity_run_id = "spr-same"
    started_at = NOW - timedelta(hours=3)
    assert parity_run_id == "spr-same"
    assert started_at == NOW - timedelta(hours=3)


def test_runtime_source_handles_ws_close_in_process_and_records_transport_evidence() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    assert "WebSocketConnectionClosedException" in source
    assert "TRANSPORT_DISCONNECT" in source
    assert "TRANSPORT_RECONNECT" in source
    assert "TRANSPORT_CONTINUITY" in source
    assert "build_exact_trade_replay" in source
    assert "assess_oi30s_continuity" in source
    assert "runtime.trade_commands" not in source
    assert "runtime.executions" not in source


def test_artificial_ws_close_reconnects_in_process_before_oi_deadline() -> None:
    from operations.monitoring import universal_entry_shadow as runtime

    calls = 0

    class FakeSocket:
        closed = False

        def close(self) -> None:
            self.closed = True

    socket = FakeSocket()

    def connect(_symbols: tuple[str, ...]):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise runtime.websocket.WebSocketConnectionClosedException("artificial close")
        return socket, NOW + timedelta(seconds=8), NOW + timedelta(seconds=8), ()

    recovered = runtime._reconnect_until_oi_safe(
        ("UNIUSDT",),
        {"UNIUSDT": OiSampleCursor("UNIUSDT", NOW, 1)},
        connect_fn=connect,
        server_time_fn=lambda: NOW + timedelta(seconds=4),
        sleep_fn=lambda _seconds: None,
    )
    assert calls == 2
    assert recovered[0] is socket
    assert recovered[2] == NOW + timedelta(seconds=8)


def test_unrecoverable_reconnect_crossing_oi_deadline_is_fail_closed() -> None:
    from operations.monitoring import universal_entry_shadow as runtime

    def connect(_symbols: tuple[str, ...]):
        raise runtime.websocket.WebSocketConnectionClosedException("still closed")

    with pytest.raises(ContinuityNotProvable, match="OI30S"):
        runtime._reconnect_until_oi_safe(
            ("UNIUSDT",),
            {"UNIUSDT": OiSampleCursor("UNIUSDT", NOW, 1)},
            connect_fn=connect,
            server_time_fn=lambda: NOW + timedelta(seconds=30),
            sleep_fn=lambda _seconds: None,
        )


def test_reconnect_helper_cannot_reset_run_identity_or_warmup_clock() -> None:
    from operations.monitoring import universal_entry_shadow as runtime

    parity_run_id = "spr-owner"
    started_at = NOW - timedelta(hours=6)

    class FakeSocket:
        def close(self) -> None:
            return None

    runtime._reconnect_until_oi_safe(
        ("UNIUSDT",),
        {"UNIUSDT": OiSampleCursor("UNIUSDT", NOW, 1)},
        connect_fn=lambda _symbols: (
            FakeSocket(),
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=1),
            (),
        ),
        server_time_fn=lambda: NOW,
        sleep_fn=lambda _seconds: None,
    )
    assert parity_run_id == "spr-owner"
    assert started_at == NOW - timedelta(hours=6)
    source = RUNTIME.read_text(encoding="utf-8")
    assert "same_parity_run_id" in source
    assert "same_started_at" in source


def test_duplicate_fact_is_rejected_before_comparator_or_runner_step() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    dedup_at = source.index("if not deduper.accept(fact.fact_id):")
    comparator_at = source.index("observation = comparator.process(fact)", dedup_at)
    runner_at = source.index("runners[fact.symbol].step(fact)", dedup_at)
    assert dedup_at < comparator_at
    assert dedup_at < runner_at


def test_disconnect_after_comparable_uses_same_transport_handler() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    comparable_branch = source.index("if current_state is ShadowComparability.PARITY_COMPARABLE:")
    transport_handler = source.index("except transport_errors as exc:", comparable_branch)
    reconnect_call = source.index(
        "_reconnect_until_oi_safe(symbols, oi_cursors)", transport_handler
    )
    assert comparable_branch < transport_handler < reconnect_call


def test_fact_source_identity_and_v1_semantics_are_not_changed_by_repair() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    assert '"BYBIT_PUBLIC_NORMALIZED_U5_V1"' in source
    assert "BYBIT_PUBLIC_NORMALIZED_U5_V1_OI30S" not in source or "FACT_SOURCE_ID" in source
    forbidden = ("entry_v2", "select_best_strategy", "activate_and_trade", "place_order")
    lowered = source.lower()
    for token in forbidden:
        assert token not in lowered


def test_exact_gap_recovery_crossing_5m_boundary_replays_closed_then_bar_open(monkeypatch) -> None:
    from decimal import Decimal

    from bybit_workbench.domain.models import Candle
    from operations.monitoring import universal_entry_shadow as runtime

    symbol = "UNIUSDT"
    ready_server = NOW + timedelta(minutes=5, seconds=10)
    ready_local = ready_server + timedelta(milliseconds=50)
    closed = Candle(
        symbol=symbol,
        timeframe="5",
        opened_at=NOW,
        closed_at=NOW + timedelta(minutes=5),
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10.5"),
        volume=Decimal("100"),
        is_closed=True,
    )
    current = Candle(
        symbol=symbol,
        timeframe="5",
        opened_at=NOW + timedelta(minutes=5),
        closed_at=NOW + timedelta(minutes=10),
        open=Decimal("10.5"),
        high=Decimal("10.6"),
        low=Decimal("10.4"),
        close=Decimal("10.55"),
        volume=Decimal("10"),
        is_closed=False,
    )

    anchor = TradeCursor(symbol, "anchor", 100, NOW)

    def fake_get_json(path: str, _params):
        assert path == "/v5/market/recent-trade"
        return {
            "result": {
                "list": [
                    {
                        "symbol": symbol,
                        "execId": "anchor",
                        "seq": "100",
                        "time": str(int(NOW.timestamp() * 1000)),
                        "price": "10",
                        "size": "1",
                        "side": "Buy",
                    }
                ]
            }
        }

    def fake_gap_candles(_symbol: str, timeframe: str, _ready: datetime):
        return (closed, current) if timeframe == "5" else ()

    monkeypatch.setattr(runtime, "_get_json", fake_get_json)
    monkeypatch.setattr(runtime, "_fetch_gap_candles", fake_gap_candles)
    items, counts = runtime._recover_public_gap(
        symbols=(symbol,),
        ready_local_at=ready_local,
        ready_server_at=ready_server,
        trade_cursors={symbol: anchor},
        oi_cursors={symbol: OiSampleCursor(symbol, ready_server - timedelta(seconds=5), 999)},
        closed_boundaries={(symbol, "5"): NOW, (symbol, "15"): NOW, (symbol, "60"): NOW},
        bar_open_boundaries={symbol: NOW},
    )
    assert counts == {
        "PUBLIC_TRADE": 0,
        "CANDLE_CLOSED": 1,
        "BAR_OPEN": 1,
        "OPEN_INTEREST": 0,
    }
    assert [fact.event_kind for fact, _cursor in items] == ["CANDLE_CLOSED", "BAR_OPEN"]
    assert items[0][0].event_at == items[1][0].event_at == NOW + timedelta(minutes=5)

    deduper = ExactFactDeduper()
    replay_bar = items[1][0]
    assert deduper.accept(replay_bar.fact_id) is True
    assert deduper.accept(replay_bar.fact_id) is False
