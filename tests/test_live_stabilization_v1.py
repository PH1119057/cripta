from __future__ import annotations

from pathlib import Path

from operations.connectivity.exact_close import classify_exit


def test_latest_matching_protection_event_wins_when_exchange_order_id_is_reused() -> None:
    owner, mechanism, method = classify_exit(
        exit_order_id="same-stop-id",
        stop_order_type="StopLoss",
        protection_events=[
            {
                "exchange_order_ids": ["same-stop-id"],
                "protection_kind": "INITIAL_HARD_STOP",
                "initiator": "ALGORITHM",
            },
            {
                "exchange_order_ids": ["same-stop-id"],
                "protection_kind": "OWNER_MODIFIED_STOP",
                "initiator": "OWNER",
            },
        ],
        close_commands=[],
    )
    assert (owner, mechanism, method) == (
        "OWNER",
        "OWNER_MODIFIED_STOP",
        "EXACT_PROTECTION_ORDER_ID",
    )


def test_dashboard_requires_server_confirmation_for_gate_and_symbol_settings() -> None:
    source = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
    assert "async function confirmGateState(expected)" in source
    assert "await confirmGateState(enabled)" in source
    handler_start = source.index("tradeGateButton.addEventListener")
    handler_end = source.index("function processAudio", handler_start)
    handler = source[handler_start:handler_end]
    assert "saveSettings()" not in handler
    assert "settings_version:serverSettingsVersion" in handler
    assert "confirmed:true" in handler
    assert "Сервер не подтвердил выбранный список монет" in source
    assert "autoSaveStatus" in source
    assert "Ошибка обновления live-state" in source


def test_dashboard_persists_gate_audit_event() -> None:
    source = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    assert "control.execution_gate_events" in source
    assert 'response["gate_enabled"] = enabled' in source
    assert "previous_enabled" in source
    assert "request_id" in source


def test_analyst_uses_exact_attribution_for_protective_exit_reason() -> None:
    source = Path("operations/monitoring/m3_trade_analyst.py").read_text(encoding="utf-8")
    assert '"decision_source": "EXACT_EXIT_ATTRIBUTION"' in source
    assert 'mechanism = str(attribution["exit_mechanism"] or "UNKNOWN")' in source


def test_patch_does_not_change_trailing_or_dispatcher_veto_policy() -> None:
    exit_source = Path("operations/monitoring/exit_runtime.py").read_text(encoding="utf-8")
    assert "trailing_start_preserves_protection" in exit_source
    assert 'trailing_pct = Decimal(str(settings[2] or "0.30"))' in exit_source

    runtime_source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert "DISPATCHER_MARKET_INCOMPATIBLE" not in runtime_source
