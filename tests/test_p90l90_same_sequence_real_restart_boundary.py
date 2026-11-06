"""Synthetic REAL restart proof for multi-frame publicTrade with one seq.

No access to a user account, exchange, live data, production PostgreSQL or
filesystem. Bybit recent-trade returns at most 1000 rows; >512 unique IDs are
legitimate when the complete immutable REST slice can prove every one.
"""
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

AT = datetime(2026, 10, 29, 12, tzinfo=UTC)
SYMBOL = "LTCUSDT"  # Exact pre-existing REAL scope; all events are synthetic.
EPOCH = "p90-real-epoch-" + "b" * 40
SOURCE = "a" * 40


def _peers(count: int) -> tuple[tuple[str, ...], list[dict[str, object]]]:
    names = tuple(f"synthetic-peer-{i:04d}" for i in range(count))
    at_ms = str(int((AT - timedelta(seconds=30)).timestamp() * 1000))
    rows: list[dict[str, object]] = [
        {
            "execId": name, "symbol": SYMBOL, "seq": "17",
            "time": at_ms, "side": "Buy", "price": "1", "size": "1",
        } for name in names
    ]
    return names, rows


def test_600_proven_same_sequence_ids_do_not_fail_512_restart_limit() -> None:
    ids, rows = _peers(600)
    anchor = resolve_durable_restart_anchor(
        symbol=SYMBOL, last_sequence=17,
        last_event_at=AT - timedelta(seconds=30),
        last_exec_ids=ids, latest_rest_rows=rows,
    )
    assert anchor == TradeCursor(
        symbol=SYMBOL, exec_id=ids[0], seq=17,
        traded_at=AT - timedelta(seconds=30),
    )

    newer = TradeCursor(
        symbol=SYMBOL, exec_id="synthetic-new-seq",
        seq=18, traded_at=AT + timedelta(seconds=2),
    )
    mirror = PublicTradeMirrorBuffer((SYMBOL,), retention_seconds=120)
    mirror.start_epoch(AT + timedelta(seconds=1))
    mirror.record_message({
        "topic": "publicTrade." + SYMBOL,
        "data": [{
            "s": SYMBOL, "i": newer.exec_id, "seq": newer.seq,
            "T": int(newer.traded_at.timestamp() * 1000),
            "S": "Buy", "p": "1", "v": "1",
        }],
    }, received_at=AT + timedelta(seconds=2, milliseconds=100))
    latest = [{
        "execId": newer.exec_id, "symbol": SYMBOL, "seq": str(newer.seq),
        "time": str(int(newer.traded_at.timestamp() * 1000)),
        "side": "Buy", "price": "1", "size": "1",
    }, *rows]
    recovery = attest_bounded_native_restart(
        source_commit=SOURCE,
        source_epoch_id=EPOCH,
        durable_anchor=anchor,
        live_anchor=newer,
        mirror=mirror,
        latest_rest_rows=latest,
        rest_server_at=AT + timedelta(seconds=3),
        checked_at=AT + timedelta(seconds=3, milliseconds=100),
        durable_exec_ids=ids,
    )
    assert [item.exec_id for item in recovery.trades] == [newer.exec_id]


def test_over_1000_same_sequence_ids_never_falsely_pass_rest_recovery() -> None:
    ids, rows = _peers(1001)
    with pytest.raises(P90RealNativeRestartUnverified):
        resolve_durable_restart_anchor(
            symbol=SYMBOL, last_sequence=17,
            last_event_at=AT - timedelta(seconds=30),
            last_exec_ids=ids, latest_rest_rows=rows,
        )
