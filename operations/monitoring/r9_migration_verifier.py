#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    BYBIT_VENUE_ID,
)
from bybit_workbench.mayak.compact_analytics import (
    COMPACT_ANALYTICS_SCHEMA_VERSION,
)
from bybit_workbench.mayak.compact_reader import (
    R8C_REQUIRED_ANALYTICAL_PATHS,
    compare_compact_panel_to_contexts,
)

DSN = os.environ.get("CRIPTA_DSN", "dbname=cripta user=cripta host=/var/run/postgresql")
EXPECTED_RUNTIME_SCHEMA_VERSION = "runtime-schema-2026-10-17-compact-analytics-r8c"
DEFAULT_PARITY_SNAPSHOTS = 3


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class Baseline:
    database_time: str
    mayak_snapshot_id: int | None
    mayak_observed_at: str | None
    account_generation_started_at: str | None
    reconciliation_started_at: str | None


def baseline(connection: psycopg.Connection[Any]) -> Baseline:
    connection.execute("SET TRANSACTION READ ONLY")
    row = connection.execute(
        """SELECT
               clock_timestamp() AS database_time,
               (SELECT max(id) FROM mayak_v2.snapshots) AS mayak_snapshot_id,
               (SELECT max(observed_at) FROM mayak_v2.snapshots) AS mayak_observed_at,
               (SELECT max(started_at) FROM runtime.account_state_generations)
                   AS account_generation_started_at,
               (
                   SELECT to_timestamp(max(started_at_epoch_ms) / 1000.0)
                     FROM runtime.reconciliation_runs
               ) AS reconciliation_started_at"""
    ).fetchone()
    assert row is not None
    return Baseline(
        database_time=_iso(row[0]),
        mayak_snapshot_id=_optional_int(row[1]),
        mayak_observed_at=_optional_iso(row[2]),
        account_generation_started_at=_optional_iso(row[3]),
        reconciliation_started_at=_optional_iso(row[4]),
    )


def verify(
    connection: psycopg.Connection[Any],
    *,
    trading_account_id: str,
    after_time: datetime | None,
    parity_snapshots: int,
) -> list[Gate]:
    connection.execute("SET TRANSACTION READ ONLY")
    gates: list[Gate] = []

    gates.append(_schema_gate(connection))
    if gates[-1].status != "PASS":
        return gates

    connector_id = _active_connector(connection, trading_account_id)
    gates.append(_identity_registry_gate(connection, trading_account_id, connector_id))
    gates.append(_private_current_state_gate(connection, trading_account_id, connector_id))
    gates.append(_market_source_gate(connection))
    gates.append(
        _compact_parity_gate(
            connection,
            after_time=after_time,
            parity_snapshots=parity_snapshots,
        )
    )
    if after_time is not None:
        gates.append(
            _restart_progress_gate(
                connection,
                trading_account_id=trading_account_id,
                connector_id=connector_id,
                after_time=after_time,
            )
        )
    return gates


def _schema_gate(connection: psycopg.Connection[Any]) -> Gate:
    missing: list[str] = []
    for relation in (
        "identity.venues",
        "identity.market_sources",
        "identity.instruments",
        "identity.trading_accounts",
        "identity.account_connectors",
        "strategy_entry.strategy_execution_bindings",
        "analytics.market_minutes",
        "runtime.schema_contract",
    ):
        row = connection.execute("SELECT to_regclass(%s)", (relation,)).fetchone()
        if not row or row[0] is None:
            missing.append(relation)
    if missing:
        return Gate("SCHEMA_CONTRACT", "FAIL", "missing=" + ",".join(missing))

    version = connection.execute(
        """SELECT version
             FROM runtime.schema_contract
            WHERE component='private_runtime'"""
    ).fetchone()
    actual = None if version is None else str(version[0])
    if actual != EXPECTED_RUNTIME_SCHEMA_VERSION:
        return Gate(
            "SCHEMA_CONTRACT",
            "FAIL",
            f"expected={EXPECTED_RUNTIME_SCHEMA_VERSION} actual={actual or 'MISSING'}",
        )

    compact_columns = (
        "flow_60m_acceleration_usd_per_min2",
        "bid_depth_50bps_usd",
        "imbalance_change_15m",
        "open_interest_change_60m_pct",
        "funding_rate_change_from_previous",
    )
    missing_columns = [
        column
        for column in compact_columns
        if connection.execute(
            """SELECT 1
                 FROM information_schema.columns
                WHERE table_schema='analytics'
                  AND table_name='market_minutes'
                  AND column_name=%s""",
            (column,),
        ).fetchone()
        is None
    ]
    if missing_columns:
        return Gate(
            "SCHEMA_CONTRACT",
            "FAIL",
            "missing_compact_columns=" + ",".join(missing_columns),
        )
    return Gate(
        "SCHEMA_CONTRACT",
        "PASS",
        EXPECTED_RUNTIME_SCHEMA_VERSION,
    )


def _active_connector(
    connection: psycopg.Connection[Any],
    trading_account_id: str,
) -> str:
    rows = connection.execute(
        """SELECT account_connector_id
             FROM identity.account_connectors
            WHERE trading_account_id=%s
              AND status='ACTIVE'
            ORDER BY account_connector_id""",
        (trading_account_id,),
    ).fetchall()
    if len(rows) != 1:
        raise RuntimeError(
            "R9_ACTIVE_CONNECTOR_AMBIGUOUS "
            f"trading_account_id={trading_account_id} count={len(rows)}"
        )
    return str(rows[0][0])


def _identity_registry_gate(
    connection: psycopg.Connection[Any],
    trading_account_id: str,
    connector_id: str,
) -> Gate:
    account = connection.execute(
        """SELECT venue_id,environment,status
             FROM identity.trading_accounts
            WHERE trading_account_id=%s""",
        (trading_account_id,),
    ).fetchone()
    connector = connection.execute(
        """SELECT trading_account_id,status
             FROM identity.account_connectors
            WHERE account_connector_id=%s""",
        (connector_id,),
    ).fetchone()
    source = connection.execute(
        """SELECT venue_id,environment,status
             FROM identity.market_sources
            WHERE market_source_id=%s""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchone()
    if account != (BYBIT_VENUE_ID, "MAINNET", "ACTIVE"):
        return Gate("IDENTITY_REGISTRY", "FAIL", f"account={account!r}")
    if connector != (trading_account_id, "ACTIVE"):
        return Gate("IDENTITY_REGISTRY", "FAIL", f"connector={connector!r}")
    if source != (BYBIT_VENUE_ID, "MAINNET", "ACTIVE"):
        return Gate("IDENTITY_REGISTRY", "FAIL", f"market_source={source!r}")
    return Gate(
        "IDENTITY_REGISTRY",
        "PASS",
        f"account={trading_account_id} connector={connector_id}",
    )


def _private_current_state_gate(
    connection: psycopg.Connection[Any],
    trading_account_id: str,
    connector_id: str,
) -> Gate:
    """Verify this one account without treating another account's rows as errors."""
    failures: list[str] = []

    wallet = connection.execute(
        """SELECT count(*),
                  count(*) FILTER (
                      WHERE identity_status='PROVEN'
                        AND account_connector_id=%s
                  )
             FROM runtime.wallet_latest
            WHERE trading_account_id=%s""",
        (connector_id, trading_account_id),
    ).fetchone()
    if wallet is None or tuple(int(v) for v in wallet) != (1, 1):
        failures.append("wallet_latest:EXACT_ACCOUNT_ROW_NOT_UNIQUE_OR_UNPROVEN")

    for table, order_column in (
        ("runtime.account_state_generations", "started_at"),
        ("dispatcher_v2.trading_capacity_snapshots", "observed_at"),
        ("runtime.reconciliation_runs", "started_at_epoch_ms"),
    ):
        row = connection.execute(
            f"""SELECT trading_account_id,account_connector_id,identity_status
                  FROM {table}
                 WHERE trading_account_id=%s
                 ORDER BY {order_column} DESC
                 LIMIT 1""",
            (trading_account_id,),
        ).fetchone()
        if row != (trading_account_id, connector_id, "PROVEN"):
            failures.append(f"{table}:NO_LATEST_EXACT_PROVEN_ROW")

    for table in ("runtime.hot_positions", "runtime.hot_orders"):
        row = connection.execute(
            f"""SELECT count(*),
                       count(*) FILTER (
                           WHERE identity_status='PROVEN'
                             AND account_connector_id=%s
                       )
                  FROM {table}
                 WHERE trading_account_id=%s""",
            (connector_id, trading_account_id),
        ).fetchone()
        if row is None or int(row[0]) != int(row[1]):
            failures.append(f"{table}:ACCOUNT_SCOPE_MISMATCH")

    if failures:
        return Gate("ACCOUNT_SCOPE_PARITY", "FAIL", "; ".join(failures))
    return Gate("ACCOUNT_SCOPE_PARITY", "PASS", "private facts scoped to exact account")


def _market_source_gate(connection: psycopg.Connection[Any]) -> Gate:
    bad = connection.execute(
        """SELECT count(*)
             FROM analytics.market_minutes minute
             LEFT JOIN identity.market_sources source_identity
               ON source_identity.market_source_id=minute.market_source_id
              AND source_identity.venue_id=minute.venue_id
             LEFT JOIN identity.instruments instrument_identity
               ON instrument_identity.instrument_id=minute.instrument_id
              AND instrument_identity.venue_id=minute.venue_id
              AND instrument_identity.product_category=minute.product_category
              AND instrument_identity.external_symbol=minute.external_symbol
            WHERE minute.schema_version=%s
              AND (
                  minute.market_source_id<>%s
                  OR minute.venue_id<>%s
                  OR source_identity.market_source_id IS NULL
                  OR instrument_identity.instrument_id IS NULL
              )""",
        (
            COMPACT_ANALYTICS_SCHEMA_VERSION,
            BYBIT_PUBLIC_MARKET_SOURCE_ID,
            BYBIT_VENUE_ID,
        ),
    ).fetchone()
    count = 0 if bad is None else int(bad[0])
    if count:
        return Gate("MARKET_SOURCE_PARITY", "FAIL", f"bad_rows={count}")
    return Gate("MARKET_SOURCE_PARITY", "PASS", "exact source/instrument lineage")


def _compact_parity_gate(
    connection: psycopg.Connection[Any],
    *,
    after_time: datetime | None,
    parity_snapshots: int,
) -> Gate:
    if parity_snapshots < 1:
        raise ValueError("parity_snapshots must be positive")
    params: list[Any] = [
        COMPACT_ANALYTICS_SCHEMA_VERSION,
        BYBIT_PUBLIC_MARKET_SOURCE_ID,
    ]
    time_filter = ""
    if after_time is not None:
        time_filter = " AND source_observed_at > %s"
        params.append(after_time)
    params.append(parity_snapshots)

    snapshots = connection.execute(
        f"""SELECT source_snapshot_id,max(source_observed_at) AS observed_at
               FROM analytics.market_minutes
              WHERE schema_version=%s
                AND market_source_id=%s
                {time_filter}
              GROUP BY source_snapshot_id
              ORDER BY source_snapshot_id DESC
              LIMIT %s""",
        tuple(params),
    ).fetchall()
    if len(snapshots) < parity_snapshots:
        return Gate(
            "COMPACT_ANALYTICS_PARITY",
            "FAIL",
            f"snapshots={len(snapshots)} required={parity_snapshots}",
        )

    checked_paths = 0
    checked_symbols = 0
    for snapshot_id, _observed_at in snapshots:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """SELECT *
                     FROM analytics.market_minutes
                    WHERE source_snapshot_id=%s
                      AND schema_version=%s
                      AND market_source_id=%s
                    ORDER BY external_symbol,product_category""",
                (
                    snapshot_id,
                    COMPACT_ANALYTICS_SCHEMA_VERSION,
                    BYBIT_PUBLIC_MARKET_SOURCE_ID,
                ),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """SELECT symbol,payload
                     FROM mayak_v2.coin_market_contexts
                    WHERE mayak_snapshot_id=%s
                    ORDER BY symbol""",
                (snapshot_id,),
            )
            contexts = {
                str(row["symbol"]): {"payload": row["payload"]}
                for row in cursor.fetchall()
            }
        report = compare_compact_panel_to_contexts(rows, contexts)
        if report.mismatches or report.uncovered_paths:
            sample = ",".join(report.mismatches[:5])
            return Gate(
                "COMPACT_ANALYTICS_PARITY",
                "FAIL",
                f"snapshot_id={snapshot_id} mismatches={sample or 'UNCOVERED'}",
            )
        checked_paths += report.checked_paths
        checked_symbols += len(report.checked_symbols)

    expected_per_symbol = len(R8C_REQUIRED_ANALYTICAL_PATHS)
    return Gate(
        "COMPACT_ANALYTICS_PARITY",
        "PASS",
        (
            f"snapshots={len(snapshots)} symbols={checked_symbols} "
            f"path_checks={checked_paths} paths_per_symbol={expected_per_symbol}"
        ),
    )


def _restart_progress_gate(
    connection: psycopg.Connection[Any],
    *,
    trading_account_id: str,
    connector_id: str,
    after_time: datetime,
) -> Gate:
    failures: list[str] = []
    snapshot = connection.execute(
        """SELECT max(id),max(observed_at)
             FROM mayak_v2.snapshots
            WHERE observed_at>%s""",
        (after_time,),
    ).fetchone()
    if snapshot is None or snapshot[0] is None:
        failures.append("MAYAK_NO_POST_RESTART_SNAPSHOT")

    compact = connection.execute(
        """SELECT count(DISTINCT source_snapshot_id)
             FROM analytics.market_minutes
            WHERE schema_version=%s
              AND market_source_id=%s
              AND source_observed_at>%s""",
        (
            COMPACT_ANALYTICS_SCHEMA_VERSION,
            BYBIT_PUBLIC_MARKET_SOURCE_ID,
            after_time,
        ),
    ).fetchone()
    if compact is None or int(compact[0]) < 1:
        failures.append("COMPACT_NO_POST_RESTART_SNAPSHOT")

    progress_queries = (
        (
            "runtime.account_state_generations",
            """SELECT count(*)
                 FROM runtime.account_state_generations
                WHERE started_at>%s
                  AND identity_status='PROVEN'
                  AND trading_account_id=%s
                  AND account_connector_id=%s""",
        ),
        (
            "runtime.reconciliation_runs",
            """SELECT count(*)
                 FROM runtime.reconciliation_runs
                WHERE started_at_epoch_ms > extract(epoch FROM %s) * 1000
                  AND identity_status='PROVEN'
                  AND trading_account_id=%s
                  AND account_connector_id=%s""",
        ),
    )
    for table, query in progress_queries:
        row = connection.execute(
            query,
            (after_time, trading_account_id, connector_id),
        ).fetchone()
        if row is None or int(row[0]) < 1:
            failures.append(f"{table}:NO_POST_RESTART_PROVEN_ROW")

    if failures:
        return Gate("RESTART_RECOVERY", "FAIL", ",".join(failures))
    return Gate("RESTART_RECOVERY", "PASS", f"after={after_time.isoformat()}")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--after-time must be timezone-aware")
    return parsed.astimezone(UTC)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


def _optional_iso(value: Any) -> str | None:
    return None if value is None else _iso(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _connect() -> psycopg.Connection[Any]:
    return psycopg.connect(DSN)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("baseline")

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument(
        "--trading-account-id",
        default=os.environ.get("CRIPTA_TRADING_ACCOUNT_ID", "").strip(),
    )
    verify_parser.add_argument("--after-time")
    verify_parser.add_argument(
        "--parity-snapshots",
        type=int,
        default=DEFAULT_PARITY_SNAPSHOTS,
    )

    args = parser.parse_args(argv)
    with _connect() as connection:
        if args.command == "baseline":
            print(json.dumps(asdict(baseline(connection)), sort_keys=True))
            connection.rollback()
            return 0

        if not args.trading_account_id:
            raise SystemExit("CRIPTA_TRADING_ACCOUNT_ID_REQUIRED")
        gates = verify(
            connection,
            trading_account_id=args.trading_account_id,
            after_time=_parse_time(args.after_time),
            parity_snapshots=args.parity_snapshots,
        )
        connection.rollback()

    payload = {
        "gates": [asdict(item) for item in gates],
        "overall": "PASS" if all(item.status == "PASS" for item in gates) else "FAIL",
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["overall"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
