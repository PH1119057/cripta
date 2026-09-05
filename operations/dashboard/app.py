from __future__ import annotations

import json
import os
import base64
import csv
import io
import crypt
import hashlib
import hmac
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request
import zipfile
import psycopg

try:
    from archive_v2 import read_job as read_archive_job
    from archive_v2 import start_job as start_archive_job
except ImportError:  # package import used by tests
    from operations.dashboard.archive_v2 import read_job as read_archive_job
    from operations.dashboard.archive_v2 import start_job as start_archive_job
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

DATA_ROOT = Path(os.environ.get("CRIPTA_DATA_ROOT", "/data/cripta"))
APP_ROOT = Path(os.environ.get("CRIPTA_APP_ROOT", "/srv/cripta"))
PERIOD = "20260518_20260816"
STATE = DATA_ROOT / "datasets" / "raw" / PERIOD / "download_state.json"
EXPANSION_STATE = DATA_ROOT / "datasets" / "raw" / PERIOD / "download_state_expansion_20260823.json"
REPORT_ROOT = Path(os.environ.get("CRIPTA_REPORT_ROOT", "/srv/cripta-share/reports"))
CONNECTIVITY_STATE = Path("/var/lib/cripta/connectivity/status.json")
PRIVATE_API_STATE = Path("/var/lib/cripta/connectivity/private_api.json")
SAFETY_STATE = Path("/var/lib/cripta/safety/latest.json")
BACKUP_STATE = Path("/var/lib/cripta/backup/latest.json")
PRIVATE_RUNTIME_STATE = Path("/var/lib/cripta/private_runtime/status.json")
HEALTH_STATE = Path("/var/lib/cripta/health/status.json")
ENTRY_SHADOW_STATE = Path("/var/lib/cripta/entry_shadow/status.json")
MAYAK_V2_STATE = Path("/var/lib/cripta/mayak_v2/status.json")
ENTRY_COMPARISON_STATE = APP_ROOT / "dashboard" / "entry_comparison.json"
AUTH_FILE = Path(os.environ.get("CRIPTA_AUTH_FILE", "/etc/nginx/cripta-dashboard.htpasswd"))
SESSION_SECRET_FILE = Path(
    os.environ.get("CRIPTA_SESSION_SECRET_FILE", "/etc/cripta-dashboard/session.secret")
)
SESSION_COOKIE = "cripta_session"
ALLOWED_SERVICES = (
    "cripta-dashboard.service",
    "cripta-download-frozen.service",
    "cripta-download-expansion.service",
    "cripta-job-intake.service",
    "cripta-job-runner.service",
    "cripta-bybit-latency.service",
    "cripta-safety-observer.service",
    "cripta-private-runtime.service",
    "cripta-health-monitor.service",
    "cripta-mayak-v2.service",
    "nginx.service",
    "postgresql.service",
)
_cache: tuple[float, dict[str, object]] | None = None
_ticker_cache: tuple[float, dict[str, dict[str, object]]] | None = None
_liquidity_cache: tuple[float, dict[str, dict[str, object]]] | None = None
_package_lock = threading.Lock()
_signal_export_job_lock = threading.Lock()
SIGNAL_EXPORT_JOB_ROOT = Path(
    os.environ.get("CRIPTA_SIGNAL_EXPORT_JOB_ROOT", "/var/lib/cripta/archive_jobs")
)

TRADING_UNIVERSE = (
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
    "NEARUSDT",
    "OPUSDT",
    "SOLUSDT",
    "SUIUSDT",
    "TRXUSDT",
    "UNIUSDT",
    "XLMUSDT",
    "XRPUSDT",
)
INDICATORS = ("BTCUSDT", "ETHUSDT")
EXCLUDED_MEMES = ("1000PEPEUSDT", "DOGEUSDT")
BYBIT_KZ_UNSUPPORTED = frozenset(("1000PEPEUSDT", "DOGEUSDT"))


def live_tickers() -> dict[str, dict[str, object]]:
    global _ticker_cache
    now = time.monotonic()
    if _ticker_cache and now - _ticker_cache[0] < 2:
        return _ticker_cache[1]
    with urllib.request.urlopen(
        "https://api.bybit.kz/v5/market/tickers?category=linear", timeout=8
    ) as response:
        payload = json.load(response)
    result: dict[str, dict[str, object]] = {}
    for item in payload.get("result", {}).get("list", []):
        symbol = item.get("symbol")
        if symbol not in {*TRADING_UNIVERSE, *INDICATORS, *EXCLUDED_MEMES}:
            continue
        bid = float(item.get("bid1Price") or 0)
        ask = float(item.get("ask1Price") or 0)
        middle = (bid + ask) / 2 if bid and ask else 0
        result[symbol] = {
            "turnover24h": float(item.get("turnover24h") or 0),
            "open_interest_value": float(item.get("openInterestValue") or 0),
            "funding_rate_pct": float(item.get("fundingRate") or 0) * 100,
            "spread_bps": ((ask - bid) / middle * 10_000) if middle else None,
            "last_price": item.get("lastPrice") or "",
            "mark_price": item.get("markPrice") or "",
            "bid_price": item.get("bid1Price") or "",
            "ask_price": item.get("ask1Price") or "",
        }
    _ticker_cache = (now, result)
    return result


def traffic_light(symbol: str, ticker: dict[str, object] | None) -> tuple[str, str]:
    if symbol in INDICATORS:
        return "red", "индикатор рынка, торговля запрещена"
    if symbol in EXCLUDED_MEMES:
        return "red", "мем-монета исключена из торговли"
    if not ticker:
        return "red", "нет свежей котировки Bybit"
    turnover = float(ticker["turnover24h"])
    oi = float(ticker["open_interest_value"])
    spread = ticker["spread_bps"]
    funding = abs(float(ticker["funding_rate_pct"]))
    if turnover < 5_000_000 or oi < 2_000_000 or spread is None or spread > 20 or funding > 0.20:
        return "red", "критический порог ликвидности, спреда, OI или funding"
    warnings = []
    if turnover < 25_000_000:
        warnings.append("оборот < $25 млн")
    if oi < 10_000_000:
        warnings.append("OI < $10 млн")
    if spread > 8:
        warnings.append("спред > 8 б.п.")
    if funding > 0.05:
        warnings.append("|funding| > 0,05%")
    if warnings:
        return "yellow", "; ".join(warnings)
    return "green", "проходит сегодняшние операционные пороги"


def command(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=8, check=False)
    return (result.stdout or result.stderr).strip()


def directory_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    output = command("du", "-sb", str(path)).split(maxsplit=1)
    return int(output[0]) if output and output[0].isdigit() else 0


def service_state(name: str) -> str:
    value = command("systemctl", "is-active", name)
    return value.splitlines()[0] if value else "unknown"


def bot_control_state() -> dict[str, object]:
    with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
        rows = connection.execute("""SELECT id,name,strategy,mode,desired_state,actual_state,executable,
            mainnet_approved,symbols_json,stats_json,updated_at_epoch FROM control.bots ORDER BY id""").fetchall()
        bots = [
            {
                "id": row[0],
                "name": row[1],
                "strategy": row[2],
                "mode": row[3],
                "desired_state": row[4],
                "actual_state": row[5],
                "executable": row[6],
                "mainnet_approved": bool(row[7]),
                "symbols": json.loads(row[8]),
                "stats": json.loads(row[9]),
                "updated_at_epoch": row[10],
            }
            for row in rows
        ]
        events = [
            {"at_epoch": row[0] // 1000, "bot_id": row[1], "action": row[2], "result": row[3]}
            for row in connection.execute(
                "SELECT at_epoch_ms,bot_id,action,result FROM control.bot_events ORDER BY id DESC LIMIT 100"
            )
        ]
        gates = {
            row[0]: {"enabled": bool(row[1]), "reason": row[2]}
            for row in connection.execute("SELECT mode,enabled,reason FROM control.execution_gates")
        }
    return {
        "execution_gate": "live-trading-locked"
        if not gates.get("mainnet", {}).get("enabled")
        else "mainnet-enabled",
        "gates": gates,
        "bots": bots,
        "events": events,
    }


def opportunity_state() -> dict[str, object]:
    with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
        counts = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT state,count(*) FROM monitoring.opportunities GROUP BY state"
            )
        }
        rows = connection.execute("""SELECT signal_id,bot_id,strategy_version,symbol,direction,signal_price,decision,
            decision_reason,traffic_light,state,max_favorable_pct,max_adverse_pct,first_hits_json,samples,
            signal_at_epoch_ms FROM monitoring.opportunities ORDER BY signal_at_epoch_ms DESC LIMIT 100""").fetchall()
    items = [
        {
            "signal_id": row[0],
            "bot_id": row[1],
            "strategy_version": row[2],
            "symbol": row[3],
            "direction": row[4],
            "signal_price": row[5],
            "decision": row[6],
            "reason": row[7],
            "traffic_light": row[8],
            "state": row[9],
            "mfe_pct": row[10],
            "mae_pct": row[11],
            "hits": json.loads(row[12]),
            "samples": row[13],
            "signal_at_epoch_ms": row[14],
        }
        for row in rows
    ]
    return {"counts": counts, "items": items}


def entry_shadow_state() -> dict[str, object]:
    if not ENTRY_SHADOW_STATE.exists():
        return {"state": "не запущен", "running": False, "assets": []}
    state = json.loads(ENTRY_SHADOW_STATE.read_text(encoding="utf-8"))
    risks = execution_liquidity_risks()
    state["assets"] = [
        item for item in state.get("assets", []) if item.get("symbol") not in BYBIT_KZ_UNSUPPORTED
    ]
    for item in state["assets"]:
        item["liquidity_risk"] = risks.get(str(item.get("symbol")))
    return state


def mayak_v2_state() -> dict[str, object]:
    if not MAYAK_V2_STATE.exists():
        return {"state": "не запущен", "confidence": 0, "coins": {}}
    try:
        return json.loads(MAYAK_V2_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "ошибка чтения", "confidence": 0, "coins": {}}


def execution_liquidity_risks() -> dict[str, dict[str, object]]:
    """Classify symbols from observed stop trigger-to-fill execution, not turnover alone."""
    global _liquidity_cache
    now = time.monotonic()
    if _liquidity_cache and now - _liquidity_cache[0] < 30:
        return _liquidity_cache[1]
    risks: dict[str, dict[str, object]] = {}
    try:
        with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
            rows = connection.execute(
                "SELECT payload_json FROM runtime.private_events WHERE topic='order.linear' "
                "ORDER BY received_at_epoch_ms DESC LIMIT 5000"
            ).fetchall()
        for (payload,) in rows:
            decoded = json.loads(payload)
            items = decoded if isinstance(decoded, list) else decoded.get("data", [])
            for item in items:
                if item.get("orderStatus") != "Filled":
                    continue
                trigger, fill = (
                    float(item.get("triggerPrice") or 0),
                    float(item.get("avgPrice") or 0),
                )
                if trigger <= 0 or fill <= 0:
                    continue
                adverse = (
                    (trigger - fill) / trigger
                    if item.get("side") == "Sell"
                    else (fill - trigger) / trigger
                ) * 100
                symbol = str(item.get("symbol") or "")
                if adverse >= 0.15 and adverse > float(
                    risks.get(symbol, {}).get("observed_slippage_pct", 0)
                ):
                    risks[symbol] = {
                        "status": "низкая ликвидность",
                        "detail": "зафиксировано повышенное проскальзывание защитного выхода",
                        "observed_slippage_pct": round(adverse, 4),
                    }
    except (psycopg.Error, json.JSONDecodeError, TypeError, ValueError):
        return {}
    _liquidity_cache = (now, risks)
    return risks


def live_rearm_readiness(connection: psycopg.Connection) -> dict[str, object]:
    reasons: list[str] = []
    now_ms = int(time.time() * 1000)
    try:
        private = json.loads(PRIVATE_RUNTIME_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        private = {}
    private_state = private.get("private", {}) if isinstance(private, dict) else {}
    updated_at = (
        int(private.get("updated_at_epoch") or 0)
        if isinstance(private, dict)
        else 0
    )
    if not isinstance(private_state, dict) or private_state.get("state") != "connected":
        reasons.append("private Bybit WS is not connected")
    if updated_at <= 0 or now_ms - updated_at * 1000 > 15_000:
        reasons.append("private runtime state is stale")
    latest = connection.execute(
        """SELECT finished_at_epoch_ms,ok FROM runtime.reconciliation_runs
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    if not latest or not bool(latest[1]) or now_ms - int(latest[0]) > 15_000:
        reasons.append("fresh exchange reconciliation is required")
    wallet = connection.execute(
        "SELECT refreshed_at_epoch_ms FROM runtime.wallet_latest WHERE singleton=1"
    ).fetchone()
    if not wallet or now_ms - int(wallet[0]) > 15_000:
        reasons.append("mandatory exchange state is stale")
    settings = connection.execute(
        "SELECT updated_at_epoch_ms FROM runtime.trade_settings WHERE singleton=1"
    ).fetchone()
    settings_version = None if not settings else str(settings[0])
    if settings is None:
        reasons.append("server trading settings are missing")
    ambiguous = connection.execute(
        """SELECT 1 FROM runtime.trade_commands
           WHERE command_type='entry' AND state IN ('queued','running') LIMIT 1"""
    ).fetchone()
    if ambiguous:
        reasons.append("ambiguous local Entry command remains")
    pending = connection.execute(
        """SELECT 1 FROM runtime.hot_orders o
           JOIN runtime.trade_commands c ON c.command_id=o.order_link_id
           WHERE c.command_type='entry'
             AND o.order_status IN ('New','PartiallyFilled','Untriggered') LIMIT 1"""
    ).fetchone()
    if pending:
        reasons.append("bot-owned pending Entry order remains")
    for symbol, raw_payload in connection.execute(
        "SELECT symbol,payload_json FROM runtime.hot_positions"
    ).fetchall():
        raw = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
        if (
            float(raw.get("stopLoss") or 0) <= 0
            and float(raw.get("trailingStop") or 0) <= 0
        ):
            reasons.append(
                f"current real position has no exchange-confirmed protection: {symbol}"
            )
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    if gate and bool(gate[0]):
        reasons.append("new Entry gate is already open")
    return {
        "rearm_ready": not reasons,
        "reasons": reasons,
        "settings_version": settings_version,
    }


def live_trading_state() -> dict[str, object]:
    tickers = live_tickers()
    liquidity_risks = execution_liquidity_risks()
    with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
        wallet = connection.execute("""SELECT refreshed_at_epoch_ms,total_equity,wallet_balance,
            available_balance,payload_json FROM runtime.wallet_latest WHERE singleton=1""").fetchone()
        rows = connection.execute("""SELECT symbol,position_idx,side,size,entry_price,leverage,
            refreshed_at_epoch_ms,payload_json FROM runtime.hot_positions ORDER BY symbol""").fetchall()
        trailing_rows = connection.execute("""SELECT DISTINCT ON (symbol) symbol,
            payload_json,exchange_updated_ms FROM runtime.hot_orders
            WHERE payload_json::jsonb->>'stopOrderType'='TrailingStop'
            ORDER BY symbol,exchange_updated_ms DESC""").fetchall()
        pending_order_rows = connection.execute("""SELECT symbol,side,qty,price,leaves_qty,
            refreshed_at_epoch_ms,payload_json FROM runtime.hot_orders
            WHERE order_status IN ('New','PartiallyFilled','Untriggered')
              AND COALESCE(payload_json::jsonb->>'reduceOnly','false') <> 'true'
              AND COALESCE(payload_json::jsonb->>'closeOnTrigger','false') <> 'true'
              AND COALESCE(payload_json::jsonb->>'orderType','') = 'Limit'
            ORDER BY exchange_updated_ms DESC""").fetchall()
        settings = connection.execute(
            """SELECT stake_usdt,leverage,enabled_symbols_json,entry_offset_pct,
            entry_limit_ttl_seconds,auto_profit_protection,auto_trailing_stop,
            trailing_distance_pct,entry_policy,updated_at_epoch_ms
            FROM runtime.trade_settings WHERE singleton=1"""
        ).fetchone()
        gate = connection.execute(
            "SELECT enabled,reason FROM control.execution_gates WHERE mode='mainnet'"
        ).fetchone()
        rearm = live_rearm_readiness(connection)
        commands = connection.execute(
            "SELECT command_id,command_type,symbol,state,requested_at_epoch_ms,error FROM runtime.trade_commands ORDER BY requested_at_epoch_ms DESC LIMIT 20"
        ).fetchall()
        supervisor_rows = []
        if connection.execute("SELECT to_regclass('supervisor.snapshots')").fetchone()[0]:
            supervisor_rows = connection.execute("""SELECT DISTINCT ON (symbol)
                symbol,observed_at_epoch_ms,state,shadow_action,snapshot_json
                FROM supervisor.snapshots ORDER BY symbol,observed_at_epoch_ms DESC""").fetchall()
        execution_rows = connection.execute("""SELECT symbol,side,exec_price,exec_qty,exec_fee,
            exec_time_ms,order_id,payload_json FROM (
                SELECT symbol,side,exec_price,exec_qty,exec_fee,exec_time_ms,order_id,payload_json
                FROM runtime.executions ORDER BY exec_time_ms DESC LIMIT 5000
            ) AS recent_executions ORDER BY exec_time_ms ASC""").fetchall()
        lifecycle_rows = []
        if connection.execute("SELECT to_regclass('analyst.trade_lifecycles')").fetchone()[0]:
            lifecycle_rows = connection.execute("""SELECT trade_id,position_id,symbol,side,
                strategy_id,strategy_version,opened_at,closed_at,lifecycle_state,
                data_completeness,diagnosis_class,actual_net_pnl,lifecycle_json,
                actual_net_without_funding,bot_instance_id,entry_command_id,
                geometry_handoff_id
                FROM analyst.trade_lifecycles ORDER BY closed_at DESC NULLS LAST LIMIT 500""").fetchall()
        ownership_rows = []
        if connection.execute("SELECT to_regclass('runtime.position_ownership')").fetchone()[0]:
            ownership_rows = connection.execute(
                """SELECT position_id,trade_id,bot_instance_id,strategy_id,
                strategy_version,signal_id,entry_command_id,geometry_handoff_id,
                symbol,side,actual_avg_fill,actual_qty,fill_at,state
                FROM runtime.position_ownership ORDER BY fill_at DESC LIMIT 500"""
            ).fetchall()
        exact_exit_rows = []
        has_exact_exit_table = bool(connection.execute(
            "SELECT to_regclass('runtime.position_exit_attribution')"
        ).fetchone()[0])
        if has_exact_exit_table:
            exact_exit_rows = connection.execute(
                """SELECT a.position_id,a.trade_id,o.symbol,o.side,a.closed_at,
                a.actual_exit_qty,a.actual_exit_avg_fill,a.exit_owner,
                a.exit_mechanism,a.gross_pnl,a.entry_fee_actual,a.exit_fee_actual,
                a.actual_net_without_funding,a.actual_net_pnl,
                a.entry_to_exit_price_move_pct,a.exit_execution_ids,
                a.economics_completeness,a.link_status
                FROM runtime.position_exit_attribution a
                JOIN runtime.position_ownership o USING(position_id,trade_id)
                WHERE a.link_status='EXACT'
                ORDER BY a.closed_at DESC LIMIT 1000"""
            ).fetchall()
        market_context = None
        if connection.execute(
            "SELECT to_regclass('mayak_v2.shared_market_contexts')"
        ).fetchone()[0]:
            market_context = connection.execute(
                """SELECT market_context_id,observed_at,mayak_version,schema_version,
                data_quality,payload FROM mayak_v2.shared_market_contexts
                ORDER BY observed_at DESC LIMIT 1"""
            ).fetchone()
        session_row = connection.execute("""SELECT changed_at_epoch_ms FROM runtime.trade_settings_history
            WHERE new_settings->>'entry_policy'='m3_full_live_v1'
            ORDER BY changed_at_epoch_ms DESC LIMIT 1""").fetchone()
        session_start_ms = int(session_row[0]) if session_row else 0
        funnel_rows = connection.execute("""SELECT decided_at_epoch_ms,decision,reason,
            details_json,entry_policy FROM runtime.entry_decisions
            WHERE decided_at_epoch_ms >= LEAST(%s,%s)""",
            (session_start_ms or int(time.time()*1000), int((time.time()-86400)*1000))).fetchall()
    trailing_by_symbol = {str(row[0]): (json.loads(row[1]), row[2]) for row in trailing_rows}
    supervisor_by_symbol = {
        str(row[0]): {
            "observed_at_epoch_ms": row[1],
            "state": row[2],
            "shadow_action": row[3],
            "snapshot": row[4],
        }
        for row in supervisor_rows
    }
    wallet_raw = json.loads(wallet[4]) if wallet else {}
    wallet_usdt = next(
        (coin for coin in wallet_raw.get("coin", []) if coin.get("coin") == "USDT"), {}
    )
    reserved_for_orders = wallet_raw.get("totalOrderIM") or wallet_usdt.get("totalOrderIM") or "0"
    reserved_for_positions = (
        wallet_raw.get("totalPositionIM") or wallet_usdt.get("totalPositionIM") or "0"
    )
    pending_orders = []
    configured_leverage = float(settings[1]) if settings and settings[1] else 1.0
    for row in pending_order_rows:
        raw = json.loads(row[6])
        leaves_qty = float(row[4] or 0)
        price = float(row[3] or raw.get("price") or 0)
        notional = float(raw.get("leavesValue") or leaves_qty * price)
        pending_orders.append(
            {
                "symbol": row[0],
                "side": row[1],
                "qty": row[2],
                "price": row[3],
                "leaves_qty": row[4],
                "notional_usdt": notional,
                "estimated_margin_usdt": notional / configured_leverage
                if configured_leverage > 0
                else None,
                "refreshed_at_epoch_ms": row[5],
            }
        )
    positions = []
    for row in rows:
        raw = json.loads(row[7])
        ticker = tickers.get(str(row[0]), {})
        executable_price = ticker.get("bid_price") if row[2] == "Buy" else ticker.get("ask_price")
        trailing_order, trailing_updated = trailing_by_symbol.get(str(row[0]), ({}, None))
        positions.append(
            {
                "symbol": row[0],
                "position_idx": row[1],
                "side": row[2],
                "size": row[3],
                "entry_price": row[4],
                "leverage": row[5],
                "refreshed_at_epoch_ms": row[6],
                "break_even_price": raw.get("breakEvenPrice") or raw.get("avgPrice"),
                "mark_price": raw.get("markPrice"),
                "stop_loss": raw.get("stopLoss"),
                "last_price": ticker.get("last_price"),
                "executable_close_price": executable_price
                or ticker.get("last_price")
                or raw.get("markPrice"),
                "liquidity_risk": liquidity_risks.get(str(row[0])),
                "trailing_stop": raw.get("trailingStop"),
                "trailing_trigger_price": trailing_order.get("triggerPrice"),
                "trailing_trigger_by": trailing_order.get("triggerBy"),
                "trailing_updated_at_epoch_ms": trailing_updated,
                "unrealised_pnl": raw.get("unrealisedPnl"),
                "position_value": raw.get("positionValue"),
                "supervisor": supervisor_by_symbol.get(str(row[0])),
            }
        )
    open_lots: dict[tuple[str, str], dict[str, float]] = {}
    closed_groups: dict[tuple[str, str], dict[str, object]] = {}
    for row in execution_rows:
        raw = json.loads(row[7])
        symbol, side = str(row[0]), str(row[1])
        qty, fee = float(row[3]), float(row[4])
        closed_qty = float(raw.get("closedSize") or 0)
        if closed_qty <= 0:
            lot = open_lots.setdefault((symbol, side), {"qty": 0.0, "fee": 0.0, "notional": 0.0})
            lot["qty"] += qty
            lot["fee"] += fee
            lot["notional"] += qty * float(row[2])
            continue
        opening_side = "Buy" if side == "Sell" else "Sell"
        lot = open_lots.setdefault(
            (symbol, opening_side), {"qty": 0.0, "fee": 0.0, "notional": 0.0}
        )
        allocated_entry_fee = 0.0
        allocated_entry_notional = 0.0
        if lot["qty"] > 0:
            allocated_qty = min(qty, lot["qty"])
            allocated_entry_fee = lot["fee"] * allocated_qty / lot["qty"]
            allocated_entry_notional = lot["notional"] * allocated_qty / lot["qty"]
            lot["qty"] -= allocated_qty
            lot["fee"] = max(0.0, lot["fee"] - allocated_entry_fee)
            lot["notional"] = max(0.0, lot["notional"] - allocated_entry_notional)
        key = (symbol, str(row[6]))
        item = closed_groups.setdefault(
            key,
            {
                "symbol": symbol,
                "side": side,
                "price": 0.0,
                "qty": 0.0,
                "gross_pnl": 0.0,
                "entry_fee": 0.0,
                "exit_fee": 0.0,
                "entry_notional": 0.0,
                "closed_at_epoch_ms": row[5],
                "reason": (
                    raw.get("stopOrderType")
                    if raw.get("stopOrderType") not in {None, "", "UNKNOWN"}
                    else raw.get("createType") or "закрытие"
                ),
                "exec_ids": [],
            },
        )
        item["exec_ids"].append(str(raw.get("execId") or ""))
        old_qty = float(item["qty"])
        item["price"] = (float(item["price"]) * old_qty + float(row[2]) * qty) / (old_qty + qty)
        item["qty"] = old_qty + qty
        item["gross_pnl"] = float(item["gross_pnl"]) + float(raw.get("execPnl") or 0)
        item["entry_fee"] = float(item["entry_fee"]) + allocated_entry_fee
        item["entry_notional"] = float(item["entry_notional"]) + allocated_entry_notional
        item["exit_fee"] = float(item["exit_fee"]) + fee
        item["closed_at_epoch_ms"] = max(int(item["closed_at_epoch_ms"]), int(row[5]))
    for item in closed_groups.values():
        item["net_pnl"] = (
            float(item["gross_pnl"]) - float(item["entry_fee"]) - float(item["exit_fee"])
        )
        item["gross_move_pct"] = (
            float(item["gross_pnl"]) / float(item["entry_notional"]) * 100
            if float(item["entry_notional"]) > 0
            else None
        )
    recent_closed = sorted(
        closed_groups.values(), key=lambda item: int(item["closed_at_epoch_ms"]), reverse=True
    )
    if has_exact_exit_table:
        recent_closed = [
            {
                "position_id": row[0],
                "trade_id": row[1],
                "symbol": row[2],
                "side": row[3],
                "closed_at_epoch_ms": int(row[4].timestamp() * 1000),
                "qty": float(row[5] or 0),
                "price": float(row[6] or 0),
                "exit_owner": row[7],
                "exit_mechanism": row[8],
                "reason": {
                    "INITIAL_HARD_STOP": "исходный защитный стоп",
                    "PROFIT_PROTECTION_STOP": "защита чистой прибыли",
                    "TRAILING_STOP": "плавающий стоп",
                    "TAKE_PROFIT": "фиксация цели",
                    "STRATEGY_EXIT": "выход стратегии",
                    "OWNER_MANUAL_STOP": "стоп изменён владельцем",
                    "OWNER_MANUAL_CLOSE": "закрыто владельцем",
                    "TECHNICAL_CLOSE": "техническое закрытие",
                    "UNKNOWN": "точный механизм не доказан",
                }.get(str(row[8]), "точный механизм не доказан"),
                "gross_pnl": float(row[9] or 0),
                "entry_fee": float(row[10] or 0),
                "exit_fee": float(row[11] or 0),
                "net_pnl": float(row[12]) if row[12] is not None else None,
                "actual_net_pnl": float(row[13]) if row[13] is not None else None,
                "gross_move_pct": float(row[14]) if row[14] is not None else None,
                "exec_ids": list(row[15] or []),
                "economics_completeness": row[16],
                "link_status": row[17],
            }
            for row in exact_exit_rows
        ]
    lifecycles = []
    for row in lifecycle_rows:
        lifecycle = row[12] if isinstance(row[12], dict) else json.loads(row[12])
        card = {
            "trade_id": row[0], "position_id": row[1], "symbol": row[2], "side": row[3],
            "strategy_id": row[4], "strategy_version": row[5],
            "opened_at": None if row[6] is None else row[6].isoformat(),
            "closed_at": None if row[7] is None else row[7].isoformat(),
            "state": row[8], "data_completeness": row[9],
            "diagnosis": row[10],
            "actual_net_pnl": None if row[11] is None else str(row[11]),
            "actual_net_without_funding": None if row[13] is None else str(row[13]),
            "bot_instance_id": row[14], "entry_command_id": row[15],
            "geometry_handoff_id": row[16],
            "lifecycle": lifecycle,
        }
        lifecycles.append(card)
        if row[7] is not None:
            lifecycle_exec_ids = set(
                str(value) for value in (lifecycle.get("close_fill") or {}).get("exec_ids", [])
            )
            candidate = next(
                (
                    item for item in recent_closed
                    if lifecycle_exec_ids.intersection(item.get("exec_ids", []))
                ),
                None,
            )
            if candidate:
                candidate["trade_card"] = card
                exit_decision = lifecycle.get("exit_decision") or {}
                decision_json = exit_decision.get("decision_json") or {}
                candidate["strategy_reason"] = (
                    decision_json.get("internal_reason")
                    or exit_decision.get("internal_reason") or "UNKNOWN"
                )
    now_ms = int(time.time() * 1000)
    def funnel_window(start_ms: int) -> dict[str, object]:
        rows = [row for row in funnel_rows if int(row[0]) >= start_ms]
        decisions = {"ALLOW": 0, "BLOCK": 0}
        reasons: dict[str, int] = {}
        for row in rows:
            key = "ALLOW" if str(row[1]) in {"разрешён", "ALLOW"} else "BLOCK"
            decisions[key] += 1
            if key == "BLOCK":
                reasons[str(row[2])] = reasons.get(str(row[2]), 0) + 1
        return {"core_signals": len(rows), "decisions": decisions,
                "top_block_reasons": sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:5]}
    return {
        "wallet": None
        if wallet is None
        else {
            "refreshed_at_epoch_ms": wallet[0],
            "total_equity": wallet[1],
            "wallet_balance": wallet[2],
            "available_balance": wallet[3],
            "reserved_for_orders": reserved_for_orders,
            "reserved_for_positions": reserved_for_positions,
        },
        "positions": positions,
        "pending_entry_orders": pending_orders,
        "settings": {
            "stake_usdt": settings[0],
            "leverage": settings[1],
            "enabled_symbols": [
                symbol for symbol in json.loads(settings[2]) if symbol not in BYBIT_KZ_UNSUPPORTED
            ],
            "entry_offset_pct": settings[3],
            "entry_limit_ttl_seconds": settings[4],
            "auto_profit_protection": bool(settings[5]),
            "auto_trailing_stop": bool(settings[6]),
            "trailing_distance_pct": settings[7],
            "entry_policy": settings[8],
            "settings_version": str(settings[9]),
        }
        if settings
        else {
            "stake_usdt": "10",
            "leverage": 10,
            "enabled_symbols": [],
            "entry_offset_pct": "0.00",
            "entry_limit_ttl_seconds": 30,
            "auto_profit_protection": True,
            "auto_trailing_stop": True,
            "trailing_distance_pct": "0.30",
            "entry_policy": "base_entry_v1",
            "settings_version": None,
        },
        "rearm": rearm,
        "rearm_ready": bool(rearm.get("rearm_ready")),
        "gate": {"enabled": bool(gate[0]), "reason": gate[1]}
        if gate
        else {"enabled": False, "reason": "шлюз не настроен"},
        "commands": [
            {
                "command_id": r[0],
                "type": r[1],
                "symbol": r[2],
                "state": r[3],
                "requested_at_epoch_ms": r[4],
                "error": r[5],
            }
            for r in commands
        ],
        "recent_closed": recent_closed,
        "trade_lifecycles": lifecycles,
        "shared_market_context": None if market_context is None else {
            "market_context_id": market_context[0],
            "observed_at": market_context[1].isoformat(),
            "mayak_version": market_context[2],
            "schema_version": market_context[3],
            "data_quality": market_context[4],
            "payload": market_context[5],
        },
        "position_ownership": [
            {
                "position_id": row[0], "trade_id": row[1], "bot_instance_id": row[2],
                "strategy_id": row[3], "strategy_version": row[4], "signal_id": row[5],
                "entry_command_id": row[6], "geometry_handoff_id": row[7],
                "symbol": row[8], "side": row[9], "actual_avg_fill": str(row[10]),
                "actual_qty": str(row[11]), "fill_at": row[12].isoformat(), "state": row[13],
            }
            for row in ownership_rows
        ],
        "entry_funnel": {
            "last_hour": funnel_window(now_ms - 3_600_000),
            "session": funnel_window(session_start_ms),
            "last_24h": funnel_window(now_ms - 86_400_000),
        },
        "strategy_controls": {
            "strategy": "M3 FULL LIVE V1.1", "installed_version": "1.1.0",
            "loaded_version": "1.1.0", "entry_profile": "M3_V1_LONG/SHORT_ENTRY 1.0.0-owner-live",
            "accepted_statuses": ["EXCELLENT_MATCH","GOOD_MATCH","PARTIAL_MATCH"],
            "max_context_age_seconds": 90, "allowed_quality": ["HIGH","MEDIUM"],
            "hold_profile": "M3_V1_LONG/SHORT_HOLD 1.0.0-owner-live",
            "early_exit_enabled": False,
            "early_exit_state": "BROKEN + INCOMPATIBLE + protective_clean_break_against",
            "early_exit_status": "DISABLED/FALLBACK_SAFE: Entry не передаёт геометрию зон",
            "minimum_net_profit_usdt": "0.01", "exit_fee_rate": "0.00055",
            "slippage_reserve": "не меньше 0,02% и наблюдаемого проскальзывания",
            "hard_stop_pct": "-1.00",
        },
    }


SIGNAL_EXPORT_PERIODS = {
    "day": (24 * 3600, "сутки"),
    "72h": (72 * 3600, "72_часа"),
    "week": (7 * 24 * 3600, "неделя"),
    "all": (None, "весь_период"),
}
ENTRY_AUDIT_DATABASE = Path(
    os.environ.get("CRIPTA_ENTRY_SHADOW_DB", "/var/lib/cripta/entry_shadow/workbench.db")
)
ENTRY_AUDIT_EVENT_TYPES = (
    "CANDIDATE_ARMED",
    "CANDIDATE_CLEARED",
    "PRELIMIT_ARM_SHADOW",
    "PRELIMIT_TOUCH_SHADOW",
    "PRELIMIT_CANCEL_SHADOW",
    "TOUCH_VETO",
    "TOUCH_BLOCKED",
    "CORE_SIGNAL",
    "EARLY_FAILURE",
    "STREAM_GAP",
    "WARMUP_ERROR",
)
SIGNAL_ANALYSIS_TABLES = (
    ("monitoring.opportunities", "signal_at_epoch_ms", "epoch_ms"),
    ("monitoring.opportunity_events", "at_epoch_ms", "epoch_ms"),
    ("monitoring.entry_dispatcher_shadow_decisions", "signal_at", "timestamp"),
    ("monitoring.entry_geometry_handoffs", "signal_at", "timestamp"),
    ("runtime.entry_decisions", "signal_at_epoch_ms", "epoch_ms"),
    ("runtime.m3_consumed_context", "signal_at", "timestamp"),
    ("runtime.trade_settings_history", "changed_at_epoch_ms", "epoch_ms"),
    ("research_context.event_links", "occurred_at", "timestamp"),
    ("strategy_dispatcher.runs", "observed_at", "timestamp"),
    ("strategy_dispatcher.assessments", "observed_at", "timestamp"),
    ("mayak_v2.snapshots", "observed_at", "timestamp"),
    ("mayak_v2.coin_minutes", "observed_at", "timestamp"),
    ("mayak_v2.events", "occurred_at", "timestamp"),
    ("mayak_v2.state_events", "occurred_at", "timestamp"),
    ("mayak_v2.observation_journal", "observed_at", "timestamp"),
    ("mayak_v2.liquidations", "occurred_at", "timestamp"),
    ("mayak_v2.shared_market_contexts", "observed_at", "timestamp"),
)


def _csv_cell(value: object) -> object:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _write_cursor_csv(archive: zipfile.ZipFile, name: str, cursor: object) -> int:
    description = getattr(cursor, "description", None)
    if not description:
        raise RuntimeError(f"экспорт {name}: запрос не вернул столбцы")
    columns = [item.name if hasattr(item, "name") else item[0] for item in description]
    count = 0
    with archive.open(name, "w") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        writer = csv.writer(text, delimiter=";")
        writer.writerow(columns)
        for row in cursor:
            writer.writerow([_csv_cell(value) for value in row])
            count += 1
        text.flush()
        text.detach()
    return count


def _first_hit_state(first_hits_json: object, target: str) -> tuple[object, object, str]:
    try:
        first_hits = (
            json.loads(first_hits_json)
            if isinstance(first_hits_json, str)
            else dict(first_hits_json or {})
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        first_hits = {}
    target_at = first_hits.get(target)
    stop_at = first_hits.get("-1.0")
    if target_at is not None and (stop_at is None or int(target_at) < int(stop_at)):
        state = "TARGET_FIRST"
    elif stop_at is not None and (target_at is None or int(stop_at) < int(target_at)):
        state = "STOP_FIRST"
    elif target_at is None and stop_at is None:
        state = "UNRESOLVED"
    else:
        state = "SAME_TIMESTAMP"
    return target_at, stop_at, state


def _write_signal_context_csv(
    archive: zipfile.ZipFile,
    connection: psycopg.Connection,
    cutoff_ms: int | None,
) -> tuple[int, dict[str, int]]:
    query = """
        WITH signal_link AS (
            SELECT DISTINCT ON (reference_id)
                   reference_id,
                   observed_mayak_snapshot_id,
                   observed_mayak_at,
                   observed_dispatcher_snapshot_id,
                   observed_dispatcher_at,
                   observed_context,
                   consumed_context,
                   link_quality,
                   provenance
            FROM research_context.event_links
            WHERE event_type='SIGNAL'
            ORDER BY reference_id, linked_at DESC
        ), shadow AS (
            SELECT DISTINCT ON (m3_setup_id)
                   m3_setup_id,
                   shadow_dispatcher_decision,
                   consumed_dispatcher_assessment_id,
                   consumed_mayak_snapshot_id,
                   assessment_observed_at,
                   profile_id,
                   profile_version,
                   data_quality,
                   coverage,
                   decision_reason_ru,
                   trading_effect,
                   payload
            FROM monitoring.entry_dispatcher_shadow_decisions
            ORDER BY m3_setup_id, created_at DESC
        )
        SELECT
            o.signal_id,
            o.bot_id,
            o.strategy_version AS scanner_strategy_version,
            o.symbol,
            o.direction,
            to_timestamp(o.signal_at_epoch_ms / 1000.0) AS signal_at,
            o.signal_at_epoch_ms,
            o.signal_price,
            o.decision AS scanner_decision,
            o.decision_reason AS scanner_decision_reason,
            o.traffic_light,
            o.horizon_seconds,
            o.state AS observation_state,
            o.last_price,
            o.max_favorable_pct,
            o.max_adverse_pct,
            o.first_hits_json,
            o.samples,
            o.finalized_at_epoch_ms,
            link.observed_mayak_snapshot_id,
            link.observed_mayak_at,
            link.observed_dispatcher_snapshot_id,
            link.observed_dispatcher_at,
            link.link_quality,
            link.provenance AS observed_link_provenance,
            link.observed_context,
            link.consumed_context,
            mayak.state AS mayak_state,
            mayak.confidence AS mayak_confidence,
            mayak.engine_version AS mayak_engine_version,
            mayak.payload AS mayak_payload,
            dispatcher.assessment_id AS dispatcher_assessment_id,
            dispatcher.profile_id AS dispatcher_profile_id,
            dispatcher.profile_version AS dispatcher_profile_version,
            dispatcher.suitability AS dispatcher_suitability,
            dispatcher.confidence AS dispatcher_confidence,
            dispatcher.status AS dispatcher_status,
            dispatcher.data_quality AS dispatcher_data_quality,
            dispatcher.coverage AS dispatcher_coverage,
            dispatcher.payload AS dispatcher_payload,
            coin.observed_at AS coin_context_at,
            coin.spot_net_usd,
            coin.spot_turnover_usd,
            coin.derivatives_net_usd,
            coin.derivatives_turnover_usd,
            coin.return_5m_pct,
            coin.open_interest,
            coin.open_interest_change_pct,
            coin.funding_rate,
            coin.mark_price,
            coin.index_price,
            coin.long_ratio,
            coin.short_ratio,
            coin.spot_bid_change_pct,
            coin.spot_ask_change_pct,
            coin.derivatives_bid_change_pct,
            coin.derivatives_ask_change_pct,
            coin.large_spot_buy_usd,
            coin.large_spot_sell_usd,
            coin.large_derivatives_buy_usd,
            coin.large_derivatives_sell_usd,
            coin.source_quality AS coin_source_quality,
            entry.decision AS entry_decision,
            entry.reason AS entry_reason,
            entry.details_json AS entry_details,
            entry.entry_policy,
            entry.policy_version AS entry_policy_version,
            entry.settings_version,
            entry.mayak_snapshot_id AS entry_recorded_mayak_snapshot_id,
            entry.mayak_snapshot_time AS entry_recorded_mayak_snapshot_time,
            advisory.context_type AS advisory_context_type,
            advisory.trading_effect AS advisory_trading_effect,
            advisory.assessment_id AS advisory_assessment_id,
            advisory.mayak_snapshot_id AS advisory_mayak_snapshot_id,
            advisory.market_context_id AS advisory_market_context_id,
            advisory.dispatcher_status AS advisory_dispatcher_status,
            advisory.reason_ru AS advisory_reason_ru,
            geometry.strategy_id,
            geometry.strategy_version,
            geometry.entry_fingerprint,
            geometry.geometry_version,
            geometry.config_fingerprint AS geometry_config_fingerprint,
            geometry.geometry_hash,
            geometry.payload AS geometry_payload,
            shadow.shadow_dispatcher_decision,
            shadow.consumed_dispatcher_assessment_id AS shadow_assessment_id,
            shadow.consumed_mayak_snapshot_id AS shadow_mayak_snapshot_id,
            shadow.assessment_observed_at AS shadow_assessment_observed_at,
            shadow.profile_id AS shadow_profile_id,
            shadow.profile_version AS shadow_profile_version,
            shadow.data_quality AS shadow_data_quality,
            shadow.coverage AS shadow_coverage,
            shadow.decision_reason_ru AS shadow_reason_ru,
            shadow.trading_effect AS shadow_trading_effect,
            shadow.payload AS shadow_payload,
            CASE
                WHEN link.observed_mayak_at IS NULL OR link.observed_dispatcher_at IS NULL
                    THEN 'MISSING'
                WHEN link.observed_mayak_at <= to_timestamp(o.signal_at_epoch_ms / 1000.0)
                 AND link.observed_dispatcher_at <= to_timestamp(o.signal_at_epoch_ms / 1000.0)
                    THEN 'YES'
                ELSE 'NO_FUTURE_CONTEXT'
            END AS causal_context_ok,
            CASE WHEN link.observed_mayak_at IS NULL THEN NULL ELSE
                extract(
                    epoch FROM (
                        to_timestamp(o.signal_at_epoch_ms / 1000.0) - link.observed_mayak_at
                    )
                ) * 1000
            END AS mayak_age_ms,
            CASE WHEN link.observed_dispatcher_at IS NULL THEN NULL ELSE
                extract(
                    epoch FROM (
                        to_timestamp(o.signal_at_epoch_ms / 1000.0) - link.observed_dispatcher_at
                    )
                ) * 1000
            END AS dispatcher_age_ms,
            CASE WHEN coin.observed_at IS NULL THEN NULL ELSE
                extract(
                    epoch FROM (
                        to_timestamp(o.signal_at_epoch_ms / 1000.0) - coin.observed_at
                    )
                ) * 1000
            END AS coin_context_age_ms
        FROM monitoring.opportunities o
        LEFT JOIN signal_link link ON link.reference_id=o.signal_id
        LEFT JOIN mayak_v2.snapshots mayak
               ON mayak.id::text=link.observed_mayak_snapshot_id::text
        LEFT JOIN strategy_dispatcher.assessments dispatcher
               ON dispatcher.snapshot_id::text=link.observed_dispatcher_snapshot_id::text
              AND dispatcher.profile_id=(
                    CASE WHEN o.direction='long'
                         THEN 'M3_V1_LONG_ENTRY' ELSE 'M3_V1_SHORT_ENTRY' END
              )
        LEFT JOIN LATERAL (
            SELECT cm.*
            FROM mayak_v2.coin_minutes cm
            WHERE cm.symbol=o.symbol
              AND cm.observed_at <= to_timestamp(o.signal_at_epoch_ms / 1000.0)
            ORDER BY cm.observed_at DESC
            LIMIT 1
        ) coin ON TRUE
        LEFT JOIN runtime.entry_decisions entry ON entry.signal_id=o.signal_id
        LEFT JOIN runtime.m3_consumed_context advisory ON advisory.signal_id=o.signal_id
        LEFT JOIN monitoring.entry_geometry_handoffs geometry ON geometry.signal_id=o.signal_id
        LEFT JOIN shadow ON shadow.m3_setup_id=o.signal_id
    """
    parameters: tuple[object, ...] = ()
    if cutoff_ms is not None:
        query += " WHERE o.signal_at_epoch_ms >= %s"
        parameters = (cutoff_ms,)
    query += " ORDER BY o.signal_at_epoch_ms"
    cursor = connection.execute(query, parameters)
    base_columns = [item.name for item in cursor.description]
    extra_columns = (
        "first_plus_0_10_at_epoch_ms",
        "first_minus_1_00_at_epoch_ms_for_be",
        "plus_0_10_vs_minus_1",
        "first_plus_1_10_at_epoch_ms",
        "first_minus_1_00_at_epoch_ms_for_target",
        "plus_1_10_vs_minus_1",
    )
    summary = {
        "signals": 0,
        "be_target_first": 0,
        "be_stop_first": 0,
        "be_unresolved": 0,
        "plus_1_10_target_first": 0,
        "plus_1_10_stop_first": 0,
        "plus_1_10_unresolved": 0,
        "causal_context_yes": 0,
        "causal_context_missing": 0,
        "causal_context_violation": 0,
    }
    first_hits_index = base_columns.index("first_hits_json")
    causal_index = base_columns.index("causal_context_ok")
    with archive.open("01_SIGNAL_CONTEXT.csv", "w") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        writer = csv.writer(text, delimiter=";")
        writer.writerow(base_columns + list(extra_columns))
        for row in cursor:
            be_at, be_stop_at, be_state = _first_hit_state(row[first_hits_index], "+0.1")
            target_at, target_stop_at, target_state = _first_hit_state(
                row[first_hits_index], "+1.1"
            )
            writer.writerow(
                [_csv_cell(value) for value in row]
                + [be_at, be_stop_at, be_state, target_at, target_stop_at, target_state]
            )
            summary["signals"] += 1
            if be_state == "TARGET_FIRST":
                summary["be_target_first"] += 1
            elif be_state == "STOP_FIRST":
                summary["be_stop_first"] += 1
            else:
                summary["be_unresolved"] += 1
            if target_state == "TARGET_FIRST":
                summary["plus_1_10_target_first"] += 1
            elif target_state == "STOP_FIRST":
                summary["plus_1_10_stop_first"] += 1
            else:
                summary["plus_1_10_unresolved"] += 1
            causal = row[causal_index]
            if causal == "YES":
                summary["causal_context_yes"] += 1
            elif causal == "MISSING":
                summary["causal_context_missing"] += 1
            else:
                summary["causal_context_violation"] += 1
        text.flush()
        text.detach()
    return summary["signals"], summary


def _write_postgresql_period_tables(
    archive: zipfile.ZipFile,
    connection: psycopg.Connection,
    cutoff_seconds: float | None,
    cutoff_ms: int | None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, time_column, time_kind in SIGNAL_ANALYSIS_TABLES:
        schema, table_name = table.split(".")
        query = f'SELECT * FROM "{schema}"."{table_name}"'
        parameters: tuple[object, ...] = ()
        if cutoff_seconds is not None:
            if time_kind == "timestamp":
                query += f' WHERE "{time_column}" >= to_timestamp(%s)'
                parameters = (cutoff_seconds,)
            else:
                query += f' WHERE "{time_column}" >= %s'
                parameters = (cutoff_ms,)
        query += f' ORDER BY "{time_column}"'
        cursor = connection.execute(query, parameters)
        path = f"postgresql/{table}.csv"
        counts[table] = _write_cursor_csv(archive, path, cursor)
    return counts


def _write_entry_audit_csv(
    archive: zipfile.ZipFile, cutoff_seconds: float | None
) -> int:
    if not ENTRY_AUDIT_DATABASE.is_file():
        raise RuntimeError(f"не найдена Entry audit DB: {ENTRY_AUDIT_DATABASE}")
    placeholders = ",".join("?" for _ in ENTRY_AUDIT_EVENT_TYPES)
    query = f"""
        SELECT event_id,occurred_at,symbol,event_type,status,candidate_id,direction,
               candidate_bar_at,entry_price,last_price,distance_pct,flow_state,oi_state,
               reason,payload_json,created_at
        FROM entry_bot_candidate_events
        WHERE event_type IN ({placeholders})
    """
    parameters: list[object] = list(ENTRY_AUDIT_EVENT_TYPES)
    if cutoff_seconds is not None:
        query += " AND occurred_at >= ?"
        parameters.append(datetime.fromtimestamp(cutoff_seconds, UTC).isoformat())
    # Keep the composite (event_type, occurred_at) index usable. Consumers can
    # sort this CSV by occurred_at when a globally chronological view is needed.
    query += " ORDER BY event_type, occurred_at, id"
    connection = sqlite3.connect(
        f"file:{ENTRY_AUDIT_DATABASE}?mode=ro", uri=True, timeout=30
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        cursor = connection.execute(query, tuple(parameters))
        return _write_cursor_csv(archive, "02_ENTRY_AUDIT_EVENTS.csv", cursor)
    finally:
        connection.close()


def export_signal_analysis_bundle(period: str) -> dict[str, object]:
    if period not in SIGNAL_EXPORT_PERIODS:
        raise ValueError("недопустимый период наблюдения сигналов")
    seconds, period_label = SIGNAL_EXPORT_PERIODS[period]
    now_seconds = time.time()
    cutoff_seconds = now_seconds - seconds if seconds is not None else None
    cutoff_ms = int(cutoff_seconds * 1000) if cutoff_seconds is not None else None
    stamp = time.strftime("%Y%m%d_%H%M%S")
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    final_path = REPORT_ROOT / f"аналитика_сигналов_{period_label}_{stamp}.zip"
    partial_path = final_path.with_suffix(".zip.partial")
    partial_path.unlink(missing_ok=True)
    source_head = "unknown"
    try:
        source_head = subprocess.run(
            ["git", "-C", "/srv/cripta/source_checkout", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        source_head = "unknown"
    installed_marker = APP_ROOT / "PROJECT_GIT_HEAD.txt"
    installed_commit = (
        installed_marker.read_text(encoding="utf-8").strip()
        if installed_marker.is_file()
        else "unknown"
    )
    manifest: dict[str, object] = {
        "format": "CRIPTA_SIGNAL_ANALYSIS_EXPORT_V1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "period": period,
        "period_label": period_label,
        "period_start_utc": (
            datetime.fromtimestamp(cutoff_seconds, UTC).isoformat()
            if cutoff_seconds is not None
            else None
        ),
        "period_end_utc": datetime.fromtimestamp(now_seconds, UTC).isoformat(),
        "source_git_head": source_head,
        "installed_commit_marker": installed_commit,
        "postgresql_database": "cripta",
        "entry_audit_database": str(ENTRY_AUDIT_DATABASE),
        "entry_audit_event_types": list(ENTRY_AUDIT_EVENT_TYPES),
        "causality_rule": "context_time <= signal_time; future context is forbidden",
        "trading_effect": "NONE: export/Analyst observation only",
        "files": {},
        "summary": {},
    }
    try:
        with zipfile.ZipFile(
            partial_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            with psycopg.connect(
                "dbname=cripta user=cripta host=/var/run/postgresql"
            ) as connection:
                signal_rows, summary = _write_signal_context_csv(
                    archive, connection, cutoff_ms
                )
                manifest["summary"] = summary
                manifest["files"]["01_SIGNAL_CONTEXT.csv"] = signal_rows
                postgres_counts = _write_postgresql_period_tables(
                    archive, connection, cutoff_seconds, cutoff_ms
                )
                for table, count in postgres_counts.items():
                    manifest["files"][f"postgresql/{table}.csv"] = count
            audit_rows = _write_entry_audit_csv(archive, cutoff_seconds)
            manifest["files"]["02_ENTRY_AUDIT_EVENTS.csv"] = audit_rows
            archive.writestr(
                "README_RU.txt",
                "CRIPTA — причинный архив анализа сигналов V1\n\n"
                "Главный файл: 01_SIGNAL_CONTEXT.csv. Одна строка = один Entry signal.\n"
                "В строке рядом находятся: точные first-hit уровни, объективно существовавший\n"
                "к моменту сигнала MAYAK, Dispatcher assessment для направления Entry,\n"
                "последняя причинная coin-minute запись конкретной монеты, Entry decision,\n"
                "advisory/observed context, geometry/fingerprint и shadow Dispatcher.\n\n"
                "02_ENTRY_AUDIT_EVENTS.csv содержит ключевые события внутреннего Entry scanner:\n"
                "candidate armed/cleared, pre-limit shadow, touch veto/blocked, Core signal,\n"
                "early failure и ошибки потока. События отбираются только за выбранный период.\n\n"
                "Каталог postgresql/ содержит только записи выбранного периода из таблиц\n"
                "MAYAK, Dispatcher, Entry, causal correlator и signal observation. "
                "Это НЕ pg_dump.\n\n"
                "OBSERVED_CONTEXT показывает, какой контекст существовал к моменту события.\n"
                "CONSUMED_CONTEXT означает только реально прочитанный торговым контуром контекст.\n"
                "Наличие OBSERVED_CONTEXT не доказывает торговое влияние.\n\n"
                "Поля plus_0_10_vs_minus_1 и plus_1_10_vs_minus_1 дают точный FIRST HIT:\n"
                "TARGET_FIRST / STOP_FIRST / UNRESOLVED.\n"
            )
            archive.writestr(
                "00_MANIFEST.json",
                json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            )
        partial_path.replace(final_path)
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise
    return {
        "file": final_path.name,
        "size": final_path.stat().st_size,
        "rows": int(manifest["summary"].get("signals", 0)),
        "files": len(manifest["files"]) + 2,
        "summary": manifest["summary"],
        "url": f"/reports/{quote(final_path.name)}",
    }


def _signal_export_job_path(job_id: str) -> Path:
    token = job_id.removeprefix("signal-")
    if (
        not job_id.startswith("signal-")
        or len(token) != 32
        or any(character not in "0123456789abcdef" for character in token)
    ):
        raise ValueError("некорректный идентификатор задания анализа сигналов")
    return SIGNAL_EXPORT_JOB_ROOT / f"{job_id}.json"


def _write_signal_export_job(state: dict[str, object]) -> None:
    SIGNAL_EXPORT_JOB_ROOT.mkdir(parents=True, exist_ok=True)
    path = _signal_export_job_path(str(state["job_id"]))
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    os.replace(temporary, path)


def read_signal_export_job(job_id: str) -> dict[str, object]:
    path = _signal_export_job_path(job_id)
    if not path.is_file():
        raise FileNotFoundError("задание анализа сигналов не найдено")
    return json.loads(path.read_text(encoding="utf-8"))


def _update_signal_export_job(job_id: str, **changes: object) -> dict[str, object]:
    with _signal_export_job_lock:
        state = read_signal_export_job(job_id)
        state.update(changes)
        state["heartbeat_at_utc"] = datetime.now(UTC).isoformat()
        _write_signal_export_job(state)
        return state


def _run_signal_export_job(job_id: str, period: str) -> None:
    started = time.monotonic()
    _update_signal_export_job(
        job_id,
        status="RUNNING",
        stage="COLLECT_CONTEXT",
        percent=5,
        started_at_utc=datetime.now(UTC).isoformat(),
    )
    try:
        output = export_signal_analysis_bundle(period)
    except Exception as exc:  # job boundary must persist failure for UI
        _update_signal_export_job(
            job_id,
            status="FAILED",
            stage="FAILED",
            percent=100,
            elapsed_seconds=round(time.monotonic() - started, 1),
            error=f"{type(exc).__name__}: {exc}",
        )
        return
    _update_signal_export_job(
        job_id,
        status="DONE",
        stage="DONE",
        percent=100,
        elapsed_seconds=round(time.monotonic() - started, 1),
        output=output,
        error=None,
    )


def start_signal_export_job(period: str) -> dict[str, object]:
    if period not in SIGNAL_EXPORT_PERIODS:
        raise ValueError("недопустимый период наблюдения сигналов")
    job_id = f"signal-{secrets.token_hex(16)}"
    state: dict[str, object] = {
        "job_id": job_id,
        "kind": "SIGNAL_ANALYSIS_EXPORT_V1",
        "period": period,
        "status": "QUEUED",
        "stage": "QUEUED",
        "percent": 0,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "heartbeat_at_utc": datetime.now(UTC).isoformat(),
        "elapsed_seconds": 0,
        "output": None,
        "error": None,
    }
    _write_signal_export_job(state)
    threading.Thread(
        target=_run_signal_export_job,
        args=(job_id, period),
        daemon=True,
        name=f"signal-export-{job_id[-8:]}",
    ).start()
    return state


def export_trading_table(table: str, period: str) -> dict[str, object]:
    if table == "signals":
        return export_signal_analysis_bundle(period)
    periods = {
        "day": (24 * 3600, "сутки"),
        "week": (7 * 24 * 3600, "неделя"),
        "all": (None, "весь_период"),
    }
    if table != "closed" or period not in periods:
        raise ValueError("недопустимая таблица или период")
    seconds, period_label = periods[period]
    cutoff_ms = int((time.time() - seconds) * 1000) if seconds else 0
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "Время",
            "Монета",
            "Закрыто",
            "Цена",
            "Причина биржи",
            "До комиссий USDT",
            "Комиссия входа USDT",
            "Комиссия выхода USDT",
            "Чистый итог USDT",
        ]
    )
    rows = [
        x
        for x in live_trading_state()["recent_closed"]
        if int(x["closed_at_epoch_ms"]) >= cutoff_ms
    ]
    for x in rows:
        writer.writerow(
            [
                time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(int(x["closed_at_epoch_ms"]) / 1000),
                ),
                x["symbol"],
                x["qty"],
                x["price"],
                x["reason"],
                x["gross_pnl"],
                x["entry_fee"],
                x["exit_fee"],
                x["net_pnl"],
            ]
        )
    stem = "закрытые_реальные_сделки"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    final_path = REPORT_ROOT / f"{stem}_{period_label}_{stamp}.zip"
    with zipfile.ZipFile(final_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{stem}_{period_label}.csv", "\ufeff" + output.getvalue())
        archive.writestr(
            "ОПИСАНИЕ.txt",
            f"Период: {period_label}. Строк: {len(rows)}. "
            f"Сформировано: {time.strftime('%Y-%m-%d %H:%M:%S')}.\n",
        )
    return {
        "file": final_path.name,
        "size": final_path.stat().st_size,
        "rows": len(rows),
        "url": f"/reports/{quote(final_path.name)}",
    }


def snapshot() -> dict[str, object]:
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < 10:
        return _cache[1]
    download = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    expansion_download = (
        json.loads(EXPANSION_STATE.read_text(encoding="utf-8")) if EXPANSION_STATE.exists() else {}
    )
    active_download = expansion_download or download
    connectivity = (
        json.loads(CONNECTIVITY_STATE.read_text(encoding="utf-8"))
        if CONNECTIVITY_STATE.exists()
        else {"state": "not-started"}
    )
    private_api = (
        json.loads(PRIVATE_API_STATE.read_text(encoding="utf-8"))
        if PRIVATE_API_STATE.exists()
        else {"state": "not-checked"}
    )
    try:
        bots = bot_control_state()
    except Exception as exc:
        bots = {
            "bots": [],
            "events": [],
            "execution_gate": "database-error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    try:
        opportunities = opportunity_state()
    except Exception as exc:
        opportunities = {"counts": {}, "items": [], "error": f"{type(exc).__name__}: {exc}"}
    try:
        live_trading = live_trading_state()
    except Exception as exc:
        live_trading = {"wallet": None, "positions": [], "error": f"{type(exc).__name__}: {exc}"}
    safety = (
        json.loads(SAFETY_STATE.read_text(encoding="utf-8"))
        if SAFETY_STATE.exists()
        else {"state": "not-started"}
    )
    backup = (
        json.loads(BACKUP_STATE.read_text(encoding="utf-8"))
        if BACKUP_STATE.exists()
        else {"state": "not-started"}
    )
    private_runtime = (
        json.loads(PRIVATE_RUNTIME_STATE.read_text(encoding="utf-8"))
        if PRIVATE_RUNTIME_STATE.exists()
        else {"private": {"state": "not-started"}, "trade": {"state": "not-started"}}
    )
    health = (
        json.loads(HEALTH_STATE.read_text(encoding="utf-8"))
        if HEALTH_STATE.exists()
        else {"state": "unknown", "issues": []}
    )
    entry_shadow = (
        json.loads(ENTRY_SHADOW_STATE.read_text(encoding="utf-8"))
        if ENTRY_SHADOW_STATE.exists()
        else {"state": "не запущен", "running": False, "assets": []}
    )
    entry_comparison = (
        json.loads(ENTRY_COMPARISON_STATE.read_text(encoding="utf-8"))
        if ENTRY_COMPARISON_STATE.exists()
        else {"rows": [], "state": "not-built"}
    )
    raw = DATA_ROOT / "datasets" / "raw" / PERIOD
    symbols = []
    roles = {
        **{symbol: "trading" for symbol in TRADING_UNIVERSE},
        **{symbol: "indicator" for symbol in INDICATORS},
        **{symbol: "excluded_meme" for symbol in EXCLUDED_MEMES},
    }
    tickers = live_tickers()
    for symbol in sorted(roles):
        root = raw / symbol
        trades = root / "public_trades"
        books = root / "orderbook"
        ticker = tickers.get(symbol)
        light, reason = traffic_light(symbol, ticker)
        symbols.append(
            {
                "symbol": symbol,
                "role": roles[symbol],
                "public_trade_files": len(list(trades.glob("*.csv.gz"))) if trades.exists() else 0,
                "orderbook_files": len(list(books.glob("*.data.zip"))) if books.exists() else 0,
                "bytes": directory_bytes(root),
                "traffic_light": light,
                "traffic_reason": reason,
                "market": ticker or {},
            }
        )
    order = {"green": 0, "yellow": 1, "red": 2}
    symbols.sort(
        key=lambda x: (order[x["traffic_light"]], -float(x["market"].get("turnover24h", 0)))
    )
    disk = shutil.disk_usage(DATA_ROOT)
    research = APP_ROOT / "research"
    scripts = sorted(p.name for p in research.glob("*") if p.is_file()) if research.exists() else []
    current_root = APP_ROOT / "current"
    current_code = {
        "release": current_root.resolve().name if current_root.exists() else "",
        "bytes": directory_bytes(current_root.resolve()) if current_root.exists() else 0,
        "source_modules": len(list((current_root / "src").glob("**/*.py")))
        if current_root.exists()
        else 0,
        "research_scripts": len(list((current_root / "scripts").glob("**/*.py")))
        if current_root.exists()
        else 0,
        "tests": len(list((current_root / "tests").glob("**/*.py")))
        if current_root.exists()
        else 0,
        "status": "baseline-not-activated" if current_root.exists() else "missing",
    }
    jobs: dict[str, object] = {}
    job_items = []
    for state in ("queued", "running", "completed", "failed"):
        path = DATA_ROOT / "jobs" / state
        entries = (
            sorted(
                (p for p in path.iterdir() if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if path.exists()
            else []
        )
        jobs[state] = len(entries)
        for entry in entries[:25]:
            status_path = entry / "status.json"
            status = (
                json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
            )
            manifest = status.get("manifest", {})
            job_items.append(
                {
                    "job_id": entry.name,
                    "state": state,
                    "title": manifest.get("title", ""),
                    "line": manifest.get("line", ""),
                    "symbols": manifest.get("dataset", {}).get("symbols", []),
                    "dependencies": manifest.get("dependencies", []),
                    "duration_seconds": status.get("duration_seconds"),
                    "exit_code": status.get("exit_code"),
                    "error": status.get("error", ""),
                    "report_path": status.get("report_path", ""),
                }
            )
    jobs["items"] = job_items
    legacy_root = DATA_ROOT / "legacy"
    legacy_items = []
    if legacy_root.exists():
        for item in sorted((p for p in legacy_root.iterdir() if p.is_dir()), key=lambda p: p.name):
            reports = item / "reports"
            modules = item / "src" / "bybit_workbench" / "research"
            legacy_items.append(
                {
                    "name": item.name,
                    "bytes": directory_bytes(item),
                    "files": len(command("find", str(item), "-type", "f", "-printf", ".")),
                    "report_roots": len([p for p in reports.iterdir() if p.is_dir()])
                    if reports.exists()
                    else 0,
                    "research_modules": len(list(modules.glob("*.py"))) if modules.exists() else 0,
                    "reports": sorted(p.name for p in reports.iterdir() if p.is_dir())
                    if reports.exists()
                    else [],
                    "modules": sorted(p.name for p in modules.glob("*.py"))
                    if modules.exists()
                    else [],
                }
            )
    payload: dict[str, object] = {
        "generated_at_epoch": int(time.time()),
        "host": command("hostname"),
        "period": PERIOD,
        "evaluation_start": download.get("evaluation_start"),
        "evaluation_end": download.get("evaluation_end"),
        "download": active_download,
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
        "symbols": symbols,
        "market_selection": {
            "updated_every_seconds": 30,
            "green": "оборот ≥ $25 млн; OI ≥ $10 млн; спред ≤ 8 б.п.; |funding| ≤ 0,05%",
            "yellow": "не критично, но хотя бы один зелёный порог не выполнен",
            "red": "индикатор/мем/нет котировки либо оборот < $5 млн, OI < $2 млн, спред > 20 б.п., |funding| > 0,20%",
        },
        "jobs": jobs,
        "legacy": legacy_items,
        "scripts": scripts,
        "current_code": current_code,
        "connectivity": connectivity,
        "private_api": private_api,
        "bots": bots,
        "opportunities": opportunities,
        "live_trading": live_trading,
        "safety": safety,
        "backup": backup,
        "private_runtime": private_runtime,
        "health": health,
        "entry_comparison": entry_comparison,
        "entry_shadow": entry_shadow,
        "services": {name: service_state(name) for name in ALLOWED_SERVICES},
        "technologies": {
            "os": command("bash", "-lc", ". /etc/os-release; printf '%s' \"$PRETTY_NAME\""),
            "python": command("python3", "--version"),
            "nginx": command("nginx", "-v"),
            "postgresql": command("psql", "--version"),
            "service_manager": "systemd",
            "data_filesystem": command("findmnt", "-n", "-o", "FSTYPE", str(DATA_ROOT)),
        },
    }
    _cache = (now, payload)
    return payload


def package_project() -> dict[str, object]:
    """Create a clean source-and-trading evidence archive with a full PostgreSQL dump."""
    if not _package_lock.acquire(blocking=False):
        raise RuntimeError("другой архив проекта уже формируется")
    database_dump_path: Path | None = None
    try:
        now = time.time()
        cutoff_seconds, cutoff_ms = now - 72 * 3600, int((now - 72 * 3600) * 1000)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        final_path = REPORT_ROOT / f"cripta_project_trading_3d_{stamp}.zip"
        partial_path = final_path.with_suffix(".zip.partial")
        manifest: dict[str, object] = {
            "создано": time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(now)),
            "период_торговых_данных": "последние 72 часа",
            "назначение": "исходный код проекта и сведения для разбора реальной торговли",
            "postgresql": {
                "охват": "полная логическая копия базы cripta на момент упаковки",
                "формат": "pg_dump custom",
                "восстановление": "см. postgresql/ВОССТАНОВЛЕНИЕ.txt",
            },
            "файлы": [],
            "таблицы": [],
            "исключено": [
                "пароли, ключи API, токены и авторизация",
                "виртуальные окружения и кэши",
                "сырые рыночные архивы и старые ZIP-пакеты",
            ],
        }
        seen: set[str] = set()
        source_suffixes = {
            ".py",
            ".html",
            ".js",
            ".css",
            ".sql",
            ".service",
            ".timer",
            ".sh",
            ".ps1",
            ".toml",
            ".yaml",
            ".yml",
            ".md",
            ".txt",
            ".docx",
            ".pdf",
            ".json",
            ".csv",
            ".spec",
            ".lock",
        }
        report_suffixes = {".csv", ".json", ".jsonl", ".html", ".md", ".txt", ".log"}
        excluded_parts = {
            ".venv",
            "venv",
            "__pycache__",
            ".git",
            "node_modules",
            "datasets",
            "cache",
            "staging",
            "backup",
            "backups",
        }

        with zipfile.ZipFile(
            partial_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:

            def add_file(
                path: Path, name: str, *, max_size: int = 25 * 1024 * 1024
            ) -> None:
                if name in seen or not path.is_file() or path.stat().st_size > max_size:
                    return
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                archive.write(path, name)
                seen.add(name)
                manifest["файлы"].append(
                    {"путь": name, "размер": path.stat().st_size, "sha256": digest.hexdigest()}
                )

            for path in APP_ROOT.rglob("*"):
                try:
                    relative = path.relative_to(APP_ROOT)
                    if excluded_parts.intersection(relative.parts) or "reports" in relative.parts:
                        continue
                    if path.is_file() and path.suffix.lower() in source_suffixes:
                        add_file(path, f"исходники/{relative.as_posix()}")
                except (OSError, PermissionError):
                    continue
            for document_name in (
                "PROJECT_ARCHITECTURE_RU.md",
                "MAYAK_ARCHITECTURE_PRINCIPLES_RU.md",
                "STRATEGY_DISPATCHER_ARCHITECTURE_RU.md",
            ):
                document = APP_ROOT / "docs" / document_name
                if not document.is_file():
                    raise RuntimeError(
                        f"обязательный архитектурный документ отсутствует: {document_name}"
                    )
                add_file(document, f"исходники/docs/{document_name}")
            for config in Path("/etc/systemd/system").glob("cripta-*.*"):
                try:
                    add_file(config, f"конфигурация/systemd/{config.name}")
                except (OSError, PermissionError):
                    pass
            for config in Path("/etc/nginx/sites-enabled").glob("*cripta*"):
                try:
                    add_file(config, f"конфигурация/nginx/{config.name}")
                except (OSError, PermissionError):
                    pass

            database_dump_path = REPORT_ROOT / f".cripta_postgresql_{stamp}.dump.partial"
            pg_dump = shutil.which("pg_dump") or "/usr/bin/pg_dump"
            dump_result = subprocess.run(
                [
                    pg_dump,
                    "--dbname=dbname=cripta user=cripta host=/var/run/postgresql",
                    "--format=custom",
                    "--compress=6",
                    "--no-owner",
                    "--no-privileges",
                    f"--file={database_dump_path}",
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            if dump_result.returncode != 0 or not database_dump_path.is_file():
                detail = dump_result.stderr.strip() or "pg_dump не создал файл"
                raise RuntimeError(f"не удалось выгрузить PostgreSQL: {detail}")
            add_file(
                database_dump_path,
                "postgresql/cripta_full.dump",
                max_size=512 * 1024 * 1024,
            )
            archive.writestr(
                "postgresql/ВОССТАНОВЛЕНИЕ.txt",
                "Полная логическая копия PostgreSQL базы cripta.\n\n"
                "Содержит все схемы, таблицы, данные, последовательности, связи и индексы "
                "на согласованный момент упаковки. Владельцы объектов и права доступа "
                "намеренно не переносятся.\n\n"
                "Пример восстановления в заранее созданную пустую базу:\n"
                "pg_restore --no-owner --no-privileges --dbname=ИМЯ_БАЗЫ cripta_full.dump\n\n"
                "Для просмотра состава без восстановления:\n"
                "pg_restore --list cripta_full.dump\n",
            )

            report_roots = (
                APP_ROOT / "reports",
                DATA_ROOT / "reports",
                Path("/var/lib/cripta"),
                Path("/srv/cripta-share/logs"),
            )
            for root in report_roots:
                if not root.exists():
                    continue
                for path in root.rglob("*"):
                    try:
                        if (
                            path.is_file()
                            and path.suffix.lower() in report_suffixes
                            and path.stat().st_mtime >= cutoff_seconds
                        ):
                            label = (
                                "состояние_и_журналы"
                                if str(root).startswith("/var/lib") or str(root).endswith("logs")
                                else "торговые_отчёты"
                            )
                            add_file(
                                path, f"{label}/{root.name}/{path.relative_to(root).as_posix()}"
                            )
                    except (OSError, PermissionError):
                        continue

            tables = (
                "runtime.executions",
                "runtime.trade_commands",
                "runtime.private_events",
                "runtime.connection_events",
                "runtime.reconciliation_runs",
                "monitoring.opportunities",
                "monitoring.opportunity_events",
                "runtime.hot_orders",
                "runtime.hot_positions",
                "runtime.wallet_latest",
                "runtime.trade_settings",
                "runtime.trade_settings_history",
                "runtime.entry_decisions",
                "mayak_v2.snapshots",
                "mayak_v2.coin_minutes",
                "mayak_v2.events",
                "mayak_v2.state_events",
                "mayak_v2.observation_journal",
                "mayak_v2.liquidations",
                "supervisor.snapshots",
                "supervisor.transitions",
                "strategy_dispatcher.runs",
                "strategy_dispatcher.assessments",
                "research_context.event_links",
            )
            time_names = (
                "exec_time_ms",
                "requested_at_epoch_ms",
                "event_time_ms",
                "occurred_at_epoch_ms",
                "received_at_epoch_ms",
                "creation_time_ms",
                "at_epoch_ms",
                "signal_at_epoch_ms",
                "created_at_epoch_ms",
                "started_at_epoch_ms",
                "refreshed_at_epoch_ms",
                "decided_at_epoch_ms",
                "observed_at_epoch_ms",
                "observed_at",
                "occurred_at",
            )
            with psycopg.connect(
                "dbname=cripta user=cripta host=/var/run/postgresql"
            ) as connection:
                for table in tables:
                    schema, table_name = table.split(".")
                    column_rows = connection.execute(
                        "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                        (schema, table_name),
                    ).fetchall()
                    columns = [row[0] for row in column_rows]
                    column_types = {row[0]: row[1] for row in column_rows}
                    if not columns:
                        continue
                    time_column = next((name for name in time_names if name in columns), None)
                    query, parameters = f'SELECT * FROM "{schema}"."{table_name}"', ()
                    if time_column:
                        if column_types[time_column].startswith("timestamp"):
                            query += f' WHERE "{time_column}" >= to_timestamp(%s) ORDER BY "{time_column}"'
                            parameters = (cutoff_seconds,)
                        else:
                            query += f' WHERE "{time_column}" >= %s ORDER BY "{time_column}"'
                            parameters = (cutoff_ms,)
                    cursor = connection.execute(query, parameters)
                    rows = cursor.fetchall()
                    output = "".join(
                        json.dumps(dict(zip(columns, row)), ensure_ascii=False, default=str) + "\n"
                        for row in rows
                    )
                    export_path = f"торговая_база/{table}.jsonl"
                    archive.writestr(export_path, output)
                    timestamps = [
                        row[columns.index(time_column)]
                        for row in rows
                        if time_column and row[columns.index(time_column)] is not None
                    ]
                    manifest["таблицы"].append(
                        {
                            "schema": schema,
                            "таблица": table,
                            "table": table_name,
                            "строк": len(rows),
                            "row_count": len(rows),
                            "min_timestamp": str(min(timestamps)) if timestamps else None,
                            "max_timestamp": str(max(timestamps)) if timestamps else None,
                            "exported_jsonl": export_path,
                            "ограничение": "72 часа" if time_column else "текущий снимок",
                        }
                    )
            commit_result = subprocess.run(
                ["git", "-C", str(APP_ROOT), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            branch_result = subprocess.run(
                ["git", "-C", str(APP_ROOT), "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            state_result = subprocess.run(
                ["git", "-C", str(APP_ROOT), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            static_head = APP_ROOT / "PROJECT_GIT_HEAD.txt"
            static_state = APP_ROOT / "PROJECT_TREE_STATE.json"
            commit_sha = (
                commit_result.stdout.strip()
                if commit_result.returncode == 0
                else static_head.read_text(encoding="utf-8").strip()
                if static_head.is_file()
                else "UNKNOWN"
            )
            if state_result.returncode == 0:
                branch = branch_result.stdout.strip() or "DETACHED"
                dirty = bool(state_result.stdout.strip())
            elif static_state.is_file():
                saved_state = json.loads(static_state.read_text(encoding="utf-8"))
                branch = str(saved_state.get("branch") or "UNKNOWN")
                dirty = bool(saved_state.get("dirty", True))
            else:
                branch, dirty = "UNKNOWN", True
            source_rows = sorted(
                (
                    str(item["путь"]),
                    str(item["sha256"]),
                )
                for item in manifest["файлы"]
                if str(item["путь"]).startswith("исходники/")
            )
            source_fingerprint = hashlib.sha256(
                json.dumps(source_rows, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
            tree_state = {
                "commit": commit_sha,
                "branch": branch,
                "dirty": dirty,
                "archive_created_at": manifest["создано"],
                "source_fingerprint": source_fingerprint,
            }
            archive.writestr("PROJECT_GIT_HEAD.txt", commit_sha + "\n")
            archive.writestr(
                "PROJECT_TREE_STATE.json",
                json.dumps(tree_state, ensure_ascii=False, indent=2),
            )
            database_manifest = {
                "database_dump_created_at": manifest["создано"],
                "project_commit_fingerprint": commit_sha,
                "project_tree_state": tree_state,
                "schema_version": "runtime-audit-v2/mayak-causal-v2/supervisor-shadow-v1",
                "tables": manifest["таблицы"],
            }
            archive.writestr(
                "postgresql/DATABASE_MANIFEST.json",
                json.dumps(database_manifest, ensure_ascii=False, indent=2),
            )
            archive.writestr("МАНИФЕСТ.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        partial_path.replace(final_path)
        return {
            "status": "готово",
            "file": final_path.name,
            "path": str(final_path),
            "size": final_path.stat().st_size,
            "files": len(manifest["файлы"]),
            "tables": manifest["таблицы"],
        }
    finally:
        if database_dump_path is not None:
            database_dump_path.unlink(missing_ok=True)
        _package_lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "CriptaDashboard/0.1"

    def send_body(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def session_user(self) -> str | None:
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie[SESSION_COOKIE].value
            encoded, signature = token.rsplit(".", 1)
            expected = hmac.new(
                SESSION_SECRET_FILE.read_bytes(), encoded.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return None
            padding = "=" * (-len(encoded) % 4)
            user, expires, _nonce = (
                base64.urlsafe_b64decode(encoded + padding).decode().split("|", 2)
            )
            return user if int(expires) >= int(time.time()) else None
        except (KeyError, ValueError, OSError):
            return None

    def require_login(self) -> bool:
        if self.session_user():
            return False
        next_path = quote(urlparse(self.path).path, safe="/")
        self.send_response(303)
        self.send_header("Location", f"/login?next={next_path}")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def login_page(self, error: str = "") -> bytes:
        next_path = parse_qs(urlparse(self.path).query).get("next", ["/"])[0]
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/"
        error_html = f'<p class="error">{escape(error)}</p>' if error else ""
        return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Вход · Cripta</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1219;color:#e8f0f6;font:16px system-ui}}form{{width:min(380px,calc(100vw - 48px));padding:30px;background:#14212c;border:1px solid #2b4355;border-radius:16px;box-shadow:0 18px 60px #0008}}h1{{margin:0 0 8px}}p{{color:#9fb2c2}}label{{display:block;margin:18px 0 7px}}input[type=text],input[type=password]{{box-sizing:border-box;width:100%;padding:12px;border:1px solid #496175;border-radius:8px;background:#0e1922;color:white;font-size:16px}}.remember{{display:flex;gap:9px;align-items:center;margin:18px 0}}button{{width:100%;padding:12px;border:0;border-radius:8px;background:#45c58a;color:#07130d;font-weight:750;font-size:16px;cursor:pointer}}.error{{color:#ff8a8a}}</style></head><body><form method="post" action="/login"><h1>Cripta</h1><p>Вход в рабочий портал</p>{error_html}<input type="hidden" name="next" value="{escape(next_path)}"><label for="username">Имя пользователя</label><input id="username" name="username" value="alex" autocomplete="username" required autofocus><label for="password">Пароль портала</label><input id="password" type="password" name="password" autocomplete="current-password" required><label class="remember"><input type="checkbox" name="remember" value="1" checked> Запомнить меня на 30 дней</label><button type="submit">Войти</button></form></body></html>'''.encode(
            "utf-8"
        )

    def verify_credentials(self, username: str, password: str) -> bool:
        try:
            for line in AUTH_FILE.read_text().splitlines():
                stored_user, stored_hash = line.split(":", 1)
                if hmac.compare_digest(username, stored_user):
                    return hmac.compare_digest(crypt.crypt(password, stored_hash), stored_hash)
        except (OSError, ValueError):
            pass
        return False

    def issue_session(self, username: str, remember: bool) -> None:
        ttl = 30 * 86400 if remember else 12 * 3600
        raw = f"{username}|{int(time.time()) + ttl}|{secrets.token_hex(12)}".encode()
        encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        signature = hmac.new(
            SESSION_SECRET_FILE.read_bytes(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        cookie = (
            f"{SESSION_COOKIE}={encoded}.{signature}; Path=/; HttpOnly; Secure; SameSite=Strict"
        )
        if remember:
            cookie += f"; Max-Age={ttl}"
        self.send_header("Set-Cookie", cookie)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/healthz":
            self.send_body(200, b'{"status":"ok"}\n', "application/json; charset=utf-8")
        elif path == "/login":
            self.send_body(200, self.login_page(), "text/html; charset=utf-8")
        elif path == "/logout":
            self.send_response(303)
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict",
            )
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.require_login():
            return
        elif path == "/api/status":
            body = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_body(200, body, "application/json; charset=utf-8")
        elif path == "/api/live/state":
            body = json.dumps(
                {
                    "live_trading": live_trading_state(),
                    "opportunities": opportunity_state(),
                    "entry_shadow": entry_shadow_state(),
                    "mayak_v2": mayak_v2_state(),
                    "generated_at_epoch": int(time.time()),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_body(200, body, "application/json; charset=utf-8")
        elif path.startswith("/api/trading/export-jobs/"):
            try:
                job_id = path.rsplit("/", 1)[-1]
                body = json.dumps(
                    read_signal_export_job(job_id), ensure_ascii=False
                ).encode("utf-8")
                self.send_body(200, body, "application/json; charset=utf-8")
            except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
                self.send_body(
                    404,
                    json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
        elif path.startswith("/api/project/archive-jobs/"):
            try:
                job_id = path.rsplit("/", 1)[-1]
                body = json.dumps(read_archive_job(job_id), ensure_ascii=False).encode("utf-8")
                self.send_body(200, body, "application/json; charset=utf-8")
            except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
                self.send_body(
                    404,
                    json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
        elif path in {
            "/",
            "/infra",
            "/current",
            "/entry",
            "/live",
            "/strategies",
            "/test-library",
            "/bots",
            "/server-control",
            "/history",
            "/rules",
            "/checklist",
        }:
            body = (Path(__file__).parent / "index.html").read_bytes()
            self.send_body(200, body, "text/html; charset=utf-8")
        elif path.startswith("/reports/"):
            self.send_report_path(path.removeprefix("/reports/"))
        else:
            self.send_body(404, b"not found\n", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        global _cache
        path = urlparse(self.path).path
        if path == "/login":
            length = min(int(self.headers.get("Content-Length", "0")), 8192)
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            username = form.get("username", [""])[0]
            password = form.get("password", [""])[0]
            next_path = form.get("next", ["/"])[0]
            if not next_path.startswith("/") or next_path.startswith("//"):
                next_path = "/"
            if not self.verify_credentials(username, password):
                self.path = f"/login?next={quote(next_path, safe='/')}"
                self.send_body(
                    401,
                    self.login_page("Неверное имя пользователя или пароль"),
                    "text/html; charset=utf-8",
                )
                return
            self.send_response(303)
            self.issue_session(username, form.get("remember", [""])[0] == "1")
            self.send_header("Location", next_path)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.require_login():
            return
        if path in {"/api/project/package", "/api/project/archive-jobs"}:
            try:
                if path == "/api/project/package":
                    result = start_archive_job("ANALYSIS_FULL", "3d")
                else:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 0 or length > 4096:
                        raise ValueError("недопустимый размер запроса")
                    request = json.loads(self.rfile.read(length) or b"{}")
                    result = start_archive_job(
                        str(request.get("profile", "ANALYSIS_FULL")),
                        str(request.get("period", "3d")),
                    )
                self.send_body(
                    202,
                    json.dumps(result, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
            except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
                self.send_body(
                    400,
                    json.dumps({"error": str(exc)}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
            return
        if path == "/api/trading/export":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 2048:
                    raise ValueError("недопустимый размер запроса")
                request = json.loads(self.rfile.read(length))
                table = str(request.get("table", ""))
                period = str(request.get("period", ""))
                if table == "signals":
                    result = start_signal_export_job(period)
                    status_code = 202
                else:
                    result = export_trading_table(table, period)
                    status_code = 201
                self.send_body(
                    status_code,
                    json.dumps(result, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
            except (
                ValueError,
                json.JSONDecodeError,
                OSError,
                psycopg.Error,
                sqlite3.Error,
                RuntimeError,
                zipfile.BadZipFile,
            ) as exc:
                self.send_body(
                    400,
                    json.dumps({"error": str(exc)}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
            return
        if path in {"/api/live/settings", "/api/live/command", "/api/live/gate"}:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 8192:
                    raise ValueError("invalid body size")
                request = json.loads(self.rfile.read(length))
                with psycopg.connect(
                    "dbname=cripta user=cripta host=/var/run/postgresql"
                ) as connection:
                    if path == "/api/live/settings":
                        old_settings = connection.execute(
                            "SELECT to_jsonb(t) FROM runtime.trade_settings t WHERE singleton=1"
                        ).fetchone()
                        stake, leverage = (
                            float(request.get("stake_usdt", 0)),
                            int(request.get("leverage", 0)),
                        )
                        entry_offset = float(request.get("entry_offset_pct", 0))
                        entry_ttl = int(request.get("entry_limit_ttl_seconds", 30))
                        auto_profit_protection = bool(request.get("auto_profit_protection", True))
                        auto_trailing_stop = bool(request.get("auto_trailing_stop", True))
                        trailing_distance_pct = float(request.get("trailing_distance_pct", 0.3))
                        entry_policy = str(request.get("entry_policy", "base_entry_v1"))
                        symbols = sorted(
                            {str(x).upper() for x in request.get("enabled_symbols", [])}
                            - BYBIT_KZ_UNSUPPORTED
                        )
                        if stake <= 0 or leverage not in {1, 2, 3, 5, 10}:
                            raise ValueError("недопустимая ставка или плечо")
                        if entry_offset not in {0.0, 0.1, 0.2} or entry_ttl not in {
                            10,
                            20,
                            30,
                            60,
                            90,
                            120,
                            240,
                            300,
                        }:
                            raise ValueError("недопустимая глубина входа или срок лимитной заявки")
                        if trailing_distance_pct not in {0.1, 0.2, 0.3, 0.5, 1.0}:
                            raise ValueError("недопустимый отступ плавающего стопа")
                        if entry_policy not in {
                            "base_entry_v1", "m3_full_live_v1"
                        }:
                            raise ValueError("неизвестное правило автоматического входа")
                        changed_at_ms = int(time.time() * 1000)
                        connection.execute(
                            "UPDATE runtime.trade_settings SET stake_usdt=%s,leverage=%s,enabled_symbols_json=%s,entry_offset_pct=%s,entry_limit_ttl_seconds=%s,auto_profit_protection=%s,auto_trailing_stop=%s,trailing_distance_pct=%s,entry_policy=%s,updated_at_epoch_ms=%s WHERE singleton=1",
                            (
                                str(stake),
                                leverage,
                                json.dumps(symbols),
                                str(entry_offset),
                                entry_ttl,
                                auto_profit_protection,
                                auto_trailing_stop,
                                str(trailing_distance_pct),
                                entry_policy,
                                changed_at_ms,
                            ),
                        )
                        new_settings = connection.execute(
                            "SELECT to_jsonb(t) FROM runtime.trade_settings t WHERE singleton=1"
                        ).fetchone()
                        connection.execute(
                            """INSERT INTO runtime.trade_settings_history(
                            changed_at_epoch_ms,old_settings,new_settings,source,origin,settings_version)
                            VALUES(%s,%s,%s,'dashboard','user',%s)""",
                            (
                                changed_at_ms,
                                json.dumps(old_settings[0] if old_settings else {}),
                                json.dumps(new_settings[0] if new_settings else {}),
                                str(changed_at_ms),
                            ),
                        )
                    elif path == "/api/live/gate":
                        enabled = bool(request.get("enabled"))
                        if enabled and request.get("confirmed") is not True:
                            raise ValueError("включение новых входов не подтверждено")
                        if enabled:
                            current_settings = connection.execute(
                                """SELECT updated_at_epoch_ms
                                   FROM runtime.trade_settings WHERE singleton=1"""
                            ).fetchone()
                            requested_version = str(request.get("settings_version") or "")
                            current_version = (
                                "" if not current_settings else str(current_settings[0])
                            )
                            if not requested_version or requested_version != current_version:
                                raise ValueError(
                                    "settings_version mismatch: reload server state before re-arm"
                                )
                            readiness = live_rearm_readiness(connection)
                            if not bool(readiness.get("rearm_ready")):
                                raise ValueError(
                                    "re-arm blocked: " + "; ".join(readiness.get("reasons", []))
                                )
                        previous_gate = connection.execute(
                            "SELECT enabled FROM control.execution_gates "
                            "WHERE mode='mainnet' FOR UPDATE"
                        ).fetchone()
                        previous_enabled = bool(previous_gate[0]) if previous_gate else False
                        gate_changed_at_ms = int(time.time() * 1000)
                        gate_reason = (
                            "явно включено владельцем через портал"
                            if enabled
                            else "выключено владельцем через портал"
                        )
                        connection.execute(
                            "UPDATE control.execution_gates SET enabled=%s,reason=%s,"
                            "updated_at_epoch_ms=%s WHERE mode='mainnet'",
                            (1 if enabled else 0, gate_reason, gate_changed_at_ms),
                        )
                        connection.execute(
                            """INSERT INTO control.execution_gate_events(
                               at_epoch_ms,mode,previous_enabled,requested_enabled,
                               resulting_enabled,reason,source,origin,request_id,
                               settings_version)
                               VALUES(%s,'mainnet',%s,%s,%s,%s,'dashboard','owner',%s,%s)""",
                            (
                                gate_changed_at_ms,
                                previous_enabled,
                                enabled,
                                enabled,
                                gate_reason,
                                str(request.get("request_id") or secrets.token_hex(8)),
                                str(request.get("settings_version") or ""),
                            ),
                        )
                    else:
                        kind, symbol = (
                            str(request.get("type", "")),
                            str(request.get("symbol", "")).upper(),
                        )
                        if kind not in {
                            "break_even",
                            "current_stop",
                            "trailing_stop",
                            "close",
                        } or not symbol.endswith("USDT"):
                            raise ValueError("недопустимая команда")
                        command_id = f"web-{kind[:4]}-{symbol[:12]}-{int(time.time() * 1000)}"
                        command_payload: dict[str, object] = {}
                        if kind == "trailing_stop":
                            enabled = bool(request.get("enabled"))
                            distance_pct = float(request.get("distance_pct", 0.2))
                            if distance_pct < 0.05 or distance_pct > 5:
                                raise ValueError(
                                    "отступ плавающего стопа должен быть от 0,05% до 5%"
                                )
                            command_payload = {"enabled": enabled, "distance_pct": distance_pct}
                        connection.execute(
                            "INSERT INTO runtime.trade_commands(command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms) VALUES(%s,%s,%s,%s,'queued',%s)",
                            (
                                command_id,
                                kind,
                                symbol,
                                json.dumps(command_payload),
                                int(time.time() * 1000),
                            ),
                        )
                    connection.commit()
                _cache = None
                response: dict[str, object] = {"status": "accepted"}
                if path == "/api/live/command":
                    response["command_id"] = command_id
                if path == "/api/live/gate":
                    response["gate_enabled"] = enabled
                self.send_body(
                    202, json.dumps(response).encode(), "application/json; charset=utf-8"
                )
            except (ValueError, json.JSONDecodeError, psycopg.Error) as exc:
                self.send_body(
                    400,
                    json.dumps({"error": str(exc)}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
            return
        if path != "/api/bots/action":
            self.send_body(404, b"not found\n", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError("invalid body size")
            request = json.loads(self.rfile.read(length))
            bot_id = str(request.get("bot_id", ""))
            action = str(request.get("action", ""))
            if action not in {"start", "stop"}:
                raise ValueError("unknown action")
            with psycopg.connect(
                "dbname=cripta user=cripta host=/var/run/postgresql"
            ) as connection:
                bot = connection.execute(
                    "SELECT executable,mode,mainnet_approved FROM control.bots WHERE id=%s",
                    (bot_id,),
                ).fetchone()
                if not bot:
                    raise ValueError("unknown bot")
                executable, mode, mainnet_approved = bot
                if action == "start" and not executable:
                    self.send_body(
                        409,
                        json.dumps(
                            {"error": "Сначала назначьте проверенный исполняемый модуль"},
                            ensure_ascii=False,
                        ).encode(),
                        "application/json; charset=utf-8",
                    )
                    return
                if action == "start" and mode == "mainnet" and not mainnet_approved:
                    self.send_body(
                        409,
                        json.dumps(
                            {"error": "Mainnet-допуск заблокирован"}, ensure_ascii=False
                        ).encode(),
                        "application/json; charset=utf-8",
                    )
                    return
                now_ms = int(time.time() * 1000)
                connection.execute(
                    "UPDATE control.bots SET desired_state=%s,updated_at_epoch=%s WHERE id=%s",
                    ("running" if action == "start" else "stopped", now_ms // 1000, bot_id),
                )
                connection.execute(
                    "INSERT INTO control.bot_events(at_epoch_ms,bot_id,action,result,details_json) VALUES(%s,%s,%s,'requested','{}')",
                    (now_ms, bot_id, action),
                )
                connection.commit()
            _cache = None
            self.send_body(
                202, json.dumps({"status": "accepted"}).encode(), "application/json; charset=utf-8"
            )
        except (ValueError, json.JSONDecodeError, OSError, psycopg.Error) as exc:
            self.send_body(
                400,
                json.dumps({"error": str(exc)}, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )

    def send_report_path(self, relative: str) -> None:
        root = REPORT_ROOT.resolve()
        target = (root / unquote(relative)).resolve()
        if not target.is_relative_to(root) or not target.exists():
            self.send_body(404, b"not found\n", "text/plain; charset=utf-8")
            return
        if target.is_dir():
            rows = []
            for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                rel = item.relative_to(root).as_posix()
                suffix = "/" if item.is_dir() else ""
                rows.append(
                    f'<li><a href="/reports/{quote(rel)}">{escape(item.name)}{suffix}</a></li>'
                )
            parent = target.parent if target != root else None
            back = ""
            if parent and parent.is_relative_to(root):
                back_rel = parent.relative_to(root).as_posix()
                back = f'<p><a href="/reports/{quote(back_rel)}">← Назад</a></p>'
            body = (
                "<!doctype html><meta charset=utf-8><title>Cripta reports</title>"
                "<style>body{background:#091017;color:#e7eef5;font:15px system-ui;padding:30px}a{color:#55b5ff}li{margin:9px}</style>"
                f"<h1>{escape(target.name)}</h1>{back}<ul>{''.join(rows)}</ul>"
            ).encode("utf-8")
            self.send_body(200, body, "text/html; charset=utf-8")
            return
        body = target.read_bytes()
        self.send_body(200, body, guess_type(target.name)[0] or "application/octet-stream")

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.client_address[0]} {fmt % args}", flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    server.serve_forever()
