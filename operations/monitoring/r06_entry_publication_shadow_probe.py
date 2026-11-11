"""R06 read-only production/CI parity probe, NEVER an Entry decision source.

It checks one current immutable publication and the exact protected P90 5m/15m
closed history needed at cold seed. No public Bybit calls, table writes, event
ACK, Strategy/Exit/Execution mutation, or server service start.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bybit_workbench.market_data.entry_publication_reader import (
    EntryPublicationUnavailable,
    read_entry_publication_pin,
    read_entry_publication_seed,
)
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    NativeSourceProofUnavailable,
    verify_native_producer_code_compatibility,
)

FIVE = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")
P90_PLAN_IDS = frozenset((
    "p90l90_legacy_main_ltc_inj",
    "p90l90_legacy_sub_dot_arb_apt",
))
# Existing Observer R03 cold source: 2600 five-minute and 1000 fifteen-minute
# bars. NOT new Strategy thresholds or an independent policy default.
EXISTING_P90_COLD_COUNTS = {"5": 2600, "15": 1000}
SOURCE_CHECKOUT = Path("/srv/cripta/source_checkout")
INSTALLED_COMMIT = Path("/srv/cripta/runtime/current/INSTALLED_COMMIT")
DB_DSN = "dbname=cripta user=cripta host=/var/run/postgresql"


class R06ShadowUnverified(RuntimeError):
    """Publication cannot be accepted for exact P90 geometry parity."""


def verify_current_entry_plan_scope(connection: Any) -> tuple[str, ...]:
    """Only P90 plans with the actually approved five-symbol 5m/15m scope."""
    rows = connection.execute(
        """SELECT a.strategy_id,ep.plan_json
             FROM strategy_entry.strategy_activations a
             JOIN strategy_entry.entry_plans ep
               ON ep.strategy_id=a.strategy_id
              AND ep.strategy_version=a.strategy_version
              AND ep.strategy_config_fingerprint=a.strategy_config_fingerprint
            WHERE a.enabled=true AND a.strategy_id=ANY(%s)
            ORDER BY a.strategy_id""",
        (list(P90_PLAN_IDS),),
    ).fetchall()
    if len(rows) != 2 or {row[0] for row in rows} != P90_PLAN_IDS:
        raise R06ShadowUnverified("R06_P90_ENTRY_PLAN_SET_CHANGED")
    symbols: set[str] = set()
    for _, plan in rows:
        if not isinstance(plan, dict):
            raise R06ShadowUnverified("R06_ENTRY_PLAN_NOT_VERIFIED")
        geometry = plan.get("watch_policy", {}).get("geometry", {})
        if (
            geometry.get("operator") != "P90L90_RAW_H9_UNION_CROSSING"
            or set(geometry.get("timeframes", [])) != {"5", "15"}
            or geometry.get("width_filter", {}).get("source")
            != "PREV7D_COMPLETED_UTC"
        ):
            raise R06ShadowUnverified("R06_ENTRY_GEOMETRY_CONTRACT_DRIFT")
        selected = plan.get("symbols")
        if not isinstance(selected, list):
            raise R06ShadowUnverified("R06_ENTRY_SYMBOLS_INVALID")
        symbols.update(selected)
    if symbols != set(FIVE):
        raise R06ShadowUnverified("R06_ENTRY_UNIVERSE_DRIFT")
    return FIVE


def verify_shadow_read(connection: Any, *, observed_at: datetime,
                       source_checkout: Path, loaded_commit: str) -> dict[str, object]:
    """One repeatable read-only snapshot: publisher, original and full seed.

    Caller already executed SET TRANSACTION before any SELECT. This is a
    diagnostic proof, not permission to submit a signal or acknowledge events.
    """
    symbols = verify_current_entry_plan_scope(connection)
    sessions = connection.execute(
        """SELECT producer_release_commit
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY subscribed_at DESC LIMIT 2""",
        ("ms-bybit-mainnet-public-v1",),
    ).fetchall()
    if len(sessions) != 1:
        raise R06ShadowUnverified("R06_SOURCE_ACTIVE_UNIQUE_NOT_PROVEN")
    producer_commit = sessions[0][0]
    try:
        accepted = verify_native_producer_code_compatibility(
            source_checkout=source_checkout,
            claimed_producer_commit=producer_commit,
            loaded_consumer_commit=loaded_commit,
        )
        pin = read_entry_publication_pin(
            connection,
            observed_at=observed_at,
            accepted_producer_commits=accepted,
            max_publication_age=timedelta(seconds=5),
            symbols=symbols,
            intervals=("5", "15"),
        )
        lengths = {}
        for symbol in symbols:
            history = read_entry_publication_seed(
                connection, pin=pin, symbol=symbol,
                required_counts=dict(EXISTING_P90_COLD_COUNTS),
            )
            lengths[symbol] = {tf: len(history[tf]) for tf in ("5", "15")}
    except (NativeSourceProofUnavailable, EntryPublicationUnavailable) as exc:
        raise R06ShadowUnverified("R06_PINNED_SOURCE_OR_SEED_UNVERIFIED") from exc
    return {
        "result": "R06_SHADOW_SOURCE_AND_COLD_SEED_PASS",
        "publication_id": pin.publication_id,
        "source_session_id": pin.source_session_id,
        "producer_release_commit": pin.producer_release_commit,
        "as_of_second": pin.as_of_second.isoformat(),
        "history_counts": lengths,
        "strategy_signals_compared": False,
        "entry_dispatch_switched": False,
        "trading_effect": "NONE",
    }


def run() -> int:
    """Authorized operator invocation only. Read-only DB, no background loop."""
    import psycopg

    try:
        release = INSTALLED_COMMIT.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{40}", release) is None:
            raise R06ShadowUnverified("R06_INSTALLED_IDENTITY_INVALID")
        with psycopg.connect(DB_DSN, autocommit=True, connect_timeout=5) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                conn.execute("SET LOCAL statement_timeout=15000")
                evidence = verify_shadow_read(
                    conn, observed_at=datetime.now(UTC),
                    source_checkout=SOURCE_CHECKOUT,
                    loaded_commit=release,
                )
        print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
        return 0
    except (R06ShadowUnverified, OSError, psycopg.Error) as exc:
        print(json.dumps({
            "result": "R06_SHADOW_BLOCKED",
            "reason": str(exc)[:240],
            "trading_effect": "NONE",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
