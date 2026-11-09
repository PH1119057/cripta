"""#470/1: native PG source -> technical Observer never loses a committed cursor.

Only synthetic native identities, inert PostgreSQL ports and read-only source
contracts are used. No Exchange, strategy permission or real account access.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.universal_entry.p90l90_scoped_native_codec import (
    original_native_fact,
)
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    NativeSourceEvidence,
    NativeSourceSymbolEvidence,
)
from operations.monitoring.native_public_trade_observer_delivery import (
    NativeObserverDeliveryBlocked,
    active_native_observer_source,
    consume_native_observer_batch,
)

SID = "1" * 32
RELEASE = "a" * 40
NOW = datetime(2026, 11, 9, 22, 0, tzinfo=UTC)
SYMS = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")


class Rows:
    def __init__(self, rows=None, *, rowcount=1):
        self.rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self.rows

    def fetchall(self):
        return self.rows


def proof():
    return NativeSourceEvidence(
        BYBIT_PUBLIC_MARKET_SOURCE_ID, SID, RELEASE, NOW,
        {s: NativeSourceSymbolEvidence(
            s, True, "NATIVE_SOURCE_COMMITTED_CURRENT",
        ) for s in SYMS},
    )


def native_rows():
    sym = "LTCUSDT"
    instrument = bybit_instrument_id("linear", sym)
    return [
        (7, 0, BYBIT_PUBLIC_MARKET_SOURCE_ID, instrument, sym,
         123, "provider-z", NOW, NOW, NOW,
         Decimal("91"), Decimal("1"), "Buy"),
        (7, 1, BYBIT_PUBLIC_MARKET_SOURCE_ID, instrument, sym,
         123, "provider-a", NOW, NOW, NOW,
         Decimal("92"), Decimal("2"), "Sell"),
    ]


class DB:
    def __init__(self):
        self.cursor = None
        self.active = "ACTIVE"
        self.producer = RELEASE
        self.trades = native_rows()
        self.operations = []
        self.rollback = False
        self.update_count = 1

    @contextmanager
    def transaction(self):
        old = self.cursor
        self.operations.append("BEGIN")
        try:
            yield
        except Exception:
            self.cursor = old
            self.rollback = True
            self.operations.append("ROLLBACK")
            raise
        else:
            self.operations.append("COMMIT")

    def execute(self, sql, params=()):
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            self.operations.append("SOURCE_LOCK")
            return Rows((self.active, self.producer, None, NOW))
        if "FROM analytics.native_public_trade_observer_delivery" in sql:
            self.operations.append("CHECKPOINT_READ")
            return Rows(self.cursor)
        if "INSERT INTO analytics.native_public_trade_observer_delivery" in sql:
            self.operations.append("CHECKPOINT_INIT")
            self.cursor = (0, 0, RELEASE)
            return Rows()
        if "FROM analytics.native_public_trade_source_events" in sql:
            self.operations.append("SOURCE_ROWS")
            return Rows(self.trades)
        if "UPDATE analytics.native_public_trade_observer_delivery" in sql:
            self.operations.append("CHECKPOINT_ACK")
            if self.update_count:
                self.cursor = (params[0], params[1], RELEASE)
            return Rows(rowcount=self.update_count)
        raise AssertionError(sql)


def test_technical_observer_receives_exact_original_pg_order_and_commits_ack():
    db = DB()
    received = []

    def observer_callback(event):
        fact, cursor = original_native_fact(event)
        assert fact.symbol == "LTCUSDT"
        assert fact.event_at == NOW
        received.append((cursor.seq, cursor.exec_id))

    result = consume_native_observer_batch(
        db, source=proof(), observed_at=NOW, on_trade=observer_callback,
    )
    assert received == [(123, "provider-z"), (123, "provider-a")]
    assert result.source_epoch_new is True
    assert result.source_session_id == SID
    assert db.cursor == (7, 1, RELEASE)
    assert db.operations == [
        "BEGIN", "SOURCE_LOCK", "CHECKPOINT_READ", "CHECKPOINT_INIT",
        "CHECKPOINT_READ", "SOURCE_ROWS", "CHECKPOINT_ACK", "COMMIT",
    ]


def test_observer_callback_crash_cannot_ack_original_native_trades():
    db = DB()
    attempted = []

    def observer_callback(event):
        attempted.append(event.exec_id)
        if len(attempted) == 2:
            raise RuntimeError("INJECTED_OBSERVER_FAULT")

    with pytest.raises(RuntimeError, match="INJECTED_OBSERVER_FAULT"):
        consume_native_observer_batch(
            db, source=proof(), observed_at=NOW, on_trade=observer_callback,
        )
    assert db.rollback
    assert db.cursor is None
    assert "CHECKPOINT_ACK" not in db.operations


@pytest.mark.parametrize("broken", [
    "GAP", "RECONNECTING",
])
def test_disconnected_native_producer_never_pretends_continuity(broken):
    db = DB()
    db.active = broken
    with pytest.raises(
        NativeObserverDeliveryBlocked,
        match="NATIVE_OBSERVER_DELIVERY_SOURCE_SESSION_MOVED",
    ):
        consume_native_observer_batch(
            db, source=proof(), observed_at=NOW, on_trade=lambda _: None,
        )
    assert db.cursor is None


def test_checkpoint_cannot_be_acknowledged_if_other_consumer_moved_it():
    db = DB()
    db.update_count = 0
    with pytest.raises(
        NativeObserverDeliveryBlocked,
        match="NATIVE_OBSERVER_DELIVERY_CONCURRENT_CURSOR_CHANGE",
    ):
        consume_native_observer_batch(
            db, source=proof(), observed_at=NOW, on_trade=lambda _: None,
        )
    assert db.cursor is None and db.rollback


def test_missing_original_journal_row_is_not_silently_skipped():
    db = DB()
    # Provider frame index exists, but canonical trade row is gone/corrupt.
    bad = list(db.trades[1])
    bad[9] = None
    bad[10] = None
    db.trades[1] = tuple(bad)
    with pytest.raises(
        NativeObserverDeliveryBlocked,
        match="NATIVE_OBSERVER_DELIVERY_ORIGINAL_ROW_MISSING",
    ):
        consume_native_observer_batch(
            db, source=proof(), observed_at=NOW, on_trade=lambda _: None,
        )
    assert db.cursor is None
    assert "CHECKPOINT_ACK" not in db.operations


def test_source_check_requires_exact_producer_abi_and_all_five_symbols(
    monkeypatch,
):
    from operations.monitoring import native_public_trade_observer_delivery as mod

    class SourceDB:
        def execute(self, sql, params=()):
            assert "producer_release_commit" in sql
            return Rows([(RELEASE,)])
    calls = []

    def verify(checkout, producer, consumer):
        assert checkout == Path("/source_checkout")
        assert producer == consumer == RELEASE
        calls.append("CODE_SHA_PROOF")
        return frozenset((RELEASE,))

    def source(_db, **kwargs):
        assert kwargs["symbols"] == SYMS
        assert kwargs["accepted_producer_commits"] == frozenset((RELEASE,))
        calls.append("SOURCE_5_SYMBOL_READ")
        return proof()

    monkeypatch.setattr(mod, "_verified_producer", verify)
    monkeypatch.setattr(mod, "read_scoped_native_source", source)
    result = active_native_observer_source(
        SourceDB(), symbols=SYMS,
        source_checkout=Path("/source_checkout"),
        loaded_consumer_commit=RELEASE,
        observed_at=NOW, max_lag=timedelta(seconds=30),
    )
    assert result.ready
    assert calls == ["CODE_SHA_PROOF", "SOURCE_5_SYMBOL_READ"]


def test_source_delivery_is_never_strategy_or_financial_gate():
    source = Path(
        "operations/monitoring/native_public_trade_observer_delivery.py"
    ).read_text(encoding="utf-8")
    observer = Path(
        "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    sql = Path(
        "operations/sql/20261110_native_public_trade_observer_delivery.sql"
    ).read_text(encoding="utf-8")
    installer = Path(
        "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert "source_repair_only" in observer
    assert "p90_repair_ingest_only" in observer
    assert "native_pg_delivery_only" in observer
    assert "original_native_fact(event)" in observer
    assert "consume_native_observer_batch(" in observer
    assert "elif topic.startswith(\"publicTrade.\"):" in observer
    assert "P90_REAL" not in source
    assert "OWNER_ARM" not in sql
    assert "GRANT SELECT,INSERT,UPDATE" in sql
    assert "REVOKE DELETE,TRUNCATE" in sql
    assert "20261110_native_public_trade_observer_delivery.sql" in installer
