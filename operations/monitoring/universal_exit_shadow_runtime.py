from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.market_data.exit_publication_reader import (
    read_exit_l53_candles,
    read_exit_ticker_last_price,
)
from bybit_workbench.lifecycle_ack import (
    claim_exit_position,
    mark_exit_claims_stale,
)
from bybit_workbench.universal_entry.contracts import FrozenPolicy, TradeDirection
from bybit_workbench.universal_entry.fingerprint import fingerprint
from bybit_workbench.universal_entry.market_watch import compute_l53_zone
from bybit_workbench.universal_entry.owner_full_contour_diagnostic import (
    is_diagnostic_strategy,
)
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
STATUS_PATH = Path(
    os.environ.get(
        "CRIPTA_UNIVERSAL_EXIT_SHADOW_STATUS_PATH",
        "/var/lib/cripta/universal_exit_shadow/status.json",
    )
)
running = True


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
              AND identity_status='PROVEN'
              AND trading_account_id IS NOT NULL
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
        # Each exact claim owns its own short transaction in claim_exit_position.
        # Never retain FOR UPDATE locks over the entire set of open positions:
        # entry-fill materialization concurrently upserts position_ownership.
        for attempt in range(3):
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
            except psycopg.errors.DeadlockDetected as exc:
                # PostgreSQL rolled back the exact claim transaction. Retry
                # boundedly; exhaustion remains a blocked, not a healthy claim.
                if attempt == 2:
                    blocked.append((position_id, f"{type(exc).__name__}: {exc}"))
                else:
                    time.sleep(0.05 * (attempt + 1))
                continue
            except (KeyError, RuntimeError, ValueError) as exc:
                blocked.append((position_id, f"{type(exc).__name__}: {exc}"))
                break
            claimed.append(position_id)
            break
    return ClaimCycleResult(tuple(claimed), tuple(blocked))


def _fetch_l53_observation(
    source_connection: psycopg.Connection[Any],
    position: object,
    plan: object,
    *,
    now: datetime,
) -> ExitObservation:
    """Only one source-pinned 240-bar PostgreSQL L5-3 geometry; no REST."""
    symbol = str(position.symbol)
    candles, pin = read_exit_l53_candles(
        source_connection, symbol=symbol, observed_at=now,
    )
    zone = compute_l53_zone(candles)
    if zone is None:
        raise RuntimeError("R08_L53_240_BAR_GEOMETRY_NOT_MATURE")
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
            "observed_at": pin.as_of_second,
            "source_ref": pin.evidence_ref("CLOSED_KLINE:5"),
        }
    )[:32]
    return ExitObservation(
        observation_id=observation_id,
        strategy_position_id=position.strategy_position_id,
        symbol=symbol,
        event_at=zone.observed_at,
        observed_at=pin.as_of_second,
        received_at=now,
        event_kind="GEOMETRY_L5_3",
        attributes=FrozenPolicy.from_mapping({"geometry": {"l5_3": geometry}}),
        source_refs=(pin.evidence_ref("CLOSED_KLINE:5"),),
    )


def _fetch_owner_diagnostic_observation(
    source_connection: psycopg.Connection[Any],
    position: object,
    *,
    now: datetime,
) -> ExitObservation:
    """Owner diagnostic remains decision-only; requires original PG ticker."""
    symbol = str(position.symbol)
    entry = Decimal(str(position.actual_avg_fill))
    if not entry.is_finite() or entry <= 0:
        raise RuntimeError("owner diagnostic actual fill is invalid")
    mark, pin = read_exit_ticker_last_price(
        source_connection, symbol=symbol, observed_at=now,
    )
    directional = (
        ((mark - entry) / entry) * Decimal("100")
        if position.direction is TradeDirection.LONG
        else ((entry - mark) / entry) * Decimal("100")
    )
    observation_id = "exit-observation-" + fingerprint(
        {
            "strategy_position_id": position.strategy_position_id,
            "event_kind": "OWNER_FULL_CONTOUR_MARK",
            "last_price": str(mark),
            "observed_at": pin.as_of_second,
            "source_ref": pin.evidence_ref("TICKER:lastPrice"),
        }
    )[:32]
    return ExitObservation(
        observation_id=observation_id,
        strategy_position_id=position.strategy_position_id,
        symbol=symbol,
        event_at=pin.as_of_second,
        observed_at=pin.as_of_second,
        received_at=now,
        event_kind="OWNER_FULL_CONTOUR_MARK",
        attributes=FrozenPolicy.from_mapping(
            {
                "directional_pnl_pct": str(directional),
                "last_price": str(mark),
                "actual_avg_fill": str(entry),
            }
        ),
        source_refs=(pin.evidence_ref("TICKER:lastPrice"),),
    )


def _evaluate_claimed_positions(
    connection: psycopg.Connection[Any],
    source_connection: psycopg.Connection[Any],
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
            diagnostic = is_diagnostic_strategy(
                position.strategy_id, position.strategy_version
            )
            # Source evidence is read in ONE immutable, read-only PG view.
            # It cannot share the claim/Exit-decision write transaction.
            with source_connection.transaction():
                source_connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                observation = (
                    _fetch_owner_diagnostic_observation(
                        source_connection, position, now=now,
                    )
                    if diagnostic
                    else _fetch_l53_observation(
                        source_connection, position, plan, now=now,
                    )
                )
            if not diagnostic:
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
                    if Decimal(str(previous["boundary"])) == Decimal(str(geometry[structural_field])):  # noqa: E501
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
    source_connection: psycopg.Connection[Any] | None = None
    try:
        source_connection = psycopg.connect(
            DB_DSN,
            autocommit=True,
            options="-c idle_in_transaction_session_timeout=120000",
        )
        while running:
            now = datetime.now(UTC)
            # claim_cycle commits each exact position independently, avoiding
            # cross-position lock retention against Entry fill materialization.
            cycle = claim_cycle(
                connection,
                now=now,
                consumer_instance_id=CONSUMER_ID,
            )
            with connection.transaction():
                recorded_count, evaluation_blocks = _evaluate_claimed_positions(
                    connection,
                    source_connection,
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
            if source_connection is not None:
                source_connection.close()
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
