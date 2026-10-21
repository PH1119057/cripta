"""Fail-closed contract tests for the one-time R1 activation transition."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.r1_strategy import build_r1_cards

SCRIPT = Path("operations/implementation/transition_r1_five_symbols.py")
SPEC = importlib.util.spec_from_file_location("transition_r1_five_symbols", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)


class Result:
    def __init__(self, rows=(), *, count=1):
        self.rows = tuple(rows)
        self.rowcount = count

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class DB:
    def __init__(self, *, gate=False, blockers=None, bad_fingerprint=False):
        self.gate = gate
        self.blockers = blockers or {}
        self.bad_fingerprint = bad_fingerprint
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append((sql, tuple(params)))
        normalized = " ".join(sql.split())
        if "FROM control.execution_gates" in sql:
            return Result([(self.gate,)])
        if "FROM strategy_entry.strategy_activations a" in sql:
            cards = build_r1_cards(approved_at=NOW)
            return Result([
                ("activation-" + c.strategy_id, c.strategy_id, c.strategy_version,
                 "bad" if self.bad_fingerprint else c.strategy_config_fingerprint)
                for c in cards
            ])
        if "SELECT count(*) FROM strategy_entry.strategy_cards" in normalized:
            return Result([(0,)])
        if normalized.startswith("SELECT count(*) FROM "):
            name = next((key for key, text in {
                "PENDING_PAPER_ORDER": "strategy_entry.paper_orders",
                "OPEN_PAPER_POSITION": "strategy_entry.paper_positions",
                "HOT_REAL_POSITION": "runtime.hot_positions",
                "HOT_REAL_ORDER": "runtime.hot_orders",
                "UNRESOLVED_TRADE_COMMAND": "runtime.trade_commands",
                "ACTIVE_CAPITAL_RESERVATION": "runtime.capital_reservations",
                "ACTIVE_POSITION_CLAIM": "runtime.exchange_position_slot_claims",
                "PREEXISTING_ACCOUNT_BINDING": "strategy_entry.strategy_execution_bindings",
            }.items() if text in normalized), None)
            assert name
            return Result([(self.blockers.get(name, 0),)])
        if normalized.startswith("UPDATE strategy_entry.execution_permissions"):
            return Result(count=5)
        if normalized.startswith("UPDATE strategy_entry.strategy_activations"):
            return Result(count=1)
        if normalized.startswith("INSERT INTO strategy_entry."):
            return Result(count=1)
        raise AssertionError("unexpected SQL: " + normalized)


def writes(db):
    return [s for s, _ in db.sql
            if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


def test_preflight_matches_five_exact_historical_activations_without_writes():
    db = DB()
    result = mod.preflight(db, NOW)
    assert len(result) == 5
    assert len({x[1] for x in result}) == 5
    assert not writes(db)


@pytest.mark.parametrize("blocker", (
    "PENDING_PAPER_ORDER", "OPEN_PAPER_POSITION", "HOT_REAL_POSITION",
    "HOT_REAL_ORDER", "UNRESOLVED_TRADE_COMMAND",
    "ACTIVE_CAPITAL_RESERVATION", "ACTIVE_POSITION_CLAIM",
    "PREEXISTING_ACCOUNT_BINDING",
))
def test_preflight_fails_before_any_mutation_for_unsettled_lifecycle(blocker):
    db = DB(blockers={blocker: 1})
    with pytest.raises(RuntimeError, match="R1_MIGRATION_BLOCKED"):
        mod.transition(db, now=NOW)
    assert not writes(db)


def test_preflight_fails_when_mainnet_is_open_or_legacy_id_mismatched():
    for db in (DB(gate=True), DB(bad_fingerprint=True)):
        with pytest.raises(RuntimeError, match="R1_MIGRATION_BLOCKED"):
            mod.transition(db, now=NOW)
        assert not writes(db)


def test_transaction_body_only_retires_old_entry_and_creates_new_plan():
    db = DB()
    receipt = mod.transition(db, now=NOW)
    assert receipt["legacy_activations_disabled"] == 5
    assert receipt["legacy_permissions_disabled"] == 5
    assert receipt["new_execution_permission"] == "OFF"
    assert receipt["new_strategy_id"] == "r1_five_symbols"
    stmt = "\n".join(writes(db))
    assert "UPDATE strategy_entry.execution_permissions" in stmt
    assert stmt.count("UPDATE strategy_entry.strategy_activations") == 5
    assert "INSERT INTO strategy_entry.strategy_cards" in stmt
    assert "INSERT INTO strategy_entry.entry_plans" in stmt
    assert "INSERT INTO strategy_entry.exit_plans" in stmt
    assert "INSERT INTO strategy_entry.strategy_activations" in stmt
    for forbidden in (
        "UPDATE strategy_entry.strategy_cards",
        "UPDATE strategy_entry.entry_plans",
        "UPDATE strategy_entry.exit_plans",
        "UPDATE strategy_entry.paper_positions",
        "UPDATE strategy_entry.paper_orders",
        "UPDATE runtime.",
        "UPDATE control.execution_gates",
        "INSERT INTO control.live_arm_sessions",
        "INSERT INTO strategy_entry.strategy_execution_bindings",
    ):
        assert forbidden not in stmt


def test_requires_quiesced_observer(monkeypatch):
    from subprocess import CompletedProcess

    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: CompletedProcess(a, 0, "active\n", ""))
    with pytest.raises(RuntimeError, match="observer must be stopped"):
        mod.require_quiesced_observer()


def test_fresh_exchange_snapshot_is_not_assumed(tmp_path, monkeypatch):
    file = tmp_path / "status.json"
    monkeypatch.setattr(mod, "LIFECYCLE_STATUS", file)
    with pytest.raises(RuntimeError, match="fresh account truth missing"):
        mod.require_fresh_exchange_account(NOW)
