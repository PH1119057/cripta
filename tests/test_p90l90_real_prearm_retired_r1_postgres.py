"""Disposable PG16 regression for retired R1 -> P90 REAL v3 PREARM ownership.

Reproduces the production defect from Issue #243: the canonically retired
r1_five_symbols activation is disabled while its historical dedicated MAIN
StrategyExecutionBinding is still ACTIVE. PREARM must close only that exact
retired binding, activate the exact P90 MAIN/SUB bindings, and never open
mainnet, grant execution permission, or create LIVE-arm sessions.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

import psycopg
import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import real_v3_cards
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.r1_strategy import build_r1_unified_card
from bybit_workbench.universal_entry.storage import StrategyEntryStore

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="requires disposable PG16")
AT = datetime(2026, 10, 30, 0, 0, tzinfo=UTC)
MAIN_ACCOUNT = "ta-2122c81ecff14f3b85b102db605212f7"
SUB_ACCOUNT = "ta-853ac5fcd58185697396d33cd57459fa"
R1_ACTIVATION = "activation-be482d99252b4055b020a84fef8e2054"
R1_BINDING = "binding-r1-unified-2c51489dae0a38fc"
P90 = (
    (
        MAIN_ID,
        MAIN_ACCOUNT,
        "activation-p90l90-real-main-20261029-v3",
        "binding-p90l90-real-main-20261029-v3",
    ),
    (
        SUB_ID,
        SUB_ACCOUNT,
        "activation-p90l90-real-sub-20261029-v3",
        "binding-p90l90-real-sub-20261029-v3",
    ),
)


def _embargo() -> StrategyHardStopEmbargoPolicy:
    return StrategyHardStopEmbargoPolicy.parse(
        {
            "enabled": True,
            "hardstop_count": 2,
            "rolling_hours": 48,
            "new_entry_pause_hours": 48,
            "close_existing_positions": False,
            "domain": "STRATEGY_VERSION",
        }
    )


def _insert_binding(
    connection: psycopg.Connection,
    *,
    binding_id: str,
    activation_id: str,
    account_id: str,
    state: str,
) -> None:
    connection.execute(
        """INSERT INTO strategy_entry.strategy_execution_bindings(
               strategy_execution_binding_id,strategy_activation_id,
               execution_leg,execution_role,trading_account_id,
               allocation_mode,binding_state,effective_from,
               operator,source,reason)
           VALUES(%s,%s,'PRIMARY','PRIMARY',%s,'DEDICATED_STRATEGY',
                  %s,%s,'CI','disposable','Issue #243 PREARM regression')""",
        (binding_id, activation_id, account_id, state, AT),
    )


def test_prearm_retires_exact_disabled_r1_binding_before_p90_ownership() -> None:
    assert DSN is not None
    with (
        psycopg.connect(DSN) as connection,
        pytest.raises(RuntimeError, match="ROLLBACK_P90_R1_PREARM"),
        connection.transaction(),
    ):
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        connection.execute(
            """INSERT INTO control.execution_gates(
                   mode,enabled,reason,updated_at_epoch_ms)
               VALUES
                 ('mainnet',0,'p90-r1-regression',0),
                 ('testnet',0,'p90-r1-regression',0)
               ON CONFLICT(mode) DO UPDATE SET enabled=0"""
        )
        for account, kind, connector in (
            (
                MAIN_ACCOUNT,
                "MAIN",
                "ac-ed73de20410548d6841aeafa5f196f70",
            ),
            (
                SUB_ACCOUNT,
                "SUB",
                "ac-ec57819f875f1bfd929a382d6dc8a125",
            ),
        ):
            connection.execute(
                """INSERT INTO identity.trading_accounts(
                       trading_account_id,venue_id,account_kind,account_type,
                       environment,status,external_account_ref)
                   VALUES(%s,'venue-bybit',%s,'UNIFIED','MAINNET','ACTIVE',
                          %s)
                   ON CONFLICT(trading_account_id) DO NOTHING""",
                (account, kind, "9000000001" if kind == "MAIN" else "9000000002"),
            )
            connection.execute(
                """INSERT INTO identity.account_connectors(
                       account_connector_id,trading_account_id,
                       connector_kind,adapter_id,credential_ref,status)
                   VALUES(%s,%s,'CEX_PRIVATE_RUNTIME','BYBIT_V5_PRIVATE',
                          'disposable-fixture','ACTIVE')
                   ON CONFLICT(account_connector_id) DO NOTHING""",
                (connector, account),
            )

        store = StrategyEntryStore(connection)
        r1 = build_r1_unified_card(approved_at=AT)
        r1_activation = StrategyActivation(
            activation_id=R1_ACTIVATION,
            strategy_id=r1.strategy_id,
            strategy_version=r1.strategy_version,
            strategy_config_fingerprint=r1.strategy_config_fingerprint,
            enabled=False,
            enabled_at=AT,
            disabled_at=AT,
            scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
            operator="owner",
            source="OWNER_R1_RETIRED_20261026",
        )
        r1_entry, r1_exit = materialize_plans(r1, r1_activation)
        store.insert_strategy_card(r1)
        store.insert_activation(r1_activation, reason="R1 RETIRED")
        store.insert_entry_plan(r1_entry)
        store.insert_exit_plan(r1_exit)
        _insert_binding(
            connection,
            binding_id=R1_BINDING,
            activation_id=R1_ACTIVATION,
            account_id=MAIN_ACCOUNT,
            state="ACTIVE",
        )

        cards = {
            card.strategy_id: card
            for card in real_v3_cards(
                approved_at=AT,
                policy_by_strategy_id={MAIN_ID: _embargo(), SUB_ID: _embargo()},
                approved_source="OWNER_P90L90_REAL_V3_ISSUE_243",
            )
        }
        for strategy_id, account, activation_id, binding_id in P90:
            card = cards[strategy_id]
            activation = StrategyActivation(
                activation_id=activation_id,
                strategy_id=card.strategy_id,
                strategy_version=card.strategy_version,
                strategy_config_fingerprint=card.strategy_config_fingerprint,
                enabled=False,
                enabled_at=AT,
                disabled_at=AT,
                scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
                operator="owner",
                source="OWNER_P90L90_REAL_V3_ISSUE_243",
            )
            entry, exit_plan = materialize_plans(card, activation)
            store.insert_strategy_card(card)
            store.insert_activation(activation, reason="V3 INACTIVE")
            store.insert_entry_plan(entry)
            store.insert_exit_plan(exit_plan)
            _insert_binding(
                connection,
                binding_id=binding_id,
                activation_id=activation_id,
                account_id=account,
                state="INACTIVE",
            )

        connection.execute("SET SESSION AUTHORIZATION cripta")
        assert connection.execute(
            "SELECT session_user,current_user"
        ).fetchone() == ("cripta", "cripta")

        assert connection.execute(
            """SELECT strategy_entry.prearm_p90l90_real_v3(
                   %s,'owner','CI_ISSUE_243_PREARM')""",
            (AT,),
        ).fetchone() == (2,)

        retired = connection.execute(
            """SELECT binding_state,effective_to
                 FROM strategy_entry.strategy_execution_bindings
                WHERE strategy_execution_binding_id=%s""",
            (R1_BINDING,),
        ).fetchone()
        assert retired is not None
        assert retired[0] == "INACTIVE"
        assert retired[1] == AT

        rows = connection.execute(
            """SELECT a.strategy_id,a.enabled,b.binding_state,b.trading_account_id
                 FROM strategy_entry.strategy_activations a
                 JOIN strategy_entry.strategy_execution_bindings b
                   ON b.strategy_activation_id=a.activation_id
                WHERE a.strategy_version='2.0-p90l90-real-main-sub-20261028'
                ORDER BY a.strategy_id"""
        ).fetchall()
        assert rows == [
            (MAIN_ID, True, "ACTIVE", MAIN_ACCOUNT),
            (SUB_ID, True, "ACTIVE", SUB_ACCOUNT),
        ]
        assert connection.execute(
            "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
        ).fetchone() == (0,)
        assert connection.execute(
            """SELECT count(*) FROM control.account_live_arm_sessions
                WHERE state='ACTIVE'"""
        ).fetchone() == (0,)

        assert connection.execute(
            """SELECT strategy_entry.unprearm_p90l90_real_v3(
                   %s,'owner','CI_ISSUE_243_UNPREARM')""",
            (AT,),
        ).fetchone() == (2,)
        assert connection.execute(
            """SELECT binding_state,effective_to
                 FROM strategy_entry.strategy_execution_bindings
                WHERE strategy_execution_binding_id=%s""",
            (R1_BINDING,),
        ).fetchone() == ("INACTIVE", AT)
        raise RuntimeError("ROLLBACK_P90_R1_PREARM")
