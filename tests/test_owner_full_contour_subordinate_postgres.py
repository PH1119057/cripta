"""Disposable PG16 regression for the owner full-contour subordinate binding.

Reproduces the production failure where a second ACTIVE DEDICATED_STRATEGY
binding collided with the already dedicated P90 MAIN/SUB accounts.  The exact
production-like upgrade must retire immutable diagnostic v1 activation/binding
lineage and install diagnostic v2 as SUBORDINATE_DIAGNOSTIC while both parent
P90 bindings remain ACTIVE and dedicated.  No execution permission/LIVE arm is
created.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import tuple_row

from bybit_workbench.universal_entry import owner_full_contour_diagnostic as diagnostic
from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.dashboard_control import card_from_editable
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.owner_full_contour_control import request_run
from bybit_workbench.universal_entry.owner_full_contour_runtime import (
    materialize_requested_run,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import real_v3_cards
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.storage import StrategyEntryStore
from operations.connectivity.universal_entry_consumer import mapping_sequence_row
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


V1_DIAG_VERSION = "1.0-owner-full-contour-20261101"
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

V2_DIAG_VERSION = "2.0-owner-full-contour-e2e-20261101"
V2_DIAG = (
    (
        diagnostic.MAIN_ID,
        MAIN_ACCOUNT,
        "activation-owner-full-contour-main-v2-20261101",
        "binding-owner-full-contour-main-v2-20261101",
    ),
    (
        diagnostic.SUB_ID,
        SUB_ACCOUNT,
        "activation-owner-full-contour-sub-v2-20261101",
        "binding-owner-full-contour-sub-v2-20261101",
    ),
)


def _historical_diagnostic_policy(
    strategy_id: str,
    *,
    version: str,
    include_forced_exit: bool,
) -> dict[str, object]:
    raw = diagnostic._policy(strategy_id)
    raw["strategy_version"] = version
    raw["exit_policy"]["exit_plan_version"] = "owner-full-contour-fixed-05-v1"
    raw["exit_policy"]["hard_stop"]["value"] = "0.5"
    raw["exit_policy"]["take_profit"]["value"] = "0.5"
    for rule in raw["exit_policy"]["rules"]:
        if rule["rule_id"] == "OWNER_DIAGNOSTIC_TP_0_2":
            rule["rule_id"] = "OWNER_DIAGNOSTIC_TP_0_5"
            rule["predicate"]["params"]["value"] = "0.5"
        elif rule["rule_id"] == "OWNER_DIAGNOSTIC_SL_0_2":
            rule["rule_id"] = "OWNER_DIAGNOSTIC_SL_0_5"
            rule["predicate"]["params"]["value"] = "-0.5"
    if not include_forced_exit:
        raw["exit_policy"]["rules"] = [
            rule
            for rule in raw["exit_policy"]["rules"]
            if rule["rule_id"] != "OWNER_DIAGNOSTIC_FORCE_CLOSE"
        ]
    raw["protection_policy"]["initial_protection"]["stop_loss_pct"] = "0.5"
    raw["lifecycle_policy"]["emergency_policy"]["initial_stop_loss_pct"] = "0.5"
    return raw


def _v2_diagnostic_card(strategy_id: str):
    return card_from_editable(
        _historical_diagnostic_policy(
            strategy_id,
            version=V2_DIAG_VERSION,
            include_forced_exit=True,
        ),
        approved_at=datetime(2026, 11, 1, 10, 5, 57, tzinfo=UTC),
        approved_source="owner-2026-11-01-full-contour-real-diagnostic-v2",
        validate_authoring=False,
    )


def _v1_diagnostic_card(strategy_id: str):
    return card_from_editable(
        _historical_diagnostic_policy(
            strategy_id,
            version=V1_DIAG_VERSION,
            include_forced_exit=False,
        ),
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

        retired_at = datetime(2026, 11, 1, 10, 5, 57, tzinfo=UTC)
        connection.execute(
            """UPDATE strategy_entry.strategy_activations
                  SET enabled=false,disabled_at=%s
                WHERE strategy_version=%s""",
            (retired_at, V1_DIAG_VERSION),
        )
        connection.execute(
            """UPDATE strategy_entry.strategy_execution_bindings
                  SET binding_state='INACTIVE',effective_to=%s
                WHERE strategy_activation_id=ANY(%s)""",
            (retired_at, [row[2] for row in V1_DIAG]),
        )

        for strategy_id, account, activation_id, binding_id in V2_DIAG:
            card = _v2_diagnostic_card(strategy_id)
            activation = StrategyActivation(
                activation_id=activation_id,
                strategy_id=card.strategy_id,
                strategy_version=card.strategy_version,
                strategy_config_fingerprint=card.strategy_config_fingerprint,
                enabled=True,
                enabled_at=retired_at,
                scope=FrozenPolicy.from_mapping(
                    {"mode": "OWNER_FULL_CONTOUR_DIAGNOSTIC"}
                ),
                operator="owner",
                source="OWNER_FULL_CONTOUR_V2_CI",
            )
            entry, exit_plan = materialize_plans(card, activation)
            store.insert_strategy_card(card)
            store.insert_activation(activation, reason="production-like diagnostic v2")
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
                          'owner','OWNER_FULL_CONTOUR_V2_CI',
                          'production-like v2 before v3 upgrade')""",
                (binding_id, activation_id, account, retired_at),
            )

        # production has this historical control-plane table; current
        # operations/sql lineage does not create it in this disposable suite.
        # Reuse the exact schema already exercised by test_release_arm_postgres.
        connection.execute(
            """CREATE TABLE IF NOT EXISTS control.execution_gate_events(
                id bigint generated always as identity primary key,
                at_epoch_ms bigint not null,mode text not null,
                previous_enabled boolean not null,requested_enabled boolean not null,
                resulting_enabled boolean not null,reason text not null,
                source text not null,origin text not null,request_id text not null,
                settings_version text,created_at timestamptz default now())"""
        )

        # Match production's already-existing read rights used by the inert
        # installer preflight.  This is disposable-fixture parity only; the
        # release does not grant new runtime privileges.
        connection.execute(
            """GRANT SELECT ON
                   control.execution_gates,
                   control.owner_full_contour_diagnostic_runs,
                   strategy_entry.execution_permissions,
                   strategy_entry.strategy_activations,
                   strategy_entry.strategy_execution_bindings,
                   control.live_arm_sessions,
                   control.account_live_arm_sessions,
                   runtime.hot_positions,
                   runtime.hot_orders,
                   runtime.position_ownership
               TO cripta"""
        )
        connection.execute(
            """GRANT UPDATE ON
                   control.execution_gates,
                   strategy_entry.execution_permissions,
                   control.live_arm_sessions,
                   control.owner_full_contour_diagnostic_runs
               TO cripta"""
        )
        connection.execute(
            """GRANT INSERT ON
                   control.execution_gate_events,
                   strategy_entry.execution_permissions,
                   control.live_arm_sessions,
                   control.owner_full_contour_diagnostic_runs
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
            (V1_DIAG_VERSION,),
        ).fetchall()
        assert len(old_rows) == 2
        assert all(
            enabled is False
            and disabled_at is not None
            and binding_state == "INACTIVE"
            and effective_to is not None
            for _sid, enabled, disabled_at, binding_state, effective_to in old_rows
        )

        v2_rows = connection.execute(
            """SELECT a.strategy_id,a.enabled,a.disabled_at,
                      b.binding_state,b.effective_to
                 FROM strategy_entry.strategy_activations a
                 JOIN strategy_entry.strategy_execution_bindings b
                   ON b.strategy_activation_id=a.activation_id
                WHERE a.strategy_version=%s
                ORDER BY a.strategy_id""",
            (V2_DIAG_VERSION,),
        ).fetchall()
        assert len(v2_rows) == 2
        assert all(
            enabled is False
            and disabled_at is not None
            and binding_state == "INACTIVE"
            and effective_to is not None
            for _sid, enabled, disabled_at, binding_state, effective_to in v2_rows
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

        # Production-path fixtures for the full-contour materializer.  They
        # deliberately stop before any Exchange mutation: the tested boundary is
        # the ordinary EntryExecutionRequest durable handoff.
        run_at = datetime.now(UTC)
        release_commit = "f" * 40
        position_mode_ref = "pmode-owner-fc-ci-arb"
        generation_id = "acctgen-owner-fc-ci-arb"
        epoch_id = "p90-real-epoch-" + ("a" * 40)
        connection.execute(
            """INSERT INTO runtime.position_mode_states(
                   position_mode_state_ref,exchange,account_ref,product_category,
                   instrument,position_mode,position_idx,observed_at,received_at,
                   fresh_until,provenance,trading_account_id,identity_status)
               VALUES(%s,'BYBIT','BYBIT:UNIFIED','LINEAR','ARBUSDT','ONE_WAY',0,
                      %s,%s,%s,'{}'::jsonb,%s,'PROVEN')""",
            (
                position_mode_ref,
                run_at,
                run_at,
                run_at + timedelta(minutes=10),
                SUB_ACCOUNT,
            ),
        )
        account_mode_proof = {
            "__cripta_signed_account_mode": {
                "status": "PASS",
                "observed_at_epoch_ms": int(run_at.timestamp() * 1000),
                "margin_mode": "ISOLATED_MARGIN",
                "unified_margin_status": "6",
            }
        }
        connection.execute(
            """INSERT INTO runtime.account_state_generations(
                   generation_id,account_ref,reason,state,started_at,completed_at,
                   account_type,total_equity,wallet_balance,available_balance,
                   positions_count,active_orders_count,position_mode_refs,
                   wallet_payload,error,trading_account_id,account_connector_id,
                   identity_status)
               VALUES(%s,'BYBIT:UNIFIED','owner-full-contour-ci','COMPLETE',
                      %s,%s,'UNIFIED',100,100,100,0,0,%s::jsonb,%s::jsonb,
                      '',%s,%s,'PROVEN')""",
            (
                generation_id,
                run_at,
                run_at,
                json.dumps({"ARBUSDT": position_mode_ref}),
                json.dumps(account_mode_proof),
                SUB_ACCOUNT,
                diagnostic.SUB_CONNECTOR_ID,
            ),
        )
        connection.execute(
            """INSERT INTO strategy_entry.p90l90_real_source_epochs(
                   epoch_id,source_commit,market_source_id,opened_at,
                   source_ws_ready_at,native_mirror_ready_at,status)
               VALUES(%s,%s,'ms-bybit-mainnet-public-v1',%s,%s,%s,'ACTIVE')""",
            (epoch_id, release_commit, run_at, run_at, run_at),
        )
        connection.execute(
            """INSERT INTO strategy_entry.p90l90_real_causal_checkpoints(
                   epoch_id,symbol,checkpoint_json,entry_checkpoint_json,
                   source_commit,writer_release_commit,generation,
                   created_at,updated_at)
               VALUES(%s,'ARBUSDT',%s::jsonb,%s::jsonb,%s,%s,1,%s,%s)""",
            (
                epoch_id,
                json.dumps(
                    {
                        "schema": "P90L90_CAUSAL_TRANSPORT_JOIN_V1",
                        "symbol": "ARBUSDT",
                    }
                ),
                json.dumps(
                    {
                        "schema": "P90L90_CAUSAL_ENTRY_RESTART_V1",
                        "previous_price": "0.50",
                    }
                ),
                release_commit,
                release_commit,
                run_at,
                run_at,
            ),
        )

        # Production regression: the previous diagnostic pre-mutation failure
        # latched only this SUB account. V3 owner-control may clear that exact
        # latch only after the fresh flat signed account-mode proof above.
        connection.execute("RESET SESSION AUTHORIZATION")
        prior_run_id = "owner-fc-ci-prior-pre-mutation"
        prior_command_id = "ue-ci-prior-pre-mutation"
        prior_at = run_at - timedelta(seconds=1)
        connection.execute(
            """INSERT INTO runtime.trade_commands(
                   command_id,command_type,symbol,payload_json,state,
                   requested_at_epoch_ms,started_at_epoch_ms,finished_at_epoch_ms,
                   error,strategy_execution_binding_id,execution_leg,
                   trading_account_id,identity_status)
               VALUES(%s,'entry','ARBUSDT','{}'::jsonb,'failed',
                      %s,%s,%s,'PRE_MUTATION_SAFETY_BLOCK:PreMutationSafetyBlock',
                      %s,'PRIMARY',%s,'PROVEN')""",
            (
                prior_command_id,
                int(prior_at.timestamp() * 1000),
                int(prior_at.timestamp() * 1000),
                int(prior_at.timestamp() * 1000),
                "binding-owner-full-contour-sub-v2-20261101",
                SUB_ACCOUNT,
            ),
        )
        connection.execute(
            """INSERT INTO control.owner_full_contour_diagnostic_runs(
                   run_id,requested_at,requested_by,source,symbol,direction,
                   strategy_id,strategy_version,strategy_config_fingerprint,
                   strategy_activation_id,strategy_execution_binding_id,
                   trading_account_id,account_connector_id,release_commit,state,
                   entry_command_id,error,completed_at,updated_at)
               VALUES(%s,%s,'owner','dashboard:owner-full-contour-diagnostic',
                      'ARBUSDT','LONG',%s,
                      '2.0-owner-full-contour-e2e-20261101',%s,
                      'activation-owner-full-contour-sub-v2-20261101',
                      'binding-owner-full-contour-sub-v2-20261101',
                      %s,%s,%s,'FAILED',%s,
                      'PRE_MUTATION_SAFETY_BLOCK:PreMutationSafetyBlock',
                      %s,%s)""",
            (
                prior_run_id,
                prior_at,
                diagnostic.SUB_ID,
                "d" * 64,
                SUB_ACCOUNT,
                diagnostic.SUB_CONNECTOR_ID,
                release_commit,
                prior_command_id,
                prior_at,
                prior_at,
            ),
        )
        connection.execute(
            """INSERT INTO runtime.account_entry_safety_blocks(
                   trading_account_id,account_connector_id,blocked_at,reason)
               VALUES(%s,%s,%s,'PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED')""",
            (SUB_ACCOUNT, diagnostic.SUB_CONNECTOR_ID, prior_at),
        )
        connection.execute("SET SESSION AUTHORIZATION cripta")
        assert connection.execute(
            "SELECT session_user,current_user"
        ).fetchone() == ("cripta", "cripta")

        # Regression for the production 2026-11-01 authenticated trigger
        # failure: control.execution_gates.enabled is SMALLINT, while the historical v2
        # SECURITY DEFINER account-arm function had compared it with boolean
        # false. Exercise the exact owner request path as runtime role cripta.
        requested = request_run(
            connection,
            request_id="owner-fc-ci-arb-long",
            symbol="ARBUSDT",
            direction="LONG",
            operator="owner",
            release_commit=release_commit,
            now=run_at,
        )
        assert requested["symbol"] == "ARBUSDT"
        assert requested["direction"] == "LONG"
        assert requested["state"] == "REQUESTED"
        assert connection.execute(
            """SELECT count(*) FROM runtime.account_entry_safety_blocks
                WHERE trading_account_id=%s AND account_connector_id=%s""",
            (SUB_ACCOUNT, diagnostic.SUB_CONNECTOR_ID),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
        ).fetchone() == (1,)
        assert connection.execute(
            """SELECT count(*) FROM strategy_entry.execution_permissions
                WHERE enabled=true
                  AND strategy_id=%s
                  AND strategy_version=%s""",
            (diagnostic.SUB_ID, diagnostic.VERSION),
        ).fetchone() == (1,)
        assert connection.execute(
            """SELECT count(*) FROM control.live_arm_sessions
                WHERE state='ACTIVE'
                  AND strategy_id=%s
                  AND strategy_version=%s
                  AND symbol='ARBUSDT'""",
            (diagnostic.SUB_ID, diagnostic.VERSION),
        ).fetchone() == (1,)
        assert connection.execute(
            """SELECT count(*) FROM control.account_live_arm_sessions
                WHERE state='ACTIVE'
                  AND strategy_id=%s
                  AND strategy_version=%s
                  AND symbol='ARBUSDT'
                  AND trading_account_id=%s""",
            (diagnostic.SUB_ID, diagnostic.VERSION, SUB_ACCOUNT),
        ).fetchone() == (1,)
        assert connection.execute(
            """SELECT count(*) FROM strategy_entry.execution_permissions
                WHERE enabled=true
                  AND strategy_id IN (%s,%s)""",
            (MAIN_ID, SUB_ID),
        ).fetchone() == (0,)

        # The actual Universal Entry process uses this Mapping-compatible row
        # factory so downstream legacy positional readers remain safe while
        # Mapping consumers continue to see dict semantics.
        connection.row_factory = mapping_sequence_row
        probe = connection.execute(
            "SELECT 'alpha'::text AS first, 'beta'::text AS second, "
            "'gamma'::text AS third"
        ).fetchone()
        assert probe is not None
        assert probe["second"] == "beta"
        assert probe[0] == "alpha"
        assert probe[1:3] == ("beta", "gamma")
        assert dict(probe) == {
            "first": "alpha",
            "second": "beta",
            "third": "gamma",
        }

        materialized = materialize_requested_run(
            connection,
            trading_account_id=SUB_ACCOUNT,
            account_connector_id=diagnostic.SUB_CONNECTOR_ID,
            release_commit=release_commit,
            now=run_at,
        )
        assert materialized is not None
        assert materialized.startswith("OWNER_DIAGNOSTIC_MATERIALIZED:")
        run = connection.execute(
            """SELECT state,signal_id,strategy_attempt_id,entry_decision_id,
                      entry_execution_request_id,entry_command_id
                 FROM control.owner_full_contour_diagnostic_runs
                WHERE run_id=%s""",
            (requested["run_id"],),
        ).fetchone()
        assert run is not None
        assert run["state"] == "MATERIALIZED"
        assert run["signal_id"]
        assert run["strategy_attempt_id"]
        assert run["entry_decision_id"]
        assert run["entry_execution_request_id"]
        assert run["entry_command_id"] is None

        # Restore the installer's real tuple-row contract for the existing
        # release-readback assertion below.
        connection.row_factory = tuple_row

        # A later verified release must be able to read back the already
        # installed immutable capability while this diagnostic runtime state is
        # active. The installer must neither require disarm nor mutate it.
        install(connection)
        assert connection.execute(
            """SELECT count(*) FROM strategy_entry.execution_permissions
                WHERE enabled=true
                  AND strategy_id=%s
                  AND strategy_version=%s""",
            (diagnostic.SUB_ID, diagnostic.VERSION),
        ).fetchone() == (1,)
        assert connection.execute(
            """SELECT count(*) FROM control.live_arm_sessions
                WHERE state='ACTIVE'
                  AND strategy_id=%s
                  AND strategy_version=%s
                  AND symbol='ARBUSDT'""",
            (diagnostic.SUB_ID, diagnostic.VERSION),
        ).fetchone() == (1,)
        assert connection.execute(
            """SELECT count(*) FROM control.account_live_arm_sessions
                WHERE state='ACTIVE'
                  AND strategy_id=%s
                  AND strategy_version=%s
                  AND symbol='ARBUSDT'""",
            (diagnostic.SUB_ID, diagnostic.VERSION),
        ).fetchone() == (1,)

        raise RuntimeError("ROLLBACK_OWNER_FULL_CONTOUR_SUBORDINATE")
