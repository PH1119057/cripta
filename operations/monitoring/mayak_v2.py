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
        self.last_persisted_minute: datetime | None = None
        self.pending_liquidations: list[tuple[float, str, str, float, float]] = []
        self.liquidation_lock = threading.Lock()

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
            db.execute(
                "ALTER TABLE mayak_v2.snapshots ADD COLUMN IF NOT EXISTS "
                "snapshot_kind text NOT NULL DEFAULT 'LEGACY'"
            )
            db.execute(
                "ALTER TABLE mayak_v2.snapshots ADD COLUMN IF NOT EXISTS regular_minute timestamptz"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS mayak_v2_one_regular_per_minute "
                "ON mayak_v2.snapshots(regular_minute) WHERE snapshot_kind='REGULAR'"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS mayak_v2.events(
                id bigserial PRIMARY KEY, occurred_at timestamptz NOT NULL,
                event_type text NOT NULL, reference_id text NOT NULL,
                symbol text, side text, snapshot_id bigint REFERENCES mayak_v2.snapshots(id),
                payload jsonb NOT NULL DEFAULT '{}'::jsonb,
                UNIQUE(event_type, reference_id))""")
            db.execute(
                "ALTER TABLE mayak_v2.events ADD COLUMN IF NOT EXISTS "
                "link_quality text NOT NULL DEFAULT 'LEGACY_UNVERIFIED'"
            )
            db.execute(
                "ALTER TABLE mayak_v2.events ADD COLUMN IF NOT EXISTS link_provenance jsonb "
                "NOT NULL DEFAULT '{}'::jsonb"
            )
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
            db.execute("""CREATE TABLE IF NOT EXISTS mayak_v2.observation_journal(
                snapshot_id bigint PRIMARY KEY REFERENCES mayak_v2.snapshots(id),
                observed_at timestamptz NOT NULL,
                market_state text NOT NULL,
                confidence double precision NOT NULL,
                reasons jsonb NOT NULL,
                price_breadth jsonb NOT NULL,
                money_breadth jsonb NOT NULL,
                direction_synchronization jsonb NOT NULL,
                btc_context jsonb,
                eth_context jsonb,
                data_quality jsonb NOT NULL,
                architecture_version text NOT NULL,
                engine_version text NOT NULL,
                feature_version text NOT NULL,
                config_fingerprint text NOT NULL)""")
            db.execute(
                "ALTER TABLE mayak_v2.observation_journal "
                "ADD COLUMN IF NOT EXISTS dispatcher_handoff jsonb"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS mayak_v2_observation_journal_at "
                "ON mayak_v2.observation_journal(observed_at DESC)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS mayak_v2.liquidations(
                occurred_at timestamptz NOT NULL,
                symbol text NOT NULL,
                position_side text NOT NULL,
                bankruptcy_price double precision NOT NULL,
                executed_size double precision NOT NULL,
                notional_usd double precision NOT NULL,
                source text NOT NULL DEFAULT 'bybit_all_liquidation_v5',
                PRIMARY KEY(occurred_at,symbol,position_side,bankruptcy_price,executed_size))""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS mayak_v2_liquidations_at "
                "ON mayak_v2.liquidations(occurred_at DESC)"
            )
            db.commit()

    def run(self) -> None:
        self.prepare()
        self._refresh_instrument_support()
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
            snapshot = self.engine.snapshot(now)
            minute = now.replace(second=0, microsecond=0)
            if now.second < 2 and minute != self.last_persisted_minute:
                snapshot_id = self._persist_snapshot(snapshot)
                self.last_persisted_minute = minute
                self._persist_liquidations()
                self._persist_coin_minutes(snapshot_id, snapshot)
                self._persist_observation_journal(snapshot_id, snapshot)
                state = str(snapshot["state"])
                if state != previous_state:
                    self._persist_state_event(snapshot_id, snapshot, previous_state)
                    previous_state = state
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

    def _refresh_instrument_support(self) -> None:
        for market in ("spot", "linear"):
            try:
                query = urllib.parse.urlencode({"category": market, "limit": 1000})
                request = urllib.request.Request(
                    f"https://api.bybit.kz/v5/market/instruments-info?{query}",
                    headers={"User-Agent": "Cripta-Mayak-V2-read-only"},
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.load(response)
                rows = payload.get("result", {}).get("list", [])
                supported = {
                    str(row["symbol"])
                    for row in rows
                    if str(row.get("status") or "Trading") == "Trading"
                }
                self.engine.set_instrument_support(market, supported)
            except (OSError, ValueError, KeyError, TypeError):
                self.engine.set_instrument_support(market, None)

    def _socket_loop(self, market: str) -> None:
        while not self.stop.is_set():
            sock = None
            try:
                sock = websocket.create_connection(WS[market], timeout=10)
                sock.settimeout(1)
                self.engine.on_transport(market, connected=True, timestamp=time.time())
                topics = [
                    topic
                    for symbol in self.symbols
                    for topic in (f"publicTrade.{symbol}", f"orderbook.50.{symbol}")
                ]
                if market == "linear":
                    topics.extend(f"tickers.{symbol}" for symbol in self.symbols)
                    topics.extend(f"allLiquidation.{symbol}" for symbol in self.symbols)
                for start in range(0, len(topics), 30):
                    sock.send(json.dumps({"op": "subscribe", "args": topics[start : start + 30]}))
                ping = time.monotonic() + 20
                while not self.stop.is_set():
                    try:
                        message = json.loads(sock.recv())
                        if isinstance(message, dict):
                            self.engine.on_transport(market, connected=True, timestamp=time.time())
                            self._message(market, message)
                    except websocket.WebSocketTimeoutException:
                        pass
                    if time.monotonic() >= ping:
                        sock.send('{"op":"ping"}')
                        ping = time.monotonic() + 20
            except Exception as exc:  # noqa: BLE001 - collector must recover from any WS failure
                self.engine.on_transport(
                    market, connected=False, timestamp=time.time(), error=type(exc).__name__
                )
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
        elif topic.startswith("allLiquidation.") and isinstance(data, list):
            for row in data:
                with suppress(KeyError, TypeError, ValueError):
                    occurred_at = float(row["T"]) / 1000
                    symbol = str(row["s"])
                    side = str(row["S"])
                    price = float(row["p"])
                    size = float(row["v"])
                    self.engine.on_liquidation(symbol, occurred_at, side, price, size)
                    with self.liquidation_lock:
                        self.pending_liquidations.append(
                            (occurred_at, symbol, side, price, size)
                        )
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

    def _persist_snapshot(self, snapshot: dict[str, Any]) -> int:
        with psycopg.connect(DSN) as db:
            row = db.execute(
                """INSERT INTO mayak_v2.snapshots(
                    observed_at,state,confidence,payload,engine_version,snapshot_kind,regular_minute)
                    VALUES(%s,%s,%s,%s,%s,'REGULAR',date_trunc('minute',%s::timestamptz))
                    ON CONFLICT(regular_minute) WHERE snapshot_kind='REGULAR' DO NOTHING
                    RETURNING id""",
                (
                    snapshot["observed_at"],
                    str(snapshot["state"]),
                    snapshot["confidence"],
                    json.dumps(snapshot, default=str),
                    self.engine.VERSION,
                    snapshot["observed_at"],
                ),
            ).fetchone()
            if row is None:
                row = db.execute(
                    """SELECT id FROM mayak_v2.snapshots
                    WHERE regular_minute=date_trunc('minute',%s::timestamptz)
                    AND snapshot_kind='REGULAR'""",
                    (snapshot["observed_at"],),
                ).fetchone()
            db.commit()
            return int(row[0])

    def _persist_liquidations(self) -> None:
        with self.liquidation_lock:
            pending = tuple(self.pending_liquidations)
        if not pending:
            return
        with psycopg.connect(DSN) as db:
            db.executemany(
                """INSERT INTO mayak_v2.liquidations(
                    occurred_at,symbol,position_side,bankruptcy_price,executed_size,notional_usd)
                    VALUES(to_timestamp(%s),%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""",
                [(*row, row[3] * row[4]) for row in pending],
            )
            db.commit()
        with self.liquidation_lock:
            del self.pending_liquidations[: len(pending)]

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

    def _persist_observation_journal(
        self, snapshot_id: int, snapshot: dict[str, Any]
    ) -> None:
        """Persist exactly what Mayak concluded from data available at this moment."""
        data_quality = {
            symbol: coin.get("quality", {})
            for symbol, coin in snapshot.get("coins", {}).items()
        }
        with psycopg.connect(DSN) as db:
            db.execute(
                """INSERT INTO mayak_v2.observation_journal(
                    snapshot_id,observed_at,market_state,confidence,reasons,
                    price_breadth,money_breadth,direction_synchronization,
                    btc_context,eth_context,data_quality,architecture_version,
                    engine_version,feature_version,config_fingerprint,dispatcher_handoff)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(snapshot_id) DO NOTHING""",
                (
                    snapshot_id,
                    snapshot["observed_at"],
                    str(snapshot["state"]),
                    snapshot["confidence"],
                    json.dumps(snapshot["reasons"], default=str),
                    json.dumps(snapshot["price_breadth"], default=str),
                    json.dumps(snapshot["money_breadth"], default=str),
                    json.dumps(snapshot["direction_synchronization"], default=str),
                    json.dumps(snapshot.get("btc"), default=str),
                    json.dumps(snapshot.get("eth"), default=str),
                    json.dumps(data_quality, default=str),
                    snapshot["architecture_version"],
                    snapshot["engine_version"],
                    snapshot["feature_version"],
                    snapshot["config_fingerprint"],
                    json.dumps(snapshot["dispatcher_handoff"], default=str),
                ),
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
