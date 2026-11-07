"""Static boundary tests for the bounded 24-hour full-telemetry retention.

A production apply also requires PostgreSQL integration, live readers, backup,
release and causal equivalence tests. These checks alone don't authorize deploy.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'operations/monitoring/market_hot_retention.py'
SPEC = importlib.util.spec_from_file_location('market_hot_retention', SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_exact_target_scope_and_interval():
    assert MODULE.RETENTION_HOURS == 24
    assert MODULE.DEFAULT_BATCH_LIMIT < MODULE.MAX_BATCH_LIMIT
    assert set(MODULE.EXPECTED_TRIGGERS) == {
        'dispatcher_v2.coin_market_contexts',
        'mayak_v2.coin_market_contexts',
        'dispatcher_v2.global_market_contexts',
        'mayak_v2.shared_market_contexts',
    }
    assert set(dict(MODULE.DELETES)) == set(MODULE.EXPECTED_TRIGGERS)
    assert len(MODULE.DELETES) == 4


def test_foreign_key_dependency_order_pinned_audit():
    tables = [table for table, _ in MODULE.DELETES]
    assert tables == [
        'dispatcher_v2.coin_market_contexts',
        'mayak_v2.coin_market_contexts',
        'dispatcher_v2.global_market_contexts',
        'mayak_v2.shared_market_contexts',
    ]
    for table, pred in MODULE.DELETES:
        sql = MODULE.delete_sql(table, pred)
        assert 'NOT EXISTS' in sql
        assert 'FOR UPDATE SKIP LOCKED' in sql
        assert 'LIMIT %s' in sql
        assert 'DELETE FROM ' + table in sql
        assert 'CASCADE' not in sql
    assert 'research_context.dispatcher_v2_event_links' in MODULE.DELETES[0][1]
    assert 'research_context.dispatcher_v2_event_links' in MODULE.DELETES[2][1]


def test_snapshot_id_and_observation_state_persist():
    sql = MODULE.compact_snapshot_sql()
    assert 'UPDATE mayak_v2.snapshots target SET payload=' in sql
    assert 'DELETE FROM mayak_v2.snapshots' not in sql
    assert 'research_context.event_links' in sql
    assert 'mayak_v2.events' in sql
    assert 'mayak_v2.coin_market_contexts' in sql
    assert 'dispatcher_v2.coin_market_contexts' in sql
    assert 'dispatcher_v2.global_market_contexts' in sql
    assert 'mayak_v2.shared_market_contexts' in sql


def test_journal_compaction_preserves_scalar_and_reasons():
    sql = MODULE.compact_journal_sql()
    assert 'UPDATE mayak_v2.observation_journal' in sql
    assert 'SET price_breadth=' in sql
    assert 'market_state=' not in sql
    assert 'confidence=' not in sql
    assert 'reasons=' not in sql
    assert 'research_context.event_links' in sql


def test_unsupported_table_fail_closed():
    with pytest.raises(ValueError, match='NOT_ALLOWLISTED'):
        MODULE.delete_sql('runtime.hot_positions', 'TRUE')


def test_systemd_unit_is_nontrading_postgres_only_and_bounded():
    service = (ROOT/'operations/systemd/cripta-market-hot-retention.service').read_text()
    timer = (ROOT/'operations/systemd/cripta-market-hot-retention.timer').read_text()
    assert 'User=postgres' in service
    assert 'Group=postgres' in service
    assert '--apply --batch-limit 1000' in service
    assert 'ProtectSystem=strict' in service
    assert 'ExecStart=/usr/bin/python3 -B /usr/local/libexec/cripta-market-hot-retention.py' in service
    assert 'OnCalendar=*:0/10' in timer
    assert 'Unit=cripta-market-hot-retention.service' in timer


def test_triggers_must_remain_enabled_after_apply_codepath():
    text = SOURCE.read_text()
    assert 'ALTER TABLE {table} DISABLE TRIGGER {trigger}' in text
    assert 'ALTER TABLE {table} ENABLE TRIGGER {trigger}' in text
    assert 'check_triggers(connection)' in text
    assert 'pg_try_advisory_xact_lock' in text
    for target in ('runtime.executions', 'runtime.hot_positions', 'runtime.hot_orders',
                   'analytics.market_minutes', 'control.execution_gates'):
        assert f'DELETE FROM {target}' not in text
        assert f'UPDATE {target}' not in text


def test_immutable_trigger_schema_drift_fails_closed():
    class ChangedSchema:
        def execute(self, _sql, _params):
            return self

        def fetchall(self):
            return []

    with pytest.raises(RuntimeError, match="TRIGGER_CONTRACT_CHANGED"):
        MODULE.check_triggers(ChangedSchema())


def test_non_postgres_actor_cannot_apply_without_db_writes(monkeypatch):
    if sys.platform == "linux":
        monkeypatch.setattr(MODULE.os, "geteuid", lambda: -1)
        message = "RETENTION_POSTGRES_UNIX_ACTOR_REQUIRED"
    else:
        message = "RETENTION_LINUX_REQUIRED"
    with pytest.raises(RuntimeError, match=message):
        MODULE.run_apply(None, None, 1)


def test_historical_report_distinguishes_policy_expiration():
    report = (ROOT / "operations/monitoring/mayak_v2_report.py").read_text()
    assert "EXPIRED_HOT_TELEMETRY" in report
    assert "payload->>'_cripta_hot_telemetry'='EXPIRED_24H'" in report


def test_release_installs_inert_units_and_preserves_activation_state():
    installer = (ROOT / "operations/infrastructure/install_verified_release.sh").read_text()
    assert "operations/systemd/cripta-market-hot-retention.service|" in installer
    assert "operations/systemd/cripta-market-hot-retention.timer|" in installer
    assert "cripta-market-hot-retention.service" in installer
    assert "cripta-market-hot-retention.timer" in installer
    assert "systemctl enable cripta-market-hot-retention.timer" not in installer
