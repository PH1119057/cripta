"""#470: P90 scoped consumer's effects and progress are one transaction.

The coordinator is never an ARM or trading permission issuer. Only an
explicitly initialized, exact per-Strategy/Plan/source epoch can consume.
"""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.market_data.entry_publication_reader import (
    EntryPublicationPin,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.p90l90_scoped_native_dispatch import (
    P90ScopedConsumerBlocked,
    consume_pinned_entry_native_batch,
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
        self.checkpoint = (0, 0)
        self.clock_override = None
        self.events = []
        self.effects = []
        self.queries = []
        self.publication_native = {
            "LTCUSDT": {
                "instrument_id": "inst-bybit-linear-ltcusdt",
                "frame_number": 1, "frame_index": 1,
                "exchange_sequence": 10, "exec_id": "provider-second",
            },
            "INJUSDT": {
                "instrument_id": "inst-bybit-linear-injusdt",
                "frame_number": 0, "frame_index": 0,
                "exchange_sequence": 0, "exec_id": "provider-prev",
            },
        }
        self.publication_closed = {
            symbol: {"5": "2026-11-11T18:20:00+00:00",
                     "15": "2026-11-11T18:15:00+00:00"}
            for symbol in ("LTCUSDT", "INJUSDT")
        }
        self.publication = (
            SID, SOURCE_RELEASE, self.now-timedelta(seconds=1),
            "WATERMARKS_ONLY", self.publication_native, self.publication_closed,
        )

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
        if sql == "SELECT clock_timestamp()":
            return Rows((self.clock_override or self.now,))
        if "FROM analytics.market_publications" in sql:
            assert params == (14, BYBIT_PUBLIC_MARKET_SOURCE_ID)
            return Rows(self.publication)
        if "FROM strategy_entry.p90_scoped_native_consumer_checkpoints" in sql:
            return Rows(
                (*self.checkpoint, ENTRY_FP, EXIT_FP, SOURCE_RELEASE, CONSUMER_RELEASE)
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


def publication_pin(db: DB) -> EntryPublicationPin:
    return EntryPublicationPin(
        publication_id=14, source_session_id=SID,
        producer_release_commit=SOURCE_RELEASE,
        as_of_second=db.now-timedelta(seconds=1),
        native_watermarks=db.publication_native,
        closed_refs=db.publication_closed,
    )


def pinned_call(db: DB, callback, *, pin: EntryPublicationPin | None = None):
    return consume_pinned_entry_native_batch(
        db, publication_pin=pin or publication_pin(db),
        max_publication_age=timedelta(seconds=5),
        source=evidence(db.now),
        strategy_id=MAIN_ID, strategy_version=VERSION,
        entry_plan_fingerprint=ENTRY_FP, exit_plan_fingerprint=EXIT_FP,
        consumer_release_commit=CONSUMER_RELEASE,
        observed_at=db.now, max_evidence_age=timedelta(seconds=20),
        on_publication_trade=callback,
    )


def test_pinned_entry_callback_and_existing_checkpoint_commit_atomically():
    now = datetime.now(UTC)
    db = DB(now)
    rows = pinned_call(
        db,
        lambda event, pid: db.events.append("ENGINE:"+event.exec_id+":"+str(pid)),
    )
    assert [e.exec_id for e in rows] == ["provider-first", "provider-second"]
    assert db.events == [
        "BEGIN", "ENGINE:provider-first:14", "ENGINE:provider-second:14",
        "CHECKPOINT", "COMMIT",
    ]
    assert any("FROM analytics.market_publications" in q for q, _ in db.queries)
    assert not any("observer_delivery" in q for q, _ in db.queries)


def test_publication_bounding_discards_only_not_yet_published_suffix():
    now = datetime.now(UTC)
    db = DB(now)
    db.publication_native["LTCUSDT"]["frame_index"] = 0
    delivered = []
    rows = pinned_call(db, lambda event, pid: delivered.append((event.exec_id, pid)))
    assert delivered == [("provider-first", 14)]
    assert [e.exec_id for e in rows] == ["provider-first"]
    assert db.events == ["BEGIN", "CHECKPOINT", "COMMIT"]


def test_publication_mismatch_blocks_all_callbacks_and_rolls_back():
    now = datetime.now(UTC)
    db = DB(now)
    db.publication = ("f"*32, *db.publication[1:])
    with pytest.raises(P90ScopedConsumerBlocked, match="EPOCH_CHANGED"):
        pinned_call(db, lambda *_: db.events.append("ENGINE"))
    assert db.events == ["BEGIN", "ROLLBACK"]


def test_stale_publication_is_not_a_decision_ticket():
    now = datetime.now(UTC)
    db = DB(now)
    stale = EntryPublicationPin(
        publication_id=14, source_session_id=SID,
        producer_release_commit=SOURCE_RELEASE,
        as_of_second=now-timedelta(minutes=1),
        native_watermarks=db.publication_native,
        closed_refs=db.publication_closed,
    )
    with pytest.raises(P90ScopedConsumerBlocked, match="IDENTITY_OR_AGE"):
        pinned_call(db, lambda *_: db.events.append("ENGINE"), pin=stale)
    assert db.events == ["BEGIN", "ROLLBACK"]


def test_corrupt_original_publication_ref_never_runs_engine():
    now = datetime.now(UTC)
    db = DB(now)
    proof = publication_pin(db)
    # Mutable mock row differs from the earlier immutable pinned read.
    db.publication_native = dict(db.publication_native)
    db.publication_native["LTCUSDT"] = dict(db.publication_native["LTCUSDT"])
    db.publication_native["LTCUSDT"]["exec_id"] = "forged-trade"
    db.publication = (
        SID, SOURCE_RELEASE, db.now-timedelta(seconds=1),
        "WATERMARKS_ONLY", db.publication_native, db.publication_closed,
    )
    with pytest.raises(P90ScopedConsumerBlocked, match="REF_DRIFT"):
        pinned_call(db, lambda *_: db.events.append("ENGINE"), pin=proof)
    assert db.events == ["BEGIN", "ROLLBACK"]


def test_pinned_failed_engine_effect_never_advances_checkpoint():
    now = datetime.now(UTC)
    db = DB(now)

    def interrupted(event, publication_id):
        db.events.append("ENGINE:"+event.exec_id)
        if event.exec_id == "provider-second":
            raise RuntimeError("INJECTED_PINNED_ENTRY_ABORT")

    with pytest.raises(RuntimeError, match="INJECTED_PINNED_ENTRY_ABORT"):
        pinned_call(db, interrupted)
    assert db.events[-1] == "ROLLBACK"
    assert "CHECKPOINT" not in db.events


def test_pinned_path_never_initializes_missing_consumer_checkpoint():
    now = datetime.now(UTC)
    db = DB(now, initialized=False)
    with pytest.raises(P90ScopedConsumerBlocked, match="NOT_INITIALIZED"):
        pinned_call(db, lambda *_: db.events.append("ENGINE"))
    assert db.events == ["BEGIN", "ROLLBACK"]


def test_pinned_expired_watermark_does_not_dispatch_or_ack():
    now = datetime.now(UTC)
    db = DB(now)
    for native in db.publication_native.values():
        native["frame_number"] = 0
        native["frame_index"] = 0
    # Existing cursor at (0,0) is not beyond the pin: no new rows may run.
    assert pinned_call(db, lambda *_: db.events.append("ENGINE")) == ()
    assert db.events == ["BEGIN", "COMMIT"]


def test_pinned_checkpoint_cannot_run_ahead_of_publication():
    db = DB(datetime.now(UTC))
    db.checkpoint = (2, 0)
    with pytest.raises(
        P90ScopedConsumerBlocked, match="BEHIND_DECISION_CHECKPOINT",
    ):
        pinned_call(db, lambda *_: db.events.append("ENGINE"))
    assert db.events == ["BEGIN", "ROLLBACK"]


def test_publication_expired_while_waiting_for_checkpoint_lock_blocks_entry():
    db = DB(datetime.now(UTC))
    db.clock_override = db.now + timedelta(seconds=12)
    with pytest.raises(
        P90ScopedConsumerBlocked, match="STALE_BEFORE_DECISION",
    ):
        pinned_call(db, lambda *_: db.events.append("ENGINE"))
    assert db.events == ["BEGIN", "ROLLBACK"]
