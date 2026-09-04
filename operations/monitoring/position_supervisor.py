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

MAYAK_STATUS = Path("/var/lib/cripta/mayak_v2/status.json")
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
        "/v5/market/kline",
        {"category": "linear", "symbol": symbol, "interval": "1", "limit": "6"},
        8,
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
    ticker_payload = public_get("/v5/market/tickers", {"category": "linear", "symbol": symbol}, 2)
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
    state = (
        "replenishment"
        if ratio >= Decimal("1.15")
        else "withdrawal"
        if ratio <= Decimal("0.85")
        else "balanced"
    )
    book = FeatureEvidence(
        state,
        now,
        Quality.FRESH,
        {"bid_depth_usdt": bid_depth, "ask_depth_usdt": ask_depth, "directional_ratio": ratio},
    )
    return execution, book


def market_feature(symbol: str, now: datetime) -> FeatureEvidence:
    feature = price_feature(symbol, "Buy", now)
    return FeatureEvidence(
        feature.state, feature.observed_at, feature.quality, feature.measurements
    )


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
    connection.execute("""CREATE TABLE IF NOT EXISTS supervisor.dispatcher_hold_context(
        position_id TEXT NOT NULL, assessment_id TEXT NOT NULL,
        mayak_snapshot_id TEXT, assessment_observed_at TIMESTAMPTZ NOT NULL,
        consumed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        profile_id TEXT NOT NULL, profile_version TEXT NOT NULL,
        dispatcher_status TEXT NOT NULL, supervisor_state TEXT NOT NULL,
        action TEXT NOT NULL, context_type TEXT NOT NULL
            CHECK(context_type='CONSUMED_CONTEXT'),
        trading_effect TEXT NOT NULL CHECK(trading_effect='FULL_LIVE_V1'),
        payload JSONB NOT NULL,
        PRIMARY KEY(position_id,assessment_id,supervisor_state))""")
    connection.execute("""CREATE TABLE IF NOT EXISTS supervisor.position_structure_handoff(
        event_id TEXT PRIMARY KEY, position_id TEXT NOT NULL, symbol TEXT NOT NULL,
        structural_event TEXT NOT NULL CHECK(structural_event IN (
            'protective_clean_break_against','protective_hold_reclaim',
            'obstacle_clean_break_with','obstacle_rejection_against')),
        observed_at TIMESTAMPTZ NOT NULL, zone_id TEXT NOT NULL,
        zone_role TEXT NOT NULL, lower_price NUMERIC NOT NULL,
        upper_price NUMERIC NOT NULL, source_timeframe TEXT NOT NULL,
        geometry_version TEXT NOT NULL, signal_at TIMESTAMPTZ NOT NULL,
        fill_at TIMESTAMPTZ NOT NULL, provenance JSONB NOT NULL,
        CHECK(observed_at>=signal_at), CHECK(observed_at>=fill_at),
        CHECK(lower_price<=upper_price))""")
    connection.execute("""CREATE TABLE IF NOT EXISTS supervisor.exit_economics_snapshots(
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        position_id TEXT NOT NULL, symbol TEXT NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL, purpose TEXT NOT NULL,
        economics_json JSONB NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS supervisor.exit_decisions(
        decision_id TEXT PRIMARY KEY, position_id TEXT NOT NULL, symbol TEXT NOT NULL,
        decided_at TIMESTAMPTZ NOT NULL, internal_reason TEXT NOT NULL,
        hold_assessment_id TEXT, mayak_snapshot_id TEXT,
        supervisor_snapshot_id BIGINT, structural_event_id TEXT,
        economics_snapshot_id BIGINT REFERENCES supervisor.exit_economics_snapshots(id),
        close_command_id TEXT, decision_json JSONB NOT NULL)""")
    connection.commit()


def exchange_positions(connection: psycopg.Connection) -> list[tuple[ExchangePosition, Decimal]]:
    rows = connection.execute("""SELECT symbol,position_idx,side,size,entry_price,
        leverage,payload_json FROM runtime.hot_positions ORDER BY symbol""").fetchall()
    result: list[tuple[ExchangePosition, Decimal]] = []
    for row in rows:
        raw = json.loads(row[6])
        open_ms = int(raw.get("openTime") or raw.get("createdTime") or 0)
        owner = connection.execute(
            """SELECT position_id FROM runtime.position_ownership
               WHERE symbol=%s AND side=%s AND state='OPEN'
                 AND abs(extract(epoch from (fill_at-to_timestamp(%s/1000.0))))<=10
               ORDER BY fill_at DESC LIMIT 1""",
            (row[0], row[2], open_ms),
        ).fetchone()
        identity = ExchangePosition(
            position_id=(
                str(owner[0]) if owner is not None
                else f"LEGACY:{row[0]}:{row[1]}:{open_ms}:{row[2]}"
            ),
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


def mayak_context() -> tuple[datetime | None, dict[str, dict[str, object]]]:
    try:
        payload = json.loads(MAYAK_STATUS.read_text(encoding="utf-8"))
        observed = datetime.fromisoformat(str(payload["observed_at"]))
        coins = payload.get("coins") or {}
        return observed, {str(symbol): item for symbol, item in coins.items()}
    except (OSError, ValueError, KeyError):
        return None, {}


def structure_feature(
    connection: psycopg.Connection, position_id: str, now: datetime
) -> tuple[FeatureEvidence, str | None, datetime | None]:
    row = connection.execute(
        """SELECT structural_event,observed_at,event_id,zone_id,zone_role,
                  lower_price,upper_price,source_timeframe,geometry_version
           FROM supervisor.position_structure_handoff
           WHERE position_id=%s AND observed_at<=%s
           ORDER BY observed_at DESC LIMIT 1""",
        (position_id, now),
    ).fetchone()
    if row is None:
        return FeatureEvidence("unknown", now, Quality.MISSING), None, None
    event, observed_at = str(row[0]), row[1]
    age = (now - observed_at).total_seconds()
    quality = Quality.FRESH if 0 <= age <= 90 else Quality.STALE
    state = {
        "protective_clean_break_against": "broken",
        "protective_hold_reclaim": "reclaim",
        "obstacle_clean_break_with": "with",
        "obstacle_rejection_against": "warning",
    }[event]
    return (
        FeatureEvidence(
            state,
            observed_at,
            quality,
            {
                "event_id": str(row[2]),
                "zone_id": str(row[3]),
                "zone_role": str(row[4]),
                "lower_price": str(row[5]),
                "upper_price": str(row[6]),
                "source_timeframe": str(row[7]),
                "geometry_version": str(row[8]),
            },
        ),
        event,
        observed_at,
    )


def features(
    connection: psycopg.Connection,
    position_id: str,
    symbol: str,
    side: str,
    now: datetime,
    observed: datetime | None,
    context: dict[str, dict[str, object]],
) -> tuple[dict[str, FeatureEvidence], str | None, datetime | None]:
    item = context.get(symbol)
    result: dict[str, FeatureEvidence] = {}
    if item and observed:
        try:
            age = (now - observed).total_seconds()
            quality = Quality.FRESH if age <= 15 else Quality.STALE
            linear = item.get("linear") or {}
            net = Decimal(str(linear.get("net_usd") or 0))
            directional_net = net if side == "Buy" else -net
            flow_state = (
                "favorable"
                if directional_net > 0
                else "persistent_adverse"
                if directional_net < 0
                else "balanced"
            )
            result["flow"] = FeatureEvidence(
                flow_state, observed, quality, {"directional_net_usd": directional_net}
            )
            ticker = item.get("ticker") or {}
            oi_change = ticker.get("open_interest_change_5m_pct")
            result["oi_price"] = FeatureEvidence(
                "unknown" if oi_change is None else "available",
                observed,
                Quality.MISSING if oi_change is None else quality,
                {"open_interest_change_5m_pct": "" if oi_change is None else str(oi_change)},
            )
        except (ValueError, TypeError, KeyError):
            pass
    structure, structural_event, structure_observed_at = structure_feature(
        connection, position_id, now
    )
    result["structure"] = structure
    try:
        result["price_1m"] = price_feature(symbol, side, now)
        execution, orderbook = execution_and_book_features(symbol, side, now)
        result["execution_now"] = execution
        result["orderbook"] = orderbook
        flow_state = result.get("flow", FeatureEvidence("unknown", now, Quality.MISSING)).state
        price_state = result["price_1m"].state
        absorption_state = (
            "absorption"
            if flow_state == "persistent_adverse" and price_state == "continuation"
            else "none"
        )
        absorption_quality = (
            Quality.FRESH
            if result.get("flow") and result["flow"].quality == Quality.FRESH
            else Quality.MISSING
        )
        result["absorption"] = FeatureEvidence(absorption_state, now, absorption_quality)
        result["market_btc"] = market_feature("BTCUSDT", now)
        result["market_eth"] = market_feature("ETHUSDT", now)
    except (OSError, RuntimeError, ValueError, TypeError):
        for name in (
            "price_1m",
            "execution_now",
            "orderbook",
            "absorption",
            "market_btc",
            "market_eth",
        ):
            result.setdefault(name, FeatureEvidence("unknown", now, Quality.MISSING))
    if structural_event == "protective_clean_break_against" and result.get("price_1m"):
        price = result["price_1m"]
        result["price_1m"] = FeatureEvidence(
            "failed_reclaim", price.observed_at, price.quality, price.measurements
        )
    return result, structural_event, structure_observed_at


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
    last_periodic_save: dict[str, int] = {}
    with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
        initialize(connection)
        while running:
            now = datetime.now(UTC)
            rows = exchange_positions(connection)
            created, _ = registry.reconcile(position for position, _ in rows)
            restore_created(connection, registry, created)
            context_observed, context = mayak_context()
            for position, mark in rows:
                evidence, structural_event, structure_observed_at = features(
                    connection,
                    position.position_id,
                    position.symbol,
                    position.side,
                    now,
                    context_observed,
                    context,
                )
                snapshot = registry.get(position.position_id).update(
                    PositionEvent(
                        now,
                        mark,
                        evidence,
                    )
                )
                document = snapshot.audit_dict()
                at_ms = int(now.timestamp() * 1000)
                state_changed = snapshot.previous_state != snapshot.state
                should_persist = (
                    state_changed
                    or at_ms - last_periodic_save.get(position.position_id, 0) >= 60_000
                )
                if should_persist:
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
                    last_periodic_save[position.position_id] = at_ms
                profile_id = "M3_V1_LONG_HOLD" if position.side == "Buy" else "M3_V1_SHORT_HOLD"
                hold = connection.execute(
                    """SELECT assessment_id,mayak_snapshot_id,observed_at,
                              profile_version,status,data_quality,payload
                       FROM strategy_dispatcher.assessments
                       WHERE profile_id=%s AND profile_version='1.0.0-owner-live'
                         AND observed_at<=%s
                       ORDER BY observed_at DESC,stored_at DESC LIMIT 1""",
                    (profile_id, now),
                ).fetchone()
                if hold is not None:
                    action = "OBSERVE_ONLY"
                    context_payload = {
                        "position_id": position.position_id,
                        "assessment_id": hold[0],
                        "mayak_snapshot_id": hold[1],
                        "assessment_observed_at": hold[2].isoformat(),
                        "profile_id": profile_id,
                        "profile_version": hold[3],
                        "dispatcher_status": hold[4],
                        "data_quality": hold[5],
                        "supervisor_state": snapshot.state.value,
                        "supervisor_reason": snapshot.reason,
                        "structural_event": structural_event,
                        "structure_observed_at": (
                            None if structure_observed_at is None
                            else structure_observed_at.isoformat()
                        ),
                        "action": action,
                        "authority": "POSITION_SUPERVISOR_INFORMATION_ONLY_V36",
                        "context_type": "CONSUMED_CONTEXT",
                        "trading_effect": "NONE",
                        "legacy_db_trading_effect_column": "FULL_LIVE_V1",
                    }
                    connection.execute(
                        """INSERT INTO supervisor.dispatcher_hold_context(
                            position_id,assessment_id,mayak_snapshot_id,
                            assessment_observed_at,profile_id,profile_version,
                            dispatcher_status,supervisor_state,action,context_type,
                            trading_effect,payload)
                            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                   'CONSUMED_CONTEXT','FULL_LIVE_V1',%s)
                            ON CONFLICT DO NOTHING""",
                        (
                            position.position_id,
                            hold[0],
                            hold[1],
                            hold[2],
                            profile_id,
                            hold[3],
                            hold[4],
                            snapshot.state.value,
                            action,
                            json.dumps(context_payload, ensure_ascii=False),
                        ),
                    )
                if state_changed:
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
