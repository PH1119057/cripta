"""Disposable PG16 proof for least-privilege passive MAIN/SUB account binding."""
from __future__ import annotations

import os
from datetime import UTC, datetime

import psycopg
import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import (
    MAIN_ID,
    SUB_ID,
    build_passive_legacy_cards,
)
from bybit_workbench.universal_entry.storage import StrategyEntryStore

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="requires explicitly disposable PG16 CI database"
)
WHEN = datetime(2026, 10, 27, 19, 0, tzinfo=UTC)
PAIRS = {
    MAIN_ID: (
        "MAIN", "ta-2122c81ecff14f3b85b102db605212f7",
        "ac-ed73de20410548d6841aeafa5f196f70",
        "activation-p90l90-legacy-main-20261027-v1",
        "binding-p90l90-legacy-main-20261027-v1",
    ),
    SUB_ID: (
        "SUB", "ta-853ac5fcd58185697396d33cd57459fa",
        "ac-ec57819f875f1bfd929a382d6dc8a125",
        "activation-p90l90-legacy-sub-20261027-v1",
        "binding-p90l90-legacy-sub-20261027-v1",
    ),
}


def test_exact_inactive_binding_via_restricted_security_definer() -> None:
    # Context itself rolls back all prepared data; this is NEVER production.
    with psycopg.connect(DSN, autocommit=False) as conn:
        with pytest.raises(RuntimeError, match="ROLLBACK_INTENTIONAL"):
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                grant = conn.execute(
                    """SELECT
                        has_function_privilege(
                          'cripta',
                          'strategy_entry.register_p90l90_inactive_binding(text,text,text)',
                          'EXECUTE'),
                        has_table_privilege(
                          'cripta', 'strategy_entry.strategy_execution_bindings',
                          'INSERT'),
                        pg_has_role(
                          'cripta','cripta_p90l90_passive_binding','MEMBER')"""
                ).fetchone()
                assert grant == (True, False, False)
                conn.execute(
                    "UPDATE control.execution_gates SET enabled=0 "
                    "WHERE mode IN ('mainnet','testnet')"
                )
                assert conn.execute(
                    "SELECT count(*) FROM strategy_entry.strategy_activations "
                    "WHERE enabled=true"
                ).fetchone() == (0,)
                assert conn.execute(
                    "SELECT count(*) FROM strategy_entry.execution_permissions "
                    "WHERE enabled=true"
                ).fetchone() == (0,)
                assert conn.execute(
                    "SELECT count(*) FROM control.live_arm_sessions "
                    "WHERE state='ACTIVE'"
                ).fetchone() == (0,)
                for _, (kind, ta, connector, _, _) in PAIRS.items():
                    conn.execute(
                        """INSERT INTO identity.trading_accounts (
                           trading_account_id,venue_id,account_kind,account_type,
                           environment,status,external_account_ref
                        ) VALUES (
                           %s,'venue-bybit',%s,'UNIFIED','MAINNET','ACTIVE',
                           '9000000000'
                        )""", (ta, kind)
                    )
                    conn.execute(
                        """INSERT INTO identity.account_connectors (
                           account_connector_id,trading_account_id,
                           connector_kind,adapter_id,credential_ref,status
                        ) VALUES (
                           %s,%s,'CEX_PRIVATE_RUNTIME','BYBIT_V5_PRIVATE',
                           'disposable-fixture','ACTIVE'
                        )""", (connector, ta)
                    )
                store = StrategyEntryStore(conn)
                for card in build_passive_legacy_cards(approved_at=WHEN):
                    _, _, _, act_id, _ = PAIRS[card.strategy_id]
                    act = StrategyActivation(
                        activation_id=act_id,
                        strategy_id=card.strategy_id,
                        strategy_version=card.strategy_version,
                        strategy_config_fingerprint=card.strategy_config_fingerprint,
                        enabled=False,
                        enabled_at=WHEN,
                        disabled_at=WHEN,
                        scope=FrozenPolicy.from_mapping({"mode":"EXACT_STRATEGY_VERSION"}),
                        operator="owner",
                        source="disposable-ci",
                    )
                    entry, exit_plan = materialize_plans(card, act)
                    store.insert_strategy_card(card)
                    store.insert_activation(act, reason="disposable-ci")
                    store.insert_entry_plan(entry)
                    store.insert_exit_plan(exit_plan)
                # Switch actual SESSION user, not only SET ROLE, to test the
                # SECURITY DEFINER caller boundary under the application role.
                conn.execute("SET SESSION AUTHORIZATION cripta")
                assert conn.execute("SELECT session_user").fetchone() == ("cripta",)
                for _, (_, ta, _, act_id, binding_id) in PAIRS.items():
                    value = conn.execute(
                        "SELECT strategy_entry.register_p90l90_inactive_binding(%s,%s,%s)",
                        (binding_id,act_id,ta),
                    ).fetchone()
                    assert value == (binding_id,)
                bound = conn.execute(
                    """SELECT a.strategy_id,a.enabled,b.binding_state,b.trading_account_id
                         FROM strategy_entry.strategy_execution_bindings b
                         JOIN strategy_entry.strategy_activations a
                           ON a.activation_id=b.strategy_activation_id
                         WHERE a.strategy_id IN (%s,%s)
                         ORDER BY a.strategy_id""",
                    (MAIN_ID,SUB_ID),
                ).fetchall()
                assert len(bound) == 2
                assert all(enabled is False and state == "INACTIVE"
                           for _, enabled, state, _ in bound)
                assert {r[3] for r in bound} == {t[1] for t in PAIRS.values()}
                with pytest.raises(psycopg.errors.RaiseException):
                    with conn.transaction():  # rollback negative assertion only
                        conn.execute(
                            "SELECT strategy_entry.register_p90l90_inactive_binding(%s,%s,%s)",
                            (PAIRS[MAIN_ID][4],PAIRS[MAIN_ID][3],PAIRS[SUB_ID][1]),
                        )
                assert conn.execute(
                    "SELECT count(*) FROM strategy_entry.strategy_execution_bindings "
                    "WHERE strategy_execution_binding_id=%s",
                    (PAIRS[MAIN_ID][4],),
                ).fetchone() == (1,)
                raise RuntimeError("ROLLBACK_INTENTIONAL")
