#!/usr/bin/env python3
"""Install/check the inert owner full-contour diagnostic Strategy pair.

This capability registration is allowed only while production is fully
DISARMED.  It never enables execution permission, creates LIVE-arm sessions,
creates a diagnostic run or mutates Bybit.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

import psycopg

from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.owner_full_contour_diagnostic import (
    MAIN_ACCOUNT_ID,
    MAIN_ACTIVATION_ID,
    MAIN_BINDING_ID,
    MAIN_ID,
    SOURCE,
    SUB_ACCOUNT_ID,
    SUB_ACTIVATION_ID,
    SUB_BINDING_ID,
    SUB_ID,
    VERSION,
    cards,
)
from bybit_workbench.universal_entry.storage import StrategyEntryStore

DB_DSN = "dbname=cripta user=cripta host=/var/run/postgresql"
APPROVED_AT = datetime(2026, 11, 1, 10, 5, 57, tzinfo=UTC)
V1_VERSION = "1.0-owner-full-contour-20261101"
V1_MAIN_ACTIVATION_ID = "activation-owner-full-contour-main-20261101"
V1_SUB_ACTIVATION_ID = "activation-owner-full-contour-sub-20261101"
V1_MAIN_BINDING_ID = "binding-owner-full-contour-main-20261101"
V1_SUB_BINDING_ID = "binding-owner-full-contour-sub-20261101"


def _require_disarmed(connection: psycopg.Connection) -> None:
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    if gate != (False,):
        raise RuntimeError("OWNER_FULL_CONTOUR_INSTALL_REQUIRES_GATE_CLOSED")
    if connection.execute(
        "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true"
    ).fetchone() != (0,):
        raise RuntimeError("OWNER_FULL_CONTOUR_INSTALL_REQUIRES_PERMISSIONS_OFF")
    if connection.execute(
        "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
    ).fetchone() != (0,):
        raise RuntimeError("OWNER_FULL_CONTOUR_INSTALL_REQUIRES_GENERIC_ARM_OFF")
    if connection.execute(
        "SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE'"
    ).fetchone() != (0,):
        raise RuntimeError("OWNER_FULL_CONTOUR_INSTALL_REQUIRES_ACCOUNT_ARM_OFF")


def _retire_v1_activations(connection: psycopg.Connection) -> None:
    if connection.execute(
        """SELECT count(*) FROM control.owner_full_contour_diagnostic_runs
            WHERE state IN (
              'REQUESTED','MATERIALIZED','DISPATCHED','POSITION_OPEN',
              'RECONCILIATION_REQUIRED'
            )"""
    ).fetchone() != (0,):
        raise RuntimeError("OWNER_FULL_CONTOUR_V2_INSTALL_ACTIVE_RUN_EXISTS")

    rows = connection.execute(
        """SELECT activation_id,enabled,disabled_at
             FROM strategy_entry.strategy_activations
            WHERE strategy_id IN (%s,%s) AND strategy_version=%s
            ORDER BY activation_id""",
        (MAIN_ID, SUB_ID, V1_VERSION),
    ).fetchall()
    if not rows:
        return
    if len(rows) != 2 or {str(row[0]) for row in rows} != {
        V1_MAIN_ACTIVATION_ID, V1_SUB_ACTIVATION_ID,
    }:
        raise RuntimeError("OWNER_FULL_CONTOUR_V1_ACTIVATION_IDENTITY_DRIFT")
    now = datetime.now(UTC)
    for activation_id, enabled, disabled_at in rows:
        if bool(enabled):
            connection.execute(
                """UPDATE strategy_entry.strategy_activations
                      SET enabled=false,disabled_at=%s,
                          operator='owner',
                          source=%s,
                          change_reason='SUPERSEDED_BY_DETERMINISTIC_FULL_CONTOUR_V2'
                    WHERE activation_id=%s
                      AND strategy_version=%s
                      AND enabled=true""",
                (now, SOURCE, str(activation_id), V1_VERSION),
            )
        elif disabled_at is None:
            raise RuntimeError("OWNER_FULL_CONTOUR_V1_DISABLED_TIMESTAMP_MISSING")


def _state(connection: psycopg.Connection) -> list[tuple[object, ...]]:
    return connection.execute(
        """SELECT a.strategy_id,a.strategy_version,a.strategy_config_fingerprint,
                  a.activation_id,a.enabled,
                  ep.entry_plan_fingerprint,xp.exit_plan_fingerprint,
                  b.strategy_execution_binding_id,b.trading_account_id,b.binding_state,
                  b.allocation_mode
             FROM strategy_entry.strategy_activations a
             JOIN strategy_entry.entry_plans ep
               ON ep.strategy_id=a.strategy_id
              AND ep.strategy_version=a.strategy_version
              AND ep.strategy_config_fingerprint=a.strategy_config_fingerprint
             JOIN strategy_entry.exit_plans xp
               ON xp.strategy_id=a.strategy_id
              AND xp.strategy_version=a.strategy_version
              AND xp.strategy_config_fingerprint=a.strategy_config_fingerprint
             LEFT JOIN strategy_entry.strategy_execution_bindings b
               ON b.strategy_activation_id=a.activation_id
            WHERE a.strategy_id IN (%s,%s)
              AND a.strategy_version=%s
            ORDER BY a.strategy_id""",
        (MAIN_ID, SUB_ID, VERSION),
    ).fetchall()


def _check_exact(
    connection: psycopg.Connection,
    *,
    require_execution_off: bool,
) -> list[tuple[object, ...]]:
    expected_cards = {card.strategy_id: card for card in cards(approved_at=APPROVED_AT)}
    rows = _state(connection)
    if len(rows) != 2:
        raise RuntimeError("OWNER_FULL_CONTOUR_EXACT_TWO_STRATEGIES_REQUIRED")
    expected = {
        MAIN_ID: (MAIN_ACTIVATION_ID, MAIN_BINDING_ID, MAIN_ACCOUNT_ID),
        SUB_ID: (SUB_ACTIVATION_ID, SUB_BINDING_ID, SUB_ACCOUNT_ID),
    }
    for row in rows:
        strategy_id = str(row[0])
        card = expected_cards.get(strategy_id)
        if card is None:
            raise RuntimeError("OWNER_FULL_CONTOUR_UNEXPECTED_STRATEGY")
        activation_id, binding_id, account_id = expected[strategy_id]
        activation = StrategyActivation(
            activation_id=activation_id,
            strategy_id=strategy_id,
            strategy_version=VERSION,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            enabled=True,
            enabled_at=APPROVED_AT,
            scope=FrozenPolicy.from_mapping({"mode": "OWNER_FULL_CONTOUR_DIAGNOSTIC"}),
            operator="owner",
            source=SOURCE,
        )
        entry_plan, exit_plan = materialize_plans(card, activation)
        actual = (
            str(row[1]), str(row[2]), str(row[3]), bool(row[4]),
            str(row[5]), str(row[6]),
            str(row[7] or ""), str(row[8] or ""), str(row[9] or ""),
            str(row[10] or ""),
        )
        wanted = (
            VERSION, card.strategy_config_fingerprint, activation_id, True,
            entry_plan.entry_plan_fingerprint, exit_plan.exit_plan_fingerprint,
            binding_id, account_id, "ACTIVE", "SUBORDINATE_DIAGNOSTIC",
        )
        if actual != wanted:
            raise RuntimeError(
                f"OWNER_FULL_CONTOUR_INSTALLED_IDENTITY_DRIFT:{strategy_id}"
            )
    if require_execution_off and connection.execute(
        """SELECT count(*) FROM strategy_entry.execution_permissions
            WHERE strategy_id IN (%s,%s) AND enabled=true""",
        (MAIN_ID, SUB_ID),
    ).fetchone() != (0,):
        raise RuntimeError("OWNER_FULL_CONTOUR_PERMISSION_MUST_BE_OFF")

    v1_activations = connection.execute(
        """SELECT activation_id,enabled,disabled_at
             FROM strategy_entry.strategy_activations
            WHERE strategy_id IN (%s,%s) AND strategy_version=%s
            ORDER BY activation_id""",
        (MAIN_ID, SUB_ID, V1_VERSION),
    ).fetchall()
    if v1_activations:
        if len(v1_activations) != 2 or any(
            bool(row[1]) or row[2] is None for row in v1_activations
        ):
            raise RuntimeError("OWNER_FULL_CONTOUR_V1_ACTIVATION_NOT_RETIRED")
        old_bindings = connection.execute(
            """SELECT strategy_execution_binding_id,binding_state,effective_to
                 FROM strategy_entry.strategy_execution_bindings
                WHERE strategy_execution_binding_id IN (%s,%s)
                ORDER BY strategy_execution_binding_id""",
            (V1_MAIN_BINDING_ID, V1_SUB_BINDING_ID),
        ).fetchall()
        if len(old_bindings) != 2 or any(
            str(row[1]) != "INACTIVE" or row[2] is None for row in old_bindings
        ):
            raise RuntimeError("OWNER_FULL_CONTOUR_V1_BINDING_NOT_RETIRED")
    return rows


def install(connection: psycopg.Connection) -> None:
    existing = connection.execute(
        """SELECT count(*) FROM strategy_entry.strategy_cards
            WHERE strategy_id IN (%s,%s) AND strategy_version=%s""",
        (MAIN_ID, SUB_ID, VERSION),
    ).fetchone()
    if existing != (0,):
        # Subsequent verified releases only read back the already-installed,
        # immutable diagnostic capability. Runtime permission/arm state is
        # owned by Execution/Lifecycle and is informational to the release
        # installer; do not require or mutate a disarmed runtime here.
        _check_exact(connection, require_execution_off=False)
        return

    # First installation remains fail-closed and is allowed only from a fully
    # disarmed baseline.
    _require_disarmed(connection)
    _retire_v1_activations(connection)

    store = StrategyEntryStore(connection)
    for card in cards(approved_at=APPROVED_AT):
        activation_id = MAIN_ACTIVATION_ID if card.strategy_id == MAIN_ID else SUB_ACTIVATION_ID
        activation = StrategyActivation(
            activation_id=activation_id,
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            enabled=True,
            enabled_at=datetime.now(UTC),
            scope=FrozenPolicy.from_mapping({"mode": "OWNER_FULL_CONTOUR_DIAGNOSTIC"}),
            operator="owner",
            source=SOURCE,
        )
        entry_plan, exit_plan = materialize_plans(card, activation)
        store.insert_strategy_card(card)
        store.insert_activation(
            activation,
            reason="owner-approved full-contour diagnostic capability; execution OFF",
        )
        store.insert_entry_plan(entry_plan)
        store.insert_exit_plan(exit_plan)

    for binding_id, activation_id, account_id in (
        (MAIN_BINDING_ID, MAIN_ACTIVATION_ID, MAIN_ACCOUNT_ID),
        (SUB_BINDING_ID, SUB_ACTIVATION_ID, SUB_ACCOUNT_ID),
    ):
        connection.execute(
            "SELECT strategy_entry.register_owner_full_contour_binding(%s,%s,%s)",
            (binding_id, activation_id, account_id),
        )
    _check_exact(connection)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "install"))
    args = parser.parse_args()
    with psycopg.connect(DB_DSN) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        if args.mode == "install":
            install(connection)
            connection.commit()
        else:
            # Readback is structural. A running diagnostic may legitimately
            # hold temporary permission/arms; the check must not own them.
            _check_exact(connection, require_execution_off=False)
            connection.rollback()
        rows = _state(connection)
    print(
        "OWNER_FULL_CONTOUR_DIAGNOSTIC="
        + ("INSTALLED" if args.mode == "install" else "CHECK_PASS")
        + f" strategies={len(rows)} exchange_mutation=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
