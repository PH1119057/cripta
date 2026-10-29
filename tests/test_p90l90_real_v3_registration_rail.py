"""Fail-closed v3 authoring rail, independent from historical PAPER v2.

This test does not touch production PostgreSQL, alter roles or run Bybit.
Actual migration and installer still require separate actor/DB preflight.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "operations/sql/20261029_p90l90_real_v3_inactive_binding_writer.sql"
INSTALLER = ROOT / "operations/implementation/install_p90l90_real_v3_inactive.py"


def test_migration_grants_only_narrow_inactive_v3_writer() -> None:
    text = SQL.read_text(encoding="utf-8")
    assert text.startswith("-- Issue #243:")
    assert text.count(
        "CREATE OR REPLACE FUNCTION strategy_entry.register_p90l90_real_v3_inactive_binding("
    ) == 1
    assert "SECURITY DEFINER" in text
    assert "SET search_path = pg_catalog, pg_temp" in text
    assert "session_user <> 'cripta'" in text
    assert "current_setting('transaction_isolation') <> 'serializable'" in text
    assert "P90_REAL_V3_EXISTING_WRITER_DRIFT" in text
    assert "v_activation:='activation-p90l90-real-main-20261029-v3'" in text
    assert "v_activation:='activation-p90l90-real-sub-20261029-v3'" in text
    assert "v_binding:='binding-p90l90-real-main-20261029-v3'" in text
    assert "v_binding:='binding-p90l90-real-sub-20261029-v3'" in text
    assert "v_kind:='MAIN';" in text and "v_kind:='SUB';" in text
    assert "v_symbols:='[\"INJUSDT\",\"LTCUSDT\"]'::jsonb;" in text
    assert "v_symbols:='[\"APTUSDT\",\"ARBUSDT\",\"DOTUSDT\"]'::jsonb;" in text
    assert "v_exit->'trailing'->>'distance_pct' IS DISTINCT FROM '0.35'" in text
    assert "v_exit->'trailing'->>'activation_profit_pct' IS DISTINCT FROM '0.5'" in text
    assert "'DEDICATED_STRATEGY','INACTIVE'" in text
    assert "'ACTIVE',transaction_timestamp()" not in text
    assert "GRANT INSERT ON strategy_entry.strategy_execution_bindings" in text
    assert "TO cripta_p90l90_real_v3_binding;" in text
    assert "REVOKE CREATE ON SCHEMA strategy_entry" in text
    assert "GRANT EXECUTE ON FUNCTION" in text
    assert "TO cripta;" in text
    assert "P90_REAL_V3_WRITER_POST_MIGRATION_PRIVILEGE_DRIFT" in text
    assert "DROP FUNCTION" not in text
    for prohibited in (
        "DROP FUNCTION", "TRUNCATE strategy_entry",
        "UPDATE strategy_entry.strategy_activations",
        "UPDATE control.execution_gates",
        "INSERT INTO strategy_entry.execution_permissions",
        "UPDATE control.account_live_arm_sessions",
    ):
        assert prohibited not in text


def test_replay_hash_is_exact_function_source_and_migration_is_listed() -> None:
    text = SQL.read_text(encoding="utf-8")
    assert text.count("AS $function$\\n") == 1
    assert text.count("\\n$function$;") == 1
    body = text.split("AS $function$\\n", 1)[1].split(
        "\\n$function$;", 1,
    )[0]
    approved = hashlib.md5(("\\n" + body + "\\n").encode("utf-8")).hexdigest()
    assert approved == "efa9a3e3604b22133184f92b02bcb88f"
    assert text.count(approved) == 1
    release = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    current_sql = "operations/sql/20261028_shared_closed_candle_source.sql"
    new_sql = "operations/sql/20261029_p90l90_real_v3_inactive_binding_writer.sql"
    assert release.count(current_sql) == 1
    assert release.count(new_sql) == 1
    assert release.index(current_sql) < release.index(new_sql)
    assert release.index(new_sql) < release.index(
        'for migration in "${migration_files[@]}"; do'
    )


def test_installation_requires_exact_actor_and_no_default_mutation() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert tree is not None
    assert 'Path("/srv/cripta/runtime/current")' in source
    assert "os.geteuid() == pwd.getpwnam(\"cripta\").pw_uid" in source
    assert "(ROOT / \"INSTALLED_COMMIT\").read_text().strip() == commit" in source
    assert "SERIALIZABLE" in source
    assert "'READ ONLY' if dry_run" not in source  # uses double quotes
    assert '"READ ONLY" if dry_run else "READ WRITE"' in source
    assert "strategy_entry.register_p90l90_real_v3_inactive_binding" in source
    assert "store.insert_strategy_card(card)" in source
    assert "store.insert_activation(" in source
    assert "store.insert_entry_plan(entry)" in source
    assert "store.insert_exit_plan(exit_plan)" in source
    assert '"--check"' in source and '"--install"' in source
    assert "EXPLICIT_CHECK_OR_INSTALL_FLAG_REQUIRED" in source
    assert "enabled=False" in source and "disabled_at=at" in source
    assert "P90_REAL_V3_INSTALL=COMMITTED" in source
    assert "P90_REAL_V3_PREFLIGHT=PASS" in source
    assert "SELECT pg_advisory_xact_lock(20901290,243)" in source
    assert source.index("prepared = preflight(connection, now)") < source.index(
        "store.insert_strategy_card(card)"
    )
    for prohibited in (
        "os.system(", "subprocess.", "chmod(", "chown(",
        "INSERT INTO strategy_entry.strategy_execution_bindings",
        "UPDATE control.execution_gates",
        "UPDATE strategy_entry.execution_permissions",
        "set_activation_enabled(", "api_post(",
        "UPDATE control.account_live_arm_sessions",
    ):
        assert prohibited not in source


def test_immutable_v3_authoring_is_distinct_from_paper_v2() -> None:
    from datetime import UTC, datetime

    from bybit_workbench.universal_entry.p90l90_legacy_strategy import (
        MAIN_ID,
        SUB_ID,
    )
    from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
        VERSION, real_v3_cards,
    )
    from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
        StrategyHardStopEmbargoPolicy,
    )

    embargo = StrategyHardStopEmbargoPolicy.parse({
        "enabled": True, "hardstop_count": 2,
        "rolling_hours": 48, "new_entry_pause_hours": 48,
        "close_existing_positions": False, "domain": "STRATEGY_VERSION",
    })
    cards = real_v3_cards(
        approved_at=datetime(2026, 10, 29, tzinfo=UTC),
        policy_by_strategy_id={MAIN_ID: embargo, SUB_ID: embargo},
    )
    assert tuple(x.strategy_id for x in cards) == (MAIN_ID, SUB_ID)
    assert all(x.strategy_version == VERSION for x in cards)
    for card in cards:
        assert card.capital_policy.to_dict()["requested_amount"] == "10"
        assert card.capital_policy.to_dict()["margin_mode"] == "ISOLATED"
        assert card.entry_policy.to_dict()["execution_policy"]["order_type"] == "MARKET"
        assert card.exit_policy.to_dict()["trailing"]["distance_pct"] == "0.35"
        assert card.exit_policy.to_dict()["execution_policy"]["max_request_age_seconds"] == 5
