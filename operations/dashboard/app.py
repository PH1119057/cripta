from __future__ import annotations

import base64
import csv
import ctypes
import ctypes.util
import hashlib
import hmac
import io
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request
import zipfile

import psycopg

from bybit_workbench.fault_delivery import acknowledge_delivery
from bybit_workbench.live_arm_readiness import (
    LiveArmContext,
    evaluate_live_arm,
    strategy_symbol_scope_key,
)
from bybit_workbench.r1_micro_live_control import (
    arm as arm_r1_micro_live,
    disarm as disarm_r1_micro_live,
    micro_live_state as r1_micro_live_state,
)
from bybit_workbench.universal_entry.dashboard_control import (
    StaleActivationState,
    StrategyDashboardStore,
    StrategyRuntimeNotReady,
    UnknownActivation,
    strategy_authoring_template,
    strategy_context_feature_catalog,
)

try:
    from archive_v2 import read_job as read_archive_job
    from archive_v2 import start_job as start_archive_job
except ImportError:  # package import used by tests
    from operations.dashboard.archive_v2 import read_job as read_archive_job
    from operations.dashboard.archive_v2 import start_job as start_archive_job
from datetime import UTC, datetime, timedelta
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

DATA_ROOT = Path(os.environ.get("CRIPTA_DATA_ROOT", "/data/cripta"))
APP_ROOT = Path(os.environ.get("CRIPTA_APP_ROOT", "/srv/cripta"))
LOADED_RELEASE_COMMIT = os.environ.get("CRIPTA_RELEASE_COMMIT", "").strip().lower()
PERIOD = "20260518_20260816"
STATE = DATA_ROOT / "datasets" / "raw" / PERIOD / "download_state.json"
EXPANSION_STATE = DATA_ROOT / "datasets" / "raw" / PERIOD / "download_state_expansion_20260823.json"
REPORT_ROOT = Path(os.environ.get("CRIPTA_REPORT_ROOT", "/srv/cripta-share/reports"))
CONNECTIVITY_STATE = Path("/var/lib/cripta/connectivity/status.json")
PRIVATE_API_STATE = Path("/var/lib/cripta/connectivity/private_api.json")
SAFETY_STATE = Path("/var/lib/cripta/safety/latest.json")
BACKUP_STATE = Path("/var/lib/cripta/backup/latest.json")
PRIVATE_RUNTIME_STATE = Path("/var/lib/cripta/private_runtime/status.json")
UNIVERSAL_ENTRY_OBSERVER_STATE = Path("/var/lib/cripta/universal_entry_observer/status.json")
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
_system_crypt_lock = threading.Lock()


def _load_system_crypt():
    library_name = ctypes.util.find_library("crypt")
    if not library_name:
        return None, None
    try:
        library = ctypes.CDLL(library_name, use_errno=True)
        function = library.crypt
    except (OSError, AttributeError):
        return None, None
    function.argtypes = (ctypes.c_char_p, ctypes.c_char_p)
    function.restype = ctypes.c_char_p
    return library, function


_system_crypt_library, _system_crypt = _load_system_crypt()


def _verify_system_password_hash(password: str, stored_hash: str) -> bool:
    if _system_crypt is None or "\x00" in password or "\x00" in stored_hash:
        return False
    try:
        password_bytes = password.encode("utf-8")
        hash_bytes = stored_hash.encode("utf-8")
    except UnicodeEncodeError:
        return False
    with _system_crypt_lock:
        candidate = _system_crypt(password_bytes, hash_bytes)
    if not candidate:
        return False
    try:
        candidate_hash = candidate.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return hmac.compare_digest(candidate_hash, stored_hash)

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




def _signal_monitor_summary(items: list[dict[str, object]]) -> dict[str, object]:
    positive = sum(1 for item in items if item.get("result_class") == "positive")
    negative = sum(1 for item in items if item.get("result_class") == "negative")
    open_count = sum(1 for item in items if item.get("result_class") == "open")
    no_entry = sum(1 for item in items if item.get("result_class") == "no_entry")
    neutral = sum(1 for item in items if item.get("result_class") == "neutral")
    closed_pnl = sum(
        float(item["pnl_usdt"])
        for item in items
        if item.get("pnl_kind") == "closed" and item.get("pnl_usdt") is not None
    )
    open_pnl = sum(
        float(item["pnl_usdt"])
        for item in items
        if item.get("pnl_kind") == "open" and item.get("pnl_usdt") is not None
    )
    pnl_rows = sum(1 for item in items if item.get("pnl_usdt") is not None)
    return {
        "total": len(items),
        "positive": positive,
        "negative": negative,
        "open": open_count,
        "no_entry": no_entry,
        "neutral": neutral,
        "closed_pnl_usdt": closed_pnl,
        "open_pnl_usdt": open_pnl,
        "total_pnl_usdt": closed_pnl + open_pnl,
        "pnl_rows": pnl_rows,
        "closed_pnl_rows": sum(
            1 for item in items if item.get("pnl_kind") == "closed" and item.get("pnl_usdt") is not None
        ),
        "open_pnl_rows": sum(
            1 for item in items if item.get("pnl_kind") == "open" and item.get("pnl_usdt") is not None
        ),
    }


def _legacy_signal_outcome(hits: dict[str, object], state: str) -> tuple[str, str]:
    tp = hits.get("+1.1")
    sl = hits.get("-1.0")
    if tp is not None and (sl is None or float(tp) < float(sl)):
        return "positive", "цель +1,10% раньше стопа"
    if sl is not None and (tp is None or float(sl) < float(tp)):
        return "negative", "стоп −1,00% раньше цели"
    if state == "tracking":
        return "open", "наблюдение продолжается"
    return "neutral", "24 часа завершены без цели и стопа"


def strategy_signal_monitor_state(*, window_hours: int = 24) -> dict[str, object]:
    now = datetime.now(UTC)
    window_start = now - timedelta(hours=window_hours)
    cutoff_epoch_ms = int(window_start.timestamp() * 1000)
    try:
        tickers = live_tickers()
    except Exception:
        tickers = {}
    items: list[dict[str, object]] = []
    active: dict[str, dict[str, object]] = {}
    with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
        if connection.execute(
            "SELECT to_regclass('strategy_entry.strategy_activations')"
        ).fetchone()[0] is not None:
            for row in connection.execute(
                """SELECT a.strategy_id,a.strategy_version,c.name
                     FROM strategy_entry.strategy_activations a
                     JOIN strategy_entry.strategy_cards c
                       ON c.strategy_id=a.strategy_id
                      AND c.strategy_version=a.strategy_version
                      AND c.strategy_config_fingerprint=a.strategy_config_fingerprint
                    WHERE a.enabled=true
                    ORDER BY c.name,a.strategy_version"""
            ).fetchall():
                key = f"{row[0]}::{row[1]}"
                active[key] = {
                    "strategy_key": key,
                    "strategy_id": row[0],
                    "strategy_version": row[1],
                    "strategy_name": row[2],
                    "active": True,
                    "legacy": False,
                }
        paper_installed = bool(
            connection.execute("SELECT to_regclass('strategy_entry.paper_orders')").fetchone()[0]
        ) and bool(
            connection.execute("SELECT to_regclass('strategy_entry.paper_positions')").fetchone()[0]
        )
        if paper_installed:
            rows = connection.execute(
                """SELECT o.paper_order_id,o.signal_id,o.strategy_id,o.strategy_version,c.name,
                          o.symbol,o.direction,o.state,o.order_type,o.requested_at,
                          o.reference_price,o.limit_price,o.expires_at,
                          p.paper_position_id,p.state,p.opened_at,p.entry_price,p.quantity,
                          p.closed_at,p.exit_price,p.exit_reason,p.gross_pnl_usdt,
                          p.gross_return_pct,p.mfe_pct,p.mae_pct
                     FROM strategy_entry.paper_orders o
                     LEFT JOIN strategy_entry.strategy_cards c
                       ON c.strategy_id=o.strategy_id
                      AND c.strategy_version=o.strategy_version
                      AND c.strategy_config_fingerprint=o.strategy_config_fingerprint
                     LEFT JOIN strategy_entry.paper_positions p
                       ON p.paper_order_id=o.paper_order_id AND p.leg_type='PRIMARY'
                    WHERE o.requested_at >= %s
                    ORDER BY o.requested_at DESC,o.paper_order_id DESC""",
                (window_start,),
            ).fetchall()
            for row in rows:
                strategy_key = f"{row[2]}::{row[3]}"
                position_state = None if row[14] is None else str(row[14])
                order_state = str(row[7])
                pnl: float | None = None
                pnl_kind: str | None = None
                current_price: float | None = None
                result_class = "neutral"
                result_text = order_state
                if position_state == "OPEN":
                    result_class = "open"
                    result_text = "псевдосделка открыта"
                    ticker = tickers.get(str(row[5])) or {}
                    raw_price = ticker.get("mark_price") or ticker.get("last_price")
                    if raw_price not in (None, "") and row[16] is not None and row[17] is not None:
                        current_price = float(raw_price)
                        entry_price = float(row[16])
                        quantity = float(row[17])
                        pnl = (
                            (current_price - entry_price) * quantity
                            if str(row[6]) == "LONG"
                            else (entry_price - current_price) * quantity
                        )
                        pnl_kind = "open"
                elif position_state == "CLOSED":
                    pnl = None if row[21] is None else float(row[21])
                    pnl_kind = "closed" if pnl is not None else None
                    result_class = (
                        "positive" if pnl is not None and pnl > 0
                        else "negative" if pnl is not None and pnl < 0
                        else "neutral"
                    )
                    result_text = f"закрыта · {row[20] or 'без причины'}"
                elif order_state == "PENDING":
                    result_class = "open"
                    result_text = "ждёт входа"
                elif order_state in {"EXPIRED", "CANCELLED"}:
                    result_class = "no_entry"
                    result_text = "вход не состоялся"
                items.append(
                    {
                        "source": "paper",
                        "strategy_key": strategy_key,
                        "strategy_id": row[2],
                        "strategy_version": row[3],
                        "strategy_name": row[4] or row[2],
                        "active_strategy": strategy_key in active,
                        "signal_id": row[1],
                        "paper_order_id": row[0],
                        "paper_position_id": row[13],
                        "signal_at": row[9].isoformat(),
                        "signal_at_epoch_ms": int(row[9].timestamp() * 1000),
                        "symbol": row[5],
                        "direction": row[6],
                        "order_type": row[8],
                        "order_state": order_state,
                        "position_state": position_state,
                        "signal_price": None if row[10] is None else float(row[10]),
                        "limit_price": None if row[11] is None else float(row[11]),
                        "entry_price": None if row[16] is None else float(row[16]),
                        "current_price": current_price,
                        "exit_price": None if row[19] is None else float(row[19]),
                        "result_class": result_class,
                        "result_text": result_text,
                        "pnl_usdt": pnl,
                        "pnl_kind": pnl_kind,
                        "return_pct": None if row[22] is None else float(row[22]),
                        "mfe_pct": None if row[23] is None else float(row[23]),
                        "mae_pct": None if row[24] is None else float(row[24]),
                    }
                )
        legacy_rows = connection.execute(
            """SELECT signal_id,bot_id,strategy_version,symbol,direction,signal_price,state,
                      max_favorable_pct,max_adverse_pct,first_hits_json,samples,signal_at_epoch_ms
                 FROM monitoring.opportunities
                WHERE signal_at_epoch_ms >= %s
                ORDER BY signal_at_epoch_ms DESC""",
            (cutoff_epoch_ms,),
        ).fetchall()
    for row in legacy_rows:
        hits = json.loads(row[9] or "{}")
        result_class, result_text = _legacy_signal_outcome(hits, str(row[6]))
        key = f"legacy-entry-v1::{row[2]}"
        items.append(
            {
                "source": "legacy",
                "strategy_key": key,
                "strategy_id": "legacy-entry-v1",
                "strategy_version": row[2],
                "strategy_name": "Entry V1 · legacy",
                "active_strategy": False,
                "signal_id": row[0],
                "signal_at": datetime.fromtimestamp(row[11] / 1000, tz=UTC).isoformat(),
                "signal_at_epoch_ms": row[11],
                "symbol": row[3],
                "direction": str(row[4]).upper(),
                "order_type": None,
                "order_state": None,
                "position_state": "OPEN" if row[6] == "tracking" else "CLOSED",
                "signal_price": row[5],
                "limit_price": None,
                "entry_price": row[5],
                "current_price": None,
                "exit_price": None,
                "result_class": result_class,
                "result_text": result_text,
                "pnl_usdt": None,
                "pnl_kind": None,
                "return_pct": None,
                "mfe_pct": row[7],
                "mae_pct": row[8],
                "samples": row[10],
            }
        )
    items.sort(key=lambda item: int(item["signal_at_epoch_ms"]), reverse=True)
    strategies: dict[str, dict[str, object]] = dict(active)
    for item in items:
        key = str(item["strategy_key"])
        if key not in strategies:
            strategies[key] = {
                "strategy_key": key,
                "strategy_id": item["strategy_id"],
                "strategy_version": item["strategy_version"],
                "strategy_name": item["strategy_name"],
                "active": bool(item.get("active_strategy")),
                "legacy": item.get("source") == "legacy",
            }
    strategy_summaries: list[dict[str, object]] = []
    for strategy in strategies.values():
        related = [item for item in items if item["strategy_key"] == strategy["strategy_key"]]
        strategy_summaries.append({**strategy, **_signal_monitor_summary(related)})
    strategy_summaries.sort(
        key=lambda item: (not bool(item["active"]), str(item["strategy_name"]), str(item["strategy_version"]))
    )
    return {
        "window_hours": window_hours,
        "window_start": window_start.isoformat(),
        "window_end": now.isoformat(),
        "items": items,
        "summary": _signal_monitor_summary(items),
        "strategies": strategy_summaries,
    }


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


def strategy_monitor_state() -> dict[str, object]:
    state: dict[str, object]
    if not UNIVERSAL_ENTRY_OBSERVER_STATE.exists():
        state = {
            "state": "OFF",
            "observer_ready": False,
            "reason": "multi-Strategy observer status is missing",
            "updated_at": None,
            "strategy_monitors": [],
            "sensor_status": {},
        }
    else:
        try:
            loaded = json.loads(UNIVERSAL_ENTRY_OBSERVER_STATE.read_text(encoding="utf-8"))
            state = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            state = {
                "state": "ERROR",
                "observer_ready": False,
                "reason": "cannot read multi-Strategy observer status",
                "updated_at": None,
                "strategy_monitors": [],
                "sensor_status": {},
            }
    items = [
        dict(item)
        for item in state.get("strategy_monitors", [])
        if isinstance(item, dict) and str(item.get("symbol") or "") not in BYBIT_KZ_UNSUPPORTED
    ]
    risks = execution_liquidity_risks()
    for item in items:
        item["liquidity_risk"] = risks.get(str(item.get("symbol") or ""))
    strategies: dict[str, dict[str, object]] = {}
    try:
        with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
            rows = connection.execute(
                """SELECT a.strategy_id,a.strategy_version,c.name,
                          c.card_json->'direction_policy',c.card_json->'symbols'
                     FROM strategy_entry.strategy_activations a
                     JOIN strategy_entry.strategy_cards c
                       ON c.strategy_id=a.strategy_id
                      AND c.strategy_version=a.strategy_version
                      AND c.strategy_config_fingerprint=a.strategy_config_fingerprint
                    WHERE a.enabled=true
                    ORDER BY c.name,a.strategy_version"""
            ).fetchall()
        for row in rows:
            key = f"{row[0]}::{row[1]}"
            strategies[key] = {
                "strategy_key": key,
                "strategy_id": str(row[0]),
                "strategy_version": str(row[1]),
                "strategy_name": str(row[2]),
                "directions": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
                "symbols": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
            }
    except (psycopg.Error, json.JSONDecodeError, TypeError):
        strategies = {}
    return {
        "state": str(state.get("state") or "UNKNOWN"),
        "observer_ready": bool(state.get("observer_ready")),
        "reason": str(state.get("reason") or ""),
        "updated_at": state.get("updated_at"),
        "source_commit": state.get("source_commit"),
        "observer_epoch_id": state.get("observer_epoch_id"),
        "facts_received": int(state.get("facts_received") or 0),
        "evaluations": int(state.get("evaluations") or 0),
        "signals": int(state.get("signals") or 0),
        "sensor_status": state.get("sensor_status") or {},
        "strategies": list(strategies.values()),
        "items": items,
    }


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


def _live_arm_context_from_request(request: dict[str, object]) -> LiveArmContext:
    if not LOADED_RELEASE_COMMIT:
        raise ValueError("CRIPTA_RELEASE_COMMIT is required for real arm")
    return LiveArmContext(
        strategy_id=str(request.get("strategy_id") or "").strip(),
        strategy_version=str(request.get("strategy_version") or "").strip(),
        strategy_config_fingerprint=str(
            request.get("strategy_config_fingerprint") or ""
        ).strip(),
        strategy_activation_id=str(request.get("strategy_activation_id") or "").strip(),
        symbol=str(request.get("symbol") or "").strip().upper(),
        release_commit=LOADED_RELEASE_COMMIT,
    )


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
    }


def live_trading_state() -> dict[str, object]:
    return _live_trading_state(include_history=True)


def strategy_paper_state() -> dict[str, object]:
    with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
        if connection.execute(
            "SELECT to_regclass('strategy_entry.paper_positions')"
        ).fetchone()[0] is None:
            return {"installed": False, "pending": [], "open": [], "closed": [], "summary": {}}
        pending_rows = connection.execute(
            """SELECT o.paper_order_id,o.strategy_id,o.strategy_version,c.name,o.symbol,
                      o.direction,o.order_type,o.requested_at,o.reference_price,o.limit_price,
                      o.expires_at,o.execution_request_id
                 FROM strategy_entry.paper_orders o
                 LEFT JOIN strategy_entry.strategy_cards c
                   ON c.strategy_id=o.strategy_id AND c.strategy_version=o.strategy_version
                  AND c.strategy_config_fingerprint=o.strategy_config_fingerprint
                WHERE o.state='PENDING'
                ORDER BY o.requested_at DESC LIMIT 200"""
        ).fetchall()
        position_rows = connection.execute(
            """SELECT p.paper_position_id,p.parent_position_id,p.leg_type,p.strategy_id,
                      p.strategy_version,c.name,p.symbol,p.direction,p.state,p.opened_at,
                      p.entry_price,p.stake_usdt,p.leverage,p.notional_usdt,p.quantity,
                      p.best_price,p.mfe_pct,p.mae_pct,p.active_stop_price,p.trailing_active,
                      p.hedge_opened,p.closed_at,p.exit_price,p.exit_reason,p.gross_pnl_usdt,
                      p.gross_return_pct,p.signal_id,p.entry_plan_fingerprint,
                      p.exit_plan_fingerprint
                 FROM strategy_entry.paper_positions p
                 LEFT JOIN strategy_entry.strategy_cards c
                   ON c.strategy_id=p.strategy_id AND c.strategy_version=p.strategy_version
                  AND c.strategy_config_fingerprint=p.strategy_config_fingerprint
                ORDER BY p.opened_at DESC LIMIT 1000"""
        ).fetchall()
        aggregate_rows = connection.execute(
            """SELECT p.strategy_id,p.strategy_version,c.name,
                      count(*) FILTER (WHERE p.leg_type='PRIMARY') AS positions,
                      count(*) FILTER (WHERE p.state='OPEN') AS open_positions,
                      count(*) FILTER (WHERE p.state='CLOSED') AS closed_positions,
                      coalesce(sum(p.gross_pnl_usdt) FILTER (WHERE p.state='CLOSED'),0),
                      coalesce(avg(p.gross_return_pct) FILTER (WHERE p.state='CLOSED'),0),
                      coalesce(avg(p.mfe_pct) FILTER (WHERE p.leg_type='PRIMARY'),0),
                      coalesce(avg(p.mae_pct) FILTER (WHERE p.leg_type='PRIMARY'),0)
                 FROM strategy_entry.paper_positions p
                 LEFT JOIN strategy_entry.strategy_cards c
                   ON c.strategy_id=p.strategy_id AND c.strategy_version=p.strategy_version
                  AND c.strategy_config_fingerprint=p.strategy_config_fingerprint
                GROUP BY p.strategy_id,p.strategy_version,c.name
                ORDER BY p.strategy_id,p.strategy_version"""
        ).fetchall()
    pending = [
        {
            "paper_order_id": row[0], "strategy_id": row[1], "strategy_version": row[2],
            "strategy_name": row[3], "symbol": row[4], "direction": row[5],
            "order_type": row[6], "requested_at": row[7].isoformat(),
            "reference_price": str(row[8]),
            "limit_price": None if row[9] is None else str(row[9]),
            "expires_at": None if row[10] is None else row[10].isoformat(),
            "execution_request_id": row[11],
        }
        for row in pending_rows
    ]
    positions = [
        {
            "paper_position_id": row[0], "parent_position_id": row[1], "leg_type": row[2],
            "strategy_id": row[3], "strategy_version": row[4], "strategy_name": row[5],
            "symbol": row[6], "direction": row[7], "state": row[8],
            "opened_at": row[9].isoformat(), "entry_price": str(row[10]),
            "stake_usdt": str(row[11]), "leverage": row[12], "notional_usdt": str(row[13]),
            "quantity": str(row[14]), "best_price": str(row[15]), "mfe_pct": str(row[16]),
            "mae_pct": str(row[17]),
            "active_stop_price": None if row[18] is None else str(row[18]),
            "trailing_active": bool(row[19]), "hedge_opened": bool(row[20]),
            "closed_at": None if row[21] is None else row[21].isoformat(),
            "exit_price": None if row[22] is None else str(row[22]), "exit_reason": row[23],
            "gross_pnl_usdt": None if row[24] is None else str(row[24]),
            "gross_return_pct": None if row[25] is None else str(row[25]),
            "signal_id": row[26], "entry_plan_fingerprint": row[27],
            "exit_plan_fingerprint": row[28],
        }
        for row in position_rows
    ]
    summary = [
        {
            "strategy_id": row[0], "strategy_version": row[1], "strategy_name": row[2],
            "positions": int(row[3]), "open_positions": int(row[4]),
            "closed_positions": int(row[5]), "gross_pnl_usdt": str(row[6]),
            "avg_return_pct": str(row[7]), "avg_mfe_pct": str(row[8]), "avg_mae_pct": str(row[9]),
        }
        for row in aggregate_rows
    ]
    return {
        "installed": True,
        "pending": pending,
        "open": [item for item in positions if item["state"] == "OPEN"],
        "closed": [item for item in positions if item["state"] == "CLOSED"],
        "summary": summary,
    }


def _live_trading_state(*, include_history: bool) -> dict[str, object]:
    tickers = live_tickers()
    liquidity_risks = execution_liquidity_risks()
    with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
        wallet = connection.execute("""SELECT refreshed_at_epoch_ms,total_equity,wallet_balance,
            available_balance,payload_json FROM runtime.wallet_latest WHERE singleton=1""").fetchone()
        rows = connection.execute("""SELECT symbol,position_idx,side,size,entry_price,leverage,
            refreshed_at_epoch_ms,payload_json FROM runtime.hot_positions ORDER BY symbol""").fetchall()
        ownership_rows = []
        if connection.execute("SELECT to_regclass('runtime.position_ownership')").fetchone()[0]:
            ownership_rows = connection.execute(
                """SELECT o.symbol,o.position_idx,o.side,o.strategy_id,o.strategy_version,c.name
                   FROM runtime.position_ownership o
                   LEFT JOIN strategy_entry.strategy_cards c
                     ON c.strategy_id=o.strategy_id AND c.strategy_version=o.strategy_version
                   WHERE o.state='OPEN'
                   ORDER BY o.created_at DESC"""
            ).fetchall()
        strategy_name_rows = connection.execute(
            "SELECT strategy_id,strategy_version,name FROM strategy_entry.strategy_cards"
        ).fetchall()
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
        exact_exit_rows = []
        has_exact_exit_table = bool(
            connection.execute("SELECT to_regclass('runtime.position_exit_attribution')").fetchone()[0]
        )
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
                ORDER BY a.closed_at DESC LIMIT %s""",
                (1000 if include_history else 20,),
            ).fetchall()
        execution_rows = []
        if include_history and not has_exact_exit_table:
            execution_rows = connection.execute("""SELECT symbol,side,exec_price,exec_qty,exec_fee,
                exec_time_ms,order_id,payload_json FROM (
                    SELECT symbol,side,exec_price,exec_qty,exec_fee,exec_time_ms,order_id,payload_json
                    FROM runtime.executions ORDER BY exec_time_ms DESC LIMIT 5000
                ) AS recent_executions ORDER BY exec_time_ms ASC""").fetchall()
        lifecycle_rows = []
        if include_history and connection.execute(
            "SELECT to_regclass('analyst.trade_lifecycles')"
        ).fetchone()[0]:
            lifecycle_rows = connection.execute("""SELECT trade_id,position_id,symbol,side,
                strategy_id,strategy_version,opened_at,closed_at,lifecycle_state,
                data_completeness,diagnosis_class,actual_net_pnl,lifecycle_json,
                actual_net_without_funding,bot_instance_id,entry_command_id,
                geometry_handoff_id
                FROM analyst.trade_lifecycles ORDER BY closed_at DESC NULLS LAST LIMIT 500""").fetchall()
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
    ownership_by_position = {
        (str(row[0]), int(row[1] or 0), str(row[2])): {
            "strategy_id": str(row[3]),
            "strategy_version": str(row[4]),
            "strategy_name": None if row[5] is None else str(row[5]),
        }
        for row in ownership_rows
    }
    strategy_name_by_identity = {
        (str(row[0]), str(row[1])): str(row[2]) for row in strategy_name_rows
    }
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
        ownership = ownership_by_position.get((str(row[0]), int(row[1] or 0), str(row[2])))
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
                "strategy_id": None if ownership is None else ownership["strategy_id"],
                "strategy_version": None if ownership is None else ownership["strategy_version"],
                "strategy_name": None if ownership is None else ownership["strategy_name"],
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
    for row in lifecycle_rows:
        lifecycle = row[12] if isinstance(row[12], dict) else json.loads(row[12])
        card = {
            "trade_id": row[0], "position_id": row[1], "symbol": row[2], "side": row[3],
            "strategy_id": row[4], "strategy_version": row[5],
            "strategy_name": strategy_name_by_identity.get((str(row[4]), str(row[5]))),
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
        if row[7] is not None:
            lifecycle_exec_ids = set(
                str(value) for value in (lifecycle.get("close_fill") or {}).get("exec_ids", [])
            )
            candidate = next(
                (
                    item for item in recent_closed
                    if lifecycle_exec_ids.intersection(item.get("lz{b��q���q�^w]5ێ[�׬ON_PERMISSION_PATH:
            _u6_set_execution_permission(handler, request)
        elif path == R1_MICRO_LIVE_PATH:
            _u6_r1_micro_live(handler, request)
        else:
            _u6_json(handler, 404, {"error": "unknown Strategy endpoint"})
    except StaleActivationState:
        _u6_json(handler, 409, {"error": "STALE_ACTIVATION_STATE"})
    except StrategyRuntimeNotReady as exc:
        _u6_json(
            handler,
            409,
            {
                "error": "STRATEGY_RUNTIME_NOT_READY",
                "runtime_readiness": exc.readiness.as_dict(),
            },
        )
    except UnknownActivation:
        _u6_json(handler, 404, {"error": "StrategyActivation NOT SET"})
    except psycopg.errors.UniqueViolation:
        _u6_json(handler, 409, {"error": "Strategy version already exists"})
    except (KeyError, ValueError, json.JSONDecodeError, psycopg.Error) as exc:
        _u6_json(handler, 400, {"error": str(exc)})
# U6_STRATEGY_API_END


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
                    return _verify_system_password_hash(password, stored_hash)
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
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
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
        elif path == U6_STRATEGY_GET_PATH:
            _u6_send_catalog(self)
        elif path == R1_MICRO_LIVE_STATUS_PATH:
            try:
                with psycopg.connect(
                    "dbname=cripta user=cripta host=/var/run/postgresql"
                ) as connection:
                    state = r1_micro_live_state(
                        connection,
                        release_commit=LOADED_RELEASE_COMMIT,
                        now=datetime.now(UTC),
                    )
                _u6_json(self, 200, state)
            except (ValueError, RuntimeError, psycopg.Error) as exc:
                _u6_json(self, 409, {"error": str(exc)})
        elif path == "/api/live/state":
            view = str((query.get("view") or ["open"])[0])
            if view not in {"open", "closed", "paper_open", "paper_closed", "monitor", "signals"}:
                view = "open"
            body = json.dumps(
                {
                    "live_trading": _live_trading_state(include_history=view == "closed"),
                    "opportunities": opportunity_state()
                    if view == "signals"
                    else {"counts": {}, "items": []},
                    "signal_monitor": strategy_signal_monitor_state()
                    if view == "signals"
                    else None,
                    "strategy_monitor": strategy_monitor_state() if view == "monitor" else None,
                    "entry_shadow": None,
                    "paper_strategy": strategy_paper_state()
                    if view in {"paper_open", "paper_closed"}
                    else None,
                    "mayak_v2": mayak_v2_state() if view == "open" else None,
                    "view": view,
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
        if path in U6_STRATEGY_POST_PATHS:
            _u6_handle_post(self, path)
            return
        if path == "/api/lifecycle/fault-delivery/ack":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4096:
                    raise ValueError("недопустимый размер запроса")
                request = json.loads(self.rfile.read(length))
                delivery_id = str(request.get("delivery_id") or "").strip()
                if not delivery_id:
                    raise ValueError("delivery_id обязателен")
                with psycopg.connect(
                    "dbname=cripta user=cripta host=/var/run/postgresql"
                ) as connection:
                    acknowledged = acknowledge_delivery(
                        connection,
                        delivery_id=delivery_id,
                        acknowledged_at=datetime.now(UTC),
                    )
                if not acknowledged:
                    self.send_body(
                        409,
                        json.dumps(
                            {"error": "delivery не ожидает acknowledgement"},
                            ensure_ascii=False,
                        ).encode(),
                        "application/json; charset=utf-8",
                    )
                    return
                self.send_body(
                    200,
                    json.dumps(
                        {"delivery_id": delivery_id, "state": "ACKNOWLEDGED"},
                        ensure_ascii=False,
                    ).encode(),
                    "application/json; charset=utf-8",
                )
            except (ValueError, json.JSONDecodeError, psycopg.Error) as exc:
                self.send_body(
                    400,
                    json.dumps({"error": str(exc)}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
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
                        gate_request_id = str(
                            request.get("request_id") or secrets.token_hex(8)
                        )
                        if enabled and request.get("confirmed") is not True:
                            raise ValueError("включение новых входов не подтверждено")
                        live_context: LiveArmContext | None = None
                        if enabled:
                            live_context = _live_arm_context_from_request(request)
                            readiness = live_rearm_readiness(connection)
                            if not bool(readiness.get("rearm_ready")):
                                raise ValueError(
                                    "re-arm blocked: " + "; ".join(readiness.get("reasons", []))
                                )
                            canonical = evaluate_live_arm(
                                connection,
                                context=live_context,
                                now=datetime.now(UTC),
                                require_owner_approval=False,
                            )
                            if not canonical.ready:
                                raise ValueError(
                                    "canonical LIVE-arm blocked: "
                                    + ", ".join(canonical.failed_codes)
                                )
                        previous_gate = connection.execute(
                            "SELECT enabled FROM control.execution_gates "
                            "WHERE mode='mainnet' FOR UPDATE"
                        ).fetchone()
                        previous_enabled = bool(previous_gate[0]) if previous_gate else False
                        gate_changed_at_ms = int(time.time() * 1000)
                        gate_changed_at = datetime.fromtimestamp(
                            gate_changed_at_ms / 1000, tz=UTC
                        )
                        gate_reason = (
                            "явно включено владельцем через портал"
                            if enabled
                            else "выключено владельцем через портал"
                        )
                        if enabled:
                            assert live_context is not None
                            owner_evidence_id = (
                                "live-owner-"
                                + hashlib.sha256(
                                    (
                                        gate_request_id
                                        + "|"
                                        + strategy_symbol_scope_key(live_context)
                                        + "|"
                                        + str(gate_changed_at_ms)
                                    ).encode()
                                ).hexdigest()[:32]
                            )
                            live_arm_session_id = (
                                "live-session-"
                                + hashlib.sha256(
                                    (
                                        gate_request_id
                                        + "|"
                                        + strategy_symbol_scope_key(live_context)
                                        + "|"
                                        + live_context.release_commit
                                    ).encode()
                                ).hexdigest()[:32]
                            )
                            connection.execute(
                                """INSERT INTO control.live_arm_evidence(
                                       evidence_id,check_code,scope_type,scope_key,status,
                                       checked_at,valid_until,release_commit,source,evidence
                                   ) VALUES(
                                       %s,'MAINNET_GATE_EXPLICIT_OWNER_APPROVAL',
                                       'STRATEGY_SYMBOL',%s,'PASS',%s,NULL,%s,
                                       'dashboard:owner',%s::jsonb
                                   )""",
                                (
                                    owner_evidence_id,
                                    strategy_symbol_scope_key(live_context),
                                    gate_changed_at,
                                    live_context.release_commit,
                                    json.dumps(
                                        {
                                            "request_id": gate_request_id,
                                            "live_arm_session_id": live_arm_session_id,
                                        }
                                    ),
                                ),
                            )
                            full_readiness = evaluate_live_arm(
                                connection,
                                context=live_context,
                                now=gate_changed_at,
                                require_owner_approval=True,
                            )
                            if not full_readiness.ready:
                                raise ValueError(
                                    "canonical LIVE-arm owner approval incomplete: "
                                    + ", ".join(full_readiness.failed_codes)
                                )
                            connection.execute(
                                """INSERT INTO control.live_arm_sessions(
                                       live_arm_session_id,strategy_id,strategy_version,
                                       strategy_config_fingerprint,strategy_activation_id,
                                       symbol,release_commit,state,owner_approved_at,
                                       activated_at,deactivated_at,source
                                   ) VALUES(
                                       %s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s,NULL,
                                       'dashboard:owner'
                                   )""",
                                (
                                    live_arm_session_id,
                                    live_context.strategy_id,
                                    live_context.strategy_version,
                                    live_context.strategy_config_fingerprint,
                                    live_context.strategy_activation_id,
                                    live_context.symbol,
                                    live_context.release_commit,
                                    gate_changed_at,
                                    gate_changed_at,
                                ),
                            )
                        else:
                            connection.execute(
                                """UPDATE control.live_arm_sessions
                                      SET state='CLOSED',deactivated_at=%s,
                                          updated_at=clock_timestamp()
                                    WHERE state='ACTIVE'""",
                                (gate_changed_at,),
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
                                gate_request_id,
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
