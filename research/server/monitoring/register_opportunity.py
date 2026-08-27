from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
import uuid

import psycopg


def main() -> None:
    parser = argparse.ArgumentParser(description="Register every strategy signal, including skipped ones")
    parser.add_argument("--signal-id", default="")
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--strategy-version", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--direction", choices=("long", "short"), required=True)
    parser.add_argument("--signal-price", type=float, required=True)
    parser.add_argument("--decision", choices=("entered", "skipped", "blocked", "shadow"), required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--traffic-light", choices=("green", "yellow", "red", "unknown"), default="unknown")
    parser.add_argument("--horizon-seconds", type=int, default=21600)
    parser.add_argument("--signal-at-epoch-ms", type=int, default=0)
    args = parser.parse_args()
    if args.signal_price <= 0 or not 60 <= args.horizon_seconds <= 7 * 86400:
        parser.error("invalid price or horizon")
    ticker_url = "https://api.bybit.kz/v5/market/tickers?" + urllib.parse.urlencode({"category": "linear", "symbol": args.symbol.upper()})
    with urllib.request.urlopen(ticker_url, timeout=8) as response:
        ticker = json.load(response)
    ticker_items = (ticker.get("result") or {}).get("list") or []
    if not ticker_items or ticker_items[0].get("symbol") != args.symbol.upper():
        parser.error("symbol is not available on Bybit linear market")
    live_price = float(ticker_items[0].get("lastPrice") or 0)
    if not live_price or abs(args.signal_price / live_price - 1) > 0.25:
        parser.error(f"signal price {args.signal_price} is inconsistent with live price {live_price}")
    signal_id = args.signal_id or str(uuid.uuid4())
    now_ms = int(time.time() * 1000)
    signal_ms = args.signal_at_epoch_ms or now_ms
    connection = psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql")
    result = connection.execute("""INSERT INTO monitoring.opportunities(signal_id,bot_id,strategy_version,symbol,
        direction,signal_at_epoch_ms,signal_price,decision,decision_reason,traffic_light,horizon_seconds,state,
        created_at_epoch_ms) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'tracking',%s)
        ON CONFLICT(signal_id) DO NOTHING RETURNING signal_id""", (signal_id, args.bot_id, args.strategy_version,
        args.symbol.upper(), args.direction, signal_ms, args.signal_price, args.decision, args.reason,
        args.traffic_light, args.horizon_seconds, now_ms)).fetchone()
    if result:
        connection.execute("""INSERT INTO monitoring.opportunity_events(signal_id,at_epoch_ms,event,price,details_json)
            VALUES(%s,%s,'registered',%s,%s)""", (signal_id, now_ms, args.signal_price, json.dumps({"decision": args.decision, "reason": args.reason}, ensure_ascii=False)))
    connection.commit()
    print(json.dumps({"signal_id": signal_id, "inserted": bool(result)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
