from __future__ import annotations

import hashlib
import json
import signal
import time
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

import psycopg
from protection_math import calculate_protection_plan, trailing_start_preserves_protection

STRUCTURAL_EARLY_EXIT_ENABLED = False
STRUCTURAL_BREAK_RULE = "NOT_PROVEN"
STATUS_PATH = Path("/var/lib/cripta/exit_runtime/status.json")
DEFAULT_SLIPPAGE_PCT = Decimal("0.0002")
running = True


def stop(*_: object) -> None:
    global running
    running = False


def atomic_status(state: str, *, error: str | None = None) -> None:
    document: dict[str, object] = {
        "state": state,
        "heartbeat_epoch": int(time.time()),
        "authority": "EXIT_RUNTIME_V36_1",
        "early_loss": "DISABLED_NOT_PROVEN_NO_CLOSE_IMPLEMENTATION",
        "structural_break_rule": STRUCTURAL_BREAK_RULE,
    }
    if error:
        document["error"] = error
    temp = STATUS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    temp.replace(STATUS_PATH)


def _json(raw: object) -> object:
    if isinstance(raw, (dict, list)):
        return raw
    return json.loads(str(raw))


def executable_close_price(symbol: str, side: str) -> Decimal:
    query = urllib.parse.urlencode({"category": "linear", "symbol": symbol})
    request = urllib.request.Request(
        "https://api.bybit.kz/v5/market/tickers?" + query,
        headers={"User-Agent": "cripta-exit-runtime/36.1"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        payload = json.load(response)
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(f"Bybit ticker rejected for {symbol}: {payload.get('retMsg')}")
    item = ((payload.get("result") or {}).get("list") or [{}])[0]
    bid = Decimal(str(item.get("bid1Price") or 0))
    ask = Decimal(str(item.get("ask1Price") or 0))
    last = Decimal(str(item.get("lastPrice") or 0))
    price = bid if side == "Buy" else ask
    price = price if price > 0 else last
    if price <= 0:
        raise RuntimeError(f"no executable close price for {symbol}")
    return price


def remaining_entry_fee(connection: psycopg.Connection, symbol: str, side: str) -> Decimal:
    qty = Decimal("0")
    fee = Decimal("0")
    rows = connection.execute(
        "SELECT side,exec_qty,exec_fee,payload_json FROM runtime.executions "
        "WHERE symbol=%s ORDER BY exec_time_ms,exec_id",
        (symbol,),
    ).fetchall()
    for execution_side, raw_qty, raw_fee, raw_payload in rows:
        execution_qty = Decimal(str(raw_qty))
        execution_fee = Decimal(str(raw_fee))
        decoded = _json(raw_payload)
        payload = decoded if isinstance(decoded, dict) else {}
        closed = Decimal(str(payload.get("closedSize") or 0))
        if str(execution_side) == side and closed <= 0:
            qty += execution_qty
            fee += execution_fee
        elif str(execution_side) != side and closed > 0 and qty > 0:
            allocated = min(execution_qty, qty)
            allocated_fee = fee * allocated / qty
            qty -= allocated
            fee = max(Decimal("0"), fee - allocated_fee)
    return fee


def observed_adverse_slippage(connection: psycopg.Connection, symbol: str) -> Decimal:
    worst = Decimal("0")
    rows = connection.execute(
        "SELECT payload_json FROM runtime.private_events WHERE topic='order.linear' "
        "ORDER BY received_at_epoch_ms DESC LIMIT 5000"
    ).fetchall()
    for (raw_payload,) in rows:
        payload = _json(raw_payload)
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("data", [])
        else:
            items = []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("symbol") != symbol or item.get("orderStatus") != "Filled":
                continue
            trigger = Decimal(str(item.get("triggerPrice") or 0))
            fill = Decimal(str(item.get("avgPrice") or 0))
            if trigger <= 0 or fill <= 0:
                continue
            gap = (
                (trigger - fill) / trigger
                if item.get("side") == "Sell"
                else (fill - trigger) / trigger
            )
            worst = max(worst, gap)
    return max(DEFAULT_SLIPPAGE_PCT, worst + DEFAULT_SLIPPAGE_PCT)


def protection_plan(
    connection: psycopg.Connection,
    symbol: str,
    side: str,
    entry: Decimal,
    qty: Decimal,
    tick: Decimal,
) -> dict[str, Decimal]:
    return calculate_protection_plan(
        entry=entry,
        qty=qty,
        entry_fee=remaining_entry_fee(connection, symbol, side),
        side=side,
        tick=tick,
        slippage_pct=observed_adverse_slippage(connection, symbol),
    )


def instrument_tick(symbol: str) -> Decimal:
    query = urllib.parse.urlencode({"category": "linear", "symbol": symbol})
    request = urllib.request.Request(
        "https://api.bybit.kz/v5/market/instruments-info?" + query,
        headers={"User-Agent": "cripta-exit-runtime/36.1"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        payload = json.load(response)
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(
            f"Bybit instrument lookup rejected for {symbol}: {payload.get('retMsg')}"
        )
    item = ((payload.get("result") or {}).get("list") or [{}])[0]
    tick = Decimal(str((item.get("priceFilter") or {}).get("tickSize") or 0))
    if tick <= 0:
        raise RuntimeError(f"mandatory tick size missing for {symbol}")
    return tick


def queue_profit_protection(
    connection: psycopg.Connection,
    *,
    entry_id: str,
    symbol: str,
    side: str,
    entry: Decimal,
    qty: Decimal,
    raw: dict[str, object],
) -> None:
    tick = instrument_tick(symbol)
    mark = executable_close_price(symbol, side)
    plan = protection_plan(connection, symbol, side, entry, qty, tick)
    existing_stop = Decimal(str(raw.get("stopLoss") or 0))
    already_protected = existing_stop > 0 and (
        (side == "Buy" and existing_stop >= plan["stop"])
        or (side == "Sell" and existing_stop <= plan["stop"])
    )
    if already_protected:
        return
    activation = plan["activation"]
    if (side == "Buy" and mark < activation) or (side == "Sell" and mark > activation):
        return
    move = (
        (mark / entry - Decimal("1"))
        * Decimal("100")
        * (Decimal("1") if side == "Buy" else Decimal("-1"))
    )
    retry_bucket = int(time.time() // 5)
    key = f"{entry_id}:{side}:{entry}:{plan['stop']}:{retry_bucket}"
    command_id = "auto-be-" + hashlib.sha256(key.encode()).hexdigest()[:25]
    connection.execute(
        """INSERT INTO runtime.trade_commands(
               command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms)
           VALUES(%s,'break_even',%s,%s,'queued',%s)
           ON CONFLICT(command_id) DO NOTHING""",
        (
            command_id,
            symbol,
            json.dumps(
                {
                    "source": "exit_runtime_v36",
                    "entry_command_id": entry_id,
                    "activation_move_pct": str(move),
                    "calculated_stop": str(plan["stop"]),
                    "minimum_fill": str(plan["minimum_fill"]),
                    "entry_fee": str(plan["entry_fee"]),
                    "slippage_reserve": str(plan["slippage"]),
                }
            ),
            int(time.time() * 1000),
        ),
    )


def queue_trailing(
    connection: psycopg.Connection,
    *,
    entry_id: str,
    symbol: str,
    side: str,
    entry: Decimal,
    qty: Decimal,
    raw: dict[str, object],
    trailing_pct: Decimal,
) -> None:
    if Decimal(str(raw.get("trailingStop") or 0)) > 0:
        return
    mark = executable_close_price(symbol, side)
    tick = instrument_tick(symbol)
    required_stop = protection_plan(connection, symbol, side, entry, qty, tick)["stop"]
    distance = mark * trailing_pct / Decimal("100")
    if not trailing_start_preserves_protection(
        side=side,
        mark=mark,
        distance=distance,
        protected_stop=required_stop,
    ):
        return
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
        return
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
        return
    retry_bucket = int(time.time() // 5)
    key = f"{entry_id}:{side}:{entry}:{trailing_pct}:{retry_bucket}"
    command_id = "auto-trail-" + hashlib.sha256(key.encode()).hexdigest()[:21]
    connection.execute(
        """INSERT INTO runtime.trade_commands(
               command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms)
           VALUES(%s,'trailing_stop',%s,%s,'queued',%s)
           ON CONFLICT(command_id) DO NOTHING""",
        (
            command_id,
            symbol,
            json.dumps(
                {
                    "enabled": True,
                    "distance_pct": str(trailing_pct),
                    "source": "exit_runtime_v36_after_profit_protection",
                    "entry_command_id": entry_id,
                }
            ),
            int(time.time() * 1000),
        ),
    )


def cycle(connection: psycopg.Connection) -> None:
    settings = connection.execute(
        """SELECT auto_profit_protection,auto_trailing_stop,trailing_distance_pct
           FROM runtime.trade_settings WHERE singleton=1"""
    ).fetchone()
    if settings is None:
        connection.commit()
        return
    auto_profit = bool(settings[0])
    auto_trailing = bool(settings[1])
    trailing_pct = Decimal(str(settings[2] or "0.30"))
    rows = connection.execute(
        """SELECT o.position_id,o.entry_command_id,o.symbol,o.side,
                  o.actual_avg_fill,o.actual_qty,p.payload_json
           FROM runtime.position_ownership o
           JOIN runtime.hot_positions p
             ON p.symbol=o.symbol AND p.position_idx=o.position_idx AND p.side=o.side
           WHERE o.state='OPEN' AND o.close_link_status='OPEN'
           ORDER BY o.fill_at"""
    ).fetchall()
    for row in rows:
        position_id, entry_id, symbol, side = map(str, row[:4])
        entry = Decimal(str(row[4]))
        qty = Decimal(str(row[5]))
        decoded = _json(row[6])
        if not isinstance(decoded, dict):
            continue
        raw = decoded
        if entry <= 0 or qty <= 0:
            continue
        if auto_profit:
            queue_profit_protection(
                connection,
                entry_id=entry_id,
                symbol=symbol,
                side=side,
                entry=entry,
                qty=qty,
                raw=raw,
            )
        if auto_trailing:
            queue_trailing(
                connection,
                entry_id=entry_id,
                symbol=symbol,
                side=side,
                entry=entry,
                qty=qty,
                raw=raw,
                trailing_pct=trailing_pct,
            )
    connection.commit()


def main() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    atomic_status("STARTING")
    with psycopg.connect(
        "dbname=cripta user=cripta host=/var/run/postgresql",
        application_name="cripta-exit-runtime-v36-1",
    ) as connection:
        next_heartbeat = 0.0
        while running:
            try:
                cycle(connection)
            except Exception as exc:
                connection.rollback()
                error = f"{type(exc).__name__}:{exc}"
                atomic_status("ERROR", error=error)
                print(f"EXIT_RUNTIME_ERROR:{error}", flush=True)
            else:
                if time.monotonic() >= next_heartbeat:
                    atomic_status("RUNNING")
                    next_heartbeat = time.monotonic() + 5
            time.sleep(0.5)
    atomic_status("STOPPED")


if __name__ == "__main__":
    main()
