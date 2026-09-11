from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.transport_continuity import (
    ContinuityNotProvable,
    PublicWsHeartbeat,
    TradeCursor,
    build_exact_trade_replay,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 11, 0, 0, tzinfo=UTC)


def _row(
    exec_id: str,
    seq: int,
    at: datetime,
    *,
    price: str = "10",
    size: str = "1",
    side: str = "Buy",
) -> dict[str, object]:
    return {
        "symbol": "UNIUSDT",
        "execId": exec_id,
        "seq": str(seq),
        "time": str(int(at.timestamp() * 1000)),
        "price": price,
        "size": size,
        "side": side,
    }


def test_contract_freezes_public_trade_watchdog_before_source() -> None:
    text = (ROOT / "docs/UNIVERSAL_STRATEGY_ENTRY_IMPLEMENTATION_RU.md").read_text(encoding="utf-8")
    assert "**Версия:** 1.6" in text
    assert "### 20.11 U5 PUBLIC_TRADE silent-stall watchdog / exact replay repair" in text
    assert "ping interval = 10 seconds" in text
    assert "pong deadline = 5 seconds" in text
    assert "Strategy/V1/EntryPlan/comparator semantics change" in text


def test_heartbeat_detects_half_open_even_if_send_ping_succeeded() -> None:
    heartbeat = PublicWsHeartbeat(
        ping_interval_seconds=10.0,
        pong_timeout_seconds=5.0,
        started_monotonic=100.0,
    )
    assert heartbeat.ping_due(109.999) is False
    assert heartbeat.ping_due(110.0) is True
    heartbeat.note_ping_sent(110.0)
    assert heartbeat.deadline_exceeded(114.999) is False
    assert heartbeat.deadline_exceeded(115.0) is True


def test_timely_pong_or_market_frame_proves_transport_activity() -> None:
    heartbeat = PublicWsHeartbeat(10.0, 5.0, started_monotonic=100.0)
    heartbeat.note_ping_sent(110.0)
    heartbeat.note_pong(110.33)
    assert heartbeat.deadline_exceeded(999.0) is False
    assert heartbeat.ping_due(120.329) is False
    assert heartbeat.ping_due(120.33) is True

    heartbeat.note_ping_sent(120.33)
    heartbeat.note_market_frame(120.5)
    assert heartbeat.deadline_exceeded(999.0) is False


def test_unique_timestamps_inside_same_seq_have_exact_chronological_order() -> None:
    anchor = TradeCursor("UNIUSDT", "anchor", 100, NOW)
    rows = [
        _row("late", 101, NOW + timedelta(seconds=2), price="10.2"),
        _row("early", 101, NOW + timedelta(seconds=1), price="10.1"),
        _row("anchor", 100, NOW),
    ]
    replay = build_exact_trade_replay(
        rows,
        anchor=anchor,
        cutoff_at=NOW + timedelta(seconds=3),
    )
    assert [item.exec_id for item in replay] == ["early", "late"]


def test_equal_timestamp_same_seq_with_different_trade_semantics_is_fail_closed() -> None:
    anchor = TradeCursor("UNIUSDT", "anchor", 100, NOW)
    tied = NOW + timedelta(seconds=1)
    rows = [
        _row("b", 101, tied, price="10.2", size="1", side="Buy"),
        _row("a", 101, tied, price="10.1", size="2", side="Sell"),
        _row("anchor", 100, NOW),
    ]
    with pytest.raises(ContinuityNotProvable, match="same timestamp"):
        build_exact_trade_replay(rows, anchor=anchor, cutoff_at=NOW + timedelta(seconds=2))


def test_equal_timestamp_identical_trade_semantics_can_use_stable_exec_id_order() -> None:
    anchor = TradeCursor("UNIUSDT", "anchor", 100, NOW)
    tied = NOW + timedelta(seconds=1)
    rows = [
        _row("b", 101, tied, price="10.1", size="2", side="Buy"),
        _row("a", 101, tied, price="10.1", size="2", side="Buy"),
        _row("anchor", 100, NOW),
    ]
    replay = build_exact_trade_replay(
        rows,
        anchor=anchor,
        cutoff_at=NOW + timedelta(seconds=2),
    )
    assert [item.exec_id for item in replay] == ["a", "b"]


def test_anchor_loss_remains_fail_closed() -> None:
    anchor = TradeCursor("UNIUSDT", "anchor", 100, NOW)
    with pytest.raises(ContinuityNotProvable, match="anchor not found"):
        build_exact_trade_replay(
            [_row("later", 101, NOW + timedelta(seconds=1))],
            anchor=anchor,
            cutoff_at=NOW + timedelta(seconds=2),
        )


def test_runtime_wires_application_heartbeat_not_only_send_ping() -> None:
    source = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")
    assert "PublicWsHeartbeat" in source
    assert "PING_INTERVAL_SECONDS = 10.0" in source
    assert "PONG_TIMEOUT_SECONDS = 5.0" in source
    assert "application heartbeat deadline exceeded" in source
    assert "note_pong" in source
    assert "note_market_frame" in source


def test_known_current_seq_exec_ids_are_excluded_before_ambiguity_check() -> None:
    anchor = TradeCursor("UNIUSDT", "anchor", 100, NOW)
    rows = [
        _row("already-seen", 100, NOW, price="99", size="9", side="Sell"),
        _row("anchor", 100, NOW),
    ]
    replay = build_exact_trade_replay(
        rows,
        anchor=anchor,
        cutoff_at=NOW + timedelta(seconds=1),
        known_exec_ids={"already-seen"},
    )
    assert replay == ()


def test_unknown_same_seq_trade_not_strictly_after_anchor_time_is_fail_closed() -> None:
    anchor = TradeCursor("UNIUSDT", "anchor", 100, NOW)
    rows = [
        _row("unknown-tied", 100, NOW, price="10.2"),
        _row("anchor", 100, NOW),
    ]
    with pytest.raises(ContinuityNotProvable, match="anchor sequence"):
        build_exact_trade_replay(
            rows,
            anchor=anchor,
            cutoff_at=NOW + timedelta(seconds=1),
        )


def test_topic_silence_audit_proves_no_gap_from_exact_anchor() -> None:
    from operations.monitoring import universal_entry_shadow as runtime

    anchor = TradeCursor("UNIUSDT", "anchor", 100, NOW)

    def fetch(_symbol: str):
        return ([_row("anchor", 100, NOW)], NOW + timedelta(seconds=1))

    result = runtime._audit_public_trade_silence(
        ("UNIUSDT",),
        {"UNIUSDT": anchor},
        {"UNIUSDT": {"anchor"}},
        fetch_fn=fetch,
    )
    assert result == {"UNIUSDT": 0}


def test_topic_silence_audit_detects_exact_missing_trade_and_forces_reconnect() -> None:
    from operations.monitoring import universal_entry_shadow as runtime

    anchor = TradeCursor("UNIUSDT", "anchor", 100, NOW)

    def fetch(_symbol: str):
        return (
            [
                _row("missing", 101, NOW + timedelta(seconds=1)),
                _row("anchor", 100, NOW),
            ],
            NOW + timedelta(seconds=2),
        )

    with pytest.raises(runtime.PublicTradeStreamBehind, match="missing exact trade"):
        runtime._audit_public_trade_silence(
            ("UNIUSDT",),
            {"UNIUSDT": anchor},
            {"UNIUSDT": {"anchor"}},
            fetch_fn=fetch,
        )


def test_topic_silence_audit_missing_anchor_is_not_provable() -> None:
    from operations.monitoring import universal_entry_shadow as runtime

    anchor = TradeCursor("UNIUSDT", "anchor", 100, NOW)

    def fetch(_symbol: str):
        return ([_row("new", 101, NOW + timedelta(seconds=1))], NOW + timedelta(seconds=2))

    with pytest.raises(ContinuityNotProvable, match="anchor not found"):
        runtime._audit_public_trade_silence(
            ("UNIUSDT",),
            {"UNIUSDT": anchor},
            {"UNIUSDT": {"anchor"}},
            fetch_fn=fetch,
        )


def test_runtime_declares_bounded_per_symbol_trade_silence_audit() -> None:
    source = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")
    assert "TRADE_SILENCE_AUDIT_SECONDS = 10.0" in source
    assert "TRADE_AUDIT_REQUEST_TIMEOUT_SECONDS = 4.0" in source
    assert "_audit_public_trade_silence" in source
    assert "/v5/market/recent-trade" in source


def test_silence_audit_same_seq_tied_unknown_forces_recovery_not_early_failure() -> None:
    from operations.monitoring import universal_entry_shadow as runtime

    anchor = TradeCursor("UNIUSDT", "anchor", 100, NOW)

    def fetch(_symbol: str):
        return (
            [
                _row("unknown-tied", 100, NOW, price="10.2", size="2", side="Sell"),
                _row("anchor", 100, NOW),
            ],
            NOW + timedelta(seconds=1),
        )

    with pytest.raises(runtime.PublicTradeStreamBehind, match="missing exact trade"):
        runtime._audit_public_trade_silence(
            ("UNIUSDT",),
            {"UNIUSDT": anchor},
            {"UNIUSDT": {"anchor"}},
            fetch_fn=fetch,
        )
