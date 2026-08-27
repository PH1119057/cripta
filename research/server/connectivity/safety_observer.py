from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg

BASE_URL = os.environ.get("BYBIT_REST", "https://api.bybit.kz")
ROOT = Path(os.environ.get("CRIPTA_SAFETY_ROOT", "/var/lib/cripta/safety"))
LATEST_PATH = ROOT / "latest.json"
INTERVAL = int(os.environ.get("CRIPTA_SAFETY_INTERVAL", "30"))
running = True


def stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def api_get(path: str, params: dict[str, str], key: str = "", secret: str = "") -> tuple[dict[str, object], float]:
    query = urllib.parse.urlencode(sorted(params.items()))
    headers = {"User-Agent": "cripta-safety-observer/1"}
    if key:
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"
        signature = hmac.new(secret.encode(), f"{timestamp}{key}{recv_window}{query}".encode(), hashlib.sha256).hexdigest()
        headers.update({"X-BAPI-API-KEY": key, "X-BAPI-TIMESTAMP": timestamp, "X-BAPI-RECV-WINDOW": recv_window, "X-BAPI-SIGN": signature})
    started = time.perf_counter()
    request = urllib.request.Request(f"{BASE_URL}{path}{'?' + query if query else ''}", headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    return payload, (time.perf_counter() - started) * 1000


def initialize(connection: psycopg.Connection) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS safety")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS safety.system_events (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at_epoch_ms BIGINT NOT NULL, event TEXT NOT NULL,
            severity TEXT NOT NULL, details_json TEXT NOT NULL
        )""")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS safety.exchange_snapshots (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at_epoch_ms BIGINT NOT NULL, ok SMALLINT NOT NULL,
            clock_offset_ms BIGINT, rest_latency_ms DOUBLE PRECISION, public_stream_age_ms BIGINT,
            total_equity TEXT, available_balance TEXT, wallet_balance TEXT,
            account_im_rate TEXT, account_mm_rate TEXT, open_positions INTEGER,
            open_orders INTEGER, position_modes_json TEXT NOT NULL, leverages_json TEXT NOT NULL,
            error TEXT NOT NULL
        )""")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_safety_events_at ON safety.system_events(at_epoch_ms)")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_safety_snapshots_at ON safety.exchange_snapshots(at_epoch_ms)")
    connection.commit()


def event(connection: psycopg.Connection, name: str, severity: str = "info", **details: object) -> None:
    connection.execute("INSERT INTO safety.system_events(at_epoch_ms,event,severity,details_json) VALUES(%s,%s,%s,%s)",
                       (int(time.time() * 1000), name, severity, json.dumps(details, ensure_ascii=False)))
    connection.commit()


def public_stream_age(now_ms: int) -> int | None:
    path = Path("/var/lib/cripta/connectivity/status.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        last = int(value.get("last_message_epoch_ms") or 0)
        return max(0, now_ms - last) if last else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def atomic_json(payload: dict[str, object]) -> None:
    temporary = LATEST_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(LATEST_PATH)


def collect(connection: psycopg.Connection, key: str, secret: str) -> None:
    now_ms = int(time.time() * 1000)
    age = public_stream_age(now_ms)
    try:
        server, server_latency = api_get("/v5/market/time", {})
        wallet, wallet_latency = api_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"}, key, secret)
        positions, positions_latency = api_get("/v5/position/list", {"category": "linear", "settleCoin": "USDT", "limit": "200"}, key, secret)
        orders, orders_latency = api_get("/v5/order/realtime", {"category": "linear", "settleCoin": "USDT", "openOnly": "0", "limit": "50"}, key, secret)
        payloads = (server, wallet, positions, orders)
        if any(item.get("retCode") != 0 for item in payloads):
            raise RuntimeError("; ".join(str(item.get("retMsg")) for item in payloads if item.get("retCode") != 0))
        server_ms = int(server.get("time") or int((server.get("result") or {}).get("timeNano", "0")) // 1_000_000)
        account = ((wallet.get("result") or {}).get("list") or [{}])[0]
        position_list = (positions.get("result") or {}).get("list") or []
        order_list = (orders.get("result") or {}).get("list") or []
        active_positions = [p for p in position_list if float(p.get("size") or 0) != 0]
        modes = sorted({str(p.get("positionIdx")) for p in active_positions})
        leverages = {str(p.get("symbol")): p.get("leverage") for p in active_positions}
        latency = max(server_latency, wallet_latency, positions_latency, orders_latency)
        latest = {
            "state": "healthy", "checked_at_epoch": now_ms // 1000,
            "clock_offset_ms": server_ms - now_ms, "rest_latency_ms": round(latency, 2),
            "public_stream_age_ms": age, "total_equity": account.get("totalEquity"),
            "available_balance": account.get("totalAvailableBalance"), "wallet_balance": account.get("totalWalletBalance"),
            "account_im_rate": account.get("accountIMRate"), "account_mm_rate": account.get("accountMMRate"),
            "open_positions": len(active_positions), "open_orders": len(order_list),
            "position_modes": modes, "leverages": leverages,
        }
        connection.execute("""INSERT INTO safety.exchange_snapshots(
            at_epoch_ms,ok,clock_offset_ms,rest_latency_ms,public_stream_age_ms,total_equity,
            available_balance,wallet_balance,account_im_rate,account_mm_rate,open_positions,
            open_orders,position_modes_json,leverages_json,error) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (now_ms, 1, latest["clock_offset_ms"], latency, age, latest["total_equity"], latest["available_balance"],
             latest["wallet_balance"], latest["account_im_rate"], latest["account_mm_rate"], len(active_positions),
             len(order_list), json.dumps(modes), json.dumps(leverages), ""))
        connection.commit()
        atomic_json(latest)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        connection.execute("""INSERT INTO safety.exchange_snapshots(
            at_epoch_ms,ok,public_stream_age_ms,position_modes_json,leverages_json,error)
            VALUES(%s,0,%s,'[]','{}',%s)""", (now_ms, age, message))
        connection.commit()
        atomic_json({"state": "error", "checked_at_epoch": now_ms // 1000, "public_stream_age_ms": age, "error": message})
        event(connection, "exchange_truth_refresh_failed", "error", error=message)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    credential_dir = Path(os.environ["CREDENTIALS_DIRECTORY"])
    credentials = json.loads((credential_dir / "bybit-mainnet").read_text(encoding="utf-8"))
    connection = psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql")
    initialize(connection)
    event(connection, "observer_startup", pid=os.getpid(), interval_seconds=INTERVAL)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            collect(connection, credentials["api_key"], credentials["api_secret"])
            for _ in range(INTERVAL):
                if not running:
                    break
                time.sleep(1)
    finally:
        event(connection, "observer_shutdown", pid=os.getpid())
        connection.close()


if __name__ == "__main__":
    main()
