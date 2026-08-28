#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import threading
import time
import urllib.parse
import urllib.request
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import websocket

from bybit_workbench.mayak.core.live import LiveMayakEngine

EXCLUDED = {"1000PEPEUSDT", "DOGEUSDT", "NEARUSDT", "XLMUSDT"}
DEFAULT_SYMBOLS = (
    "AAVEUSDT",
    "ADAUSDT",
    "APTUSDT",
    "ARBUSDT",
    "AVAXUSDT",
    "BCHUSDT",
    "BNBUSDT",
    "DOTUSDT",
    "HBARUSDT",
    "INJUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "OPUSDT",
    "SOLUSDT",
    "SUIUSDT",
    "TRXUSDT",
    "UNIUSDT",
    "XRPUSDT",
    "BTCUSDT",
    "ETHUSDT",
)
STATE_PATH = Path(os.environ.get("MAYAK_V2_STATE", "/var/lib/cripta/mayak_v2/status.json"))
DSN = os.environ.get("CRIPTA_DSN", "dbname=cripta user=cripta host=/var/run/postgresql")
WS = {
    "spot": "wss://stream.bybit.kz/v5/public/spot",
    "linear": "wss://stream.bybit.kz/v5/public/linear",
}


class Collector:
    def __init__(self) -> None:
        configured = os.environ.get("MAYAK_V2_SYMBOLS", ",".join(DEFAULT_SYMBOLS))
        self.symbols = tuple(
            x.strip().upper()
            for x in configured.split(",")
            if x.strip() and x.strip().upper() not in EXCLUDED
        )
        self.engine = LiveMayakEngine(self.symbols)
        self.stop = threading.Event()
        self.books: dict[tuple[str, str], dict[str, dict[float, float]]] = {}
        self.last_snapshot: dict[str, Any] | None = None

    def prepare(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with psycopg.connect(DSN) as db:
            db.execute("CREATE SCHEMA IF NOT EXISTS mayak_v2")
            db.execute("""CREATE TABLE IF NOT EXISTS mayak_v2.snapshots(
                id bigserial PRIMARY KEY, observed_at timestamptz NOT NULL,
                state text NOT NULL, confidence double precision NOT NULL,
                payload jsonb NOT NULL, engine_version text NOT NULL)""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS mayak_v2_snapshots_at "
                "ON mayak_v2.snapshots(observed_at DESC)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS mayak_v2.events(
                id bigserial PRIMARY KEY, occurred_at timestamptz NOT NULL,
                event_type text NOT NULL, reference_id text NOT NULL,
                symbol text, side text, snapshot_id bigint REFERENCES mayak_v2.snapshots(id),
                payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                UNIQUE(event_type, reference_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS mayak_v2.state_events(
                id bigserial PRIMARY KEY, occurred_at timestamptz NOT NULL,
                state text NOT NULL, previous_state text, confidence double precision NOT NULL,
                reasons jsonb NOT NULL, snapshot_id bigint REFERENCES mayak_v2.snapshots(id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS mayak_v2.coin_minutes(
                observed_at timestamptz NOT NULL,
                snapshot_id bigint NOT NULL REFERENCES mayak_v2.snapshots(id),
                symbol text NOT NULL,
                spot_buy_usd double precision, spot_sell_usd double precision,
                spot_net_usd double precision, spot_turnover_usd double precision,
                derivatives_buy_usd double precision, derivatives_sell_usd double precision,
                derivatives_net_usd double precision, derivatives_turnover_usd double precision,
                return_5m_pct double precision, open_interest double precision,
                open_interest_change_pct double precision, funding_rate double precision,
                mark_price double precision, index_price double precision,
                long_ratio double precision, short_ratio double precision,
                spot_bid_usd double precision, spot_ask_usd double precision,
                spot_bid_change_pct double precision, spot_ask_change_pct double precision,
                derivatives_bid_usd double precision, derivatives_ask_usd double precision,
                derivatives_bid_change_pct double precision,
                derivatives_ask_change_pct double precision,
                large_spot_buy_usd double precision, large_spot_sell_usd double precision,
                large_derivatives_buy_usd double precision,
                large_derivatives_sell_usd double precision,
                source_quality jsonb NOT NULL,
                PRIMARY KEY(observed_at,symbol))""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS mayak_v2_coin_symbol_at "
                "ON mayak_v2.coin_minutes(symbol,observed_at DESC)"
            )
            db.commit()

    def run(self) -> None:
        self.prepare()
        threads = [
            threading.Thread(target=self._socket_loop, args=(market,), daemon=True)
            for market in ("spot", "linear")
        ]
        for thread in threads:
            thread.start()
        previous_state: str | None = None
        next_ratios = 0.0
        while not self.stop.wait(1):
            now = datetime.now(UTC)
            if time.monotonic() >= next_ratios:
                threading.Thread(target=self._refresh_ratios, daemon=True).start()
                next_ratios = time.monotonic() + 300
            signals, positions = self._read_context()
            snapshot = self.engine.snapshot(now, signals=signals, positions=positions)
            if now.second < 2:
                snapshot_id = self._persist_snapshot(snapshot)
                self._persist_coin_minutes(snapshot_id, snapshot)
                state = str(snapshot["state"])
                if state != previous_state:
                    self._persist_state_event(snapshot_id, snapshot, previous_state)
                    previous_state = state
                self._link_events(snapshot_id)
            self.last_snapshot = snapshot
            self._write_state(snapshot)
        for thread in threads:
            thread.join(timeout=5)

    def _refresh_ratios(self) -> None:
        for symbol in self.symbols:
            if self.stop.is_set():
                return
            try:
                query = urllib.parse.urlencode(
                    {"category": "linear", "symbol": symbol, "period": "5min", "limit": 1}
                )
                request = urllib.request.Request(
                    f"https://api.bybit.kz/v5/market/account-ratio?{query}",
                    headers={"User-Agent": "Cripta-Mayak-V2-read-only"},
                )
                with urllib.request.urlopen(request, timeout=8) as response:
                    payload = json.load(response)
                row = payload.get("result", {}).get("list", [])[0]
                self.engine.on_ticker(
                    symbol,
                    time.time(),
                    long_ratio=float(row["buyRatio"]),
                    short_ratio=float(row["sellRatio"]),
                )
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                continue

    def _socket_loop(self, market: str) -> None:
        while not self.stop.is_set():
            sock = None
            try:
                sock = websocket.create_connection(WS[market], timeout=10)
                sock.settimeout(1)
                topics = [
                    topic
                    for symbol in self.symbols
                    for topic in (f"publicTrade.{symbol}", f"orderbook.50.{symbol}")
                ]
                if market == "linear":
                    topics.extend(f"tickers.{symbol}" for symbol in self.symbols)
                for start in range(0, len(topics), 30):
                    sock.send(json.dumps({"op": "subscribe", "args": topics[start : start + 30]}))
                ping = time.monotonic() + 20
                while not self.stop.is_set():
                    try:
                        message = json.loads(sock.recv())
                        if isinstance(message, dict):
                            self._message(market, message)
                    except websocket.WebSocketTimeoutException:
                        pass
                    if time.monotonic() >= ping:
                        sock.send('{"op":"ping"}')
                        ping = time.monotonic() + 20
            except Exception as exc:
                self._write_error(market, exc)
                self.stop.wait(3)
            finally:
                if sock is not None:
                    with suppress(Exception):
                        sock.close()

    def _message(self, market: str, message: dict[str, Any]) -> None:
        topic = str(message.get("topic") or "")
        data = message.get("data")
        timestamp = float(message.get("ts") or time.time() * 1000) / 1000
        if topic.startswith("publicTrade.") and isinstance(data, list):
            for row in data:
                with suppress(KeyError, TypeError, ValueError):
                    self.engine.on_trade(
                        market,
                        str(row["s"]),
                        float(row["T"]) / 1000,
                        str(row["S"]),
                        float(row["p"]),
                        float(row["v"]),
                    )
        elif topic.startswith("tickers.") and isinstance(data, dict):
            symbol = topic.rsplit(".", 1)[-1]
            values = {}
            for source, target in (
                ("openInterest", "open_interest"),
                ("fundingRate", "funding_rate"),
                ("markPrice", "mark_price"),
                ("indexPrice", "index_price"),
            ):
                with suppress(TypeError, ValueError):
                    if data.get(source) not in (None, ""):
                        values[target] = float(data[source])
            self.engine.on_ticker(symbol, timestamp, **values)
        elif topic.startswith("orderbook.") and isinstance(data, dict):
            self._book(
                market,
                str(data.get("s") or topic.rsplit(".", 1)[-1]),
                timestamp,
                str(message.get("type") or "snapshot"),
                data,
            )

    def _book(
        self, market: str, symbol: str, timestamp: float, kind: str, data: dict[str, Any]
    ) -> None:
        key = (market, symbol)
        if kind == "snapshot" or key not in self.books:
            self.books[key] = {"b": {}, "a": {}}
        state = self.books[key]
        for side in ("b", "a"):
            for raw in data.get(side, []):
                with suppress(TypeError, ValueError, IndexError):
                    price, size = float(raw[0]), float(raw[1])
                    if size:
                        state[side][price] = size
                    else:
                        state[side].pop(price, None)
        bids = sorted(state["b"].items(), reverse=True)[:50]
        asks = sorted(state["a"].items())[:50]
        self.engine.on_book(market, symbol, timestamp, bids, asks)

    def _read_context(self) -> tuple[dict[str, Any], dict[str, Any]]:
        signals: dict[str, Any] = {"count_30m": 0, "unique_30m": 0, "long_30m": 0, "short_30m": 0}
        positions: dict[str, Any] = {
            "count": 0,
            "long": 0,
            "short": 0,
            "below_entry": 0,
            "worse_0p10": 0,
            "worse_0p25": 0,
            "worse_0p50": 0,
            "worse_0p75": 0,
        }
        try:
            with psycopg.connect(DSN) as db:
                rows = db.execute(
                    """SELECT direction,count(*),count(DISTINCT symbol)
                    FROM monitoring.opportunities
                    WHERE signal_at_epoch_ms >= %s GROUP BY direction""",
                    (int((time.time() - 1800) * 1000),),
                ).fetchall()
                for direction, count, unique in rows:
                    signals["count_30m"] += count
                    signals["unique_30m"] += unique
                    signals[
                        "long_30m"
                        if str(direction).lower() in ("buy", "long", "покупка")
                        else "short_30m"
                    ] += count
                for _symbol, side, entry, payload, size in db.execute(
                    "SELECT symbol,side,entry_price,payload_json,size FROM runtime.hot_positions"
                ):
                    try:
                        mark = json.loads(payload).get("markPrice")
                    except (TypeError, json.JSONDecodeError):
                        mark = None
                    if float(size or 0) <= 0 or float(entry or 0) <= 0 or float(mark or 0) <= 0:
                        continue
                    direction = 1 if str(side).lower() in ("buy", "long") else -1
                    move = (float(mark) / float(entry) - 1) * 100 * direction
                    positions["count"] += 1
                    positions["long" if direction == 1 else "short"] += 1
                    positions["below_entry"] += move < 0
                    for threshold, key in (
                        (-0.10, "worse_0p10"),
                        (-0.25, "worse_0p25"),
                        (-0.50, "worse_0p50"),
                        (-0.75, "worse_0p75"),
                    ):
                        positions[key] += move < threshold
        except psycopg.Error:
            positions["quality"] = "нет данных"
            signals["quality"] = "нет данных"
        return signals, positions

    def _persist_snapshot(self, snapshot: dict[str, Any]) -> int:
        with psycopg.connect(DSN) as db:
            row = db.execute(
                """INSERT INTO mayak_v2.snapshots(
                    observed_at,state,confidence,payload,engine_version)
                    VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                (
                    snapshot["observed_at"],
                    str(snapshot["state"]),
                    snapshot["confidence"],
                    json.dumps(snapshot, default=str),
                    self.engine.VERSION,
                ),
            ).fetchone()
            db.commit()
            return int(row[0])

    def _persist_state_event(
        self, snapshot_id: int, snapshot: dict[str, Any], previous: str | None
    ) -> None:
        with psycopg.connect(DSN) as db:
            db.execute(
                """INSERT INTO mayak_v2.state_events(
                    occurred_at,state,previous_state,confidence,reasons,snapshot_id)
                    VALUES(%s,%s,%s,%s,%s,%s)""",
                (
                    snapshot["observed_at"],
                    str(snapshot["state"]),
                    previous,
                    snapshot["confidence"],
                    json.dumps(snapshot["reasons"], default=str),
                    snapshot_id,
                ),
            )
            db.commit()

    def _persist_coin_minutes(self, snapshot_id: int, snapshot: dict[str, Any]) -> None:
        def get(source: dict[str, Any], key: str) -> Any:
            return source.get(key)

        rows = []
        for symbol, coin in snapshot.get("coins", {}).items():
            spot, linear = coin.get("spot") or {}, coin.get("linear") or {}
            ticker, books = coin.get("ticker") or {}, coin.get("books") or {}
            spot_book, linear_book = books.get("spot") or {}, books.get("linear") or {}
            rows.append(
                (
                    snapshot["observed_at"],
                    snapshot_id,
                    symbol,
                    get(spot, "buy_usd"),
                    get(spot, "sell_usd"),
                    get(spot, "net_usd"),
                    get(spot, "turnover_usd"),
                    get(linear, "buy_usd"),
                    get(linear, "sell_usd"),
                    get(linear, "net_usd"),
                    get(linear, "turnover_usd"),
                    get(linear, "return_pct"),
                    get(ticker, "open_interest"),
                    get(ticker, "open_interest_change_pct"),
                    get(ticker, "funding_rate"),
                    get(ticker, "mark_price"),
                    get(ticker, "index_price"),
                    get(ticker, "long_ratio"),
                    get(ticker, "short_ratio"),
                    get(spot_book, "bid_usd"),
                    get(spot_book, "ask_usd"),
                    get(spot_book, "bid_change_pct"),
                    get(spot_book, "ask_change_pct"),
                    get(linear_book, "bid_usd"),
                    get(linear_book, "ask_usd"),
                    get(linear_book, "bid_change_pct"),
                    get(linear_book, "ask_change_pct"),
                    get(spot, "large_buy_usd"),
                    get(spot, "large_sell_usd"),
                    get(linear, "large_buy_usd"),
                    get(linear, "large_sell_usd"),
                    json.dumps(coin.get("quality") or {}, default=str),
                )
            )
        if rows:
            with psycopg.connect(DSN) as db:
                db.cursor().executemany(
                    """INSERT INTO mayak_v2.coin_minutes VALUES(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(observed_at,symbol) DO NOTHING""",
                    rows,
                )
                db.commit()

    def _link_events(self, snapshot_id: int) -> None:
        # Idempotently bind actual fills/exits and signals to the causal snapshot available now.
        with suppress(psycopg.Error), psycopg.connect(DSN) as db:
            db.execute(
                """INSERT INTO mayak_v2.events(
                    occurred_at,event_type,reference_id,symbol,side,snapshot_id,payload)
                    SELECT to_timestamp(exec_time_ms/1000.0),'исполнение',
                    exec_id,symbol,side,%s,payload_json::jsonb
                    FROM runtime.executions WHERE exec_time_ms >= %s
                    ON CONFLICT(event_type,reference_id) DO NOTHING""",
                (snapshot_id, int((time.time() - 120) * 1000)),
            )
            db.execute(
                """INSERT INTO mayak_v2.events(
                    occurred_at,event_type,reference_id,symbol,side,snapshot_id,payload)
                    SELECT to_timestamp(signal_at_epoch_ms/1000.0),'сигнал',
                    signal_id,symbol,direction,%s,'{}'::jsonb
                    FROM monitoring.opportunities WHERE signal_at_epoch_ms >= %s
                    ON CONFLICT(event_type,reference_id) DO NOTHING""",
                (snapshot_id, int((time.time() - 120) * 1000)),
            )
            db.commit()

    def _write_state(self, snapshot: dict[str, Any]) -> None:
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(STATE_PATH)

    def _write_error(self, market: str, exc: Exception) -> None:
        payload = self.last_snapshot or {"state": "прогрев", "confidence": 0, "coins": {}}
        payload["collector_error"] = {
            "market": market,
            "message": str(exc),
            "observed_at": datetime.now(UTC).isoformat(),
        }
        self._write_state(payload)


def main() -> None:
    collector = Collector()
    signal.signal(signal.SIGTERM, lambda *_: collector.stop.set())
    signal.signal(signal.SIGINT, lambda *_: collector.stop.set())
    collector.run()


if __name__ == "__main__":
    main()
