from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import psycopg
import websocket

URL = os.environ.get("BYBIT_PUBLIC_WS", "wss://stream.bybit.kz/v5/public/linear")
SYMBOLS = tuple(os.environ.get("CRIPTA_SYMBOLS", "1000PEPEUSDT,AAVEUSDT,ADAUSDT,APTUSDT,ARBUSDT,AVAXUSDT,BCHUSDT,BNBUSDT,BTCUSDT,DOGEUSDT,DOTUSDT,ETHUSDT,HBARUSDT,INJUSDT,LINKUSDT,LTCUSDT,NEARUSDT,OPUSDT,SOLUSDT,SUIUSDT,TRXUSDT,UNIUSDT,XLMUSDT,XRPUSDT").split(","))
LEVELS = (-3.0, -1.1, -1.0, -0.2, -0.1, 0.1, 0.5, 0.7, 1.0, 1.1)


@dataclass
class Opportunity:
    signal_id: str
    symbol: str
    direction: str
    signal_ms: int
    price: float
    horizon: int
    favorable: float = 0
    adverse: float = 0
    hits: dict[str, int] = field(default_factory=dict)
    samples: int = 0
    last_price: float | None = None
    dirty: bool = False

    def observe(self, trade_ms: int, price: float) -> list[tuple[float, int]]:
        if trade_ms < self.signal_ms:
            return []
        move = (price / self.price - 1) * 100 * (1 if self.direction == "long" else -1)
        self.favorable = max(self.favorable, move)
        self.adverse = min(self.adverse, move)
        self.samples += 1
        self.last_price = price
        self.dirty = True
        new_hits = []
        for level in LEVELS:
            key = f"{level:+.1f}"
            reached = move >= level if level > 0 else move <= level
            if reached and key not in self.hits:
                self.hits[key] = trade_ms
                new_hits.append((level, trade_ms))
        return new_hits


def load_active(connection: psycopg.Connection, active: dict[str, Opportunity]) -> None:
    rows = connection.execute("""SELECT signal_id,symbol,direction,signal_at_epoch_ms,signal_price,horizon_seconds,
        max_favorable_pct,max_adverse_pct,first_hits_json,samples,last_price FROM monitoring.opportunities WHERE state='tracking'""").fetchall()
    ids = set()
    for row in rows:
        ids.add(row[0])
        if row[0] not in active:
            active[row[0]] = Opportunity(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], json.loads(row[8]), row[9], row[10])
    for signal_id in set(active) - ids:
        active.pop(signal_id, None)


def flush(connection: psycopg.Connection, active: dict[str, Opportunity]) -> None:
    now_ms = int(time.time() * 1000)
    with connection.transaction():
        for item in list(active.values()):
            if item.dirty:
                connection.execute("""UPDATE monitoring.opportunities SET last_price=%s,max_favorable_pct=%s,
                    max_adverse_pct=%s,first_hits_json=%s,samples=%s WHERE signal_id=%s""", (item.last_price,
                    item.favorable, item.adverse, json.dumps(item.hits), item.samples, item.signal_id))
                item.dirty = False
            if now_ms >= item.signal_ms + item.horizon * 1000:
                connection.execute("UPDATE monitoring.opportunities SET state='completed',finalized_at_epoch_ms=%s WHERE signal_id=%s", (now_ms, item.signal_id))
                connection.execute("""INSERT INTO monitoring.opportunity_events(signal_id,at_epoch_ms,event,value_pct,
                    price,details_json) VALUES(%s,%s,'horizon_completed',%s,%s,%s)""", (item.signal_id, now_ms,
                    item.favorable, item.last_price, json.dumps({"mfe_pct": item.favorable, "mae_pct": item.adverse, "hits": item.hits})))
                active.pop(item.signal_id, None)


def main() -> None:
    connection = psycopg.connect(
        "dbname=cripta user=cripta host=/var/run/postgresql "
        "application_name=cripta-opportunity-tracker"
    )
    active: dict[str, Opportunity] = {}
    while True:
        try:
            ws = websocket.create_connection(URL, timeout=10, enable_multithread=False)
            ws.settimeout(1)
            ws.send(json.dumps({"op": "subscribe", "args": [f"publicTrade.{symbol}" for symbol in SYMBOLS]}, separators=(",", ":")))
            next_load = 0.0
            next_flush = time.monotonic() + 1
            next_ping = time.monotonic() + 20
            while True:
                now = time.monotonic()
                if now >= next_load:
                    load_active(connection, active)
                    # Close the read transaction before flush() opens its write transaction.
                    connection.commit()
                    next_load = now + 2
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    raw = None
                if raw:
                    message = json.loads(raw)
                    topic = str(message.get("topic") or "")
                    if topic.startswith("publicTrade."):
                        symbol = topic.split(".", 1)[1]
                        targets = [item for item in active.values() if item.symbol == symbol]
                        for trade in message.get("data") or []:
                            price = float(trade.get("p") or 0)
                            trade_ms = int(trade.get("T") or 0)
                            if price:
                                for item in targets:
                                    for level, hit_ms in item.observe(trade_ms, price):
                                        connection.execute("""INSERT INTO monitoring.opportunity_events(signal_id,at_epoch_ms,
                                            event,value_pct,price,details_json) VALUES(%s,%s,'level_first_hit',%s,%s,'{}')""",
                                            (item.signal_id, hit_ms, level, price))
                                if targets:
                                    connection.commit()
                now = time.monotonic()
                if now >= next_flush:
                    flush(connection, active)
                    next_flush = now + 1
                if now >= next_ping:
                    ws.send('{"op":"ping"}')
                    next_ping = now + 20
        except Exception:
            connection.rollback()
            time.sleep(3)


if __name__ == "__main__":
    main()
