"""Atomic installer for two owner-approved *passive* P90L90 Strategy versions.

Run ONLY with an exact approved/released binary under the dedicated postgres
operational actor. No Exchange interaction, credentials, trading activations,
execution permission or LIVE-arm. Non-effective account assignments only.
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
    raise SystemExit("FAIL_CLOSED:INSTALLED_RUNTIME_SOURCE_NOT_FOUND")
sys.path.insert(0, str(SRC))

from bybit_workbench.universal_entry.contracts import (  # noqa: E402
    FrozenPolicy,
    StrategyActivation,
)
from bybit_workbench.universal_entry.materializer import materialize_plans  # noqa: E402
from bybit_workbench.universal_entry.p90l90_legacy_strategy import (  # noqa: E402
    MAIN_ID,
    SUB_ID,
    build_passive_legacy_cards,
)
from bybit_workbench.universal_entry.readiness import (  # noqa: E402
    assess_strategy_runtime_readiness,
)
from bybit_workbench.universal_entry.storage import StrategyEntryStore  # noqa: E402

# Non-secret stable registry IDs, not provider UIDs or credential material.
REGISTRY = {
    MAIN_ID: (
        "MAIN",
        "ta-2122c81ecff14f3b85b102db605212f7",
        "ac-ed73de20410548d6841aeafa5f196f70",
        "activation-p90l90-legacy-main-20261027-v1",
        "binding-p90l90-legacy-main-20261027-v1",
    ),
    SUB_ID: (
        "SUB",
        "ta-853ac5fcd58185697396d33cd57459fa",
        "ac-ec57819f875f1bfd929a382d6dc8a125",
        "activation-p90l90-legacy-sub-20261027-v1",
        "binding-p90l90-legacy-sub-20261027-v1",
    ),
}
REASON = "OWNER_APPROVED_PASSIVE_LEGACY_ACCOUNT_MAPPING_ISSUE_243"


def _require(predicate: bool, code: str) -> None:
    if not predicate:
        raise RuntimeError("PASSIVE_INSTALL_BLOCKED:" + code)


def install(*, dry_run: bool = False) -> None:
    # Security context is part of the installer identity, not a suggestion.
    _require(os.geteuid() == pwd.getpwnam("postgres").pw_uid, "WRONG_UNIX_ACTOR")
    _require(Path(__file__).resolve() == (ROOT / "operations/implementation"
                                          / "install_p90l90_legacy_passive.py").resolve(),
             "WRONG_INSTALLED_SCRIPT_PATH")
    when = datetime.now(UTC)
    cards = build_passive_legacy_cards(approved_at=when)
    _require(len(cards) == 2, "CARD_COUNT")
    for card in cards:
        readiness = assess_strategy_runtime_readiness(card, observer_ready=True)
        _require(not readiness.active_ready and not readiness.execution_ready,
                 "CARD_NOT_FAIL_CLOSED")
    with psycopg.connect("dbname=cripta user=postgres") as connection:
        with connection.transaction():
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, "
                + ("READ ONLY" if dry_run else "READ WRITE")
            )
            _require(connection.execute(
                "SELECT NOT pg_is_in_recovery()"
            ).fetchone() == (True,), "DATABASE_READ_ONLY_RECOVERY")
            # Scoped lock prevents concurrent installers for these exact identities.
            if not dry_run:
                connection.execute("SELECT pg_advisory_xact_lock(20901290, 243)")
            states = connection.execute(
                "SELECT mode, enabled FROM control.execution_gates "
                "WHERE mode IN ('mainnet','testnet')"
            ).fetchall()
            _require(len(states) == 2 and all(not bool(on) for _, on in states),
                     "TRADING_GATE_NOT_OFF")
            _require(connection.execute(
                "SELECT count(*) FROM strategy_entry.execution_permissions "
                "WHERE enabled"
            ).fetchone()[0] == 0, "REAL_PERMISSIONS_ENABLED")
            existing = connection.execute(
                "SELECT strategy_id FROM strategy_entry.strategy_cards "
                "WHERE strategy_id = ANY(%s)", (list(REGISTRY),)
            ).fetchall()
            _require(not existing, "TARGET_STRATEGY_ALREADY_EXISTS_NO_REINSTALL")
            for card in cards:
                kind, ta_id, connector_id, _, _ = REGISTRY[card.strategy_id]
                rows = connection.execute(
                    """SELECT a.account_kind,a.account_type,a.environment,a.status,
                              c.account_connector_id,c.status,c.retired_at,
                              c.connector_kind,c.adapter_id
                       FROM identity.trading_accounts a
                       JOIN identity.account_connectors c
                         ON c.trading_account_id=a.trading_account_id
                       WHERE a.trading_account_id=%s
                         AND c.account_connector_id=%s""",
                    (ta_id, connector_id),
                ).fetchall()
                _require(len(rows) == 1, "ACCOUNT_CONNECTOR_MISMATCH:" + kind)
                ak, at, env, astate, cid, cstate, retired, ckind, adapter = rows[0]
                _require(
                    (ak, at, env, astate, cid, cstate, retired, ckind, adapter)
                    == (kind, "UNIFIED", "MAINNET", "ACTIVE", connector_id, "ACTIVE",
                        None, "CEX_PRIVATE_RUNTIME", "BYBIT_V5_PRIVATE"),
                    "ACCOUNT_NOT_REGISTERED:" + kind,
                )
                other = connection.execute(
                    """SELECT count(*) FROM identity.account_connectors
                       WHERE trading_account_id=%s AND status='ACTIVE'
                         AND retired_at IS NULL
                         AND connector_kind='CEX_PRIVATE_RUNTIME'
                         AND adapter_id='BYBIT_V5_PRIVATE'""",
                    (ta_id,),
                ).fetchone()[0]
                _require(other == 1, "ACCOUNT_CONNECTOR_AMBIGUOUS:" + kind)
                _require(connection.execute(
                    "SELECT count(*) FROM strategy_entry.strategy_activations "
                    "WHERE activation_id=%s", (REGISTRY[card.strategy_id][3],)
                ).fetchone()[0] == 0, "ACTIVATION_COLLISION:" + kind)
                _require(connection.execute(
                    "SELECT count(*) FROM strategy_entry.strategy_execution_bindings "
                    "WHERE strategy_execution_binding_id=%s",
                    (REGISTRY[card.strategy_id][4],)
                ).fetchone()[0] == 0, "BINDING_COLLISION:" + kind)
            if dry_run:
                print("PASSIVE_PREFLIGHT=PASS CARDS=2 BINDINGS=INACTIVE "
                      "ACTIVATIONS=DISABLED DB_WRITES=0 LIVE=OFF")
                return
            store = StrategyEntryStore(connection)
            for card in cards:
                kind, ta_id, _, activation_id, binding_id = REGISTRY[card.strategy_id]
                activation = StrategyActivation(
                    activation_id=activation_id,
                    strategy_id=card.strategy_id,
                    strategy_version=card.strategy_version,
                    strategy_config_fingerprint=card.strategy_config_fingerprint,
                    enabled=False,
                    enabled_at=when,
                    disabled_at=when,
                    scope=FrozenPolicy.from_mapping(
                        {"mode": "EXACT_STRATEGY_VERSION"}
                    ),
                    operator="owner",
                    source=REASON,
                )
                entry, exit_plan = materialize_plans(card, activation)
                store.insert_strategy_card(card)
                store.insert_activation(activation, reason=REASON)
                store.insert_entry_plan(entry)
                store.insert_exit_plan(exit_plan)
                connection.execute(
                    """INSERT INTO strategy_entry.strategy_execution_bindings(
                      strategy_execution_binding_id,strategy_activation_id,
                      execution_leg,execution_role,trading_account_id,
                      allocation_mode,binding_state,effective_from,effective_to,
                      operator,source,reason
                    ) VALUES (%s,%s,'PRIMARY','PRIMARY',%s,
                              'DEDICATED_STRATEGY','INACTIVE',%s,NULL,
                              'owner',%s,%s)""",
                    (binding_id, activation_id, ta_id, when, REASON, REASON),
                )
                _require(connection.execute(
                    """SELECT a.enabled,b.binding_state,ec.enabled
                       FROM strategy_entry.strategy_activations a
                       JOIN strategy_entry.strategy_execution_bindings b
                         ON a.activation_id=b.strategy_activation_id
                       LEFT JOIN strategy_entry.execution_permissions ec
                         ON ec.strategy_id=a.strategy_id
                        AND ec.strategy_version=a.strategy_version
                        AND ec.strategy_config_fingerprint=a.strategy_config_fingerprint
                       WHERE a.activation_id=%s AND b.strategy_execution_binding_id=%s""",
                    (activation_id, binding_id),
                ).fetchone() == (False, "INACTIVE", None), "POSTWRITE_STATE:" + kind)
            _require(connection.execute(
                "SELECT count(*) FROM strategy_entry.strategy_cards "
                "WHERE strategy_id = ANY(%s)", (list(REGISTRY),)
            ).fetchone()[0] == 2, "CARD_WRITE_PARITY")
            _require(connection.execute(
                "SELECT count(*) FROM strategy_entry.strategy_activations "
                "WHERE enabled"
            ).fetchone()[0] == 0, "ENABLED_ACTIVATION_EXISTS")
    # Connection context committed successfully before claiming installed.
    print("PASSIVE_INSTALL=COMMITTED CARDS=2 ENTRY_PLANS=2 EXIT_PLANS=2 "
          "ACTIVATIONS_DISABLED=2 BINDINGS_INACTIVE=2 LIVE=OFF")


if __name__ == "__main__":
    _require(len(sys.argv) == 1 or sys.argv == [sys.argv[0], "--check"],
             "UNEXPECTED_ARGUMENTS")
    install(dry_run=len(sys.argv) == 2)
