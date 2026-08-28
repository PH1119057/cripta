from __future__ import annotations

import json
import signal
import time
import urllib.parse
import urllib.request
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
_public_cache: dict[str, tuple[float, dict[str, object]]] = {}


def public_get(path: str, params: dict[str, str], ttl: float = 5) -> dict[str, object]:
    key = path + "?" + urllib.parse.urlencode(sorted(params.items()))
    cached = _public_cache.get(key)
    if cached and time.monotonic() - cached[0] <= ttl:
        return cached[1]
    request = urllib.request.Request(
        "https://api.bybit.kz" + key, headers={"User-Agent": "cripta-position-card/1"}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.load(response)
    if payload.get("retCode") != 0:
        raise RuntimeError(str(payload.get("retMsg") or "Bybit public API error"))
    _public_cache[key] = (time.monotonic(), payload)
    return payload


def price_feature(symbol: str, side: str, now: datetime) -> FeatureEvidence:
    payload = public_get(
        "/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": "1", "limit": "6"}, 8
    )
    rows = (payload.get("result") or {}).get("list") or []
    closes = [Decimal(str(row[4])) for row in rows]
    if len(closes) < 6 or any(value <= 0 for value in closes):
        return FeatureEvidence("unknown", now, Quality.MISSING)
    direction = Decimal("1") if side == "Buy" else Decimal("-1")
    moves = {
        "move_1m_pct": (closes[0] / closes[1] - 1) * 100 * direction,
        "move_3m_pct": (closes[0] / closes[3] - 1) * 100 * direction,
        "move_5m_pct": (closes[0] / closes[5] - 1) * 100 * direction,
    }
    state = "continuation" if moves["move_1m_pct"] > 0 else "against"
    return FeatureEvidence(state, now, Quality.FRESH, moves)


def execution_and_book_features(
    symbol: str, side: str, now: datetime
) -> tuple[FeatureEvidence, FeatureEvidence]:
    ticker_payload = public_get(
        "/v5/market/tickers", {"category": "linear", "symbol": symbol}, 2
    )
    ticker = ((ticker_payload.get("result") or {}).get("list") or [{}])[0]
    bid = Decimal(str(ticker.get("bid1Price") or 0))
    ask = Decimal(str(ticker.get("ask1Price") or 0))
    middle = (bid + ask) / 2 if bid > 0 and ask > 0 else Decimal("0")
    spread_bps = (ask - bid) / middle * 10_000 if middle > 0 else Decimal("0")
    execution = FeatureEvidence(
        "executable" if middle > 0 else "unavailable",
        now,
        Quality.FRESH if middle > 0 else Quality.MISSING,
        {"bid": bid, "ask": ask, "spread_bps": spread_bps},
    )
    book_payload = public_get(
        "/v5/market/orderbook", {"category": "linear", "symbol": symbol, "limit": "25"}, 3
    )
    result = book_payload.get("result") or {}
    bid_depth = sum(Decimal(str(p)) * Decimal(str(q)) for p, q in result.get("b", []))
    ask_depth = sum(Decimal(str(p)) * Decimal(str(q)) for p, q in result.get("a", []))
    favorable = bid_depth if side == "Buy" else ask_depth
    adverse = ask_depth if side == "Buy" else bid_depth
    ratio = favorable / adverse if adverse > 0 else Decimal("0")
    state = "replenishment" if ratio >= Decimal("1.15") else "withdrawal" if ratio <= Decimal("0.85") else "balanced"
    book = FeatureEvidence(
        state, now, Quality.FRESH,
        {"bid_depth_usdt": bid_depth, "ask_depth_usdt": ask_depth, "directional_ratio": ratio},
    )
    return execution, book


def market_feature(symbol: str, now: datetime) -> FeatureEvidence:
    feature = price_feature(symbol, "Buy", now)
    return FeatureEvidence(feature.state, feature.observed_at, feature.quality, feature.measurements)


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
    symbol: str, side: str, now: datetime, context: dict[str, dict[str, object]]
) -> dict[str, FeatureEvidence]:
    item = context.get(symbol)
    result: dict[str, FeatureEvidence] = {}
    if item:
        try:
            observed = datetime.fromisoformat(str(item["updated_at"]))
            quality = Quality.PARTIAL if (now - observed).total_seconds() <= 15 else Quality.STALE
            result["flow"] = FeatureEvidence(
                str(item.get("flow_state") or "unknown"), observed, quality
            )
            result["oi_price"] = FeatureEvidence(
                str(item.get("oi_state") or "unknown"), observed, quality
            )
            result["structure"] = FeatureEvidence(
                "unknown", observed, quality,
                {
                    "scanner_status": str(item.get("status") or ""),
                    "distance_pct": str(item.get("distance_pct") or ""),
                },
            )
        except (ValueError, TypeError, KeyError):
            pass
    result.setdefault("structure", FeatureEvidence("unknown", now, Quality.MISSING))
    try:
        result["price_1m"] = price_feature(symbol, side, now)
        execution, orderbook = execution_and_book_features(symbol, side, now)
        result["execution_now"] = execution
        result["orderbook"] = orderbook
        flow_state = result.get("flow", FeatureEvidence("unknown", now, Quality.MISSING)).state
        price_state = result["price_1m"].state
        absorption_state = (
            "absorption"
            if flow_state == "pressure_continues" and price_state == "continuation"
            else "none"
        )
        result["absorption"] = FeatureEvidence(absorption_state, now, Quality.PARTIAL)
        result["market_btc"] = market_feature("BTCUSDT", now)
        result["market_eth"] = market_feature("ETHUSDT", now)
    except (OSError, RuntimeError, ValueError, TypeError):
        for name in ("price_1m", "execution_now", "orderbook", "absorption", "market_btc", "market_eth"):
            result.setdefault(name, FeatureEvidence("unknown", now, Quality.MISSING))
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
                    PositionEvent(now, mark, features(position.symbol, position.side, now, context))
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
