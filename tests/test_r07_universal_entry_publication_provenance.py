"""R07 universal consumer: original-native publication proof and account isolation."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.r07_publication_provenance import (
    R07PublicationProvenanceUnavailable,
    require_r07_dispatch_epoch_guard,
    require_r07_publication_provenance,
)
from operations.connectivity import universal_entry_consumer as consumer

AT = datetime(2026, 11, 12, 12, 0, 5, tzinfo=UTC)
EVENT = AT - timedelta(seconds=3)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Rows(self.rows)


def evidence(**changes):
    row = {
        "publication_id": 45,
        "source_session_id": "b" * 32,
        "frame_number": 100,
        "frame_index": 2,
        "native_exec_id": "native-100",
        "event_at": EVENT,
        "symbol": "LTCUSDT",
        "direction": "LONG",
        "market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
        "publication_session_id": "b" * 32,
        "producer_release_commit": "c" * 40,
        "published_at": EVENT + timedelta(seconds=1),
        "as_of_second": EVENT,
        "publication_status": "WATERMARKS_ONLY",
        "publication_native_watermarks": {
            "LTCUSDT": {
                "instrument_id": "inst-bybit-linear-ltcusdt",
                "frame_number": 100, "frame_index": 2,
                "exchange_sequence": 123456, "exec_id": "native-100",
            },
        },
        "source_session_state": "ACTIVE",
        "session_producer_release_commit": "c" * 40,
        "event_market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
        "instrument_id": "inst-bybit-linear-ltcusdt",
        "external_symbol": "LTCUSDT",
        "exec_id": "native-100",
        "original_exchange_sequence": 123456,
        "original_event_at": EVENT,
        "accepted_at": EVENT + timedelta(milliseconds=200),
        "checkpoint_frame_number": 101,
        "checkpoint_frame_index": 0,
        "last_publication_id": 46,
        "bootstrap_publication_id": 40,
        "consumer_release_commit": "d" * 40,
        "checkpoint_producer_release_commit": "c" * 40,
        "entry_plan_fingerprint": "e" * 64,
        "exit_plan_fingerprint": "f" * 64,
    }
    row.update(changes)
    return row


def request(account_id="ta-account", strategy_id=MAIN_ID, version=VERSION):
    return {
        "strategy_id": strategy_id,
        "strategy_version": version,
        "signal_id": "signal-100",
        "symbol": "LTCUSDT",
        "direction": "LONG",
        "trading_account_id": account_id,
        "requested_at": AT,
        "entry_plan_fingerprint": "e" * 64,
        "exit_plan_fingerprint": "f" * 64,
    }


def test_one_published_signal_is_proven_for_arbitrary_independent_account_scopes():
    # R07 uses the SAME code for MAIN and an arbitrary N of SUB identities.
    db = Query([evidence()])
    accounts = ("ta-main",) + tuple(f"ta-sub-{index}" for index in range(1, 11))
    assert [require_r07_publication_provenance(db, request(account_id=a))
            for a in accounts] == [45] * len(accounts)
    assert len(db.calls) == len(accounts)
    assert all(
        params == (MAIN_ID, VERSION, "signal-100", "LTCUSDT", "LONG")
        for _, params in db.calls
    )
    assert "analytics.native_public_trade_source_events" in db.calls[0][0]
    assert "strategy_entry.p90_scoped_native_consumer_checkpoints" in db.calls[0][0]
    assert "competing.state=\'ACTIVE\'" in db.calls[0][0]
    assert "competing.market_source_id=s.market_source_id" in db.calls[0][0]


@pytest.mark.parametrize(("changes", "reason"), [
    ({"publication_id": 0}, "INVARIANT"),
    ({"publication_session_id": "another"}, "INVARIANT"),
    ({"source_session_state": "GAP"}, "INVARIANT"),
    ({"instrument_id": "inst-bybit-spot-ltcusdt"}, "INVARIANT"),
    ({"external_symbol": "APTUSDT"}, "INVARIANT"),
    ({"event_market_source_id": "other"}, "INVARIANT"),
    ({"exec_id": "forged"}, "INVARIANT"),
    ({"publication_status": "STALE"}, "INVARIANT"),
    ({"producer_release_commit": "bad"}, "INVARIANT"),
    ({"checkpoint_producer_release_commit": "a" * 40}, "INVARIANT"),
    ({"session_producer_release_commit": "a" * 40}, "INVARIANT"),
    ({"session_producer_release_commit": None}, "INVARIANT"),
    ({"entry_plan_fingerprint": "0" * 64}, "INVARIANT"),
    ({"exit_plan_fingerprint": "1" * 64}, "INVARIANT"),
    ({"consumer_release_commit": "bad"}, "INVARIANT"),
    ({"bootstrap_publication_id": 46}, "INVARIANT"),
    ({"last_publication_id": 44}, "INVARIANT"),
    ({"checkpoint_frame_number": 99}, "INVARIANT"),
    ({"event_at": EVENT - timedelta(seconds=5)}, "TIME_OR_FRESHNESS"),
    ({"original_event_at": EVENT + timedelta(seconds=1)}, "TIME_OR_FRESHNESS"),
    ({"published_at": AT + timedelta(seconds=1)}, "TIME_OR_FRESHNESS"),
    ({"accepted_at": AT + timedelta(seconds=1)}, "TIME_OR_FRESHNESS"),
    ({"as_of_second": AT + timedelta(seconds=1)}, "TIME_OR_FRESHNESS"),
    ({"event_at": "not-timestamp"}, "TIME_INVALID"),
])
def test_malformed_or_stale_pinned_evidence_blocks_without_order(changes, reason):
    db = Query([evidence(**changes)])
    with pytest.raises(R07PublicationProvenanceUnavailable, match=reason):
        require_r07_publication_provenance(db, request())


@pytest.mark.parametrize(("wrong_plan", "reason"), [
    ({"entry_plan_fingerprint": "0" * 64}, "INVARIANT"),
    ({"exit_plan_fingerprint": "1" * 64}, "INVARIANT"),
    ({"entry_plan_fingerprint": ""}, "PLAN_IDENTITY_INVALID"),
    ({"exit_plan_fingerprint": None}, "PLAN_IDENTITY_INVALID"),
])
def test_valid_original_signal_cannot_be_rebound_to_other_plan(wrong_plan, reason):
    db = Query([evidence()])
    with pytest.raises(R07PublicationProvenanceUnavailable, match=reason):
        require_r07_publication_provenance(db, {**request(), **wrong_plan})
    if "PLAN_IDENTITY_INVALID" in reason:
        assert db.calls == []


def test_r07_rejects_missing_exact_plan_even_with_valid_original_source():
    db = Query([evidence()])
    row = request()
    del row["entry_plan_fingerprint"]
    with pytest.raises(R07PublicationProvenanceUnavailable, match="PLAN_IDENTITY_INVALID"):
        require_r07_publication_provenance(db, row)
    assert db.calls == []


@pytest.mark.parametrize(("watermarks", "reason"), [
    ({}, "MISSING"),
    ({"LTCUSDT": None}, "MISSING"),
    ({"LTCUSDT": {"frame_number": 99, "frame_index": 1,
                   "exchange_sequence": 123455, "exec_id": "native-old",
                   "instrument_id": "inst-bybit-linear-ltcusdt"}}, "INVALID"),
    ({"LTCUSDT": {"frame_number": 100, "frame_index": 2,
                   "exchange_sequence": 123456, "exec_id": "forged",
                   "instrument_id": "inst-bybit-linear-ltcusdt"}}, "INVALID"),
    ({"LTCUSDT": {"frame_number": 100, "frame_index": 2,
                   "exchange_sequence": 123999, "exec_id": "native-100",
                   "instrument_id": "inst-bybit-linear-ltcusdt"}}, "INVALID"),
    ({"LTCUSDT": {"frame_number": 100, "frame_index": 2,
                   "exchange_sequence": 123456, "exec_id": "native-100",
                   "instrument_id": "inst-bybit-spot-ltcusdt"}}, "INVALID"),
    ({"LTCUSDT": {"frame_number": True, "frame_index": 2,
                   "exchange_sequence": 123456, "exec_id": "native-100",
                   "instrument_id": "inst-bybit-linear-ltcusdt"}}, "INVALID"),
])
def test_authentic_event_beyond_or_outside_native_publication_watermark_denied(
    watermarks, reason,
):
    db = Query([evidence(publication_native_watermarks=watermarks)])
    with pytest.raises(R07PublicationProvenanceUnavailable, match="WATERMARK_" + reason):
        require_r07_publication_provenance(db, request())


def test_native_publication_watermark_may_lie_after_observed_original_event():
    later = {
        "LTCUSDT": {
            "instrument_id": "inst-bybit-linear-ltcusdt",
            "frame_number": 101, "frame_index": 0,
            "exchange_sequence": 123999, "exec_id": "native-101",
        },
    }
    assert require_r07_publication_provenance(
        Query([evidence(publication_native_watermarks=later)]), request(),
    ) == 45


def test_missing_or_duplicated_signal_is_not_silently_skipped():
    for rows in ([], [evidence(), evidence(publication_id=46)]):
        with pytest.raises(R07PublicationProvenanceUnavailable, match="MISSING_OR_AMBIGUOUS"):
            require_r07_publication_provenance(Query(rows), request())


def test_r07_never_upgrades_legacy_or_wrong_p90_version():
    db = Query([evidence()])
    assert require_r07_publication_provenance(
        db, request(strategy_id="independent-r1")
    ) is None
    with pytest.raises(R07PublicationProvenanceUnavailable, match="VERSION"):
        require_r07_publication_provenance(
            db, request(strategy_id=MAIN_ID, version="old-paper")
        )
    assert db.calls == []


def test_sub_profile_source_symbol_mismatch_fails_closed():
    db = Query([evidence()])
    with pytest.raises(R07PublicationProvenanceUnavailable, match="SOURCE_IDENTITY"):
        require_r07_publication_provenance(
            db, request(strategy_id=SUB_ID)
        )
    assert db.calls == []


def test_runtime_does_not_reach_live_arm_or_publish_when_publication_unproven(monkeypatch):
    calls = []
    db = SimpleNamespace(transaction=nullcontext)
    monkeypatch.setattr(consumer, "TRADING_ACCOUNT_ID", "ta-sub-7")
    monkeypatch.setattr(consumer, "ACCOUNT_CONNECTOR_ID", "ac-sub-7")
    monkeypatch.setattr(consumer, "_release_expired_reserved_requests",
                        lambda *_a, **_kw: None)
    monkeypatch.setattr(consumer, "check_consumer_worker_route",
                        lambda *_a, **_kw: SimpleNamespace(ready=True, reasons=()))
    monkeypatch.setattr(consumer, "_execution_gate_enabled", lambda *_a: True)
    monkeypatch.setattr(consumer, "account_entry_safety_ready",
                        lambda *_a, **_kw: (True, ()))
    monkeypatch.setattr(consumer, "_next_request",
                        lambda *_a, **_kw: {
                            **request(account_id="ta-sub-7"),
                            "capital_reservation_state": "RESERVED",
                        })
    monkeypatch.setattr(consumer, "_request_from_row",
                        lambda _row: SimpleNamespace(
                            signal_id="signal-100",
                            capital_reservation_id="reserve-7",
                            exchange_position_slot_claim_id="slot-7",
                            position_mode_state_ref="mode-7",
                            trading_account_id="ta-sub-7",
                        ))
    monkeypatch.setattr(consumer, "_admission_pre_dispatch_status",
                        lambda *_a, **_kw: ("OK", ""))
    monkeypatch.setattr(consumer, "_critical_lifecycle_fault_code",
                        lambda *_a, **_kw: None)
    def unproven(*_a):
        raise R07PublicationProvenanceUnavailable("R07_NATIVE_OR_PUBLICATION_GAP")
    monkeypatch.setattr(consumer, "require_r07_publication_provenance", unproven)
    monkeypatch.setattr(consumer, "_live_arm_ready",
                        lambda *_a, **_kw: pytest.fail("financial arm reached"))
    monkeypatch.setattr(consumer, "_publish_command",
                        lambda *_a, **_kw: pytest.fail("exchange command reached"))
    monkeypatch.setattr(consumer, "_record_blocked",
                        lambda *_a, **_kw: calls.append("blocked"))
    monkeypatch.setattr(consumer, "_release_pre_exchange_admission",
                        lambda *_a, **_kw: calls.append("reservation released"))
    monkeypatch.setattr(consumer, "_record_request_state",
                        lambda *_a, **_kw: calls.append("cancelled"))
    assert consumer.run_once(db, now=AT) == "BLOCKED:R07_SOURCE_PUBLICATION_UNPROVEN"
    assert calls == ["blocked", "reservation released", "cancelled"]


def test_dispatch_proof_keeps_read_only_source_acl():
    db = Query([evidence()])
    assert require_r07_publication_provenance(db, request()) == 45
    assert len(db.calls) == 1
    assert "FOR SHARE" not in db.calls[0][0]
    assert "FOR UPDATE" not in db.calls[0][0]


def test_final_source_check_and_both_dispatch_writes_share_transaction(monkeypatch):
    events = []

    @contextmanager
    def transaction():
        events.append("begin")
        try:
            yield
        finally:
            events.append("end")

    db = SimpleNamespace(transaction=transaction)

    def prove(_db, _request, *, lock_checkpoint_for_dispatch=False):
        assert lock_checkpoint_for_dispatch is True
        events.append("final-source-recheck")
        return 45

    monkeypatch.setattr(consumer, "require_r07_publication_provenance", prove)
    monkeypatch.setattr(
        consumer, "require_r07_dispatch_epoch_guard",
        lambda *_a: events.append("epoch-fence"),
    )
    monkeypatch.setattr(
        consumer, "_publish_command",
        lambda *_a, **_kw: events.append("command"),
    )
    monkeypatch.setattr(
        consumer, "_finalize_owner_diagnostic_dispatch",
        lambda *_a, **_kw: events.append("diagnostic-finalize"),
    )
    consumer._publish_command_after_r07_recheck(
        db, row=request(), prepared=SimpleNamespace(command_id="test"),
        now=AT,
    )
    assert events == [
        "begin", "epoch-fence", "final-source-recheck", "command",
        "diagnostic-finalize", "end",
    ]


def test_runtime_revoked_source_after_first_check_never_queues_command(monkeypatch):
    events = []
    checks = []

    @contextmanager
    def transaction():
        events.append("begin")
        try:
            yield
        finally:
            events.append("end")

    db = SimpleNamespace(transaction=transaction)
    monkeypatch.setattr(consumer, "TRADING_ACCOUNT_ID", "ta-sub-7")
    monkeypatch.setattr(consumer, "ACCOUNT_CONNECTOR_ID", "ac-sub-7")
    monkeypatch.setattr(
        consumer, "_release_expired_reserved_requests", lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        consumer, "check_consumer_worker_route",
        lambda *_a, **_kw: SimpleNamespace(ready=True, reasons=()),
    )
    monkeypatch.setattr(consumer, "_execution_gate_enabled", lambda *_a: True)
    monkeypatch.setattr(
        consumer, "account_entry_safety_ready", lambda *_a, **_kw: (True, ()),
    )
    monkeypatch.setattr(
        consumer, "_next_request",
        lambda *_a, **_kw: {
            **request(account_id="ta-sub-7"),
            "capital_reservation_state": "RESERVED",
        },
    )
    monkeypatch.setattr(
        consumer, "_request_from_row",
        lambda _row: SimpleNamespace(
            signal_id="signal-100",
            capital_reservation_id="reserve-7",
            exchange_position_slot_claim_id="slot-7",
            position_mode_state_ref="mode-7",
            trading_account_id="ta-sub-7",
        ),
    )
    monkeypatch.setattr(
        consumer, "_admission_pre_dispatch_status", lambda *_a, **_kw: ("OK", ""),
    )
    monkeypatch.setattr(
        consumer, "_critical_lifecycle_fault_code", lambda *_a, **_kw: None,
    )

    def prove(_db, _row, *, lock_checkpoint_for_dispatch=False):
        checks.append("final" if lock_checkpoint_for_dispatch else "early")
        if checks[-1] == "final":
            raise R07PublicationProvenanceUnavailable("R07_SOURCE_SESSION_REVOKED")
        return 45

    monkeypatch.setattr(consumer, "require_r07_publication_provenance", prove)
    monkeypatch.setattr(
        consumer, "require_r07_dispatch_epoch_guard",
        lambda *_a: events.append("epoch-fence"),
    )
    monkeypatch.setattr(consumer, "_live_arm_ready", lambda *_a, **_kw: (True, ()))
    monkeypatch.setattr(consumer, "_policy_bundle", lambda *_a: object())
    monkeypatch.setattr(
        consumer, "prepare_runtime_entry_command",
        lambda *_a, **_kw: SimpleNamespace(command_id="never-sent"),
    )
    monkeypatch.setattr(
        consumer, "_publish_command",
        lambda *_a, **_kw: pytest.fail("unproven source published an Entry command"),
    )
    monkeypatch.setattr(
        consumer, "_finalize_owner_diagnostic_dispatch",
        lambda *_a, **_kw: pytest.fail("unproven source finalized an Entry"),
    )
    monkeypatch.setattr(
        consumer, "_record_blocked", lambda *_a, **_kw: events.append("blocked"),
    )
    monkeypatch.setattr(
        consumer, "_release_pre_exchange_admission",
        lambda *_a, **_kw: events.append("reservation released"),
    )
    monkeypatch.setattr(
        consumer, "_record_request_state",
        lambda *_a, **_kw: events.append("cancelled"),
    )
    assert consumer.run_once(db, now=AT) == "BLOCKED:R07_SOURCE_PUBLICATION_UNPROVEN"
    assert checks == ["early", "final"]
    assert events == [
        "begin", "epoch-fence", "end", "begin", "blocked",
        "reservation released", "cancelled", "end",
    ]



class _One:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value


class _EpochGuardDB:
    def __init__(self, *, installed=True, acquired=True, sql_error=False):
        self.installed = installed
        self.acquired = acquired
        self.sql_error = sql_error
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if self.sql_error:
            raise RuntimeError("synthetic SQL unavailable")
        if "pg_catalog.pg_trigger" in sql:
            return _One({"guard_valid": self.installed} if self.installed is not None else None)
        if "pg_try_advisory_xact_lock_shared" in sql:
            return _One({"locked": self.acquired})
        return _One(None)


def test_r07_epoch_guard_requires_migration_and_holds_exact_source_shared_lock():
    db = _EpochGuardDB()
    require_r07_dispatch_epoch_guard(db, request())
    assert len(db.calls) == 3
    assert "ROW EXCLUSIVE MODE NOWAIT" in db.calls[0][0]
    assert "tgenabled='O'" in db.calls[1][0]
    assert "tgtype=31" in db.calls[1][0]
    assert "pg_try_advisory_xact_lock_shared" in db.calls[2][0]
    assert len(db.calls[2][1]) == 1
    assert db.calls[2][1][0] == (
        "CRIPTA:R07:SOURCE_EPOCH_TRANSITION:V1:" + BYBIT_PUBLIC_MARKET_SOURCE_ID
    )


@pytest.mark.parametrize(("installed", "acquired", "reason"), [
    (False, True, "GUARD_MISSING_OR_DISABLED"),
    (None, True, "GUARD_MISSING_OR_DISABLED"),
    (True, False, "LOCK_UNAVAILABLE"),
])
def test_r07_epoch_guard_fails_closed_before_any_financial_dispatch(
    installed, acquired, reason,
):
    with pytest.raises(R07PublicationProvenanceUnavailable, match=reason):
        require_r07_dispatch_epoch_guard(
            _EpochGuardDB(installed=installed, acquired=acquired), request(),
        )


def test_r07_epoch_guard_db_error_is_typed_and_non_p90_unchanged():
    with pytest.raises(R07PublicationProvenanceUnavailable, match="SQL_UNAVAILABLE"):
        require_r07_dispatch_epoch_guard(_EpochGuardDB(sql_error=True), request())
    db = _EpochGuardDB(sql_error=True)
    require_r07_dispatch_epoch_guard(db, request(strategy_id="independent-r1"))
    assert db.calls == []


def test_r07_final_checkpoint_proof_requires_row_share_only_at_dispatch():
    db = Query([evidence()])
    assert require_r07_publication_provenance(
        db, request(), lock_checkpoint_for_dispatch=True,
    ) == 45
    assert "FOR SHARE OF cp" in db.calls[0][0]
    assert "FOR UPDATE" not in db.calls[0][0]
    earlier = Query([evidence()])
    require_r07_publication_provenance(earlier, request())
    assert "FOR SHARE OF cp" not in earlier.calls[0][0]
