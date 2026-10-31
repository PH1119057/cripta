from __future__ import annotations

from datetime import UTC, datetime

from bybit_workbench.stale_arm_retirement import (
    RETIREMENT_REASON,
    retire_stale_release_arms,
)

NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
CURRENT = "a" * 40
OLD = "b" * 40


class Cursor:
    def __init__(self, rows=(), rowcount=1):
        self.rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self, *, gate=False, release=OLD, active=True):
        self.gate = gate
        self.release = release
        self.active = active
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        if "FROM control.execution_gates" in sql:
            return Cursor([{"enabled": self.gate}])
        if "FROM control.live_arm_sessions" in sql:
            if not self.active or self.release == params[0]:
                return Cursor()
            return Cursor([{
                "live_arm_session_id": "session-a",
                "strategy_id": "r1_aptusdt",
                "strategy_version": "1.1-micro-live",
                "symbol": "APTUSDT",
                "release_commit": self.release,
                "strategy_activation_id": "activation-a",
            }])
        if sql.lstrip().startswith("UPDATE control.live_arm_sessions"):
            self.active = False
            return Cursor(rowcount=1)
        if "INSERT INTO control.live_arm_retirement_events" in sql:
            return Cursor()
        raise AssertionError(f"unexpected inventory/binding SQL: {sql}")


def _writes(c):
    return [sql for sql, _ in c.statements if sql.lstrip().startswith(("UPDATE", "INSERT"))]


def test_superseded_arm_retired_without_inventory_or_account_binding():
    c = FakeDB()
    assert retire_stale_release_arms(c, loaded_commit=CURRENT, now=NOW) == ("session-a",)
    statements = "\n".join(sql for sql, _ in c.statements)
    assert "UPDATE control.live_arm_sessions" in statements
    assert "INSERT INTO control.live_arm_retirement_events" in statements
    assert "strategy_execution_bindings" not in statements
    assert "runtime.position_ownership" not in statements
    assert "runtime.trade_commands" not in statements
    assert "runtime.capital_reservations" not in statements
    assert "BYBIT" not in statements
    audit_params = next(p for sql, p in c.statements
                        if "INSERT INTO control.live_arm_retirement_events" in sql)
    assert RETIREMENT_REASON in audit_params
    assert '"trading_account_id": null' in audit_params[-1]
    assert '"exchange_flatness_not_required": true' in audit_params[-1]
    assert "INSERT INTO control.live_arm_sessions" not in statements
    assert "UPDATE control.execution_gates" not in statements


def test_gate_on_must_not_retire_historical_arm():
    c = FakeDB(gate=True)
    assert not retire_stale_release_arms(c, loaded_commit=CURRENT, now=NOW)
    assert not _writes(c)


def test_current_release_arm_is_not_retired():
    c = FakeDB(release=CURRENT)
    assert not retire_stale_release_arms(c, loaded_commit=CURRENT, now=NOW)
    assert c.active
    assert not _writes(c)


def test_repeated_lifecycle_supervisor_cycle_is_idempotent():
    c = FakeDB()
    assert retire_stale_release_arms(c, loaded_commit=CURRENT, now=NOW)
    assert not retire_stale_release_arms(c, loaded_commit=CURRENT, now=NOW)
    assert sum("INSERT INTO control.live_arm_retirement_events" in s
               for s, _ in c.statements) == 1


def test_invalid_release_identity_or_naive_timestamp_is_fail_closed():
    for sha in ("", "z" * 40, "short"):
        c = FakeDB()
        assert not retire_stale_release_arms(c, loaded_commit=sha, now=NOW)
        assert not c.statements
    c = FakeDB()
    assert not retire_stale_release_arms(
        c, loaded_commit=CURRENT, now=datetime(2026, 10, 21)
    )
    assert not c.statements


def test_supervisor_calls_retirement_without_bybit_or_binding_arguments():
    from pathlib import Path

    src = Path("operations/monitoring/lifecycle_supervisor_runtime.py").read_text(
        encoding="utf-8"
    )
    call = src.split("retire_stale_release_arms(", 1)[1].split(")", 1)[0]
    assert "loaded_commit=loaded_commit" in call
    assert "account_snapshot=" not in call
    assert "trading_account_id=" not in call
    assert "if retired_arms:" not in src
    assert "check_release_arm_invariant(" in src
    assert "require_entry_runtime_health=True" in src


def test_installer_does_not_touch_trading_control_or_exchange():
    from pathlib import Path

    installer = Path("operations/infrastructure/install_verified_release.sh").read_text(
        encoding="utf-8"
    )
    assert "UPDATE control.live_arm_sessions" not in installer
    assert "INSERT INTO control.live_arm_retirement_events" not in installer
    migration = Path("operations/sql/20261015_live_arm_retirement_events_v1.sql").read_text(
        encoding="utf-8"
    )
    assert "control.live_arm_retirement_events" in migration
    assert "GRANT SELECT,INSERT" in migration


def test_canon_expressly_supersedes_proven_flat_dependency():
    from pathlib import Path

    dev = Path("docs/DEVELOPMENT_RELEASE_RULES_RU.md").read_text(encoding="utf-8")
    tc = Path("docs/TRADING_CONTOUR_RU.md").read_text(encoding="utf-8")
    assert "SUPERSEDED by OWNER DECISION 2026-10-21" in dev
    assert "proven-flat criterion has no current normative effect" in dev
    assert "SUPERSEDED" in tc
    assert "explicit owner approval" in tc


def test_bybit_clock_failure_explains_network_samples_not_host_clock():
    from pathlib import Path

    runtime = Path("operations/connectivity/private_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "BYBIT_TIME_PROBE_MAX_RTT_MS = 1000.0" in runtime
    assert "BYBIT_TIME_PROBE_ATTEMPTS = 3" in runtime
    assert "probe_rtts_ms=" in runtime
    assert "path=/v5/market/time" in runtime
    assert "UNSAFE_BYBIT_CLOCK_OFFSET" not in runtime
