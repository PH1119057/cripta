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
        "source_session_state": "ACTIVE",
        "event_market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
        "instrument_id": "inst-bybit-linear-ltcusdt",
        "external_symbol": "LTCUSDT",
        "exec_id": "native-100",
        "original_event_at": EVENT,
        "accepted_at": EVENT + timedelta(milliseconds=200),
        "checkpoint_frame_number": 101,
        "checkpoint_frame_index": 0,
        "last_publication_id": 46,
        "bootstrap_publication_id": 40,
        "consumer_release_commit": "d" * 40,
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

    def prove(_db, _request):
        events.append("final-source-recheck")
        return 45

    monkeypatch.setattr(consumer, "require_r07_publication_provenance", prove)
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
        "begin", "final-source-recheck", "command",
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

    def prove(_db, _row):
        checks.append("final" if events and events[-1] == "begin" else "early")
        if checks[-1] == "final":
            raise R07PublicationProvenanceUnavailable("R07_SOURCE_SESSION_REVOKED")
        return 45

    monkeypatch.setattr(consumer, "require_r07_publication_provenance", prove)
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
        "begin", "end", "begin", "blocked",
        "reservation released", "cancelled", "end",
    ]
