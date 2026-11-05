from pathlib import Path

import pytest

from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateIpcUnverified,
)
from operations.dashboard import app

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
UI = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")
OBSERVER = (
    ROOT / "operations/monitoring/universal_entry_shadow.py"
).read_text(encoding="utf-8")
PREARM = (
    ROOT / "src/bybit_workbench/p90l90_real_prearm_evidence.py"
).read_text(encoding="utf-8")
ROUTES = (
    ROOT / "src/bybit_workbench/universal_entry/p90l90_real_account_routes.py"
).read_text(encoding="utf-8")


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_readiness_retries_only_transport_transient_private_ipc(monkeypatch):
    calls = 0

    def evidence(*_args, **kwargs):
        nonlocal calls
        calls += 1
        assert (
            kwargs["private_proof_timeout_seconds"]
            == app.P90_ACCOUNT_PROOF_TIMEOUT_SECONDS
            == app.P90_REAL_READINESS_PRIVATE_ATTEMPT_SECONDS
        )
        if calls == 1:
            raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_INCOMPLETE_FRAME")
        return {"status": "PREARM_TECHNICAL_READY"}

    monkeypatch.setattr(app.psycopg, "connect", lambda *_args, **_kwargs: _Connection())
    monkeypatch.setattr(app, "prepare_p90l90_real_v3_prearm_evidence", evidence)
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)

    result = app._wait_p90_real_v3_technical_evidence(timeout_seconds=30.0)
    assert result["status"] == "PREARM_TECHNICAL_READY"
    assert calls == 2


def test_readiness_does_not_retry_identity_or_scope_private_ipc(monkeypatch):
    calls = 0

    def evidence(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_PEER_NOT_VERIFIED")

    monkeypatch.setattr(app.psycopg, "connect", lambda *_args, **_kwargs: _Connection())
    monkeypatch.setattr(app, "prepare_p90l90_real_v3_prearm_evidence", evidence)
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)

    with pytest.raises(P90PrivateIpcUnverified, match="PEER_NOT_VERIFIED"):
        app._wait_p90_real_v3_technical_evidence(timeout_seconds=1.0)
    assert calls == 1


def test_readiness_starts_required_services_before_account_ws_recovery():
    start = APP.index("def _u6_prepare_p90_real_v3_readiness(")
    end = APP.index("\ndef _u6_p90_real_v3_arm(", start)
    readiness = APP[start:end]
    assert readiness.index("snapshots = collect_systemd_snapshots(") < readiness.index(
        "ws_recovery = _recover_p90_real_ws_safety_if_needed("
    )
    assert readiness.index("if plan.starts:") < readiness.index(
        "ws_recovery = _recover_p90_real_ws_safety_if_needed("
    )


def test_staged_and_source_only_private_preflight_precede_public_ws_clock():
    epoch = OBSERVER[
        OBSERVER.index("def _run_observer_epoch("):
        OBSERVER.index("\ndef main()", OBSERVER.index("def _run_observer_epoch("))
        if "\ndef main()" in OBSERVER[OBSERVER.index("def _run_observer_epoch("):]
        else len(OBSERVER)
    ]
    assert "if p90_real_source_only or (" in epoch
    assert "p90_real_staged and preflight_restart_epoch_id is None" in epoch
    assert "preflight_p90_real_private_flat_source(" in epoch
    assert epoch.index("preflight_restart_epoch_id = inspect_p90_real_open_restart_epoch(") < (
        epoch.index("preflight_p90_real_private_flat_source(")
    )
    assert epoch.index("preflight_p90_real_private_flat_source(") < epoch.index(
        "sock, _ready_local, _ready_server, buffered = _connect_and_subscribe"
    )


def test_post_ws_private_proof_attempts_are_shorter_than_ws_budget():
    assert "_PRIVATE_PROOF_RETRY_BUDGET_SECONDS = 8.0" in (
        ROOT / "operations/monitoring/p90l90_real_source_bootstrap.py"
    ).read_text(encoding="utf-8")
    assert (
        "_PRIVATE_PROOF_ATTEMPT_SECONDS = _PRIVATE_PROOF_RETRY_BUDGET_SECONDS"
        in (
            ROOT / "operations/monitoring/p90l90_real_source_bootstrap.py"
        ).read_text(encoding="utf-8")
    )
    assert (
        "_ACCOUNT_GENERATION_BARRIER_MAX_AGE_SECONDS = "
        "P90_ACCOUNT_PROOF_TIMEOUT_SECONDS"
        in (
            ROOT / "operations/monitoring/p90l90_real_source_bootstrap.py"
        ).read_text(encoding="utf-8")
    )
    assert (
        "_PRIVATE_PREFLIGHT_ATTEMPT_SECONDS = P90_ACCOUNT_PROOF_TIMEOUT_SECONDS"
        in (
            ROOT / "operations/monitoring/p90l90_real_source_bootstrap.py"
        ).read_text(encoding="utf-8")
    )
    assert "private_proof_timeout_seconds: float | None = None" in PREARM
    assert "timeout_seconds: float | None = None" in ROUTES


def test_unresolved_observer_faults_are_not_auto_hidden_after_recovery():
    assert "superseded_count += 1" not in APP
    assert '"state": "red" if items else "clear"' in APP
    assert '"p90_operation_relation"' in APP
    assert '"BEFORE_CURRENT_P90_OPERATION"' in APP
    assert '"superseded_count": 0' in APP


def test_primary_owner_arm_status_is_plain_language_and_raw_detail_is_collapsed():
    start = UI.index("function p90ReadinessOperationText(")
    end = UI.index("function refreshP90RealV3ArmControl(", start)
    helper = UI[start:end]
    assert "blockerMap" in helper
    assert "blocker_detail" in helper.split(
        "function p90ReadinessOperationTechnicalText", 1
    )[0]
    assert "Технические детали последней операции OWNER ARM" in UI
    assert "panel.open=true" not in UI[
        UI.index("function showP90ArmResult("):
        UI.index("function showAuthoritativeP90ArmState(")
    ]
    assert "РАНЕЕ · до текущего запуска" in UI
    assert "Это отдельный durable-журнал, а не статус текущей кнопки OWNER ARM" in UI
    assert "function observerFaultFriendlyText(item)" in UI
    assert "Не удалось получить полное подтверждение состояния MAIN/SUB аккаунтов" in UI
    assert "Не удалось завершить проверку рыночного источника в допустимое окно" in UI
    assert "Для открытой позиции не подтверждён биржевой TRAIL035" in UI
    assert "Не удалось вовремя подтвердить состояние аккаунта для восстановления" in UI
    assert "Восстановление аккаунта остановлено точной проверкой состояния" in UI
    assert "Не подтверждён локальный канал восстановления аккаунта" in UI
    assert "<b>Технические детали:</b>" in UI
