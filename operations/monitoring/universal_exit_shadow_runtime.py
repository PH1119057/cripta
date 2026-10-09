from __future__ import annotations

import json
import os
import signal
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.exchange.bybit.mappers import map_rest_klines
from bybit_workbench.lifecycle_ack import (
    claim_exit_position,
    mark_exit_claims_stale,
)
from bybit_workbench.universal_entry.contracts import FrozenPolicy, TradeDirection
from bybit_workbench.universal_entry.fingerprint import fingerprint
from bybit_workbench.universal_entry.market_watch import compute_l53_zone
from bybit_workbench.universal_exit.contracts import ExitObservation
from bybit_workbench.universal_exit.engine import UniversalExitEngine
from bybit_workbench.universal_exit.loader import load_exit_binding
from bybit_workbench.universal_exit.storage import PostgresExitShadowStore

DB_DSN = os.environ.get(
    "CRIPTA_DATABASE_DSN",
    "dbname=cripta user=cripta host=/var/run/postgresql application_name=universal-exit-shadow",
)
MODE = os.environ.get("CRIPTA_UNIVERSAL_EXIT_SHADOW", "DISABLED").strip().upper()
CONSUMER_ID = os.environ.get(
    "CRIPTA_UNIVERSAL_EXIT_SHADOW_CONSUMER_ID",
    "universal-exit-shadow-v1",
).strip()
POLL_SECONDS = float(os.environ.get("CRIPTA_UNIVERSAL_EXIT_SHADOW_POLL_SECONDS", "1.0"))
PUBLIC_REST = os.environ.get("CRIPTA_U5_PUBLIC_REST", "").rstrip("/")
STATUS_PATH = Path(
    os.environ.get(
        "CRIPTA_UNIVERSAL_EXIT_SHADOW_STATUS_PATH",
        "/var/lib/cripta/universal_exit_shadow/status.json",
    )
)
running = True
_l53_cache: dict[str, tuple[datetime, Any]] = {}


@dataclass(frozen=True, slots=True)
class ClaimCycleResult:
    claimed_positions: tuple[str, ...]
    blocked_positions: tuple[tuple[str, str], ...]


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def _status(payload: dict[str, object]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "service": "universal_exit_shadow",
        "consumer_instance_id": CONSUMER_ID,
        **payload,
    }
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str),
        encoding="utf-8",
    )
    temporary.replace(STATUS_PATH)


def _position_ids(connection: psycopg.Connection[Any]) -> tuple[str, ...]:
    rows = connection.execute(
        """SELECT position_id
             FROM runtime.position_ownership
            WHERE bot_instance_id='universal-entry'
              AND state IN ('OPEN','RECONCILIATION_REQUIRED')
            ORDER BY fill_at,position_id"""
    ).fetchall()
    return tuple(str(row["position_id"]) for row in rows)


def claim_cycle(
    connection: psycopg.Connection[Any],
    *,
    now: datetime,
    consumer_instance_id: str,
) -> ClaimCycleResult:
    if not consumer_instance_id:
        raise ValueError("consumer_instance_id is required")
    current = now.astimezone(UTC)
    claimed: list[str] = []
    blocked: list[tuple[str, str]] = []
    for position_id in _position_ids(connection):
        try:
            position, plan = load_exit_binding(
                connection,
                strategy_position_id=position_id,
            )
            if position.exit_plan_fingerprint != plan.exit_plan_fingerprint:
                raise RuntimeError("Universal Exit exact binding mismatch")
            claim_exit_position(
                connection,
                strategy_position_id=position_id,
                consumer_instance_id=consumer_instance_id,
                claimed_at=current,
                payload={
                    "runtime": "universal_exit_shadow",
                    "mode": "DECISION_ONLY_NO_EXECUTION",
                },
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            blocked.append((position_id, f"{type(exc).__name__}: {exc}"))
            continue
        claimed.append(position_id)
    return ClaimCycleResult(tuple(claimed), tuple(blocked))


def _fetch_l53_observation(position: object, plan: object, *, now: datetime) -> ExitObservation:
    if not PUBLIC_REST:
        raise RuntimeError("CRIPTA_U5_PUBLIC_REST is required for L5-3 Exit observation")
    symbol = str(position.symbol)
    cached = _l53_cache.get(symbol)
    if cached is not None and now < cached[1].observed_at + timedelta(minutes=5, seconds=2):
        fact_observed_at, zone = cached
    else:
        query = urllib.parse.urlencode(
            {"category": "linear", "symbol": symbol, "interval": "5", "limit": "240"}
        )
        with urllib.request.urlopen(
            f"{PUBLIC_REST}/v5/market/kline?{query}", timeout=10
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if str(payload.get("retCode")) != "0":
            raise RuntimeError(f"Bybit kline failed: {payload.get('retMsg')}")
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("list"), list):
            raise RuntimeError("Bybit kline result.list missing")
        candles = map_rest_klines(
            result["list"], symbol=symbol, interval="5", observed_at=now
        )
        zone = compute_l53_zone(candles)
        if zone is None:
            raise RuntimeError("L5-3 geometry is not mature")
        if cached is not None and cached[1].observed_at == zone.observed_at:
            fact_observed_at = cached[0]
        else:
            fact_observed_at = now
        _l53_cache[symbol] = (fact_observed_at, zone)
    target_inner = (
        zone.resistance_bottom
        if position.direction is TradeDirection.LONG
        else zone.support_top
    )
    geometry = {
        "spec": "L5-3",
        "observed_at": zone.observed_at,
        "range_high": str(zone.range_high),
        "range_low": str(zone.range_low),
        "atr": str(zone.atr),
        "resistance_top": str(zone.resistance_top),
        "resistance_bottom": str(zone.resistance_bottom),
        "support_top": str(zone.support_top),
        "support_bottom": str(zone.support_bottom),
        "target_inner": str(target_inner),
        "effective_lookback": zone.effective_lookback,
    }
    observation_id = "exit-observation-" + fingerprint(
        {
            "strategy_position_id": position.strategy_position_id,
            "exit_plan_fingerprint": plan.exit_plan_fingerprint,
            "geometry": geometry,
            "observed_at": fact_observed_at,
        }
    )[:32]
    return ExitObservation(
        observation_id=observation_id,
        strategy_position_id=position.strategy_position_id,
        symbol=symbol,
        event_at=zone.observed_at,
        observed_at=fact_observed_at,
        received_at=fact_observed_at,
        event_kind="GEOMETRY_L5_3",
        attributes=FrozenPolicy.from_mapping({"geometry": {"l5_3": geometry}}),
        source_refs=(f"bybit:kline:5:{symbol}:{zone.observed_at.isoformat()}",),
    )


def _evaluate_claimed_positions(
    connection: psycopg.Connection[Any],
    engine: UniversalExitEngine,
    position_ids: tuple[str, ...],
    *,
    now: datetime,
) -> tuple[int, tuple[tuple[str, str], ...]]:
    store = PostgresExitShadowStore(connection)
    recorded = 0
    blocked: list[tuple[str, str]] = []
    for position_id in position_ids:
        try:
            position, plan = load_exit_binding(connection, strategy_position_id=position_id)
            observation = _fetch_l53_observation(position, plan, now=now)
            # Existing ATR200 still determines the INNER target at a structural
            # change, but its movement alone cannot emit another SET_TP.
            # Compare only the relevant opposite working-range extremum.
            geometry = observation.attributes.to_dict()["geometry"]["l5_3"]
            structural_field = (
                "range_high" if position.direction is TradeDirection.LONG
                else "range_low"
            )
            previous = connection.execute(
                """SELECT attributes->'geometry'->'l5_3'->>%s AS boundary
                     FROM strategy_exit.exit_observations
                    WHERE strategy_position_id=%s
                      AND event_kind='GEOMETRY_L5_3'
                    ORDER BY observed_at DESC, observation_id DESC LIMIT 1""",
                (structural_field, position_id),
            ).fetchone()
            if previous is not None:
                if previous["boundary"] is None:
                    raise RuntimeError("previous L5-3 structural boundary missing")
                if Decimal(str(previous["boundary"])) == Decimal(str(geometry[structural_field])):
                    # No new geometry event needed: current range is unchanged.
                    continue
            prior_rows = connection.execute(
                """SELECT rule_id FROM strategy_exit.exit_decisions
                     WHERE strategy_position_id=%s AND repeat_policy='ONCE_PER_POSITION'""",
                (position_id,),
            ).fetchall()
            prior_once = frozenset(str(row["rule_id"]) for row in prior_rows)
            evaluation = engine.evaluate(
                position,
                plan,
                observation,
                prior_once_rule_ids=prior_once,
            )
            store.record(evaluation, position=position, plan=plan)
            recorded += 1
        except (KeyError, RuntimeError, ValueError, OSError) as exc:
            blocked.append((position_id, f"{type(exc).__name__}: {exc}"))
    return recorded, tuple(blocked)


def main() -> int:
    global running
    if MODE != "ENABLED":
        raise SystemExit("Universal Exit shadow runtime is explicitly disabled")
    if not CONSUMER_ID:
        raise SystemExit("Universal Exit shadow consumer id is empty")
    if POLL_SECONDS <= 0:
        raise SystemExit("Universal Exit shadow poll seconds must be positive")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    engine = UniversalExitEngine()
    connection = psycopg.connect(
        DB_DSN,
        autocommit=True,
        row_factory=dict_row,
        options="-c idle_in_transaction_session_timeout=120000",
    )
    try:
        while running:
            now = datetime.now(UTC)
            with connection.transaction():
                cycle = claim_cycle(
                    connection,
                    now=now,
                    consumer_instance_id=CONSUMER_ID,
                )
            with connection.transaction():
                recorded_count, evaluation_blocks = _evaluate_claimed_positions(
                    connection,
                    engine,
                    cycle.claimed_positions,
                    now=now,
                )
            all_blocks = cycle.blocked_positions + evaluation_blocks
            _status(
                {
                    "state": "RUNNING",
                    "observed_at": now.isoformat(),
                    "claimed_positions": list(cycle.claimed_positions),
                    "claimed_count": len(cycle.claimed_positions),
                    "evaluations_recorded": recorded_count,
                    "blocked_positions": [
                        {"strategy_position_id": position_id, "reason": reason}
                        for position_id, reason in all_blocks
                    ],
                    "blocked_count": len(all_blocks),
                    "engine": type(engine).__name__,
                    "execution_rights": "NONE",
                }
            )
            time.sleep(POLL_SECONDS)
    except Exception as exc:
        _status(
            {
                "state": "ERROR",
                "observed_at": datetime.now(UTC).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
                "execution_rights": "NONE",
            }
        )
        raise
    finally:
        try:
            with connection.transaction():
                mark_exit_claims_stale(
                    connection,
                    consumer_instance_id=CONSUMER_ID,
                    seen_at=datetime.now(UTC),
                    reason="Universal Exit shadow runtime stopped",
                )
        finally:
            connection.close()
    _status(
        {
            "state": "STOPPED",
            "observed_at": datetime.now(UTC).isoformat(),
            "execution_rights": "NONE",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
