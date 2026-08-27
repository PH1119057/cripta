from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import threading
import time
import urllib.request
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

import psycopg
import websocket

from safety_observer import api_get
from protection_math import calculate_protection_plan, trailing_start_preserves_protection

PRIVATE_URL = os.environ.get("BYBIT_PRIVATE_WS", "wss://stream.bybit.kz/v5/private?max_active_time=1m")
TRADE_URL = os.environ.get("BYBIT_TRADE_WS", "wss://stream.bybit.kz/v5/trade?max_active_time=1m")
STATUS = Path("/var/lib/cripta/private_runtime/status.json")
running = True
status_lock = threading.Lock()
status: dict[str, object] = {"private": {"state": "starting"}, "trade": {"state": "starting"}}
REST_URL = os.environ.get("BYBIT_REST", "https://api.bybit.kz")
_tick_cache: dict[str, Decimal] = {}


def db() -> psycopg.Connection:
    return psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql")


def initialize(connection: psycopg.Connection) -> None:
    statements = (
        "CREATE SCHEMA IF NOT EXISTS runtime",
        """CREATE TABLE IF NOT EXISTS runtime.connection_events(
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at_epoch_ms BIGINT NOT NULL,
            channel TEXT NOT NULL, event TEXT NOT NULL, details_json TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS runtime.private_events(
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, received_at_epoch_ms BIGINT NOT NULL,
            topic TEXT NOT NULL, message_id TEXT NOT NULL, creation_time_ms BIGINT, payload_json TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS runtime.hot_positions(
            symbol TEXT NOT NULL, position_idx INTEGER NOT NULL, side TEXT NOT NULL, size TEXT NOT NULL,
            entry_price TEXT NOT NULL, leverage TEXT NOT NULL, exchange_updated_ms BIGINT,
            refreshed_at_epoch_ms BIGINT NOT NULL, payload_json TEXT NOT NULL,
            PRIMARY KEY(symbol,position_idx))""",
        """CREATE TABLE IF NOT EXISTS runtime.hot_orders(
            order_id TEXT PRIMARY KEY, order_link_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
            order_status TEXT NOT NULL, qty TEXT NOT NULL, price TEXT NOT NULL, leaves_qty TEXT NOT NULL,
            exchange_updated_ms BIGINT, refreshed_at_epoch_ms BIGINT NOT NULL, payload_json TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS runtime.executions(
            exec_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, order_link_id TEXT NOT NULL, symbol TEXT NOT NULL,
            side TEXT NOT NULL, exec_qty TEXT NOT NULL, exec_price TEXT NOT NULL, exec_fee TEXT NOT NULL,
            exec_time_ms BIGINT, received_at_epoch_ms BIGINT NOT NULL, payload_json TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS runtime.wallet_latest(
            singleton SMALLINT PRIMARY KEY CHECK(singleton=1), refreshed_at_epoch_ms BIGINT NOT NULL,
            total_equity TEXT NOT NULL, wallet_balance TEXT NOT NULL, available_balance TEXT NOT NULL,
            payload_json TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS runtime.reconciliation_runs(
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, started_at_epoch_ms BIGINT NOT NULL,
            finished_at_epoch_ms BIGINT NOT NULL, reason TEXT NOT NULL, ok SMALLINT NOT NULL,
            positions INTEGER NOT NULL, orders INTEGER NOT NULL, error TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS runtime.trade_settings(
            singleton SMALLINT PRIMARY KEY CHECK(singleton=1), stake_usdt TEXT NOT NULL DEFAULT '10',
            leverage INTEGER NOT NULL DEFAULT 10, enabled_symbols_json TEXT NOT NULL DEFAULT '[]',
            updated_at_epoch_ms BIGINT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS runtime.trade_commands(
            command_id TEXT PRIMARY KEY, command_type TEXT NOT NULL, symbol TEXT NOT NULL,
            payload_json TEXT NOT NULL, state TEXT NOT NULL, requested_at_epoch_ms BIGINT NOT NULL,
            started_at_epoch_ms BIGINT, finished_at_epoch_ms BIGINT, result_json TEXT NOT NULL DEFAULT '{}',
            error TEXT NOT NULL DEFAULT '')""",
        "ALTER TABLE runtime.trade_settings ADD COLUMN IF NOT EXISTS entry_offset_pct TEXT NOT NULL DEFAULT '0.00'",
        "ALTER TABLE runtime.trade_settings ADD COLUMN IF NOT EXISTS entry_limit_ttl_seconds INTEGER NOT NULL DEFAULT 30",
        "ALTER TABLE runtime.trade_settings ADD COLUMN IF NOT EXISTS auto_profit_protection BOOLEAN NOT NULL DEFAULT TRUE",
        """INSERT INTO runtime.trade_settings(singleton,updated_at_epoch_ms) VALUES(1,0)
            ON CONFLICT(singleton) DO NOTHING""",
    )
    for statement in statements:
        connection.execute(statement)
    connection.commit()


def atomic_status(channel: str, value: dict[str, object]) -> None:
    with status_lock:
        status[channel] = value
        status["updated_at_epoch"] = int(time.time())
        temporary = STATUS.with_suffix(".tmp")
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(STATUS)


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
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
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
        entry=entry, qty=qty, entry_fee=entry_fee, side=side, tick=tick
    )


def account_available_usdt(account: dict[str, object]) -> Decimal:
    direct = str(account.get("totalAvailableBalance") or "")
    if direct:
        return Decimal(direct)
    usdt = next((coin for coin in account.get("coin", []) if coin.get("coin") == "USDT"), {})
    wallet = Decimal(str(usdt.get("walletBalance") or 0))
    reserved = sum(Decimal(str(usdt.get(name) or 0)) for name in ("totalOrderIM", "totalPositionIM", "locked"))
    return max(Decimal("0"), wallet - reserved)


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
    elif kind in {"break_even", "current_stop"}:
        if not position: raise RuntimeError("open position not found")
        side, mark = str(position["side"]), Decimal(str(position.get("markPrice") or 0))
        if kind == "break_even":
            plan = protection_plan(connection, symbol, position, tick)
            stop, activation = plan["stop"], plan["activation"]
            if (side == "Buy" and mark < activation) or (side == "Sell" and mark > activation):
                raise RuntimeError(f"price has not reached calculated protection activation {activation}")
        else:
            stop = quantize(mark * (Decimal("0.998") if side=="Buy" else Decimal("1.002")), tick, upward=side!="Buy")
        if (side=="Buy" and stop >= mark) or (side=="Sell" and stop <= mark): raise RuntimeError("calculated stop is already beyond current price")
        result = api_post("/v5/position/trading-stop", {"category":"linear","symbol":symbol,"positionIdx":int(position.get("positionIdx") or 0),"tpslMode":"Full","stopLoss":str(stop),"slTriggerBy":"MarkPrice","slOrderType":"Market"}, key, secret)
        if kind == "break_even":
            result["protectionPlan"] = {name: str(value) for name, value in plan.items()}
    elif kind == "trailing_stop":
        if not position: raise RuntimeError("open position not found")
        enabled = bool(payload.get("enabled"))
        params: dict[str, object] = {"category":"linear","symbol":symbol,"positionIdx":int(position.get("positionIdx") or 0),"tpslMode":"Full","slTriggerBy":"MarkPrice"}
        if enabled:
            distance_pct = Decimal(str(payload.get("distance_pct") or "0.2"))
            if distance_pct < Decimal("0.05") or distance_pct > Decimal("5"):
                raise RuntimeError("trailing stop distance must be from 0.05% to 5%")
            mark = Decimal(str(position.get("markPrice") or 0))
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
        result = api_post("/v5/position/trading-stop", params, key, secret)
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
        stop=quantize(price*(Decimal("0.99") if side=="Buy" else Decimal("1.01")),tick,upward=side=="Buy")
        target=quantize(price*(Decimal("1.011") if side=="Buy" else Decimal("0.989")),tick,upward=side=="Buy")
        order={"category":"linear","symbol":symbol,"side":side,"orderType":"Market" if offset == 0 else "Limit","qty":str(qty),"positionIdx":0,"orderLinkId":command_id[:36],"takeProfit":str(target),"stopLoss":str(stop),"tpTriggerBy":"MarkPrice","slTriggerBy":"MarkPrice","tpslMode":"Full","tpOrderType":"Market","slOrderType":"Market"}
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
            actual_stop = quantize(
                actual_entry * (Decimal("0.99") if side == "Buy" else Decimal("1.01")),
                tick,
                upward=side == "Buy",
            )
            actual_target = quantize(
                actual_entry * (Decimal("1.011") if side == "Buy" else Decimal("0.989")),
                tick,
                upward=side == "Buy",
            )
            protection = api_post(
                "/v5/position/trading-stop",
                {
                    "category": "linear", "symbol": symbol,
                    "positionIdx": int(filled_position.get("positionIdx") or 0),
                    "tpslMode": "Full", "stopLoss": str(actual_stop),
                    "takeProfit": str(actual_target), "slTriggerBy": "MarkPrice",
                    "tpTriggerBy": "MarkPrice", "slOrderType": "Market",
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
    reconcile(connection,key,secret,"after_command")
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


def command_loop(key: str, secret: str) -> None:
    connection=db()
    next_limit_cleanup = 0.0
    while running:
        gate=connection.execute("SELECT enabled,updated_at_epoch_ms FROM control.execution_gates WHERE mode='mainnet'").fetchone()
        settings=connection.execute("SELECT stake_usdt,leverage,enabled_symbols_json,updated_at_epoch_ms,entry_offset_pct,entry_limit_ttl_seconds,auto_profit_protection FROM runtime.trade_settings WHERE singleton=1").fetchone()
        if gate and gate[0] and settings:
            enabled=set(json.loads(settings[2])); now_ms=int(time.time()*1000)
            if time.monotonic() >= next_limit_cleanup:
                try:
                    cancel_expired_entry_limits(connection, key, secret, now_ms)
                except Exception:
                    connection.rollback()
                next_limit_cleanup = time.monotonic() + 1
            fresh_after=max(now_ms-10_000,int(gate[1] or 0),int(settings[3] or 0))
            signals=connection.execute("""SELECT signal_id,symbol,direction,signal_price FROM monitoring.opportunities
                WHERE bot_id='entry-v1-shadow' AND decision='shadow' AND signal_at_epoch_ms >= %s
                ORDER BY signal_at_epoch_ms DESC LIMIT 100""",(fresh_after,)).fetchall()
            for signal_id,symbol,direction,price in signals:
                if symbol not in enabled: continue
                occupied=connection.execute("""SELECT
                    EXISTS(SELECT 1 FROM runtime.hot_positions WHERE symbol=%s) OR
                    EXISTS(SELECT 1 FROM runtime.hot_orders WHERE symbol=%s AND order_status IN ('New','PartiallyFilled','Untriggered')) OR
                    EXISTS(SELECT 1 FROM runtime.trade_commands WHERE symbol=%s AND command_type='entry' AND state IN ('queued','running'))""",(symbol,symbol,symbol)).fetchone()[0]
                if occupied: continue
                cid="auto-"+hashlib.sha256(str(signal_id).encode()).hexdigest()[:28]
                body={"stake_usdt":settings[0],"leverage":settings[1],"side":"Buy" if direction=="long" else "Sell","price":price,"signal_id":signal_id,"entry_offset_pct":settings[4],"entry_limit_ttl_seconds":settings[5]}
                connection.execute("""INSERT INTO runtime.trade_commands(command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms)
                    VALUES(%s,'entry',%s,%s,'queued',%s) ON CONFLICT(command_id) DO NOTHING""",(cid,symbol,json.dumps(body),int(time.time()*1000)))
            connection.commit()
        completed_entries=connection.execute("""SELECT command_id,symbol FROM runtime.trade_commands
            WHERE command_type='entry' AND state='completed'""").fetchall()
        if not settings or not settings[6]:
            completed_entries = []
        for entry_id,symbol in completed_entries:
            position_row=connection.execute("SELECT side,entry_price,payload_json FROM runtime.hot_positions WHERE symbol=%s",(symbol,)).fetchone()
            if not position_row: continue
            raw=json.loads(position_row[2]); entry=Decimal(str(position_row[1])); mark=Decimal(str(raw.get("markPrice") or 0))
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
            if (position_row[0] == "Buy" and mark < activation) or (position_row[0] == "Sell" and mark > activation):
                continue
            be_id="auto-be-"+hashlib.sha256(str(entry_id).encode()).hexdigest()[:25]
            connection.execute("""INSERT INTO runtime.trade_commands(command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms)
                VALUES(%s,'break_even',%s,%s,'queued',%s) ON CONFLICT(command_id) DO NOTHING""",(be_id,symbol,json.dumps({"entry_command_id":entry_id,"activation_move_pct":str(move),"calculated_stop":str(plan["stop"]),"minimum_fill":str(plan["minimum_fill"]),"entry_fee":str(plan["entry_fee"]),"slippage_reserve":str(plan["slippage"])}),int(time.time()*1000)))
        connection.commit()
        row=connection.execute("""SELECT command_id,command_type,symbol,payload_json FROM runtime.trade_commands
            WHERE state='queued' ORDER BY requested_at_epoch_ms LIMIT 1""").fetchone()
        if not row: time.sleep(0.25); continue
        command_id=str(row[0]); connection.execute("UPDATE runtime.trade_commands SET state='running',started_at_epoch_ms=%s WHERE command_id=%s AND state='queued'",(int(time.time()*1000),command_id)); connection.commit()
        try: execute_command(connection,key,secret,row)
        except Exception as exc:
            connection.rollback(); connection.execute("UPDATE runtime.trade_commands SET state='failed',finished_at_epoch_ms=%s,error=%s WHERE command_id=%s",(int(time.time()*1000),f"{type(exc).__name__}: {exc}",command_id)); connection.commit()


def reconcile(connection: psycopg.Connection, key: str, secret: str, reason: str) -> tuple[int, int]:
    started = int(time.time() * 1000)
    try:
        wallet, _ = api_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"}, key, secret)
        positions, _ = api_get("/v5/position/list", {"category": "linear", "settleCoin": "USDT", "limit": "200"}, key, secret)
        orders, _ = api_get("/v5/order/realtime", {"category": "linear", "settleCoin": "USDT", "openOnly": "0", "limit": "50"}, key, secret)
        if any(item.get("retCode") != 0 for item in (wallet, positions, orders)):
            raise RuntimeError("exchange rejected reconciliation request")
        now = int(time.time() * 1000)
        position_list = [p for p in ((positions.get("result") or {}).get("list") or []) if float(p.get("size") or 0) != 0]
        order_list = (orders.get("result") or {}).get("list") or []
        account = ((wallet.get("result") or {}).get("list") or [{}])[0]
        with connection.transaction():
            connection.execute("DELETE FROM runtime.hot_positions")
            connection.execute("DELETE FROM runtime.hot_orders")
            for p in position_list:
                upsert_position(connection, p, now)
            for o in order_list:
                upsert_order(connection, o, now)
            upsert_wallet(connection, account, now)
            connection.execute("""INSERT INTO runtime.reconciliation_runs(
                started_at_epoch_ms,finished_at_epoch_ms,reason,ok,positions,orders,error)
                VALUES(%s,%s,%s,1,%s,%s,'')""", (started, int(time.time() * 1000), reason, len(position_list), len(order_list)))
        return len(position_list), len(order_list)
    except Exception as exc:
        connection.rollback()
        connection.execute("""INSERT INTO runtime.reconciliation_runs(
            started_at_epoch_ms,finished_at_epoch_ms,reason,ok,positions,orders,error)
            VALUES(%s,%s,%s,0,0,0,%s)""", (started, int(time.time() * 1000), reason, f"{type(exc).__name__}: {exc}"))
        connection.commit()
        raise


def upsert_position(connection: psycopg.Connection, item: dict[str, object], now: int) -> None:
    connection.execute("""INSERT INTO runtime.hot_positions(symbol,position_idx,side,size,entry_price,leverage,
        exchange_updated_ms,refreshed_at_epoch_ms,payload_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(symbol,position_idx) DO UPDATE SET side=excluded.side,size=excluded.size,
        entry_price=excluded.entry_price,leverage=excluded.leverage,exchange_updated_ms=excluded.exchange_updated_ms,
        refreshed_at_epoch_ms=excluded.refreshed_at_epoch_ms,payload_json=excluded.payload_json""",
        (item.get("symbol", ""), int(item.get("positionIdx") or 0), item.get("side", ""), item.get("size", "0"),
         item.get("entryPrice") or item.get("avgPrice") or "", item.get("leverage", ""), int(item.get("updatedTime") or 0), now,
         json.dumps(item, ensure_ascii=False)))


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
    initialize(bootstrap)
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
