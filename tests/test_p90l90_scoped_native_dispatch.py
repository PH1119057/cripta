"""#470: P90 scoped consumer's effects and progress are one transaction.

The coordinator is never an ARM or trading permission issuer. Only an
explicitly initialized, exact per-Strategy/Plan/source epoch can consume.
"""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.p90l90_scoped_native_dispatch import (
    P90ScopedConsumerBlocked,
    consume_scoped_native_batch,
)
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    NativeSourceEvidence,
    NativeSourceSymbolEvidence,
)

SOURCE_RELEASE = "a" * 40
CONSUMER_RELEASE = "b" * 40
ENTRY_FP = "c" * 64
EXIT_FP = "d" * 64
SID = "1" * 32


def evidence(now: datetime) -> NativeSourceEvidence:
    return NativeSourceEvidence(
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        session_id=SID,
        producer_release_commit=SOURCE_RELEASE,
        checked_at=now-timedelta(seconds=1),
        by_symbol={
            s: NativeSourceSymbolEvidence(s, True, "NATIVE_SOURCE_COMMITTED_CURRENT")
            for s in ("LTCUSDT", "INJUSDT")
        },
    )


class Rows:
    def __init__(self, row=None, *, rowcount=1):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [] if self.row is None else self.row


class DB:
    def __init__(self, now: datetime, *, initialized: bool = True):
        self.now = now
        self.initialized = initialized
        self.events = []
        self.effects = []
        self.queries = []

    @contextmanager
    def transaction(self):
        self.events.append("BEGIN")
        try:
            yield
        except Exception:
            self.events.append("ROLLBACK")
            raise
        else:
            self.events.append("COMMIT")

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            return Rows(("ACTIVE", SOURCE_RELEASE, None, self.now))
        if "FROM strategy_entry.p90_scoped_native_consumer_checkpoints" in sql:
            return Rows(
                (0, 0, ENTRY_FP, EXIT_FP, SOURCE_RELEASE, CONSUMER_RELEASE)
                if self.initialized else None
            )
        if "FROM analytics.native_public_trade_source_events" in sql:
            return Rows([
                (1, 0, BYBIT_PUBLIC_MARKET_SOURCE_ID,
                 "inst-bybit-linear-ltcusdt", "LTCUSDT", 10,
                 "provider-first", self.now, self.now, self.now,
                 Decimal("52.1"), Decimal("0.1"), "Buy"),
                (1, 1, BYBIT_PUBLIC_MARKET_SOURCE_ID,
                 "inst-bybit-linear-ltcusdt", "LTCUSDT", 10,
                 "provider-second", self.now, self.now, self.now,
                 Decimal("52.2"), Decimal("0.2"), "Sell"),
            ])
        if "UPDATE strategy_entry.p90_scoped_native_consumer_checkpoints" in sql:
            self.events.append("CHECKPOINT")
            return Rows(rowcount=1)
        raise AssertionError(sql)


def call(db: DB, proof: NativeSourceEvidence, callback):
    return consume_scoped_native_batch(
        db, source=proof, strategy_id=MAIN_ID, strategy_version=VERSION,
        entry_plan_fingerprint=ENTRY_FP, exit_plan_fingerprint=EXIT_FP,
        consumer_release_commit=CONSUMER_RELEASE, observed_at=db.now,
        max_evidence_age=timedelta(seconds=20), on_native_trade=callback,
    )


def test_verified_native_events_then_checkpoint_commit_in_one_transaction():
    now = datetime.now(UTC)
    db = DB(now)
    result = call(db, evidence(now), lambda e: db.events.append("ENGINE:"+e.exec_id))
    assert [x.exec_id for x in result] == ["provider-first", "provider-second"]
    assert db.events == [
        "BEGIN", "ENGINE:provider-first", "ENGINE:provider-second",
        "CHECKPOINT", "COMMIT",
    ]
    assert not any("observer" in sql.lower() for sql, _ in db.queries)


def test_missing_checkpoint_cannot_start_from_unrelated_event_offset():
    now = datetime.now(UTC)
    db = DB(now, initialized=False)
    with pytest.raises(P90ScopedConsumerBlocked, match="NOT_INITIALIZED"):
        call(db, evidence(now), lambda e: db.events.append(e.exec_id))
    assert db.events == ["BEGIN", "ROLLBACK"]


def test_failed_engine_effect_aborts_without_checkpoint_ack():
    now = datetime.now(UTC)
    db = DB(now)
    def fail_on_second(event):
        db.events.append("ENGINE:"+event.exec_id)
        if event.exec_id == "provider-second":
            raise RuntimeError("INJECTED_ENGINE_ABORT")
    with pytest.raises(RuntimeError, match="INJECTED_ENGINE_ABORT"):
        call(db, evidence(now), fail_on_second)
    assert db.events[-1] == "ROLLBACK"
    assert "CHECKPOINT" not in db.events


def test_stale_source_and_wrong_plan_cannot_dispatch():
    now = datetime.now(UTC)
    db = DB(now)
    old = evidence(now-timedelta(minutes=10))
    with pytest.raises(P90ScopedConsumerBlocked, match="STALE"):
        call(db, old, lambda _: None)
    assert db.events == []
    with pytest.raises(P90ScopedConsumerBlocked, match="INPUT_INVALID"):
        consume_scoped_native_batch(
            db, source=evidence(now), strategy_id=MAIN_ID,
            strategy_version=VERSION, entry_plan_fingerprint="f"*64,
            exit_plan_fingerprint=EXIT_FP,
            consumer_release_commit=CONSUMER_RELEASE,
            observed_at=now, max_evidence_age=timedelta(seconds=0),
            on_native_trade=lambda _: None,
        )
    assert db.events == []
