"""Negative and positive proof of bounded REAL publicTrade restart window."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.universal_entry.p90l90_real_bounded_native_restart import (
    P90RealNativeRestartUnverified,
    P90VerifiedNativeSourceWindow,
    _source_rows_digest,
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


def _long_native_source_window() -> P90VerifiedNativeSourceWindow:
    # 1200 exact native executions occur inside one seq. Their IDs, order
    # and timestamps are authentic-format SOURCE evidence (synthetic test),
    # not guessed from OHLC/CSV or proof of production source continuity.
    rows = [_row(NEW)]
    for index in range(1200, 0, -1):
        rows.append(_row(TradeCursor(
            symbol=OLD.symbol,
            exec_id=f"original-native-intermediate-{index}",
            seq=102,
            traded_at=OLD.traded_at + timedelta(milliseconds=20 * index),
        )))
    rows.append(_row(OLD))
    native = tuple(rows)
    return P90VerifiedNativeSourceWindow(
        symbol="LTCUSDT",
        instrument_id=bybit_instrument_id("linear", "LTCUSDT"),
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        coverage_start=OLD.traded_at,
        coverage_end=NEW.traded_at,
        event_count=len(native),
        max_required_records=len(native),
        source_sha256=_source_rows_digest(native),
        source_reference="approved-original-native-source-test-470",
        coverage_witness="APPROVED_FULL_NATIVE_SOURCE_ARCHIVE_V1",
        complete_coverage_verified=True,
        rows=native,
    )


def test_complete_vetted_native_source_is_not_limited_to_recent_rest_1000() -> None:
    case = _case()
    case["latest_rest_rows"] = [_row(NEW)]  # old cursor evicted by REST
    with pytest.raises(P90RealNativeRestartUnverified):
        resolve_durable_restart_anchor(
            symbol=OLD.symbol,
            last_sequence=OLD.seq,
            last_event_at=OLD.traded_at,
            last_exec_ids=(OLD.exec_id,),
            latest_rest_rows=case["latest_rest_rows"],
        )
    proof = _long_native_source_window()
    durable = resolve_durable_restart_anchor(
        symbol=OLD.symbol,
        last_sequence=OLD.seq,
        last_event_at=OLD.traded_at,
        last_exec_ids=(OLD.exec_id,),
        latest_rest_rows=case["latest_rest_rows"],
        verified_native_source=proof,
        live_anchor=NEW,
    )
    result = attest_bounded_native_restart(
        **{**case, "durable_anchor": durable, "verified_native_source": proof},
    )
    assert len(result.trades) == 1201
    assert result.trades[0].exec_id == "original-native-intermediate-1"
    assert result.trades[-1].exec_id == NEW.exec_id


def test_unproved_archives_never_bypass_original_native_requirement() -> None:
    case = _case()
    case["latest_rest_rows"] = [_row(NEW)]
    proof = _long_native_source_window()
    invalids = [
        replace(proof, complete_coverage_verified=False),
        replace(proof, max_required_records=len(proof.rows) - 1),
        replace(proof, source_sha256="0" * 64),
        replace(proof, coverage_start=OLD.traded_at + timedelta(milliseconds=1)),
        replace(proof, coverage_end=NEW.traded_at - timedelta(milliseconds=1)),
        replace(proof, source_reference=""),
        replace(proof, market_source_id="unknown-source"),
        replace(proof, coverage_witness="CSV_GUESSED_NATIVE_SEQ"),
        replace(proof, rows=tuple(
            {**row, "trdMatchID": row["execId"], "seq": None}
            for row in proof.rows
        ), source_sha256=_source_rows_digest(tuple(
            {**row, "trdMatchID": row["execId"], "seq": None}
            for row in proof.rows
        ))),
    ]
    for invalid in invalids:
        with pytest.raises(P90RealNativeRestartUnverified):
            attest_bounded_native_restart(
                **{**case, "verified_native_source": invalid},
            )
    with pytest.raises(P90RealNativeRestartUnverified):
        resolve_durable_restart_anchor(
            symbol=OLD.symbol,
            last_sequence=OLD.seq,
            last_event_at=OLD.traded_at,
            last_exec_ids=(OLD.exec_id,),
            latest_rest_rows=case["latest_rest_rows"],
            verified_native_source=proof,
            # Without original live WS cursor the archive cannot be accepted.
        )


def test_21_hour_outage_can_use_complete_original_native_source_not_rest_page() -> None:
    old = TradeCursor(
        symbol="LTCUSDT", exec_id="bybit-21h-old-101",
        seq=101, traded_at=AT - timedelta(hours=21),
    )
    new = TradeCursor(
        symbol="LTCUSDT", exec_id="bybit-21h-new-1302",
        seq=1302, traded_at=AT + timedelta(seconds=2),
    )
    mirror = PublicTradeMirrorBuffer(("LTCUSDT",), retention_seconds=120)
    mirror.start_epoch(AT + timedelta(seconds=1))
    mirror.record_message({
        "topic": "publicTrade.LTCUSDT",
        "data": [{
            "s": new.symbol, "i": new.exec_id, "seq": new.seq,
            "T": int(new.traded_at.timestamp() * 1000),
            "S": "Buy", "p": "100.2", "v": "0.1",
        }],
    }, received_at=AT + timedelta(seconds=2, milliseconds=100))
    native = tuple([
        _row(new),
        *(
            _row(TradeCursor(
                symbol="LTCUSDT", exec_id=f"bybit-historical-{i}",
                seq=101 + i,
                traded_at=old.traded_at + timedelta(seconds=63 * i),
            ))
            for i in range(1200, 0, -1)
        ),
        _row(old),
    ])
    proof = P90VerifiedNativeSourceWindow(
        symbol="LTCUSDT",
        instrument_id=bybit_instrument_id("linear", "LTCUSDT"),
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        coverage_start=old.traded_at,
        coverage_end=new.traded_at,
        event_count=len(native),
        max_required_records=len(native),
        source_sha256=_source_rows_digest(native),
        source_reference="approved-native-source-21h-gap-test",
        coverage_witness="VERIFIED_ATOMIC_PUBLIC_WS_SESSION_CHAIN_V1",
        complete_coverage_verified=True,
        rows=native,
    )
    only_recent = [_row(new)]
    durable = resolve_durable_restart_anchor(
        symbol="LTCUSDT", last_sequence=old.seq,
        last_event_at=old.traded_at, last_exec_ids=(old.exec_id,),
        latest_rest_rows=only_recent,
        verified_native_source=proof, live_anchor=new,
    )
    result = attest_bounded_native_restart(
        source_commit=SHA, source_epoch_id=EPOCH,
        durable_anchor=durable, live_anchor=new, mirror=mirror,
        latest_rest_rows=only_recent,
        rest_server_at=AT + timedelta(seconds=3),
        checked_at=AT + timedelta(seconds=3, milliseconds=100),
        durable_exec_ids=(old.exec_id,),
        verified_native_source=proof,
    )
    assert len(result.trades) == 1201
    assert result.trades[-1].exec_id == new.exec_id
    # The same outdated REST page must NEVER become historical coverage.
    with pytest.raises(P90RealNativeRestartUnverified):
        attest_bounded_native_restart(
            source_commit=SHA, source_epoch_id=EPOCH,
            durable_anchor=durable, live_anchor=new, mirror=mirror,
            latest_rest_rows=only_recent,
            rest_server_at=AT + timedelta(seconds=3),
            checked_at=AT + timedelta(seconds=3, milliseconds=100),
            durable_exec_ids=(old.exec_id,),
        )
