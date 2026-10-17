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
    def __init__(self, *, gate=False, blocked=False, bound_account="ta-current"):
        self.gate = gate
        self.blocked = blocked
        self.bound_account = bound_account
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
                "strategy_activation_id": "activation-a",
                "strategy_execution_binding_id": "binding-a",
                "trading_account_id": self.bound_account,
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
        c, loaded_commit=CURRENT, account_snapshot=snapshot(), trading_account_id="ta-current", now=NOW
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
        c, loaded_commit=CURRENT, account_snapshot=snapshot(), trading_account_id="ta-current", now=NOW
    )
    assert not any(sql.lstrip().startswith("UPDATE ") for sql, _ in c.statements)


def test_fail_closed_when_exchange_has_position():
    c = FakeDB()
    assert not retire_stale_release_arms(
        c, loaded_commit=CURRENT,
        account_snapshot=snapshot(positions=[{"size": "0.4"}]),
        trading_account_id="ta-current", now=NOW
    )
    assert not any(sql.lstrip().startswith("UPDATE ") for sql, _ in c.statements)


def test_fail_closed_when_exchange_has_order_or_local_pending():
    for snap, blocked in (
        (snapshot(orders=[{"orderId": "pending"}]), False),
        (snapshot(), True),
    ):
        c = FakeDB(blocked=blocked)
        assert not retire_stale_release_arms(
            c, loaded_commit=CURRENT, account_snapshot=snap, trading_account_id="ta-current", now=NOW
        )
        assert not any(sql.lstrip().startswith("UPDATE ") for sql, _ in c.statements)


def test_fail_closed_when_account_is_not_fresh_or_complete():
    for snap in (
        snapshot(observed=NOW - timedelta(seconds=16)),
        {**snapshot(), "complete": False},
        {**snapshot(), "account_user_id": ""},
        {**snapshot(), "symbols": {}},
    ):
        c = FakeDB()
        assert not retire_stale_release_arms(
            c, loaded_commit=CURRENT, account_snapshot=snap, trading_account_id="ta-current", now=NOW
        )
        assert not any(sql.lstrip().startswith("UPDATE ") for sql, _ in c.statements)


def test_runtime_supervisor_audits_all_active_arm_symbols():
    from pathlib import Path

    source = Path(
        "operations/monitoring/lifecycle_supervisor_runtime.py"
    ).read_text(encoding="utf-8")
    assert "SELECT DISTINCT symbol FROM control.live_arm_sessions" in source
    assert "retire_stale_release_arms(" in source
    assert "check_release_arm_invariant(" in source
    assert "if retired_arms:" in source


def test_retirement_uses_only_approved_migration_and_release_path():
    from pathlib import Path

    migration = Path(
        "operations/sql/20261015_live_arm_retirement_events_v1.sql"
    ).read_text(encoding="utf-8")
    installer = Path(
        "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert "control.live_arm_retirement_events" in migration
    assert "GRANT SELECT,INSERT" in migration
    assert "20261015_live_arm_retirement_events_v1.sql" in installer


def test_bybit_clock_failure_explains_network_samples_not_host_clock():
    from pathlib import Path

    runtime = Path(
        "operations/connectivity/private_runtime.py"
    ).read_text(encoding="utf-8")
    assert "BYBIT_TIME_PROBE_MAX_RTT_MS = 1000.0" in runtime
    assert "BYBIT_TIME_PROBE_ATTEMPTS = 3" in runtime
    assert "probe_rtts_ms=" in runtime
    assert "path=/v5/market/time" in runtime
    assert "UNSAFE_BYBIT_CLOCK_OFFSET" not in runtime


def test_fail_closed_when_arm_binding_belongs_to_other_account():
    c = FakeDB(bound_account="ta-other")
    assert not retire_stale_release_arms(
        c,
        loaded_commit=CURRENT,
        account_snapshot=snapshot(),
        trading_account_id="ta-current",
        now=NOW,
    )
    assert not any(sql.lstrip().startswith("UPDATE ") for sql, _ in c.statements)


def test_retirement_scopes_local_blockers_to_exact_proven_account():
    c = FakeDB()
    retire_stale_release_arms(
        c,
        loaded_commit=CURRENT,
        account_snapshot=snapshot(),
        trading_account_id="ta-current",
        now=NOW,
    )
    joined = "\n".join(sql for sql, _ in c.statements)
    assert "strategy_execution_bindings" in joined
    assert "b.execution_leg='PRIMARY'" in joined
    assert "b.allocation_mode='DEDICATED_STRATEGY'" in joined
    assert joined.count("trading_account_id=%s") >= 4
    assert "LEGACY_UNKNOWN" not in joined
    assert joined.count("identity_status='PROVEN'") >= 4
    params = [params for sql, params in c.statements if "SELECT (" in sql][0]
    assert params.count("ta-current") == 4


def test_runtime_passes_exact_trading_account_to_stale_arm_retirement():
    from pathlib import Path

    source = Path(
        "operations/monitoring/lifecycle_supervisor_runtime.py"
    ).read_text(encoding="utf-8")
    assert "trading_account_id=TRADING_ACCOUNT_ID" in source
