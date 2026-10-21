from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "operations" / "connectivity" / "private_runtime.py"


def source() -> str:
    return PRIVATE.read_text(encoding="utf-8")


def test_private_reconnect_is_temporarily_fail_closed_not_permanent_owner_rearm() -> None:
    text = source()
    assert 'PRIVATE_RECONNECT_GATE_REASON = "private WS reconnect: verification pending"' in text
    assert "private WS reconnect: owner re-arm required" not in text
    assert "def restore_r1_gate_after_verified_reconnect(" in text
    assert "gate_was_open=restore_gate_after_reconnect" in text
    assert "gate_restored=restored" in text


def test_account_generation_forces_exact_position_mode_probe_every_cycle() -> None:
    text = source()
    assert "force_refresh: bool = False" in text
    assert "if refresh_seconds > 0 and not force_refresh:" in text
    start = text.index("def reconcile(")
    body = text[start:text.index("def upsert_exchange_order_history(", start)]
    assert "force_refresh=True" in body
    assert "position_mode_refs" in body
    assert "runtime.account_state_generations" in body

def test_reconnect_auto_restore_verifies_exact_owner_arm_and_complete_inventory() -> None:
    text = source()
    start = text.index("def restore_r1_gate_after_verified_reconnect(")
    end = text.index("\ndef entry_runtime_readiness(", start)
    body = text[start:end]
    for token in (
        "control.live_arm_sessions",
        "state='ACTIVE'",
        "strategy_entry.execution_permissions",
        "strategy_entry.strategy_activations",
        'str(reconciliation[2]) != "reconnect"',
        "int(reconciliation[4]) != hot_positions",
        "int(reconciliation[5]) != hot_orders",
        "runtime.position_mode_states",
        'str(row[1]) != "ONE_WAY"',
        "PRIVATE_RECONNECT_RECOVERED_REASON",
        "'safety_recovery'",
    ):
        assert token in body


def test_process_restart_preserves_authorized_gate_and_resting_orders() -> None:
    text = source()
    body = text.split('def startup_live_safety(', 1)[1].split('def record_entry_decision(', 1)[0]
    assert 'disarm_new_entries(connection' not in body
    assert 'cancel_bot_owned_pending_entry_orders(' not in body
    assert 'refresh_recent_executions(connection, key, secret)' in body
    assert 'resolve_prestart_entry_commands(connection)' in body

def test_unified_r1_read_model_is_visible_but_never_implicitly_armed(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from bybit_workbench import r1_micro_live_control as rail
    from bybit_workbench.universal_entry.r1_strategy import (
        build_r1_unified_card,
    )

    card = build_r1_unified_card()
    release = "a" * 40
    activation = "active-r1-five"
    trading_account = "trading-account-proven"
    seen = []

    class Rows:
        def __init__(self, values):
            self.values = values

        def fetchone(self):
            return self.values[0] if self.values else None

        def fetchall(self):
            return list(self.values)

    class ReadOnlyDB:
        def execute(self, statement, args=()):
            seen.append(statement)
            if "FROM control.execution_gates" in statement:
                return Rows([(0, "private WS reconnect: verification pending")])
            if "FROM strategy_entry.strategy_cards" in statement:
                return Rows([(card.strategy_config_fingerprint,)])
            if "FROM strategy_entry.strategy_activations" in statement:
                if "count(*)" in statement:
                    return Rows([(1,)])
                if "strategy_id=%s AND strategy_version=%s" in statement:
                    if args[0] != card.strategy_id:
                        return Rows([])
                    return Rows([(activation, card.strategy_config_fingerprint)])
                return Rows([])
            if "FROM strategy_entry.strategy_execution_bindings" in statement:
                return Rows([(
                    trading_account, "PRIMARY", "PRIMARY",
                    "DEDICATED_STRATEGY",
                )])
            if "FROM strategy_entry.execution_permissions" in statement:
                return Rows([])
            if "FROM control.live_arm_sessions" in statement:
                return Rows([(0,)])
            raise AssertionError("Unexpected read-model SQL: " + statement)

    monkeypatch.setattr(
        rail,
        "evaluate_live_arm",
        lambda *a, **k: SimpleNamespace(
            ready=False, failed_codes=("PAPER_REAL_DECISION_PARITY",)
        ),
    )
    result = rail.micro_live_state(
        ReadOnlyDB(),
        release_commit=release,
        now=datetime(2026, 10, 21, tzinfo=UTC),
    )
    assert result["installed"] is True
    assert result["cohort"] == "R1_UNIFIED_FIVE_SYMBOLS"
    assert result["binding_verified"] is True
    assert result["trading_account_id"] == trading_account
    assert result["arm_supported"] is False
    assert result["ready_to_arm"] is False
    assert result["armed"] is False
    assert len(result["strategies"]) == 5
    assert all(not item["execution_permission"] for item in result["strategies"])
    assert all(not item["pre_owner_ready"] for item in result["strategies"])
    assert "R1_UNIFIED_MICRO_LIVE_FINANCIAL_LIMIT_OWNER_DECISION_REQUIRED" in (
        result["reasons"]
    )
    assert all(not sql.lstrip().upper().startswith(
        ("UPDATE", "INSERT", "DELETE")
    ) for sql in seen)


def test_unified_r1_has_no_automatic_legacy_arm_or_gate_restore():
    from pathlib import Path

    src = Path("src/bybit_workbench/r1_micro_live_control.py").read_text(
        encoding="utf-8"
    )
    branch = src.split("def unified_r1_micro_live_state(", 1)[1].split(
        "\ndef micro_live_state(", 1
    )[0]
    assert "UPDATE " not in branch
    assert "INSERT INTO " not in branch
    assert '"ready_to_arm": False' in branch
    assert '"arm_supported": False' in branch
    assert "R1_UNIFIED_OWNER_ARM_RAIL_NOT_IMPLEMENTED" in branch
    assert "R1_UNIFIED_MICRO_LIVE_FINANCIAL_LIMIT_OWNER_DECISION_REQUIRED" in branch
