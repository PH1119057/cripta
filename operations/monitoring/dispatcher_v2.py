#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg
from bybit_workbench.dispatcher_v2 import (
    DISPATCHER_VERSION,
    TradingAccountState,
    build_coin_market_context,
    build_global_market_context,
    build_trading_capacity_snapshot,
)
from bybit_workbench.dispatcher_v2.serialization import (
    capacity_record,
    coin_context_record,
    global_context_record,
)
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DSN = os.environ.get("CRIPTA_DSN", "dbname=cripta user=cripta host=/var/run/postgresql")
STATUS_PATH = Path(
    os.environ.get(
        "CRIPTA_DISPATCHER_V2_STATUS", "/var/lib/cripta/dispatcher_v2/status.json"
    )
)
SOURCE_COMMIT = os.environ.get("DISPATCHER_V2_SOURCE_COMMIT", "UNSPECIFIED")
SOURCE_ADAPTER = os.environ.get("CRIPTA_ACCOUNT_ADAPTER", "current_exchange_adapter")
SOURCE_ACCOUNT_REF = os.environ.get("CRIPTA_ACCOUNT_REF", "runtime.wallet_latest:1")


def _source_global_rows(
    connection: psycopg.Connection[Any], limit: int = 8
) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        return list(
            cursor.execute(
                """SELECT market_context_id,mayak_snapshot_id,observed_at,mayak_version,
                          schema_version,config_fingerprint,data_quality,payload,
                          provenance,content_hash
                   FROM mayak_v2.shared_market_contexts source
                   WHERE NOT EXISTS (
                     SELECT 1 FROM dispatcher_v2.global_market_contexts target
                     WHERE target.source_market_context_id=source.market_context_id)
                   ORDER BY observed_at ASC LIMIT %s""",
                (limit,),
            ).fetchall()
        )


def _source_coin_rows(
    connection: psycopg.Connection[Any], mayak_snapshot_id: int
) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        return list(
            cursor.execute(
                """SELECT coin_context_id,mayak_snapshot_id,observed_at,symbol,
                          schema_version,engine_version,feature_version,
                          config_fingerprint,data_quality,payload,provenance,content_hash
                   FROM mayak_v2.coin_market_contexts
                   WHERE mayak_snapshot_id=%s ORDER BY symbol""",
                (mayak_snapshot_id,),
            ).fetchall()
        )


def _insert_global(connection: psycopg.Connection[Any], record: Mapping[str, Any]) -> None:
    connection.execute(
        """INSERT INTO dispatcher_v2.global_market_contexts(
            global_context_id,source_market_context_id,source_mayak_snapshot_id,
            observed_at,created_at,dispatcher_version,schema_version,config_fingerprint,
            data_quality,freshness_status,freshness_age_seconds,coverage,payload,
            provenance,content_hash,trading_effect)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(global_context_id) DO NOTHING""",
        (
            record["global_context_id"],
            record["source_market_context_id"],
            record["source_mayak_snapshot_id"],
            record["observed_at"],
            record["created_at"],
            record["dispatcher_version"],
            record["schema_version"],
            record["config_fingerprint"],
            record["data_quality"],
            record["freshness_status"],
            record["freshness_age_seconds"],
            Jsonb(record["coverage"]),
            Jsonb(record["payload"]),
            Jsonb(record["provenance"]),
            record["content_hash"],
            record["trading_effect"],
        ),
    )


def _insert_coin(connection: psycopg.Connection[Any], record: Mapping[str, Any]) -> None:
    connection.execute(
        """INSERT INTO dispatcher_v2.coin_market_contexts(
            coin_context_id,global_context_id,source_coin_context_id,
            source_mayak_snapshot_id,symbol,observed_at,created_at,dispatcher_version,
            schema_version,config_fingerprint,data_quality,freshness_status,
            freshness_age_seconds,coverage,payload,provenance,content_hash,trading_effect)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(coin_context_id) DO NOTHING""",
        (
            record["coin_context_id"],
            record["global_context_id"],
            record["source_coin_context_id"],
            record["source_mayak_snapshot_id"],
            record["symbol"],
            record["observed_at"],
            record["created_at"],
            record["dispatcher_version"],
            record["schema_version"],
            record["config_fingerprint"],
            record["data_quality"],
            record["freshness_status"],
            record["freshness_age_seconds"],
            Jsonb(record["coverage"]),
            Jsonb(record["payload"]),
            Jsonb(record["provenance"]),
            record["content_hash"],
            record["trading_effect"],
        ),
    )


def _account_state(connection: psycopg.Connection[Any]) -> TradingAccountState | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        wallet = cursor.execute(
            """SELECT refreshed_at_epoch_ms,total_equity,wallet_balance,
                      available_balance,payload_json
               FROM runtime.wallet_latest WHERE singleton=1"""
        ).fetchone()
        if wallet is None:
            return None
        positions_row = cursor.execute(
            "SELECT count(*) AS n FROM runtime.hot_positions"
        ).fetchone()
        assert positions_row is not None
        positions_count = int(positions_row["n"])
        active_orders_row = cursor.execute(
            """SELECT count(*) AS n FROM runtime.hot_orders
               WHERE order_status IN ('New','PartiallyFilled','Untriggered')
                 AND COALESCE(payload_json::jsonb->>'reduceOnly','false') <> 'true'
                 AND COALESCE(payload_json::jsonb->>'closeOnTrigger','false') <> 'true'"""
        ).fetchone()
        assert active_orders_row is not None
        active_orders_count = int(active_orders_row["n"])
    payload = json.loads(str(wallet["payload_json"]))
    if not isinstance(payload, dict):
        payload = {}
    return TradingAccountState(
        observed_at=datetime.fromtimestamp(int(wallet["refreshed_at_epoch_ms"]) / 1000, UTC),
        source_adapter=SOURCE_ADAPTER,
        source_account_ref=SOURCE_ACCOUNT_REF,
        account_type=_text_or_none(payload.get("accountType")),
        total_equity=_decimal_or_none(wallet["total_equity"]),
        wallet_balance=_decimal_or_none(wallet["wallet_balance"]),
        used_position_margin=_wallet_margin(payload, "totalPositionIM"),
        reserved_order_margin=_wallet_margin(payload, "totalOrderIM"),
        free_balance=_decimal_or_none(wallet["available_balance"]),
        open_positions_count=positions_count,
        active_orders_count=active_orders_count,
        source_payload=payload,
    )


def _insert_capacity(connection: psycopg.Connection[Any], record: Mapping[str, Any]) -> None:
    connection.execute(
        """INSERT INTO dispatcher_v2.trading_capacity_snapshots(
            capacity_snapshot_id,observed_at,created_at,dispatcher_version,schema_version,
            config_fingerprint,source_adapter,source_account_ref,account_type,data_quality,
            freshness_status,freshness_age_seconds,total_equity,wallet_balance,
            used_position_margin,reserved_order_margin,free_balance,available_for_new_trading,
            open_positions_count,active_orders_count,coverage,payload,provenance,
            content_hash,trading_effect)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                   %s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(capacity_snapshot_id) DO NOTHING""",
        (
            record["capacity_snapshot_id"],
            record["observed_at"],
            record["created_at"],
            record["dispatcher_version"],
            record["schema_version"],
            record["config_fingerprint"],
            record["source_adapter"],
            record["source_account_ref"],
            record["account_type"],
            record["data_quality"],
            record["freshness_status"],
            record["freshness_age_seconds"],
            record["total_equity"],
            record["wallet_balance"],
            record["used_position_margin"],
            record["reserved_order_margin"],
            record["free_balance"],
            record["available_for_new_trading"],
            record["open_positions_count"],
            record["active_orders_count"],
            Jsonb(record["coverage"]),
            Jsonb(record["payload"]),
            Jsonb(record["provenance"]),
            record["content_hash"],
            record["trading_effect"],
        ),
    )


def run_once() -> dict[str, Any]:
    now = datetime.now(UTC)
    globals_published = 0
    coins_published = 0
    capacity_published = 0
    latest_global_id: str | None = None
    latest_capacity_id: str | None = None
    with psycopg.connect(DSN) as connection:
        for source in _source_global_rows(connection):
            global_context = build_global_market_context(source, now=now)
            global_record = global_context_record(global_context)
            _insert_global(connection, global_record)
            latest_global_id = global_context.global_context_id
            globals_published += 1
            for coin_source in _source_coin_rows(
                connection, global_context.source_mayak_snapshot_id
            ):
                coin_context = build_coin_market_context(
                    coin_source,
                    global_context_id=global_context.global_context_id,
                    now=now,
                )
                _insert_coin(connection, coin_context_record(coin_context))
                coins_published += 1
        account = _account_state(connection)
        if account is not None:
            capacity = build_trading_capacity_snapshot(account, now=now)
            _insert_capacity(connection, capacity_record(capacity))
            latest_capacity_id = capacity.capacity_snapshot_id
            capacity_published = 1
        connection.commit()
        if latest_global_id is None:
            row = connection.execute(
                """SELECT global_context_id FROM dispatcher_v2.global_market_contexts
                   ORDER BY observed_at DESC LIMIT 1"""
            ).fetchone()
            latest_global_id = None if row is None else str(row[0])
        if latest_capacity_id is None:
            row = connection.execute(
                """SELECT capacity_snapshot_id FROM dispatcher_v2.trading_capacity_snapshots
                   ORDER BY observed_at DESC LIMIT 1"""
            ).fetchone()
            latest_capacity_id = None if row is None else str(row[0])
        coin_count_row = connection.execute(
            "SELECT count(*) FROM dispatcher_v2.coin_market_contexts"
        ).fetchone()
        assert coin_count_row is not None
        coin_total = int(coin_count_row[0])
    return {
        "status": "RUNNING",
        "dispatcher_version": DISPATCHER_VERSION,
        "source_commit": SOURCE_COMMIT,
        "trading_effect": "NONE",
        "coin_market_rating": "NOT_IMPLEMENTED",
        "legacy_profile_runtime": "DISABLED",
        "updated_at": now.isoformat(),
        "cycle": {
            "global_contexts_published": globals_published,
            "coin_contexts_published": coins_published,
            "capacity_snapshots_published": capacity_published,
        },
        "latest_global_context_id": latest_global_id,
        "latest_capacity_snapshot_id": latest_capacity_id,
        "stored_coin_contexts": coin_total,
    }


def publish_status(value: Mapping[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(STATUS_PATH)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _wallet_margin(payload: Mapping[str, Any], key: str) -> Decimal | None:
    direct = _decimal_or_none(payload.get(key))
    if direct is not None:
        return direct
    coins = payload.get("coin")
    if isinstance(coins, list):
        for coin in coins:
            if isinstance(coin, Mapping) and str(coin.get("coin")) == "USDT":
                value = _decimal_or_none(coin.get(key))
                if value is not None:
                    return value
    return None


def _text_or_none(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cripta Dispatcher V2 passive runtime")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        raise ValueError("poll-seconds must be positive")
    stopped = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        started = time.monotonic()
        try:
            publish_status(run_once())
        except (OSError, ValueError, TypeError, psycopg.Error) as exc:
            publish_status(
                {
                    "status": "WAITING",
                    "dispatcher_version": DISPATCHER_VERSION,
                    "source_commit": SOURCE_COMMIT,
                    "trading_effect": "NONE",
                    "coin_market_rating": "NOT_IMPLEMENTED",
                    "legacy_profile_runtime": "DISABLED",
                    "updated_at": datetime.now(UTC).isoformat(),
                    "error": str(exc),
                }
            )
        remaining = args.poll_seconds - (time.monotonic() - started)
        end = time.monotonic() + max(0.0, remaining)
        while not stopped and time.monotonic() < end:
            time.sleep(min(0.25, end - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
