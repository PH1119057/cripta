"""Atomic P90L90 Strategy-v2 authoring installer; no LIVE and no orders.

Uses existing cripta Unix/DB application actor. Schema/grants owned by postgres
in 20261028_p90l90_strategy_v2_passive_binding_writer.sql migration.
Never overwrites historical v1 cards, activations or account bindings.
"""
from __future__ import annotations

import os
import pwd
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path("/srv/cripta/runtime/current")
SRC = ROOT / "src"
if not SRC.is_dir():
    raise SystemExit("P90L90_V2_INSTALLED_RUNTIME_SOURCE_NOT_FOUND")
sys.path.insert(0, str(SRC))

from bybit_workbench.universal_entry.contracts import (  # noqa: E402
    FrozenPolicy,
    StrategyActivation,
)
from bybit_workbench.universal_entry.materializer import materialize_plans  # noqa: E402
from bybit_workbench.universal_entry.p90l90_legacy_strategy import (  # noqa: E402
    MAIN_ID,
    SUB_ID,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (  # noqa: E402
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import (  # noqa: E402
    VERSION,
    new_version_cards,
)
from bybit_workbench.universal_entry.readiness import (  # noqa: E402
    assess_strategy_runtime_readiness,
)
from bybit_workbench.universal_entry.storage import StrategyEntryStore  # noqa: E402

REGISTRY = {
    MAIN_ID: (
        "MAIN", "ta-2122c81ecff14f3b85b102db605212f7",
        "ac-ed73de20410548d6841aeafa5f196f70",
        "activation-p90l90-legacy-main-20261028-v2",
        "binding-p90l90-legacy-main-20261028-v2",
    ),
    SUB_ID: (
        "SUB", "ta-853ac5fcd58185697396d33cd57459fa",
        "ac-ec57819f875f1bfd929a382d6dc8a125",
        "activation-p90l90-legacy-sub-20261028-v2",
        "binding-p90l90-legacy-sub-20261028-v2",
    ),
}
REASON = "OWNER_P90L90_PER_STRATEGY_SECOND48_V2_ISSUE_243"


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError("P90L90_V2_PASSIVE_BLOCKED:" + reason)


def _baseline_embargo() -> StrategyHardStopEmbargoPolicy:
    # Initial v2 values are explicitly owner-approved, and remain immutable.
    # Later parameter changes create new StrategyVersions; no in-place edits.
    return StrategyHardStopEmbargoPolicy.parse({
        "enabled": True,
        "hardstop_count": 2,
        "rolling_hours": 48,
        "new_entry_pause_hours": 48,
        "close_existing_positions": False,
        "domain": "STRATEGY_VERSION",
    })


def install(*, dry_run: bool) -> None:
    _require(os.geteuid() == pwd.getpwnam("cripta").pw_uid, "UNIX_ACTOR")
    _require(
        Path(__file__).resolve() == (
            ROOT / "operations/implementation/install_p90l90_strategy_v2_passive.py"
        ).resolve(), "INSTALLED_SCRIPT_IDENTITY"
    )
    now = datetime.now(UTC)
    cards = new_version_cards(
        approved_at=now,
        policy_by_strategy_id={ident: _baseline_embargo() for ident in REGISTRY},
    )
    _require(len(cards) == 2, "TWO_IMMUTABLE_V2_REQUIRED")
    for card in cards:
        _require(card.strategy_version == VERSION, "CARD_VERSION_DRIFT")
        readiness = assess_strategy_runtime_readiness(card, observer_ready=True)
        _require(
            not readiness.execution_ready and not readiness.active_ready,
            "FAIL_CLOSED_READINESS_EXPECTED_BEFORE_FULL_REPLAY",
        )
    with psycopg.connect("dbname=cripta user=cripta") as connection:
        with connection.transaction():
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, "
                + ("READ ONLY" if dry_run else "READ WRITE")
            )
            identity = connection.execute(
                "SELECT session_user,current_user"
            ).fetchone()
            _require(identity == ("cripta", "cripta"), "POSTGRESQL_ACTOR")
            _require(connection.execute(
                """SELECT has_function_privilege(
                  'cripta',
                  'strategy_entry.register_p90l90_v2_inactive_binding(text,text,text)',
                  'EXECUTE'
                )"""
            ).fetchone() == (True,), "NARROW_V2_BINDING_MIGRATION_REQUIRED")
            _require(not connection.execute(
                """SELECT has_table_privilege(
                  'cripta','strategy_entry.strategy_execution_bindings','INSERT')"""
            ).fetchone()[0], "ARBITRARY_BINDING_INSERT_FORBIDDEN")
            gates = connection.execute(
                "SELECT mode,enabled FROM control.execution_gates "
                "WHERE mode IN ('mainnet','testnet')"
            ).fetchall()
            _require(len(gates) == 2 and all(not bool(g[1]) for g in gates),
                     "REAL_GATES_MUST_BE_OFF")
            for sql in (
                "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled",
                "SELECT count(*) FROM strategy_entry.strategy_activations WHERE enabled",
                "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'",
                "SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE'",
            ):
                _require(connection.execute(sql).fetchone()[0] == 0,
                         "TRADING_CONTROL_ACTIVE")
            previous = connection.execute(
                """SELECT strategy_id,strategy_version FROM
                   strategy_entry.strategy_cards
                  WHERE strategy_id IN (%s,%s)""",
                (MAIN_ID, SUB_ID),
            ).fetchall()
            _require(
                set(previous) == {
                    (MAIN_ID, "1.0-p90l90-legacy-passive-20261027"),
                    (SUB_ID, "1.0-p90l90-legacy-passive-20261027"),
                },
                "EXACT_TWO_HISTORICAL_V1_CARDS_REQUIRED",
            )
            for card in cards:
                kind, account, connector, act_id, bind_id = REGISTRY[card.strategy_id]
                rows = connection.execute(
                    """SELECT a.account_kind,a.account_type,a.environment,a.status,
                              c.status,c.retired_at,c.connector_kind,c.adapter_id
                       FROM identity.trading_accounts a
                       JOIN identity.account_connectors c
                         ON c.trading_account_id=a.trading_account_id
                       WHERE a.trading_account_id=%s
                         AND c.account_connector_id=%s""",
                    (account, connector),
                ).fetchall()
                _require(
                    rows == [(
                        kind, "UNIFIED", "MAINNET", "ACTIVE",
                        "ACTIVE", None, "CEX_PRIVATE_RUNTIME", "BYBIT_V5_PRIVATE",
                    )],
                    "ACCOUNT_CONNECTOR_IDENTITY_" + kind,
                )
                _require(connection.execute(
                    """SELECT count(*) FROM strategy_entry.strategy_activations
                       WHERE activation_id=%s""", (act_id,),
                ).fetchone()[0] == 0, "ACTIVATION_ID_COLLISION_" + kind)
                _require(connection.execute(
                    """SELECT count(*) FROM strategy_entry.strategy_execution_bindings
                       WHERE strategy_execution_binding_id=%s""", (bind_id,),
                ).fetchone()[0] == 0, "BINDING_ID_COLLISION_" + kind)
            if dry_run:
                print("V2_PASSIVE_PREFLIGHT=PASS CARDS=2 BINDINGS=INACTIVE "
                      "ACTIVATIONS=DISABLED EXCHANGE=NONE DB_WRITES=0")
                return
            connection.execute("SELECT pg_advisory_xact_lock(20901290,244)")
            store = StrategyEntryStore(connection)
            for card in cards:
                kind, account, _, act_id, bind_id = REGISTRY[card.strategy_id]
                activation = StrategyActivation(
                    activation_id=act_id,
                    strategy_id=card.strategy_id,
                    strategy_version=card.strategy_version,
                    strategy_config_fingerprint=card.strategy_config_fingerprint,
                    enabled=False,
                    enabled_at=now,
                    disabled_at=now,
                    scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
                    operator="owner",
                    source=REASON,
                )
                plan, exit_plan = materialize_plans(card, activation)
                store.insert_strategy_card(card)
                store.insert_activation(activation, reason=REASON)
                store.insert_entry_plan(plan)
                store.insert_exit_plan(exit_plan)
                v = connection.execute(
                    """SELECT strategy_entry.register_p90l90_v2_inactive_binding(
                       %s,%s,%s)""",
                    (bind_id, act_id, account),
                ).fetchone()
                _require(v == (bind_id,), "REGISTRATION_RESULT_" + kind)
                _require(connection.execute(
                    """SELECT a.enabled,b.binding_state
                       FROM strategy_entry.strategy_activations a
                       JOIN strategy_entry.strategy_execution_bindings b
                         ON b.strategy_activation_id=a.activation_id
                       WHERE a.activation_id=%s
                         AND b.strategy_execution_binding_id=%s""",
                    (act_id, bind_id),
                ).fetchone() == (False, "INACTIVE"), "POSTWRITE_DISABLED_" + kind)
            _require(connection.execute(
                """SELECT count(*) FROM strategy_entry.strategy_cards
                    WHERE strategy_id IN (%s,%s)""",
                (MAIN_ID, SUB_ID),
            ).fetchone()[0] == 4, "HISTORICAL_CARDS_NOT_PRESERVED")
    print("V2_PASSIVE_INSTALL=COMMITTED CARDS=2 ENTRY_PLANS=2 EXIT_PLANS=2 "
          "ACTIVATIONS_DISABLED=2 BINDINGS_INACTIVE=2 PAPER_ONLY_NO_ORDER LIVE=OFF")


if __name__ == "__main__":
    _require(
        len(sys.argv) == 1 or sys.argv == [sys.argv[0], "--check"],
        "UNEXPECTED_ARGUMENTS",
    )
    install(dry_run=len(sys.argv) == 2)
