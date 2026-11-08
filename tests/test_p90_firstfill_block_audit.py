"""Durable, account-scoped first-fill P90 Exit denial audit regression.

No real Bybit operations. The existing Exit owner must report missing proofs
without changing trading safety, expiry, or position state.
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONNECTIVITY = ROOT / "operations/connectivity"
if str(CONNECTIVITY) not in sys.path:
    sys.path.insert(0, str(CONNECTIVITY))

import p90_real_independent_exit as subject  # noqa: E402

AT = datetime(2026, 11, 8, 4, 27, 22, tzinfo=UTC)
ACCOUNT = "ta-" + "a" * 32
POSITION = "POS-verified-firstfill"
SOURCE_FAILURE = (
    "P90_EXIT_PROOF_BLOCKED:P90RealPostfillSourceClientUnverified:"
    "P90_REAL_MARKET_SOURCE_POST_PROTECTION_NATIVE_MISSING"
)


class AuditConnection:
    def __init__(self, account=ACCOUNT, position=POSITION):
        self.account = account
        self.position = position
        self.writes: list[dict[str, object]] = []
        self.queries: list[str] = []
        self.enabled = True

    def execute(self, sql, params=()):
        self.queries.append(sql)
        if "SELECT p.position_id,p.fill_at,p.initial_protection_confirmed_at" in sql:
            row = (
                {
                    "position_id": self.position,
                    "fill_at": AT - timedelta(seconds=5),
                    "initial_protection_confirmed_at": AT - timedelta(seconds=1),
                    "trading_account_id": self.account,
                    "symbol": "INJUSDT",
                }
                if self.enabled else None
            )
            return SimpleNamespace(fetchone=lambda: row)
        if "FROM runtime.connection_events" in sql:
            account, pos = params
            rows = [
                event for event in self.writes
                if event["account"] == account and event["detail"]["position_id"] == pos
            ]
            prior = rows[-1]["detail"]["reason_code"] if rows else None
            return SimpleNamespace(fetchone=lambda: None if prior is None else {"reason_code": prior})
        if "INSERT INTO runtime.connection_events" in sql:
            observed_ms, payload, account = params
            detail = json.loads(payload)
            assert account == self.account
            assert detail["position_id"] == self.position
            assert detail["symbol"] == "INJUSDT"
            assert detail["exchange_mutation"] is False
            assert observed_ms == int(AT.timestamp() * 1000)
            self.writes.append({"account": account, "detail": detail})
            return SimpleNamespace()
        raise AssertionError("Unexpected SQL: " + sql[:100])


def test_one_durable_denial_per_exact_position_account_and_changed_proof():
    db = AuditConnection()
    subject.audit_firstfill_exit_result(db, result=SOURCE_FAILURE, observed_at=AT)
    assert len(db.writes) == 1
    assert db.writes[0]["detail"]["reason_code"] == SOURCE_FAILURE
    # Repeat polls and even a new caller after process restart must not flood.
    subject.audit_firstfill_exit_result(db, result=SOURCE_FAILURE, observed_at=AT)
    assert len(db.writes) == 1
    subject.audit_firstfill_exit_result(
        db, result="P90_REAL_EXIT_FIRST_FILL_ORIGINAL_SOURCE_WINDOW_EXPIRED",
        observed_at=AT,
    )
    assert len(db.writes) == 2


@pytest.mark.parametrize("result", (
    "P90_NO_PROTECTED_POSITION_REQUIRING_TRAIL",
    "P90_EXIT_TRAIL035_REQUEST_PUBLISHED",
    "P90_EXIT_NO_MUTATION_PROVED",
))
def test_no_denial_record_for_absent_position_or_proved_outcome(result):
    db = AuditConnection()
    subject.audit_firstfill_exit_result(db, result=result, observed_at=AT)
    assert not db.queries
    assert not db.writes


def test_no_fake_account_or_position_evidence_and_no_exchange_mutation():
    db = AuditConnection()
    db.enabled = False
    subject.audit_firstfill_exit_result(db, result=SOURCE_FAILURE, observed_at=AT)
    assert not db.writes
    assert not any("INSERT INTO runtime.trade_commands" in q for q in db.queries)
    assert not any("UPDATE control.execution_gates" in q for q in db.queries)


def test_other_account_and_other_position_keep_independent_denials():
    db1 = AuditConnection(ACCOUNT, POSITION)
    db2 = AuditConnection("ta-" + "b" * 32, "POS-other")
    subject.audit_firstfill_exit_result(db1, result=SOURCE_FAILURE, observed_at=AT)
    subject.audit_firstfill_exit_result(db2, result=SOURCE_FAILURE, observed_at=AT)
    assert len(db1.writes) == len(db2.writes) == 1
    assert db1.writes[0]["account"] != db2.writes[0]["account"]


def test_consumer_records_exact_p90_result_before_any_ordinary_dispatch():
    code = (CONNECTIVITY / "universal_exit_consumer.py").read_text(encoding="utf-8")
    func = next(
        node for node in ast.parse(code).body
        if isinstance(node, ast.FunctionDef) and node.name == "run_once"
    )
    body = ast.get_source_segment(code, func)
    assert body is not None
    assert body.index("run_p90_exit_once(connection, now=current)") < body.index(
        "audit_firstfill_exit_result("
    ) < body.index("materialize_once(")


def test_only_safe_canonical_error_token_is_disclosed_in_blocked_result():
    # A private signed transport exception may include an arbitrary string.
    # Neither credentials nor raw provider responses belong in connection_events.
    assert subject.safe_firstfill_block_reason(
        RuntimeError("P90_REAL_MARKET_SOURCE_NATIVE_WINDOW_INCOMPLETE")
    ) == (
        "P90_EXIT_PROOF_BLOCKED:RuntimeError:"
        "P90_REAL_MARKET_SOURCE_NATIVE_WINDOW_INCOMPLETE"
    )
    assert subject.safe_firstfill_block_reason(
        RuntimeError("secret key=unsafe URL with provider response")
    ) == "P90_EXIT_PROOF_BLOCKED:RuntimeError"


def test_installed_consumer_and_audit_use_exact_dict_row_contract():
    source = (CONNECTIVITY / "universal_exit_consumer.py").read_text(encoding="utf-8")
    independent = (CONNECTIVITY / "p90_real_independent_exit.py").read_text(
        encoding="utf-8"
    )
    assert "row_factory=dict_row" in source
    assert "AS reason_code" in independent
    assert 'row["reason_code"] == code' in independent
    # Regression: first insert is followed by the read of that same row.
    # Under psycopg.rows.dict_row the result is NOT a tuple; previous
    # row[0] crashed the systemd consumer every 2s after first durable audit.
    db = AuditConnection()
    subject.audit_firstfill_exit_result(db, result=SOURCE_FAILURE, observed_at=AT)
    subject.audit_firstfill_exit_result(db, result=SOURCE_FAILURE, observed_at=AT)
    assert len(db.writes) == 1
