from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
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
PROCESS_STARTED_AT_MS = int(time.time() * 1000)
RECONCILIATION_MAX_AGE_MS = int(
    os.environ.get("CRIPTA_RECONCILIATION_MAX_AGE_MS", "15000")
)


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


def db() -> psycopg.Connection:
    return psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql")


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
            "SELECT 1 FROM runtime.executions WHERE order_link_id=%s LIMIT 1", (command_id,)
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


def startup_live_safety(
    connection: psycopg.Connection, key: str, secret: str
) -> None:
    """Synchronously fail-close new Entry before any command worker can start."""
    disarm_new_entries(connection, "restart: owner re-arm required")
    reconcile(connection, key, secret, "startup_preflight")
    refresh_recent_executions(connection, key, secret)
    cancel_bot_owned_pending_entry_orders(connection, key, secret)
    resolve_prestart_entry_commands(connection)
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
        reason = "До сигнала нет причинно допустимой оценки Диспетчера; вход не блокируется"
        assessment_id = mayak_id = observed_at = version = quality = market_context_id = None
        age_seconds = None
        freshness = "MISSING"
        payload: object = {}
    else:
        assessment_id, mayak_id, observed_at, version, status, quality, payload, market_context_id = row
        age_seconds = (signal_at - observed_at).total_seconds()
        freshness = "FRESH" if 0 <= age_seconds <= 90 else "STALE"
        reason = "Причинная оценка Диспетчера сохранена только как контекст"
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


def api_post(path: str, params: dict[str, object], key: str, secret: str, *, accepted_codes: tuple[int, ...] = ()) -> dict[str, object]:
    body = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
    timestamp, recv_window = str(int(time.time() * 1000)), "5000"
    signature = hmac.new(secret.encode(), f"{timestamp}{key}{recv_window}{body}".encode(), hashlib.sha256).hexdigest()
    request = urllib.request.Request(REST_URL + path, data=body.encode(), method="POST", headers={
        "Content-Type": "application/json", "X-BAPI-API-KEY": key, "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window, "X-BAPI-SIGN": signature, "User-Agent": "cripta-live-executor/1",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(
            f"Bybit HTTP {exc.code} for {path}: {response_body or exc.reason}"
        ) from exc
    if payload.get("retCode") != 0 and payload.get("retCode") not in accepted_codes:
        raise RuntimeError(str(payload.get("retMsg") or "Bybit rejected command"))
    return payload


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


def execute_command(connection: psycopg.Connection, key: str, secret: str, row: tuple[object, ...]) -> None:
    command_id, kind, symbol, raw_payload = map(str, row)
    payload = json.loads(raw_payload)
    positions, _ = api_get("/v5/position/list", {"category": "linear", "symbol": symbol}, key, secret)
    position = next((p for p in ((positions.get("result") or {}).get("list") or []) if Decimal(str(p.get("size") or 0)) > 0), None)
    instruments, _ = api_get("/v5/market/instruments-info", {"category": "linear", "symbol": symbol})
    instrument = ((instruments.get("result") or {}).get("list") or [{}])[0]
    tick = Decimal(str((instrument.get("priceFilter") or {}).get("tickSize") or "0"))
    if tick > 0:
        _tick_cache[symbol] = tick
    qty_step = Decimal(str((instrument.get("lotSizeFilter") or {}).get("qtyStep") or "0"))
    if tick <= 0 or qty_step <= 0:
        raise RuntimeError("Bybit did not return price/quantity steps")
    result: dict[str, object]
    if kind == "close":
        if not position: raise RuntimeError("open position not found")
        result = api_post("/v5/order/create", {"category":"linear","symbol":symbol,"side":"Sell" if position["side"]=="Buy" else "Buy","orderType":"Market","qty":str(position["size"]),"positionIdx":int(position.get("positionIdx") or 0),"orderLinkId":command_id[:36],"reduceOnly":True,"closeOnTrigger":False}, key, secret)
    elif kind in {"break_even", "current_stop", "initial_protection"}:
        if not position: raise RuntimeError("open position not found")
        side = str(position["side"])
        mark = executable_close_price(symbol, side)
        if kind == "break_even":
            plan = protection_plan(connection, symbol, position, tick)
            stop, activation = plan["stop"], plan["activation"]
            if (side == "Buy" and mark < activation) or (side == "Sell" and mark > activation):
                raise RuntimeError(f"price has not reached calculated protection activation {activation}")
        elif kind == "current_stop":
            stop = quantize(mark * (Decimal("0.998") if side=="Buy" else Decimal("1.002")), tick, upward=side!="Buy")
        else:
            actual_entry = Decimal(str(position.get("avgPrice") or 0))
            if actual_entry <= 0:
                raise RuntimeError("Bybit did not return actual average entry price")
            stop, target = calculate_initial_boundaries(
                entry=actual_entry, side=side, tick=tick
            )
        if (side=="Buy" and stop >= mark) or (side=="Sell" and stop <= mark): raise RuntimeError("calculated stop is already beyond current price")
        stop_request: dict[str, object] = {"category":"linear","symbol":symbol,"positionIdx":int(position.get("positionIdx") or 0),"tpslMode":"Full","stopLoss":str(stop),"slTriggerBy":"LastPrice","slOrderType":"Market"}
        if kind == "initial_protection":
            stop_request.update({"takeProfit": str(target), "tpTriggerBy": "LastPrice", "tpOrderType": "Market"})
        result = api_post("/v5/position/trading-stop", stop_request, key, secret)
        if kind == "break_even":
            result["protectionPlan"] = {name: str(value) for name, value in plan.items()}
        elif kind == "initial_protection":
            result["actualProtection"] = {"entryPrice": str(actual_entry), "stopLoss": str(stop), "takeProfit": str(target)}
    elif kind == "trailing_stop":
        if not position: raise RuntimeError("open position not found")
        enabled = bool(payload.get("enabled"))
        params: dict[str, object] = {"category":"linear","symbol":symbol,"positionIdx":int(position.get("positionIdx") or 0),"tpslMode":"Full","slTriggerBy":"LastPrice"}
        if enabled:
            distance_pct = Decimal(str(payload.get("distance_pct") or "0.2"))
            if distance_pct < Decimal("0.05") or distance_pct > Decimal("5"):
                raise RuntimeError("trailing stop distance must be from 0.05% to 5%")
            mark = executable_close_price(symbol, str(position["side"]))
            distance = quantize(mark * distance_pct / Decimal("100"), tick, upward=True)
            plan = protection_plan(connection, symbol, position, tick)
            if not trailing_start_preserves_protection(
                side=str(position["side"]),
                mark=mark,
                distance=distance,
                protected_stop=plan["stop"],
            ):
                raise RuntimeError(
                    "trailing stop is blocked: its initial stop would not preserve calculated net profit"
                )
            params["trailingStop"] = str(max(distance, tick))
        else:
            params["trailingStop"] = "0"
        try:
            result = api_post("/v5/position/trading-stop", params, key, secret)
        except RuntimeError as exc:
            if "not modified" not in str(exc).lower():
                raise
            result = {"retCode": 0, "retMsg": "not modified", "idempotent": True}
    elif kind == "entry":
        if position: raise RuntimeError("position already exists")
        existing_orders, _ = api_get(
            "/v5/order/realtime",
            {"category": "linear", "symbol": symbol, "openOnly": "0", "limit": "50"},
            key,
            secret,
        )
        active_entry = next(
            (
                order
                for order in ((existing_orders.get("result") or {}).get("list") or [])
                if order.get("orderStatus") in {"New", "PartiallyFilled", "Untriggered"}
                and not bool(order.get("reduceOnly"))
            ),
            None,
        )
        if active_entry:
            raise RuntimeError("по монете уже существует незавершённая заявка на вход")
        stake, leverage, side, signal_price = Decimal(str(payload["stake_usdt"])), int(payload["leverage"]), str(payload["side"]), Decimal(str(payload["price"]))
        offset = Decimal(str(payload.get("entry_offset_pct") or 0)) / Decimal("100")
        price = signal_price * (Decimal("1") - offset if side == "Buy" else Decimal("1") + offset)
        price = quantize(price, tick, upward=side == "Sell")
        wallet, _ = api_get("/v5/account/wallet-balance", {"accountType":"UNIFIED"}, key, secret); account=((wallet.get("result") or {}).get("list") or [{}])[0]
        available=account_available_usdt(account)
        if available < stake: raise RuntimeError("недостаточно доступного баланса")
        qty=quantize(stake*Decimal(leverage)/price,qty_step)
        if qty<=0: raise RuntimeError("calculated quantity is below exchange step")
        api_post("/v5/position/set-leverage", {"category":"linear","symbol":symbol,"buyLeverage":str(leverage),"sellLeverage":str(leverage)}, key, secret, accepted_codes=(110043,))
        # Стоп округляется к входу: фактический риск не должен стать глубже 1%.
        stop,target=calculate_initial_boundaries(entry=price,side=side,tick=tick)
        # Границы ставятся только после подтверждённого исполнения Bybit, когда
        # известна фактическая средняя цена позиции.
        order={"category":"linear","symbol":symbol,"side":side,"orderType":"Market" if offset == 0 else "Limit","qty":str(qty),"positionIdx":0,"orderLinkId":command_id[:36]}
        if offset > 0:
            order.update({"price": str(price), "timeInForce": "GTC"})
        result=api_post("/v5/order/create", order, key, secret)
        if offset == 0:
            filled_position = None
            for _ in range(20):
                current, _ = api_get(
                    "/v5/position/list", {"category": "linear", "symbol": symbol}, key, secret
                )
                filled_position = next(
                    (
                        item
                        for item in ((current.get("result") or {}).get("list") or [])
                        if Decimal(str(item.get("size") or 0)) > 0
                    ),
                    None,
                )
                if filled_position:
                    break
                time.sleep(0.25)
            if not filled_position:
                raise RuntimeError("рыночный вход принят, но фактическая цена исполнения ещё не подтверждена")
            actual_entry = Decimal(str(filled_position.get("avgPrice") or 0))
            if actual_entry <= 0:
                raise RuntimeError("Bybit не вернул фактическую цену исполнения")
            actual_stop, actual_target = calculate_initial_boundaries(
                entry=actual_entry, side=side, tick=tick
            )
            protection = api_post(
                "/v5/position/trading-stop",
                {
                    "category": "linear", "symbol": symbol,
                    "positionIdx": int(filled_position.get("positionIdx") or 0),
                    "tpslMode": "Full", "stopLoss": str(actual_stop),
                    "takeProfit": str(actual_target), "slTriggerBy": "LastPrice",
                    "tpTriggerBy": "LastPrice", "slOrderType": "Market",
                    "tpOrderType": "Market",
                },
                key,
                secret,
            )
            result["actualProtection"] = {
                "entryPrice": str(actual_entry), "stopLoss": str(actual_stop),
                "takeProfit": str(actual_target), "exchange": protection.get("retMsg"),
            }
    else: raise RuntimeError("unknown command type")
    before_position = None if position is None else dict(position)
    reconcile(connection,key,secret,"after_command")
    record_protection_or_owner_event(
        connection, command_id, kind, symbol, payload, before_position
    )
    connection.execute("UPDATE runtime.trade_commands SET state='completed',finished_at_epoch_ms=%s,result_json=%s WHERE command_id=%s",(int(time.time()*1000),json.dumps(result,ensure_ascii=False),command_id)); connection.commit()


def cancel_expired_entry_limits(
    connection: psycopg.Connection, key: str, secret: str, now_ms: int
) -> None:
    rows = connection.execute("""SELECT o.order_id,o.symbol,c.requested_at_epoch_ms,c.payload_json
        FROM runtime.hot_orders o JOIN runtime.trade_commands c ON o.order_link_id=c.command_id
        WHERE c.command_type='entry' AND c.state='completed'""").fetchall()
    for order_id, symbol, requested_at, raw_payload in rows:
        payload = json.loads(raw_payload)
        if Decimal(str(payload.get("entry_offset_pct") or 0)) <= 0:
            continue
        ttl_ms = int(payload.get("entry_limit_ttl_seconds") or 30) * 1000
        if now_ms - int(requested_at) < ttl_ms:
            continue
        api_post(
            "/v5/order/cancel",
            {"category": "linear", "symbol": symbol, "orderId": order_id},
            key,
            secret,
            accepted_codes=(110001,),
        )
        reconcile(connection, key, secret, "expired_entry_limit")


def command_worker_loop(key: str, secret: str) -> None:
    connection=db()
    next_limit_cleanup = 0.0
    next_heartbeat = 0.0
    while running:
        if time.monotonic() >= next_heartbeat:
            atomic_status(
                "command",
                {"state": "running", "heartbeat_epoch": int(time.time())},
            )
            next_heartbeat = time.monotonic() + 5
        gate=connection.execute("SELECT enabled,updated_at_epoch_ms FROM control.execution_gates WHERE mode='mainnet'").fetchone()
        settings=connection.execute("SELECT stake_usdt,leverage,enabled_symbols_json,updated_at_epoch_ms,entry_offset_pct,entry_limit_ttl_seconds,auto_profit_protection,auto_trailing_stop,trailing_distance_pct,entry_policy FROM runtime.trade_settings WHERE singleton=1").fetchone()
        if settings:
            gate_enabled = bool(gate and gate[0])
            configured_entry_policy = str(settings[9] or "base_entry_v1")
            entry_policy = configured_entry_policy
            configured=set(json.loads(settings[2]))
            enabled=configured - EXCLUDED_TRADING_SYMBOLS
            now_ms=int(time.time()*1000)
            if time.monotonic() >= next_limit_cleanup:
                try:
                    cancel_expired_entry_limits(connection, key, secret, now_ms)
                except Exception:
                    connection.rollback()
                next_limit_cleanup = time.monotonic() + 1
            pickup_window_ms = 10_000 if entry_policy == "base_entry_v1" else SIGNAL_PICKUP_WINDOW_MS
            fresh_after=max(
                now_ms-pickup_window_ms,
                int(gate[1] or 0),
                int(settings[3] or 0),
            )
            signals=connection.execute("""SELECT signal_id,symbol,direction,signal_price,signal_at_epoch_ms FROM monitoring.opportunities
                WHERE bot_id='entry-v1-shadow' AND decision='shadow' AND signal_at_epoch_ms >= %s
                ORDER BY signal_at_epoch_ms DESC LIMIT 100""",(fresh_after,)).fetchall()
            for signal_id,symbol,direction,price,signal_at_ms in signals:
                observed_context = None
                if symbol in EXCLUDED_TRADING_SYMBOLS:
                    record_entry_decision(connection, signal_id, symbol, direction, signal_at_ms,
                                          "запрещён", "монета находится в карантине")
                    continue
                if symbol not in enabled:
                    record_entry_decision(connection, signal_id, symbol, direction, signal_at_ms,
                                          "запрещён", "монета выключена в торговых настройках")
                    continue
                if entry_policy == "m3_full_live_v1":
                    observed_context = observe_m3_entry_context(
                        connection,
                        signal_id=str(signal_id), symbol=str(symbol),
                        direction=str(direction), signal_at_ms=int(signal_at_ms),
                    )
                occupied=connection.execute("""SELECT
                    EXISTS(SELECT 1 FROM runtime.hot_positions WHERE symbol=%s) OR
                    EXISTS(SELECT 1 FROM runtime.hot_orders WHERE symbol=%s AND order_status IN ('New','PartiallyFilled','Untriggered')) OR
                    EXISTS(SELECT 1 FROM runtime.trade_commands WHERE symbol=%s AND command_type='entry' AND state IN ('queued','running'))""",(symbol,symbol,symbol)).fetchone()[0]
                if occupied:
                    record_entry_decision(connection, signal_id, symbol, direction, signal_at_ms,
                                          "запрещён", "по монете уже есть позиция, заявка или команда")
                    continue
                if not gate_enabled:
                    record_entry_decision(
                        connection, signal_id, symbol, direction, signal_at_ms,
                        "теневой допуск",
                        "все проверки пройдены, но реальный торговый шлюз закрыт",
                    )
                    continue
                geometry = connection.execute(
                    """SELECT geometry_handoff_id,strategy_id,strategy_version,payload
                       FROM monitoring.entry_geometry_handoffs WHERE signal_id=%s""",
                    (signal_id,),
                ).fetchone()
                if entry_policy == "m3_full_live_v1" and geometry is None:
                    record_entry_decision(
                        connection, signal_id, symbol, direction, signal_at_ms,
                        "запрещён",
                        "нет причинной неизменяемой геометрии Entry",
                        entry_policy=entry_policy,
                        policy_version="1.0.0-owner-live",
                    )
                    continue
                cid="auto-"+hashlib.sha256(str(signal_id).encode()).hexdigest()[:28]
                body={"stake_usdt":settings[0],"leverage":settings[1],"side":"Buy" if direction=="long" else "Sell","price":price,"signal_id":signal_id,"entry_offset_pct":settings[4],"entry_limit_ttl_seconds":settings[5],"entry_policy":entry_policy,"policy_version":"1.0.0-owner-live" if entry_policy=="m3_full_live_v1" else "entry-policy-v1","bot_instance_id":BOT_INSTANCE_ID,"geometry_handoff_id":None if geometry is None else geometry[0]}
                connection.execute("""INSERT INTO runtime.trade_commands(command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms)
                    VALUES(%s,'entry',%s,%s,'queued',%s) ON CONFLICT(command_id) DO NOTHING""",(cid,symbol,json.dumps(body),int(time.time()*1000)))
                if geometry is not None:
                    connection.execute(
                        """INSERT INTO runtime.entry_geometry_bindings(
                            entry_command_id,geometry_handoff_id,signal_id,bot_instance_id,
                            strategy_id,strategy_version,payload)
                            VALUES(%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT(entry_command_id) DO NOTHING""",
                        (
                            cid, geometry[0], signal_id, BOT_INSTANCE_ID,
                            geometry[1], geometry[2],
                            json.dumps(geometry[3], ensure_ascii=False, default=str),
                        ),
                    )
                record_entry_decision(connection, signal_id, symbol, direction, signal_at_ms,
                                      "разрешён", "проверки пройдены, команда поставлена в очередь",
                                      command_id=cid, entry_policy=entry_policy,
                                      policy_version="1.0.0-owner-live" if entry_policy=="m3_full_live_v1" else "entry-policy-v1",
                                      observed_context=observed_context)
            connection.commit()
        filled_entries=connection.execute("""SELECT c.command_id,c.symbol,max(e.exec_time_ms)
            FROM runtime.trade_commands c JOIN runtime.executions e ON e.order_link_id=c.command_id
            WHERE c.command_type='entry' AND c.state='completed'
              AND COALESCE((e.payload_json::jsonb->>'closedSize')::numeric,0)=0
            GROUP BY c.command_id,c.symbol""").fetchall()
        for entry_id,symbol,fill_time_ms in filled_entries:
            position_row=connection.execute(
                "SELECT side,size,entry_price,payload_json FROM runtime.hot_positions WHERE symbol=%s",
                (symbol,),
            ).fetchone()
            if not position_row:
                continue
            raw=json.loads(position_row[3])
            open_time_ms=int(raw.get("openTime") or 0)
            if open_time_ms and abs(open_time_ms-int(fill_time_ms or 0)) > 10_000:
                continue
            actual_entry=Decimal(str(position_row[2]))
            execution_rows = connection.execute(
                """SELECT exec_id,order_id,order_link_id,exec_time_ms
                   FROM runtime.executions WHERE order_link_id=%s
                   ORDER BY exec_time_ms,exec_id""",
                (entry_id,),
            ).fetchall()
            binding = connection.execute(
                """SELECT geometry_handoff_id,signal_id,bot_instance_id,
                          strategy_id,strategy_version
                   FROM runtime.entry_geometry_bindings WHERE entry_command_id=%s""",
                (entry_id,),
            ).fetchone()
            if binding is not None and execution_rows:
                position_idx = int(raw.get("positionIdx") or 0)
                first_execution_id = str(execution_rows[0][0])
                first_fill_ms = int(execution_rows[0][3] or fill_time_ms)
                position_id, trade_id = stable_cycle_ids(
                    entry_command_id=str(entry_id),
                    first_execution_id=first_execution_id,
                    symbol=str(symbol),
                    side=str(position_row[0]),
                    position_idx=position_idx,
                )
                connection.execute(
                    """INSERT INTO runtime.position_ownership(
                        position_id,trade_id,bot_instance_id,strategy_id,strategy_version,
                        signal_id,entry_command_id,geometry_handoff_id,symbol,side,
                        actual_avg_fill,actual_qty,fill_at,exchange_order_ids,
                        client_order_ids,execution_ids,exchange_position_key,position_idx)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               to_timestamp(%s/1000.0),%s,%s,%s,%s,%s)
                        ON CONFLICT(entry_command_id) DO UPDATE SET
                          actual_avg_fill=excluded.actual_avg_fill,
                          actual_qty=excluded.actual_qty,
                          exchange_order_ids=excluded.exchange_order_ids,
                          client_order_ids=excluded.client_order_ids,
                          execution_ids=excluded.execution_ids,
                          exchange_position_key=excluded.exchange_position_key,
                          position_idx=excluded.position_idx""",
                    (
                        position_id, trade_id, binding[2], binding[3], binding[4],
                        binding[1], entry_id, binding[0], symbol, position_row[0],
                        actual_entry, Decimal(str(position_row[1])), first_fill_ms,
                        json.dumps(sorted({str(row[1]) for row in execution_rows})),
                        json.dumps(sorted({str(row[2]) for row in execution_rows})),
                        json.dumps([str(row[0]) for row in execution_rows]),
                        f"BYBIT:UNIFIED:LINEAR:USDT:{symbol}:{position_idx}",
                        position_idx,
                    ),
                )
            current_stop=Decimal(str(raw.get("stopLoss") or 0))
            profit_already_protected=current_stop > 0 and (
                (position_row[0] == "Buy" and current_stop >= actual_entry)
                or (position_row[0] == "Sell" and current_stop <= actual_entry)
            )
            if profit_already_protected or Decimal(str(raw.get("trailingStop") or 0)) > 0:
                continue
            protection_key=f"{entry_id}:{position_row[1]}:{position_row[2]}"
            init_id="auto-init-"+hashlib.sha256(protection_key.encode()).hexdigest()[:23]
            connection.execute("""INSERT INTO runtime.trade_commands(command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms)
                VALUES(%s,'initial_protection',%s,%s,'queued',%s) ON CONFLICT(command_id) DO NOTHING""",
                (init_id,symbol,json.dumps({"entry_command_id":entry_id,"actual_entry":position_row[2],"actual_size":position_row[1]}),int(time.time()*1000)))
        connection.commit()
        owned_entries = connection.execute(
            """SELECT o.entry_command_id,o.symbol
               FROM runtime.position_ownership o
               JOIN runtime.hot_positions p
                 ON p.symbol=o.symbol AND p.position_idx=o.position_idx
                AND p.side=o.side
               WHERE o.state='OPEN' AND o.close_link_status='OPEN'"""
        ).fetchall()
        protection_entries = owned_entries if settings and settings[6] else []
        for entry_id,symbol in protection_entries:
            position_row=connection.execute("SELECT side,entry_price,payload_json FROM runtime.hot_positions WHERE symbol=%s",(symbol,)).fetchone()
            if not position_row: continue
            raw=json.loads(position_row[2]); entry=Decimal(str(position_row[1])); mark=executable_close_price(str(symbol), str(position_row[0]))
            if entry<=0 or mark<=0: continue
            move=(mark/entry-Decimal("1"))*Decimal("100")*(Decimal("1") if position_row[0]=="Buy" else Decimal("-1"))
            tick = _tick_cache.get(str(symbol))
            if not tick:
                instruments, _ = api_get("/v5/market/instruments-info", {"category":"linear","symbol":symbol})
                instrument = ((instruments.get("result") or {}).get("list") or [{}])[0]
                tick = Decimal(str((instrument.get("priceFilter") or {}).get("tickSize") or "0"))
                if tick <= 0: continue
                _tick_cache[str(symbol)] = tick
            position = dict(raw); position.update({"side":position_row[0],"avgPrice":str(entry)})
            plan = protection_plan(connection, str(symbol), position, tick)
            activation = plan["activation"]
            existing_stop = Decimal(str(raw.get("stopLoss") or 0))
            already_protected = existing_stop > 0 and (
                (position_row[0] == "Buy" and existing_stop >= plan["stop"])
                or (position_row[0] == "Sell" and existing_stop <= plan["stop"])
            )
            if already_protected:
                continue
            if (position_row[0] == "Buy" and mark < activation) or (position_row[0] == "Sell" and mark > activation):
                continue
            retry_bucket = int(time.time() // 5)
            protection_key = f"{symbol}:{position_row[0]}:{entry}:{retry_bucket}"
            be_id="auto-be-"+hashlib.sha256(protection_key.encode()).hexdigest()[:25]
            connection.execute("""INSERT INTO runtime.trade_commands(command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms)
                VALUES(%s,'break_even',%s,%s,'queued',%s) ON CONFLICT(command_id) DO NOTHING""",(be_id,symbol,json.dumps({"entry_command_id":entry_id,"activation_move_pct":str(move),"calculated_stop":str(plan["stop"]),"minimum_fill":str(plan["minimum_fill"]),"entry_fee":str(plan["entry_fee"]),"slippage_reserve":str(plan["slippage"])}),int(time.time()*1000)))
        connection.commit()
        if settings and settings[7]:
            trailing_pct = Decimal(str(settings[8] or "0.30"))
            for entry_id, symbol in owned_entries:
                position_row = connection.execute(
                    "SELECT side,entry_price,payload_json FROM runtime.hot_positions WHERE symbol=%s",
                    (symbol,),
                ).fetchone()
                if not position_row:
                    continue
                raw = json.loads(position_row[2])
                if Decimal(str(raw.get("trailingStop") or 0)) > 0:
                    continue
                entry = Decimal(str(position_row[1]))
                mark = executable_close_price(str(symbol), str(position_row[0]))
                if entry <= 0 or mark <= 0:
                    continue
                side = str(position_row[0])
                tick = _tick_cache.get(str(symbol))
                if not tick:
                    instruments, _ = api_get(
                        "/v5/market/instruments-info",
                        {"category": "linear", "symbol": symbol},
                    )
                    instrument = ((instruments.get("result") or {}).get("list") or [{}])[0]
                    tick = Decimal(str((instrument.get("priceFilter") or {}).get("tickSize") or "0"))
                    if tick <= 0:
                        continue
                    _tick_cache[str(symbol)] = tick
                position = dict(raw)
                position.update({"side": side, "avgPrice": str(entry)})
                required_protection = protection_plan(
                    connection, str(symbol), position, tick
                )["stop"]
                distance = mark * trailing_pct / Decimal("100")
                if not trailing_start_preserves_protection(
                    side=side,
                    mark=mark,
                    distance=distance,
                    protected_stop=required_protection,
                ):
                    continue
                open_time = int(raw.get("openTime") or 0)
                disabled = connection.execute(
                    """SELECT 1 FROM runtime.trade_commands
                       WHERE symbol=%s AND command_type='trailing_stop'
                         AND command_id LIKE 'web-%%'
                         AND requested_at_epoch_ms >= %s
                         AND payload_json::jsonb->>'enabled'='false'
                       ORDER BY requested_at_epoch_ms DESC LIMIT 1""",
                    (symbol, open_time),
                ).fetchone()
                if disabled:
                    continue
                already_enabled = connection.execute(
                    """SELECT 1 FROM runtime.trade_commands
                       WHERE symbol=%s AND command_type='trailing_stop'
                         AND state='completed' AND requested_at_epoch_ms >= %s
                         AND payload_json::jsonb->>'enabled'='true'
                         AND payload_json::jsonb->>'distance_pct'=%s
                       LIMIT 1""",
                    (symbol, open_time, str(trailing_pct)),
                ).fetchone()
                if already_enabled:
                    continue
                retry_bucket = int(time.time() // 5)
                idempotency_key = (
                    f"{entry_id}:{side}:{entry}:{trailing_pct}:{retry_bucket}"
                )
                command_id = (
                    "auto-trail-"
                    + hashlib.sha256(idempotency_key.encode()).hexdigest()[:21]
                )
                connection.execute(
                    """INSERT INTO runtime.trade_commands(
                           command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms)
                       VALUES(%s,'trailing_stop',%s,%s,'queued',%s)
                       ON CONFLICT(command_id) DO NOTHING""",
                    (command_id, symbol, json.dumps({"enabled": True, "distance_pct": str(trailing_pct), "source": "after_profit_protection"}), int(time.time()*1000)),
                )
            connection.commit()
        row=connection.execute("""SELECT command_id,command_type,symbol,payload_json FROM runtime.trade_commands
            WHERE state='queued' ORDER BY (left(command_id,4)='web-') DESC, requested_at_epoch_ms LIMIT 1""").fetchone()
        if not row: time.sleep(0.25); continue
        command_id=str(row[0])
        if str(row[1]) == "entry":
            ready, readiness_reason = entry_runtime_readiness(connection)
            if not ready:
                connection.execute(
                    """UPDATE runtime.trade_commands
                       SET state='failed',finished_at_epoch_ms=%s,error=%s
                       WHERE command_id=%s AND state='queued'""",
                    (int(time.time()*1000), f"ENTRY_BLOCKED:{readiness_reason}", command_id),
                )
                connection.commit()
                continue
        connection.execute(
            """UPDATE runtime.trade_commands
               SET state='running',started_at_epoch_ms=%s
               WHERE command_id=%s AND state='queued'""",
            (int(time.time() * 1000), command_id),
        )
        connection.commit()
        try: execute_command(connection,key,secret,row)
        except Exception as exc:
            connection.rollback(); connection.execute("UPDATE runtime.trade_commands SET state='failed',finished_at_epoch_ms=%s,error=%s WHERE command_id=%s",(int(time.time()*1000),f"{type(exc).__name__}: {exc}",command_id)); connection.commit()


def command_loop(key: str, secret: str) -> None:
    """Keep the command worker alive and make an internal failure visible."""
    while running:
        try:
            atomic_status("command", {"state": "running", "heartbeat_epoch": int(time.time())})
            command_worker_loop(key, secret)
        except Exception as exc:
            atomic_status(
                "command",
                {
                    "state": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "retry_in_seconds": 2,
                },
            )
            time.sleep(2)


def reconcile_position_ownership(
    connection: psycopg.Connection,
    position_list: list[dict[str, object]],
    now: int,
    order_history: list[dict[str, object]] | None = None,
) -> None:
    """Reconcile durable ownership from exact Bybit position inventory and IDs."""
    current_positions = {
        (str(item.get("symbol") or ""), int(item.get("positionIdx") or 0)): item
        for item in position_list
        if Decimal(str(item.get("size") or 0)) > 0
    }
    rows = connection.execute(
        """SELECT position_id,trade_id,symbol,side,actual_avg_fill,actual_qty,
                  extract(epoch from fill_at)*1000,position_idx,entry_command_id
           FROM runtime.position_ownership
           WHERE state='OPEN' OR close_link_status='UNRESOLVED_EXACT_LINK'
           ORDER BY fill_at"""
    ).fetchall()
    latest_by_key = {
        (str(row[2]), int(row[7] or 0)): str(row[0]) for row in rows
    }
    for row in rows:
        position_id, trade_id, symbol, side = map(str, row[:4])
        position_idx = int(row[7] or 0)
        current = current_positions.get((symbol, position_idx))
        current_matches = (
            current is not None
            and latest_by_key.get((symbol, position_idx)) == position_id
            and str(current.get("side") or "") == side
        )
        if current_matches:
            connection.execute(
                """UPDATE runtime.position_ownership
                   SET state='OPEN',close_link_status='OPEN'
                   WHERE position_id=%s AND state='RECONCILIATION_REQUIRED'""",
                (position_id,),
            )
            continue
        fill_ms = int(row[6])
        next_fill = connection.execute(
            """SELECT extract(epoch from min(fill_at))*1000
               FROM runtime.position_ownership
               WHERE symbol=%s AND position_idx=%s AND fill_at>to_timestamp(%s/1000.0)""",
            (symbol, position_idx, fill_ms),
        ).fetchone()[0]
        interval_sql = """SELECT exec_id,order_id,order_link_id,side,exec_qty,exec_price,
                                 exec_fee,exec_time_ms,payload_json
                          FROM runtime.executions
                          WHERE symbol=%s AND exec_time_ms>=%s"""
        interval_args: tuple[object, ...] = (symbol, fill_ms)
        if next_fill is not None:
            interval_sql += " AND exec_time_ms<%s"
            interval_args += (int(next_fill),)
        execution_rows = connection.execute(
            interval_sql + " ORDER BY exec_time_ms,exec_id", interval_args
        ).fetchall()
        executions = [
            {
                "exec_id": value[0], "order_id": value[1], "order_link_id": value[2],
                "side": value[3], "exec_qty": value[4], "exec_price": value[5],
                "exec_fee": value[6], "exec_time_ms": value[7], "payload_json": value[8],
            }
            for value in execution_rows
        ]
        close = resolve_exchange_position_close(
            side=side,
            actual_avg_fill=Decimal(str(row[4])),
            actual_qty=Decimal(str(row[5])),
            executions=executions,
        )
        if close.status != "EXACT" or close.exit_order_id is None:
            connection.execute(
                """UPDATE runtime.position_ownership
                   SET state='CLOSED',
                       close_link_status='UNRESOLVED_EXACT_LINK'
                   WHERE position_id=%s""",
                (position_id,),
            )
            continue
        protection_rows = connection.execute(
            """SELECT protection_kind,initiator,exchange_order_ids,
                      stop_after,trailing_after,source_payload
               FROM runtime.protection_events WHERE position_id=%s
               ORDER BY occurred_at""",
            (position_id,),
        ).fetchall()
        protections = [
            {
                "protection_kind": value[0], "initiator": value[1],
                "exchange_order_ids": value[2], "stop_after": value[3],
                "trailing_after": value[4], "source_payload": value[5],
            }
            for value in protection_rows
        ]
        command_rows = connection.execute(
            """SELECT command_id,result_json FROM runtime.trade_commands
               WHERE command_type='close'
                 AND payload_json::jsonb->>'position_id'=%s""",
            (position_id,),
        ).fetchall()
        commands = [
            {"command_id": value[0], "result_json": value[1]} for value in command_rows
        ]
        exit_rows = [
            value for value in executions if value["order_id"] in close.exit_order_ids
        ]
        history_by_id = {
            str(value.get("orderId") or ""): value for value in (order_history or [])
        }
        exit_body = history_by_id.get(close.exit_order_id) or (
            json.loads(str(exit_rows[0]["payload_json"] or "{}")) if exit_rows else {}
        )
        exit_owner, exit_mechanism, attribution_method = classify_exit(
            exit_order_id=close.exit_order_id,
            stop_order_type=str(exit_body.get("stopOrderType") or ""),
            create_type=str(exit_body.get("createType") or ""),
            protection_events=protections,
            close_commands=commands,
        )
        closed_ms = max(int(value["exec_time_ms"] or now) for value in exit_rows)
        entry_fee = connection.execute(
            """SELECT coalesce(sum(abs(exec_fee::numeric)),0)
               FROM runtime.executions WHERE order_link_id=%s""",
            (str(row[8]),),
        ).fetchone()[0]
        gross = close.gross_pnl or Decimal(0)
        exit_fee = close.exit_fee_actual or Decimal(0)
        net_without_funding = gross - Decimal(str(entry_fee)) - exit_fee
        trigger = number(exit_body.get("triggerPrice")) or None
        trigger_slippage = trigger_to_fill_slippage_pct(
            side, trigger, close.actual_exit_avg_fill or Decimal(0)
        )
        initial_stop = Decimal(str(row[4])) * (
            Decimal("0.99") if side == "Buy" else Decimal("1.01")
        )
        latest_stop = next(
            (Decimal(str(value["stop_after"])) for value in reversed(protections) if value["stop_after"] is not None),
            None,
        )
        attribution_id = "XAT-" + hashlib.sha256(
            f"{position_id}|{close.exit_order_id}".encode()
        ).hexdigest()[:32]
        evidence = {
            "exchange_position_key": f"BYBIT:UNIFIED:LINEAR:USDT:{symbol}:{position_idx}",
            "close_resolution": close.reason,
            "attribution_method": attribution_method,
            "stop_order_type": exit_body.get("stopOrderType"),
            "create_type": exit_body.get("createType"),
            "funding": None,
        }
        connection.execute(
            """INSERT INTO runtime.position_exit_attribution(
                attribution_id,position_id,trade_id,closed_at,link_status,link_method,
                exit_owner,exit_mechanism,exit_order_id,exit_order_ids,
                exit_execution_ids,
                actual_avg_entry,intended_initial_hard_stop,
                actual_exchange_stop_before_exit,exchange_trigger_price,trigger_by,
                actual_exit_avg_fill,actual_exit_qty,entry_to_exit_price_move_pct,
                trigger_to_fill_slippage_pct,gross_pnl,entry_fee_actual,exit_fee_actual,
                funding,actual_net_without_funding,actual_net_pnl,
                economics_completeness,evidence)
                VALUES(%s,%s,%s,to_timestamp(%s/1000.0),'EXACT',%s,%s,%s,%s,%s,%s,
                       %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,NULL,
                       'PARTIAL_NO_FUNDING',%s)
                ON CONFLICT(position_id) DO NOTHING""",
            (
                attribution_id, position_id, trade_id, closed_ms, close.link_method,
                exit_owner, exit_mechanism, close.exit_order_id,
                json.dumps(close.exit_order_ids), json.dumps(close.exit_execution_ids),
                row[4], initial_stop, latest_stop,
                trigger, exit_body.get("triggerBy"), close.actual_exit_avg_fill,
                close.actual_exit_qty, close.entry_to_exit_move_pct, trigger_slippage,
                gross, entry_fee, exit_fee, net_without_funding,
                json.dumps(evidence, ensure_ascii=False),
            ),
        )
        event_id = "PLE-" + hashlib.sha256(
            f"{position_id}|CLOSED|{close.exit_order_id}".encode()
        ).hexdigest()[:32]
        connection.execute(
            """INSERT INTO runtime.position_lifecycle_events(
                lifecycle_event_id,position_id,trade_id,event_type,occurred_at,
                exact_ids,payload,provenance)
                VALUES(%s,%s,%s,'CLOSED',to_timestamp(%s/1000.0),%s,%s,%s)
                ON CONFLICT(lifecycle_event_id) DO NOTHING""",
            (
                event_id, position_id, trade_id, closed_ms,
                json.dumps({"exit_order_id": close.exit_order_id,
                            "exit_order_ids": close.exit_order_ids,
                            "exit_execution_ids": close.exit_execution_ids}),
                json.dumps({"exit_owner": exit_owner, "exit_mechanism": exit_mechanism}),
                json.dumps({"source": "fresh_bybit_reconciliation",
                            "link_method": close.link_method}),
            ),
        )
        connection.execute(
            """UPDATE runtime.position_ownership
               SET state='CLOSED',closed_at=to_timestamp(%s/1000.0),
                   exit_order_id=%s,exit_order_ids=%s,exit_execution_ids=%s,
                   close_link_status='EXACT'
               WHERE position_id=%s""",
            (closed_ms, close.exit_order_id, json.dumps(close.exit_order_ids),
             json.dumps(close.exit_execution_ids), position_id),
        )


def reconcile(
    connection: psycopg.Connection, key: str, secret: str, reason: str
) -> tuple[int, int]:
    started = int(time.time() * 1000)
    try:
        wallet, _ = api_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"}, key, secret)
        positions, _ = api_get(
            "/v5/position/list",
            {"category": "linear", "settleCoin": "USDT", "limit": "200"},
            key,
            secret,
        )
        orders, _ = api_get(
            "/v5/order/realtime",
            {"category": "linear", "settleCoin": "USDT", "openOnly": "0", "limit": "50"},
            key,
            secret,
        )
        if any(item.get("retCode") != 0 for item in (wallet, positions, orders)):
            raise RuntimeError("exchange rejected reconciliation request")
        now = int(time.time() * 1000)
        position_list = [
            p for p in ((positions.get("result") or {}).get("list") or [])
            if float(p.get("size") or 0) != 0
        ]
        order_list = (orders.get("result") or {}).get("list") or []
        fetch_history = reason != "periodic"
        if not fetch_history:
            current_keys = {
                (str(item.get("symbol") or ""), int(item.get("positionIdx") or 0))
                for item in position_list
            }
            owned_keys = {
                (str(row[0]), int(row[1] or 0))
                for row in connection.execute(
                    """SELECT symbol,position_idx FROM runtime.position_ownership
                       WHERE state='OPEN' AND close_link_status='OPEN'"""
                ).fetchall()
            }
            # Recovery/audit exception: history is fetched only when an owned cycle disappeared.
            missing_owned_position = bool(owned_keys - current_keys)
            fetch_history = missing_owned_position
        order_history: list[dict[str, object]] = []
        if fetch_history:
            order_history_response, _ = api_get(
                "/v5/order/history",
                {"category": "linear", "settleCoin": "USDT", "limit": "200"},
                key,
                secret,
            )
            if order_history_response.get("retCode") != 0:
                raise RuntimeError("exchange rejected order-history reconciliation request")
            order_history = (order_history_response.get("result") or {}).get("list") or []
        account = ((wallet.get("result") or {}).get("list") or [{}])[0]
        with connection.transaction():
            connection.execute("DELETE FROM runtime.hot_positions")
            connection.execute("DELETE FROM runtime.hot_orders")
            for p in position_list:
                upsert_position(connection, p, now)
            for o in order_list:
                upsert_order(connection, o, now)
            for item in order_history:
                upsert_exchange_order_history(connection, item, now)
            upsert_wallet(connection, account, now)
            reconcile_position_ownership(connection, position_list, now, order_history)
            connection.execute(
                """INSERT INTO runtime.reconciliation_runs(
                    started_at_epoch_ms,finished_at_epoch_ms,reason,ok,positions,orders,error)
                    VALUES(%s,%s,%s,1,%s,%s,'')""",
                (started, int(time.time() * 1000), reason, len(position_list), len(order_list)),
            )
        return len(position_list), len(order_list)
    except Exception as exc:
        connection.rollback()
        connection.execute(
            """INSERT INTO runtime.reconciliation_runs(
                started_at_epoch_ms,finished_at_epoch_ms,reason,ok,positions,orders,error)
                VALUES(%s,%s,%s,0,0,0,%s)""",
            (started, int(time.time() * 1000), reason, f"{type(exc).__name__}: {exc}"),
        )
        connection.commit()
        raise


def upsert_exchange_order_history(
    connection: psycopg.Connection, item: dict[str, object], now: int
) -> None:
    connection.execute(
        """INSERT INTO runtime.exchange_order_history(
            order_id,order_link_id,symbol,side,order_status,updated_at_epoch_ms,
            payload_json,refreshed_at_epoch_ms)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(order_id) DO UPDATE SET
              order_link_id=excluded.order_link_id,symbol=excluded.symbol,
              side=excluded.side,order_status=excluded.order_status,
              updated_at_epoch_ms=excluded.updated_at_epoch_ms,
              payload_json=excluded.payload_json,
              refreshed_at_epoch_ms=excluded.refreshed_at_epoch_ms""",
        (
            str(item.get("orderId") or ""),
            str(item.get("orderLinkId") or ""),
            str(item.get("symbol") or ""),
            str(item.get("side") or ""),
            str(item.get("orderStatus") or ""),
            int(item.get("updatedTime") or 0),
            json.dumps(item, ensure_ascii=False),
            now,
        ),
    )


def upsert_position(connection: psycopg.Connection, item: dict[str, object], now: int) -> None:
    existing = connection.execute(
        "SELECT payload_json FROM runtime.hot_positions WHERE symbol=%s AND position_idx=%s",
        (item.get("symbol", ""), int(item.get("positionIdx") or 0)),
    ).fetchone()
    merged = json.loads(existing[0]) if existing else {}
    merged.update(item)
    connection.execute("""INSERT INTO runtime.hot_positions(symbol,position_idx,side,size,entry_price,leverage,
        exchange_updated_ms,refreshed_at_epoch_ms,payload_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(symbol,position_idx) DO UPDATE SET side=excluded.side,size=excluded.size,
        entry_price=excluded.entry_price,leverage=excluded.leverage,exchange_updated_ms=excluded.exchange_updated_ms,
        refreshed_at_epoch_ms=excluded.refreshed_at_epoch_ms,payload_json=excluded.payload_json""",
        (merged.get("symbol", ""), int(merged.get("positionIdx") or 0), merged.get("side", ""), merged.get("size", "0"),
         merged.get("entryPrice") or merged.get("avgPrice") or "", merged.get("leverage", ""), int(merged.get("updatedTime") or 0), now,
         json.dumps(merged, ensure_ascii=False)))


def upsert_order(connection: psycopg.Connection, item: dict[str, object], now: int) -> None:
    connection.execute("""INSERT INTO runtime.hot_orders(order_id,order_link_id,symbol,side,order_status,qty,price,
        leaves_qty,exchange_updated_ms,refreshed_at_epoch_ms,payload_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(order_id) DO UPDATE SET order_status=excluded.order_status,leaves_qty=excluded.leaves_qty,
        exchange_updated_ms=excluded.exchange_updated_ms,refreshed_at_epoch_ms=excluded.refreshed_at_epoch_ms,
        payload_json=excluded.payload_json""",
        (item.get("orderId", ""), item.get("orderLinkId", ""), item.get("symbol", ""), item.get("side", ""),
         item.get("orderStatus", ""), item.get("qty", ""), item.get("price", ""), item.get("leavesQty", ""),
         int(item.get("updatedTime") or 0), now, json.dumps(item, ensure_ascii=False)))


def upsert_wallet(connection: psycopg.Connection, item: dict[str, object], now: int) -> None:
    available = str(account_available_usdt(item))
    connection.execute("""INSERT INTO runtime.wallet_latest(singleton,refreshed_at_epoch_ms,total_equity,wallet_balance,
        available_balance,payload_json) VALUES(1,%s,%s,%s,%s,%s) ON CONFLICT(singleton) DO UPDATE SET
        refreshed_at_epoch_ms=excluded.refreshed_at_epoch_ms,total_equity=excluded.total_equity,
        wallet_balance=excluded.wallet_balance,available_balance=excluded.available_balance,payload_json=excluded.payload_json""",
        (now, item.get("totalEquity", ""), item.get("totalWalletBalance", ""), available, json.dumps(item, ensure_ascii=False)))


def handle_private(connection: psycopg.Connection, message: dict[str, object]) -> None:
    topic = str(message.get("topic") or "")
    if not topic:
        return
    now = int(time.time() * 1000)
    data = message.get("data") or []
    connection.execute("INSERT INTO runtime.private_events(received_at_epoch_ms,topic,message_id,creation_time_ms,payload_json) VALUES(%s,%s,%s,%s,%s)",
                       (now, topic, str(message.get("id") or ""), int(message.get("creationTime") or 0), json.dumps(message, ensure_ascii=False)))
    for item in data:
        if topic.startswith("position"):
            if float(item.get("size") or 0) == 0:
                connection.execute("DELETE FROM runtime.hot_positions WHERE symbol=%s AND position_idx=%s", (item.get("symbol", ""), int(item.get("positionIdx") or 0)))
            else:
                upsert_position(connection, item, now)
        elif topic.startswith("order"):
            if item.get("orderStatus") in {"Filled", "Cancelled", "Rejected", "Deactivated"}:
                connection.execute("DELETE FROM runtime.hot_orders WHERE order_id=%s", (item.get("orderId", ""),))
            else:
                upsert_order(connection, item, now)
        elif topic.startswith("execution"):
            connection.execute("""INSERT INTO runtime.executions(exec_id,order_id,order_link_id,symbol,side,exec_qty,
                exec_price,exec_fee,exec_time_ms,received_at_epoch_ms,payload_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(exec_id) DO NOTHING""", (item.get("execId", ""), item.get("orderId", ""), item.get("orderLinkId", ""),
                item.get("symbol", ""), item.get("side", ""), item.get("execQty", ""), item.get("execPrice", ""),
                item.get("execFee", ""), int(item.get("execTime") or 0), now, json.dumps(item, ensure_ascii=False)))
        elif topic == "wallet":
            upsert_wallet(connection, item, now)
    connection.commit()


def private_loop(key: str, secret: str) -> None:
    reconnects = 0
    connection = db()
    while running:
        try:
            if reconnects > 0:
                disarm_new_entries(connection, "private WS reconnect: owner re-arm required")
            hot_positions, hot_orders = reconcile(connection, key, secret, "startup" if reconnects == 0 else "reconnect")
            ws = websocket.create_connection(PRIVATE_URL, timeout=10, enable_multithread=False)
            ws.settimeout(1)
            auth(ws, key, secret)
            ws.send(json.dumps({"op": "subscribe", "args": ["order.linear", "execution.linear", "position.linear", "wallet"]}, separators=(",", ":")))
            connection_event(connection, "private", "connected", reconnects=reconnects)
            atomic_status("private", {"state": "connected", "connected_at_epoch": int(time.time()), "reconnects": reconnects, "last_message_epoch": None, "hot_positions": hot_positions, "hot_orders": hot_orders})
            next_ping = time.monotonic() + 20
            next_reconcile = time.monotonic() + 5
            while running:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    raw = None
                if raw:
                    message = json.loads(raw)
                    handle_private(connection, message)
                    previous = status.get("private", {})
                    atomic_status("private", {"state": "connected", "connected_at_epoch": previous.get("connected_at_epoch"), "reconnects": reconnects, "last_message_epoch": int(time.time()), "hot_positions": previous.get("hot_positions", 0), "hot_orders": previous.get("hot_orders", 0)})
                if time.monotonic() >= next_ping:
                    ws.send('{"op":"ping"}')
                    next_ping = time.monotonic() + 20
                if time.monotonic() >= next_reconcile:
                    hot_positions, hot_orders = reconcile(connection, key, secret, "periodic")
                    previous = status.get("private", {})
                    atomic_status("private", {"state": "connected", "connected_at_epoch": previous.get("connected_at_epoch"), "reconnects": reconnects, "last_message_epoch": previous.get("last_message_epoch"), "hot_positions": hot_positions, "hot_orders": hot_orders})
                    next_reconcile = time.monotonic() + (1 if hot_positions else 5)
        except Exception as exc:
            reconnects += 1
            connection_event(connection, "private", "reconnecting", error=f"{type(exc).__name__}: {exc}", reconnects=reconnects)
            atomic_status("private", {"state": "reconnecting", "reconnects": reconnects, "error": f"{type(exc).__name__}: {exc}"})
            time.sleep(min(30, reconnects))


def trade_loop(key: str, secret: str) -> None:
    reconnects = 0
    connection = db()
    while running:
        try:
            ws = websocket.create_connection(TRADE_URL, timeout=10, enable_multithread=False)
            ws.settimeout(1)
            auth(ws, key, secret)
            connection_event(connection, "trade", "authenticated_no_commands", reconnects=reconnects)
            connected = int(time.time())
            atomic_status("trade", {"state": "authenticated-locked", "connected_at_epoch": connected, "reconnects": reconnects, "commands_sent": 0})
            next_ping = time.monotonic() + 20
            while running:
                try:
                    ws.recv()
                except websocket.WebSocketTimeoutException:
                    pass
                if time.monotonic() >= next_ping:
                    ws.send('{"op":"ping"}')
                    next_ping = time.monotonic() + 20
        except Exception as exc:
            reconnects += 1
            connection_event(connection, "trade", "reconnecting", error=f"{type(exc).__name__}: {exc}", reconnects=reconnects)
            atomic_status("trade", {"state": "reconnecting", "reconnects": reconnects, "commands_sent": 0, "error": f"{type(exc).__name__}: {exc}"})
            time.sleep(min(30, reconnects))


def main() -> None:
    global running
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    credentials = json.loads((Path(os.environ["CREDENTIALS_DIRECTORY"]) / "bybit-mainnet").read_text(encoding="utf-8"))
    bootstrap = db()
    disarm_new_entries(
        bootstrap,
        "restart: schema validation pending; owner re-arm required",
    )
    try:
        validate_runtime_schema_contract(bootstrap)
    except Exception as exc:
        atomic_status(
            "schema",
            {
                "state": "BLOCKED",
                "expected_version": EXPECTED_RUNTIME_SCHEMA_VERSION,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        bootstrap.close()
        raise
    atomic_status(
        "schema",
        {
            "state": "READY",
            "version": EXPECTED_RUNTIME_SCHEMA_VERSION,
        },
    )
    startup_live_safety(bootstrap, credentials["api_key"], credentials["api_secret"])
    bootstrap.close()
    signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__("running", False))
    signal.signal(signal.SIGINT, lambda *_: globals().__setitem__("running", False))
    thread = threading.Thread(target=trade_loop, args=(credentials["api_key"], credentials["api_secret"]), daemon=True)
    thread.start()
    commands = threading.Thread(target=command_loop, args=(credentials["api_key"], credentials["api_secret"]), daemon=True)
    commands.start()
    private_loop(credentials["api_key"], credentials["api_secret"])


if __name__ == "__main__":
    main()
