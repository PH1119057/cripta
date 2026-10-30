#!/usr/bin/env python3
"""Owner-approved, TWO-strategy P90L90 PAPER activation rail, never REAL.

Exact actor: Unix cripta, PostgreSQL session/current_user cripta.
Exact installed source: /srv/cripta/runtime/current/operations/implementation/
    activate_p90l90_paper_v2.py, same installed release SHA and pinned venv.
READ: identity/account control, immutable cards/plans/bindings, source-pinned
    five-symbol P90 closed-bar archive/checkpoints; live public Bybit
    REST time and read-only WS topic acknowledgements (mirror+5m/15m).
WRITE/UPDATE: ONLY the exact two existing P90 v2 StrategyActivation rows
    (enabled,enabled_at,disabled_at,operator,source,change_reason), in ONE
    serializable PG transaction, after the exact no-order PAPER proof.
CREATE/DELETE/FS WRITE: NONE. Exchange/order/capital mutation: NONE.
No change to other/archived StrategyActivations, permissions or LIVE-arm.
If either audit/commit fails, BOTH activations remain disabled.
This action does not install source, apply migrations or clear safety blocks.
"""
from __future__ import annotations

import argparse
import os
import pwd
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

ROOT = Path("/srv/cripta/runtime/current")
SHA = re.compile(r"^[0-9a-f]{40}$")
if not (ROOT / "src").is_dir():
    raise SystemExit("P90_ACTIVATION_INSTALLED_SOURCE_MISSING")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from bybit_workbench.universal_entry.contracts import (  # noqa: E402
    FrozenPolicy, StrategyActivation,
)
from bybit_workbench.universal_entry.materializer import materialize_plans  # noqa: E402
from bybit_workbench.universal_entry.p90l90_legacy_strategy import (  # noqa: E402
    MAIN_ID, SUB_ID,
)
from bybit_workbench.universal_entry.p90l90_data_lineage import (  # noqa: E402
    inspect_p90_data_lineage,
)
from bybit_workbench.universal_entry.p90l90_shared_candle_bridge import (  # noqa: E402
    P90SharedCandleLedger,
)
from bybit_workbench.universal_entry.p90l90_observer_paper_consumer import (  # noqa: E402
    P90L90ObserverPaperConsumer,
)
from bybit_workbench.universal_entry.p90l90_observer_strategy_partition import (  # noqa: E402
    P90NativeBarRuntimeProof, ObserverStrategySplit,
    require_p90_atomic_observer_connection,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import (  # noqa: E402
    VERSION, new_version_cards,
)
from bybit_workbench.universal_entry.paper_runtime import PaperTradeRuntime  # noqa: E402
from bybit_workbench.universal_entry.runtime_loader import (  # noqa: E402
    ActiveStrategyBundle,
)
from bybit_workbench.universal_entry.storage import StrategyEntryStore  # noqa: E402
from operations.implementation.install_p90l90_strategy_v2_passive import (  # noqa: E402
    REGISTRY, _baseline_embargo,
)
from operations.monitoring.universal_entry_shadow import (  # noqa: E402
    _connect_and_subscribe,
    _connect_public_trade_mirror,
)


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError("P90_PAPER_ACTIVATION_BLOCKED:" + reason)


def installed_identity(commit: str) -> None:
    require(os.geteuid() == pwd.getpwnam("cripta").pw_uid, "WRONG_UNIX_ACTOR")
    require(SHA.fullmatch(commit) is not None, "SOURCE_SHA_NOT_EXACT")
    require(ROOT.resolve().name == commit, "INSTALLED_RELEASE_SHA_MISMATCH")
    require((ROOT / "INSTALLED_COMMIT").read_text().strip() == commit,
            "INSTALLED_COMMIT_FILE_MISMATCH")
    require(Path(sys.prefix).resolve() == (ROOT / ".venv").resolve(),
            "WRONG_RUNTIME_VENV")
    require(
        Path(__file__).resolve() == (
            ROOT / "operations/implementation/activate_p90l90_paper_v2.py"
        ).resolve(), "SCRIPT_NOT_IN_EXACT_INSTALLED_RELEASE",
    )


def preflight(connection: psycopg.Connection, commit: str) -> tuple[
    tuple[ActiveStrategyBundle, ...], frozenset[str],
]:
    require(
        connection.execute("SELECT session_user,current_user").fetchone()
        == ("cripta", "cripta"), "DB_ROLE_NOT_CRIPTA",
    )
    require(
        connection.execute(
            "SELECT count(*) FROM strategy_entry.strategy_activations WHERE enabled"
        ).fetchone() == (0,), "ANOTHER_STRATEGY_ALREADY_ACTIVE",
    )
    for sql, code in (
        ("SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'",
         "LIVE_ARM_ACTIVE"),
        ("SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE'",
         "ACCOUNT_LIVE_ARM_ACTIVE"),
        ("SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled",
         "REAL_EXECUTION_PERMISSION_ENABLED"),
    ):
        require(connection.execute(sql).fetchone() == (0,), code)
    require(
        connection.execute(
            "SELECT mode,enabled FROM control.execution_gates "
            "WHERE mode IN ('mainnet','testnet')"
        ).fetchall() in (
            [("mainnet", False), ("testnet", False)],
            [("testnet", False), ("mainnet", False)],
        ), "MAINNET_OR_TESTNET_GATE_ON",
    )
    require(
        connection.execute(
            "SELECT count(*) FROM strategy_entry.paper_orders "
            "WHERE strategy_id IN (%s,%s)",
            (MAIN_ID, SUB_ID),
        ).fetchone() == (0,), "P90_MUST_NOT_CREATE_EXCHANGE_OR_PAPER_ORDERS",
    )
    approved_at = datetime.now(UTC)
    cards = new_version_cards(
        approved_at=approved_at,
        policy_by_strategy_id={
            MAIN_ID: _baseline_embargo(), SUB_ID: _baseline_embargo(),
        },
    )
    require(len(cards) == 2 and all(c.strategy_version == VERSION for c in cards),
            "INCORRECT_IMMUTABLE_V2_UNIVERSE")
    bundles: list[ActiveStrategyBundle] = []
    for card in cards:
        kind, account, connector, activation_id, binding_id = REGISTRY[card.strategy_id]
        row = connection.execute(
            """SELECT a.enabled,a.disabled_at,a.strategy_config_fingerprint,
                      b.binding_state,b.trading_account_id,c.card_json,
                      p.entry_plan_fingerprint,x.exit_plan_fingerprint
                 FROM strategy_entry.strategy_activations a
                 JOIN strategy_entry.strategy_execution_bindings b
                   ON b.strategy_activation_id=a.activation_id
                  AND b.strategy_execution_binding_id=%s
                  AND b.execution_leg='PRIMARY' AND b.execution_role='PRIMARY'
                 JOIN strategy_entry.strategy_cards c
                   ON c.strategy_id=a.strategy_id
                  AND c.strategy_version=a.strategy_version
                  AND c.strategy_config_fingerprint=a.strategy_config_fingerprint
                 JOIN strategy_entry.entry_plans p
                   ON p.strategy_id=a.strategy_id
                  AND p.strategy_version=a.strategy_version
                  AND p.strategy_config_fingerprint=a.strategy_config_fingerprint
                 JOIN strategy_entry.exit_plans x
                   ON x.strategy_id=a.strategy_id
                  AND x.strategy_version=a.strategy_version
                  AND x.strategy_config_fingerprint=a.strategy_config_fingerprint
                WHERE a.activation_id=%s AND a.strategy_id=%s
                  AND a.strategy_version=%s""",
            (binding_id, activation_id, card.strategy_id, VERSION),
        ).fetchall()
        require(
            len(row) == 1
            and row[0][0] is False and row[0][1] is not None
            and row[0][2] == card.strategy_config_fingerprint
            and row[0][3] == "INACTIVE" and row[0][4] == account
            and isinstance(row[0][5], dict),
            "MISSING_EXACT_DISABLED_STRATEGY_" + kind,
        )
        identity = connection.execute(
            """SELECT a.account_kind,a.account_type,a.environment,a.status,
                      c.status,c.retired_at,c.connector_kind,c.adapter_id
                 FROM identity.trading_accounts a
                 JOIN identity.account_connectors c
                   ON c.trading_account_id=a.trading_account_id
                WHERE a.trading_account_id=%s AND c.account_connector_id=%s""",
            (account, connector),
        ).fetchall()
        require(
            identity == [(kind, "UNIFIED", "MAINNET", "ACTIVE",
                          "ACTIVE", None, "CEX_PRIVATE_RUNTIME", "BYBIT_V5_PRIVATE")],
            "UNVERIFIED_DEDICATED_ACCOUNT_" + kind,
        )
        activation = StrategyActivation(
            activation_id=activation_id,
            strategy_id=card.strategy_id, strategy_version=VERSION,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            enabled=True, enabled_at=approved_at,
        )
        entry, exit_ = materialize_plans(card, activation)
        require(
            (row[0][6], row[0][7]) ==
            (entry.entry_plan_fingerprint, exit_.exit_plan_fingerprint),
            "IMMUTABLE_MATERIALIZED_PLANS_CHANGED_" + kind,
        )
        bundles.append(ActiveStrategyBundle(
            card, activation, entry, exit_, approved_at,
        ))
    all_symbols = frozenset(
        symbol for bundle in bundles for symbol in bundle.card.symbols
    )
    require(len(all_symbols) == 5, "P90_FIVE_EXACT_SYMBOLS_REQUIRED")
    lineage = inspect_p90_data_lineage(connection, loaded_commit=commit)
    source_rows = connection.execute(
        """SELECT symbol,source_commit FROM
           strategy_entry.p90l90_causal_transport_checkpoints ORDER BY symbol"""
    ).fetchall()
    require(
        source_rows == [
            (symbol, lineage.origin_commit) for symbol in sorted(all_symbols)
        ],
        "INCOMPLETE_OR_FOREIGN_CAUSAL_CHECKPOINT",
    )
    bars = connection.execute(
        """SELECT symbol,timeframe,max(closed_at),count(*)
             FROM strategy_entry.p90l90_causal_closed_bars
            GROUP BY symbol,timeframe ORDER BY symbol,timeframe"""
    ).fetchall()
    require(
        len(bars) == 10 and all(
            row[0] in all_symbols and row[1] in {"5", "15"}
            and row[3] >= (2216 if row[1] == "5" else 200)
            and row[2].tzinfo is not None
            for row in bars
        ), "P90_FULL_PREV7D_CANDLE_HISTORY_REQUIRED",
    )
    # Unlike the original legacy preflight, verify that the one canonical
    # source has ALL actual closed bars between the frozen checkpoint and
    # a fresh Bybit watermark. No PAPER position/Entry can be inferred from
    # these historical bars, and no SQL write occurs in this check.
    bridge = P90SharedCandleLedger(
        connection, source_commit=lineage.origin_commit,
        producer_release_commit=commit,
        accepted_source_commits=lineage.approved_producer_commits,
    )
    for symbol in sorted(all_symbols):
        endpoints = []
        for tf in ("5", "15"):
            key = bridge._key[symbol, tf]
            endpoint = connection.execute(
                """SELECT max(closed_at) FROM analytics.closed_candles
                    WHERE market_source_id=%s AND instrument_id=%s
                      AND timeframe=%s""",
                (key.market_source_id, key.instrument_id, tf),
            ).fetchone()[0]
            require(
                endpoint is not None and
                timedelta(0) <= datetime.now(UTC)-endpoint <
                timedelta(minutes=10 if tf == "5" else 20),
                "SHARED_CLOSED_SOURCE_NOT_FRESH_" + symbol + "_" + tf,
            )
            endpoints.append(endpoint)
        bridge.closed_gap_plan(
            symbol=symbol,
            through_5m=endpoints[0], through_15m=endpoints[1],
        )
    return tuple(bundles), all_symbols


def prove_native_source(
    connection: psycopg.Connection,
    bundles: tuple[ActiveStrategyBundle, ...],
    symbols: frozenset[str],
    commit: str,
) -> P90NativeBarRuntimeProof:
    # Uses the exact running-code consumer in read-only mode, not a custom
    # substitute for Entry/Exit recovery.
    lineage = inspect_p90_data_lineage(connection, loaded_commit=commit)
    consumer = P90L90ObserverPaperConsumer(
        connection, bundles=bundles, source_commit=lineage.origin_commit,
        producer_release_commit=commit,
        accepted_source_commits=lineage.approved_producer_commits,
        shared_candle_source=True,
        provenance=FrozenPolicy.from_mapping({
            "source": "P90_OWNER_PAPER_ACTIVATION_CHECK",
            "source_commit": commit,
            "data_lineage_id": lineage.data_lineage_id,
            "data_origin_commit": lineage.origin_commit,
        }),
    )
    consumer.recover_all()
    mirror = None
    ws = None
    started_at = datetime.now(UTC)
    try:
        mirror, _, _ = _connect_public_trade_mirror(tuple(sorted(symbols)))
        ws, subscribed_at, server_at, _ = _connect_and_subscribe(
            tuple(sorted(symbols)), ("5", "15"),
        )
        proof = P90NativeBarRuntimeProof(
            source_commit=commit,
            restored_symbols=consumer.recovered_symbols,
            subscribed_symbols=symbols,
            subscribed_intervals=frozenset({"5", "15"}),
            mirror_active=mirror is not None,
            subscribed_local_at=subscribed_at,
            subscribed_server_at=server_at,
            epoch_started_at=started_at,
        )
        require_p90_atomic_observer_connection(
            ObserverStrategySplit((), bundles), live_proof=proof,
        )
        return proof
    finally:
        if ws is not None:
            ws.close()
        if mirror is not None:
            mirror.close()


def perform(commit: str, *, activate: bool) -> None:
    installed_identity(commit)
    # No production mutation in --check: read-only transaction and public
    # WebSocket subscriptions, with no API keys or private exchange requests.
    with psycopg.connect("dbname=cripta user=cripta") as connection:
        with connection.transaction():
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, READ ONLY"
            )
            bundles, symbols = preflight(connection, commit)
            proof = prove_native_source(connection, bundles, symbols, commit)
    if not activate:
        print(
            "P90_PAPER_ACTIVATION_PREFLIGHT=PASS STRATEGIES=2 SYMBOLS=5 "
            "NATIVE_WS=PROVEN CHECKPOINTS=PROVEN "
            "EXCHANGE_ORDERS=0 DB_WRITES=0"
        )
        return
    require(
        datetime.now(UTC) - proof.subscribed_local_at < timedelta(seconds=45),
        "LIVE_SOURCE_PROOF_STALE_BEFORE_ACTIVATION",
    )
    with psycopg.connect("dbname=cripta user=cripta") as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, READ WRITE")
            connection.execute(
                "SELECT pg_advisory_xact_lock(20901290,243)"
            )
            current, exact_symbols = preflight(connection, commit)
            require(
                exact_symbols == symbols
                and [x.exact_identity for x in current] ==
                [x.exact_identity for x in bundles],
                "P90_ACTIVATION_IDENTITY_CHANGED_AFTER_PROOF",
            )
            store = StrategyEntryStore(connection)
            now = datetime.now(UTC)
            for bundle in current:
                store.set_activation_enabled(
                    bundle.activation.activation_id,
                    enabled=True, changed_at=now,
                    operator="owner",
                    source="P90_PAPER_ONLY_ISSUE_243",
                    reason="OWNER_APPROVED_NO_ORDER_NATIVE_PAPER_ONLY",
                )
            rows = connection.execute(
                """SELECT strategy_id,enabled,disabled_at
                     FROM strategy_entry.strategy_activations
                    WHERE activation_id IN (%s,%s) ORDER BY strategy_id""",
                (REGISTRY[MAIN_ID][3], REGISTRY[SUB_ID][3]),
            ).fetchall()
            require(
                len(rows) == 2 and all(x[1] is True and x[2] is None for x in rows),
                "P90_ATOMIC_ACTIVATION_NOT_APPLIED",
            )
            require(
                connection.execute(
                    "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled"
                ).fetchone() == (0,),
                "EXCHANGE_PERMISSION_CHANGED_DURING_ACTIVATION",
            )
    print(
        f"P90_PAPER_ACTIVATION_COMMITTED=YES SOURCE_COMMIT={commit} "
        "STRATEGIES=2 SYMBOLS=5 REAL_ORDERS=0 LIVE_ARM=OFF "
        "RUNTIME_BEHAVIOR_EVIDENCE=REQUIRED"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--activate-paper", action="store_true")
    args = parser.parse_args()
    perform(args.source_commit, activate=args.activate_paper)


if __name__ == "__main__":
    main()
