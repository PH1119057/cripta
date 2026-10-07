from __future__ import annotations

from decimal import Decimal
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
    scope_for_check,
    strategy_symbol_scope_key,
)
from bybit_workbench.r1_micro_live_control import (
    arm as arm_r1_micro_live,
    disarm as disarm_r1_micro_live,
    expected_contexts as r1_expected_contexts,
    micro_live_state as r1_micro_live_state,
)
from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.r1_strategy import build_r1_cards
from bybit_workbench.universal_entry.readiness import assess_strategy_runtime_readiness
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
PAPER_MAKER_FEE_RATE = 0.00020
PAPER_TAKER_FEE_RATE = 0.00055
REAL_IMMEDIATE_CLOSE_FEE_RATE = 0.00055
OBSERVER_RUNTIME_FAULT_CODE = "UNIVERSAL_ENTRY_OBSERVER_RUNTIME_ERROR"
R1_PARITY_ATTESTED_MODULE_SHA256 = {
    "operations/monitoring/universal_entry_shadow.py": "e50b9298fe0b3d91e5c650e38b4482c8fee7beadea48e9f03d10fcec05e25010",
    "src/bybit_workbench/universal_entry/engine.py": "3f703f4da5c4bfc69df324aab89c1767223ed639be48ac43d34f5bc8fa1adedf",
    "src/bybit_workbench/universal_entry/paper_runtime.py": "a9dcdf57cccb527d148eb6bc4122af5396af0976875efd94855242e1aefb2767",
    "src/bybit_workbench/universal_entry/reverse_intent.py": "4d942fe169ce2c7ce8eb6b9ef1b3f894fd2f6f1228b0161d295e86ca8b8e3ac3",
    "src/bybit_workbench/universal_exit/engine.py": "d5da446f651fbddd88189c50a30f0591cd92b18b326c31f5271476101672cd36",
    "src/bybit_workbench/universal_exit/execution_bridge.py": "259d7c6972666e05b5d9c4ae3ee602e5f16440e28183ed87740365ca4299c2c2",
}


def _paper_entry_fee_rate(order_type: object) -> float:
    return PAPER_MAKER_FEE_RATE if str(order_type or "").upper() == "LIMIT_OFFSET" else PAPER_TAKER_FEE_RATE


def _paper_exit_fee_rate(exit_reason: object) -> float:
    return PAPER_MAKER_FEE_RATE if str(exit_reason or "").upper() == "TAKE_PROFIT" else PAPER_TAKER_FEE_RATE


def _paper_net_pnl_usdt(
    *, gross_pnl: object, entry_price: object, exit_price: object,
    quantity: object, order_type: object, exit_reason: object,
) -> float | None:
    if any(value is None for value in (gross_pnl, entry_price, exit_price, quantity)):
        return None
    qty = float(quantity)
    entry = float(entry_price)
    exit_value = float(exit_price)
    gross = float(gross_pnl)
    entry_fee = entry * qty * _paper_entry_fee_rate(order_type)
    exit_fee = exit_value * qty * _paper_exit_fee_rate(exit_reason)
    return gross - entry_fee - exit_fee


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
                        gross_open = (
                            (current_price - entry_price) * quantity
                            if str(row[6]) == "LONG"
                            else (entry_price - current_price) * quantity
                        )
                        entry_fee = entry_price * quantity * _paper_entry_fee_rate(row[8])
                        exit_fee = current_price * quantity * PAPER_TAKER_FEE_RATE
                        pnl = gross_open - entry_fee - exit_fee
                        pnl_kind = "open"
                elif position_state == "CLOSED":
                    pnl = _paper_net_pnl_usdt(
                        gross_pnl=row[21],
                        entry_price=row[16],
                        exit_price=row[19],
                        quantity=row[17],
                        order_type=row[8],
                        exit_reason=row[20],
                    )
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
                        "pnl_basis": "AFTER_COMMISSIONS",
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


def strategy_trade_monitor_state() -> dict[str, object]:
    if not UNIVERSAL_ENTRY_OBSERVER_STATE.exists():
        return {
            "state": "OFF",
            "observer_ready": False,
            "updated_at": None,
            "facts_received": 0,
            "signals": 0,
            "items": [],
        }
    try:
        loaded = json.loads(UNIVERSAL_ENTRY_OBSERVER_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "state": "ERROR",
            "observer_ready": False,
            "updated_at": None,
            "facts_received": 0,
            "signals": 0,
            "items": [],
        }
    if not isinstance(loaded, dict):
        return {
            "state": "ERROR",
            "observer_ready": False,
            "updated_at": None,
            "facts_received": 0,
            "signals": 0,
            "items": [],
        }
    keep = (
        "strategy_id",
        "strategy_version",
        "symbol",
        "direction",
        "state",
        "current_price",
        "entry_price",
        "distance_pct",
        "candidate_id",
        "r1_stability",
        "last_touch_at",
        "updated_at",
    )
    items = []
    for raw in loaded.get("strategy_monitors", []):
        if not isinstance(raw, dict):
            continue
        if str(raw.get("symbol") or "") in BYBIT_KZ_UNSUPPORTED:
            continue
        items.append({key: raw.get(key) for key in keep})
    return {
        "state": loaded.get("state"),
        "observer_ready": bool(loaded.get("observer_ready")),
        "updated_at": loaded.get("updated_at"),
        "facts_received": int(loaded.get("facts_received") or 0),
        "signals": int(loaded.get("signals") or 0),
        "items": items,
    }


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
    try:
        paper_tickers = live_tickers()
    except Exception:
        paper_tickers = {}
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
                      p.exit_plan_fingerprint,o.order_type
                 FROM strategy_entry.paper_positions p
                 LEFT JOIN strategy_entry.paper_orders o
                   ON o.paper_order_id=p.paper_order_id
                 LEFT JOIN strategy_entry.strategy_cards c
                   ON c.strategy_id=p.strategy_id AND c.strategy_version=p.strategy_version
                  AND c.strategy_config_fingerprint=p.strategy_config_fingerprint
                ORDER BY p.opened_at DESC LIMIT 1000"""
        ).fetchall()
        active_aggregate_rows = connection.execute(
            """SELECT p.strategy_id,p.strategy_version,c.name,
                      count(*) FILTER (WHERE p.leg_type='PRIMARY') AS positions,
                      count(*) FILTER (WHERE p.state='OPEN') AS open_positions,
                      count(*) FILTER (WHERE p.state='CLOSED') AS closed_positions,
                      coalesce(sum(
                          p.gross_pnl_usdt
                          - p.entry_price*p.quantity*(
                              CASE WHEN o.order_type='LIMIT_OFFSET' THEN 0.0002 ELSE 0.00055 END
                            )
                          - p.exit_price*p.quantity*(
                              CASE WHEN p.exit_reason='TAKE_PROFIT' THEN 0.0002 ELSE 0.00055 END
                            )
                      ) FILTER (WHERE p.state='CLOSED'),0),
                      coalesce(avg(p.gross_return_pct) FILTER (WHERE p.state='CLOSED'),0),
                      coalesce(avg(p.mfe_pct) FILTER (WHERE p.leg_type='PRIMARY'),0),
                      coalesce(avg(p.mae_pct) FILTER (WHERE p.leg_type='PRIMARY'),0)
                 FROM strategy_entry.paper_positions p
                 LEFT JOIN strategy_entry.paper_orders o ON o.paper_order_id=p.paper_order_id
                 JOIN strategy_entry.strategy_activations a
                   ON a.strategy_id=p.strategy_id
                  AND a.strategy_version=p.strategy_version
                  AND a.strategy_config_fingerprint=p.strategy_config_fingerprint
                  AND a.enabled=true
                 LEFT JOIN strategy_entry.strategy_cards c
                   ON c.strategy_id=p.strategy_id AND c.strategy_version=p.strategy_version
                  AND c.strategy_config_fingerprint=p.strategy_config_fingerprint
                GROUP BY p.strategy_id,p.strategy_version,c.name
                ORDER BY p.strategy_id,p.strategy_version"""
        ).fetchall()
        historical_aggregate_rows = connection.execute(
            """SELECT p.strategy_id,p.strategy_version,c.name,
                      count(*) FILTER (WHERE p.leg_type='PRIMARY') AS positions,
                      count(*) FILTER (WHERE p.state='OPEN') AS open_positions,
                      count(*) FILTER (WHERE p.state='CLOSED') AS closed_positions,
                      coalesce(sum(
                          p.gross_pnl_usdt
                          - p.entry_price*p.quantity*(
                              CASE WHEN o.order_type='LIMIT_OFFSET' THEN 0.0002 ELSE 0.00055 END
                            )
                          - p.exit_price*p.quantity*(
                              CASE WHEN p.exit_reason='TAKE_PROFIT' THEN 0.0002 ELSE 0.00055 END
                            )
                      ) FILTER (WHERE p.state='CLOSED'),0),
                      coalesce(avg(p.gross_return_pct) FILTER (WHERE p.state='CLOSED'),0),
                      coalesce(avg(p.mfe_pct) FILTER (WHERE p.leg_type='PRIMARY'),0),
                      coalesce(avg(p.mae_pct) FILTER (WHERE p.leg_type='PRIMARY'),0)
                 FROM strategy_entry.paper_positions p
                 LEFT JOIN strategy_entry.paper_orders o ON o.paper_order_id=p.paper_order_id
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
            "exit_plan_fingerprint": row[28], "entry_order_type": row[29],
            "net_pnl_usdt": _paper_net_pnl_usdt(
                gross_pnl=row[24], entry_price=row[10], exit_price=row[22],
                quantity=row[14], order_type=row[29], exit_reason=row[23],
            ),
        }
        for row in position_rows
    ]
    for item in positions:
        if item["state"] != "OPEN":
            continue
        ticker = paper_tickers.get(str(item["symbol"])) or {}
        current_price = (
            ticker.get("bid_price")
            if item["direction"] == "LONG"
            else ticker.get("ask_price")
        ) or ticker.get("last_price") or ticker.get("mark_price")
        if current_price in (None, ""):
            continue
        entry = float(item["entry_price"])
        qty = float(item["quantity"])
        current = float(current_price)
        gross = (
            (current - entry) * qty
            if item["direction"] == "LONG"
            else (entry - current) * qty
        )
        entry_fee = entry * qty * _paper_entry_fee_rate(item["entry_order_type"])
        exit_fee = current * qty * PAPER_TAKER_FEE_RATE
        item["current_price"] = str(current_price)
        item["net_pnl_usdt"] = gross - entry_fee - exit_fee
        item["pnl_basis"] = "AFTER_COMMISSIONS_TO_IMMEDIATE_CLOSE"
    def aggregate_payload(rows: list[object]) -> list[dict[str, object]]:
        return [
            {
                "strategy_id": row[0], "strategy_version": row[1], "strategy_name": row[2],
                "positions": int(row[3]), "open_positions": int(row[4]),
                "closed_positions": int(row[5]), "net_pnl_usdt": str(row[6]),
                "avg_return_pct": str(row[7]), "avg_mfe_pct": str(row[8]), "avg_mae_pct": str(row[9]),
                "pnl_basis": "AFTER_COMMISSIONS",
            }
            for row in rows
        ]

    summary = aggregate_payload(active_aggregate_rows)
    history_summary = aggregate_payload(historical_aggregate_rows)
    return {
        "installed": True,
        "pending": pending,
        "open": [item for item in positions if item["state"] == "OPEN"],
        "closed": [item for item in positions if item["state"] == "CLOSED"],
        "summary": summary,
        "history_summary": history_summary,
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
                """SELECT o.symbol,o.position_idx,o.side,o.strategy_id,o.strategy_version,c.name,
                      coalesce((
                          SELECT sum(e.exec_fee::numeric)
                            FROM jsonb_array_elements_text(o.execution_ids) AS xid(exec_id)
                            JOIN runtime.executions e ON e.exec_id=xid.exec_id
                      ),0) AS entry_fee_actual
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
            "entry_fee_actual": float(row[6] or 0),
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
        entry_price = float(row[4] or 0)
        position_size = float(row[3] or 0)
        executable_value = float(executable_price or ticker.get("last_price") or raw.get("markPrice") or 0)
        gross_to_close = (
            (executable_value - entry_price) * position_size
            if row[2] == "Buy"
            else (entry_price - executable_value) * position_size
        ) if entry_price > 0 and executable_value > 0 and position_size > 0 else None
        net_to_close = None
        if gross_to_close is not None and ownership is not None:
            exit_fee_estimate = executable_value * position_size * REAL_IMMEDIATE_CLOSE_FEE_RATE
            net_to_close = gross_to_close - float(ownership["entry_fee_actual"]) - exit_fee_estimate
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
                "net_pnl_to_close": net_to_close,
                "pnl_basis": "AFTER_COMMISSIONS_TO_IMMEDIATE_CLOSE",
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