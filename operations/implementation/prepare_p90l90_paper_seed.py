#!/usr/bin/env python3
"""Offline-only native P90 PAPER seed after approved release and migrations.

SCRIPT / SERVICE: operations/implementation/prepare_p90l90_paper_seed.py
EFFECTIVE_ACTOR: Unix cripta / matching PostgreSQL role cripta
READ: installed verified runtime source; public Bybit REST closed 5m/15m;
      production control/strategy tables, selected five-symbol archive.
WRITE: ONLY strategy_entry.p90l90_causal_closed_bars and
       strategy_entry.p90l90_causal_transport_checkpoints in --seed mode.
CREATE: at most absent rows in the two exact DB tables. No filesystem write.
RENAME / DELETE / UPDATE EXISTING: none. No systemd service/sandbox.
DB ROLE: cripta, no schema creation/DDL/role changes.
REQUIRED PRECHECK: approved migration, runtime exact release identity, actor,
  inactive v2 Strategy + binding, mainnet/testnet gates OFF, zero REAL
  permissions and LIVE sessions; 7d+ATR200 raw candle coverage.
Output: stdout QC only, no secret or operator credential material.
A seed does not approve PAPER StrategyActivation or REAL Exchange orders.
"""
from __future__ import annotations

import argparse
import os
import pwd
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path("/srv/cripta/runtime/current")
RELEASE = ROOT.resolve()
SHA = re.compile(r"^[0-9a-f]{40}$")
SYMBOLS = ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")
if not (ROOT / "src").is_dir():
    raise SystemExit("P90_SEED_RUNTIME_MISSING")
sys.path.insert(0, str(ROOT / "src"))

from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalEntry  # noqa: E402
from bybit_workbench.universal_entry.p90l90_causal_fact_codec import (  # noqa: E402
    encode_p90l90_fact,
)
from bybit_workbench.universal_entry.p90l90_atomic_paper_checkpoint import (  # noqa: E402
    P90L90AtomicPaperCheckpointStore,
)
from bybit_workbench.universal_entry.p90l90_immutable_bar_ledger import (  # noqa: E402
    P90L90ImmutableBarLedger,
)
from bybit_workbench.universal_entry.p90l90_offline_seed_plan import (  # noqa: E402
    plan_p90l90_offline_seed,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import VERSION  # noqa: E402
from operations.monitoring.universal_entry_shadow import (  # noqa: E402
    _candle_fact,
    _fetch_history,
)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError("P90_SEED_BLOCKED:" + code)


def _actor_identity(commit: str) -> None:
    _require(os.geteuid() == pwd.getpwnam("cripta").pw_uid, "UNIX_ACTOR")
    _require(SHA.fullmatch(commit) is not None, "EXACT_COMMIT_REQUIRED")
    _require(RELEASE.name == commit, "INSTALLED_COMMIT_MISMATCH")
    _require(
        Path(__file__).resolve()
        == (ROOT / "operations/implementation/prepare_p90l90_paper_seed.py").resolve(),
        "INSTALLED_SCRIPT_IDENTITY",
    )
    _require(
        Path(sys.prefix).resolve() == (ROOT / ".venv").resolve(),
        "PINNED_RUNTIME_VENV_REQUIRED",
    )


def _check_db(connection: psycopg.Connection, symbol: str) -> None:
    c = connection
    _require(c.execute(
        "SELECT session_user,current_user"
    ).fetchone() == ("cripta", "cripta"), "DB_ACTOR")
    for name in (
        "strategy_entry.p90l90_causal_transport_checkpoints",
        "strategy_entry.p90l90_causal_closed_bars",
    ):
        _require(
            c.execute("SELECT to_regclass(%s)::text", (name,)).fetchone()[0] == name,
            "MISSING_MIGRATION_" + name.rsplit(".", 1)[1],
        )
    _require(c.execute(
        "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
    ).fetchone() == (0,), "LIVE_ARM_PRESENT")
    _require(c.execute(
        "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled"
    ).fetchone() == (0,), "EXECUTION_PERMISSION_ENABLED")
    gates = c.execute(
        "SELECT mode,enabled FROM control.execution_gates "
        "WHERE mode IN ('mainnet','testnet')"
    ).fetchall()
    _require(len(gates) == 2 and all(not bool(row[1]) for row in gates),
             "EXCHANGE_GATE_ENABLED")
    for ident in ("p90l90_legacy_main_ltc_inj",
                  "p90l90_legacy_sub_dot_arb_apt"):
        rows = c.execute(
            """SELECT a.enabled,b.binding_state
                 FROM strategy_entry.strategy_activations a
                 JOIN strategy_entry.strategy_execution_bindings b
                   ON b.strategy_activation_id=a.activation_id
                WHERE a.strategy_id=%s AND a.strategy_version=%s""",
            (ident, VERSION),
        ).fetchall()
        _require(rows == [(False, "INACTIVE")],
                 "EXACT_DISABLED_V2_MISSING_" + ident)
    _require(c.execute(
        """SELECT count(*) FROM strategy_entry.p90l90_causal_transport_checkpoints
            WHERE symbol=%s""", (symbol,),
    ).fetchone() == (0,), "EXISTING_CHECKPOINT_MUST_NOT_BE_RESET")
    _require(c.execute(
        """SELECT count(*) FROM strategy_entry.p90l90_causal_closed_bars
            WHERE symbol=%s""", (symbol,),
    ).fetchone() == (0,), "EXISTING_CANDLE_LEDGER_MUST_NOT_BE_RESET")


def prepare(*, symbol: str, commit: str, dry_run: bool) -> None:
    _actor_identity(commit)
    _require(symbol in SYMBOLS, "OUTSIDE_OWNER_UNIVERSE")
    with psycopg.connect("dbname=cripta user=cripta") as conn:
        with conn.transaction():
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, "
                + ("READ ONLY" if dry_run else "READ WRITE")
            )
            _check_db(conn, symbol)
            observed = datetime.now(UTC)
            history = _fetch_history(
                symbol, observed, ("5", "15"),
                p90l90_full_prev7d=True,
            )
            plan = plan_p90l90_offline_seed(symbol, history)
            if dry_run:
                print(
                    f"P90_SEED_CHECK=PASS SYMBOL={symbol} COMMIT={commit} "
                    f"5M={len(plan.fives)} 15M={len(plan.fifteens)} "
                    f"CLOSED_5M={plan.closed_5m_at.isoformat()} "
                    f"CLOSED_15M={plan.closed_15m_at.isoformat()} "
                    "WRITE=0 ACTIVATION=DISABLED"
                )
                return
            ledger = P90L90ImmutableBarLedger(conn, source_commit=commit)
            # REST seed facts deliberately carry distinct source identity.
            # A conflicting websocket candle can never silently rewrite REST.
            for candle in (*plan.fives, *plan.fifteens):
                fact = _candle_fact(
                    candle, closed=True, received_at=observed,
                )
                fact = replace(fact, source_refs=(
                    f"bybit:REST:linear:kline:{symbol}:{candle.timeframe}:"
                    f"{candle.opened_at.isoformat()}",
                ))
                _require(
                    ledger.append(encode_p90l90_fact(fact)),
                    "DUPLICATE_SOURCE_BAR",
                )
            restored = ledger.recompute_geometry(
                symbol=symbol, effective_at=plan.closed_5m_at,
            )
            _require(restored == plan.initial_geometry,
                     "PG_RECOMPUTE_PARITY_FAILED")
            store = P90L90AtomicPaperCheckpointStore(conn, source_commit=commit)
            store.seed(
                symbol=symbol,
                last_closed_5m=plan.closed_5m_at,
                last_closed_15m=plan.closed_15m_at,
                entry=P90L90CausalEntry(state=restored),
                proven_geometry=plan.initial_geometry,
            )
            saved = store.inspect(
                symbol=symbol, recomputed_geometry=restored,
            )
            _require(
                saved[1].state == restored and not saved[0].pending
                and saved[1].previous_price is None,
                "ATOMIC_CHECKPOINT_RESTART_PARITY_FAILED",
            )
    print(
        f"P90_SEED_COMMITTED=YES SYMBOL={symbol} COMMIT={commit} "
        f"5M={len(plan.fives)} 15M={len(plan.fifteens)} "
        f"BOUNDARY={plan.closed_5m_at.isoformat()} "
        "ACTIVATION=DISABLED LIVE=OFF"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", choices=SYMBOLS, required=True)
    parser.add_argument("--source-commit", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--seed-disabled-only", action="store_true")
    args = parser.parse_args()
    prepare(
        symbol=args.symbol, commit=args.source_commit,
        dry_run=args.check,
    )


if __name__ == "__main__":
    main()
