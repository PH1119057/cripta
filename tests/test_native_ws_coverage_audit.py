"""Independent P90 native source audit: good identity != proved full WS coverage."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.market_data.native_ws_coverage_audit import (
    NativeWsCoverageUnverified,
    audit_native_ws_session_interval,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

AT = datetime(2026, 11, 9, 4, 7, 30, tzinfo=UTC)
SOURCE = BYBIT_PUBLIC_MARKET_SOURCE_ID
SYMBOL = "ARBUSDT"
INSTRUMENT = bybit_instrument_id("linear", SYMBOL)
SESSION = "a" * 32
PRODUCER = "b" * 40
OLD = "bybit-old"
PEER = "bybit-old-peer"
NEW = "bybit-new"


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


def _row(
    frame: int, seq: int, exec_id: str, when: datetime, *,
    price: Decimal = Decimal("0.1736"),
):
    return (
        frame, 0, SOURCE, INSTRUMENT, SYMBOL,
        seq, exec_id, when, when + timedelta(milliseconds=80),
        when, price, Decimal("57.6"), "Buy",
        "BYBIT_WS_PUBLIC_TRADE", PRODUCER,
    )


class SourceDB:
    def __init__(self):
        self.session = (
            SOURCE, PRODUCER, "ACTIVE", AT - timedelta(minutes=3),
            AT + timedelta(seconds=3), None, None,
        )
        self.rows = [
            _row(41, 100, PEER, AT - timedelta(milliseconds=1)),
            _row(42, 100, OLD, AT),
            _row(43, 101, "middle", AT + timedelta(seconds=1)),
            _row(44, 102, NEW, AT + timedelta(seconds=2)),
        ]
        self.sql_calls: list[str] = []

    def execute(self, sql, params):
        self.sql_calls.append(sql)
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            assert params == (SESSION,)
            return Cursor([self.session])
        if "SELECT frame_number,frame_index,exchange_sequence,exec_id,event_at" in sql:
            _sid, _source, _instrument, _sym, seq, requested = params
            assert (_sid, _source, _instrument, _sym) == (
                SESSION, SOURCE, INSTRUMENT, SYMBOL,
            )
            return Cursor([
                (r[0], r[1], r[5], r[6], r[7])
                for r in self.rows if r[5] == seq and r[6] in requested
            ])
        if "JOIN analytics.native_public_trade_events n" in sql:
            sid, sym, from_frame, from_index, to_frame, to_index, limit = params
            assert sid == SESSION and sym == SYMBOL
            return Cursor([
                r for r in self.rows
                if (from_frame, from_index) < (r[0], r[1])
                <= (to_frame, to_index)
            ][:limit])
        raise AssertionError("unapproved SQL in MarketSource audit")


def _audit(db: SourceDB, **changes):
    kwargs = {
        "session_id": SESSION,
        "producer_release_commit": PRODUCER,
        "symbol": SYMBOL,
        "durable_seq": 100,
        "durable_exec_ids": (PEER, OLD),
        "durable_exec_id": OLD,
        "durable_at": AT,
        "live_seq": 102,
        "live_exec_id": NEW,
        "live_at": AT + timedelta(seconds=2),
        "checked_at": AT + timedelta(seconds=3),
        "max_required_records": 10,
        "max_lag": timedelta(minutes=2),
    }
    kwargs.update(changes)
    return audit_native_ws_session_interval(db, **kwargs)


def test_exact_native_source_rows_are_audited_but_never_self_certify_coverage():
    db = SourceDB()
    result = _audit(db)
    assert result.native_ids_verified is True
    assert result.atomic_session_verified is True
    assert result.native_continuity_verified is False
    assert result.reason == "NATIVE_COVERAGE_PRODUCER_FRAME_RECEIPT_WITNESS_NOT_STORED"
    assert result.durable_same_seq_ids == {OLD, PEER}
    assert result.record_count == 4
    assert result.oldest_frame == (41, 0)
    assert result.latest_frame == (44, 0)
    assert len(result.digest_sha256) == 64
    assert all("DELETE" not in sql and "UPDATE" not in sql for sql in db.sql_calls)


def test_identical_trade_data_digest_and_scope_are_reproducible():
    result1 = _audit(SourceDB())
    result2 = _audit(SourceDB())
    assert result1.digest_sha256 == result2.digest_sha256
    assert result1.record_count == result2.record_count
    assert not result2.native_continuity_verified


@pytest.mark.parametrize("condition", [
    "gap", "late-subscribe", "wrong-producer", "stale-session",
    "wrong-market", "ended", "missing-peer", "missing-new",
    "bad-journal-price", "journal-timestamp-drift", "unapproved-transport",
    "sequence-regression", "time-regression", "resource-exceeded",
])
def test_missing_source_or_immutable_identity_fails_closed(condition):
    db = SourceDB()
    if condition == "gap":
        db.session = (*db.session[:2], "GAP", *db.session[3:])
    elif condition == "late-subscribe":
        db.session = (*db.session[:3], AT + timedelta(seconds=1), *db.session[4:])
    elif condition == "wrong-producer":
        db.session = (db.session[0], "c" * 40, *db.session[2:])
    elif condition == "stale-session":
        db.session = (*db.session[:4], AT - timedelta(seconds=5), *db.session[5:])
    elif condition == "wrong-market":
        db.session = ("wrong-market", *db.session[1:])
    elif condition == "ended":
        db.session = (*db.session[:5], AT, "NETWORK_GAP")
    elif condition == "missing-peer":
        db.rows.pop(0)
    elif condition == "missing-new":
        db.rows.pop()
    elif condition == "bad-journal-price":
        row = list(db.rows[2])
        row[10] = Decimal("NaN")
        db.rows[2] = tuple(row)
    elif condition == "journal-timestamp-drift":
        row = list(db.rows[2])
        row[9] = AT + timedelta(minutes=1)
        db.rows[2] = tuple(row)
    elif condition == "unapproved-transport":
        row = list(db.rows[2])
        row[13] = "BYBIT_REST_CANDLE"
        db.rows[2] = tuple(row)
    elif condition == "sequence-regression":
        row = list(db.rows[2])
        row[5] = 99
        db.rows[2] = tuple(row)
    elif condition == "time-regression":
        row = list(db.rows[2])
        row[7] = AT - timedelta(seconds=2)
        row[9] = row[7]
        db.rows[2] = tuple(row)
    with pytest.raises(NativeWsCoverageUnverified):
        _audit(
            db, **({"max_required_records": 2}
                   if condition == "resource-exceeded" else {}),
        )


@pytest.mark.parametrize("invalid", [
    {"session_id": "not-a-session"},
    {"producer_release_commit": "bad-commit"},
    {"durable_seq": -1},
    {"durable_exec_ids": (OLD, OLD)},
    {"durable_exec_id": "wrong-old"},
    {"max_required_records": 0},
    {"max_required_records": 300_000},
    {"live_at": AT - timedelta(seconds=1)},
    {"durable_at": AT.replace(tzinfo=None)},
    {"checked_at": AT.replace(tzinfo=None)},
    {"max_lag": timedelta(minutes=10)},
])
def test_invalid_required_data_manifest_rejected_before_source_reads(invalid):
    db = SourceDB()
    with pytest.raises(NativeWsCoverageUnverified):
        _audit(db, **invalid)
    assert db.sql_calls == []


def test_pg_frame_number_jumps_do_not_become_fabricated_complete_ws_proof():
    db = SourceDB()
    row = list(db.rows[2])
    row[0] = 120  # producer did not store per-frame message receipts
    db.rows[2] = tuple(row)
    row = list(db.rows[3])
    row[0] = 121
    db.rows[3] = tuple(row)
    result = _audit(db)
    assert result.oldest_frame == (41, 0)
    assert result.latest_frame == (121, 0)
    assert result.native_continuity_verified is False


def test_type_audit_has_no_restart_or_exchange_mutation_port():
    result = _audit(SourceDB())
    assert not hasattr(result, "complete_coverage_verified")
    assert not hasattr(result, "exchange_action")
    assert result.reason.endswith("WITNESS_NOT_STORED")
