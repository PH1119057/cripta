from pathlib import Path

import pytest

from operations.dashboard import archive_v2

ROOT = Path(__file__).parents[1]
CORRELATOR = (ROOT / "operations/monitoring/dispatcher_v2_context_correlator.py").read_text(
    encoding="utf-8"
)
ANALYST = (ROOT / "operations/monitoring/m3_trade_analyst.py").read_text(encoding="utf-8")
AUDIT = (ROOT / "operations/monitoring/live_v1_session_audit.py").read_text(encoding="utf-8")
ARCHIVE = (ROOT / "operations/dashboard/archive_v2.py").read_text(encoding="utf-8")
MIGRATION = (
    ROOT / "operations/sql/20261016_report_replay_provenance_r5.sql"
).read_text(encoding="utf-8")
INSTALLER = (ROOT / "operations/infrastructure/install_verified_release.sh").read_text(
    encoding="utf-8"
)


def test_dispatcher_correlator_keeps_private_capacity_on_exact_proven_account() -> None:
    assert "dispatcher-v2-context-correlator-v2" in CORRELATOR
    assert "e.identity_status='PROVEN'" in CORRELATOR
    assert "trading_account_id=e.trading_account_id" in CORRELATOR
    assert "'trading_account_id',e.trading_account_id" in CORRELATOR
    assert "'identity_status',e.identity_status" in CORRELATOR


def test_execution_event_reference_and_joins_are_account_scoped_when_proven() -> None:
    assert "x.trading_account_id || ':' || x.exec_id" in CORRELATOR
    assert "c.trading_account_id=x.trading_account_id" in CORRELATOR
    assert "p.trading_account_id=x.trading_account_id" in CORRELATOR


def test_m3_analyst_never_crosses_private_identity_scope() -> None:
    assert "def private_identity(" in ANALYST
    assert "def identity_predicate(" in ANALYST
    assert 'identity_predicate(\n            "x", identity_status, trading_account_id' in ANALYST
    assert 'identity_predicate(\n            "c", identity_status, trading_account_id' in ANALYST
    assert 'identity_predicate(\n            "p", identity_status, trading_account_id' in ANALYST
    assert '"private_identity": {' in ANALYST


def test_historical_live_v1_audit_is_explicitly_legacy_only() -> None:
    assert AUDIT.count("identity_status='LEGACY_UNKNOWN'") >= 2
    assert AUDIT.count("trading_account_id IS NULL") >= 2
    assert '"private_identity_scope": "LEGACY_UNKNOWN_ONLY"' in AUDIT


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            {"identity_status": "PROVEN", "trading_account_id": "ta-current"},
            ("PROVEN", "ta-current"),
        ),
        (
            {"identity_status": "LEGACY_UNKNOWN", "trading_account_id": None},
            ("LEGACY_UNKNOWN", None),
        ),
    ],
)
def test_archive_identity_validator_accepts_only_consistent_rows(row, expected) -> None:
    assert archive_v2._validate_account_identity(row) == expected


@pytest.mark.parametrize(
    "row",
    [
        {"identity_status": "PROVEN", "trading_account_id": None},
        {"identity_status": "LEGACY_UNKNOWN", "trading_account_id": "ta-current"},
        {"identity_status": "UNKNOWN", "trading_account_id": None},
    ],
)
def test_archive_identity_validator_rejects_mixed_or_unknown_provenance(row) -> None:
    with pytest.raises(RuntimeError):
        archive_v2._validate_account_identity(row)


def test_archive_preserves_all_current_private_identity_evidence() -> None:
    tables = {item.table for item in archive_v2.TABLE_EXPORTS}
    expected = {
        "runtime.account_state_generations",
        "runtime.position_mode_states",
        "runtime.capital_reservations",
        "runtime.exchange_position_slot_claims",
        "runtime.executions",
        "runtime.private_events",
        "runtime.trade_commands",
        "runtime.hot_orders",
        "runtime.hot_positions",
        "runtime.wallet_latest",
        "runtime.position_ownership",
        "runtime.exchange_order_history",
        "dispatcher_v2.trading_capacity_snapshots",
        "strategy_entry.entry_decisions",
        "strategy_entry.execution_requests",
        "strategy_exit.execution_requests",
        "strategy_entry.strategy_execution_bindings",
    }
    assert expected <= tables
    identity_expected = expected - {"strategy_entry.strategy_execution_bindings"}
    assert identity_expected <= archive_v2.ACCOUNT_IDENTITY_EXPORTS
    assert '"identity_provenance"' in ARCHIVE


def test_derived_event_link_migration_is_additive_and_never_backfills_account() -> None:
    assert "ADD COLUMN IF NOT EXISTS trading_account_id text" in MIGRATION
    assert "identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN'" in MIGRATION
    assert "REFERENCES identity.trading_accounts(trading_account_id)" in MIGRATION
    assert "UPDATE research_context.dispatcher_v2_event_links" not in MIGRATION
    assert "DELETE FROM" not in MIGRATION
    assert "DROP COLUMN" not in MIGRATION
    assert "20261016_report_replay_provenance_r5.sql" in INSTALLER


def test_no_physical_key_cutover_in_this_changeset() -> None:
    assert "DROP CONSTRAINT" not in MIGRATION
    assert "PRIMARY KEY" not in MIGRATION
    assert "UNIQUE INDEX" not in MIGRATION


def test_active_observer_capacity_is_scoped_to_current_trading_account() -> None:
    observer = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(
        encoding="utf-8"
    )
    assert "FROM dispatcher_v2.trading_capacity_snapshots" in observer
    assert "WHERE trading_account_id=%s" in observer
    assert "AND identity_status='PROVEN'" in observer
    assert "(TRADING_ACCOUNT_ID, observed_at)" in observer


def test_exact_close_audit_never_crosses_private_account_identity() -> None:
    audit = (ROOT / "operations/sql/p1_exact_close_readonly_audit.sql").read_text(
        encoding="utf-8"
    )
    assert "PARTITION BY identity_status,trading_account_id,symbol,position_idx" in audit
    assert "e.trading_account_id=o.trading_account_id" in audit
    assert "o.identity_status='LEGACY_UNKNOWN'" in audit
    assert "e.identity_status='LEGACY_UNKNOWN'" in audit
    assert "trading_account_id,identity_status" in audit
