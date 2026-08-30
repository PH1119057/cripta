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
            db.commit()
        self._repair_legacy_event_links()

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
                self._persist_coin_minutes(snapshot_id, snapshot)
                self._persist_observation_journal(snapshot_id, snapshot)
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
            except Exception as exc:
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
                    "SELECT id FROM mayak_v2.snapshots WHERE regular_minute=date_trunc('minute',%s::timestamptz) AND snapshot_kind='REGULAR'",
                    (snapshot["observed_at"],),
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

    def _link_events(self, snapshot_id: int) -> None:
        # Bind each event only to the newest snapshot that existed at or before the event.
        with suppress(psycopg.Error), psycopg.connect(DSN) as db:
            db.execute(
                """INSERT INTO mayak_v2.events(
                    occurred_at,event_type,reference_id,symbol,side,snapshot_id,payload,
                    link_quality,link_provenance)
                    SELECT to_timestamp(exec_time_ms/1000.0),'исполнение',
                    exec_id,symbol,side,s.id,payload_json::jsonb,'CAUSAL_PRIOR',
                    jsonb_build_object('linked_at',clock_timestamp(),'method','latest_snapshot_not_after_event')
                    FROM runtime.executions e
                    JOIN LATERAL (SELECT id FROM mayak_v2.snapshots
                        WHERE observed_at <= to_timestamp(e.exec_time_ms/1000.0)
                        ORDER BY observed_at DESC LIMIT 1) s ON true
                    WHERE exec_time_ms >= %s
                    ON CONFLICT(event_type,reference_id) DO NOTHING""",
                (int((time.time() - 120) * 1000),),
            )
            db.execute(
                """INSERT INTO mayak_v2.events(
                    occurred_at,event_type,reference_id,symbol,side,snapshot_id,payload,
                    link_quality,link_provenance)
                    SELECT to_timestamp(signal_at_epoch_ms/1000.0),'сигнал',
                    signal_id,symbol,direction,s.id,'{}'::jsonb,'CAUSAL_PRIOR',
                    jsonb_build_object('linked_at',clock_timestamp(),'method','latest_snapshot_not_after_event')
                    FROM monitoring.opportunities e
                    JOIN LATERAL (SELECT id FROM mayak_v2.snapshots
                        WHERE observed_at <= to_timestamp(e.signal_at_epoch_ms/1000.0)
                        ORDER BY observed_at DESC LIMIT 1) s ON true
                    WHERE signal_at_epoch_ms >= %s
                    ON CONFLICT(event_type,reference_id) DO NOTHING""",
                (int((time.time() - 120) * 1000),),
            )
            db.commit()

    def _repair_legacy_event_links(self) -> None:
        """Relink old events causally and retain explicit repair provenance."""
        with psycopg.connect(DSN) as db:
            db.execute(
                """UPDATE mayak_v2.events e SET snapshot_id=s.id,
                    link_quality='CAUSAL_RELINKED',
                    link_provenance=jsonb_build_object(
                        'repaired_at',clock_timestamp(),
                        'method','latest_snapshot_not_after_event',
                        'previous_snapshot_id',e.snapshot_id)
                    FROM mayak_v2.snapshots s
                    WHERE s.id=(SELECT prior.id FROM mayak_v2.snapshots prior
                        WHERE prior.observed_at <= e.occurred_at
                        ORDER BY prior.observed_at DESC LIMIT 1) AND (
                    e.link_quality='LEGACY_UNVERIFIED' OR e.snapshot_id IS NULL OR
                    EXISTS(SELECT 1 FROM mayak_v2.snapshots current_snapshot
                           WHERE current_snapshot.id=e.snapshot_id
                           AND current_snapshot.observed_at>e.occurred_at))"""
            )
            db.execute(
                """UPDATE mayak_v2.events SET link_quality='NO_CAUSAL_SNAPSHOT',
                    link_provenance=jsonb_build_object(
                        'checked_at',clock_timestamp(),'method','no_snapshot_not_after_event')
                    WHERE link_quality='LEGACY_UNVERIFIED'"""
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
