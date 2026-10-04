from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

import psycopg
import websocket
from exact_close import (
    classify_exit,
    number,
    resolve_exchange_position_close,
    trigger_to_fill_slippage_pct,
)
from position_cycle import stable_cycle_ids
from protection_math import (
    calculate_initial_boundaries,
    calculate_protection_plan,
    trailing_start_preserves_protection,
)
from runtime_schema import (
    EXPECTED_RUNTIME_SCHEMA_VERSION,
    validate_runtime_schema_contract,
)
from safety_observer import api_get

from bybit_workbench.exchange.bybit.mappers import map_rest_klines
from bybit_workbench.universal_entry.market_watch import compute_r1_l53_stable_zone

from bybit_workbench.entry_reservation_lifecycle import (
    finalize_failed_entry_command_reservation,
    mark_entry_order_acknowledged,
    resolve_cancelled_entry_reservation_after_reconcile,
)
from bybit_workbench.strategy_position_binding import (
    load_universal_entry_lineage,
    persist_universal_strategy_position,
    release_position_capital_reservation,
)
from bybit_workbench.universal_exit.contracts import ExitActionKind
from bybit_workbench.universal_exit.execution_bridge import validate_exit_mutation
from bybit_workbench.universal_exit.execution_store import mark_ambiguous_exit_command

PRIVATE_URL = os.environ.get("BYBIT_PRIVATE_WS", "wss://stream.bybit.kz/v5/private?max_active_time=1m")
TRADE_URL = os.environ.get("BYBIT_TRADE_WS", "wss://stream.bybit.kz/v5/trade?max_active_time=1m")
STATUS = Path("/var/lib/cripta/private_runtime/status.json")
running = True
status_lock = threading.Lock()
status: dict[str, object] = {"private": {"state": "starting"}, "trade": {"state": "starting"}}
REST_URL = os.environ.get("BYBIT_REST", "https://api.bybit.kz")
_tick_cache: dict[str, Decimal] = {}
_ticker_cache: dict[str, tuple[float, Decimal, Decimal, Decimal]] = {}
_slippage_cache: dict[str, tuple[float, Decimal]] = {}
EXCLUDED_TRADING_SYMBOLS = {"1000PEPEUSDT", "DOGEUSDT", "NEARUSDT", "XLMUSDT"}
SIGNAL_PICKUP_WINDOW_MS = int(os.environ.get("CRIPTA_SIGNAL_PICKUP_WINDOW_MS", "120000"))
BOT_INSTANCE_ID = os.environ.get("CRIPTA_BOT_INSTANCE_ID", "m3-mainnet-primary")
ENTRY_COMMAND_SOURCE = os.environ.get("CRIPTA_ENTRY_COMMAND_SOURCE", "LEGACY_V1").strip().upper()
if ENTRY_COMMAND_SOURCE not in {"LEGACY_V1", "UNIVERSAL_ENTRY"}:
    raise RuntimeError(f"unsupported CRIPTA_ENTRY_COMMAND_SOURCE={ENTRY_COMMAND_SOURCE}")
PROCESS_STARTED_AT_MS = int(time.time() * 1000)
RECONCILIATION_MAX_AGE_MS = int(
    os.environ.get("CRIPTA_RECONCILIATION_MAX_AGE_MS", "15000")
)
SIGNED_RECV_WINDOW = "5000"
SIGNED_MUTATION_TIMEOUT_SECONDS = 3.0
MUTATION_CLOCK_MAX_ABS_OFFSET_MS = 500.0


class ExchangeMutationBarrier(RuntimeError):
    """Mutation outcome or immediate post-mutation truth is uncertain."""


class UnsafeBybitClock(ExchangeMutationBarrier):
    """Local/Bybit clock evidence is outside the mutation safety limit."""


class AmbiguousBybitMutation(ExchangeMutationBarrier):
    """Transport ended without deterministic exchange acknowledgement."""


class BybitMutationRejected(RuntimeError):
    """Bybit explicitly rejected the mutation."""


def executable_close_price(symbol: str, side: str) -> Decimal:
    """Return the immediately executable exit price, never the mark price."""
    cached = _ticker_cache.get(symbol)
    if not cached or time.monotonic() - cached[0] > 1:
        payload, _ = api_get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
        item = ((payload.get("result") or {}).get("list") or [{}])[0]
        cached = (
            time.monotonic(), Decimal(str(item.get("lastPrice") or 0)),
            Decimal(str(item.get("bid1Price") or 0)), Decimal(str(item.get("ask1Price") or 0)),
        )
        _ticker_cache[symbol] = cached
    _, last, bid, ask = cached
    price = bid if side == "Buy" else ask
    return price if price > 0 else last


def observed_adverse_slippage(connection: psycopg.Connection, symbol: str) -> Decimal:
    """Worst recent trigger-to-fill gap plus a small live reserve."""
    cached = _slippage_cache.get(symbol)
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    worst = Decimal("0")
    rows = connection.execute(
        "SELECT payload_json FROM runtime.private_events WHERE topic='order.linear' "
        "ORDER BY received_at_epoch_ms DESC LIMIT 5000"
    ).fetchall()
    for (raw_payload,) in rows:
        payload = json.loads(raw_payload)
        items = payload if isinstance(payload, list) else payload.get("data", [])
        for item in items:
            if item.get("symbol") != symbol or item.get("orderStatus") != "Filled":
                continue
            trigger = Decimal(str(item.get("triggerPrice") or 0))
            fill = Decimal(str(item.get("avgPrice") or 0))
            if trigger <= 0 or fill <= 0:
                continue
            gap = ((trigger - fill) / trigger if item.get("side") == "Sell"
                   else (fill - trigger) / trigger)
            worst = max(worst, gap)
    reserve = max(Decimal("0.0002"), worst + Decimal("0.0002"))
    _slippage_cache[symbol] = (time.monotonic(), reserve)
    return reserve


def db(application_name: str) -> psycopg.Connection:
    return psycopg.connect(
        "dbname=cripta user=cripta host=/var/run/postgresql "
        f"application_name={application_name}"
    )


def disarm_new_entries(connection: psycopg.Connection, reason: str) -> None:
    """Close only the new-entry gate; ownership/protection commands remain available."""
    now_ms = int(time.time() * 1000)
    connection.execute(
        """UPDATE control.execution_gates
           SET enabled=0,reason=%s,updated_at_epoch_ms=%s
           WHERE mode='mainnet'""",
        (reason, now_ms),
    )
    connection.commit()


def entry_runtime_readiness(connection: psycopg.Connection) -> tuple[bool, str]:
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    if not gate or not bool(gate[0]):
        return False, "NEW_ENTRY_GATE_DISARMED"
    private = status.get("private", {})
    if not isinstance(private, dict) or private.get("state") != "connected":
        return False, "PRIVATE_WS_NOT_CONNECTED"
    latest = connection.execute(
        """SELECT finished_at_epoch_ms,ok FROM runtime.reconciliation_runs
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    now_ms = int(time.time() * 1000)
    if (
        not latest
        or not bool(latest[1])
        or now_ms - int(latest[0]) > RECONCILIATION_MAX_AGE_MS
    ):
        return False, "FRESH_RECONCILIATION_REQUIRED"
    wallet = connection.execute(
        "SELECT refreshed_at_epoch_ms FROM runtime.wallet_latest WHERE singleton=1"
    ).fetchone()
    if not wallet or now_ms - int(wallet[0]) > RECONCILIATION_MAX_AGE_MS:
        return False, "MANDATORY_EXCHANGE_STATE_STALE"
    if not connection.execute(
        "SELECT 1 FROM runtime.trade_settings WHERE singleton=1"
    ).fetchone():
        return False, "SERVER_TRADING_SETTINGS_MISSING"
    unresolved = connection.execute(
        """SELECT 1 FROM runtime.trade_commands
           WHERE command_type='entry' AND state='running'
             AND error LIKE 'EXCHANGE_MUTATION_BARRIER:%'
           LIMIT 1"""
    ).fetchone()
    if unresolved:
        return False, "AMBIGUOUS_EXCHANGE_MUTATION"
    ambiguous = connection.execute(
        """SELECT 1 FROM runtime.trade_commands
           WHERE command_type='entry' AND state IN ('queued','running')
             AND requested_at_epoch_ms < %s LIMIT 1""",
        (PROCESS_STARTED_AT_MS,),
    ).fetchone()
    if ambiguous:
        return False, "AMBIGUOUS_PRESTART_ENTRY_COMMAND"
    pending_owned = connection.execute(
        """SELECT 1 FROM runtime.hot_orders o
           JOIN runtime.trade_commands c ON c.command_id=o.order_link_id
           WHERE c.command_type='entry'
             AND o.order_status IN ('New','PartiallyFilled','Untriggered')
           LIMIT 1"""
    ).fetchone()
    if pending_owned:
        return False, "BOT_OWNED_PENDING_ENTRY_REMAINS"
    for symbol, raw_payload in connection.execute(
        "SELECT symbol,payload_json FROM runtime.hot_positions"
    ).fetchall():
        raw = (
            raw_payload
            if isinstance(raw_payload, dict)
            else json.loads(raw_payload)
        )
        stop = Decimal(str(raw.get("stopLoss") or 0))
        trailing = Decimal(str(raw.get("trailingStop") or 0))
        if stop <= 0 and trailing <= 0:
            return False, f"UNPROTECTED_EXCHANGE_POSITION:{symbol}"
    return True, "REARM_READY"


def refresh_recent_executions(
    connection: psycopg.Connection, key: str, secret: str
) -> None:
    response, _ = api_get(
        "/v5/execution/list", {"category": "linear", "limit": "100"}, key, secret
    )
    if response.get("retCode") != 0:
        raise RuntimeError("exchange rejected startup execution recovery")
    now = int(time.time() * 1000)
    for item in ((response.get("result") or {}).get("list") or []):
        connection.execute(
            """INSERT INTO runtime.executions(
                exec_id,order_id,order_link_id,symbol,side,exec_qty,exec_price,
                exec_fee,exec_time_ms,received_at_epoch_ms,payload_json)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(exec_id) DO NOTHING""",
            (
                item.get("execId", ""),
                item.get("orderId", ""),
                item.get("orderLinkId", ""),
                item.get("symbol", ""),
                item.get("side", ""),
                item.get("execQty", ""),
                item.get("execPrice", ""), item.get("execFee", ""),
                int(item.get("execTime") or 0), now,
                json.dumps(item, ensure_ascii=False),
            ),
        )
    connection.commit()


def cancel_bot_owned_pending_entry_orders(
    connection: psycopg.Connection, key: str, secret: str
) -> int:
    rows = connection.execute(
        """SELECT o.order_id,o.symbol,o.payload_json
           FROM runtime.hot_orders o
           JOIN runtime.trade_commands c ON c.command_id=o.order_link_id
           WHERE c.command_type='entry'
             AND o.order_status IN ('New','PartiallyFilled','Untriggered')"""
    ).fetchall()
    cancelled = 0
    for order_id, symbol, raw_payload in rows:
        raw = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
        if bool(raw.get("reduceOnly")) or bool(raw.get("closeOnTrigger")):
            continue
        api_post(
            "/v5/order/cancel",
            {"category": "linear", "symbol": symbol, "orderId": order_id},
            key,
            secret,
            accepted_codes=(110001,),
        )
        cancelled += 1
    if cancelled:
        reconcile(connection, key, secret, "restart_entry_cancel")
    return cancelled


def protect_recovered_bot_positions(
    connection: psycopg.Connection,
    key: str,
    secret: str,
) -> int:
    """Restore initial server protection only for exact bot Entry cycles."""
    rows = connection.execute(
        """SELECT c.command_id,c.symbol,min(e.exec_time_ms),c.payload_json
           FROM runtime.trade_commands c
           JOIN runtime.executions e ON e.order_link_id=c.command_id
           WHERE c.command_type='entry'
             AND COALESCE(
                   (e.payload_json::jsonb->>'closedSize')::numeric,
                   0
                 )=0
           GROUP BY c.command_id,c.symbol,c.payload_json"""
    ).fetchall()
    protected = 0

    for command_id, symbol, fill_time_ms, raw_entry_payload in rows:
        position_row = connection.execute(
            """SELECT side,entry_price,payload_json
               FROM runtime.hot_positions
               WHERE symbol=%s
               ORDER BY position_idx LIMIT 1""",
            (symbol,),
        ).fetchone()
        if position_row is None:
            continue

        raw = (
            position_row[2]
            if isinstance(position_row[2], dict)
            else json.loads(position_row[2])
        )
        open_time_ms = int(raw.get("openTime") or 0)
        if open_time_ms and abs(open_time_ms - int(fill_time_ms or 0)) > 10_000:
            continue

        stop = Decimal(str(raw.get("stopLoss") or 0))
        trailing = Decimal(str(raw.get("trailingStop") or 0))
        if stop > 0 or trailing > 0:
            continue

        actual_entry = Decimal(str(position_row[1] or 0))
        if actual_entry <= 0:
            raise RuntimeError(
                f"recovered position has no actual avg fill symbol={symbol}"
            )

        instruments, _ = api_get(
            "/v5/market/instruments-info",
            {"category": "linear", "symbol": str(symbol)},
        )
        if int(instruments.get("retCode", -1)) != 0:
            raise RuntimeError(
                f"instrument lookup failed during recovery symbol={symbol}"
            )
        instrument = ((instruments.get("result") or {}).get("list") or [{}])[0]
        tick = Decimal(
            str((instrument.get("priceFilter") or {}).get("tickSize") or 0)
        )
        if tick <= 0:
            raise RuntimeError(
                f"recovered position has no tick size symbol={symbol}"
            )

        entry_payload = (
            raw_entry_payload
            if isinstance(raw_entry_payload, dict)
            else json.loads(str(raw_entry_payload))
        )
        contract = initial_protection_contract(entry_payload)
        actual_stop, _unused_target = calculate_initial_boundaries(
            entry=actual_entry,
            side=str(position_row[0]),
            tick=tick,
            stop_loss_pct=contract["stop_loss_pct"],
            take_profit_pct=(
                contract["take_profit_pct"]
                if contract["take_profit_pct"] is not None
                else Decimal("1")
            ),
        )
        protection_request: dict[str, object] = {
            "category": "linear",
            "symbol": str(symbol),
            "positionIdx": int(raw.get("positionIdx") or 0),
            "tpslMode": str(contract["tpsl_mode"]),
            "stopLoss": str(actual_stop),
            "slTriggerBy": str(contract["trigger_by"]),
            "slOrderType": "Market",
        }
        if contract["take_profit_enabled"]:
            if contract["take_profit_pct"] is not None:
                _stop, actual_target = calculate_initial_boundaries(
                    entry=actual_entry,
                    side=str(position_row[0]),
                    tick=tick,
                    stop_loss_pct=contract["stop_loss_pct"],
                    take_profit_pct=contract["take_profit_pct"],
                )
            else:
                actual_target = quantize(
                    contract["take_profit_price"],
                    tick,
                    upward=str(position_row[0]) == "Buy",
                )
            protection_request.update(
                {
                    "takeProfit": str(actual_target),
                    "tpTriggerBy": str(contract["trigger_by"]),
                    "tpOrderType": "Market",
                }
            )
        api_post(
            "/v5/position/trading-stop",
            protection_request,
            key,
            secret,
        )
        reconcile(
            connection,
            key,
            secret,
            "restart_recovered_entry_protection",
        )

        verified = connection.execute(
            """SELECT payload_json FROM runtime.hot_positions
               WHERE symbol=%s ORDER BY position_idx LIMIT 1""",
            (symbol,),
        ).fetchone()
        if verified is not None:
            verified_raw = (
                verified[0]
                if isinstance(verified[0], dict)
                else json.loads(verified[0])
            )
            verified_stop = Decimal(str(verified_raw.get("stopLoss") or 0))
            verified_trailing = Decimal(
                str(verified_raw.get("trailingStop") or 0)
            )
            if verified_stop <= 0 and verified_trailing <= 0:
                raise RuntimeError(
                    "recovered bot position remains unprotected "
                    f"symbol={symbol} entry_command_id={command_id}"
                )
        protected += 1

    return protected


def resolve_prestart_entry_commands(connection: psycopg.Connection) -> None:
    rows = connection.execute(
        """SELECT command_id,state FROM runtime.trade_commands
           WHERE command_type='entry' AND state IN ('queued','running')
             AND requested_at_epoch_ms < %s""",
        (PROCESS_STARTED_AT_MS,),
    ).fetchall()
    now_ms = int(time.time() * 1000)
    for command_id, _state in rows:
        execution = connection.execute(
            """SELECT 1 FROM runtime.executions
               WHERE order_link_id=%s LIMIT 1""",
            (command_id,),
        ).fetchone()
        if execution:
            connection.execute(
                """UPDATE runtime.trade_commands
                   SET state='completed',finished_at_epoch_ms=%s,
                       error='recovered after restart from exact execution evidence'
                   WHERE command_id=%s""",
                (now_ms, command_id),
            )
        else:
            connection.execute(
                """UPDATE runtime.trade_commands
                   SET state='failed',finished_at_epoch_ms=%s,
                       error='RESTART_DISARMED: no exact execution evidence'
                   WHERE command_id=%s""",
                (now_ms, command_id),
            )
    connection.commit()


def resolve_prestart_non_entry_running_commands(
    connection: psycopg.Connection,
) -> None:
    """Never replay an uncertain non-Entry mutation after process restart."""
    connection.execute(
        """UPDATE runtime.trade_commands
           SET state='failed',finished_at_epoch_ms=%s,
               error='RESTART_DISARMED: mutation not replayed; exchange truth reconciled'
           WHERE command_type<>'entry' AND state='running'
             AND requested_at_epoch_ms < %s""",
        (int(time.time() * 1000), PROCESS_STARTED_AT_MS),
    )
    connection.commit()


def startup_live_safety(
    connection: psycopg.Connection,
    key: str,
    secret: str,
) -> None:
    """Synchronously fail-close Entry and restore exchange truth before workers."""
    disarm_new_entries(connection, "restart: owner re-arm required")
    reconcile(connection, key, secret, "startup_preflight")
    refresh_recent_executions(connection, key, secret)
    cancel_bot_owned_pending_entry_orders(connection, key, secret)
    refresh_recent_executions(connection, key, secret)
    protect_recovered_bot_positions(connection, key, secret)
    resolve_prestart_entry_commands(connection)
    resolve_prestart_non_entry_running_commands(connection)
    reconcile(connection, key, secret, "startup_post_cancel")

def record_entry_decision(
    connection: psycopg.Connection,
    signal_id: object,
    symbol: object,
    direction: object,
    signal_at_ms: object,
    decision: str,
    reason: str,
    **details: object,
) -> None:
    current_settings = connection.execute(
        "SELECT entry_policy,updated_at_epoch_ms FROM runtime.trade_settings WHERE singleton=1"
    ).fetchone()
    configured_policy = str(current_settings[0] if current_settings else "base_entry_v1")
    policy = str(details.pop("entry_policy", "base_entry_v1"))
    policy_version = str(details.pop("policy_version", "entry-policy-v1"))
    settings_version = str(
        details.pop("settings_version", current_settings[1] if current_settings else "unknown")
    )
    details["configured_shadow_policy"] = configured_policy
    details["mayak_live_influence"] = False
    details["dispatcher_trading_effect"] = "NONE"
    mayak_snapshot_id = details.pop("mayak_snapshot_id", None)
    mayak_snapshot_time = details.pop("mayak_snapshot_time", None)
    decided_at = int(time.time()*1000)
    event_type = "TERMINAL_STRATEGY_DECISION"
    existing = connection.execute(
        "SELECT 1 FROM runtime.entry_decisions WHERE signal_id=%s", (str(signal_id),)
    ).fetchone()
    if existing:
        event_type = "DUPLICATE_RUNTIME_OBSERVATION"
    connection.execute(
        """INSERT INTO runtime.entry_decision_events(
            signal_id,symbol,direction,signal_at_epoch_ms,observed_at_epoch_ms,
            event_type,decision,reason,details_json,entry_policy,policy_version,settings_version)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (str(signal_id), str(symbol), str(direction), int(signal_at_ms), decided_at,
         event_type, decision, reason, json.dumps(details, ensure_ascii=False),
         policy, policy_version, settings_version),
    )
    connection.execute(
        """INSERT INTO runtime.entry_decisions(
               signal_id,symbol,direction,signal_at_epoch_ms,decided_at_epoch_ms,
               decision,reason,details_json,entry_policy,policy_version,settings_version,
               mayak_snapshot_id,mayak_snapshot_time)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(signal_id) DO NOTHING""",
        (
            str(signal_id), str(symbol), str(direction), int(signal_at_ms),
            decided_at, decision, reason,
            json.dumps(details, ensure_ascii=False),
            policy, policy_version, settings_version, mayak_snapshot_id, mayak_snapshot_time,
        ),
    )


def atomic_status(channel: str, value: dict[str, object]) -> None:
    with status_lock:
        status[channel] = value
        status["updated_at_epoch"] = int(time.time())
        temporary = STATUS.with_suffix(".tmp")
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(STATUS)


def observe_m3_entry_context(
    connection: psycopg.Connection,
    *,
    signal_id: str,
    symbol: str,
    direction: str,
    signal_at_ms: int,
) -> dict[str, object]:
    profile_id = "M3_V1_LONG_ENTRY" if direction == "long" else "M3_V1_SHORT_ENTRY"
    signal_at = datetime.fromtimestamp(signal_at_ms / 1000, UTC)
    row = connection.execute(
        """SELECT assessment_id,mayak_snapshot_id,observed_at,profile_version,
                  status,data_quality,payload,market_context_id
           FROM strategy_dispatcher.assessments
           WHERE profile_id=%s AND profile_version='1.0.0-owner-live'
             AND observed_at<=%s
           ORDER BY observed_at DESC,stored_at DESC LIMIT 1""",
        (profile_id, signal_at),
    ).fetchone()
    if row is None:
        status = "NO_CONTEXT"
        reason = "Ğ”Ğ¾ ÑĞ¸Ğ³Ğ½Ğ°Ğ»Ğ° Ğ½ĞµÑ‚ Ğ¿Ñ€Ğ¸Ñ‡Ğ¸Ğ½Ğ½Ğ¾ Ğ´Ğ¾Ğ¿ÑƒÑÑ‚Ğ¸Ğ¼Ğ¾Ğ¹ Ğ¾Ñ†ĞµĞ½ĞºĞ¸ Ğ”Ğ¸ÑĞ¿ĞµÑ‚Ñ‡ĞµÑ€Ğ°; Ğ²Ñ…Ğ¾Ğ´ Ğ½Ğµ Ğ±Ğ»Ğ¾ĞºĞ¸Ñ€ÑƒĞµÑ‚ÑÑ"
        assessment_id = mayak_id = observed_at = version = quality = market_context_id = None
        age_seconds = None
        freshness = "MISSING"
        payload: object = {}
    else:
        assessment_id, mayak_id, observed_at, version, status, quality, payload, market_context_id = row
        age_seconds = (signal_at - observed_at).total_seconds()
        freshness = "FRESH" if 0 <= age_seconds <= 90 else "STALE"
        reason = "ĞŸÑ€Ğ¸Ñ‡Ğ¸Ğ½Ğ½Ğ°Ñ Ğ¾Ñ†ĞµĞ½ĞºĞ° Ğ”Ğ¸ÑĞ¿ĞµÑ‚Ñ‡ĞµÑ€Ğ° ÑĞ¾Ñ…Ñ€Ğ°Ğ½ĞµĞ½Ğ° Ñ‚Ğ¾Ğ»ÑŒĞºĞ¾ ĞºĞ°Ğº ĞºĞ¾Ğ½Ñ‚ĞµĞºÑÑ‚"
    document = {
        "signal_id": signal_id,
        "assessment_id": assessment_id,
        "mayak_snapshot_id": mayak_id,
        "market_context_id": market_context_id,
        "assessment_observed_at": None if observed_at is None else observed_at.isoformat(),
        "profile_id": profile_id,
        "profile_version": version,
        "dispatcher_status": status,
        "data_quality": quality,
        "age_seconds": age_seconds,
        "freshness": freshness,
        "decision": "OBSERVED",
        "context_type": "OBSERVED_CONTEXT",
        "trading_effect": "NONE",
        "reason_ru": reason,
        "assessment": payload,
    }
    connection.execute(
        """INSERT INTO runtime.m3_consumed_context(
            signal_id,symbol,direction,signal_at,assessment_id,mayak_snapshot_id,
            assessment_observed_at,profile_id,profile_version,dispatcher_status,
            decision,reason_ru,context_type,trading_effect,payload,market_context_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                   'OBSERVED_CONTEXT','NONE',%s,%s)
            ON CONFLICT(signal_id) DO NOTHING""",
        (signal_id, symbol, direction, signal_at, assessment_id, mayak_id,
         observed_at, profile_id, version, status, document["decision"], reason,
         json.dumps(document, ensure_ascii=False, default=str), market_context_id),
    )
    return document


def connection_event(connection: psycopg.Connection, channel: str, event: str, **details: object) -> None:
    connection.execute("INSERT INTO runtime.connection_events(at_epoch_ms,channel,event,details_json) VALUES(%s,%s,%s,%s)",
                       (int(time.time() * 1000), channel, event, json.dumps(details, ensure_ascii=False)))
    connection.commit()


def auth(ws: websocket.WebSocket, key: str, secret: str) -> None:
    expires = int(time.time() * 1000) + 10_000
    signature = hmac.new(secret.encode(), f"GET/realtime{expires}".encode(), hashlib.sha256).hexdigest()
    ws.send(json.dumps({"op": "auth", "args": [key, expires, signature]}, separators=(",", ":")))
    response = json.loads(ws.recv())
    success = response.get("success") is True or response.get("retCode") in (0, 20001)
    if not success:
        raise RuntimeError(f"websocket auth failed: {response.get('retMsg') or response.get('ret_msg')}")


def _server_time_ms(payload: dict[str, object]) -> float:
    raw = payload.get("time")
    if raw not in (None, ""):
        return float(raw)
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise RuntimeError("Bybit time response has no result object")
    nano = result.get("timeNano")
    if nano:
        return float(nano) / 1_000_000
    second = result.get("timeSecond")
    if second:
        return float(second) * 1000
    raise RuntimeError("Bybit time response has no server timestamp")


def mutation_clock_offset_ms() -> float:
    """Return a fresh midpoint clock observation for this mutation attempt."""
    started_ns = time.time_ns()
    payload, _ = api_get("/v5/market/time", {})
    finished_ns = time.time_ns()
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(
            f"Bybit clock probe rejected: {payload.get('retMsg')}"
        )
    midpoint_ms = (started_ns + finished_ns) / 2_000_000
    return _server_time_ms(payload) - midpoint_ms


def assert_mutation_clock_safe() -> float:
    offset_ms = mutation_clock_offset_ms()
    if abs(offset_ms) > MUTATION_CLOCK_MAX_ABS_OFFSET_MS:
        raise UnsafeBybitClock(
            "UNSAFE_BYBIT_CLOCK_OFFSET "
            f"offset_ms={offset_ms:.1f} "
            f"limit_ms={MUTATION_CLOCK_MAX_ABS_OFFSET_MS:.1f}"
        )
    return offset_ms


def _explicit_http_payload(
    exc: urllib.error.HTTPError,
) -> dict[str, object] | None:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        value = json.loads(raw)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and "retCode" in value else None


def _validate_post_payload(
    path: str,
    payload: dict[str, object],
    accepted_codes: tuple[int, ...],
) -> dict[str, object]:
    code = int(payload.get("retCode", -1))
    if code == 0 or code in accepted_codes:
        return payload
    raise BybitMutationRejected(
        f"Bybit POST rejected path={path} retCode={code} "
        f"retMsg={payload.get('retMsg')}"
    )


def api_post(
    path: str,
    params: dict[str, object],
    key: str,
    secret: str,
    *,
    accepted_codes: tuple[int, ...] = (),
) -> dict[str, object]:
    """One signed mutation attempt. Never retry a POST with uncertain outcome."""
    assert_mutation_clock_safe()
    body = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
    timestamp = str(int(time.time() * 1000))
    signature = hmac.new(
        secret.encode(),
        f"{timestamp}{key}{SIGNED_RECV_WINDOW}{body}".encode(),
        hashlib.sha256,
    ).hexdigest()
    request = urllib.request.Request(
        REST_URL + path,
        data=body.encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-BAPI-API-KEY": key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": SIGNED_RECV_WINDOW,
            "X-BAPI-SIGN": signature,
            "User-Agent": "cripta-live-executor/1",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=SIGNED_MUTATION_TIMEOUT_SECONDS,
        ) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        explicit = _explicit_http_payload(exc)
        if explicit is not None:
            return _validate_post_payload(path, explicit, accepted_codes)
        raise AmbiguousBybitMutation(
            f"HTTP response without deterministic Bybit payload path={path} "
            f"status={exc.code}"
        ) from exc
    except (TimeoutError, urllib.error.URLError, OSError, ValueError) as exc:
        raise AmbiguousBybitMutation(
            f"transport/response uncertainty path={path} "
            f"error={type(exc).__name__}:{exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise AmbiguousBybitMutation(
            f"non-object Bybit POST response path={path}"
        )
    return _validate_post_payload(path, payload, accepted_codes)


def handle_exchange_mutation_barrier(
    connection: psycopg.Connection,
    key: str,
    secret: str,
    command_id: str | None,
    exc: BaseException,
) -> None:
    """Fail closed and force startup reconciliation before any later mutation."""
    error = f"EXCHANGE_MUTATION_BARRIER:{type(exc).__name__}:{exc}"
    recovery_errors: list[str] = []

    try:
        connection.rollback()
        if command_id:
            finalize_failed_entry_command_reservation(
                connection,
                command_id=command_id,
                reason=error,
                mutation_ambiguous=True,
            )
            mark_ambiguous_exit_command(
                connection,
                command_id=command_id,
                reason=error,
            )
            connection.execute(
                """UPDATE runtime.trade_commands
                   SET error=%s
                   WHERE command_id=%s AND state='running'""",
                (error, command_id),
            )
            connection.commit()
    except Exception as recovery_exc:
        recovery_errors.append(
            f"persist={type(recovery_exc).__name__}:{recovery_exc}"
        )

    try:
        disarm_new_entries(connection, error[:500])
    except Exception as recovery_exc:
        try:
            connection.rollback()
        except Exception:
            pass
        recovery_errors.append(
            f"disarm={type(recovery_exc).__name__}:{recovery_exc}"
        )

    try:
        refresh_recent_executions(connection, key, secret)
    except Exception as recovery_exc:
        try:
            connection.rollback()
        except Exception:
            pass
        recovery_errors.append(
            f"executions={type(recovery_exc).__name__}:{recovery_exc}"
        )

    try:
        reconcile(connection, key, secret, "ambiguous_exchange_mutation")
    except Exception as recovery_exc:
        try:
            connection.rollback()
        except Exception:
            pass
        recovery_errors.append(
            f"reconcile={type(recovery_exc).__name__}:{recovery_exc}"
        )

    try:
        atomic_status(
            "command",
            {
                "state": "blocked",
                "error": error,
                "recovery_errors": recovery_errors,
                "restart_required": True,
            },
        )
    finally:
        os._exit(75)

def quantize(value: Decimal, step: Decimal, upward: bool = False) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING if upward else ROUND_FLOOR) * step


def remaining_entry_fee(connection: psycopg.Connection, symbol: str, side: str) -> Decimal:
    qty = Decimal("0")
    fee = Decimal("0")
    rows = connection.execute(
        "SELECT side,exec_qty,exec_fee,payload_json FROM runtime.executions WHERE symbol=%s ORDER BY exec_time_ms,exec_id",
        (symbol,),
    ).fetchall()
    for execution_side, raw_qty, raw_fee, raw_payload in rows:
        execution_qty = Decimal(str(raw_qty))
        execution_fee = Decimal(str(raw_fee))
        closed = Decimal(str(json.loads(raw_payload).get("closedSize") or 0))
        if str(execution_side) == side and closed <= 0:
            qty += execution_qty
            fee += execution_fee
        elif str(execution_side) != side and closed > 0 and qty > 0:
            allocated = min(execution_qty, qty)
            allocated_fee = fee * allocated / qty
            qty -= allocated
            fee = max(Decimal("0"), fee - allocated_fee)
    return fee


def protection_plan(
    connection: psycopg.Connection,
    symbol: str,
    position: dict[str, object],
    tick: Decimal,
) -> dict[str, Decimal]:
    side = str(position["side"])
    entry = Decimal(str(position.get("avgPrice") or 0))
    qty = Decimal(str(position.get("size") or 0))
    if entry <= 0 or qty <= 0:
        raise RuntimeError("position has no valid entry or size")
    entry_fee = remaining_entry_fee(connection, symbol, side)
    return calculate_protection_plan(
        entry=entry, qty=qty, entry_fee=entry_fee, side=side, tick=tick,
        slippage_pct=observed_adverse_slippage(connection, symbol),
    )


def account_available_usdt(account: dict[str, object]) -> Decimal:
    direct = str(account.get("totalAvailableBalance") or "")
    if direct:
        return Decimal(direct)
    usdt = next((coin for coin in account.get("coin", []) if coin.get("coin") == "USDT"), {})
    wallet = Decimal(str(usdt.get("walletBalance") or 0))
    reserved = sum(Decimal(str(usdt.get(name) or 0)) for name in ("totalOrderIM", "totalPositionIM", "locked"))
    return max(Decimal("0"), wallet - reserved)


def initial_protection_contract(payload: dict[str, object]) -> dict[str, object]:
    raw = payload.get("initial_protection")
    if not isinstance(raw, dict):
        raise RuntimeError("ENTRY_INITIAL_PROTECTION_CONTRACT_MISSING")
    stop_enabled = bool(raw.get("stop_loss_enabled", True))
    target_enabled = bool(raw.get("take_profit_enabled", True))
    stop_loss_pct = Decimal(str(raw.get("stop_loss_pct") or 0))
    take_profit_pct_raw = raw.get("take_profit_pct")
    take_profit_price_raw = raw.get("take_profit_price")
    if not stop_enabled or stop_loss_pct <= 0:
        raise RuntimeError("ENTRY_INITIAL_PROTECTION_PERCENT_INVALID")
    if target_enabled:
        if (take_profit_pct_raw in (None, "")) == (take_profit_price_raw in (None, "")):
            raise RuntimeError("ENTRY_INITIAL_PROTECTION_TARGET_AMBIGUOUS")
    elif take_profit_pct_raw not in (None, "") or take_profit_price_raw not in (None, ""):
        raise RuntimeError("ENTRY_INITIAL_PROTECTION_DISABLED_TARGET_PRESENT")
    take_profit_pct = (
        None
        if take_profit_pct_raw in (None, "")
        else Decimal(str(take_profit_pct_raw))
    )
    take_profit_price = (
        None
        if take_profit_price_raw in (None, "")
        else Decimal(str(take_profit_price_raw))
    )
    trigger_by = str(raw.get("trigger_by") or "")
    tpsl_mode = str(raw.get("tpsl_mode") or "")
    if take_profit_pct is not None and take_profit_pct <= 0:
        raise RuntimeError("ENTRY_INITIAL_PROTECTION_PERCENT_INVALID")
    if take_profit_price is not None and take_profit_price <= 0:
        raise RuntimeError("ENTRY_INITIAL_PROTECTION_PRICE_INVALID")
    if trigger_by != "LastPrice":
        raise RuntimeError("ENTRY_INITIAL_PROTECTION_TRIGGER_UNSUPPORTED")
    if tpsl_mode != "Full":
        raise RuntimeError("ENTRY_INITIAL_PROTECTION_MODE_UNSUPPORTED")
    return {
        "stop_loss_enabled": stop_enabled,
        "take_profit_enabled": target_enabled,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "take_profit_price": take_profit_price,
        "trigger_by": trigger_by,
        "tpsl_mode": tpsl_mode,
    }


def record_protection_or_owner_event(
    connection: psycopg.Connection,
    command_id: str,
    kind: str,
    symbol: str,
    command_payload: dict[str, object],
    before: dict[str, object] | None,
) -> None:
    if kind not in {"initial_protection", "break_even", "current_stop", "trailing_stop", "close"}:
        return
    entry_command_id = str(command_payload.get("entry_command_id") or "")
    ownership = connection.execute(
        """SELECT position_id,trade_id FROM runtime.position_ownership
           WHERE (%s<>'' AND entry_command_id=%s)
              OR (%s='' AND symbol=%s AND state='OPEN')
           ORDER BY fill_at DESC LIMIT 1""",
        (entry_command_id, entry_command_id, entry_command_id, symbol),
    ).fetchone()
    if ownership is None:
        return
    after_row = connection.execute(
        "SELECT payload_json FROM runtime.hot_positions WHERE symbol=%s ORDER BY position_idx LIMIT 1",
        (symbol,),
    ).fetchone()
    after = json.loads(after_row[0]) if after_row else {}
    before = before or {}
    order_rows = connection.execute(
        """SELECT order_id FROM runtime.hot_orders
           WHERE symbol=%s AND payload_json::jsonb->>'reduceOnly'='true'
           ORDER BY order_id""",
        (symbol,),
    ).fetchall()
    exchange_order_ids = sorted(str(value[0]) for value in order_rows)
    initiator = "OWNER" if command_id.startswith("web-") else "ALGORITHM"
    protection_kind = {
        "initial_protection": "INITIAL_HARD_STOP",
        "break_even": "PROFIT_PROTECTION_STOP",
        "current_stop": "OWNER_MODIFIED_STOP" if initiator == "OWNER" else "UNKNOWN",
        "trailing_stop": "TRAILING_STOP",
    }.get(kind)
    if protection_kind is not None:
        event_id = "PRT-" + hashlib.sha256(
            f"{ownership[0]}|{command_id}".encode()
        ).hexdigest()[:32]
        connection.execute(
            """INSERT INTO runtime.protection_events(
                protection_event_id,position_id,trade_id,command_id,protection_kind,
                initiator,stop_before,stop_after,take_profit_before,take_profit_after,
                trailing_before,trailing_after,trailing_distance,exchange_order_ids,
                source_payload,provenance)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(protection_event_id) DO NOTHING""",
            (
                event_id, ownership[0], ownership[1], command_id, protection_kind,
                initiator, number(before.get("stopLoss")) or None,
                number(after.get("stopLoss")) or None,
                number(before.get("takeProfit")) or None,
                number(after.get("takeProfit")) or None,
                number(before.get("trailingStop")) or None,
                number(after.get("trailingStop")) or None,
                number(command_payload.get("distance_pct")) or None,
                json.dumps(exchange_order_ids),
                json.dumps({"before": before, "after": after,
                            "command_payload": command_payload}, ensure_ascii=False),
                json.dumps({"source": "runtime_command_and_bybit_reconciliation",
                            "command_id": command_id}),
            ),
        )
    if initiator == "OWNER":
        action = {
            "initial_protection": "MANUAL_STOP_CHANGE",
            "break_even": "MANUAL_STOP_CHANGE",
            "current_stop": "MANUAL_STOP_CHANGE",
            "trailing_stop": "MANUAL_TRAILING_CHANGE",
            "close": "MANUAL_CLOSE",
        }[kind]
        intervention_id = "OMI-" + hashlib.sha256(
            f"{ownership[0]}|{command_id}".encode()
        ).hexdigest()[:32]
        connection.execute(
            """INSERT INTO runtime.owner_manual_interventions(
                intervention_id,position_id,trade_id,action,command_id,
                exchange_order_ids,before_state,after_state,provenance)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(intervention_id) DO NOTHING""",
            (
                intervention_id, ownership[0], ownership[1], action, command_id,
                json.dumps(exchange_order_ids), json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                json.dumps({"source": "owner_dashboard_command"}),
            ),
        )


def _step_aligned(value: Decimal, step: Decimal, label: str) -> Decimal:
    if value <= 0 or step <= 0:
        raise RuntimeError(f"{label} must be positive")
    units = value / step
    if units != units.to_integral_value():
        raise RuntimeError(f"{label} is not aligned to exchange step {step}")
    return value


def _universal_exit_position(
    connection: psycopg.Connection,
    *,
    payload: dict[str, object],
    symbol: str,
    positions_payload: dict[str, object],
) -> dict[str, object]:
    if str(payload.get("source") or "") != "universal_exit":
        raise RuntimeError("STRATEGY_EXIT_SOURCE_INVALID")
    position_id = str(payload.get("strategy_position_id") or "")
    if not position_id:
        raise RuntimeError("STRATEGY_EXIT_POSITION_ID_MISSING")
    expected_idx = int(str(payload.get("position_idx")))
    expected_direction = str(payload.get("direction") or "")
    expected_side = (
        "Buy"
        if expected_direction == "LONG"
        else "Sell" if expected_direction == "SHORT" else ""
    )
    if not expected_side:
        raise RuntimeError("STRATEGY_EXIT_DIRECTION_INVALID")
    expiry_raw = str(payload.get("expires_at") or "")
    try:
        expires_at = datetime.fromisoformat(expiry_raw)
    except ValueError as exc:
        raise RuntimeError("STRATEGY_EXIT_EXPIRY_INVALID") from exc
    if expires_at.tzinfo is None or datetime.now(UTC) >= expires_at.astimezone(UTC):
        raise RuntimeError("STRATEGY_EXIT_REQUEST_EXPIRED")

    owner = connection.execute(
        """SELECT position_id,account_ref,exchange_position_key,position_idx,
                  strategy_id,strategy_version,strategy_config_fingerprint,
                  exit_plan_fingerprint,symbol,side,state
             FROM runtime.position_ownership
            WHERE position_id=%s""",
        (position_id,),
    ).fetchone()
    if owner is None:
        raise RuntimeError("STRATEGY_EXIT_POSITION_OWNER_MISSING")
    expected_owner = (
        position_id,
        str(payload.get("account_ref") or ""),
        str(payload.get("exchange_position_key") or ""),
        expected_idx,
        str(payload.get("strategy_id") or ""),
        str(payload.get("strategy_version") or ""),
        str(payload.get("strategy_config_fingerprint") or ""),
        str(payload.get("exit_plan_fingerprint") or ""),
        symbol,
        expected_side,
        "OPEN",
    )
    actual_owner = tuple(owner)
    if actual_owner != expected_owner:
        raise RuntimeError("STRATEGY_EXIT_POSITION_OWNER_MISMATCH")
    if expected_owner[1] != "BYBIT:UNIFIED":
        raise RuntimeError("STRATEGY_EXIT_ACCOUNT_UNSUPPORTED")
    expected_key = f"BYBIT:UNIFIED:LINEAR:USDT:{symbol}:{expected_idx}"
    if expected_owner[2] != expected_key:
        raise RuntimeError("STRATEGY_EXIT_EXCHANGE_POSITION_KEY_MISMATCH")

    exchange_position = next(
        (
            item
            for item in ((positions_payload.get("result") or {}).get("list") or [])
            if int(item.get("positionIdx") or 0) == expected_idx
            and str(item.get("side") or "") == expected_side
            and Decimal(str(item.get("size") or 0)) > 0
        ),
        None,
    )
    if exchange_position is None:
        raise RuntimeError("STRATEGY_EXIT_EXCHANGE_POSITION_NOT_FOUND")
    return exchange_position


def assert_legacy_automated_exit_ownership(
    connection: psycopg.Connection,
    *,
    command_id: str,
    kind: str,
    symbol: str,
    payload: dict[str, object],
) -> None:
    if kind not in {"break_even", "trailing_stop"}:
        return
    if command_id.startswith("web-"):
        return
    source = str(payload.get("source") or "")
    if source and not source.startswith("exit_runtime_v36"):
        raise ExchangeMutationBarrier(
            f"LEGACY_EXIT_SOURCE_UNSUPPORTED:{source}"
        )
    entry_command_id = str(payload.get("entry_command_id") or "")
    if entry_command_id:
        rows = connection.execute(
            """SELECT position_id,bot_instance_id FROM runtime.position_ownership
               WHERE entry_command_id=%s
                 AND state IN ('OPEN','RECONCILIATION_REQUIRED')
               ORDER BY fill_at DESC""",
            (entry_command_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            """SELECT position_id,bot_instance_id FROM runtime.position_ownership
               WHERE symbol=%s
                 AND state IN ('OPEN','RECONCILIATION_REQUIRED')
               ORDER BY fill_at DESC""",
            (symbol,),
        ).fetchall()
    if not rows:
        raise ExchangeMutationBarrier(
            "LEGACY_EXIT_OWNERSHIP_UNKNOWN:" + (entry_command_id or symbol)
        )
    for position_id, bot_instance_id in rows:
        if str(bot_instance_id or "") == "universal-entry":
            raise ExchangeMutationBarrier(
                f"LEGACY_EXIT_OWNERSHIP_CONFLICT:{position_id}"
            )


def _execute_universal_exit_command(
    connection: psycopg.Connection,
    key: str,
    secret: str,
    *,
    command_id: str,
    symbol: str,
    payload: dict[str, object],
    positions_payload: dict[str, object],
    tick: Decimal,
    qty_step: Decimal,
) -> dict[str, object]:
    position = _universal_exit_position(
        connection,
        payload=payload,
        symbol=symbol,
        positions_payload=positions_payload,
    )
    try:
        action_kind = ExitActionKind(str(payload.get("action_kind") or ""))
    except ValueError as exc:
        raise RuntimeError("STRATEGY_EXIT_ACTION_UNSUPPORTED") from exc
    mutation_raw = payload.get("requested_mutation")
    if not isinstance(mutation_raw, dict):
        raise RuntimeError("STRATEGY_EXIT_MUTATION_INVALID")
    mutation = validate_exit_mutation(action_kind, mutation_raw)
    position_idx = int(position.get("positionIdx") or 0)

    if action_kind is ExitActionKind.SET_STOP:
        stop = _step_aligned(Decimal(str(mutation["stop_price"])), tick, "stop_price")
        return api_post(
            "/v5/position/trading-stop",
            {
                "category": "linear",
                "symbol": symbol,
                "positionIdx": position_idx,
                "tpslMode": str(mutation["tpsl_mode"]),
                "stopLoss": str(stop),
                "slTriggerBy": str(mutation["trigger_by"]),
                "slOrderType": str(mutation["order_type"]),
            },
            key,
            secret,
        )

    if action_kind is ExitActionKind.SET_TP:
        target = _step_aligned(
            Decimal(str(mutation["take_profit_price"])), tick, "take_profit_price"
        )
        if str(mutation["order_type"]).upper() == "MARKET":
            return api_post(
                "/v5/position/trading-stop",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "positionIdx": position_idx,
                    "tpslMode": str(mutation["tpsl_mode"]),
                    "takeProfit": str(target),
                    "tpTriggerBy": str(mutation["trigger_by"]),
                    "tpOrderType": "Market",
                },
                key,
                secret,
            )

        if str(mutation["order_type"]).upper() != "LIMIT":
            raise RuntimeError("STRATEGY_EXIT_TP_ORDER_TYPE_UNSUPPORTED")
        side = str(position["side"])
        executable = executable_close_price(symbol, side)
        marketable = (
            side == "Buy" and target <= executable
        ) or (
            side == "Sell" and target >= executable
        )
        current_qty = Decimal(str(position.get("size") or 0))
        qty = _step_aligned(current_qty, qty_step, "dynamic TP quantity")
        if marketable:
            return api_post(
                "/v5/order/create",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "side": "Sell" if side == "Buy" else "Buy",
                    "orderType": "Market",
                    "qty": str(qty),
                    "positionIdx": position_idx,
                    "orderLinkId": command_id[:36],
                    "reduceOnly": True,
                    "closeOnTrigger": False,
                },
                key,
                secret,
            )

        tag = "utp" + hashlib.sha256(
            str(payload["strategy_position_id"]).encode()
        ).hexdigest()[:12] + "-"
        open_orders, _ = api_get(
            "/v5/order/realtime",
            {"category": "linear", "symbol": symbol, "openOnly": "0", "limit": "50"},
            key,
            secret,
        )
        existing = [
            item
            for item in ((open_orders.get("result") or {}).get("list") or [])
            if item.get("orderStatus") in {"New", "PartiallyFilled", "Untriggered"}
            and bool(item.get("reduceOnly"))
            and str(item.get("orderLinkId") or "").startswith(tag)
        ]
        same = next(
            (
                item
                for item in existing
                if Decimal(str(item.get("price") or 0)) == target
                and Decimal(str(item.get("leavesQty") or item.get("qty") or 0)) == qty
            ),
            None,
        )
        if same is not None:
            return {
                "retCode": 0,
                "retMsg": "dynamic TP already resting",
                "result": {
                    "orderId": same.get("orderId"),
                    "orderLinkId": same.get("orderLinkId"),
                },
                "idempotent": True,
            }
        for item in existing:
            api_post(
                "/v5/order/cancel",
                {
                    "category": "linear",
                    "symbol": symbol,
                    "orderId": str(item.get("orderId") or ""),
                },
                key,
                secret,
                accepted_codes=(110001,),
            )
        link_id = (tag + hashlib.sha256(command_id.encode()).hexdigest()[:16])[:36]
        result = api_post(
            "/v5/order/create",
            {
                "category": "linear",
                "symbol": symbol,
                "side": "Sell" if side == "Buy" else "Buy",
                "orderType": "Limit",
                "price": str(target),
                "qty": str(qty),
                "timeInForce": "PostOnly",
                "positionIdx": position_idx,
                "orderLinkId": link_id,
                "reduceOnly": True,
                "closeOnTrigger": False,
            },
            key,
            secret,
        )
        exchange_order_id = str((result.get("result") or {}).get("orderId") or "")
        if not exchange_order_id:
            raise ExchangeMutationBarrier(
                "dynamic maker TP acknowledged without exchange orderId"
            )
        return result

    if action_kind is ExitActionKind.SET_PROTECTION:
        stop = _step_aligned(Decimal(str(mutation["stop_price"])), tick, "stop_price")
        target = _step_aligned(
            Decimal(str(mutation["take_profit_price"])), tick, "take_profit_price"
        )
        return api_post(
            "/v5/position/trading-stop",
            {
                "category": "linear",
                "symbol": symbol,
                "positionIdx": position_idx,
                "tpslMode": str(mutation["tpsl_mode"]),
                "stopLoss": str(stop),
                "takeProfit": str(target),
                "slTriggerBy": str(mutation["sl_trigger_by"]),
                "tpTriggerBy": str(mutation["tp_trigger_by"]),
                "slOrderType": str(mutation["sl_order_type"]),
                "tpOrderType": str(mutation["tp_order_type"]),
            },
            key,
            secret,
        )

    if action_kind is ExitActionKind.SET_TRAILING:
        distance = _step_aligned(
            Decimal(str(mutation["distance"])), tick, "trailing distance"
        )
        params: dict[str, object] = {
            "category": "linear",
            "symbol": symbol,
            "positionIdx": position_idx,
            "tpslMode": str(mutation["tpsl_mode"]),
            "trailingStop": str(distance),
        }
        if "active_price" in mutation:
            active = _step_aligned(
                Decimal(str(mutation["active_price"])), tick, "trailing active_price"
            )
            params["activePrice"] = str(active)
        return api_post("/v5/position/trading-stop", params, key, secret)

    current_qty = Decimal(str(position.get("size") or 0))
    if action_kind is ExitActionKind.REDUCE:
        qty = _step_aligned(Decimal(str(mutation["quantity"])), qty_step, "reduce quantity")
        if qty > current_qty:
            raise RuntimeError("STRATEGY_EXIT_REDUCE_EXCEEDS_POSITION")
    elif action_kind is ExitActionKind.CLOSE:
        qty = _step_aligned(current_qty, qty_step, "close quantity")
    else:
        raise RuntimeError("STRATEGY_EXIT_ACTION_UNSUPPORTED")

    result = api_post(
        "/v5/order/create",
        {
            "category": "linear",
            "symbol": symbol,
            "side": "Sell" if str(position["side"]) == "Buy" else "Buy",
            "orderType": "Market",
            "qty": str(qty),
            "positionIdx": position_idx,
            "orderLinkId": command_id[:36],
            "reduceOnly": True,
            "closeOnTrigger": False,
        },
        key,
        secret,
    )
    exchange_order_id = str((result.get("result") or {}).get("orderId") or "")
    if not exchange_order_id:
        raise ExchangeMutationBarrier(
            "Universal Exit order acknowledged without exchange orderId"
        )
    return result


def execute_command(connection: psycopg.Connection, key: str, secret: str, row: tuple[object, ...]) -> None:
    command_id, kind, symbol, raw_payload = map(str, row)
    payload = json.loads(raw_payload)
    assert_legacy_automated_exit_ownership(
        connection,
        command_id=command_id,
        kind=kind,
        symbol=symbol,
        payload=payload,
    )
    positions, _ = api_get("/v5/position/list", {"category": "linear", "symbol": symbol}, key, secret)
    position = next((p for p in ((positions.get("result") or {}).get("list") or []) if Decimal(str(p.get("size") or 0)) > 0),+Ø§z\m®éÜj×ã­yõ¼­zÁ¥½¹}¥‘Ì°(€€€€€€€€€€€€€€€€€€€€€€€Á½Í¥Ñ¥½¹}¥‘àõÁ½Í¥Ñ¥½¹}¥‘à°(€€€€€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€ÕÉÉ•¹Ñ}ÍÑ½Àõ•¥µ…°¡ÍÑÈ¡É…Ü¹•Ğ ‰ÍÑ½Á1½ÍÌˆ¤½È€À¤¤(€€€€€€€€€€€ÁÉ½™¥Ñ}…±É•…‘å}ÁÉ½Ñ•Ñ•õÕÉÉ•¹Ñ}ÍÑ½À€ø€À…¹€ (€€€€€€€€€€€€€€€€¡Á½Í¥Ñ¥½¹}É½İlÁt€ôô€‰	Õäˆ…¹ÕÉÉ•¹Ñ}ÍÑ½À€øô…ÑÕ…±}•¹ÑÉä¤(€€€€€€€€€€€€€€€½È€¡Á½Í¥Ñ¥½¹}É½İlÁt€ôô€‰M•±°ˆ…¹ÕÉÉ•¹Ñ}ÍÑ½À€ğô…ÑÕ…±}•¹ÑÉä¤(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜ÁÉ½™¥Ñ}…±É•…‘å}ÁÉ½Ñ•Ñ•½È•¥µ…°¡ÍÑÈ¡É…Ü¹•Ğ ‰ÑÉ…¥±¥¹MÑ½Àˆ¤½È€À¤¤€ø€Àè(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€€€€€ÁÉ½Ñ•Ñ¥½¹}­•äõ˜‰í•¹ÑÉå}¥‘ôéíÁ½Í¥Ñ¥½¹}É½İlÅuôéíÁ½Í¥Ñ¥½¹}É½İlÉuôˆ(€€€€€€€€€€€¥¹¥Ñ}¥ô‰…ÕÑ¼µ¥¹¥Ğ´ˆ­¡…Í¡±¥ˆ¹Í¡„ÈÔØ¡ÁÉ½Ñ•Ñ¥½¹}­•ä¹•¹½‘” ¤¤¹¡•á‘¥•ÍĞ ¥lèÈÍt(€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” ˆˆ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹ÑÉ…‘•}½µµ…¹‘Ì¡½µµ…¹‘}¥±½µµ…¹‘}ÑåÁ”±Íåµ‰½°±Á…å±½…‘}©Í½¸±ÍÑ…Ñ”±É•ÅÕ•ÍÑ•‘}…Ñ}•Á½¡}µÌ¤(€€€€€€€€€€€€€€€Y1UL •Ì°¥¹¥Ñ¥…±}ÁÉ½Ñ•Ñ¥½¸œ°•Ì°•Ì°ÅÕ•Õ•œ°•Ì¤=8=91%P¡½µµ…¹‘}¥¤<9=Q!%9ˆˆˆ°(€€€€€€€€€€€€€€€€¡¥¹¥Ñ}¥±Íåµ‰½°±©Í½¸¹‘ÕµÁÌ¡ì‰•¹ÑÉå}½µµ…¹‘}¥ˆé•¹ÑÉå}¥°‰…ÑÕ…±}•¹ÑÉäˆéÁ½Í¥Ñ¥½¹}É½İlÉt°‰…ÑÕ…±}Í¥é”ˆéÁ½Í¥Ñ¥½¹}É½İlÅt°‰¥¹¥Ñ¥…±}ÁÉ½Ñ•Ñ¥½¸ˆé¥¹¥Ñ¥…±}ÁÉ½Ñ•Ñ¥½¹ô¤±¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤¨ÄÀÀÀ¤¤¤(€€€€€€€½¹¹•Ñ¥½¸¹½µµ¥Ğ ¤(€€€€€€€€ŒXÌØè	½ÑÉ…¥±¥¹œ½±½Í”‘•¥Í¥½¹Ì‰•±½¹œÑ¼á¥Ğ¸Q¡¥Ìİ½É­•È½¹±ä•á•ÕÑ•Ì½µµ…¹‘Ì¸(€€€€€€€É½Üõ½¹¹•Ñ¥½¸¹•á•ÕÑ” ˆˆ‰M1P½µµ…¹‘}¥±½µµ…¹‘}ÑåÁ”±Íåµ‰½°±Á…å±½…‘}©Í½¸I=4ÉÕ¹Ñ¥µ”¹ÑÉ…‘•}½µµ…¹‘Ì(€€€€€€€€€€€]!IÍÑ…Ñ”ôÅÕ•Õ•œ=IH	d€¡±•™Ğ¡½µµ…¹‘}¥°Ğ¤ôİ•ˆ´œ¤M°É•ÅÕ•ÍÑ•‘}…Ñ}•Á½¡}µÌ1%5%P€Äˆˆˆ¤¹™•Ñ¡½¹” ¤(€€€€€€€¥˜¹½ĞÉ½Üè(€€€€€€€€€€€€ŒM1PÁ½±±¥¹œÍÑ…ÉÑÌ…¸¥µÁ±¥¥ĞÑÉ…¹Í…Ñ¥½¸ì±½Í”¥Ğ‰•™½É”Í±••À¸(€€€€€€€€€€€½¹¹•Ñ¥½¸¹½µµ¥Ğ ¤(€€€€€€€€€€€Ñ¥µ”¹Í±••À À¸ÈÔ¤(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€½µµ…¹‘}¥õÍÑÈ¡É½İlÁt¤(€€€€€€€¥˜ÍÑÈ¡É½İlÅt¤€ôô€‰•¹ÑÉäˆè(€€€€€€€€€€€É•…‘ä°É•…‘¥¹•ÍÍ}É•…Í½¸€ô•¹ÑÉå}ÉÕ¹Ñ¥µ•}É•…‘¥¹•ÍÌ¡½¹¹•Ñ¥½¸¤(€€€€€€€€€€€¥˜¹½ĞÉ•…‘äè(€€€€€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€€€€€€€€€ˆˆ‰UAQÉÕ¹Ñ¥µ”¹ÑÉ…‘•}½µµ…¹‘Ì(€€€€€€€€€€€€€€€€€€€€€€MPÍÑ…Ñ”ô™…¥±•œ±™¥¹¥Í¡•‘}…Ñ}•Á½¡}µÌô•Ì±•ÉÉ½Èô•Ì(€€€€€€€€€€€€€€€€€€€€€€]!I½µµ…¹‘}¥ô•Ì9ÍÑ…Ñ”ôÅÕ•Õ•œˆˆˆ°(€€€€€€€€€€€€€€€€€€€€¡¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤¨ÄÀÀÀ¤°˜‰9QIe}	1=-éíÉ•…‘¥¹•ÍÍ}É•…Í½¹ôˆ°½µµ…¹‘}¥¤°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€™¥¹…±¥é•}™…¥±•‘}•¹ÑÉå}½µµ…¹‘}É•Í•ÉÙ…Ñ¥½¸ (€€€€€€€€€€€€€€€€€€€½¹¹•Ñ¥½¸°(€€€€€€€€€€€€€€€€€€€½µµ…¹‘}¥õ½µµ…¹‘}¥°(€€€€€€€€€€€€€€€€€€€É•…Í½¸õ˜‰9QIe}	1=-éíÉ•…‘¥¹•ÍÍ}É•…Í½¹ôˆ°(€€€€€€€€€€€€€€€€€€€µÕÑ…Ñ¥½¹}…µ‰¥Õ½ÕÌõ…±Í”°(€€€€€€€€€€€€€€€€¤(€€€€€€€€€€€€€€€½¹¹•Ñ¥½¸¹½µµ¥Ğ ¤(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€ˆˆ‰UAQÉÕ¹Ñ¥µ”¹ÑÉ…‘•}½µµ…¹‘Ì(€€€€€€€€€€€€€€MPÍÑ…Ñ”ôÉÕ¹¹¥¹œœ±ÍÑ…ÉÑ•‘}…Ñ}•Á½¡}µÌô•Ì(€€€€€€€€€€€€€€]!I½µµ…¹‘}¥ô•Ì9ÍÑ…Ñ”ôÅÕ•Õ•œˆˆˆ°(€€€€€€€€€€€€¡¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤€¨€ÄÀÀÀ¤°½µµ…¹‘}¥¤°(€€€€€€€€¤(€€€€€€€½¹¹•Ñ¥½¸¹½µµ¥Ğ ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€•á•ÕÑ•}½µµ…¹¡½¹¹•Ñ¥½¸°­•ä°Í•É•Ğ°É½Ü¤(€€€€€€€•á•ÁĞá¡…¹•5ÕÑ…Ñ¥½¹	…ÉÉ¥•È…Ì•áŒè(€€€€€€€€€€€¡…¹‘±•}•á¡…¹•}µÕÑ…Ñ¥½¹}‰…ÉÉ¥•È (€€€€€€€€€€€€€€€½¹¹•Ñ¥½¸°­•ä°Í•É•Ğ°½µµ…¹‘}¥°•áŒ(€€€€€€€€€€€€¤(€€€€€€€•á•ÁĞá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€½¹¹•Ñ¥½¸¹É½±±‰…¬ ¤(€€€€€€€€€€€É•Í•ÉÙ…Ñ¥½¹}ÍÑ…Ñ”€ô™¥¹…±¥é•}™…¥±•‘}•¹ÑÉå}½µµ…¹‘}É•Í•ÉÙ…Ñ¥½¸ (€€€€€€€€€€€€€€€½¹¹•Ñ¥½¸°(€€€€€€€€€€€€€€€½µµ…¹‘}¥õ½µµ…¹‘}¥°(€€€€€€€€€€€€€€€É•…Í½¸õ˜‰íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ôéí•áôˆ°(€€€€€€€€€€€€€€€µÕÑ…Ñ¥½¹}…µ‰¥Õ½ÕÌõ…±Í”°(€€€€€€€€€€€€¤(€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€€€€€ˆˆ‰UAQÉÕ¹Ñ¥µ”¹ÑÉ…‘•}½µµ…¹‘Ì(€€€€€€€€€€€€€€€€€€MPÍÑ…Ñ”ô™…¥±•œ±™¥¹¥Í¡•‘}…Ñ}•Á½¡}µÌô•Ì±•ÉÉ½Èô•Ì(€€€€€€€€€€€€€€€€€€]!I½µµ…¹‘}¥ô•Ìˆˆˆ°(€€€€€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€€€€¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤€¨€ÄÀÀÀ¤°(€€€€€€€€€€€€€€€€€€€˜‰íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ôèí•áôˆ°(€€€€€€€€€€€€€€€€€€€½µµ…¹‘}¥°(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€¤(€€€€€€€€€€€½¹¹•Ñ¥½¸¹½µµ¥Ğ ¤(€€€€€€€€€€€¥˜É•Í•ÉÙ…Ñ¥½¹}ÍÑ…Ñ”€ôô€‰I=9%1%Q%=9}IEU%Iˆè(€€€€€€€€€€€€€€€¡…¹‘±•}•á¡…¹•}µÕÑ…Ñ¥½¹}‰…ÉÉ¥•È (€€€€€€€€€€€€€€€€€€€½¹¹•Ñ¥½¸°(€€€€€€€€€€€€€€€€€€€­•ä°(€€€€€€€€€€€€€€€€€€€Í•É•Ğ°(€€€€€€€€€€€€€€€€€€€½µµ…¹‘}¥°(€€€€€€€€€€€€€€€€€€€á¡…¹•5ÕÑ…Ñ¥½¹	…ÉÉ¥•È (€€€€€€€€€€€€€€€€€€€€€€€˜‰Á½ÍĞµ…¬¹ÑÉä™…¥±ÕÉ”É•ÅÕ¥É•ÌÉ•½¹¥±¥…Ñ¥½¸èí•áôˆ(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€¤(()‘•˜½µµ…¹‘}±½½À¡­•äèÍÑÈ°Í•É•ĞèÍÑÈ¤€´ø9½¹”è(€€€€ˆˆ‰-••ÀÑ¡”½µµ…¹İ½É­•È…±¥Ù”…¹µ…­”…¸¥¹Ñ•É¹…°™…¥±ÕÉ”Ù¥Í¥‰±”¸ˆˆˆ(€€€İ¡¥±”ÉÕ¹¹¥¹œè(€€€€€€€ÑÉäè(€€€€€€€€€€€…Ñ½µ¥}ÍÑ…ÑÕÌ ‰½µµ…¹ˆ°ì‰ÍÑ…Ñ”ˆè€‰ÉÕ¹¹¥¹œˆ°€‰¡•…ÉÑ‰•…Ñ}•Á½ ˆè¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤¥ô¤(€€€€€€€€€€€½µµ…¹‘}İ½É­•É}±½½À¡­•ä°Í•É•Ğ¤(€€€€€€€•á•ÁĞá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€…Ñ½µ¥}ÍÑ…ÑÕÌ (€€€€€€€€€€€€€€€€‰½µµ…¹ˆ°(€€€€€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…Ñ”ˆè€‰•ÉÉ½Èˆ°(€€€€€€€€€€€€€€€€€€€€‰•ÉÉ½Èˆè˜‰íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ôèí•áôˆ°(€€€€€€€€€€€€€€€€€€€€‰É•ÑÉå}¥¹}Í•½¹‘Ìˆè€È°(€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€¤(€€€€€€€€€€€Ñ¥µ”¹Í±••À È¤(()‘•˜É•½¹¥±•}Á½Í¥Ñ¥½¹}½İ¹•ÉÍ¡¥À (€€€½¹¹•Ñ¥½¸èÁÍå½Áœ¹½¹¹•Ñ¥½¸°(€€€Á½Í¥Ñ¥½¹}±¥ÍĞè±¥ÍÑm‘¥ÑmÍÑÈ°½‰©•Ñut°(€€€¹½Üè¥¹Ğ°(€€€½É‘•É}¡¥ÍÑ½Éäè±¥ÍÑm‘¥ÑmÍÑÈ°½‰©•Ñutğ9½¹”€ô9½¹”°(¤€´ø9½¹”è(€€€€ˆˆ‰I•½¹¥±”‘ÕÉ…‰±”½İ¹•ÉÍ¡¥À™É½´•á…Ğ	å‰¥ĞÁ½Í¥Ñ¥½¸¥¹Ù•¹Ñ½Éä…¹%Ì¸ˆˆˆ(€€€ÕÉÉ•¹Ñ}Á½Í¥Ñ¥½¹Ì€ôì(€€€€€€€€¡ÍÑÈ¡¥Ñ•´¹•Ğ ‰Íåµ‰½°ˆ¤½È€ˆˆ¤°¥¹Ğ¡¥Ñ•´¹•Ğ ‰Á½Í¥Ñ¥½¹%‘àˆ¤½È€À¤¤è¥Ñ•´(€€€€€€€™½È¥Ñ•´¥¸Á½Í¥Ñ¥½¹}±¥ÍĞ(€€€€€€€¥˜•¥µ…°¡ÍÑÈ¡¥Ñ•´¹•Ğ ‰Í¥é”ˆ¤½È€À¤¤€ø€À(€€€ô(€€€É½İÌ€ô½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€ˆˆ‰M1PÁ½Í¥Ñ¥½¹}¥±ÑÉ…‘•}¥±Íåµ‰½°±Í¥‘”±…ÑÕ…±}…Ù}™¥±°±…ÑÕ…±}ÅÑä°(€€€€€€€€€€€€€€€€€•áÑÉ…Ğ¡•Á½ ™É½´™¥±±}…Ğ¤¨ÄÀÀÀ±Á½Í¥Ñ¥½¹}¥‘à±•¹ÑÉå}½µµ…¹‘}¥°(€€€€€€€€€€€€€€€€€•¹ÑÉå}•á•ÕÑ¥½¹}É•ÅÕ•ÍÑ}¥(€€€€€€€€€€I=4ÉÕ¹Ñ¥µ”¹Á½Í¥Ñ¥½¹}½İ¹•ÉÍ¡¥À(€€€€€€€€€€]!IÍÑ…Ñ”ô=A8œ=H±½Í•}±¥¹­}ÍÑ…ÑÕÌôU9IM=1Y}aQ}1%9,œ(€€€€€€€€€€=IH	d™¥±±}…Ğˆˆˆ(€€€€¤¹™•Ñ¡…±° ¤(€€€±…Ñ•ÍÑ}‰å}­•ä€ôì(€€€€€€€€¡ÍÑÈ¡É½İlÉt¤°¥¹Ğ¡É½İlİt½È€À¤¤èÍÑÈ¡É½İlÁt¤™½ÈÉ½Ü¥¸É½İÌ(€€€ô(€€€™½ÈÉ½Ü¥¸É½İÌè(€€€€€€€Á½Í¥Ñ¥½¹}¥°ÑÉ…‘•}¥°Íåµ‰½°°Í¥‘”€ôµ…À¡ÍÑÈ°É½İlèÑt¤(€€€€€€€Á½Í¥Ñ¥½¹}¥‘à€ô¥¹Ğ¡É½İlİt½È€À¤(€€€€€€€ÕÉÉ•¹Ğ€ôÕÉÉ•¹Ñ}Á½Í¥Ñ¥½¹Ì¹•Ğ ¡Íåµ‰½°°Á½Í¥Ñ¥½¹}¥‘à¤¤(€€€€€€€ÕÉÉ•¹Ñ}µ…Ñ¡•Ì€ô€ (€€€€€€€€€€€ÕÉÉ•¹Ğ¥Ì¹½Ğ9½¹”(€€€€€€€€€€€…¹±…Ñ•ÍÑ}‰å}­•ä¹•Ğ ¡Íåµ‰½°°Á½Í¥Ñ¥½¹}¥‘à¤¤€ôôÁ½Í¥Ñ¥½¹}¥(€€€€€€€€€€€…¹ÍÑÈ¡ÕÉÉ•¹Ğ¹•Ğ ‰Í¥‘”ˆ¤½È€ˆˆ¤€ôôÍ¥‘”(€€€€€€€€¤(€€€€€€€¥˜ÕÉÉ•¹Ñ}µ…Ñ¡•Ìè(€€€€€€€€€€€ÕÉÉ•¹Ñ}ÍÑ½À€ô•¥µ…°¡ÍÑÈ¡ÕÉÉ•¹Ğ¹•Ğ ‰ÍÑ½Á1½ÍÌˆ¤½È€À¤¤(€€€€€€€€€€€ÁÉ½Ñ•Ñ¥½¹}½¹™¥Éµ•€ôÕÉÉ•¹Ñ}ÍÑ½À€ø€À(€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€€€€€ˆˆ‰UAQÉÕ¹Ñ¥µ”¹Á½Í¥Ñ¥½¹}½İ¹•ÉÍ¡¥À(€€€€€€€€€€€€€€€€€€MPÍÑ…Ñ”ô=A8œ°(€€€€€€€€€€€€€€€€€€€€€€±½Í•}±¥¹­}ÍÑ…ÑÕÌô=A8œ°(€€€€€€€€€€€€€€€€€€€€€€¥¹¥Ñ¥…±}ÁÉ½Ñ•Ñ¥½¹}½¹™¥Éµ•‘}…Ğô(€€€€€€€€€€€€€€€€€€€€€€€€€€M(€€€€€€€€€€€€€€€€€€€€€€€€€€€€]!8€•Ì9¥¹¥Ñ¥…±}ÁÉ½Ñ•Ñ¥½¹}½¹™¥Éµ•‘}…Ğ%L9U10(€€€€€€€€€€€€€€€€€€€€€€€€€€€€Q!8Ñ½}Ñ¥µ•ÍÑ…µÀ •Ì¼ÄÀÀÀ¸À¤(€€€€€€€€€€€€€€€€€€€€€€€€€€€€1M¥¹¥Ñ¥…±}ÁÉ½Ñ•Ñ¥½¹}½¹™¥Éµ•‘}…Ğ(€€€€€€€€€€€€€€€€€€€€€€€€€€9°(€€€€€€€€€€€€€€€€€€€€€€¥¹¥Ñ¥…±}ÁÉ½Ñ•Ñ¥½¹}•Ù¥‘•¹”ô(€€€€€€€€€€€€€€€€€€€€€€€€€€M(€€€€€€€€€€€€€€€€€€€€€€€€€€€€]!8€•Ì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€Q!8€•Ìèé©Í½¹ˆ(€€€€€€€€€€€€€€€€€€€€€€€€€€€€1M¥¹¥Ñ¥…±}ÁÉ½Ñ•Ñ¥½¹}•Ù¥‘•¹”(€€€€€€€€€€€€€€€€€€€€€€€€€€9(€€€€€€€€€€€€€€€€€€]!IÁ½Í¥Ñ¥½¹}¥ô•Ìˆˆˆ°(€€€€€€€€€€€€€€€€ (€€€€€€€€€€€€€€€€€€€ÁÉ½Ñ•Ñ¥½¹}½¹™¥Éµ•°(€€€€€€€€€€€€€€€€€€€¹½Ü°(€€€€€€€€€€€€€€€€€€€ÁÉ½Ñ•Ñ¥½¹}½¹™¥Éµ•°(€€€€€€€€€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ (€€€€€€€€€€€€€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰	e	%Q}A=M%Q%=9}I=9%1%Q%=8ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Íåµ‰½°ˆèÍåµ‰½°°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Á½Í¥Ñ¥½¹}¥‘àˆèÁ½Í¥Ñ¥½¹}¥‘à°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÍÑ½Á1½ÍÌˆèÕÉÉ•¹Ğ¹•Ğ ‰ÍÑ½Á1½ÍÌˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Ñ…­•AÉ½™¥ĞˆèÕÉÉ•¹Ğ¹•Ğ ‰Ñ…­•AÉ½™¥Ğˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰½‰Í•ÉÙ•‘}…Ñ}•Á½¡}µÌˆè¹½Ü°(€€€€€€€€€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€€€€€€€€€€€€€•¹ÍÕÉ•}…Í¥¤õ…±Í”°(€€€€€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€€€€€€€€Á½Í¥Ñ¥½¹}¥°(€€€€€€€€€€€€€€€€¤°(€€€€€€€€€€€€¤(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€™¥±±}µÌ€ô¥¹Ğ¡É½İlÙt¤(€€€€€€€¹•áÑ}™¥±°€ô½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€ˆˆ‰M1P•áÑÉ…Ğ¡•Á½ ™É½´µ¥¸¡™¥±±}…Ğ¤¤¨ÄÀÀÀ(€€€€€€€€€€€€€€I=4ÉÕ¹Ñ¥µ”¹Á½Í¥Ñ¥½¹}½İ¹•ÉÍ¡¥À(€€€€€€€€€€€€€€]!IÍåµ‰½°ô•Ì9Á½Í¥Ñ¥½¹}¥‘àô•Ì9™¥±±}…ĞùÑ½}Ñ¥µ•ÍÑ…µÀ •Ì¼ÄÀÀÀ¸À¤ˆˆˆ°(€€€€€€€€€€€€¡Íåµ‰½°°Á½Í¥Ñ¥½¹}¥‘à°™¥±±}µÌ¤°(€€€€€€€€¤¹™•Ñ¡½¹” ¥lÁt(€€€€€€€¥¹Ñ•ÉÙ…±}ÍÅ°€ô€ˆˆ‰M1P•á•}¥±½É‘•É}¥±½É‘•É}±¥¹­}¥±Í¥‘”±•á•}ÅÑä±•á•}ÁÉ¥”°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€•á•}™•”±•á•}Ñ¥µ•}µÌ±Á…å±½…‘}©Í½¸(€€€€€€€€€€€€€€€€€€€€€€€€€I=4ÉÕ¹Ñ¥µ”¹•á•ÕÑ¥½¹Ì(€€€€€€€€€€€€€€€€€€€€€€€€€]!IÍåµ‰½°ô•Ì9•á•}Ñ¥µ•}µÌøô•Ìˆˆˆ(€€€€€€€¥¹Ñ•ÉÙ…±}…ÉÌèÑÕÁ±•m½‰©•Ğ°€¸¸¹t€ô€¡Íåµ‰½°°™¥±±}µÌ¤(€€€€€€€¥˜¹•áÑ}™¥±°¥Ì¹½Ğ9½¹”è(€€€€€€€€€€€¥¹Ñ•ÉÙ…±}ÍÅ°€¬ô€ˆ9•á•}Ñ¥µ•}µÌğ•Ìˆ(€€€€€€€€€€€¥¹Ñ•ÉÙ…±}…ÉÌ€¬ô€¡¥¹Ğ¡¹•áÑ}™¥±°¤°¤(€€€€€€€•á•ÕÑ¥½¹}É½İÌ€ô½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€¥¹Ñ•ÉÙ…±}ÍÅ°€¬€ˆ=IH	d•á•}Ñ¥µ•}µÌ±•á•}¥ˆ°¥¹Ñ•ÉÙ…±}…ÉÌ(€€€€€€€€¤¹™•Ñ¡…±° ¤(€€€€€€€•á•ÕÑ¥½¹Ì€ôl(€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€‰•á•}¥ˆèÙ…±Õ•lÁt°€‰½É‘•É}¥ˆèÙ…±Õ•lÅt°€‰½É‘•É}±¥¹­}¥ˆèÙ…±Õ•lÉt°(€€€€€€€€€€€€€€€€‰Í¥‘”ˆèÙ…±Õ•lÍt°€‰•á•}ÅÑäˆèÙ…±Õ•lÑt°€‰•á•}ÁÉ¥”ˆèÙ…±Õ•lÕt°(€€€€€€€€€€€€€€€€‰•á•}™•”ˆèÙ…±Õ•lÙt°€‰•á•}Ñ¥µ•}µÌˆèÙ…±Õ•lİt°€‰Á…å±½…‘}©Í½¸ˆèÙ…±Õ•lát°(€€€€€€€€€€€ô(€€€€€€€€€€€™½ÈÙ…±Õ”¥¸•á•ÕÑ¥½¹}É½İÌ(€€€€€€€t(€€€€€€€±½Í”€ôÉ•Í½±Ù•}•á¡…¹•}Á½Í¥Ñ¥½¹}±½Í” (€€€€€€€€€€€Í¥‘”õÍ¥‘”°(€€€€€€€€€€€…ÑÕ…±}…Ù}™¥±°õ•¥µ…°¡ÍÑÈ¡É½İlÑt¤¤°(€€€€€€€€€€€…ÑÕ…±}ÅÑäõ•¥µ…°¡ÍÑÈ¡É½İlÕt¤¤°(€€€€€€€€€€€•á•ÕÑ¥½¹Ìõ•á•ÕÑ¥½¹Ì°(€€€€€€€€¤(€€€€€€€¥˜±½Í”¹ÍÑ…ÑÕÌ€„ô€‰aPˆ½È±½Í”¹•á¥Ñ}½É‘•É}¥¥Ì9½¹”è(€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€€€€€ˆˆ‰UAQÉÕ¹Ñ¥µ”¹Á½Í¥Ñ¥½¹}½İ¹•ÉÍ¡¥À(€€€€€€€€€€€€€€€€€€MPÍÑ…Ñ”ô1=Mœ°(€€€€€€€€€€€€€€€€€€€€€€±½Í•}±¥¹­}ÍÑ…ÑÕÌôU9IM=1Y}aQ}1%9,œ(€€€€€€€€€€€€€€€€€€]!IÁ½Í¥Ñ¥½¹}¥ô•Ìˆˆˆ°(€€€€€€€€€€€€€€€€¡Á½Í¥Ñ¥½¹}¥°¤°(€€€€€€€€€€€€¤(€€€€€€€€€€€É•±•…Í•}Á½Í¥Ñ¥½¹}…Á¥Ñ…±}É•Í•ÉÙ…Ñ¥½¸ (€€€€€€€€€€€€€€€½¹¹•Ñ¥½¸°(€€€€€€€€€€€€€€€Á½Í¥Ñ¥½¹}¥õÁ½Í¥Ñ¥½¹}¥°(€€€€€€€€€€€€€€€•¹ÑÉå}•á•ÕÑ¥½¹}É•ÅÕ•ÍÑ}¥õ9½¹”¥˜É½İlåt¥Ì9½¹”•±Í”ÍÑÈ¡É½İlåt¤°(€€€€€€€€€€€€¤(€€€€€€€€€€€½¹Ñ¥¹Õ”(€€€€€€€ÁÉ½Ñ•Ñ¥½¹}É½İÌ€ô½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€ˆˆ‰M1PÁÉ½Ñ•Ñ¥½¹}­¥¹±¥¹¥Ñ¥…Ñ½È±•á¡…¹•}½É‘•É}¥‘Ì°(€€€€€€€€€€€€€€€€€€€€€ÍÑ½Á}…™Ñ•È±ÑÉ…¥±¥¹}…™Ñ•È±Í½ÕÉ•}Á…å±½…(€€€€€€€€€€€€€€I=4ÉÕ¹Ñ¥µ”¹ÁÉ½Ñ•Ñ¥½¹}•Ù•¹ÑÌ]!IÁ½Í¥Ñ¥½¹}¥ô•Ì(€€€€€€€€€€€€€€=IH	d½ÕÉÉ•‘}…Ğˆˆˆ°(€€€€€€€€€€€€¡Á½Í¥Ñ¥½¹}¥°¤°(€€€€€€€€¤¹™•Ñ¡…±° ¤(€€€€€€€ÁÉ½Ñ•Ñ¥½¹Ì€ôl(€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€‰ÁÉ½Ñ•Ñ¥½¹}­¥¹ˆèÙ…±Õ•lÁt°€‰¥¹¥Ñ¥…Ñ½ÈˆèÙ…±Õ•lÅt°(€€€€€€€€€€€€€€€€‰•á¡…¹•}½É‘•É}¥‘ÌˆèÙ…±Õ•lÉt°€‰ÍÑ½Á}…™Ñ•ÈˆèÙ…±Õ•lÍt°(€€€€€€€€€€€€€€€€‰ÑÉ…¥±¥¹}…™Ñ•ÈˆèÙ…±Õ•lÑt°€‰Í½ÕÉ•}Á…å±½…ˆèÙ…±Õ•lÕt°(€€€€€€€€€€€ô(€€€€€€€€€€€™½ÈÙ…±Õ”¥¸ÁÉ½Ñ•Ñ¥½¹}É½İÌ(€€€€€€€t(€€€€€€€½µµ…¹‘}É½İÌ€ô½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€ˆˆ‰M1P½µµ…¹‘}¥±É•ÍÕ±Ñ}©Í½¸I=4ÉÕ¹Ñ¥µ”¹ÑÉ…‘•}½µµ…¹‘Ì(€€€€€€€€€€€€€€]!I½µµ…¹‘}ÑåÁ”ô±½Í”œ(€€€€€€€€€€€€€€€€9Á…å±½…‘}©Í½¸èé©Í½¹ˆ´øøÁ½Í¥Ñ¥½¹}¥œô•Ìˆˆˆ°(€€€€€€€€€€€€¡Á½Í¥Ñ¥½¹}¥°¤°(€€€€€€€€¤¹™•Ñ¡…±° ¤(€€€€€€€½µµ…¹‘Ì€ôl(€€€€€€€€€€€ì‰½µµ…¹‘}¥ˆèÙ…±Õ•lÁt°€‰É•ÍÕ±Ñ}©Í½¸ˆèÙ…±Õ•lÅuô™½ÈÙ…±Õ”¥¸½µµ…¹‘}É½İÌ(€€€€€€€t(€€€€€€€•á¥Ñ}É½İÌ€ôl(€€€€€€€€€€€Ù…±Õ”™½ÈÙ…±Õ”¥¸•á•ÕÑ¥½¹Ì¥˜Ù…±Õ•l‰½É‘•É}¥‰t¥¸±½Í”¹•á¥Ñ}½É‘•É}¥‘Ì(€€€€€€€t(€€€€€€€¡¥ÍÑ½Éå}‰å}¥€ôì(€€€€€€€€€€€ÍÑÈ¡Ù…±Õ”¹•Ğ ‰½É‘•É%ˆ¤½È€ˆˆ¤èÙ…±Õ”™½ÈÙ…±Õ”¥¸€¡½É‘•É}¡¥ÍÑ½Éä½Èmt¤(€€€€€€€ô(€€€€€€€•á¥Ñ}‰½‘ä€ô¡¥ÍÑ½Éå}‰å}¥¹•Ğ¡±½Í”¹•á¥Ñ}½É‘•É}¥¤½È€ (€€€€€€€€€€€©Í½¸¹±½…‘Ì¡ÍÑÈ¡•á¥Ñ}É½İÍlÁul‰Á…å±½…‘}©Í½¸‰t½È€‰íôˆ¤¤¥˜•á¥Ñ}É½İÌ•±Í”íô(€€€€€€€€¤(€€€€€€€•á¥Ñ}½İ¹•È°•á¥Ñ}µ•¡…¹¥Í´°…ÑÑÉ¥‰ÕÑ¥½¹}µ•Ñ¡½€ô±…ÍÍ¥™å}•á¥Ğ (€€€€€€€€€€€•á¥Ñ}½É‘•É}¥õ±½Í”¹•á¥Ñ}½É‘•É}¥°(€€€€€€€€€€€ÍÑ½Á}½É‘•É}ÑåÁ”õÍÑÈ¡•á¥Ñ}‰½‘ä¹•Ğ ‰ÍÑ½Á=É‘•ÉQåÁ”ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€É•…Ñ•}ÑåÁ”õÍÑÈ¡•á¥Ñ}‰½‘ä¹•Ğ ‰É•…Ñ•QåÁ”ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€ÁÉ½Ñ•Ñ¥½¹}•Ù•¹ÑÌõÁÉ½Ñ•Ñ¥½¹Ì°(€€€€€€€€€€€±½Í•}½µµ…¹‘Ìõ½µµ…¹‘Ì°(€€€€€€€€¤(€€€€€€€±½Í•‘}µÌ€ôµ…à¡¥¹Ğ¡Ù…±Õ•l‰•á•}Ñ¥µ•}µÌ‰t½È¹½Ü¤™½ÈÙ…±Õ”¥¸•á¥Ñ}É½İÌ¤(€€€€€€€•¹ÑÉå}™•”€ô½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€ˆˆ‰M1P½…±•Í”¡ÍÕ´¡…‰Ì¡•á•}™•”èé¹Õµ•É¥Œ¤¤°À¤(€€€€€€€€€€€€€€I=4ÉÕ¹Ñ¥µ”¹•á•ÕÑ¥½¹Ì]!I½É‘•É}±¥¹­}¥ô•Ìˆˆˆ°(€€€€€€€€€€€€¡ÍÑÈ¡É½İlát¤°¤°(€€€€€€€€¤¹™•Ñ¡½¹” ¥lÁt(€€€€€€€É½ÍÌ€ô±½Í”¹É½ÍÍ}Á¹°½È•¥µ…° À¤(€€€€€€€•á¥Ñ}™•”€ô±½Í”¹•á¥Ñ}™••}…ÑÕ…°½È•¥µ…° À¤(€€€€€€€¹•Ñ}İ¥Ñ¡½ÕÑ}™Õ¹‘¥¹œ€ôÉ½ÍÌ€´•¥µ…°¡ÍÑÈ¡•¹ÑÉå}™•”¤¤€´•á¥Ñ}™•”(€€€€€€€ÑÉ¥•È€ô¹Õµ‰•È¡•á¥Ñ}‰½‘ä¹•Ğ ‰ÑÉ¥•ÉAÉ¥”ˆ¤¤½È9½¹”(€€€€€€€ÑÉ¥•É}Í±¥ÁÁ…”€ôÑÉ¥•É}Ñ½}™¥±±}Í±¥ÁÁ…•}ÁĞ (€€€€€€€€€€€Í¥‘”°ÑÉ¥•È°±½Í”¹…ÑÕ…±}•á¥Ñ}…Ù}™¥±°½È•¥µ…° À¤(€€€€€€€€¤(€€€€€€€¥¹¥Ñ¥…±}ÍÑ½À€ô•¥µ…°¡ÍÑÈ¡É½İlÑt¤¤€¨€ (€€€€€€€€€€€•¥µ…° ˆÀ¸ääˆ¤¥˜Í¥‘”€ôô€‰	Õäˆ•±Í”•¥µ…° ˆÄ¸ÀÄˆ¤(€€€€€€€€¤(€€€€€€€±…Ñ•ÍÑ}ÍÑ½À€ô¹•áĞ (€€€€€€€€€€€€¡•¥µ…°¡ÍÑÈ¡Ù…±Õ•l‰ÍÑ½Á}…™Ñ•È‰t¤¤™½ÈÙ…±Õ”¥¸É•Ù•ÉÍ•¡ÁÉ½Ñ•Ñ¥½¹Ì¤¥˜Ù…±Õ•l‰ÍÑ½Á}…™Ñ•È‰t¥Ì¹½Ğ9½¹”¤°(€€€€€€€€€€€9½¹”°(€€€€€€€€¤(€€€€€€€…ÑÑÉ¥‰ÕÑ¥½¹}¥€ô€‰aP´ˆ€¬¡…Í¡±¥ˆ¹Í¡„ÈÔØ (€€€€€€€€€€€˜‰íÁ½Í¥Ñ¥½¹}¥‘õñí±½Í”¹•á¥Ñ}½É‘•É}¥‘ôˆ¹•¹½‘” ¤(€€€€€€€€¤¹¡•á‘¥•ÍĞ ¥lèÌÉt(€€€€€€€•Ù¥‘•¹”€ôì(€€€€€€€€€€€€‰•á¡…¹•}Á½Í¥Ñ¥½¹}­•äˆè˜‰	e	%PéU9%%é1%9HéUMPéíÍåµ‰½±ôéíÁ½Í¥Ñ¥½¹}¥‘áôˆ°(€€€€€€€€€€€€‰±½Í•}É•Í½±ÕÑ¥½¸ˆè±½Í”¹É•…Í½¸°(€€€€€€€€€€€€‰…ÑÑÉ¥‰ÕÑ¥½¹}µ•Ñ¡½ˆè…ÑÑÉ¥‰ÕÑ¥½¹}µ•Ñ¡½°(€€€€€€€€€€€€‰ÍÑ½Á}½É‘•É}ÑåÁ”ˆè•á¥Ñ}‰½‘ä¹•Ğ ‰ÍÑ½Á=É‘•ÉQåÁ”ˆ¤°(€€€€€€€€€€€€‰É•…Ñ•}ÑåÁ”ˆè•á¥Ñ}‰½‘ä¹•Ğ ‰É•…Ñ•QåÁ”ˆ¤°(€€€€€€€€€€€€‰™Õ¹‘¥¹œˆè9½¹”°(€€€€€€€ô(€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€ˆˆ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹Á½Í¥Ñ¥½¹}•á¥Ñ}…ÑÑÉ¥‰ÕÑ¥½¸ (€€€€€€€€€€€€€€€…ÑÑÉ¥‰ÕÑ¥½¹}¥±Á½Í¥Ñ¥½¹}¥±ÑÉ…‘•}¥±±½Í•‘}…Ğ±±¥¹­}ÍÑ…ÑÕÌ±±¥¹­}µ•Ñ¡½°(€€€€€€€€€€€€€€€•á¥Ñ}½İ¹•È±•á¥Ñ}µ•¡…¹¥Í´±•á¥Ñ}½É‘•É}¥±•á¥Ñ}½É‘•É}¥‘Ì°(€€€€€€€€€€€€€€€•á¥Ñ}•á•ÕÑ¥½¹}¥‘Ì°(€€€€€€€€€€€€€€€…ÑÕ…±}…Ù}•¹ÑÉä±¥¹Ñ•¹‘•‘}¥¹¥Ñ¥…±}¡…É‘}ÍÑ½À°(€€€€€€€€€€€€€€€…ÑÕ…±}•á¡…¹•}ÍÑ½Á}‰•™½É•}•á¥Ğ±•á¡…¹•}ÑÉ¥•É}ÁÉ¥”±ÑÉ¥•É}‰ä°(€€€€€€€€€€€€€€€…ÑÕ…±}•á¥Ñ}…Ù}™¥±°±…ÑÕ…±}•á¥Ñ}ÅÑä±•¹ÑÉå}Ñ½}•á¥Ñ}ÁÉ¥•}µ½Ù•}ÁĞ°(€€€€€€€€€€€€€€€ÑÉ¥•É}Ñ½}™¥±±}Í±¥ÁÁ…•}ÁĞ±É½ÍÍ}Á¹°±•¹ÑÉå}™••}…ÑÕ…°±•á¥Ñ}™••}…ÑÕ…°°(€€€€€€€€€€€€€€€™Õ¹‘¥¹œ±…ÑÕ…±}¹•Ñ}İ¥Ñ¡½ÕÑ}™Õ¹‘¥¹œ±…ÑÕ…±}¹•Ñ}Á¹°°(€€€€€€€€€€€€€€€•½¹½µ¥Í}½µÁ±•Ñ•¹•ÍÌ±•Ù¥‘•¹”¤(€€€€€€€€€€€€€€€Y1UL •Ì°•Ì°•Ì±Ñ½}Ñ¥µ•ÍÑ…µÀ •Ì¼ÄÀÀÀ¸À¤°aPœ°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°(€€€€€€€€€€€€€€€€€€€€€€€•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì±9U10°•Ì±9U10°(€€€€€€€€€€€€€€€€€€€€€€€AIQ%1}9=}U9%9œ°•Ì¤(€€€€€€€€€€€€€€€=8=91%P¡Á½Í¥Ñ¥½¹}¥¤<9=Q!%9ˆˆˆ°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€…ÑÑÉ¥‰ÕÑ¥½¹}¥°Á½Í¥Ñ¥½¹}¥°ÑÉ…‘•}¥°±½Í•‘}µÌ°±½Í”¹±¥¹­}µ•Ñ¡½°(€€€€€€€€€€€€€€€•á¥Ñ}½İ¹•È°•á¥Ñ}µ•¡…¹¥Í´°±½Í”¹•á¥Ñ}½É‘•É}¥°(€€€€€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ¡±½Í”¹•á¥Ñ}½É‘•É}¥‘Ì¤°©Í½¸¹‘ÕµÁÌ¡±½Í”¹•á¥Ñ}•á•ÕÑ¥½¹}¥‘Ì¤°(€€€€€€€€€€€€€€€É½İlÑt°¥¹¥Ñ¥…±}ÍÑ½À°±…Ñ•ÍÑ}ÍÑ½À°(€€€€€€€€€€€€€€€ÑÉ¥•È°•á¥Ñ}‰½‘ä¹•Ğ ‰ÑÉ¥•É	äˆ¤°±½Í”¹…ÑÕ…±}•á¥Ñ}…Ù}™¥±°°(€€€€€€€€€€€€€€€±½Í”¹…ÑÕ…±}•á¥Ñ}ÅÑä°±½Í”¹•¹ÑÉå}Ñ½}•á¥Ñ}µ½Ù•}ÁĞ°ÑÉ¥•É}Í±¥ÁÁ…”°(€€€€€€€€€€€€€€€É½ÍÌ°•¹ÑÉå}™•”°•á¥Ñ}™•”°¹•Ñ}İ¥Ñ¡½ÕÑ}™Õ¹‘¥¹œ°(€€€€€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ¡•Ù¥‘•¹”°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤°(€€€€€€€€€€€€¤°(€€€€€€€€¤(€€€€€€€•Ù•¹Ñ}¥€ô€‰A1´ˆ€¬¡…Í¡±¥ˆ¹Í¡„ÈÔØ (€€€€€€€€€€€˜‰íÁ½Í¥Ñ¥½¹}¥‘õñ1=Mñí±½Í”¹•á¥Ñ}½É‘•É}¥‘ôˆ¹•¹½‘” ¤(€€€€€€€€¤¹¡•á‘¥•ÍĞ ¥lèÌÉt(€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€ˆˆ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹Á½Í¥Ñ¥½¹}±¥™•å±•}•Ù•¹ÑÌ (€€€€€€€€€€€€€€€±¥™•å±•}•Ù•¹Ñ}¥±Á½Í¥Ñ¥½¹}¥±ÑÉ…‘•}¥±•Ù•¹Ñ}ÑåÁ”±½ÕÉÉ•‘}…Ğ°(€€€€€€€€€€€€€€€•á…Ñ}¥‘Ì±Á…å±½…±ÁÉ½Ù•¹…¹”¤(€€€€€€€€€€€€€€€Y1UL •Ì°•Ì°•Ì°1=Mœ±Ñ½}Ñ¥µ•ÍÑ…µÀ •Ì¼ÄÀÀÀ¸À¤°•Ì°•Ì°•Ì¤(€€€€€€€€€€€€€€€=8=91%P¡±¥™•å±•}•Ù•¹Ñ}¥¤<9=Q!%9ˆˆˆ°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€•Ù•¹Ñ}¥°Á½Í¥Ñ¥½¹}¥°ÑÉ…‘•}¥°±½Í•‘}µÌ°(€€€€€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ¡ì‰•á¥Ñ}½É‘•É}¥ˆè±½Í”¹•á¥Ñ}½É‘•É}¥°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰•á¥Ñ}½É‘•É}¥‘Ìˆè±½Í”¹•á¥Ñ}½É‘•É}¥‘Ì°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰•á¥Ñ}•á•ÕÑ¥½¹}¥‘Ìˆè±½Í”¹•á¥Ñ}•á•ÕÑ¥½¹}¥‘Íô¤°(€€€€€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ¡ì‰•á¥Ñ}½İ¹•Èˆè•á¥Ñ}½İ¹•È°€‰•á¥Ñ}µ•¡…¹¥Í´ˆè•á¥Ñ}µ•¡…¹¥Íµô¤°(€€€€€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ¡ì‰Í½ÕÉ”ˆè€‰™É•Í¡}‰å‰¥Ñ}É•½¹¥±¥…Ñ¥½¸ˆ°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰±¥¹­}µ•Ñ¡½ˆè±½Í”¹±¥¹­}µ•Ñ¡½‘ô¤°(€€€€€€€€€€€€¤°(€€€€€€€€¤(€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€ˆˆ‰UAQÉÕ¹Ñ¥µ”¹Á½Í¥Ñ¥½¹}½İ¹•ÉÍ¡¥À(€€€€€€€€€€€€€€MPÍÑ…Ñ”ô1=Mœ±±½Í•‘}…ĞõÑ½}Ñ¥µ•ÍÑ…µÀ •Ì¼ÄÀÀÀ¸À¤°(€€€€€€€€€€€€€€€€€€•á¥Ñ}½É‘•É}¥ô•Ì±•á¥Ñ}½É‘•É}¥‘Ìô•Ì±•á¥Ñ}•á•ÕÑ¥½¹}¥‘Ìô•Ì°(€€€€€€€€€€€€€€€€€€±½Í•}±¥¹­}ÍÑ…ÑÕÌôaPœ(€€€€€€€€€€€€€€]!IÁ½Í¥Ñ¥½¹}¥ô•Ìˆˆˆ°(€€€€€€€€€€€€¡±½Í•‘}µÌ°±½Í”¹•á¥Ñ}½É‘•É}¥°©Í½¸¹‘ÕµÁÌ¡±½Í”¹•á¥Ñ}½É‘•É}¥‘Ì¤°(€€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ¡±½Í”¹•á¥Ñ}•á•ÕÑ¥½¹}¥‘Ì¤°Á½Í¥Ñ¥½¹}¥¤°(€€€€€€€€¤(€€€€€€€É•±•…Í•}Á½Í¥Ñ¥½¹}…Á¥Ñ…±}É•Í•ÉÙ…Ñ¥½¸ (€€€€€€€€€€€½¹¹•Ñ¥½¸°(€€€€€€€€€€€Á½Í¥Ñ¥½¹}¥õÁ½Í¥Ñ¥½¹}¥°(€€€€€€€€€€€•¹ÑÉå}•á•ÕÑ¥½¹}É•ÅÕ•ÍÑ}¥õ9½¹”¥˜É½İlåt¥Ì9½¹”•±Í”ÍÑÈ¡É½İlåt¤°(€€€€€€€€¤(()‘•˜½±±•Ñ}Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•Ì (€€€½¹¹•Ñ¥½¸èÁÍå½Áœ¹½¹¹•Ñ¥½¸°(€€€­•äèÍÑÈ°(€€€Í•É•ĞèÍÑÈ°(€€€¹½İ}µÌè¥¹Ğ°(¤€´ø±¥ÍÑm‘¥ÑmÍÑÈ°½‰©•Ñutè(€€€€ˆˆ‰Pµ½¹±äÁ•ÈµÍåµ‰½°µ½‘”½‰Í•ÉÙ…Ñ¥½¹Ì™½È…Ñ¥Ù”MÑÉ…Ñ•äÕ¹¥Ù•ÉÍ•Ì¸ˆˆˆ(€€€İ¥Ñ ½¹¹•Ñ¥½¸¹ÑÉ…¹Í…Ñ¥½¸ ¤è(€€€€€€€É½İÌ€ô½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€ˆˆ‰M1P%MQ%9P©Í½¹‰}…ÉÉ…å}•±•µ•¹ÑÍ}Ñ•áĞ¡•À¹Á±…¹}©Í½¸´øÍåµ‰½±Ìœ¤LÍåµ‰½°(€€€€€€€€€€€€€€€€I=4ÍÑÉ…Ñ•å}•¹ÑÉä¹ÍÑÉ…Ñ•å}…Ñ¥Ù…Ñ¥½¹Ì„(€€€€€€€€€€€€€€€€)=%8ÍÑÉ…Ñ•å}•¹ÑÉä¹•¹ÑÉå}Á±…¹Ì•À(€€€€€€€€€€€€€€€€€€=8•À¹ÍÑÉ…Ñ•å}¥õ„¹ÍÑÉ…Ñ•å}¥(€€€€€€€€€€€€€€€€€9•À¹ÍÑÉ…Ñ•å}Ù•ÉÍ¥½¸õ„¹ÍÑÉ…Ñ•å}Ù•ÉÍ¥½¸(€€€€€€€€€€€€€€€€€9•À¹ÍÑÉ…Ñ•å}½¹™¥}™¥¹•ÉÁÉ¥¹Ğõ„¹ÍÑÉ…Ñ•å}½¹™¥}™¥¹•ÉÁÉ¥¹Ğ(€€€€€€€€€€€€€€€]!I„¹•¹…‰±•õÑÉÕ”(€€€€€€€€€€€€€€€=IH	dÍåµ‰½°ˆˆˆ(€€€€€€€€¤¹™•Ñ¡…±° ¤(€€€Íåµ‰½±Ì€ômÍÑÈ¡É½İlÁt¤™½ÈÉ½Ü¥¸É½İÌ¥˜ÍÑÈ¡É½İlÁt¤¹ÍÑÉ¥À ¥t(€€€¥˜¹½ĞÍåµ‰½±Ìè(€€€€€€€É•ÑÕÉ¸mt(€€€É•™É•Í¡}Í•½¹‘Ì€ô¥¹Ğ¡½Ì¹•¹Ù¥É½¸¹•Ğ ‰I%AQ}A=M%Q%=9}5=}IIM!}M=9Lˆ°€ˆÌÀˆ¤½È€ÌÀ¤(€€€½‰Í•ÉÙ•‘}…Ğ€ô‘…Ñ•Ñ¥µ”¹™É½µÑ¥µ•ÍÑ…µÀ¡¹½İ}µÌ€¼€ÄÀÀÀ°ÑèõUQ¤(€€€¥˜É•™É•Í¡}Í•½¹‘Ì€ø€Àè(€€€€€€€ÕÑ½™˜€ô½‰Í•ÉÙ•‘}…Ğ€´Ñ¥µ•‘•±Ñ„¡Í•½¹‘ÌõÉ•™É•Í¡}Í•½¹‘Ì¤(€€€€€€€İ¥Ñ ½¹¹•Ñ¥½¸¹ÑÉ…¹Í…Ñ¥½¸ ¤è(€€€€€€€€€€€É••¹Ğ€ôì(€€€€€€€€€€€€€€€ÍÑÈ¡É½İlÁt¤(€€€€€€€€€€€€€€€™½ÈÉ½Ü¥¸½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€€€€€€€€€ˆˆ‰M1P¥¹ÍÑÉÕµ•¹Ğ(€€€€€€€€€€€€€€€€€€€€€€€€I=4ÉÕ¹Ñ¥µ”¹Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•Ì(€€€€€€€€€€€€€€€€€€€€€€€]!I…½Õ¹Ñ}É•˜ô	e	%PéU9%%œ(€€€€€€€€€€€€€€€€€€€€€€€€€9ÁÉ½‘ÕÑ}…Ñ•½Éäô1%9Hœ(€€€€€€€€€€€€€€€€€€€€€€€€€9½‰Í•ÉÙ•‘}…Ğ€øô€•Ì(€€€€€€€€€€€€€€€€€€€€€€€€€9¥¹ÍÑÉÕµ•¹Ğ€ô9d •Ì¤(€€€€€€€€€€€€€€€€€€€€€€€I=U@	d¥¹ÍÑÉÕµ•¹Ğˆˆˆ°(€€€€€€€€€€€€€€€€€€€€¡ÕÑ½™˜°Íåµ‰½±Ì¤°(€€€€€€€€€€€€€€€€¤¹™•Ñ¡…±° ¤(€€€€€€€€€€€ô(€€€€€€€¥˜É••¹Ğ€ôôÍ•Ğ¡Íåµ‰½±Ì¤è(€€€€€€€€€€€É•ÑÕÉ¸mt(€€€™É•Í¡¹•ÍÍ}Í•½¹‘Ì€ô¥¹Ğ¡½Ì¹•¹Ù¥É½¸¹•Ğ ‰I%AQ}A=M%Q%=9}5=}IM!9MM}M=9Lˆ°€ˆÀˆ¤½È€À¤(€€€É•ÍÕ±Ğè±¥ÍÑm‘¥ÑmÍÑÈ°½‰©•Ñut€ômt(€€€™½ÈÍåµ‰½°¥¸Íåµ‰½±Ìè(€€€€€€€‰½‘ä°|€ô…Á¥}•Ğ (€€€€€€€€€€€€ˆ½ØÔ½Á½Í¥Ñ¥½¸½±¥ÍĞˆ°(€€€€€€€€€€€ì‰…Ñ•½Éäˆè€‰±¥¹•…Èˆ°€‰Íåµ‰½°ˆèÍåµ‰½±ô°(€€€€€€€€€€€­•ä°(€€€€€€€€€€€Í•É•Ğ°(€€€€€€€€¤(€€€€€€€¥˜‰½‘ä¹•Ğ ‰É•Ñ½‘”ˆ¤€„ô€Àè(€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È¡˜‰Á½Í¥Ñ¥½¸µµ½‘”ÁÉ½‰”É•©•Ñ•™½ÈíÍåµ‰½±ôˆ¤(€€€€€€€¥Ñ•µÌ€ô€¡‰½‘ä¹•Ğ ‰É•ÍÕ±Ğˆ¤½Èíô¤¹•Ğ ‰±¥ÍĞˆ¤½Èmt(€€€€€€€¥¹‘•á•Ì€ôÍ½ÉÑ•¡í¥¹Ğ¡¥Ñ•´¹•Ğ ‰Á½Í¥Ñ¥½¹%‘àˆ°€´Ä¤¤™½È¥Ñ•´¥¸¥Ñ•µÍô¤(€€€€€€€¥˜¥¹‘•á•Ì…¹Í•Ğ¡¥¹‘•á•Ì¤¹¥ÍÍÕ‰Í•Ğ¡ìÁô¤è(€€€€€€€€€€€µ½‘”€ô€‰=9}]dˆ(€€€€€€€€€€€Á½Í¥Ñ¥½¹}¥‘àè¥¹Ğğ9½¹”€ô€À(€€€€€€€•±¥˜Í•Ğ¡¥¹‘•á•Ì¤¹¥¹Ñ•ÉÍ•Ñ¥½¸¡ìÄ°€Éô¤è(€€€€€€€€€€€µ½‘”€ô€‰!ˆ(€€€€€€€€€€€Á½Í¥Ñ¥½¹}¥‘à€ô9½¹”(€€€€€€€•±Í”è(€€€€€€€€€€€µ½‘”€ô€‰U9-9=]8ˆ(€€€€€€€€€€€Á½Í¥Ñ¥½¹}¥‘à€ô9½¹”(€€€€€€€ÍÑ…Ñ•}É•˜€ô€‰Áµ½‘”´ˆ€¬¡…Í¡±¥ˆ¹Í¡„ÈÔØ (€€€€€€€€€€€˜‰	e	%Qñ	e	%PéU9%%ñ1%9IñíÍåµ‰½±õñíµ½‘•õñí¥¹‘•á•Íõñí¹½İ}µÍôˆ¹•¹½‘” ¤(€€€€€€€€¤¹¡•á‘¥•ÍĞ ¥lèÌÉt(€€€€€€€É•ÍÕ±Ğ¹…ÁÁ•¹ (€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€‰Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•}É•˜ˆèÍÑ…Ñ•}É•˜°(€€€€€€€€€€€€€€€€‰•á¡…¹”ˆè€‰	e	%Pˆ°(€€€€€€€€€€€€€€€€‰…½Õ¹Ñ}É•˜ˆè€‰	e	%PéU9%%ˆ°(€€€€€€€€€€€€€€€€‰ÁÉ½‘ÕÑ}…Ñ•½Éäˆè€‰1%9Hˆ°(€€€€€€€€€€€€€€€€‰¥¹ÍÑÉÕµ•¹ĞˆèÍåµ‰½°°(€€€€€€€€€€€€€€€€‰Á½Í¥Ñ¥½¹}µ½‘”ˆèµ½‘”°(€€€€€€€€€€€€€€€€‰Á½Í¥Ñ¥½¹}¥‘àˆèÁ½Í¥Ñ¥½¹}¥‘à°(€€€€€€€€€€€€€€€€‰½‰Í•ÉÙ•‘}…Ğˆè½‰Í•ÉÙ•‘}…Ğ°(€€€€€€€€€€€€€€€€‰É••¥Ù•‘}…Ğˆè‘…Ñ•Ñ¥µ”¹¹½Ü¡UQ¤°(€€€€€€€€€€€€€€€€‰™É•Í¡}Õ¹Ñ¥°ˆè½‰Í•ÉÙ•‘}…Ğ(€€€€€€€€€€€€€€€€¬Ñ¥µ•‘•±Ñ„¡Í•½¹‘Ìõµ…à À°™É•Í¡¹•ÍÍ}Í•½¹‘Ì¤¤°(€€€€€€€€€€€€€€€€‰ÁÉ½Ù•¹…¹”ˆèì(€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ”ˆè€‰	e	%Q}AI%YQ}IMQ}A=M%Q%=9}1%MPˆ°(€€€€€€€€€€€€€€€€€€€€‰Á½Í¥Ñ¥½¹}¥¹‘•á•Ìˆè¥¹‘•á•Ì°(€€€€€€€€€€€€€€€€€€€€‰™É•Í¡¹•ÍÍ}Í•½¹‘Ìˆè™É•Í¡¹•ÍÍ}Í•½¹‘Ì°(€€€€€€€€€€€€€€€ô°(€€€€€€€€€€€ô(€€€€€€€€¤(€€€É•ÑÕÉ¸É•ÍÕ±Ğ(()‘•˜Á•ÉÍ¥ÍÑ}Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•Ì (€€€½¹¹•Ñ¥½¸èÁÍå½Áœ¹½¹¹•Ñ¥½¸°(€€€ÍÑ…Ñ•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°½‰©•Ñut°(¤€´ø9½¹”è(€€€™½È¥Ñ•´¥¸ÍÑ…Ñ•Ìè(€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€ˆˆ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•Ì (€€€€€€€€€€€€€€€€€€Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•}É•˜±•á¡…¹”±…½Õ¹Ñ}É•˜±ÁÉ½‘ÕÑ}…Ñ•½Éä°(€€€€€€€€€€€€€€€€€€¥¹ÍÑÉÕµ•¹Ğ±Á½Í¥Ñ¥½¹}µ½‘”±Á½Í¥Ñ¥½¹}¥‘à±½‰Í•ÉÙ•‘}…Ğ±É••¥Ù•‘}…Ğ°(€€€€€€€€€€€€€€€€€€™É•Í¡}Õ¹Ñ¥°±ÁÉ½Ù•¹…¹”(€€€€€€€€€€€€€€€¤Y1UL •Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ìèé©Í½¹ˆ¤(€€€€€€€€€€€€€€=8=91%P¡Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•}É•˜¤<9=Q!%9ˆˆˆ°(€€€€€€€€€€€€ (€€€€€€€€€€€€€€€¥Ñ•µl‰Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•}É•˜‰t°(€€€€€€€€€€€€€€€¥Ñ•µl‰•á¡…¹”‰t°(€€€€€€€€€€€€€€€¥Ñ•µl‰…½Õ¹Ñ}É•˜‰t°(€€€€€€€€€€€€€€€¥Ñ•µl‰ÁÉ½‘ÕÑ}…Ñ•½Éä‰t°(€€€€€€€€€€€€€€€¥Ñ•µl‰¥¹ÍÑÉÕµ•¹Ğ‰t°(€€€€€€€€€€€€€€€¥Ñ•µl‰Á½Í¥Ñ¥½¹}µ½‘”‰t°(€€€€€€€€€€€€€€€¥Ñ•µl‰Á½Í¥Ñ¥½¹}¥‘à‰t°(€€€€€€€€€€€€€€€¥Ñ•µl‰½‰Í•ÉÙ•‘}…Ğ‰t°(€€€€€€€€€€€€€€€¥Ñ•µl‰É••¥Ù•‘}…Ğ‰t°(€€€€€€€€€€€€€€€¥Ñ•µl‰™É•Í¡}Õ¹Ñ¥°‰t°(€€€€€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ¡¥Ñ•µl‰ÁÉ½Ù•¹…¹”‰t°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤°(€€€€€€€€€€€€¤°(€€€€€€€€¤(()‘•˜É•½¹¥±” (€€€½¹¹•Ñ¥½¸èÁÍå½Áœ¹½¹¹•Ñ¥½¸°­•äèÍÑÈ°Í•É•ĞèÍÑÈ°É•…Í½¸èÍÑÈ(¤€´øÑÕÁ±•m¥¹Ğ°¥¹Ñtè(€€€ÍÑ…ÉÑ•€ô¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤€¨€ÄÀÀÀ¤(€€€ÑÉäè(€€€€€€€İ…±±•Ğ°|€ô…Á¥}•Ğ ˆ½ØÔ½…½Õ¹Ğ½İ…±±•Ğµ‰…±…¹”ˆ°ì‰…½Õ¹ÑQåÁ”ˆè€‰U9%%‰ô°­•ä°Í•É•Ğ¤(€€€€€€€Á½Í¥Ñ¥½¹Ì°|€ô…Á¥}•Ğ (€€€€€€€€€€€€ˆ½ØÔ½Á½Í¥Ñ¥½¸½±¥ÍĞˆ°(€€€€€€€€€€€ì‰…Ñ•½Éäˆè€‰±¥¹•…Èˆ°€‰Í•ÑÑ±•½¥¸ˆè€‰UMPˆ°€‰±¥µ¥Ğˆè€ˆÈÀÀ‰ô°(€€€€€€€€€€€­•ä°(€€€€€€€€€€€Í•É•Ğ°(€€€€€€€€¤(€€€€€€€½É‘•ÉÌ°|€ô…Á¥}•Ğ (€€€€€€€€€€€€ˆ½ØÔ½½É‘•È½É•…±Ñ¥µ”ˆ°(€€€€€€€€€€€ì‰…Ñ•½Éäˆè€‰±¥¹•…Èˆ°€‰Í•ÑÑ±•½¥¸ˆè€‰UMPˆ°€‰½Á•¹=¹±äˆè€ˆÀˆ°€‰±¥µ¥Ğˆè€ˆÔÀ‰ô°(€€€€€€€€€€€­•ä°(€€€€€€€€€€€Í•É•Ğ°(€€€€€€€€¤(€€€€€€€¥˜…¹ä¡¥Ñ•´¹•Ğ ‰É•Ñ½‘”ˆ¤€„ô€À™½È¥Ñ•´¥¸€¡İ…±±•Ğ°Á½Í¥Ñ¥½¹Ì°½É‘•ÉÌ¤¤è(€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‰•á¡…¹”É•©•Ñ•É•½¹¥±¥…Ñ¥½¸É•ÅÕ•ÍĞˆ¤(€€€€€€€¹½Ü€ô¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤€¨€ÄÀÀÀ¤(€€€€€€€Á½Í¥Ñ¥½¹}±¥ÍĞ€ôl(€€€€€€€€€€€À™½ÈÀ¥¸€ ¡Á½Í¥Ñ¥½¹Ì¹•Ğ ‰É•ÍÕ±Ğˆ¤½Èíô¤¹•Ğ ‰±¥ÍĞˆ¤½Èmt¤(€€€€€€€€€€€¥˜™±½…Ğ¡À¹•Ğ ‰Í¥é”ˆ¤½È€À¤€„ô€À(€€€€€€€t(€€€€€€€½É‘•É}±¥ÍĞ€ô€¡½É‘•ÉÌ¹•Ğ ‰É•ÍÕ±Ğˆ¤½Èíô¤¹•Ğ ‰±¥ÍĞˆ¤½Èmt(€€€€€€€Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•Ì€ô½±±•Ñ}Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•Ì (€€€€€€€€€€€½¹¹•Ñ¥½¸°­•ä°Í•É•Ğ°¹½Ü(€€€€€€€€¤(€€€€€€€™•Ñ¡}¡¥ÍÑ½Éä€ôÉ•…Í½¸€„ô€‰Á•É¥½‘¥Œˆ(€€€€€€€¥˜¹½Ğ™•Ñ¡}¡¥ÍÑ½Éäè(€€€€€€€€€€€ÕÉÉ•¹Ñ}­•åÌ€ôì(€€€€€€€€€€€€€€€€¡ÍÑÈ¡¥Ñ•´¹•Ğ ‰Íåµ‰½°ˆ¤½È€ˆˆ¤°¥¹Ğ¡¥Ñ•´¹•Ğ ‰Á½Í¥Ñ¥½¹%‘àˆ¤½È€À¤¤(€€€€€€€€€€€€€€€™½È¥Ñ•´¥¸Á½Í¥Ñ¥½¹}±¥ÍĞ(€€€€€€€€€€€ô(€€€€€€€€€€€€Œ±½Í”Ñ¡”É•…ÑÉ…¹Í…Ñ¥½¸‰•™½É”Ñ¡”±…Ñ•ÈİÉ¥Ñ”ÑÉ…¹Í…Ñ¥½¸¸(€€€€€€€€€€€€Œ=Ñ¡•Éİ¥Í”ÁÍå½ÁœÑÉ…¹Í…Ñ¥½¸ ¤‰•½µ•Ì„MYA=%9PÕ¹‘•ÈÑ¡”(€€€€€€€€€€€€Œ¥µÁ±¥¥Ğ½ÕÑ•ÈÑÉ…¹Í…Ñ¥½¸…¹Á•É¥½‘¥ŒÉ•½¹¥±¥…Ñ¥½¸±•…­Ì¥Ğ¸(€€€€€€€€€€€İ¥Ñ ½¹¹•Ñ¥½¸¹ÑÉ…¹Í…Ñ¥½¸ ¤è(€€€€€€€€€€€€€€€½İ¹•‘}­•åÌ€ôì(€€€€€€€€€€€€€€€€€€€€¡ÍÑÈ¡É½İlÁt¤°¥¹Ğ¡É½İlÅt½È€À¤¤(€€€€€€€€€€€€€€€€€€€™½ÈÉ½Ü¥¸½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€€€€€€€€€€€€€ˆˆ‰M1PÍåµ‰½°±Á½Í¥Ñ¥½¹}¥‘àI=4ÉÕ¹Ñ¥µ”¹Á½Í¥Ñ¥½¹}½İ¹•ÉÍ¡¥À(€€€€€€€€€€€€€€€€€€€€€€€€€€]!IÍÑ…Ñ”ô=A8œ9±½Í•}±¥¹­}ÍÑ…ÑÕÌô=A8œˆˆˆ(€€€€€€€€€€€€€€€€€€€€¤¹™•Ñ¡…±° ¤(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€ŒI•½Ù•Éä½…Õ‘¥Ğ•á•ÁÑ¥½¸è¡¥ÍÑ½Éä¥Ì™•Ñ¡•½¹±äİ¡•¸…¸½İ¹•å±”‘¥Í…ÁÁ•…É•¸(€€€€€€€€€€€µ¥ÍÍ¥¹}½İ¹•‘}Á½Í¥Ñ¥½¸€ô‰½½°¡½İ¹•‘}­•åÌ€´ÕÉÉ•¹Ñ}­•åÌ¤(€€€€€€€€€€€™•Ñ¡}¡¥ÍÑ½Éä€ôµ¥ÍÍ¥¹}½İ¹•‘}Á½Í¥Ñ¥½¸(€€€€€€€½É‘•É}¡¥ÍÑ½Éäè±¥ÍÑm‘¥ÑmÍÑÈ°½‰©•Ñut€ômt(€€€€€€€¥˜™•Ñ¡}¡¥ÍÑ½Éäè(€€€€€€€€€€€½É‘•É}¡¥ÍÑ½Éå}É•ÍÁ½¹Í”°|€ô…Á¥}•Ğ (€€€€€€€€€€€€€€€€ˆ½ØÔ½½É‘•È½¡¥ÍÑ½Éäˆ°(€€€€€€€€€€€€€€€ì‰…Ñ•½Éäˆè€‰±¥¹•…Èˆ°€‰Í•ÑÑ±•½¥¸ˆè€‰UMPˆ°€‰±¥µ¥Ğˆè€ˆÈÀÀ‰ô°(€€€€€€€€€€€€€€€­•ä°(€€€€€€€€€€€€€€€Í•É•Ğ°(€€€€€€€€€€€€¤(€€€€€€€€€€€¥˜½É‘•É}¡¥ÍÑ½Éå}É•ÍÁ½¹Í”¹•Ğ ‰É•Ñ½‘”ˆ¤€„ô€Àè(€€€€€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‰•á¡…¹”É•©•Ñ•½É‘•Èµ¡¥ÍÑ½ÉäÉ•½¹¥±¥…Ñ¥½¸É•ÅÕ•ÍĞˆ¤(€€€€€€€€€€€½É‘•É}¡¥ÍÑ½Éä€ô€¡½É‘•É}¡¥ÍÑ½Éå}É•ÍÁ½¹Í”¹•Ğ ‰É•ÍÕ±Ğˆ¤½Èíô¤¹•Ğ ‰±¥ÍĞˆ¤½Èmt(€€€€€€€…½Õ¹Ğ€ô€ ¡İ…±±•Ğ¹•Ğ ‰É•ÍÕ±Ğˆ¤½Èíô¤¹•Ğ ‰±¥ÍĞˆ¤½Èmíõt¥lÁt(€€€€€€€İ¥Ñ ½¹¹•Ñ¥½¸¹ÑÉ…¹Í…Ñ¥½¸ ¤è(€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” ‰1QI=4ÉÕ¹Ñ¥µ”¹¡½Ñ}Á½Í¥Ñ¥½¹Ìˆ¤(€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” ‰1QI=4ÉÕ¹Ñ¥µ”¹¡½Ñ}½É‘•ÉÌˆ¤(€€€€€€€€€€€™½ÈÀ¥¸Á½Í¥Ñ¥½¹}±¥ÍĞè(€€€€€€€€€€€€€€€ÕÁÍ•ÉÑ}Á½Í¥Ñ¥½¸¡½¹¹•Ñ¥½¸°À°¹½Ü¤(€€€€€€€€€€€™½È¼¥¸½É‘•É}±¥ÍĞè(€€€€€€€€€€€€€€€ÕÁÍ•ÉÑ}½É‘•È¡½¹¹•Ñ¥½¸°¼°¹½Ü¤(€€€€€€€€€€€Á•ÉÍ¥ÍÑ}Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•Ì¡½¹¹•Ñ¥½¸°Á½Í¥Ñ¥½¹}µ½‘•}ÍÑ…Ñ•Ì¤(€€€€€€€€€€€™½È¥Ñ•´¥¸½É‘•É}¡¥ÍÑ½Éäè(€€€€€€€€€€€€€€€ÕÁÍ•ÉÑ}•á¡…¹•}½É‘•É}¡¥ÍÑ½Éä¡½¹¹•Ñ¥½¸°¥Ñ•´°¹½Ü¤(€€€€€€€€€€€ÕÁÍ•ÉÑ}İ…±±•Ğ¡½¹¹•Ñ¥½¸°…½Õ¹Ğ°¹½Ü¤(€€€€€€€€€€€É•½¹¥±•}Á½Í¥Ñ¥½¹}½İ¹•ÉÍ¡¥À¡½¹¹•Ñ¥½¸°Á½Í¥Ñ¥½¹}±¥ÍĞ°¹½Ü°½É‘•É}¡¥ÍÑ½Éä¤(€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€€€€€ˆˆ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹É•½¹¥±¥…Ñ¥½¹}ÉÕ¹Ì (€€€€€€€€€€€€€€€€€€€ÍÑ…ÉÑ•‘}…Ñ}•Á½¡}µÌ±™¥¹¥Í¡•‘}…Ñ}•Á½¡}µÌ±É•…Í½¸±½¬±Á½Í¥Ñ¥½¹Ì±½É‘•ÉÌ±•ÉÉ½È¤(€€€€€€€€€€€€€€€€€€€Y1UL •Ì°•Ì°•Ì°Ä°•Ì°•Ì°œœ¤ˆˆˆ°(€€€€€€€€€€€€€€€€¡ÍÑ…ÉÑ•°¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤€¨€ÄÀÀÀ¤°É•…Í½¸°±•¸¡Á½Í¥Ñ¥½¹}±¥ÍĞ¤°±•¸¡½É‘•É}±¥ÍĞ¤¤°(€€€€€€€€€€€€¤(€€€€€€€É•ÑÕÉ¸±•¸¡Á½Í¥Ñ¥½¹}±¥ÍĞ¤°±•¸¡½É‘•É}±¥ÍĞ¤(€€€•á•ÁĞá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€½¹¹•Ñ¥½¸¹É½±±‰…¬ ¤(€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€€€€€ˆˆ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹É•½¹¥±¥…Ñ¥½¹}ÉÕ¹Ì (€€€€€€€€€€€€€€€ÍÑ…ÉÑ•‘}…Ñ}•Á½¡}µÌ±™¥¹¥Í¡•‘}…Ñ}•Á½¡}µÌ±É•…Í½¸±½¬±Á½Í¥Ñ¥½¹Ì±½É‘•ÉÌ±•ÉÉ½È¤(€€€€€€€€€€€€€€€Y1UL •Ì°•Ì°•Ì°À°À°À°•Ì¤ˆˆˆ°(€€€€€€€€€€€€¡ÍÑ…ÉÑ•°¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤€¨€ÄÀÀÀ¤°É•…Í½¸°˜‰íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ôèí•áôˆ¤°(€€€€€€€€¤(€€€€€€€½¹¹•Ñ¥½¸¹½µµ¥Ğ ¤(€€€€€€€É…¥Í”(()‘•˜ÕÁÍ•ÉÑ}•á¡…¹•}½É‘•É}¡¥ÍÑ½Éä (€€€½¹¹•Ñ¥½¸èÁÍå½Áœ¹½¹¹•Ñ¥½¸°¥Ñ•´è‘¥ÑmÍÑÈ°½‰©•Ñt°¹½Üè¥¹Ğ(¤€´ø9½¹”è(€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€ˆˆ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹•á¡…¹•}½É‘•É}¡¥ÍÑ½Éä (€€€€€€€€€€€½É‘•É}¥±½É‘•É}±¥¹­}¥±Íåµ‰½°±Í¥‘”±½É‘•É}ÍÑ…ÑÕÌ±ÕÁ‘…Ñ•‘}…Ñ}•Á½¡}µÌ°(€€€€€€€€€€€Á…å±½…‘}©Í½¸±É•™É•Í¡•‘}…Ñ}•Á½¡}µÌ¤(€€€€€€€€€€€Y1UL •Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì¤(€€€€€€€€€€€=8=91%P¡½É‘•É}¥¤<UAQMP(€€€€€€€€€€€€€½É‘•É}±¥¹­}¥õ•á±Õ‘•¹½É‘•É}±¥¹­}¥±Íåµ‰½°õ•á±Õ‘•¹Íåµ‰½°°(€€€€€€€€€€€€€Í¥‘”õ•á±Õ‘•¹Í¥‘”±½É‘•É}ÍÑ…ÑÕÌõ•á±Õ‘•¹½É‘•É}ÍÑ…ÑÕÌ°(€€€€€€€€€€€€€ÕÁ‘…Ñ•‘}…Ñ}•Á½¡}µÌõ•á±Õ‘•¹ÕÁ‘…Ñ•‘}…Ñ}•Á½¡}µÌ°(€€€€€€€€€€€€€Á…å±½…‘}©Í½¸õ•á±Õ‘•¹Á…å±½…‘}©Í½¸°(€€€€€€€€€€€€€É•™É•Í¡•‘}…Ñ}•Á½¡}µÌõ•á±Õ‘•¹É•™É•Í¡•‘}…Ñ}•Á½¡}µÌˆˆˆ°(€€€€€€€€ (€€€€€€€€€€€ÍÑÈ¡¥Ñ•´¹•Ğ ‰½É‘•É%ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€ÍÑÈ¡¥Ñ•´¹•Ğ ‰½É‘•É1¥¹­%ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€ÍÑÈ¡¥Ñ•´¹•Ğ ‰Íåµ‰½°ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€ÍÑÈ¡¥Ñ•´¹•Ğ ‰Í¥‘”ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€ÍÑÈ¡¥Ñ•´¹•Ğ ‰½É‘•ÉMÑ…ÑÕÌˆ¤½È€ˆˆ¤°(€€€€€€€€€€€¥¹Ğ¡¥Ñ•´¹•Ğ ‰ÕÁ‘…Ñ•‘Q¥µ”ˆ¤½È€À¤°(€€€€€€€€€€€©Í½¸¹‘ÕµÁÌ¡¥Ñ•´°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤°(€€€€€€€€€€€¹½Ü°(€€€€€€€€¤°(€€€€¤(()‘•˜ÕÁÍ•ÉÑ}Á½Í¥Ñ¥½¸¡½¹¹•Ñ¥½¸èÁÍå½Áœ¹½¹¹•Ñ¥½¸°¥Ñ•´è‘¥ÑmÍÑÈ°½‰©•Ñt°¹½Üè¥¹Ğ¤€´ø9½¹”è(€€€•á¥ÍÑ¥¹œ€ô½¹¹•Ñ¥½¸¹•á•ÕÑ” (€€€€€€€€‰M1PÁ…å±½…‘}©Í½¸I=4ÉÕ¹Ñ¥µ”¹¡½Ñ}Á½Í¥Ñ¥½¹Ì]!IÍåµ‰½°ô•Ì9Á½Í¥Ñ¥½¹}¥‘àô•Ìˆ°(€€€€€€€€¡¥Ñ•´¹•Ğ ‰Íåµ‰½°ˆ°€ˆˆ¤°¥¹Ğ¡¥Ñ•´¹•Ğ ‰Á½Í¥Ñ¥½¹%‘àˆ¤½È€À¤¤°(€€€€¤¹™•Ñ¡½¹” ¤(€€€µ•É•€ô©Í½¸¹±½…‘Ì¡•á¥ÍÑ¥¹lÁt¤¥˜•á¥ÍÑ¥¹œ•±Í”íô(€€€µ•É•¹ÕÁ‘…Ñ”¡¥Ñ•´¤(€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” ˆˆ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹¡½Ñ}Á½Í¥Ñ¥½¹Ì¡Íåµ‰½°±Á½Í¥Ñ¥½¹}¥‘à±Í¥‘”±Í¥é”±•¹ÑÉå}ÁÉ¥”±±•Ù•É…”°(€€€€€€€•á¡…¹•}ÕÁ‘…Ñ•‘}µÌ±É•™É•Í¡•‘}…Ñ}•Á½¡}µÌ±Á…å±½…‘}©Í½¸¤Y1UL •Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì¤(€€€€€€€=8=91%P¡Íåµ‰½°±Á½Í¥Ñ¥½¹}¥‘à¤<UAQMPÍ¥‘”õ•á±Õ‘•¹Í¥‘”±Í¥é”õ•á±Õ‘•¹Í¥é”°(€€€€€€€•¹ÑÉå}ÁÉ¥”õ•á±Õ‘•¹•¹ÑÉå}ÁÉ¥”±±•Ù•É…”õ•á±Õ‘•¹±•Ù•É…”±•á¡…¹•}ÕÁ‘…Ñ•‘}µÌõ•á±Õ‘•¹•á¡…¹•}ÕÁ‘…Ñ•‘}µÌ°(€€€€€€€É•™É•Í¡•‘}…Ñ}•Á½¡}µÌõ•á±Õ‘•¹É•™É•Í¡•‘}…Ñ}•Á½¡}µÌ±Á…å±½…‘}©Í½¸õ•á±Õ‘•¹Á…å±½…‘}©Í½¸ˆˆˆ°(€€€€€€€€¡µ•É•¹•Ğ ‰Íåµ‰½°ˆ°€ˆˆ¤°¥¹Ğ¡µ•É•¹•Ğ ‰Á½Í¥Ñ¥½¹%‘àˆ¤½È€À¤°µ•É•¹•Ğ ‰Í¥‘”ˆ°€ˆˆ¤°µ•É•¹•Ğ ‰Í¥é”ˆ°€ˆÀˆ¤°(€€€€€€€€µ•É•¹•Ğ ‰•¹ÑÉåAÉ¥”ˆ¤½Èµ•É•¹•Ğ ‰…ÙAÉ¥”ˆ¤½È€ˆˆ°µ•É•¹•Ğ ‰±•Ù•É…”ˆ°€ˆˆ¤°¥¹Ğ¡µ•É•¹•Ğ ‰ÕÁ‘…Ñ•‘Q¥µ”ˆ¤½È€À¤°¹½Ü°(€€€€€€€€©Í½¸¹‘ÕµÁÌ¡µ•É•°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤¤¤(()‘•˜ÕÁÍ•ÉÑ}½É‘•È¡½¹¹•Ñ¥½¸èÁÍå½Áœ¹½¹¹•Ñ¥½¸°¥Ñ•´è‘¥ÑmÍÑÈ°½‰©•Ñt°¹½Üè¥¹Ğ¤€´ø9½¹”è(€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” ˆˆ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹¡½Ñ}½É‘•ÉÌ¡½É‘•É}¥±½É‘•É}±¥¹­}¥±Íåµ‰½°±Í¥‘”±½É‘•É}ÍÑ…ÑÕÌ±ÅÑä±ÁÉ¥”°(€€€€€€€±•…Ù•Í}ÅÑä±•á¡…¹•}ÕÁ‘…Ñ•‘}µÌ±É•™É•Í¡•‘}…Ñ}•Á½¡}µÌ±Á…å±½…‘}©Í½¸¤Y1UL •Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì¤(€€€€€€€=8=91%P¡½É‘•É}¥¤<UAQMP½É‘•É}ÍÑ…ÑÕÌõ•á±Õ‘•¹½É‘•É}ÍÑ…ÑÕÌ±±•…Ù•Í}ÅÑäõ•á±Õ‘•¹±•…Ù•Í}ÅÑä°(€€€€€€€•á¡…¹•}ÕÁ‘…Ñ•‘}µÌõ•á±Õ‘•¹•á¡…¹•}ÕÁ‘…Ñ•‘}µÌ±É•™É•Í¡•‘}…Ñ}•Á½¡}µÌõ•á±Õ‘•¹É•™É•Í¡•‘}…Ñ}•Á½¡}µÌ°(€€€€€€€Á…å±½…‘}©Í½¸õ•á±Õ‘•¹Á…å±½…‘}©Í½¸ˆˆˆ°(€€€€€€€€¡¥Ñ•´¹•Ğ ‰½É‘•É%ˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰½É‘•É1¥¹­%ˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰Íåµ‰½°ˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰Í¥‘”ˆ°€ˆˆ¤°(€€€€€€€€¥Ñ•´¹•Ğ ‰½É‘•ÉMÑ…ÑÕÌˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰ÅÑäˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰ÁÉ¥”ˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰±•…Ù•ÍEÑäˆ°€ˆˆ¤°(€€€€€€€€¥¹Ğ¡¥Ñ•´¹•Ğ ‰ÕÁ‘…Ñ•‘Q¥µ”ˆ¤½È€À¤°¹½Ü°©Í½¸¹‘ÕµÁÌ¡¥Ñ•´°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤¤¤(()‘•˜ÕÁÍ•ÉÑ}İ…±±•Ğ¡½¹¹•Ñ¥½¸èÁÍå½Áœ¹½¹¹•Ñ¥½¸°¥Ñ•´è‘¥ÑmÍÑÈ°½‰©•Ñt°¹½Üè¥¹Ğ¤€´ø9½¹”è(€€€…Ù…¥±…‰±”€ôÍÑÈ¡…½Õ¹Ñ}…Ù…¥±…‰±•}ÕÍ‘Ğ¡¥Ñ•´¤¤(€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” ˆˆ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹İ…±±•Ñ}±…Ñ•ÍĞ¡Í¥¹±•Ñ½¸±É•™É•Í¡•‘}…Ñ}•Á½¡}µÌ±Ñ½Ñ…±}•ÅÕ¥Ñä±İ…±±•Ñ}‰…±…¹”°(€€€€€€€…Ù…¥±…‰±•}‰…±…¹”±Á…å±½…‘}©Í½¸¤Y1UL Ä°•Ì°•Ì°•Ì°•Ì°•Ì¤=8=91%P¡Í¥¹±•Ñ½¸¤<UAQMP(€€€€€€€É•™É•Í¡•‘}…Ñ}•Á½¡}µÌõ•á±Õ‘•¹É•™É•Í¡•‘}…Ñ}•Á½¡}µÌ±Ñ½Ñ…±}•ÅÕ¥Ñäõ•á±Õ‘•¹Ñ½Ñ…±}•ÅÕ¥Ñä°(€€€€€€€İ…±±•Ñ}‰…±…¹”õ•á±Õ‘•¹İ…±±•Ñ}‰…±…¹”±…Ù…¥±…‰±•}‰…±…¹”õ•á±Õ‘•¹…Ù…¥±…‰±•}‰…±…¹”±Á…å±½…‘}©Í½¸õ•á±Õ‘•¹Á…å±½…‘}©Í½¸ˆˆˆ°(€€€€€€€€¡¹½Ü°¥Ñ•´¹•Ğ ‰Ñ½Ñ…±ÅÕ¥Ñäˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰Ñ½Ñ…±]…±±•Ñ	…±…¹”ˆ°€ˆˆ¤°…Ù…¥±…‰±”°©Í½¸¹‘ÕµÁÌ¡¥Ñ•´°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤¤¤(()‘•˜¡…¹‘±•}ÁÉ¥Ù…Ñ”¡½¹¹•Ñ¥½¸èÁÍå½Áœ¹½¹¹•Ñ¥½¸°µ•ÍÍ…”è‘¥ÑmÍÑÈ°½‰©•Ñt¤€´ø9½¹”è(€€€Ñ½Á¥Œ€ôÍÑÈ¡µ•ÍÍ…”¹•Ğ ‰Ñ½Á¥Œˆ¤½È€ˆˆ¤(€€€¥˜¹½ĞÑ½Á¥Œè(€€€€€€€É•ÑÕÉ¸(€€€¹½Ü€ô¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤€¨€ÄÀÀÀ¤(€€€‘…Ñ„€ôµ•ÍÍ…”¹•Ğ ‰‘…Ñ„ˆ¤½Èmt(€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹ÁÉ¥Ù…Ñ•}•Ù•¹ÑÌ¡É••¥Ù•‘}…Ñ}•Á½¡}µÌ±Ñ½Á¥Œ±µ•ÍÍ…•}¥±É•…Ñ¥½¹}Ñ¥µ•}µÌ±Á…å±½…‘}©Í½¸¤Y1UL •Ì°•Ì°•Ì°•Ì°•Ì¤ˆ°(€€€€€€€€€€€€€€€€€€€€€€€¡¹½Ü°Ñ½Á¥Œ°ÍÑÈ¡µ•ÍÍ…”¹•Ğ ‰¥ˆ¤½È€ˆˆ¤°¥¹Ğ¡µ•ÍÍ…”¹•Ğ ‰É•…Ñ¥½¹Q¥µ”ˆ¤½È€À¤°©Í½¸¹‘ÕµÁÌ¡µ•ÍÍ…”°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤¤¤(€€€™½È¥Ñ•´¥¸‘…Ñ„è(€€€€€€€¥˜Ñ½Á¥Œ¹ÍÑ…ÉÑÍİ¥Ñ  ‰Á½Í¥Ñ¥½¸ˆ¤è(€€€€€€€€€€€¥˜™±½…Ğ¡¥Ñ•´¹•Ğ ‰Í¥é”ˆ¤½È€À¤€ôô€Àè(€€€€€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” ‰1QI=4ÉÕ¹Ñ¥µ”¹¡½Ñ}Á½Í¥Ñ¥½¹Ì]!IÍåµ‰½°ô•Ì9Á½Í¥Ñ¥½¹}¥‘àô•Ìˆ°€¡¥Ñ•´¹•Ğ ‰Íåµ‰½°ˆ°€ˆˆ¤°¥¹Ğ¡¥Ñ•´¹•Ğ ‰Á½Í¥Ñ¥½¹%‘àˆ¤½È€À¤¤¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€ÕÁÍ•ÉÑ}Á½Í¥Ñ¥½¸¡½¹¹•Ñ¥½¸°¥Ñ•´°¹½Ü¤(€€€€€€€•±¥˜Ñ½Á¥Œ¹ÍÑ…ÉÑÍİ¥Ñ  ‰½É‘•Èˆ¤è(€€€€€€€€€€€¥˜¥Ñ•´¹•Ğ ‰½É‘•ÉMÑ…ÑÕÌˆ¤¥¸ì‰¥±±•ˆ°€‰…¹•±±•ˆ°€‰I•©•Ñ•ˆ°€‰•…Ñ¥Ù…Ñ•‰ôè(€€€€€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” ‰1QI=4ÉÕ¹Ñ¥µ”¹¡½Ñ}½É‘•ÉÌ]!I½É‘•É}¥ô•Ìˆ°€¡¥Ñ•´¹•Ğ ‰½É‘•É%ˆ°€ˆˆ¤°¤¤(€€€€€€€€€€€•±Í”è(€€€€€€€€€€€€€€€ÕÁÍ•ÉÑ}½É‘•È¡½¹¹•Ñ¥½¸°¥Ñ•´°¹½Ü¤(€€€€€€€•±¥˜Ñ½Á¥Œ¹ÍÑ…ÉÑÍİ¥Ñ  ‰•á•ÕÑ¥½¸ˆ¤è(€€€€€€€€€€€½¹¹•Ñ¥½¸¹•á•ÕÑ” ˆˆ‰%9MIP%9Q<ÉÕ¹Ñ¥µ”¹•á•ÕÑ¥½¹Ì¡•á•}¥±½É‘•É}¥±½É‘•É}±¥¹­}¥±Íåµ‰½°±Í¥‘”±•á•}ÅÑä°(€€€€€€€€€€€€€€€•á•}ÁÉ¥”±•á•}™•”±•á•}Ñ¥µ•}µÌ±É••¥Ù•‘}…Ñ}•Á½¡}µÌ±Á…å±½…‘}©Í½¸¤Y1UL •Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì°•Ì¤(€€€€€€€€€€€€€€€=8=91%P¡•á•}¥¤<9=Q!%9ˆˆˆ°€¡¥Ñ•´¹•Ğ ‰•á•%ˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰½É‘•É%ˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰½É‘•É1¥¹­%ˆ°€ˆˆ¤°(€€€€€€€€€€€€€€€¥Ñ•´¹•Ğ ‰Íåµ‰½°ˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰Í¥‘”ˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰•á•EÑäˆ°€ˆˆ¤°¥Ñ•´¹•Ğ ‰•á•AÉ¥”ˆ°€ˆˆ¤°(€€€€€€€€€€€€€€€¥Ñ•´¹•Ğ ‰•á••”ˆ°€ˆˆ¤°¥¹Ğ¡¥Ñ•´¹•Ğ ‰•á•Q¥µ”ˆ¤½È€À¤°¹½Ü°©Í½¸¹‘ÕµÁÌ¡¥Ñ•´°•¹ÍÕÉ•}…Í¥¤õ…±Í”¤¤¤(€€€€€€€•±¥˜Ñ½Á¥Œ€ôô€‰İ…±±•Ğˆè(€€€€€€€€€€€ÕÁÍ•ÉÑ}İ…±±•Ğ¡½¹¹•Ñ¥½¸°¥Ñ•´°¹½Ü¤(€€€½¹¹•Ñ¥½¸¹½µµ¥Ğ ¤(()‘•˜ÁÉ¥Ù…Ñ•}±½½À¡­•äèÍÑÈ°Í•É•ĞèÍÑÈ¤€´ø9½¹”è(€€€É•½¹¹•ÑÌ€ô€À(€€€½¹¹•Ñ¥½¸€ô‘ˆ ‰É¥ÁÑ„µÁÉ¥Ù…Ñ”µİÌˆ¤(€€€İ¡¥±”ÉÕ¹¹¥¹œè(€€€€€€€ÑÉäè(€€€€€€€€€€€¥˜É•½¹¹•ÑÌ€ø€Àè(€€€€€€€€€€€€€€€‘¥Í…Éµ}¹•İ}•¹ÑÉ¥•Ì¡½¹¹•Ñ¥½¸°€‰ÁÉ¥Ù…Ñ”]LÉ•½¹¹•Ğè½İ¹•ÈÉ”µ…É´É•ÅÕ¥É•ˆ¤(€€€€€€€€€€€¡½Ñ}Á½Í¥Ñ¥½¹Ì°¡½Ñ}½É‘•ÉÌ€ôÉ•½¹¥±”¡½¹¹•Ñ¥½¸°­•ä°Í•É•Ğ°€‰ÍÑ…ÉÑÕÀˆ¥˜É•½¹¹•ÑÌ€ôô€À•±Í”€‰É•½¹¹•Ğˆ¤(€€€€€€€€€€€İÌ€ôİ•‰Í½­•Ğ¹É•…Ñ•}½¹¹•Ñ¥½¸¡AI%YQ}UI0°Ñ¥µ•½ÕĞôÄÀ°•¹…‰±•}µÕ±Ñ¥Ñ¡É•…õ…±Í”¤(€€€€€€€€€€€İÌ¹Í•ÑÑ¥µ•½ÕĞ Ä¤(€€€€€€€€€€€…ÕÑ ¡İÌ°­•ä°Í•É•Ğ¤(€€€€€€€€€€€İÌ¹Í•¹¡©Í½¸¹‘ÕµÁÌ¡ì‰½Àˆè€‰ÍÕ‰ÍÉ¥‰”ˆ°€‰…ÉÌˆèl‰½É‘•È¹±¥¹•…Èˆ°€‰•á•ÕÑ¥½¸¹±¥¹•…Èˆ°€‰Á½Í¥Ñ¥½¸¹±¥¹•…Èˆ°€‰İ…±±•Ğ‰uô°Í•Á…É…Ñ½ÉÌô ˆ°ˆ°€ˆèˆ¤¤¤(€€€€€€€€€€€½¹¹•Ñ¥½¹}•Ù•¹Ğ¡½¹¹•Ñ¥½¸°€‰ÁÉ¥Ù…Ñ”ˆ°€‰½¹¹•Ñ•ˆ°É•½¹¹•ÑÌõÉ•½¹¹•ÑÌ¤(€€€€€€€€€€€…Ñ½µ¥}ÍÑ…ÑÕÌ ‰ÁÉ¥Ù…Ñ”ˆ°ì‰ÍÑ…Ñ”ˆè€‰½¹¹•Ñ•ˆ°€‰½¹¹•Ñ•‘}…Ñ}•Á½ ˆè¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤¤°€‰É•½¹¹•ÑÌˆèÉ•½¹¹•ÑÌ°€‰±…ÍÑ}µ•ÍÍ…•}•Á½ ˆè9½¹”°€‰¡½Ñ}Á½Í¥Ñ¥½¹Ìˆè¡½Ñ}Á½Í¥Ñ¥½¹Ì°€‰¡½Ñ}½É‘•ÉÌˆè¡½Ñ}½É‘•ÉÍô¤(€€€€€€€€€€€¹•áÑ}Á¥¹œ€ôÑ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€¬€ÈÀ(€€€€€€€€€€€¹•áÑ}É•½¹¥±”€ôÑ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€¬€Ô(€€€€€€€€€€€İ¡¥±”ÉÕ¹¹¥¹œè(€€€€€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€€€€€É…Ü€ôİÌ¹É•Ø ¤(€€€€€€€€€€€€€€€•á•ÁĞİ•‰Í½­•Ğ¹]•‰M½­•ÑQ¥µ•½ÕÑá•ÁÑ¥½¸è(€€€€€€€€€€€€€€€€€€€É…Ü€ô9½¹”(€€€€€€€€€€€€€€€¥˜É…Üè(€€€€€€€€€€€€€€€€€€€µ•ÍÍ…”€ô©Í½¸¹±½…‘Ì¡É…Ü¤(€€€€€€€€€€€€€€€€€€€¡…¹‘±•}ÁÉ¥Ù…Ñ”¡½¹¹•Ñ¥½¸°µ•ÍÍ…”¤(€€€€€€€€€€€€€€€€€€€ÁÉ•Ù¥½ÕÌ€ôÍÑ…ÑÕÌ¹•Ğ ‰ÁÉ¥Ù…Ñ”ˆ°íô¤(€€€€€€€€€€€€€€€€€€€…Ñ½µ¥}ÍÑ…ÑÕÌ ‰ÁÉ¥Ù…Ñ”ˆ°ì‰ÍÑ…Ñ”ˆè€‰½¹¹•Ñ•ˆ°€‰½¹¹•Ñ•‘}…Ñ}•Á½ ˆèÁÉ•Ù¥½ÕÌ¹•Ğ ‰½¹¹•Ñ•‘}…Ñ}•Á½ ˆ¤°€‰É•½¹¹•ÑÌˆèÉ•½¹¹•ÑÌ°€‰±…ÍÑ}µ•ÍÍ…•}•Á½ ˆè¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤¤°€‰¡½Ñ}Á½Í¥Ñ¥½¹ÌˆèÁÉ•Ù¥½ÕÌ¹•Ğ ‰¡½Ñ}Á½Í¥Ñ¥½¹Ìˆ°€À¤°€‰¡½Ñ}½É‘•ÉÌˆèÁÉ•Ù¥½ÕÌ¹•Ğ ‰¡½Ñ}½É‘•ÉÌˆ°€À¥ô¤(€€€€€€€€€€€€€€€¥˜Ñ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€øô¹•áÑ}Á¥¹œè(€€€€€€€€€€€€€€€€€€€İÌ¹Í•¹ ì‰½Àˆè‰Á¥¹œ‰ôœ¤(€€€€€€€€€€€€€€€€€€€¹•áÑ}Á¥¹œ€ôÑ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€¬€ÈÀ(€€€€€€€€€€€€€€€¥˜Ñ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€øô¹•áÑ}É•½¹¥±”è(€€€€€€€€€€€€€€€€€€€¡½Ñ}Á½Í¥Ñ¥½¹Ì°¡½Ñ}½É‘•ÉÌ€ôÉ•½¹¥±”¡½¹¹•Ñ¥½¸°­•ä°Í•É•Ğ°€‰Á•É¥½‘¥Œˆ¤(€€€€€€€€€€€€€€€€€€€ÁÉ•Ù¥½ÕÌ€ôÍÑ…ÑÕÌ¹•Ğ ‰ÁÉ¥Ù…Ñ”ˆ°íô¤(€€€€€€€€€€€€€€€€€€€…Ñ½µ¥}ÍÑ…ÑÕÌ ‰ÁÉ¥Ù…Ñ”ˆ°ì‰ÍÑ…Ñ”ˆè€‰½¹¹•Ñ•ˆ°€‰½¹¹•Ñ•‘}…Ñ}•Á½ ˆèÁÉ•Ù¥½ÕÌ¹•Ğ ‰½¹¹•Ñ•‘}…Ñ}•Á½ ˆ¤°€‰É•½¹¹•ÑÌˆèÉ•½¹¹•ÑÌ°€‰±…ÍÑ}µ•ÍÍ…•}•Á½ ˆèÁÉ•Ù¥½ÕÌ¹•Ğ ‰±…ÍÑ}µ•ÍÍ…•}•Á½ ˆ¤°€‰¡½Ñ}Á½Í¥Ñ¥½¹Ìˆè¡½Ñ}Á½Í¥Ñ¥½¹Ì°€‰¡½Ñ}½É‘•ÉÌˆè¡½Ñ}½É‘•ÉÍô¤(€€€€€€€€€€€€€€€€€€€¹•áÑ}É•½¹¥±”€ôÑ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€¬€ Ä¥˜¡½Ñ}Á½Í¥Ñ¥½¹Ì•±Í”€Ô¤(€€€€€€€•á•ÁĞá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€É•½¹¹•ÑÌ€¬ô€Ä(€€€€€€€€€€€½¹¹•Ñ¥½¹}•Ù•¹Ğ¡½¹¹•Ñ¥½¸°€‰ÁÉ¥Ù…Ñ”ˆ°€‰É•½¹¹•Ñ¥¹œˆ°•ÉÉ½Èõ˜‰íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ôèí•áôˆ°É•½¹¹•ÑÌõÉ•½¹¹•ÑÌ¤(€€€€€€€€€€€…Ñ½µ¥}ÍÑ…ÑÕÌ ‰ÁÉ¥Ù…Ñ”ˆ°ì‰ÍÑ…Ñ”ˆè€‰É•½¹¹•Ñ¥¹œˆ°€‰É•½¹¹•ÑÌˆèÉ•½¹¹•ÑÌ°€‰•ÉÉ½Èˆè˜‰íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ôèí•áô‰ô¤(€€€€€€€€€€€Ñ¥µ”¹Í±••À¡µ¥¸ ÌÀ°É•½¹¹•ÑÌ¤¤(()‘•˜ÑÉ…‘•}±½½À¡­•äèÍÑÈ°Í•É•ĞèÍÑÈ¤€´ø9½¹”è(€€€É•½¹¹•ÑÌ€ô€À(€€€½¹¹•Ñ¥½¸€ô‘ˆ ‰É¥ÁÑ„µÁÉ¥Ù…Ñ”µÑÉ…‘”ˆ¤(€€€İ¡¥±”ÉÕ¹¹¥¹œè(€€€€€€€ÑÉäè(€€€€€€€€€€€İÌ€ôİ•‰Í½­•Ğ¹É•…Ñ•}½¹¹•Ñ¥½¸¡QI}UI0°Ñ¥µ•½ÕĞôÄÀ°•¹…‰±•}µÕ±Ñ¥Ñ¡É•…õ…±Í”¤(€€€€€€€€€€€İÌ¹Í•ÑÑ¥µ•½ÕĞ Ä¤(€€€€€€€€€€€…ÕÑ ¡İÌ°­•ä°Í•É•Ğ¤(€€€€€€€€€€€½¹¹•Ñ¥½¹}•Ù•¹Ğ¡½¹¹•Ñ¥½¸°€‰ÑÉ…‘”ˆ°€‰…ÕÑ¡•¹Ñ¥…Ñ•‘}¹½}½µµ…¹‘Ìˆ°É•½¹¹•ÑÌõÉ•½¹¹•ÑÌ¤(€€€€€€€€€€€½¹¹•Ñ•€ô¥¹Ğ¡Ñ¥µ”¹Ñ¥µ” ¤¤(€€€€€€€€€€€…Ñ½µ¥}ÍÑ…ÑÕÌ ‰ÑÉ…‘”ˆ°ì‰ÍÑ…Ñ”ˆè€‰…ÕÑ¡•¹Ñ¥…Ñ•µ±½­•ˆ°€‰½¹¹•Ñ•‘}…Ñ}•Á½ ˆè½¹¹•Ñ•°€‰É•½¹¹•ÑÌˆèÉ•½¹¹•ÑÌ°€‰½µµ…¹‘Í}Í•¹Ğˆè€Áô¤(€€€€€€€€€€€¹•áÑ}Á¥¹œ€ôÑ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€¬€ÈÀ(€€€€€€€€€€€İ¡¥±”ÉÕ¹¹¥¹œè(€€€€€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€€€€€İÌ¹É•Ø ¤(€€€€€€€€€€€€€€€•á•ÁĞİ•‰Í½­•Ğ¹]•‰M½­•ÑQ¥µ•½ÕÑá•ÁÑ¥½¸è(€€€€€€€€€€€€€€€€€€€Á…ÍÌ(€€€€€€€€€€€€€€€¥˜Ñ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€øô¹•áÑ}Á¥¹œè(€€€€€€€€€€€€€€€€€€€İÌ¹Í•¹ ì‰½Àˆè‰Á¥¹œ‰ôœ¤(€€€€€€€€€€€€€€€€€€€¹•áÑ}Á¥¹œ€ôÑ¥µ”¹µ½¹½Ñ½¹¥Œ ¤€¬€ÈÀ(€€€€€€€•á•ÁĞá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€É•½¹¹•ÑÌ€¬ô€Ä(€€€€€€€€€€€½¹¹•Ñ¥½¹}•Ù•¹Ğ¡½¹¹•Ñ¥½¸°€‰ÑÉ…‘”ˆ°€‰É•½¹¹•Ñ¥¹œˆ°•ÉÉ½Èõ˜‰íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ôèí•áôˆ°É•½¹¹•ÑÌõÉ•½¹¹•ÑÌ¤(€€€€€€€€€€€…Ñ½µ¥}ÍÑ…ÑÕÌ ‰ÑÉ…‘”ˆ°ì‰ÍÑ…Ñ”ˆè€‰É•½¹¹•Ñ¥¹œˆ°€‰É•½¹¹•ÑÌˆèÉ•½¹¹•ÑÌ°€‰½µµ…¹‘Í}Í•¹Ğˆè€À°€‰•ÉÉ½Èˆè˜‰íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ôèí•áô‰ô¤(€€€€€€€€€€€Ñ¥µ”¹Í±••À¡µ¥¸ ÌÀ°É•½¹¹•ÑÌ¤¤(()‘•˜µ…¥¸ ¤€´ø9½¹”è(€€€±½‰…°ÉÕ¹¹¥¹œ(€€€MQQUL¹Á…É•¹Ğ¹µ­‘¥È¡Á…É•¹ÑÌõQÉÕ”°•á¥ÍÑ}½¬õQÉÕ”¤(€€€É•‘•¹Ñ¥…±Ì€ô©Í½¸¹±½…‘Ì ¡A…Ñ ¡½Ì¹•¹Ù¥É½¹l‰I9Q%1M}%IQ=Id‰t¤€¼€‰‰å‰¥Ğµµ…¥¹¹•Ğˆ¤¹É•…‘}Ñ•áĞ¡•¹½‘¥¹œô‰ÕÑ˜´àˆ¤¤(€€€‰½½ÑÍÑÉ…À€ô‘ˆ ‰É¥ÁÑ„µÁÉ¥Ù…Ñ”µ‰½½ÑÍÑÉ…Àˆ¤(€€€‘¥Í…Éµ}¹•İ}•¹ÑÉ¥•Ì (€€€€€€€‰½½ÑÍÑÉ…À°(€€€€€€€€‰É•ÍÑ…ÉĞèÍ¡•µ„Ù…±¥‘…Ñ¥½¸Á•¹‘¥¹œì½İ¹•ÈÉ”µ…É´É•ÅÕ¥É•ˆ°(€€€€¤(€€€ÑÉäè(€€€€€€€Ù…±¥‘…Ñ•}ÉÕ¹Ñ¥µ•}Í¡•µ…}½¹ÑÉ…Ğ¡‰½½ÑÍÑÉ…À¤(€€€•á•ÁĞá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€…Ñ½µ¥}ÍÑ…ÑÕÌ (€€€€€€€€€€€€‰Í¡•µ„ˆ°(€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€‰ÍÑ…Ñ”ˆè€‰	1=-ˆ°(€€€€€€€€€€€€€€€€‰•áÁ•Ñ•‘}Ù•ÉÍ¥½¸ˆèaAQ}IU9Q%5}M!5}YIM%=8°(€€€€€€€€€€€€€€€€‰•ÉÉ½Èˆè˜‰íÑåÁ”¡•áŒ¤¹}}¹…µ•}}ôèí•áôˆ°(€€€€€€€€€€€ô°(€€€€€€€€¤(€€€€€€€‰½½ÑÍÑÉ…À¹±½Í” ¤(€€€€€€€É…¥Í”(€€€…Ñ½µ¥}ÍÑ…ÑÕÌ (€€€€€€€€‰Í¡•µ„ˆ°(€€€€€€€ì(€€€€€€€€€€€€‰ÍÑ…Ñ”ˆè€‰Idˆ°(€€€€€€€€€€€€‰Ù•ÉÍ¥½¸ˆèaAQ}IU9Q%5}M!5}YIM%=8°(€€€€€€€ô°(€€€€¤(€€€ÍÑ…ÉÑÕÁ}±¥Ù•}Í…™•Ñä¡‰½½ÑÍÑÉ…À°É•‘•¹Ñ¥…±Íl‰…Á¥}­•ä‰t°É•‘•¹Ñ¥…±Íl‰…Á¥}Í•É•Ğ‰t¤(€€€‰½½ÑÍÑÉ…À¹±½Í” ¤(€€€Í¥¹…°¹Í¥¹…°¡Í¥¹…°¹M%QI4°±…µ‰‘„€©|è±½‰…±Ì ¤¹}}Í•Ñ¥Ñ•µ}| ‰ÉÕ¹¹¥¹œˆ°…±Í”¤¤(€€€Í¥¹…°¹Í¥¹…°¡Í¥¹…°¹M%%9P°±…µ‰‘„€©|è±½‰…±Ì ¤¹}}Í•Ñ¥Ñ•µ}| ‰ÉÕ¹¹¥¹œˆ°…±Í”¤¤(€€€Ñ¡É•…€ôÑ¡É•…‘¥¹œ¹Q¡É•…¡Ñ…É•ĞõÑÉ…‘•}±½½À°…ÉÌô¡É•‘•¹Ñ¥…±Íl‰…Á¥}­•ä‰t°É•‘•¹Ñ¥…±Íl‰…Á¥}Í•É•Ğ‰t¤°‘…•µ½¸õQÉÕ”¤(€€€Ñ¡É•…¹ÍÑ…ÉĞ ¤(€€€½µµ…¹‘Ì€ôÑ¡É•…‘¥¹œ¹Q¡É•…¡Ñ…É•Ğõ½µµ…¹‘}±½½À°…ÉÌô¡É•‘•¹Ñ¥…±Íl‰…Á¥}­•ä‰t°É•‘•¹Ñ¥…±Íl‰…Á¥}Í•É•Ğ‰t¤°‘…•µ½¸õQÉÕ”¤(€€€½µµ…¹‘Ì¹ÍÑ…ÉĞ ¤(€€€ÁÉ¥Ù…Ñ•}±½½À¡É•‘•¹Ñ¥…±Íl‰…Á¥}­•ä‰t°É•‘•¹Ñ¥…±Íl‰…Á¥}Í•É•Ğ‰t¤(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€µ…¥¸ ¤