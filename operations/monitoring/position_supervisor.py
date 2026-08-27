from __future__ import annotations

import json
import signal
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg

from bybit_workbench.position_supervisor import (
    ExchangePosition,
    FeatureEvidence,
    PositionEvent,
    Quality,
    SupervisorRegistry,
    SupervisorState,
)

ENTRY_STATUS = Path("/var/lib/cripta/entry_shadow/status.json")
running = True


def stop(*_: object) -> None:
    global running
    running = False


def initialize(connection: psycopg.Connection) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS supervisor")
    connection.execute("""CREATE TABLE IF NOT EXISTS supervisor.snapshots(
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        observed_at_epoch_ms BIGINT NOT NULL, position_id TEXT NOT NULL,
        symbol TEXT NOT NULL, state TEXT NOT NULL, shadow_action TEXT NOT NULL,
        snapshot_json JSONB NOT NULL)""")
    connection.execute("""CREATE INDEX IF NOT EXISTS supervisor_snapshots_position_time
        ON supervisor.snapshots(position_id, observed_at_epoch_ms DESC)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS supervisor.transitions(
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        observed_at_epoch_ms BIGINT NOT NULL, position_id TEXT NOT NULL,
        symbol TEXT NOT NULL, old_state TEXT, new_state TEXT NOT NULL,
        reason TEXT NOT NULL, shadow_action TEXT NOT NULL,
        snapshot_json JSONB NOT NULL)""")
    connection.commit()


def exchange_positions(connection: psycopg.Connection) -> list[tuple[ExchangePosition, Decimal]]:
    rows = connection.execute("""SELECT symbol,position_idx,side,size,entry_price,
        leverage,payload_json FROM runtime.hot_positions ORDER BY symbol""").fetchall()
    result: list[tuple[ExchangePosition, Decimal]] = []
    for row in rows:
        raw = json.loads(row[6])
        open_ms = int(raw.get("openTime") or raw.get("createdTime") or 0)
        identity = ExchangePosition(
            position_id=f"{row[0]}:{row[1]}:{open_ms}:{row[2]}",
            symbol=str(row[0]),
            side=str(row[2]),
            actual_avg_fill=Decimal(str(row[4])),
            qty=Decimal(str(row[3])),
            fill_time=datetime.fromtimestamp(open_ms / 1000, UTC),
            leverage=Decimal(str(row[5])),
            break_even_price=(
                Decimal(str(raw["breakEvenPrice"])) if raw.get("breakEvenPrice") else None
            ),
        )
        result.append((identity, Decimal(str(raw.get("markPrice") or row[4]))))
    return result


def entry_context() -> dict[str, dict[str, object]]:
    try:
        payload = json.loads(ENTRY_STATUS.read_text(encoding="utf-8"))
        return {str(item["symbol"]): item for item in payload.get("assets", [])}
    except (OSError, ValueError, KeyError):
        return {}


def features(
    symbol: str, now: datetime, context: dict[str, dict[str, object]]
) -> dict[str, FeatureEvidence]:
    item = context.get(symbol)
    result: dict[str, FeatureEvidence] = {}
    if not item:
        return result
    try:
        observed = datetime.fromisoformat(str(item["updated_at"]))
        quality = Quality.PARTIAL if (now - observed).total_seconds() <= 15 else Quality.STALE
        result["flow"] = FeatureEvidence(
            str(item.get("flow_state") or "unknown"), observed, quality
        )
        result["oi_price"] = FeatureEvidence(
            str(item.get("oi_state") or "unknown"), observed, quality
        )
    except (ValueError, TypeError, KeyError):
        pass
    return result


def restore_created(
    connection: psycopg.Connection, registry: SupervisorRegistry, created: set[str]
) -> None:
    for position_id in created:
        row = connection.execute(
            """SELECT snapshot_json FROM supervisor.snapshots
               WHERE position_id=%s ORDER BY observed_at_epoch_ms DESC LIMIT 1""",
            (position_id,),
        ).fetchone()
        if not row:
            continue
        raw = row[0]
        registry.get(position_id).restore_path(
            mfe_pct=Decimal(str(raw["mfe_pct"])),
            mae_pct=Decimal(str(raw["mae_pct"])),
            state=SupervisorState(str(raw["new_state"])),
            state_since=datetime.fromisoformat(str(raw.get("state_since") or raw["timestamp"])),
            last_at=datetime.fromisoformat(str(raw["timestamp"])),
        )


def main() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    registry = SupervisorRegistry()
    with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
        initialize(connection)
        while running:
            now = datetime.now(UTC)
            rows = exchange_positions(connection)
            created, _ = registry.reconcile(position for position, _ in rows)
            restore_created(connection, registry, created)
            context = entry_context()
            for position, mark in rows:
                snapshot = registry.get(position.position_id).update(
                    PositionEvent(now, mark, features(position.symbol, now, context))
                )
                document = snapshot.audit_dict()
                at_ms = int(now.timestamp() * 1000)
                connection.execute(
                    """INSERT INTO supervisor.snapshots(
                    observed_at_epoch_ms,position_id,symbol,state,shadow_action,snapshot_json)
                    VALUES(%s,%s,%s,%s,%s,%s)""",
                    (
                        at_ms,
                        position.position_id,
                        position.symbol,
                        snapshot.state.value,
                        snapshot.shadow_action,
                        json.dumps(document, ensure_ascii=False),
                    ),
                )
                if snapshot.previous_state != snapshot.state:
                    connection.execute(
                        """INSERT INTO supervisor.transitions(
                        observed_at_epoch_ms,position_id,symbol,old_state,new_state,reason,
                        shadow_action,snapshot_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            at_ms,
                            position.position_id,
                            position.symbol,
                            None
                            if snapshot.previous_state is None
                            else snapshot.previous_state.value,
                            snapshot.state.value,
                            snapshot.reason,
                            snapshot.shadow_action,
                            json.dumps(document, ensure_ascii=False),
                        ),
                    )
            connection.commit()
            time.sleep(2)


if __name__ == "__main__":
    main()
