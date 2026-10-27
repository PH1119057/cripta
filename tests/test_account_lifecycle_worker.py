"""Fake-Exchange / fake-PG safeguards for dormant account-local worker (#235)."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bybit_workbench.lifecycle_exposure_bridge import ExposureAuditResult

MODULE = Path(__file__).resolve().parents[1] / "operations/monitoring/account_lifecycle_worker.py"
spec = importlib.util.spec_from_file_location("account_lifecycle_worker", MODULE)
assert spec is not None and spec.loader is not None
imported = importlib.util.module_from_spec(spec)
spec.loader.exec_module(imported)

A = "ta-" + "a" * 32
B = "ta-" + "b" * 32
C = "ac-" + "1" * 32
NAME = "bybit-sub-" + B
CREDS = "/run/credentials/cripta-lifecycle-account@" + B + ".service"
STATUS = "/var/lib/cripta/lifecycle_account/account-" + B + "/status.json"
NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


class _Cursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class _Connection:
    def __init__(self, *, kind: str = "SUB") -> None:
        self.kind = kind
        self.queries: list[str] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _Cursor:
        self.queries.append(sql)
        if "FROM identity.trading_accounts a" in sql:
            return _Cursor([{
                "account_kind": self.kind,
                "account_status": "ACTIVE",
                "connector_status": "ACTIVE",
                "trading_account_id": B,
            }])
        if "FROM runtime.position_ownership" in sql:
            assert params == (B,)
            return _Cursor([{
                "position_id": "pos-1", "symbol": "BTCUSDT",
                "side": "Buy", "position_idx": 0,
                "actual_avg_fill": "100", "entry_command_id": "entry-1",
                "fill_at": NOW,
            }])
        raise AssertionError("Unexpected worker query")


def test_exact_systemd_credential_and_status_scope_required() -> None:
    file, path, ref = imported.validated_scope(B, C, NAME, CREDS, STATUS)
    assert file.name == NAME
    assert path.name == "status.json"
    assert ref == "systemd:" + NAME
    with pytest.raises(RuntimeError, match="MAIN_CREDENTIAL"):
        imported.validated_scope(B, C, "bybit-mainnet", CREDS, STATUS)
    with pytest.raises(RuntimeError, match="MOUNT_UNTRUSTED"):
        imported.validated_scope(B, C, NAME, "/tmp/credentials", STATUS)
    with pytest.raises(RuntimeError, match="STATUS_NOT_ACCOUNT_SCOPED"):
        imported.validated_scope(B, C, NAME, CREDS, STATUS.replace(B, A))
    with pytest.raises(RuntimeError, match="TRADING_ACCOUNT_INVALID"):
        imported.validated_scope("ta-unknown", C, NAME, CREDS, STATUS)


def test_main_account_identity_rejected_before_any_scan() -> None:
    db = _Connection(kind="MAIN")
    with pytest.raises(RuntimeError, match="SUB_IDENTITY_NOT_READY"):
        imported.inspect_account_once(
            db, account_id=B, connector_id=C,
            credential_ref="systemd:" + NAME,
            account_snapshot={}, now=NOW,
        )
    assert len(db.queries) == 1


def test_failed_signed_exchange_check_cannot_resolve_old_faults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Connection()
    def _unavailable(*_: Any, **__: Any) -> ExposureAuditResult:
        return ExposureAuditResult(None, "EXCHANGE_UNKNOWN", ("UID_MISMATCH",))
    monkeypatch.setattr(imported, "verified_account_exposure", _unavailable)
    def _never_scan(*_: Any, **__: Any) -> Any:
        raise AssertionError("No lifecycle fault may auto-resolve on failed signed check")
    monkeypatch.setattr(imported, "LifecycleSupervisor", _never_scan)
    got = imported.inspect_account_once(
        db, account_id=B, connector_id=C,
        credential_ref="systemd:" + NAME,
        account_snapshot={"complete": False}, now=NOW,
    )
    assert got["state"] == "UNKNOWN"
    assert got["trading_rights"] == "NONE"


def test_proven_sub_passes_exact_isolated_scope_to_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Connection()
    seen: dict[str, object] = {}
    def _audit(*_: Any, **params: Any) -> ExposureAuditResult:
        assert params["trading_account_id"] == B
        assert params["account_connector_id"] == C
        assert params["verified_credential_ref"] == "systemd:" + NAME
        return ExposureAuditResult((), "VERIFIED", ())
    monkeypatch.setattr(imported, "verified_account_exposure", _audit)
    def _protection(*_: Any, **params: Any) -> dict[str, dict[str, str]]:
        assert params["trading_account_id"] == B
        return {"pos-1": {"state": "VERIFIED", "reason": "fake-test"}}
    monkeypatch.setattr(imported, "account_protection_verdicts", _protection)
    class _Supervisor:
        def __init__(self, _: Any, **kwargs: Any) -> None:
            seen.update(kwargs)
        def scan(self, *, now: datetime) -> Any:
            assert now == NOW
            return type("Result", (), {"open_faults": 0, "resolved_faults": 0})()
    monkeypatch.setattr(imported, "LifecycleSupervisor", _Supervisor)
    got = imported.inspect_account_once(
        db, account_id=B, connector_id=C,
        credential_ref="systemd:" + NAME,
        account_snapshot={"complete": True}, now=NOW,
    )
    assert got["state"] == "VERIFIED_ACCOUNT_SCAN"
    assert seen["trading_account_id"] == B
    assert seen["owns_global_faults"] is False
    assert seen["exchange_exposure"] == ()
