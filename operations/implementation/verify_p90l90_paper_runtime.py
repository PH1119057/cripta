#!/usr/bin/env python3
"""Authoritative read-only P90L90 PAPER behavior verification after deployment.

No filesystem/DB/exchange writes; no R1 consumer or real order path.
Run only as cripta under the exact installed runtime Python venv.
PASS requires the observer is actually RUNNING and all five P90 native
trade cursors are committed by the original live source on PostgreSQL.
Opening a modeled PAPER position is signal-contingent, not forced.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

ROOT = Path("/srv/cripta/runtime/current")
STATUS = Path("/var/lib/cripta/universal_entry_observer/status.json")
SYMBOLS = frozenset(("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"))
STRATEGIES = (
    "p90l90_legacy_main_ltc_inj",
    "p90l90_legacy_sub_dot_arb_apt",
)
VERSION = "1.1-p90l90-strategy-second48-20261028"


def require(ready: bool, reason: str) -> None:
    if not ready:
        raise RuntimeError("P90_RUNTIME_BEHAVIOR_NOT_VERIFIED:" + reason)


def verify(*, commit: str) -> dict[str, object]:
    require(re.fullmatch("[a-f0-9]{40}", commit) is not None, "INVALID_SOURCE_SHA")
    require(os.geteuid() == pwd.getpwnam("cripta").pw_uid, "INVALID_UNIX_ACTOR")
    require(ROOT.resolve().name == commit, "INSTALLED_RELEASE_NOT_EXACT")
    require((ROOT / "INSTALLED_COMMIT").read_text().strip() == commit,
            "INSTALLED_IDENTITY_NOT_EXACT")
    require(Path(sys.prefix).resolve() == (ROOT / ".venv").resolve(),
            "WRONG_INTERPRETER")
    require(
        Path(__file__).resolve() == (
            ROOT / "operations/implementation/verify_p90l90_paper_runtime.py"
        ).resolve(), "WRONG_SOURCE_SCRIPT",
    )
    status = json.loads(STATUS.read_text(encoding="utf-8"))
    require(isinstance(status, dict), "OBSERVER_STATUS_NOT_OBJECT")
    observed = datetime.fromisoformat(str(status.get("updated_at") or ""))
    require(observed.tzinfo is not None, "OBSERVER_TIME_UNAWARE")
    require(
        timedelta(0) <= datetime.now(UTC)-observed.astimezone(UTC)
        < timedelta(seconds=25),
        "OBSERVER_LIVENESS_NOT_FRESH",
    )
    require(
        status.get("state") == "RUNNING"
        and status.get("runtime_mode") == "MULTI_STRATEGY_OBSERVER"
        and status.get("source_commit") == commit
        and status.get("active_strategies") == 2
        and frozenset(status.get("symbols") or ()) == SYMBOLS
        and int(status.get("facts_received") or 0) > 0
        and status.get("observer_ready") is True,
        "NOT_ACTUALLY_RUNNING_TWO_P90_STRATEGIES",
    )
    from bybit_workbench.universal_entry.p90l90_data_lineage import (
        inspect_p90_data_lineage,
    )

    with psycopg.connect(
        "dbname=cripta user=cripta", connect_timeout=6,
        options="-c default_transaction_read_only=on",
    ) as conn:
        lineage = inspect_p90_data_lineage(conn, loaded_commit=commit)
        require(conn.execute(
            "SELECT session_user,current_user"
        ).fetchone() == ("cripta", "cripta"), "WRONG_PG_ROLE")
        activations = conn.execute(
            """SELECT strategy_id,strategy_version,enabled
                 FROM strategy_entry.strategy_activations
                WHERE enabled ORDER BY strategy_id"""
        ).fetchall()
        require(
            activations == [
                (name, VERSION, True) for name in STRATEGIES
            ], "EXTRA_OR_MISSING_ACTIVE_STRATEGY",
        )
        for sql in (
            "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'",
            "SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE'",
            "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled",
            "SELECT count(*) FROM strategy_entry.paper_orders WHERE strategy_id IN (%s,%s)",
        ):
            args = STRATEGIES if "%s" in sql else ()
            require(conn.execute(sql, args).fetchone() == (0,),
                    "EXCHANGE_OR_PAPER_ORDER_PATH_ENABLED")
        rows = conn.execute(
            """SELECT symbol,source_commit,generation,
                      checkpoint_json->>'last_emitted_trade_sequence',
                      last_writer_release_commit
                 FROM strategy_entry.p90l90_causal_transport_checkpoints
                ORDER BY symbol"""
        ).fetchall()
        require(
            len(rows) == 5
            and {row[0] for row in rows} == SYMBOLS
            and all(
                row[1] == lineage.origin_commit and row[2] > 0
                and row[3] is not None and row[4] == commit
                for row in rows
            ),
            "FIVE_NATIVE_SOURCE_CHECKPOINTS_NOT_ADVANCING",
        )
        paper = conn.execute(
            """SELECT strategy_id,state,count(*)
                 FROM strategy_entry.paper_positions
                WHERE strategy_id IN (%s,%s)
                GROUP BY strategy_id,state ORDER BY strategy_id,state""",
            STRATEGIES,
        ).fetchall()
        bad = conn.execute(
            """SELECT count(*) FROM strategy_entry.paper_positions
                WHERE strategy_id IN (%s,%s)
                  AND (paper_order_id IS NOT NULL OR
                       payload->>'modeled_exchange_fill' IS DISTINCT FROM 'false'
                       OR payload->'p90l90_exit_checkpoint' IS NULL)""",
            STRATEGIES,
        ).fetchone()
        require(bad == (0,), "NON_MODELED_OR_UNATTRIBUTED_P90_POSITION")
        conn.rollback()
    return {
        "verification": "P90L90_RUNTIME_NATIVE_PAPER_BEHAVIOR_VERIFIED",
        "source_commit": commit,
        "data_lineage_id": lineage.data_lineage_id,
        "original_data_commit": lineage.origin_commit,
        "observer_epoch": status.get("observer_epoch_id"),
        "live_facts_processed": int(status["facts_received"]),
        "strategies": list(STRATEGIES),
        "symbols": sorted(SYMBOLS),
        "native_checkpoint_generations": {
            sym: generation for sym, _, generation, _, _ in rows
        },
        "paper_position_counts": [
            {"strategy_id": name, "state": state, "count": count}
            for name, state, count in paper
        ],
        "exchange_orders": 0,
        "live_arm": 0,
        "forced_trades": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(commit=args.source_commit), sort_keys=True))
