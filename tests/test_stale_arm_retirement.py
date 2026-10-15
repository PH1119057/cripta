from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bybit_workbench.stale_arm_retirement import retire_stale_release_arms

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
CURRENT = "a" * 40
PRIOR = "b" * 40


class Cursor:
    def __init__(self, rows=(), rowcount=1):
        self.rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    def __init__(self, *, gate=False, blocked=False):
        self.gate = gate
        self.blocked = blocked
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        if "FROM control.execution_gates" in sql:
            return Cursor([{"enabled": self.gate}])
        if "FROM control.live_arm_sessions" in sql:
            return Cursor([{
                "live_arm_session_id": "session-a",
                "strategy_id": "r1_aptusdt",
                "strategy_version": "1.1-micro-live",
                "symbol": "APTUSDT",
                "release_commit": PRIOR,
            }])
        if "SELECT (" in sql:
            return Cursor([(self.blocked,)])
        return Cursor()


def snapshot(*, positions=None, orders=None, observed=NOW):
    return {
        "source": "BYBIT_AUTHENTICATED_ACCOUNT_GET",
        "account_ref": "BYBIT:UNIFIED",
        "account_user_id": "account-identity-present",
        "complete": True,
        "observed_at": observed.isoformat(),
        "symbols": {
            "APTUSDT": {
                "positions": [] if positions is None else positions,
                "orders": [] if orders is None else orders,
            }
        },
    }


def test_safe_flat_scope_retires_old_authorization_not_rearms():
    c = FakeDB()
    result = retire_stale_release_arms(
        c, loaded_commit=CURRENT, account_snapshot=snapshot(), now=NOW
    )
    assert result == ("session-a",)
    joined = "\n".join(sql for sql, _ in c.statements)
    assert "UPDATE control.live_arm_sessions" in joined
    assert "state='CLOSED'" in joined
    assert "control.live_arm_retirement_events" in joined
    assert "enabled=true" not in joined
    assert "INSERT INTO control.live_arm_sessions" not in joined
    assert "UPDATE strategy_entry.execution_permissions" not in joined
    assert "UPDATE control.execution_gates" not in joined


def test_fail_closed_when_gate_open():
    c = FakeDB(gate=True)
    assert not retire_stale_release_arms(
        c, loaded_commit=CURRENT, account_snapshot=snapshot(), now=NOW
    )
    assert not any("UPDATE" in sql for sql, _ in c.statements)


def test_fail_closed_when_exchange_has_position():
    c = FakeDB()
    assert not retire_stale_release_arms(
        c, loaded_commit=CURRENT,
        account_snapshot=snapshot(positions=[{"size": "0.4"}]), now=NOW
    )
    assert not any("UPDATE" in sql for sql, _ in c.statements)


def test_fail_closed_when_exchange_has_order_or_local_pending():
    for snap, blocked in (
        (snapshot(orders=[{"orderId": "pending"}]), False),
        (snapshot(), True),
    ):
        c = FakeDB(blocked=blocked)
        assert not retire_stale_release_arms(
            c, loaded_commit=CURRENT, account_snapshot=snap, now=NOW
        )
        assert not any("UPDATE" in sql for sql, _ in c.statements)


def test_fail_closed_when_account_is_not_fresh_or_complete():
    for snap in (
        snapshot(observed=NOW - timedelta(seconds=16)),
        {**snapshot(), "complete": False},
        {**snapshot(), "account_user_id": ""},
        {**snapshot(), "symbols": {}},
    ):
        c = FakeDB()
        assert not retire_stale_release_arms(
            c, loaded_commit=CURRENT, account_snapshot=snap, now=NOW
        )
        assert not any("UPDATE" in sql for sql, _ in c.statements)


def test_runtime_supervisor_audits_all_active_arm_symbols():
    from pathlib import Path

    source = Path(
        "operations/monitoring/lifecycle_supervisor_runtime.py"
    ).read_text(encoding="utf-8")
    assert "SELECT DISTINCT symbol FROM control.live_arm_sessions" in source
    assert "retire_stale_release_arms(" in source
    assert "check_release_arm_invariant(" in source
    assert "if retired_arms:" in source
