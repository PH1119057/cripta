"""Negative and positive proof of bounded REAL publicTrade restart window."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.universal_entry.p90l90_real_bounded_native_restart import (
    P90RealNativeRestartUnverified,
    attest_bounded_native_restart,
    resolve_durable_restart_anchor,
)
from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
OLD = TradeCursor(
    symbol="LTCUSDT", exec_id="bybit-old-101",
    seq=101, traded_at=AT - timedelta(seconds=30),
)
NEW = TradeCursor(
    symbol="LTCUSDT", exec_id="bybit-new-ws-103",
    seq=103, traded_at=AT + timedelta(seconds=2),
)
SHA = "a" * 40
EPOCH = "p90-real-epoch-" + "b" * 40


def _row(cursor: TradeCursor, *, price: str = "100.2") -> dict[str, object]:
    return {
        "execId": cursor.exec_id,
        "symbol": cursor.symbol,
        "seq": str(cursor.seq),
        "time": str(int(cursor.traded_at.timestamp() * 1000)),
        "side": "Buy",
        "price": price,
        "size": "0.1",
    }


def _case():
    mirror = PublicTradeMirrorBuffer(("LTCUSDT",), retention_seconds=120)
    mirror.start_epoch(AT + timedelta(seconds=1))
    mirror.record_message({
        "topic": "publicTrade.LTCUSDT",
        "data": [{
            "s": NEW.symbol,
            "i": NEW.exec_id,
            "seq": NEW.seq,
            "T": int(NEW.traded_at.timestamp() * 1000),
            "S": "Buy", "p": "100.2", "v": "0.1",
        }],
    }, received_at=AT + timedelta(seconds=2, milliseconds=100))
    intermediate = TradeCursor(
        symbol="LTCUSDT", exec_id="bybit-gap-102",
        seq=102, traded_at=AT + timedelta(seconds=1),
    )
    return {
        "source_commit": SHA,
        "source_epoch_id": EPOCH,
        "durable_anchor": OLD,
        "live_anchor": NEW,
        "mirror": mirror,
        "latest_rest_rows": [_row(NEW), _row(intermediate), _row(OLD)],
        "rest_server_at": AT + timedelta(seconds=3),
        "checked_at": AT + timedelta(seconds=3, milliseconds=100),
    }


def test_bounded_restart_requires_and_returns_both_original_execution_anchors():
    case = _case()
    result = attest_bounded_native_restart(**case)
    assert result.source_epoch_id == EPOCH
    assert [x.exec_id for x in result.trades] == [
        "bybit-gap-102", NEW.exec_id,
    ]
    assert result.durable_anchor == OLD
    assert result.live_anchor == NEW


@pytest.mark.parametrize("failure", (
    "old-evicted", "new-not-rest", "invalid-rest-order", "duplicate-rest",
    "foreign-symbol", "invalid-price", "no-live-anchor", "mirror-gap",
    "stale-server", "source-release-drift", "wrong-epoch", "overflow-rest",
))
def test_missing_rest_or_original_ws_evidence_must_fail_closed(failure: str):
    case = _case()
    rows = case["latest_rest_rows"]
    if failure == "old-evicted":
        rows.pop()
    elif failure == "new-not-rest":
        rows.pop(0)
    elif failure == "invalid-rest-order":
        rows.reverse()
    elif failure == "duplicate-rest":
        rows.insert(1, dict(rows[0]))
    elif failure == "foreign-symbol":
        rows[1]["symbol"] = "BTCUSDT"
    elif failure == "invalid-price":
        rows[1]["price"] = "NaN"
    elif failure == "no-live-anchor":
        case["live_anchor"] = TradeCursor(
            symbol="LTCUSDT", seq=103, exec_id="not-present-in-ws",
            traded_at=NEW.traded_at,
        )
    elif failure == "mirror-gap":
        case["mirror"].mark_gap("network restart")
    elif failure == "stale-server":
        case["checked_at"] = case["checked_at"] + timedelta(seconds=20)
    elif failure == "source-release-drift":
        case["source_commit"] = "wrong-code-version"
    elif failure == "wrong-epoch":
        case["source_epoch_id"] = "unverified"
    elif failure == "overflow-rest":
        case["latest_rest_rows"] = rows * 350
    with pytest.raises(P90RealNativeRestartUnverified):
        attest_bounded_native_restart(**case)


def test_same_durable_and_live_anchor_has_empty_but_verified_suffix():
    case = _case()
    case["durable_anchor"] = NEW
    case["latest_rest_rows"] = [_row(NEW)]
    result = attest_bounded_native_restart(**case)
    assert result.trades == ()



def test_durable_same_sequence_exec_ids_are_all_preserved_across_restart():
    peer = TradeCursor(
        symbol=OLD.symbol, exec_id="bybit-old-peer-101",
        seq=OLD.seq, traded_at=OLD.traded_at - timedelta(milliseconds=1),
    )
    rows = [_row(NEW), _row(TradeCursor(
        symbol=OLD.symbol, exec_id="gap-102", seq=102,
        traded_at=AT + timedelta(seconds=1),
    )), _row(OLD), _row(peer)]
    anchor = resolve_durable_restart_anchor(
        symbol=OLD.symbol,
        last_sequence=OLD.seq,
        last_event_at=OLD.traded_at,
        last_exec_ids=(peer.exec_id, OLD.exec_id),
        latest_rest_rows=rows,
    )
    assert anchor == OLD
    case = _case()
    case["durable_anchor"] = anchor
    case["latest_rest_rows"] = rows
    case["durable_exec_ids"] = (peer.exec_id, OLD.exec_id)
    result = attest_bounded_native_restart(**case)
    assert [item.exec_id for item in result.trades] == ["gap-102", NEW.exec_id]


def test_evicted_durable_same_sequence_peer_blocks_restart():
    rows = _case()["latest_rest_rows"]
    with pytest.raises(
        P90RealNativeRestartUnverified,
        match="SAME_SEQUENCE_IDS_EVICTED",
    ):
        resolve_durable_restart_anchor(
            symbol=OLD.symbol,
            last_sequence=OLD.seq,
            last_event_at=OLD.traded_at,
            last_exec_ids=(OLD.exec_id, "missing-old-peer"),
            latest_rest_rows=rows,
        )
