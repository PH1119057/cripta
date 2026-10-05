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

from bybit_workbench.mayak.context_records import (
    coin_market_context_records,
    shared_market_context_record,
)
from bybit_workbench.mayak.continuity import MinuteContinuityTracker
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
SUBSCRIBE_BATCH_LIMIT = {"spot": 10, "linear": 30}


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
        self.last_persisted_handoff: dict[str, Any] | None = None
        self.pending_liquidations: list[tuple[float, str, str, float, float]] = []
        self.liquidation_lock = threading.Lock()
        self.state_write_lock = threading.Lock()
        self.subscription_acks: dict[str, dict[str, Any]] = {}
        self.previous_state: str | None = None
        self.continuity = MinuteContinuityTracker()
        self.transport_gap_started_at: dict[str, float | None] = {
            "spot": None,
            "linear": None,
        }
        self.transport_gap_lock = threading.Lock()

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
            db.execute("""CREATE TABLE IF NOT EXISTS mayak_v2.continuity_gaps(
                source text NOT NULL,
                scope text NOT NULL,
                gap_started_at timestamptz NOT NULL,
                gap_ended_at timestamptz NOT NULL,
                duration_seconds double precision NOT NULL,
                detected_at timestamptz NOT NULL DEFAULT clock_timestamp(),
                repair_status text NOT NULL DEFAULT 'UNREPAIRED',
                repair_source text,
                provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
                PRIMARY KEY(source,scope,gap_started_at,gap_ended_at))""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS mayak_v2_continuity_gaps_started "
                "ON mayak_v2.continuity_gaps(gap_started_at DESC)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS mayak_v2.shared_market_contexts(
                market_context_id TEXT PRIMARY KEY,
                mayak_snapshot_id BIGINT NOT NULL REFERENCES mayak_v2.snapshots(id),
                observed_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                mayak_version TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                config_fingerprint TEXT NOT NULL,
                data_quality TEXT NOT NULL,
                payload JSONB NOT NULL,
                provenance JSONB NOT NULL,
                content_hash TEXT UNIQUE NOT NULL)""")
            db.commit()
        self._backfill_snapshot_continuity_gaps()
        self._load_persistence_checkpoint()

    def _backfill_snapshot_continuity_gaps(self) -> None:
        """Materialize historical missing minute buckets without inventing snapshots."""
        with psycopg.connect(DSN) as db:
            db.execute(
                """WITH ordered AS (
                    SELECT regular_minute,
                           lag(regular_minute) OVER(ORDER BY regular_minute) AS previous
                    FROM mayak_v2.snapshots
                    WHERE snapshot_kind='REGULAR' AND regular_minute IS NOT NULL
                )
                INSERT INTO mayak_v2.continuity_gaps(
                    source,scope,gap_started_at,gap_ended_at,duration_seconds,
                    repair_status,provenance)
                SELECT 'SNAPSHOT_CADENCE','GLOBAL',
                       previous + interval '1 minute',
                       regular_minute - interval '1 minute',
                       extract(epoch FROM (regular_minute-previous))-60,
                       'UNREPAIRED',
                       jsonb_build_object(
                           'detected_by','DATA_CONTINUITY_REPAIR_V1',
                           'semantics','MISSING_BUCKETS_NOT_SYNTHESIZED')
                FROM ordered
                WHERE previous IS NOT NULL
                  AND regular_minute-previous > interval '1 minute'
                ON CONFLICT(source,scope,gap_started_at,gap_ended_at) DO NOTHING"""
            )
            db.commit()

    def _persist_continuity_gap(
        self,
        *,
        source: str,
        scope: str,
        started_at: datetime,
        ended_at: datetime,
        provenance: dict[str, Any],
    ) -> None:
        if ended_at < started_at:
            return
        duration_seconds = max(0.0, (ended_at - started_at).total_seconds())
        with psycopg.connect(DSN) as db:
            db.execute(
                """INSERT INTO mayak_v2.continuity_gaps(
                    source,scope,gap_started_at,gap_ended_at,duration_seconds,
                    repair_status,provenance)
                   VALUES(%s,%s,%s,%s,%s,'UNREPAIRED',%s::jsonb)
                   ON CONFLICT(source,scope,gap_started_at,gap_ended_at) DO NOTHING""",
                (
                    source,
                    scope,
                    started_at,
                    ended_at,
                    duration_seconds,
                    json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                ),
            )
            db.commit()

    def _load_persistence_checkpoint(self) -> None:
        with psycopg.connect(DSN) as db:
            latest = db.execute(
                """SELECT s.regular_minute,s.state,j.dispatcher_handoff
                FROM mayak_v2.snapshots s
                LEFT JOIN mayak_v2.observation_journal j ON j.snapshot_id=s.id
                WHERE s.snapshot_kind='REGULAR' AND s.regular_minute IS NOT NULL
                ORDER BY s.regular_minute DESC LIMIT 1"""
            ).fetchone()
            stats = db.execute(
                """SELECT min(regular_minute),max(regular_minute),count(*)
                FROM mayak_v2.snapshots
                WHERE snapshot_kind='REGULAR' AND regular_minute IS NOT NULL"""
            ).fetchone()
            max_gap = db.execute(
                """WITH ordered AS (
                    SELECT regular_minute,
                           lag(regular_minute) OVER(ORDER BY regular_minute) AS previous
                    FROM mayak_v2.snapshots
                    WHERE snapshot_kind='REGULAR' AND regular_minute IS NOT NULL
                )
                SELECT coalesce(max(extract(epoch FROM (regular_minute-previous))/60),0)
                FROM ordered WHERE previous IS NOT NULL"""
            ).fetchone()[0]
            liquidation_rows = db.execute(
                """SELECT extract(epoch FROM occurred_at),symbol,position_side,
                          bankruptcy_price,executed_size
                   FROM mayak_v2.liquidations
                   WHERE occurred_at >= clock_timestamp() - interval '24 hours'
                     AND occurred_at <= clock_timestamp()
                   ORDER BY occurred_at"""
            ).fetchall()
        self._restore_liquidation_checkpoint(liquidation_rows)
        if latest is not None:
            self.last_persisted_minute = latest[0]
            self.previous_state = str(latest[1])
            if isinstance(latest[2], dict):
                self.last_persisted_handoff = latest[2]
            startup_gap_origin = latest[0].timestamp()
            with self.transport_gap_lock:
                for market in self.transport_gap_started_at:
                    self.transport_gap_started_at[market] = startup_gap_origin
        first, last, actual = stats
        actual_count = int(actual or 0)
        expected_count = 0
        missing_count = 0
        if first is not None and last is not None:
            expected_count = int((last - first).total_seconds() // 60) + 1
            missing_count = max(0, expected_count - actual_count)
        self.continuity = MinuteContinuityTracker(
            start_minute=first,
            last_minute=last,
            expected_snapshots=expected_count,
            actual_snapshots=actual_count,
            missing_snapshots=missing_count,
            max_gap_minutes=int(float(max_gap or 0)),
        )

    def _restore_liquidation_checkpoint(
        self, rows: list[tuple[Any, str, str, float, float]]
    ) -> None:
        """Restore persisted exact liquidation events without inventing missing events."""
        for occurred_at, symbol, side, price, size in rows:
            self.engine.on_liquidation(
                str(symbol),
                float(occurred_at),
                str(side),
                float(price),
                float(size),
            )

    def _advance_continuity(self, minute: datetime) -> dict[str, Any]:
        result = self.continuity.advance(minute)
        gap_start = result.get("gap_started_at")
        gap_end = result.get("gap_ended_at")
        if gap_start and gap_end:
            self._persist_continuity_gap(
                source="SNAPSHOT_CADENCE",
                scope="GLOBAL",
                started_at=datetime.fromisoformat(str(gap_start)),
                ended_at=datetime.fromisoformat(str(gap_end)),
                provenance={
                    "detected_by": "DATA_CONTINUITY_REPAIR_V1",
                    "semantics": "MISSING_BUCKETS_NOT_SYNTHESIZED",
                    "missing_minutes": result.get("gap_missing_minutes"),
                },
            )
        self.last_persisted_minute = minute
        return result

    def run(self) -> None:
        self.prepare()
        self._refresh_instrument_support()
        threads = [
            threading.Thread(target=self._socket_loop, args=(market,), daemon=True)
            for market in ("spot", "linear")
        ]
        for thread in threads:
            thread.start()
        next_ratios = 0.0
        while not self.stop.wait(1):
            now = datetime.now(UTC)
            if time.monotonic() >= next_ratios:
                threading.Thread(target=self._refresh_ratios, daemon=True).start()
                next_ratios = time.monotonic() + 300
            snapshot = self.engine.snapshot(now)
            minute = now.replace(second=0, microsecond=0)
            if self.last_persisted_minute is None or minute > self.last_persisted_minute:
                snapshot["collector_continuity"] = self._advance_continuity(minute)
                snapshot_id = self._persist_snapshot(snapshot)
                self._persist_liquidations()
                self._persist_coin_minutes(snapshot_id, snapshot)
                self._persist_coin_market_contexts(snapshot_id, snapshot)
                self._persist_observation_journal(snapshot_id, snapshot)
                self._persist_shared_market_context(snapshot_id, snapshot)
                self.last_persisted_handoff = snapshot["dispatcher_handoff"]
                state = str(snapshot["state"])
                if state != self.previous_state:
                    self._persist_state_event(snapshot_id, snapshot, self.previous_state)
                    self.previous_state = state
            if self.last_persisted_handoff is not None:
                snapshot["dispatcher_handoff"] = self.last_persisted_handoff
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

    def _mark_transport_disconnected(
        self, market: str, disconnected_at: float, error: str | None = None
    ) -> None:
        with self.transport_gap_lock:
            if self.transport_gap_started_at.get(market) is None:
                self.transport_gap_started_at[market] = disconnected_at
        self.engine.on_transport(
            market,
            connected=False,
            timestamp=disconnected_at,
            error=error,
        )

    def _mark_transport_connected(self, market: str, connected_at: float) -> None:
        with self.transport_gap_lock:
            gap_started_at = self.transport_gap_started_at.get(market)
            self.transport_gap_started_at[market] = None
        if gap_started_at is not None and connected_at > gap_started_at:
            self._persist_continuity_gap(
                source="WS_TRANSPORT",
                scope=market,
                started_at=datetime.fromtimestamp(gap_started_at, UTC),
                ended_at=datetime.fromtimestamp(connected_at, UTC),
                provenance={
                    "detected_by": "DATA_CONTINUITY_REPAIR_V1",
                    "semantics": "EXACT_EVENTS_DURING_GAP_UNKNOWN",
                    "endpoint": WS[market],
                },
            )
        self.engine.on_transport(market, connected=True, timestamp=connected_at)

    def _socket_loop(self, market: str) -> None:
        while not self.stop.is_set():
            sock = None
            try:
                sock = websocket.create_connection(WS[market], timeout=10)
                sock.settimeout(1)
                connected_at = time.time()
                self._mark_transport_connected(market, connected_at)
                topics = [
                    topic
                    for symbol in self.symbols
                    for topic in (f"publicTrade.{symbol}", f"orderbook.50.{symbol}")
                ]
                if market == "linear":
                    topics.extend(f"tickers.{symbol}" for symbol in self.symbols)
                    topics.extend(f"allLiquidation.{symbol}" for symbol in self.symbols)
                batch_limit = SUBSCRIBE_BATCH_LIMIT[market]
                for start in range(0, len(topics), batch_limit):
                    batch = topics[start : start + batch_limit]
                    req_id = f"mayak-{market}-{start // batch_limit}-{int(time.time() * 1000)}"
                    self.subscription_acks[req_id] = {
                        "market": market,
                        "args": tuple(batch),
                        "status": "PENDING",
                    }
                    sock.send(json.dumps({"op": "subscribe", "req_id": req_id, "args": batch}))
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
                disconnected_at = time.time()
                self._mark_transport_disconnected(
                    market, disconnected_at, error=type(exc).__name__
                )
                try:
                    self._write_error(market, exc)
                except Exception as write_exc:  # noqa: BLE001 - reconnect must survive status failures
                    print(
                        "MAYAK_STATUS_WRITE_ERROR "
                        f"market={market} error={type(write_exc).__name__}: {write_exc}",
                        flush=True,
                    )
                self.stop.wait(3)
            finally:
                if sock is not None:
                    with suppress(Exception):
                        sock.close()

    def _message(self, market: str, message: dict[str, Any]) -> None:
        topic = str(message.get("topic") or "")
        data = message.get("data")
        timestamp = float(message.get("ts") or time.time() * 1000) / 1000
        if message.get("op") == "subscribe" or "success" in message and message.get("req_id"):
            req_id = str(message.get("req_id") or "")
            row = self.subscription_acks.setdefault(req_id, {"market": market, "args": ()})
            success = bool(message.get("success"))
            row["status"] = "ACK" if success else "REJECTED"
            row["response"] = {
                key: message.get(key) for key in ("success", "ret_msg", "conn_id") if key in message
            }
            if not success:
                raise RuntimeError(
                    f"MAYAK_SUBSCRIPTION_REJECTED market={market} req_id={req_id} "
                    f"reason={message.get('ret_msg') or message.get('retMsg') or 'unknown'}"
                )
            return
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
                ("openInterestValue", "open_interest_value"),
                ("fundingRate", "funding_rate"),
                ("lastPrice", "last_price"),
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
            if row is None:
                raise RuntimeError("MAYAK_REGULAR_SNAPSHOT_ID_MISSING")
            return int(row[0])

    def _persist_liquidations(self) -> None:
        with self.liquidation_lock:
            pending = tuple(self.pending_liquidations)
        if not pending:
            return
        with psycopg.connect(DSN) as db:
            db.cursor().executemany(
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

    def _persist_coin_market_contexts(
        self, snapshot_id: int, snapshot: dict[str, Any]
    ) -> None:
        records = coin_market_context_records(snapshot_id, snapshot)
        if not records:
            return
        rows = [
            (
                record["coin_context_id"],
                record["mayak_snapshot_id"],
                record["observed_at"],
                record["symbol"],
                record["schema_version"],
                record["engine_version"],
                record["feature_version"],
                record["config_fingerprint"],
                record["data_quality"],
                json.dumps(
                    record["payload"],
                    ensure_ascii=False,
                    default=str,
                    sort_keys=True,
                ),
                json.dumps(
                    record["provenance"],
                    ensure_ascii=False,
                    default=str,
                    sort_keys=True,
                ),
                record["content_hash"],
            )
            for record in records
        ]
        with psycopg.connect(DSN) as db:
            db.cursor().executemany(
                """INSERT INTO mayak_v2.coin_market_contexts(
                    coin_context_id,mayak_snapshot_id,observed_at,symbol,schema_version,
                    engine_version,feature_version,config_fingerprint,data_quality,
                    payload,provenance,content_hash)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(coin_context_id) DO NOTHING""",
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
        with self.state_write_lock:
            tmp = STATE_PATH.with_name(
                f"{STATE_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                tmp.write_text(
                    json.dumps(snapshot, ensure_ascii=False, default=str), encoding="utf-8"
                )
                tmp.replace(STATE_PATH)
            finally:
                with suppress(FileNotFoundError):
                    tmp.unlink()

    def _persist_shared_market_context(
        self, snapshot_id: int, snapshot: dict[str, Any]
    ) -> None:
        """Persist the immutable market observation; it never carries a trade command."""
        record = shared_market_context_record(snapshot_id, snapshot)
        with psycopg.connect(DSN) as db:
            db.execute(
                """INSERT INTO mayak_v2.shared_market_contexts(
                    market_context_id,mayak_snapshot_id,observed_at,mayak_version,
                    schema_version,config_fingerprint,data_quality,payload,provenance,
                    content_hash)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(market_context_id) DO NOTHING""",
                (
                    record["market_context_id"],
                    record["mayak_snapshot_id"],
                    record["observed_at"],
                    record["mayak_version"],
                    record["schema_version"],
                    record["config_fingerprint"],
                    record["data_quality"],
                    json.dumps(
                        record["payload"],
                        ensure_ascii=False,
                        default=str,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    json.dumps(record["provenance"], ensure_ascii=False),
                    record["content_hash"],
                ),
            )
            db.commit()

    def _write_error(self, market: str, exc: Exception) -> None:
        payload = dict(
            self.last_snapshot or {"state": "прогрев", "confidence": 0, "coins": {}}
        )
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
