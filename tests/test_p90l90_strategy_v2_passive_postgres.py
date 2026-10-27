"""Disposable PG16 proof of narrow v2 MAIN/SUB inactive bindings.

The postgres migration owner creates the function; application session cripta
can only EXECUTE the exact writer. Both immutable v1 records remain intact.
No production, Exchange or LIVE changes. All rows rollback at test end.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

import psycopg
import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import (
    MAIN_ID, SUB_ID, build_passive_legacy_cards,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards
from bybit_workbench.universal_entry.storage import StrategyEntryStore

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="requires disposable PG16")
AT = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)
VALUES = (
    (MAIN_ID,"MAIN","ta-2122c81ecff14f3b85b102db605212f7",
     "ac-ed73de20410548d6841aeafa5f196f70",
     "activation-p90l90-legacy-main-20261027-v1",
     "binding-p90l90-legacy-main-20261027-v1",
     "activation-p90l90-legacy-main-20261028-v2",
     "binding-p90l90-legacy-main-20261028-v2"),
    (SUB_ID,"SUB","ta-853ac5fcd58185697396d33cd57459fa",
     "ac-ec57819f875f1bfd929a382d6dc8a125",
     "activation-p90l90-legacy-sub-20261027-v1",
     "binding-p90l90-legacy-sub-20261027-v1",
     "activation-p90l90-legacy-sub-20261028-v2",
     "binding-p90l90-legacy-sub-20261028-v2"),
)


def test_v2_binding_only_exact_new_versions_with_old_v1_unchanged():
    embargo = StrategyHardStopEmbargoPolicy.parse({
        "enabled": True, "hardstop_count": 2,
        "rolling_hours": 48, "new_entry_pause_hours": 48,
        "close_existing_positions": False, "domain": "STRATEGY_VERSION",
    })
    with (
        psycopg.connect(DSN) as c,
        pytest.raises(RuntimeError, match="ROLLBACK_V2_PASSIVE"),
        c.transaction(),
    ):
        c.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        c.execute(
            """INSERT INTO control.execution_gates(
                 mode,enabled,reason,updated_at_epoch_ms)
               VALUES
                 ('mainnet',0,'p90-disposable-v2',0),
                 ('testnet',0,'p90-disposable-v2',0)
               ON CONFLICT(mode) DO UPDATE SET enabled=0"""
        )
        for _, kind, account, connector, *_ in VALUES:
            c.execute(
                """INSERT INTO identity.trading_accounts(
                     trading_account_id,venue_id,account_kind,account_type,
                     environment,status,external_account_ref)
                   VALUES (%s,'venue-bybit',%s,'UNIFIED','MAINNET','ACTIVE',
                     '9000000000')""",
                (account,kind),
            )
            c.execute(
                """INSERT INTO identity.account_connectors(
                     account_connector_id,trading_account_id,
                     connector_kind,adapter_id,credential_ref,status)
                   VALUES (%s,%s,'CEX_PRIVATE_RUNTIME','BYBIT_V5_PRIVATE',
                     'disposable-fixture','ACTIVE')""",
                (connector,account),
            )
        store = StrategyEntryStore(c)
        cards_v1 = {
            card.strategy_id: card
            for card in build_passive_legacy_cards(approved_at=AT)
        }
        cards_v2 = {
            card.strategy_id: card
            for card in new_version_cards(
                approved_at=AT, policy_by_strategy_id={
                    MAIN_ID: embargo, SUB_ID: embargo,
                }
            )
        }
        for strategy_id, _, _, _, act1, _, act2, _ in VALUES:
            for card, activation_id in (
                (cards_v1[strategy_id],act1), (cards_v2[strategy_id],act2),
            ):
                act = StrategyActivation(
                    activation_id=activation_id,
                    strategy_id=card.strategy_id,
                    strategy_version=card.strategy_version,
                    strategy_config_fingerprint=card.strategy_config_fingerprint,
                    enabled=False, enabled_at=AT, disabled_at=AT,
                    scope=FrozenPolicy.from_mapping({
                        "mode":"EXACT_STRATEGY_VERSION",
                    }),
                    operator="CI",source="disposable",
                )
                entry,exit_plan = materialize_plans(card,act)
                store.insert_strategy_card(card)
                store.insert_activation(act,reason="CI")
                store.insert_entry_plan(entry)
                store.insert_exit_plan(exit_plan)
        c.execute("SET SESSION AUTHORIZATION cripta")
        assert c.execute("SELECT session_user,current_user").fetchone()==("cripta","cripta")
        for strategy_id, _, account, _, act1,bind1,act2,bind2 in VALUES:
            assert c.execute(
                "SELECT strategy_entry.register_p90l90_inactive_binding(%s,%s,%s)",
                (bind1,act1,account)
            ).fetchone() == (bind1,)
            assert c.execute(
                "SELECT strategy_entry.register_p90l90_v2_inactive_binding(%s,%s,%s)",
                (bind2,act2,account)
            ).fetchone() == (bind2,)
            assert cards_v1[strategy_id].strategy_config_fingerprint != (
                cards_v2[strategy_id].strategy_config_fingerprint
            )
        rows = c.execute(
            """SELECT a.strategy_id,a.strategy_version,a.enabled,
                      b.binding_state,b.trading_account_id
               FROM strategy_entry.strategy_execution_bindings b
               JOIN strategy_entry.strategy_activations a
                 ON a.activation_id=b.strategy_activation_id
              WHERE a.strategy_id IN (%s,%s)
              ORDER BY a.strategy_id,a.strategy_version""",
            (MAIN_ID,SUB_ID),
        ).fetchall()
        assert len(rows)==4
        assert all(enabled is False and state=="INACTIVE"
                   for _,_,enabled,state,_ in rows)
        assert {r[4] for r in rows} == {r[2] for r in VALUES}
        with (
            pytest.raises(psycopg.errors.RaiseException),
            c.transaction(),
        ):
            c.execute(
                "SELECT strategy_entry.register_p90l90_v2_inactive_binding(%s,%s,%s)",
                (VALUES[0][7],VALUES[0][6],VALUES[1][2]),
            )
        assert c.execute(
            """SELECT count(*) FROM strategy_entry.execution_permissions
                WHERE enabled"""
        ).fetchone() == (0,)
        assert c.execute(
            "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
        ).fetchone() == (0,)
        assert c.execute(
            """SELECT count(*) FROM control.account_live_arm_sessions
                 WHERE state='ACTIVE'"""
        ).fetchone() == (0,)
        raise RuntimeError("ROLLBACK_V2_PASSIVE")
