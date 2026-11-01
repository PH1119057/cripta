"""Disposable PG16 regression for the owner full-contour subordinate binding.

Reproduces the production failure where a second ACTIVE DEDICATED_STRATEGY
binding collided with the already dedicated P90 MAIN/SUB accounts.  The exact
production-like upgrade must retire immutable diagnostic v1 activation/binding
lineage and install diagnostic v2 as SUBORDINATE_DIAGNOSTIC while both parent
P90 bindings remain ACTIVE and dedicated.  No execution permission/LIVE arm is
created.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

import psycopg
import pytest

from bybit_workbench.universal_entry import owner_full_contour_diagnostic as diagnostic
from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.dashboard_control import card_from_editable
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import real_v3_cards
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.storage import StrategyEntryStore
from operations.implementation.install_owner_full_contour_diagnostic import install

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="requires disposable PG16")
AT = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)

MAIN_ACCOUNT = "ta-2122c81ecff14f3b85b102db605212f7"
SUB_ACCOUNT = "ta-853ac5fcd58185697396d33cd57459fa"
P90 = (
    (
        MAIN_ID,
        MAIN_ACCOUNT,
        "activation-p90l90-real-main-20261029-v3",
        "binding-p90l90-real-main-20261029-v3",
        "ac-ed73de20410548d6841aeafa5f196f70",
        "MAIN",
    ),
    (
        SUB_ID,
        SUB_ACCOUNT,
        "activation-p90l90-real-sub-20261029-v3",
        "binding-p90l90-real-sub-20261029-v3",
        "ac-ec57819f875f1bfd929a382d6dc8a125",
        "SUB",
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


V1_diagnostic.VERSION = "1.0-owner-full-contour-20261101"
V1_DIAG = (
    (
        diagnostic.MAIN_ID,
        MAIN_ACCOUNT,
        "activation-owner-full-contour-main-20261101",
        "binding-owner-full-contour-main-20261101",
    ),
    (
        diagnostic.SUB_ID,
        SUB_ACCOUNT,
        "activation-owner-full-contour-sub-20261101",
        "binding-owner-full-contour-sub-20261101",
    ),
)


def _v1_diagnostic_card(strategy_id: str):
    raw = diagnostic._policy(strategy_id)
    raw["strategy_version"] = V1_diagnostic.VERSION
    raw["exit_policy"]["rules"] = [
        rule
        for rule in raw["exit_policy"]["rules"]
        if rule["rule_id"] != "OWNER_DIAGNOSTIC_FORCE_CLOSE"
    ]
    return card_from_editable(
        raw,
        approved_at=datetime(2026, 10, 31, 20, 55, tzinfo=UTC),
        approved_source="owner-2026-11-01-full-contour-real-diagnostic",
        validate_authoring=False,
    )


def test_owner_full_contour_installs_subordinate_on_exact_p90_accounts() -> None:
    assert DSN is not None
    with (
        psycopg.connect(DSN) as connection,
        pytest.raises(RuntimeError, match="ROLLBACK_OWNER_FULL_CONTOUR_SUBORDINATE"),
        connection.transaction(),
    ):
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        connection.execute(
            """INSERT INTO control.execution_gates(
                   mode,enabled,reason,updated_at_epoch_ms)
               VALUES
                 ('mainnet',0,'owner-full-contour-subordinate-ci',0),
                 ('testnet',0,'owner-full-contour-subordinate-ci',0)
               ON CONFLICT(mode) DO UPDATE SET enabled=0"""
        )

        for _strategy_id, account, _activation, _binding, connector, kind in P90:
            connection.execute(
                """INSERT INTO identity.trading_accounts(
                       trading_account_id,venue_id,account_kind,account_type,
                       environment,status,external_account_ref)
                   VALUES(%s,'venue-bybit',%s,'UNIFIED','MAINNET','ACTIVE',%s)
                   ON CONFLICT(trading_account_id) DO NOTHING""",
                (account, kind, "ci-" + kind.lower()),
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

        cards = {
            card.strategy_id: card
            for card in real_v3_cards(
                approved_at=AT,
                policy_by_strategy_id={MAIN_ID: _embargo(), SUB_ID: _embargo()},
                approved_source="OWNER_FULL_CONTOUR_SUBORDINATE_CI",
            )
        }
        store = StrategyEntryStore(connection)
        for strategy_id, account, activation_id, binding_id, _connector, _kind in P90:
            card = cards[strategy_id]
            activation = StrategyActivation(
                activation_id=activation_id,
                strategy_id=card.strategy_id,
                strategy_version=card.strategy_version,
                strategy_config_fingerprint=card.strategy_config_fingerprint,
                enabled=True,
                enabled_at=AT,
                scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
                operator="owner",
                source="OWNER_FULL_CONTOUR_SUBORDINATE_CI",
            )
            entry, exit_plan = materialize_plans(card, activation)
            store.insert_strategy_card(card)
            store.insert_activation(activation, reason="P90 parent for diagnostic CI")
            store.insert_entry_plan(entry)
            store.insert_exit_plan(exit_plan)
            connection.execute(
                """INSERT INTO strategy_entry.strategy_execution_bindings(
                       strategy_execution_binding_id,strategy_activation_id,
                       execution_leg,execution_role,trading_account_id,
                       allocation_mode,binding_state,effective_from,
                       operator,source,reason)
                   VALUES(%s,%s,'PRIMARY','PRIMARY',%s,'DEDICATED_STRATEGY',
                          'ACTIVE',%s,'CI','disposable',
                          'parent P90 binding for subordinate diagnostic')""",
                (binding_id, activation_id, account, AT),
            )

        # Reproduce production's already-installed immutable v1 diagnostic
        # activation/binding pair before upgrading to deterministic v2.
        for strategy_id, account, activation_id, binding_id in V1_DIAG:
            card = _v1_diagnostic_card(strategy_id)
            activation = StrategyActivation(
                activation_id=activation_id,
                strategy_id=card.strategy_id,
                strategy_version=card.strategy_version,
                strategy_config_fingerprint=card.strategy_config_fingerprint,
                enabled=True,
                enabled_at=datetime(2026, 10, 31, 20, 55, tzinfo=UTC),
                scope=FrozenPolicy.from_mapping(
                    {"mode": "OWNER_FULL_CONTOUR_DIAGNOSTIC"}
                ),
                operator="owner",
                source="OWNER_FULL_CONTOUR_V1_CI",
            )
            entry, exit_plan = materialize_plans(card, activation)
            store.insert_strategy_card(card)
            store.insert_activation(activation, reason="production-like diagnostic v1")
            store.insert_entry_plan(entry)
            store.insert_exit_plan(exit_plan)
            connection.execute(
                """INSERT INTO strategy_entry.strategy_execution_bindings(
                       strategy_execution_binding_id,strategy_activation_id,
                       execution_leg,execution_role,trading_account_id,
                       allocation_mode,binding_state,effective_from,
                       operator,source,reason)
                   VALUES(%s,%s,'PRIMARY','PRIMARY',%s,
                          'SUBORDINATE_DIAGNOSTIC','ACTIVE',%s,
                          'owner','OWNER_FULL_CONTOUR_V1_CI',
                          'production-like v1 before upgrade')""",
                (
                    binding_id,
                    activation_id,
                    account,
                    datetime(2026, 10, 31, 20, 55, tzinfo=UTC),
                ),
            )

        # Match production's already-existing read rights used by the inert
        # installer preflight.  This is disposable-fixture parity only; the
        # release does not grant new runtime privileges.
        connection.execute(
            """GRANT SELECT ON
                   control.execution_gates,
                   strategy_entry.execution_permissions,
                   control.live_arm_sessions,
                   control.account_live_arm_sessions
               TO cripta"""
        )
        connection.execute("SET SESSION AUTHORIZATION cripta")
        assert connection.execute(
            "SELECT session_user,current_user"
        ).fetchone() == ("cripta", "cripta")

        install(connection)

        rows = connection.execute(
            """SELECT a.strategy_id,a.activation_id,
                      b.strategy_execution_binding_id,b.trading_account_id,
                      b.allocation_mode,b.binding_state
                 FROM strategy_entry.strategy_activations a
                 JOIN strategy_entry.strategy_execution_bindings b
                   ON b.strategy_activation_id=a.activation_id
                WHERE a.strategy_version=%s
                ORDER BY a.strategy_id""",
            (diagnostic.VERSION,),
        ).fetchall()
        assert rows == [
            (
                diagnostic.MAIN_ID,
                diagnostic.MAIN_ACTIVATION_ID,
                diagnostic.MAIN_BINDING_ID,
                MAIN_ACCOUNT,
                "SUBORDINATE_DIAGNOSTIC",
                "ACTIVE",
            ),
            (
                diagnostic.SUB_ID,
                diagnostic.SUB_ACTIVATION_ID,
                diagnostic.SUB_BINDING_ID,
                SUB_ACCOUNT,
                "SUBORDINATE_DIAGNOSTIC",
                "ACTIVE",
            ),
        ]

        old_rows = connection.execute(
            """SELECT a.strategy_id,a.enabled,a.disabled_at,
                      b.binding_state,b.effective_to
                 FROM strategy_entry.strategy_activations a
                 JOIN strategy_entry.strategy_execution_bindings b
                   ON b.strategy_activation_id=a.activation_id
                WHERE a.strategy_version=%s
                ORDER BY a.strategy_id""",
            (V1_diagnostic.VERSION,),
        ).fetchall()
        assert len(old_rows) == 2
        assert all(
            enabled is False
            and disabled_at is not None
            and binding_state == "INACTIVE"
            and effective_to is not None
            for _sid, enabled, disabled_at, binding_state, effective_to in old_rows
        )

        assert connection.execute(
            """SELECT count(*) FROM strategy_entry.strategy_execution_bindings
                WHERE binding_state='ACTIVE'
                  AND allocation_mode='DEDICATED_STRATEGY'
                  AND trading_account_id IN (%s,%s)""",
            (MAIN_ACCOUNT, SUB_ACCOUNT),
        ).fetchone() == (2,)
        assert connection.execute(
            """SELECT count(*) FROM strategy_entry.execution_permissions
                WHERE enabled=true"""
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
        ).fetchone() == (0,)
        assert connection.execute(
            """SELECT count(*) FROM control.account_live_arm_sessions
                WHERE state='ACTIVE'"""
        ).fetchone() == (0,)

        raise RuntimeError("ROLLBACK_OWNER_FULL_CONTOUR_SUBORDINATE")
