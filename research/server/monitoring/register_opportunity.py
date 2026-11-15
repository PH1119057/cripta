from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import UTC, datetime

import psycopg

from bybit_workbench.market_data.r13_analytics_source import (
    R13AnalyticsSourceUnavailable,
    read_publication_tickers,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register every strategy signal, including skipped ones"
    )
    parser.add_argument("--signal-id", default="")
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--strategy-version", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument(
        "--direction", choices=("long", "short"), required=True,
    )
    parser.add_argument("--signal-price", type=float, required=True)
    parser.add_argument(
        "--decision",
        choices=("entered", "skipped", "blocked", "shadow"),
        required=True,
    )
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--traffic-light",
        choices=("green", "yellow", "red", "unknown"),
        default="unknown",
    )
    parser.add_argument("--horizon-seconds", type=int, default=21600)
    parser.add_argument("--signal-at-epoch-ms", type=int, default=0)
    args = parser.parse_args()
    if (
        args.signal_price <= 0
        or not 60 <= args.horizon_seconds <= 7 * 86400
    ):
        parser.error("invalid price or horizon")

    symbol = args.symbol.upper()
    observed_at = datetime.now(UTC)
    try:
        with psycopg.connect(
            "dbname=cripta user=cripta host=/var/run/postgresql "
            "options=-cdefault_transaction_read_only=on "
            "application_name=opportunity-register-market"
        ) as market:
            with market.transaction():
                market.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                ticker = read_publication_tickers(
                    market,
                    symbols=(symbol,),
                    observed_at=observed_at,
                ).get(symbol)
    except (psycopg.Error, R13AnalyticsSourceUnavailable) as exc:
        parser.error(
            "shared MarketPublication ticker unavailable: "
            + type(exc).__name__
        )
    if ticker is None:
        parser.error("symbol ticker is missing from current MarketPublication")

    live_price = float(ticker.last_price)
    if abs(args.signal_price / live_price - 1) > 0.25:
        parser.error(
            f"signal price {args.signal_price} is inconsistent "
            f"with publication price {live_price}"
        )

    signal_id = args.signal_id or str(uuid.uuid4())
    now_ms = int(time.time() * 1000)
    signal_ms = args.signal_at_epoch_ms or now_ms
    lineage = {
        "decision": args.decision,
        "reason": args.reason,
        "market_source": "PG_MARKET_PUBLICATION",
        "publication_id": ticker.publication_id,
        "source_session_id": ticker.source_session_id,
        "producer_release_commit": ticker.producer_release_commit,
        "publication_as_of": ticker.publication_as_of.isoformat(),
        "ticker_observed_second": ticker.observed_second.isoformat(),
        "ticker_source_time_max": ticker.source_time_max.isoformat(),
        "trading_rights": "NONE",
    }
    with psycopg.connect(
        "dbname=cripta user=cripta host=/var/run/postgresql "
        "application_name=opportunity-register"
    ) as connection:
        result = connection.execute(
            """INSERT INTO monitoring.opportunities(
                   signal_id,bot_id,strategy_version,symbol,direction,
                   signal_at_epoch_ms,signal_price,decision,decision_reason,
                   traffic_light,horizon_seconds,state,created_at_epoch_ms)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'tracking',%s)
               ON CONFLICT(signal_id) DO NOTHING RETURNING signal_id""",
            (
                signal_id,
                args.bot_id,
                args.strategy_version,
                symbol,
                args.direction,
                signal_ms,
                args.signal_price,
                args.decision,
                args.reason,
                args.traffic_light,
                args.horizon_seconds,
                now_ms,
            ),
        ).fetchone()
        if result:
            connection.execute(
                """INSERT INTO monitoring.opportunity_events(
                       signal_id,at_epoch_ms,event,price,details_json)
                   VALUES(%s,%s,'registered',%s,%s)""",
                (
                    signal_id,
                    now_ms,
                    args.signal_price,
                    json.dumps(
                        lineage, ensure_ascii=False, sort_keys=True,
                    ),
                ),
            )
        connection.commit()
    print(json.dumps(
        {
            "signal_id": signal_id,
            "inserted": bool(result),
            "publication_id": ticker.publication_id,
            "source_session_id": ticker.source_session_id,
        },
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
