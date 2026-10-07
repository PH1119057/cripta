from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from bybit_workbench.universal_entry.dashboard_control import StrategyDashboardStore
from bybit_workbench.universal_entry.r1_strategy import build_r1_cards
from bybit_workbench.universal_entry.storage import StrategyEntryStore

DB_DSN = "dbname=cripta user=cripta host=/var/run/postgresql"
OBSERVER_STATUS = Path("/var/lib/cripta/universal_entry_observer/status.json")


def observer_ready() -> bool:
    try:
        payload = json.loads(OBSERVER_STATUS.read_text())
        updated = datetime.fromisoformat(str(payload["updated_at"]))
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return False
    if updated.tzinfo is None:
        return False
    age = (datetime.now(UTC) - updated.astimezone(UTC)).total_seconds()
    return (
        -1 <= age <= 10
        and payload.get("runtime_mode") == "MULTI_STRATEGY_OBSERVER"
        and bool(payload.get("observer_ready"))
        and payload.get("state") in {"IDLE", "WARMUP", "RUNNING", "RELOADING"}
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--activate-monitoring", action="store_true")
    args = parser.parse_args()

    cards = build_r1_cards(
        approved_at=datetime(2026, 10, 7, tzinfo=UTC),
        approved_source="owner-r1-initial-opposite-tp-2026-10-07",
    )
    ready = observer_ready()
    if args.activate_monitoring and not ready:
        raise SystemExit("R1_MONITOR_ACTIVATION_BLOCKED: observer is not fresh/ready")

    with psycopg.connect(DB_DSN) as connection:
        enabled_permissions = connection.execute(
            """SELECT strategy_id FROM strategy_entry.execution_permissions
                WHERE enabled=true"""
        ).fetchall()
        if enabled_permissions:
            raise RuntimeError(
                "DISARMED_INSTALL_INVARIANT: enabled execution permissions exist"
            )
        gate = connection.execute(
            "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
        ).fetchone()
        if gate is not None and bool(gate[0]):
            raise RuntimeError("DISARMED_INSTALL_INVARIANT: mainnet gate is open")

        entry_store = StrategyEntryStore(connection)
        dashboard = StrategyDashboardStore(connection)
        for card in cards:
            row = connection.execute(
                """SELECT strategy_config_fingerprint
                     FROM strategy_entry.strategy_cards
                    WHERE strategy_id=%s AND strategy_version=%s""",
                (card.strategy_id, card.strategy_version),
            ).fetchone()
            if row is None:
                entry_store.insert_strategy_card(card)
                print(
                    f"CARD_CREATED {card.strategy_id} {card.strategy_version} "
                    f"{card.strategy_config_fingerprint}"
                )
            elif str(row[0]) != card.strategy_config_fingerprint:
                raise RuntimeError(
                    f"immutable R1 identity conflict: {card.strategy_id} "
                    f"stored={row[0]} expected={card.strategy_config_fingerprint}"
                )
            else:
                print(
                    f"CARD_VERIFIED {card.strategy_id} {card.strategy_version} "
                    f"{card.strategy_config_fingerprint}"
                )

            if args.activate_monitoring:
                superseded = connection.execute(
                    """SELECT activation_id,strategy_version,strategy_config_fingerprint
                         FROM strategy_entry.strategy_activations
                        WHERE strategy_id=%s AND enabled=true
                          AND NOT (
                              strategy_version=%s
                              AND strategy_config_fingerprint=%s
                          )
                        ORDER BY created_at,activation_id
                        FOR UPDATE""",
                    (
                        card.strategy_id,
                        card.strategy_version,
                        card.strategy_config_fingerprint,
                    ),
                ).fetchall()
                for activation_id, old_version, old_fingerprint in superseded:
                    entry_store.set_activation_enabled(
                        str(activation_id),
                        enabled=False,
                        changed_at=datetime.now(UTC),
                        operator="owner-approved-prearm",
                        source="r1-install-cards",
                        reason=(
                            "superseded immutable R1 Strategy version "
                            f"{old_version} -> {card.strategy_version}"
                        ),
                    )
                    print(
                        "MONITOR_SUPERSEDED "
                        f"{card.strategy_id} {old_version} {old_fingerprint}"
                    )

                activation = connection.execute(
                    """SELECT activation_id,enabled
                         FROM strategy_entry.strategy_activations
                        WHERE strategy_id=%s AND strategy_version=%s
                          AND strategy_config_fingerprint=%s
                        ORDER BY created_at,activation_id""",
                    (
                        card.strategy_id,
                        card.strategy_version,
                        card.strategy_config_fingerprint,
                    ),
                ).fetchall()
                if not activation:
                    result = dashboard.activate_exact_strategy(
                        strategy_id=card.strategy_id,
                        strategy_version=card.strategy_version,
                        strategy_config_fingerprint=card.strategy_config_fingerprint,
                        changed_at=datetime.now(UTC),
                        operator="owner-approved-prearm",
                        source="r1-install-cards",
                        reason="R1 monitoring activated before MICRO_LIVE arm",
                        observer_ready=ready,
                    )
                    print(
                        f"MONITOR_CREATED {card.strategy_id} "
                        f"{result.get('activation_id')}"
                    )
                elif len(activation) == 1 and bool(activation[0][1]):
                    print(f"MONITOR_VERIFIED {card.strategy_id} {activation[0][0]}")
                else:
                    raise RuntimeError(
                        f"R1 activation requires owner/UI handling: {card.strategy_id}"
                    )

        enabled_permissions = connection.execute(
            """SELECT strategy_id FROM strategy_entry.execution_permissions
                WHERE enabled=true"""
        ).fetchall()
        if enabled_permissions:
            raise RuntimeError(
                "DISARMED_INSTALL_INVARIANT: enabled execution permissions exist"
            )
        gate = connection.execute(
            "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
        ).fetchone()
        if gate is not None and bool(gate[0]):
            raise RuntimeError("DISARMED_INSTALL_INVARIANT: mainnet gate is open")

    print("R1_INSTALL_STATE=DISARMED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
