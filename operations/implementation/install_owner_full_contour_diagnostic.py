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
APPROVED_AT = datetime(2026, 10, 31, 20, 55, tzinfo=UTC)


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


def _state(connection: psycopg.Connection) -> list[tuple[object, ...]]:
    return connection.execute(
        """SELECT a.strategy_id,a.strategy_version,a.strategy_config_fingerprint,
                  a.activation_id,a.enabled,
                  ep.entry_plan_fingerprint,xp.exit_plan_fingerprint,
                  b.strategy_execution_binding_id,b.trading_account_id,b.binding_state
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


def _check_exact(connection: psycopg.Connection) -> list[tuple[object, ...]]:
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
        )
        wanted = (
            VERSION, card.strategy_config_fingerprint, activation_id, True,
            entry_plan.entry_plan_fingerprint, exit_plan.exit_plan_fingerprint,
            binding_id, account_id, "ACTIVE",
        )
        if actual != wanted:
            raise RuntimeError(
                f"OWNER_FULL_CONTOUR_INSTALLED_IDENTITY_DRIFT:{strategy_id}"
            )
    if connection.execute(
        """SELECT count(*) FROM strategy_entry.execution_permissions
            WHERE strategy_id IN (%s,%s) AND enabled=true""",
        (MAIN_ID, SUB_ID),
    ).fetchone() != (0,):
        raise RuntimeError("OWNER_FULL_CONTOUR_PERMISSION_MUST_BE_OFF")
    return rows


def install(connection: psycopg.Connection) -> None:
    _require_disarmed(connection)
    existing = connection.execute(
        """SELECT count(*) FROM strategy_entry.strategy_cards
            WHERE strategy_id IN (%s,%s) AND strategy_version=%s""",
        (MAIN_ID, SUB_ID, VERSION),
    ).fetchone()
    if existing != (0,):
        _check_exact(connection)
        return

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
            _check_exact(connection)
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
