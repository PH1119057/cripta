"""One-time, transactionally audited R1 monitoring cutover.

Run only through the approved release/DB rail as Unix/DB actor cripta, with
the Universal Entry observer stopped. It never trades, arms or migrates any
historical Strategy/EntryPlan/ExitPlan/PAPER/REAL position identity.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.r1_strategy import (
    R1_STRATEGY_IDS, R1_VERSION, build_r1_cards, build_r1_unified_card,
)
from bybit_workbench.universal_entry.storage import StrategyEntryStore

DSN = "dbname=cripta user=cripta host=/var/run/postgresql"
OBSERVER_UNIT = "cripta-universal-entry-observer.service"
LIFECYCLE_STATUS = Path("/var/lib/cripta/lifecycle_supervisor/status.json")


def require_quiesced_observer() -> None:
    result = subprocess.run(
        ["systemctl", "is-active", OBSERVER_UNIT],
        check=False, capture_output=True, text=True,
    )
    if result.stdout.strip() == "active":
        raise RuntimeError("R1_MIGRATION_BLOCKED: observer must be stopped")


def require_fresh_exchange_account(now: datetime) -> None:
    try:
        status = json.loads(LIFECYCLE_STATUS.read_text(encoding="utf-8"))
        audit = status["protection_audit"]
        observed = datetime.fromisoformat(str(audit["account_observed_at"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("R1_MIGRATION_BLOCKED: fresh account truth missing") from exc
    age = (now - observed.astimezone(UTC)).total_seconds()
    if not (
        status.get("state") == "RUNNING"
        and audit.get("account_read_complete") is True
        and audit.get("account_identity_present") is True
        and not audit.get("account_read_error")
        and 0 <= age <= 20
        and int(audit.get("positions_checked", -1)) == 0
    ):
        raise RuntimeError("R1_MIGRATION_BLOCKED: Exchange account inventory not proven flat")


def preflight(connection: psycopg.Connection, now: datetime) -> list[tuple[str, str]]:
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode=%s",
        ("mainnet",),
    ).fetchone()
    if gate is None or bool(gate[0]):
        raise RuntimeError("R1_MIGRATION_BLOCKED: mainnet new Entry not OFF")

    expected = {card.strategy_id: card for card in build_r1_cards()}
    rows = connection.execute(
        """SELECT a.activation_id,a.strategy_id,a.strategy_version,
                  a.strategy_config_fingerprint
             FROM strategy_entry.strategy_activations a
            WHERE a.enabled=true ORDER BY a.strategy_id FOR UPDATE"""
    ).fetchall()
    if {str(row[1]) for row in rows} != set(R1_STRATEGY_IDS.values()) or len(rows) != 5:
        raise RuntimeError("R1_MIGRATION_BLOCKED: expected exact five active legacy Strategies")
    for row in rows:
        old = expected[str(row[1])]
        if (str(row[2]), str(row[3])) != (R1_VERSION, old.strategy_config_fingerprint):
            raise RuntimeError("R1_MIGRATION_BLOCKED: legacy Strategy fingerprint differs")

    checks = {
        "PENDING_PAPER_ORDER": ("strategy_entry.paper_orders", "state='PENDING'"),
        "OPEN_PAPER_POSITION": ("strategy_entry.paper_positions", "state='OPEN'"),
        "HOT_REAL_POSITION": ("runtime.hot_positions", "TRUE"),
        "HOT_REAL_ORDER": ("runtime.hot_orders", "TRUE"),
        "UNRESOLVED_TRADE_COMMAND": (
            "runtime.trade_commands", "state NOT IN ('COMPLETED','FAILED','CANCELLED')"
        ),
        "ACTIVE_CAPITAL_RESERVATION": (
            "runtime.capital_reservations", "state<>'RELEASED'"
        ),
        "ACTIVE_POSITION_CLAIM": (
            "runtime.exchange_position_slot_claims", "state<>'RELEASED'"
        ),
        "PREEXISTING_ACCOUNT_BINDING": (
            "strategy_entry.strategy_execution_bindings", "binding_state='ACTIVE'"
        ),
    }
    for reason, (table, predicate) in checks.items():
        result = connection.execute(
            f"SELECT count(*) FROM {table} WHERE {predicate}"
        ).fetchone()
        if result is None or int(result[0]) != 0:
            raise RuntimeError(f"R1_MIGRATION_BLOCKED: {reason}")

    other_cards = connection.execute(
        """SELECT count(*) FROM strategy_entry.strategy_cards
             WHERE strategy_id=%s""",
        ("r1_five_symbols",),
    ).fetchone()
    if other_cards is None or int(other_cards[0]) != 0:
        raise RuntimeError("R1_MIGRATION_BLOCKED: new Strategy ID already exists")
    return [(str(row[0]), str(row[1])) for row in rows]


def transition(connection: psycopg.Connection, *, now: datetime) -> dict[str, object]:
    """Caller is responsible for BEGIN/COMMIT, observer quiescence and backup."""
    old_rows = preflight(connection, now)
    card = build_r1_unified_card(approved_at=now)
    activation = StrategyActivation(
        activation_id="activation-" + uuid.uuid4().hex,
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True,
        enabled_at=now,
        scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
        operator="owner-confirmed-2026-10-21",
        source="approved:r1-five-symbol-transition",
    )
    entry_plan, exit_plan = materialize_plans(card, activation)
    store = StrategyEntryStore(connection)
    # Legacy execution permissions must be revoked in the SAME transaction.
    old_ids = [row[1] for row in old_rows]
    revoked = connection.execute(
        """UPDATE strategy_entry.execution_permissions
              SET enabled=false,disabled_at=%s,operator=%s,source=%s,
                  change_reason=%s
            WHERE enabled=true AND strategy_id=ANY(%s)""",
        (now, "owner-confirmed-2026-10-21", "approved:r1-five-symbol-transition",
         "Legacy per-coin R1 retired; no new REAL Entry", old_ids),
    )
    if revoked.rowcount != len(old_ids):
        raise RuntimeError("R1_MIGRATION_BLOCKED: legacy permission count changed")

    for activation_id, _strategy_id in old_rows:
        store.set_activation_enabled(
            activation_id, enabled=False, changed_at=now,
            operator="owner-confirmed-2026-10-21",
            source="approved:r1-five-symbol-transition",
            reason="Retired new Entry; historical Exit and PAPER lineage preserved",
        )
    store.insert_strategy_card(card)
    store.insert_activation(
        activation, reason="One R1 Strategy for five instruments; PAPER monitoring only"
    )
    store.insert_entry_plan(entry_plan)
    store.insert_exit_plan(exit_plan)
    return {
        "new_strategy_id": card.strategy_id,
        "new_activation_id": activation.activation_id,
        "new_entry_plan": entry_plan.entry_plan_fingerprint,
        "new_exit_plan": exit_plan.exit_plan_fingerprint,
        "legacy_activations_disabled": len(old_rows),
        "legacy_permissions_disabled": revoked.rowcount,
        "new_execution_permission": "OFF",
        "binding": "NOT CREATED (separate authorized account binding procedure)",
        "mainnet": "OFF",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require_quiesced_observer()
    now = datetime.now(UTC)
    require_fresh_exchange_account(now)
    with psycopg.connect(DSN) as connection:
        with connection.transaction():
            connection.execute("SET LOCAL lock_timeout='2s'")
            connection.execute("SET LOCAL statement_timeout='20s'")
            if not args.apply:
                legacy = preflight(connection, now)
                print(json.dumps({
                    "status": "PREPARED", "legacy_activations": legacy,
                    "new_strategy_id": "r1_five_symbols",
                    "writes": 0, "mainnet": "OFF",
                }, sort_keys=True))
            else:
                output = transition(connection, now=now)
                print(json.dumps({"status": "APPLIED", **output}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
