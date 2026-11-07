"""Independent P90 REAL Exit: first-fill and fail-closed negative regression.

These tests never use real Exchange orders or impersonate Bybit signatures.
The runtime producer has to supply actual signed IPC/source proofs separately.
"""
from __future__ import annotations

import ast
import sys
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONNECTIVITY = ROOT / "operations/connectivity"
if str(CONNECTIVITY) not in sys.path:
    sys.path.insert(0, str(CONNECTIVITY))

import p90_real_independent_exit as subject  # noqa: E402

AT = datetime(2026, 11, 8, 0, 0, tzinfo=UTC)


class CandidateConnection:
    def __init__(self, protected_at: datetime | None):
        self.protected_at = protected_at
        self.sql: list[str] = []

    def execute(self, sql, parameters=()):
        self.sql.append(sql)
        if "SELECT p.position_id,p.fill_at,p.initial_protection_confirmed_at" in sql:
            row = (
                None if self.protected_at is None
                else {
                    "position_id": "p90-protected-real-position",
                    "fill_at": self.protected_at - timedelta(seconds=1),
                    "initial_protection_confirmed_at": self.protected_at,
                }
            )
            return SimpleNamespace(fetchone=lambda: row)
        raise AssertionError("Unexpected DB call: " + sql[:90])


def test_no_position_or_exit_request_does_not_trigger_any_exchange_activity():
    db = CandidateConnection(None)
    assert subject.run_p90_exit_once(db, now=AT) == (
        "P90_NO_PROTECTED_POSITION_REQUIRING_TRAIL"
    )
    assert len(db.sql) == 1


@pytest.mark.parametrize("seconds", (16, 30, 3600, 7200))
def test_old_open_position_never_arms_a_stale_active_price(seconds):
    db = CandidateConnection(AT - timedelta(seconds=seconds))
    assert subject.run_p90_exit_once(db, now=AT) == (
        "P90_REAL_EXIT_FIRST_FILL_ORIGINAL_SOURCE_WINDOW_EXPIRED"
    )
    assert len(db.sql) == 1
    assert "INSERT" not in db.sql[0] and "UPDATE" not in db.sql[0]


@pytest.mark.parametrize("protection_offset,now_offset", (
    (-1, 16), (1, 18), (3, 600),
))
def test_pre_fill_and_expired_windows_are_never_promoted_to_exit(
    protection_offset, now_offset,
):
    with pytest.raises(subject.P90IndependentExitBlocked):
        subject._validate_first_fill_age(
            fill_at=AT,
            protected_at=AT + timedelta(seconds=protection_offset),
            now=AT + timedelta(seconds=now_offset),
        )


def test_fresh_candidate_without_original_proof_is_blocked_not_assumed_ready():
    db = CandidateConnection(AT - timedelta(seconds=1))
    # No transaction provider means no proof. Catching this cannot grant a
    # decision, post an Exchange order, or count as an Exit success.
    result = subject.run_p90_exit_once(db, now=AT)
    assert result.startswith("P90_EXIT_PROOF_BLOCKED:")
    assert not any("INSERT" in sql or "UPDATE" in sql for sql in db.sql)


def test_independent_exit_runs_before_generic_consumer_and_ignores_entry_gate():
    source = (CONNECTIVITY / "universal_exit_consumer.py").read_text(
        encoding="utf-8"
    )
    main = next(
        n for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "run_once"
    )
    body = ast.get_source_segment(source, main)
    assert body is not None
    assert "run_p90_exit_once(connection, now=current)" in body
    assert body.index("run_p90_exit_once(") < body.index("materialize_once(")
    assert "_execution_gate_enabled(" not in body
    assert "OWNER_ARM" not in body


def test_only_exit_engine_decides_and_account_execution_sends_exchange_order():
    code = (CONNECTIVITY / "p90_real_independent_exit.py").read_text(
        encoding="utf-8"
    )
    assert "P90RealPostfillExitPort(" in code
    assert "P90RealExitExecutionPort(" in code
    assert "read_real_postfill_source_window(" in code
    assert "P90PrivateProofClient(" in code
    assert "p90_real_exit_no_pending(" in code
    assert "pg_advisory_xact_lock" in code
    assert "POST /v5/" not in code
    assert "api_post(" not in code
    assert "/v5/order/create" not in code
    assert "trailing_stop(" not in code.lower()
    private = (CONNECTIVITY / "private_runtime.py").read_text(encoding="utf-8")
    fn = next(
        n for n in ast.parse(private).body
        if isinstance(n, ast.FunctionDef) and n.name == "execute_command"
    )
    body = ast.get_source_segment(private, fn)
    assert body is not None
    assert "handoff_real_first_protected_fill(" not in body
    assert "_execute_first_fill_trailing(" not in body


class ExitConnection(CandidateConnection):
    def __init__(self):
        super().__init__(AT - timedelta(seconds=2))
        self.initial = {
            "entry_order_id": "signed-original-order-id",
            "entry_execution_ids": ["signed-original-execution-id"],
            "symbol": "LTCUSDT",
            "trading_account_id": "ta-" + "a" * 32,
        }

    def transaction(self):
        return nullcontext()

    def cursor(self, *, row_factory):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, parameters=()):
        self.sql.append(sql)
        if "SELECT p.position_id,p.fill_at,p.initial_protection_confirmed_at" in sql:
            return SimpleNamespace(fetchone=lambda: {
                "position_id": "p90-protected-real-position",
                "fill_at": AT - timedelta(seconds=3),
                "initial_protection_confirmed_at": AT - timedelta(seconds=2),
            })
        if "SELECT epoch_id FROM strategy_entry.p90l90_real_source_epochs" in sql:
            return SimpleNamespace(fetchall=lambda: [("p90-real-epoch-"+"b"*40,)])
        if "SELECT initial_protection_evidence" in sql:
            return SimpleNamespace(fetchone=lambda: {
                "initial_protection_evidence": self.initial,
            })
        if "pg_advisory_xact_lock" in sql:
            return SimpleNamespace(fetchone=lambda: (1,))
        raise AssertionError("Unexpected SQL: "+sql[:100])


def _live_proofs(monkeypatch, uid="12345678"):
    monkeypatch.setattr(subject, "datetime", SimpleNamespace(now=lambda _: AT))
    account = "ta-" + "a" * 32
    connector = "ac-" + "c" * 32
    position = SimpleNamespace(
        strategy_position_id="p90-protected-real-position",
        fill_at=AT - timedelta(seconds=3),
        initial_protection_confirmed_at=AT - timedelta(seconds=2),
        strategy_id=subject.MAIN_ID,
        strategy_activation_id="the-owned-activation",
        trading_account_id=account,
        strategy_execution_binding_id="exact-binding",
        symbol="LTCUSDT",
    )
    plan = SimpleNamespace(exit_plan_fingerprint="the-exit-plan")
    route = SimpleNamespace(
        trading_account_id=account,
        strategy_execution_binding_id="exact-binding",
        activation_id="the-owned-activation",
        account_connector_id=connector,
        registered_exchange_uid="12345678",
    )
    routes = SimpleNamespace(
        main=route, sub=route,
        account_by_strategy={subject.MAIN_ID:account, subject.SUB_ID:"ta-"+"d"*32},
    )
    private = SimpleNamespace(
        signed_provider_uid=uid,
        trading_account_id=account,
        strategy_execution_binding_id="exact-binding",
        exchange_position_key="the-exchange-position",
        pending_exit_mutation=False,
        observed_at=AT,
        position={"stopLoss":"90","takeProfit":"110","trailingStop":"0"},
    )
    coverage = SimpleNamespace(
        covered_through=AT,
    )
    monkeypatch.setattr(subject, "load_exit_binding", lambda *_a, **_kw: (position,plan))
    monkeypatch.setattr(
        subject, "load_active_strategy_bundles",
        lambda *_: (SimpleNamespace(card=SimpleNamespace(strategy_version=subject.VERSION)),),
    )
    monkeypatch.setattr(subject, "read_p90_real_account_routes", lambda *_a, **_kw: routes)
    monkeypatch.setattr(
        subject, "inspect_real_source_identity",
        lambda *_a, **_kw: SimpleNamespace(origin_commit="b"*40),
    )
    monkeypatch.setattr(subject, "read_real_postfill_source_window", lambda **_kw: coverage)
    monkeypatch.setattr(subject, "p90_real_exit_no_pending", lambda *_a, **_kw: True)
    monkeypatch.setattr(subject, "P90PrivateProofClient", lambda **_kw:
        SimpleNamespace(for_closed_boundary=lambda *_a: private))
    monkeypatch.setattr(subject, "P90RealExitExecutionPort", lambda *_a, **_kw: lambda *_a: None)
    monkeypatch.setattr(subject, "PostgresExitShadowStore", lambda *_a: object())
    return private


def test_exact_fresh_proof_invokes_universal_exit_but_not_direct_bybit(monkeypatch):
    seen = []
    _live_proofs(monkeypatch)
    monkeypatch.setenv("CRIPTA_RELEASE_COMMIT", "e"*40)
    def stub_exit_port(*_a, **_kw):
        def run(**kw):
            seen.append(kw)
            return SimpleNamespace(status=subject.ExitEvaluationStatus.DECISION_CREATED)
        return run
    monkeypatch.setattr(subject,"P90RealPostfillExitPort",stub_exit_port)
    db=ExitConnection()
    assert subject.run_p90_exit_once(db,now=AT) == "P90_EXIT_TRAIL035_REQUEST_PUBLISHED"
    assert len(seen)==1
    assert seen[0]["position"].strategy_position_id=="p90-protected-real-position"
    assert seen[0]["signed_fill_execution_ids"]==("signed-original-execution-id",)
    assert all("/v5/" not in query for query in db.sql)


def test_signed_wrong_uid_blocks_the_exit_without_preparing_order(monkeypatch):
    seen=[]
    _live_proofs(monkeypatch,uid="unrelated-signed-uid")
    monkeypatch.setenv("CRIPTA_RELEASE_COMMIT", "e"*40)
    monkeypatch.setattr(subject,"P90RealPostfillExitPort",lambda *_a,**_kw:
        seen.append(True))
    result=subject.run_p90_exit_once(ExitConnection(),now=AT)
    assert result=="P90_REAL_EXIT_PRIVATE_SIGNED_UID_MISMATCH"
    assert not seen
