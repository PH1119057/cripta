from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.r1_strategy import build_r1_unified_card

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "a" * 40
NOW = datetime(2026, 10, 21, 17, 0, tzinfo=UTC)
CARD = build_r1_unified_card()


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class ReadOnlyDB:
    def __init__(self):
        self.query_log: list[str] = []
        self.active_count = 1
        self.binding_role = "PRIMARY"
        self.binding_count = 1
        self.account_env = "MAINNET"
        self.gate_enabled = 0
        self.active_permissions = False
        self.active_sessions = False
        self.card_fingerprint = CARD.strategy_config_fingerprint

    def execute(self, statement, args=()):
        self.query_log.append(statement)
        if "FROM strategy_entry.strategy_cards" in statement:
            return Cursor([(self.card_fingerprint,)])
        if "FROM strategy_entry.strategy_activations" in statement:
            if "count(*)" in statement:
                return Cursor([(self.active_count,)])
            if args[:2] == (CARD.strategy_id, CARD.strategy_version):
                return Cursor([("activation-unified-r1", self.card_fingerprint)])
            return Cursor([])
        if "FROM strategy_entry.strategy_execution_bindings" in statement:
            return Cursor([
                (
                    "one-account", "PRIMARY", self.binding_role,
                    "DEDICATED_STRATEGY", datetime(2026, 10, 20, tzinfo=UTC),
                    None, "ACTIVE", self.account_env,
                )
            ] if self.binding_count else [])
        if "FROM control.execution_gates" in statement:
            return Cursor([(self.gate_enabled, "private reconnect")])
        if "FROM strategy_entry.execution_permissions" in statement:
            if "strategy_id=%s" in statement:
                return Cursor([(1,)] if self.active_permissions else [])
            return Cursor([("unknown", "1")] if self.active_permissions else [])
        if "FROM control.live_arm_sessions" in statement:
            return Cursor([("other", "1", "APTUSDT")] if self.active_sessions else [])
        raise AssertionError("Unexpected query: " + statement)


def test_one_strategy_one_account_five_symbol_prearm_scopes(monkeypatch):
    import bybit_workbench.r1_unified_prearm as prearm

    db = ReadOnlyDB()
    monkeypatch.setattr(
        "bybit_workbench.r1_unified_prearm.evaluate_live_arm",
        lambda *args, **kwargs: SimpleNamespace(
            ready=True, failed_codes=()
        ),
    )
    result = prearm.unified_r1_prearm_state(
        db, release_commit=RELEASE, now=NOW
    )
    assert result["installed"] is True
    assert result["binding_verified"] is True
    assert result["trading_account_id"] == "one-account"
    assert result["technical_pre_owner_ready"] is True
    assert len(result["strategies"]) == 1
    strategy = result["strategies"][0]
    assert strategy["strategy_id"] == "r1_five_symbols"
    assert strategy["directional_monitor_lanes"] == 10
    assert set(strategy["symbols"]) == set(CARD.symbols)
    assert len(result["instrument_scopes"]) == 5
    assert {scope["symbol"] for scope in result["instrument_scopes"]} == set(
        CARD.symbols
    )
    assert result["ready_to_arm"] is True
    assert result["arm_route_enabled"] is True
    assert result["armed"] is False
    assert "EXPLICIT_OWNER_ARM_NOT_GRANTED" in result["reasons"]
    assert all(
        command.lstrip().upper().startswith("SELECT")
        for command in db.query_log
    )


@pytest.mark.parametrize(
    ("attribute", "value", "reason"),
    [
        ("active_count", 2, "OTHER_ACTIVE_STRATEGY_ACTIVATION"),
        ("binding_role", "SECONDARY", "PRIMARY_TRADING_ACCOUNT_BINDING_NOT_PROVEN"),
        ("binding_count", 0, "PRIMARY_TRADING_ACCOUNT_BINDING_NOT_UNIQUE"),
        ("account_env", "TESTNET", "PRIMARY_TRADING_ACCOUNT_BINDING_NOT_PROVEN"),
        ("card_fingerprint", "wrong", "UNIFIED_R1_IMMUTABLE_CARD_MISMATCH"),
    ],
)
def test_prearm_refuses_ambiguous_identity(attribute, value, reason):
    import bybit_workbench.r1_unified_prearm as prearm

    db = ReadOnlyDB()
    setattr(db, attribute, value)
    result = prearm.unified_r1_prearm_state(
        db, release_commit=RELEASE, now=NOW
    )
    assert result["installed"] is False
    assert reason in result["reasons"]
    assert result["ready_to_arm"] is False


def test_unified_readiness_refuses_preexisting_gate_permission_and_arms(monkeypatch):
    import bybit_workbench.r1_unified_prearm as prearm

    db = ReadOnlyDB()
    db.gate_enabled = 1
    db.active_permissions = True
    db.active_sessions = True
    monkeypatch.setattr(
        "bybit_workbench.r1_unified_prearm.evaluate_live_arm",
        lambda *a, **k: SimpleNamespace(
            ready=False, failed_codes=("CRITICAL_FAULT_DELIVERY",)
        ),
    )
    result = prearm.unified_r1_prearm_state(
        db, release_commit=RELEASE, now=NOW
    )
    assert len(result["strategies"]) == 1
    assert len(result["instrument_scopes"]) == 5
    assert result["technical_pre_owner_ready"] is False
    assert "MAINNET_GATE_NOT_CLOSED" in result["reasons"]
    assert "EXECUTION_PERMISSION_ALREADY_ENABLED" in result["reasons"]
    assert "ACTIVE_OR_LEGACY_ARM_SESSION_REQUIRES_REVIEW" in result["reasons"]
    assert "CRITICAL_FAULT_DELIVERY" in result["reasons"]


def test_unified_arm_never_inherits_old_five_strategy_critical_delivery_waiver():
    source = (ROOT / "src/bybit_workbench/r1_unified_prearm.py").read_text(
        encoding="utf-8"
    )
    assert "OWNER_WAIVED_FOR_R1_MICRO_LIVE" not in source
    assert "_R1_MICRO_LIVE_STRATEGY_IDS" not in source
    assert "arm_route_enabled" in source
    assert "UPDATE control.execution_gates" not in source
    assert "INSERT INTO control.live_arm_sessions" not in source


def test_dashboard_exposes_authenticated_read_only_prearm_route():
    source = (ROOT / "operations/dashboard/app.py").read_text(
        encoding="utf-8"
    )
    assert 'R1_UNIFIED_PREARM_STATUS_PATH = "/api/r1/unified/prearm-status"' in source
    assert "state = unified_r1_prearm_state(" in source
    handler = source.split("    def do_GET(self)", 1)[1].split(
        "    def do_POST(self)", 1
    )[0]
    assert "elif self.require_login():" in handler
    assert "elif path == R1_UNIFIED_PREARM_STATUS_PATH:" in handler
    assert handler.index("elif self.require_login():") < handler.index(
        "elif path == R1_UNIFIED_PREARM_STATUS_PATH:"
    )
    assert "R1_UNIFIED_PREARM_STATUS_PATH," not in source.split(
        "U6_STRATEGY_POST_PATHS =", 1
    )[1].split("}", 1)[0]


