#!/usr/bin/env python3
"""Exact owner P90L90 MAIN/SUB REAL v3 authoring; NEVER activates LIVE.

SCRIPT: operations/implementation/install_p90l90_real_v3_inactive.py
EFFECTIVE_ACTOR: Unix cripta; PostgreSQL session/current_user cripta
READ_PATHS: installed /srv/cripta/runtime/current source/INSTALLED_COMMIT,
            installed pinned venv, production Strategy/Identity/Control rows
WRITE_PATHS: none (filesystem). PostgreSQL only under --install: exactly
             2 StrategyCards, 2 disabled StrategyActivations,
             2 EntryPlans + 2 ExitPlans, 2 inactive dedicated bindings
             through SECURITY DEFINER narrow v3 registration function.
CREATE_CHILD_PARENTS/RENAME/DELETE: none
SYSTEMD_SANDBOX: no new service (one-shot operator diagnostic)
DB_ROLE: cripta; no DDL, role/grant, arbitrary binding INSERT or UPDATE
EXPECTED_OWNER/GROUP/MODE/ACL: current installed release immutable;
             migration applies separately via approved postgres actor.
PREFLIGHT_COMMAND: exact installed cripta Python + this script --check.
No Exchange API, filesystem writes, gate, permission, enabled Strategy, arms,
source checkpoint, PAPER order or old v1/v2 policy mutation at any point.
The installing phase requires explicit --install after successful preflight.
"""
from __future__ import annotations

import os
import pwd
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path("/srv/cripta/runtime/current")
SHA = re.compile(r"[0-9a-f]{40}\Z")
if not (ROOT / "src").is_dir():
    raise SystemExit("P90_REAL_V3_INSTALLED_SOURCE_MISSING")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from bybit_workbench.universal_entry.contracts import (  # noqa: E402
    FrozenPolicy,
    StrategyActivation,
)
from bybit_workbench.universal_entry.materializer import materialize_plans  # noqa: E402
from bybit_workbench.universal_entry.p90l90_legacy_strategy import (  # noqa: E402
    MAIN_ID,
    SUB_ID,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (  # noqa: E402
    ALLOWED_SYMBOLS,
    VERSION,
    real_v3_cards,
)
from bybit_workbench.universal_entry.storage import StrategyEntryStore  # noqa: E402
from operations.implementation.install_p90l90_strategy_v2_passive import (  # noqa: E402
    REGISTRY as V2_REGISTRY,
    _baseline_embargo,
)

REGISTRY = {
    MAIN_ID: (
        "MAIN", "ta-2122c81ecff14f3b85b102db605212f7",
        "ac-ed73de20410548d6841aeafa5f196f70",
        "activation-p90l90-real-main-20261029-v3",
        "binding-p90l90-real-main-20261029-v3",
    ),
    SUB_ID: (
        "SUB", "ta-853ac5fcd58185697396d33cd57459fa",
        "ac-ec57819f875f1bfd929a382d6dc8a125",
        "activation-p90l90-real-sub-20261029-v3",
        "binding-p90l90-real-sub-20261029-v3",
    ),
}
WRITER = (
    "strategy_entry.register_p90l90_real_v3_inactive_binding(text,text,text)"
)


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError("P90_REAL_V3_INSTALL_BLOCKED:" + reason)


def installed_identity() -> str:
    release = ROOT.resolve()
    commit = release.name
    require(os.geteuid() == pwd.getpwnam("cripta").pw_uid, "UNIX_ACTOR")
    require(SHA.fullmatch(commit) is not None, "INSTALLED_COMMIT_INVALID")
    require((ROOT / "INSTALLED_COMMIT").read_text().strip() == commit,
            "INSTALLED_COMMIT_MARKER_DRIFT")
    require(Path(sys.prefix).resolve() == (release / ".venv").resolve(),
            "WRONG_PINNED_VENV")
    require(
        Path(__file__).resolve() == (
            release / "operations/implementation/install_p90l90_real_v3_inactive.py"
        ).resolve(),
        "INSTALLED_SCRIPT_IDENTITY",
    )
    return commit


def preflight(connection: psycopg.Connection, at: datetime):
    require(
        connection.execute("SELECT session_user,current_user").fetchone()
        == ("cripta", "cripta"), "DB_ACTOR",
    )
    require(connection.execute(
        "SELECT has_table_privilege('cripta',"
        "'strategy_entry.strategy_execution_bindings','INSERT')"
    ).fetchone() == (False,), "BROAD_BINDING_WRITE_FORBIDDEN")
    require(connection.execute(
        "SELECT has_function_privilege('cripta',%s,'EXECUTE')",
        (WRITER,),
    ).fetchone() == (True,), "NARROW_V3_MIGRATION_MISSING")

    gates = connection.execute(
        "SELECT mode,enabled FROM control.execution_gates "
        "WHERE mode IN ('mainnet','testnet') ORDER BY mode"
    ).fetchall()
    require(gates == [("mainnet", 0), ("testnet", 0)]
            or gates == [("mainnet", False), ("testnet", False)],
            "REAL_OR_TESTNET_GATE_NOT_DISARMED")
    for query in (
        "SELECT count(*) FROM strategy_entry.strategy_activations WHERE enabled",
        "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled",
        "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'",
        "SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE'",
        "SELECT count(*) FROM runtime.position_ownership "
        "WHERE strategy_version='2.0-p90l90-real-main-sub-20261028' "
        "AND state IN ('OPEN','RECONCILIATION_REQUIRED')",
    ):
        require(connection.execute(query).fetchone() == (0,),
                "EXISTING_ACTIVE_TRADING_OR_EXPOSURE")

    old = connection.execute(
        "SELECT strategy_id,strategy_version FROM strategy_entry.strategy_cards "
        "WHERE strategy_id IN (%s,%s) ORDER BY strategy_id,strategy_version",
        (MAIN_ID, SUB_ID),
    ).fetchall()
    expected = {
        (key, version)
        for key in (MAIN_ID, SUB_ID)
        for version in (
            "1.0-p90l90-legacy-passive-20261027",
            "1.1-p90l90-strategy-second48-20261028",
        )
    }
    require(set(old) == expected and len(old) == 4,
            "EXACT_OLD_V1_V2_CARDS_ONLY_REQUIRED")
    cards = real_v3_cards(
        approved_at=at,
        policy_by_strategy_id={
            MAIN_ID: _baseline_embargo(),
            SUB_ID: _baseline_embargo(),
        },
    )
    require(
        len(cards) == 2 and set(card.strategy_id for card in cards)
        == {MAIN_ID, SUB_ID}
        and all(card.strategy_version == VERSION for card in cards),
        "TWO_IMMUTABLE_V3_CARDS_REQUIRED",
    )
    prepared = []
    for card in cards:
        kind, account, connector, activation_id, binding_id = (
            REGISTRY[card.strategy_id]
        )
        require(
            frozenset(card.symbols) == ALLOWED_SYMBOLS[card.strategy_id],
            "EXACT_REAL_V3_FIVE_SYMBOLS_REQUIRED",
        )
        v2 = V2_REGISTRY[card.strategy_id]
        require((kind, account, connector) == v2[:3],
                "MAIN_SUB_ORIGINAL_ACCOUNT_IDENTITY_DRIFT")
        identity = connection.execute(
            """SELECT a.account_kind,a.account_type,a.environment,a.status,
                      a.venue_id,(a.external_account_ref ~ '^[0-9]+$'),
                      c.status,c.retired_at,c.connector_kind,c.adapter_id,
                      (nullif(btrim(c.credential_ref),'') IS NOT NULL)
                 FROM identity.trading_accounts a
                 JOIN identity.account_connectors c
                   ON c.trading_account_id=a.trading_account_id
                WHERE a.trading_account_id=%s
                  AND c.account_connector_id=%s""",
            (account, connector),
        ).fetchall()
        require(
            identity == [(
                kind, "UNIFIED", "MAINNET", "ACTIVE", "venue-bybit", True,
                "ACTIVE", None, "CEX_PRIVATE_RUNTIME", "BYBIT_V5_PRIVATE", True,
            )],
            "EXACT_ACCOUNT_CONNECTOR_UNVERIFIED_" + kind,
        )
        historic = connection.execute(
            """SELECT a.enabled,a.disabled_at,b.binding_state
                 FROM strategy_entry.strategy_activations a
                 JOIN strategy_entry.strategy_execution_bindings b
                   ON b.strategy_activation_id=a.activation_id
                WHERE a.activation_id=%s
                  AND b.strategy_execution_binding_id=%s""",
            (v2[3], v2[4]),
        ).fetchall()
        require(
            len(historic) == 1 and historic[0][0] is False
            and historic[0][1] is not None
            and historic[0][2] == "INACTIVE",
            "HISTORICAL_V2_NOT_INACTIVE_" + kind,
        )
        require(
            connection.execute(
                "SELECT count(*) FROM strategy_entry.strategy_activations "
                "WHERE activation_id=%s",
                (activation_id,),
            ).fetchone() == (0,),
            "V3_ACTIVATION_COLLISION_" + kind,
        )
        require(
            connection.execute(
                "SELECT count(*) FROM strategy_entry.strategy_execution_bindings "
                "WHERE strategy_execution_binding_id=%s",
                (binding_id,),
            ).fetchone() == (0,),
            "V3_BINDING_COLLISION_" + kind,
        )
        activation = StrategyActivation(
            activation_id=activation_id,
            strategy_id=card.strategy_id,
            strategy_version=VERSION,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            enabled=False,
            enabled_at=at,
            disabled_at=at,
            scope=FrozenPolicy.from_mapping({"mode": "EXACT_STRATEGY_VERSION"}),
            operator="owner",
            source="OWNER_P90L90_REAL_V3_ISSUE_243",
        )
        entry, exit_plan = materialize_plans(card, activation)
        prepared.append((card, activation, entry, exit_plan, binding_id, account))
    require(len(prepared) == 2, "TWO_V3_STRATEGIES_REQUIRED")
    return tuple(prepared)


def install(*, dry_run: bool) -> None:
    commit = installed_identity()
    now = datetime.now(UTC)
    with (
        psycopg.connect("dbname=cripta user=cripta") as connection,
        connection.transaction(),
    ):
        connection.execute(
            "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, "
            + ("READ ONLY" if dry_run else "READ WRITE")
        )
        prepared = preflight(connection, now)
        if dry_run:
            print(
                "P90_REAL_V3_PREFLIGHT=PASS PAIRS=2 "
                "MAIN_LTC_INJ=DISABLED SUB_DOT_ARB_APT=DISABLED "
                "EXCHANGE=OFF DB_WRITES=0 INSTALLED_COMMIT=" + commit
            )
            return
        connection.execute("SELECT pg_advisory_xact_lock(20901290,243)")
        store = StrategyEntryStore(connection)
        for card, activation, entry, exit_plan, binding_id, account in prepared:
            store.insert_strategy_card(card)
            store.insert_activation(
                activation, reason="V3_INACTIVE_FOR_SOURCE_LIVE_EQUIVALENCE",
            )
            store.insert_entry_plan(entry)
            store.insert_exit_plan(exit_plan)
            outcome = connection.execute(
                """SELECT strategy_entry.
                          register_p90l90_real_v3_inactive_binding(%s,%s,%s)""",
                (binding_id, activation.activation_id, account),
            ).fetchone()
            require(outcome == (binding_id,), "V3_NARROW_BINDING_WRITE_FAILED")
        rows = connection.execute(
            """SELECT a.strategy_id,a.enabled,b.binding_state,b.trading_account_id
                 FROM strategy_entry.strategy_activations a
                 JOIN strategy_entry.strategy_execution_bindings b
                   ON b.strategy_activation_id=a.activation_id
                WHERE a.strategy_version=%s ORDER BY a.strategy_id""",
            (VERSION,),
        ).fetchall()
        require(
            rows == [
                (MAIN_ID, False, "INACTIVE", REGISTRY[MAIN_ID][1]),
                (SUB_ID, False, "INACTIVE", REGISTRY[SUB_ID][1]),
            ],
            "V3_POSTWRITE_INACTIVE_MAIN_SUB_NOT_PROVEN",
        )
    print(
        "P90_REAL_V3_INSTALL=COMMITTED CARDS=2 ENTRY_PLANS=2 EXIT_PLANS=2 "
        "ACTIVATIONS_DISABLED=2 BINDINGS_INACTIVE=2 "
        "EXCHANGE=NONE LIVE_ARM=OFF"
    )


if __name__ == "__main__":
    require(
        sys.argv[1:] in (["--check"], ["--install"]),
        "EXPLICIT_CHECK_OR_INSTALL_FLAG_REQUIRED",
    )
    install(dry_run=sys.argv[1:] == ["--check"])
