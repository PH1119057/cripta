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
from safety_observer import api_get as safety_api_get

from bybit_workbench.account_state_generation import (
    AccountStateGenerationUnavailable,
    current_complete_account_state_generation,
)
from bybit_workbench.exchange.bybit.mappers import map_rest_klines
from bybit_workbench.exchange.bybit.time_calibration import (
    BybitTimeCalibration,
    build_bybit_time_calibration,
)
from bybit_workbench.universal_entry.market_watch import compute_r1_l53_stable_zone
from bybit_workbench.universal_entry.r1_strategy import R1_STRATEGY_IDS, R1_SYMBOLS, R1_VERSION

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
ACCOUNT_REF = "BYBIT:UNIFIED"
SIGNED_RECV_WINDOW = "5000"
SIGNED_READ_RECV_WINDOW = "10000"
SIGNED_READ_TIMEOUT_SECONDS = 5.0
SIGNED_MUTATION_TIMEOUT_SECONDS = 3.0
BYBIT_TIME_PROBE_MAX_RTT_MS = 1000.0
BYBIT_TIME_PROBE_ATTEMPTS = 3
PRIVATE_RECONNECT_GATE_REASON = "private WS reconnect: verification pending"
PRIVATE_RECONNECT_RECOVERED_REASON = (
    "private WS reconnect recovered: verified R1 arm restored"
)


class PreMutationSafetyBlock(RuntimeError):
    """A safety prerequisite failed before any exchange mutation was sent."""


class ExchangeMutationBarrier(RuntimeError):
    """Mutation outcome or immediate post-mutation truth is uncertain."""


class UnsafeBybitClock(PreMutationSafetyBlock):
    """Fresh Bybit-time calibration could not be established safely."""


class AmbiguousBybitMutation(ExchangeMutationBarrier):
    """Transport ended without deterministic exchange acknowledgement."""


class BybitMutationRejected(RuntimeError):
    """Bybit explicitly rejected the mutation."""


class ExchangeReadUnavailable(RuntimeError):
    """Read-only exchange truth could not be refreshed deterministically."""


def api_get(
    path: str,
    params: dict[str, str],
    key: str = "",
    secret: str = "",
) -> tuple[dict[str, object], float]:
    """Bybit GET. Signed live reads get a wider window than trading mutations."""
    if not key:
        return safety_api_get(path, params, key, secret)

    query = urllib.parse.urlencode(sorted(params.items()))
    started = time.perf_counter()
    last_timestamp_ms = 0
    calibration: BybitTimeCalibration | None = None
    for attempt in range(2):
        candidate_ms = (
            int(time.time() * 1000)
            if calibration is None
            else calibration.now_ms()
        )
        timestamp_ms = max(candidate_ms, last_timestamp_ms + 1)
        last_timestamp_ms = timestamp_ms
        timestamp = str(timestamp_ms)
        signature = hmac.new(
            secret.encode(),
            f"{timestamp}{key}{SIGNED_READ_RECV_WINDOW}{query}".encode(),
            hashlib.sha256,
        ).hexdigest()
        request = urllib.request.Request(
            f"{REST_URL}{path}{'?' + query if query else ''}",
            headers={
                "X-BAPI-API-KEY": key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": SIGNED_READ_RECV_WINDOW,
                "X-BAPI-SIGN": signature,
                "User-Agent": "cripta-live-read/1",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=SIGNED_READ_TIMEOUT_SECONDS,
            ) as response:
                payload = json.load(response)
        except (TimeoutError, urllib.error.URLError, OSError, ValueError) as exc:
            raise ExchangeReadUnavailable(
                f"Bybit GET transport failed path={path} "
                f"error={type(exc).__name__}:{exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise ExchangeReadUnavailable(
                f"Bybit GET returned non-object payload path={path}"
            )
        code = int(payload.get("retCode", -1))
        if code == 10002 and attempt == 0:
            try:
                calibration = fresh_bybit_time_calibration()
            except Exception as exc:
                raise ExchangeReadUnavailable(
                    "Bybit signed GET timestamp recovery could not calibrate "
                    f"exchange time: {type(exc).__name__}:{exc}"
                ) from exc
            continue
        return payload, (time.perf_counter() - started) * 1000

    raise AssertionError("unreachable signed GET retry state")


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



def mainnet_gate_enabled(connection: psycopg.Connection) -> bool:
    row = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    return bool(row and row[0])


def restore_r1_gate_after_verified_reconnect(
    connection: psycopg.Connection,
    *,
    reconnect_started_ms: int,
    hot_positions: int,
    hot_orders: int,
) -> bool:
    """Restore only an already owner-armed flat R1 cohort after verified reconnect."""
    if hot_positions or hot_orders:
        return False

    gate = connection.execute(
        "SELECT enabled,reason FROM control.execution_gates WHERE mode='mainnet' FOR UPDATE"
    ).fetchone()
    if (
        not gate
        or bool(gate[0])
        or str(gate[1] or "") != PRIVATE_RECONNECT_GATE_REASON
    ):
        connection.rollback()
        return False

    try:
        release_commit = Path("/proc/self/cwd/INSTALLED_COMMIT").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        connection.rollback()
        return False
    if len(release_commit) != 40:
        connection.rollback()
        return False

    expected_ids = set(R1_STRATEGY_IDS.values())
    active_sessions = connection.execute(
        """SELECT strategy_id
             FROM control.live_arm_sessions
            WHERE state='ACTIVE'
              AND release_commit=%s
              AND strategy_version=%s""",
        (release_commit, R1_VERSION),
    ).fetchall()
    if len(active_sessions) != 5 or {str(row[0]) for row in active_sessions} != expected_ids:
        connection.rollback()
        return False
    if connection.execute(
        "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
    ).fetchone()[0] != 5:
        connection.rollback()
        return False

    permissions = connection.execute(
        """SELECT strategy_id,strategy_version
             FROM strategy_entry.execution_permissions
            WHERE enabled=true"""
    ).fetchall()
    if len(permissions) != 5 or {
        (str(row[0]), str(row[1])) for row in permissions
    } != {(strategy_id, R1_VERSION) for strategy_id in expected_ids}:
        connection.rollback()
        return False

    activations = connection.execute(
        """SELECT strategy_id,strategy_version
             FROM strategy_entry.strategy_activations
            WHERE enabled=true"""
    ).fetchall()
    if len(activations) != 5 or {
        (str(row[0]), str(row[1])) for row in activations
    } != {(strategy_id, R1_VERSION) for strategy_id in expected_ids}:
        connection.rollback()
        return False

    reconciliation = connection.execute(
        """SELECT started_at_epoch_ms,finished_at_epoch_ms,reason,ok,positions,orders
             FROM runtime.reconciliation_runs ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    if (
        not reconciliation
        or int(reconciliation[0]) < reconnect_started_ms
        or str(reconciliation[2]) != "reconnect"
        or not bool(reconciliation[3])
        or int(reconciliation[4]) != 0
        or int(reconciliation[5]) != 0
    ):
        connection.rollback()
        return False

    wallet = connection.execute(
        """SELECT refreshed_at_epoch_ms,payload_json::jsonb->>'accountType'
             FROM runtime.wallet_latest WHERE singleton=1"""
    ).fetchone()
    if (
        not wallet
        or int(wallet[0]) < reconnect_started_ms
        or str(wallet[1]) != "UNIFIED"
    ):
        connection.rollback()
        return False

    mode_rows = connection.execute(
        """SELECT DISTINCT ON (instrument)
                  instrument,position_mode,position_idx,observed_at,fresh_until
             FROM runtime.position_mode_states
            WHERE account_ref='BYBIT:UNIFIED'
              AND product_category='LINEAR'
              AND instrument = ANY(%s)
              AND observed_at >= to_timestamp(%s / 1000.0)
            ORDER BY instrument,observed_at DESC""",
        (list(R1_SYMBOLS), reconnect_started_ms),
    ).fetchall()
    now = datetime.now(UTC)
    if len(mode_rows) != 5 or {str(row[0]) for row in mode_rows} != set(R1_SYMBOLS):
        connection.rollback()
        return False
    if any(
        str(row[1]) != "ONE_WAY"
        or int(row[2] if row[2] is not None else -1) != 0
        or row[4] is None
        or row[4].astimezone(UTC) < now
        for row in mode_rows
    ):
        connection.rollback()
        return False

    now_ms = int(time.time() * 1000)
    connection.execute(
        """UPDATE control.execution_gates
              SET enabled=1,reason=%s,updated_at_epoch_ms=%s
            WHERE mode='mainnet'
              AND enabled=0
              AND reason=%s""",
        (PRIVATE_RECONNECT_RECOVERED_REASON, now_ms, PRIVATE_RECONNECT_GATE_REASON),
    )
    connection.execute(
        """INSERT INTO control.execution_gate_events(
               at_epoch_ms,mode,previous_enabled,requested_enabled,
               resulting_enabled,reason,source,origin,request_id,settings_version)
           VALUES(%s,'mainnet',false,true,true,%s,
                  'private_runtime','safety_recovery',%s,%s)""",
        (
            now_ms,
            PRIVATE_RECONNECT_RECOVERED_REASON,
            f"private-reconnect-recover-{reconnect_started_ms}",
            R1_VERSION,
        ),
    )
    connection.commit()
    return True


def entry_runtime_readiness(connection: psycopg.Connection) -> tuple[bool, str]:
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    if not gate or not bool(gate[0]):
        return False, "NEW_ENTRY_GATE_DISARMED"
    private = status.get("private", {})
    if not isinstance(private, dict) or private.get("state") != "connected":
        return False, "PRIVATE_WS_NOT_CONNECTED"
    try:
        current_complete_account_state_generation(
            connection,
            account_ref=ACCOUNT_REF,
        )
    except AccountStateGenerationUnavailable:
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
        actual_stop, actual_target = resolve_initial_protection_boundaries(
            entry=actual_entry,
            side=str(position_row[0]),
            tick=tick,
            contract=contract,
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
        if actual_target is not None:
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
    calibration = fresh_bybit_time_calibration()
    expires = calibration.now_ms() + 10_000
    signature = hmac.new(
        secret.encode(),
        f"GET/realtime{expires}".encode(),
        hashlib.sha256,
    ).hexdigest()
    ws.send(
        json.dumps(
            {"op": "auth", "args": [key, expires, signature]},
            separators=(",", ":"),
        )
    )
    response = json.loads(ws.recv())
    success = response.get("success") is True or response.get("retCode") in (0, 20001)
    if not success:
        raise RuntimeError(
            f"websocket auth failed: {response.get('retMsg') or response.get('ret_msg')}"
        )


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


def fresh_bybit_time_calibration() -> BybitTimeCalibration:
    """Calibrate signed timestamps to Bybit itself, not to the host wall clock."""

    observations: list[BybitTimeCalibration] = []
    errors: list[str] = []
    for attempt in range(1, BYBIT_TIME_PROBE_ATTEMPTS + 1):
        wall_started_ns = time.time_ns()
        monotonic_started_ns = time.monotonic_ns()
        try:
            payload, _ = api_get("/v5/market/time", {})
        except Exception as exc:
            errors.append(f"{attempt}:{type(exc).__name__}:{exc}")
            continue
        wall_finished_ns = time.time_ns()
        monotonic_finished_ns = time.monotonic_ns()

        if int(payload.get("retCode", -1)) != 0:
            errors.append(
                f"{attempt}:retCode={payload.get('retCode')}:{payload.get('retMsg')}"
            )
            continue

        calibration = build_bybit_time_calibration(
            server_time_ms=_server_time_ms(payload),
            wall_started_ns=wall_started_ns,
            wall_finished_ns=wall_finished_ns,
            monotonic_started_ns=monotonic_started_ns,
            monotonic_finished_ns=monotonic_finished_ns,
        )
        observations.append(calibration)
        if calibration.round_trip_ms <= BYBIT_TIME_PROBE_MAX_RTT_MS:
            return calibration

    if observations:
        best = min(observations, key=lambda item: item.round_trip_ms)
        raise UnsafeBybitClock(
            "UNSAFE_BYBIT_TIME_PROBE_RTT "
            f"best_rtt_ms={best.round_trip_ms:.1f} "
            f"limit_ms={BYBIT_TIME_PROBE_MAX_RTT_MS:.1f} "
            f"offset_ms={best.offset_ms:.1f} "
            f"attempts={BYBIT_TIME_PROBE_ATTEMPTS}"
        )

    raise UnsafeBybitClock(
        "BYBIT_TIME_CALIBRATION_UNAVAILABLE "
        + (";".join(errors[-BYBIT_TIME_PROBE_ATTEMPTS:]) or "no observations")
    )


def assert_mutation_clock_safe() -> BybitTimeCalibration:
    """Return fresh exchange-time evidence before a mutation is constructed."""

    return fresh_bybit_time_calibration()


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
    calibration = assert_mutation_clock_safe()
    body = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
    timestamp = str(calibration.now_ms())
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


def handle_pre_mutation_safety_block(
    connection: psycopg.Connection,
    command_id: str | None,
    exc: BaseException,
) -> None:
    """Fail closed without claiming ambiguity when no mutation was sent."""

    error = f"PRE_MUTATION_SAFETY_BLOCK:{type(exc).__name__}:{exc}"
    connection.rollback()
    if command_id is not None:
        finalize_failed_entry_command_reservation(
            connection,
            command_id=command_id,
            reason=error,
            mutation_ambiguous=False,
        )
        connection.execute(
            """UPDATE runtime.trade_commands
               SET state='failed',finished_at_epoch_ms=%s,error=%s
               WHERE command_id=%s""",
            (int(time.time() * 1000), error, command_id),
        )
        connection.commit()
    disarm_new_entries(connection, error[:500])
    atomic_status(
        "command",
        {
            "state": "blocked",
            "error": error,
            "mutation_sent": False,
            "restart_required": False,
        },
    )


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


def resolve_initial_protection_boundaries(
    *,
    entry: Decimal,
    side: str,
    tick: Decimal,
    contract: dict[str, object],
) -> tuple[Decimal, Decimal | None]:
    """Resolve Strategy-owned Entry-time SL and optional exact TP price."""
    stop_loss_pct = contract["stop_loss_pct"]
    target_pct = contract["take_profit_pct"]
    target_price = contract["take_profit_price"]
    if not isinstance(stop_loss_pct, Decimal):
        raise RuntimeError("ENTRY_INITIAL_PROTECTION_STOP_INVALID")
    if target_pct is not None and not isinstance(target_pct, Decimal):
        raise RuntimeError("ENTRY_INITIAL_PROTECTION_PERCENT_INVALID")
    if target_price is not None and not isinstance(target_price, Decimal):
        raise RuntimeError("ENTRY_INITIAL_PROTECTION_PRICE_INVALID")
    stop, _unused = calculate_initial_boundaries(
        entry=entry,
        side=side,
        tick=tick,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=target_pct if target_pct is not None else Decimal("1"),
    )
    if not bool(contract["take_profit_enabled"]):
        return stop, None
    if target_pct is not None:
        _stop, target = calculate_initial_boundaries(
            entry=entry,
            side=side,
            tick=tick,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=target_pct,
        )
    else:
        if target_price is None:
            raise RuntimeError("ENTRY_INITIAL_PROTECTION_TARGET_MISSING")
        target = quantize(target_price, tick, upward=side == "Buy")
    if (side == "Buy" and target <= entry) or (side == "Sell" and target >= entry):
        raise RuntimeError("ENTRY_INITIAL_PROTECTION_TARGET_WRONG_SIDE")
    return stop, target


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
    if kind == "strategy_exit":
        result = _execute_universal_exit_command(
            connection,
            key,
            secret,
            command_id=command_id,
            symbol=symbol,
            payload=payload,
            positions_payload=positions,
            tick=tick,
            qty_step=qty_step,
        )
    elif kind == "close":
        if not position: raise RuntimeError("open position not found")
        result = api_post("/v5/order/create", {"category":"linear","symbol":symbol,"side":"Sell" if position["side"]=="Buy" else "Buy","orderType":"Market","qty":str(position["size"]),"positionIdx":int(position.get("positionIdx") or 0),"orderLinkId":command_id[:36],"reduceOnly":True,"closeOnTrigger":False}, key, secret)
    elif kind in {"break_even", "current_stop", "initial_protection"}:
        if not position: raise RuntimeError("open position not found")
        side = str(position["side"])
        mark = executable_close_price(symbol, side)
        trigger_by = "LastPrice"
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
            contract = initial_protection_contract(payload)
            trigger_by = str(contract["trigger_by"])
            stop, target = resolve_initial_protection_boundaries(
                entry=actual_entry,
                side=side,
                tick=tick,
                contract=contract,
            )
        if (side=="Buy" and stop >= mark) or (side=="Sell" and stop <= mark): raise RuntimeError("calculated stop is already beyond current price")
        stop_request: dict[str, object] = {"category":"linear","symbol":symbol,"positionIdx":int(position.get("positionIdx") or 0),"tpslMode":"Full","stopLoss":str(stop),"slTriggerBy":trigger_by,"slOrderType":"Market"}
        if kind == "initial_protection":
            stop_request["tpslMode"] = str(contract["tpsl_mode"])
            if target is not None:
                stop_request.update(
                    {
                        "takeProfit": str(target),
                        "tpTriggerBy": trigger_by,
                        "tpOrderType": "Market",
                    }
                )
        try:
            result = api_post("/v5/position/trading-stop", stop_request, key, secret)
        except RuntimeError as exc:
            if kind != "initial_protection" or "not modified" not in str(exc).lower():
                raise
            verified_payload, _ = api_get(
                "/v5/position/list",
                {"category": "linear", "symbol": symbol},
                key,
                secret,
            )
            verified_position = next(
                (
                    item
                    for item in ((verified_payload.get("result") or {}).get("list") or [])
                    if Decimal(str(item.get("size") or 0)) > 0
                    and int(item.get("positionIdx") or 0)
                    == int(position.get("positionIdx") or 0)
                    and str(item.get("side") or "") == side
                ),
                None,
            )
            if verified_position is None:
                raise RuntimeError(
                    "initial protection not-modified could not be verified: position missing"
                ) from exc
            verified_stop = Decimal(str(verified_position.get("stopLoss") or 0))
            verified_target = Decimal(str(verified_position.get("takeProfit") or 0))
            stop_matches = verified_stop == stop
            target_matches = target is None or verified_target == target
            if not stop_matches or not target_matches:
                raise RuntimeError(
                    "initial protection not-modified verification mismatch "
                    f"requested_stop={stop} actual_stop={verified_stop} "
                    f"requested_target={target} actual_target={verified_target}"
                ) from exc
            result = {
                "retCode": 0,
                "retMsg": "not modified",
                "idempotent": True,
                "verifiedFromExchange": True,
            }
        if kind == "break_even":
            result["protectionPlan"] = {name: str(value) for name, value in plan.items()}
        elif kind == "initial_protection":
            result["actualProtection"] = {
                "entryPrice": str(actual_entry),
                "stopLoss": str(stop),
                "takeProfit": None if target is None else str(target),
            }
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
        contract = initial_protection_contract(payload)
        stop, target = resolve_initial_protection_boundaries(
            entry=price,
            side=side,
            tick=tick,
            contract=contract,
        )
        # Entry submits server-side initial protection atomically with the order.
        # R1 carries catastrophic SL plus exact causal opposite-inner TP.
        order: dict[str, object] = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Market" if offset == 0 else "Limit",
            "qty": str(qty),
            "positionIdx": 0,
            "orderLinkId": command_id[:36],
            "tpslMode": str(contract["tpsl_mode"]),
            "stopLoss": str(stop),
            "slTriggerBy": str(contract["trigger_by"]),
            "slOrderType": "Market",
        }
        if target is not None:
            order.update(
                {
                    "takeProfit": str(target),
                    "tpTriggerBy": str(contract["trigger_by"]),
                    "tpOrderType": "Market",
                }
            )
        if offset > 0:
            entry_time_in_force = str(payload.get("entry_time_in_force") or "GTC").upper()
            if entry_time_in_force == "POST_ONLY":
                bybit_time_in_force = "PostOnly"
            elif entry_time_in_force == "GTC":
                bybit_time_in_force = "GTC"
            else:
                raise RuntimeError(
                    f"unsupported entry_time_in_force {entry_time_in_force}"
                )
            order.update({"price": str(price), "timeInForce": bybit_time_in_force})
        result=api_post("/v5/order/create", order, key, secret)
        exchange_order_id = str((result.get("result") or {}).get("orderId") or "")
        if not exchange_order_id:
            raise ExchangeMutationBarrier(
                "entry order acknowledged without exchange orderId"
            )
        try:
            mark_entry_order_acknowledged(
                connection,
                command_id=command_id,
                exchange_order_id=exchange_order_id,
                acknowledged_at=datetime.now(UTC),
            )
            connection.commit()
        except Exception as exc:
            raise ExchangeMutationBarrier(
                f"entry order acknowledged but reservation handoff failed: {exc}"
            ) from exc
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
                raise ExchangeMutationBarrier(
                    "market entry acknowledged but actual fill is not yet confirmed"
                )
            actual_entry = Decimal(str(filled_position.get("avgPrice") or 0))
            if actual_entry <= 0:
                raise RuntimeError("Bybit не вернул фактическую цену исполнения")
            actual_stop, actual_target = resolve_initial_protection_boundaries(
                entry=actual_entry,
                side=side,
                tick=tick,
                contract=contract,
            )
            protection_request: dict[str, object] = {
                "category": "linear", "symbol": symbol,
                "positionIdx": int(filled_position.get("positionIdx") or 0),
                "tpslMode": str(contract["tpsl_mode"]), "stopLoss": str(actual_stop),
                "slTriggerBy": str(contract["trigger_by"]), "slOrderType": "Market",
            }
            if actual_target is not None:
                protection_request.update(
                    {
                        "takeProfit": str(actual_target),
                        "tpTriggerBy": str(contract["trigger_by"]),
                        "tpOrderType": "Market",
                    }
                )
            try:
                protection = api_post(
                    "/v5/position/trading-stop",
                    protection_request,
                    key,
                    secret,
                )
            except RuntimeError as exc:
                if "not modified" not in str(exc).lower():
                    raise
                verified_payload, _ = api_get(
                    "/v5/position/list",
                    {"category": "linear", "symbol": symbol},
                    key,
                    secret,
                )
                verified_position = next(
                    (
                        item
                        for item in ((verified_payload.get("result") or {}).get("list") or [])
                        if Decimal(str(item.get("size") or 0)) > 0
                        and int(item.get("positionIdx") or 0)
                        == int(filled_position.get("positionIdx") or 0)
                        and str(item.get("side") or "") == side
                    ),
                    None,
                )
                if verified_position is None:
                    raise RuntimeError(
                        "entry protection not-modified could not be verified: position missing"
                    ) from exc
                verified_stop = Decimal(str(verified_position.get("stopLoss") or 0))
                verified_target = Decimal(str(verified_position.get("takeProfit") or 0))
                if verified_stop != actual_stop or (
                    actual_target is not None and verified_target != actual_target
                ):
                    raise RuntimeError(
                        "entry protection not-modified verification mismatch "
                        f"requested_stop={actual_stop} actual_stop={verified_stop} "
                        f"requested_target={actual_target} actual_target={verified_target}"
                    ) from exc
                protection = {
                    "retCode": 0,
                    "retMsg": "not modified",
                    "idempotent": True,
                    "verifiedFromExchange": True,
                }
            result["actualProtection"] = {
                "entryPrice": str(actual_entry), "stopLoss": str(actual_stop),
                "takeProfit": None if actual_target is None else str(actual_target),
                "exchange": protection.get("retMsg"),
            }
    else: raise RuntimeError("unknown command type")
    before_position = None if position is None else dict(position)
    try:
        reconcile(connection, key, secret, "after_command")
    except Exception as exc:
        raise ExchangeMutationBarrier(
            f"post-mutation reconciliation failed: {exc}"
        ) from exc
    record_protection_or_owner_event(
        connection, command_id, kind, symbol, payload, before_position
    )
    connection.execute("UPDATE runtime.trade_commands SET state='completed',finished_at_epoch_ms=%s,result_json=%s WHERE command_id=%s",(int(time.time()*1000),json.dumps(result,ensure_ascii=False),command_id)); connection.commit()


def _r1_signal_validity(
    payload: dict[str, object],
    symbol: str,
) -> tuple[bool, str]:
    validity = payload.get("entry_validity")
    if not isinstance(validity, dict):
        raise ExchangeMutationBarrier("R1 SIGNAL_VALIDITY payload is missing entry_validity")
    if str(validity.get("operator") or "") != "R1_EXACT_SIGNAL":
        raise ExchangeMutationBarrier("unsupported R1 entry validity operator")
    side = str(payload.get("side") or "")
    if side not in {"Buy", "Sell"}:
        raise ExchangeMutationBarrier("R1 SIGNAL_VALIDITY payload has invalid side")
    original_entry = Decimal(str(validity.get("signal_entry_price") or 0))
    original_target = Decimal(str(validity.get("signal_target_price") or 0))
    if original_entry <= 0 or original_target <= 0:
        raise ExchangeMutationBarrier("R1 SIGNAL_VALIDITY prices are invalid")

    ticker, _ = api_get(
        "/v5/market/tickers",
        {"category": "linear", "symbol": symbol},
    )
    item = ((ticker.get("result") or {}).get("list") or [{}])[0]
    last = Decimal(str(item.get("lastPrice") or 0))
    if last <= 0:
        raise ExchangeMutationBarrier("R1 SIGNAL_VALIDITY ticker price unavailable")
    target_hit = (side == "Buy" and last >= original_target) or (
        side == "Sell" and last <= original_target
    )
    if target_hit:
        return False, "R1_OPPOSITE_TARGET_REACHED_BEFORE_FILL"

    query = urllib.parse.urlencode(
        {"category": "linear", "symbol": symbol, "interval": "5", "limit": "240"}
    )
    request = urllib.request.Request(
        f"{REST_URL}/v5/market/kline?{query}",
        headers={"User-Agent": "cripta-r1-entry-validity/1"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = json.loads(response.read().decode("utf-8"))
    if int(raw.get("retCode", -1)) != 0:
        raise ExchangeMutationBarrier(
            f"R1 SIGNAL_VALIDITY kline failed: {raw.get('retMsg')}"
        )
    rows = ((raw.get("result") or {}).get("list") or [])
    candles = map_rest_klines(
        rows,
        symbol=symbol,
        interval="5",
        observed_at=datetime.now(UTC),
    )
    # Wilder ATR depends on its seed/history length. Revalidate with exactly
    # the same causal closed-candle window used by the Entry observer.
    # Do not compare a 240-kline REST ATR to the observer's bounded ATR.
    history_limit_raw = validity.get("history_limit")
    if history_limit_raw is None:
        raise ExchangeMutationBarrier("R1 validity lacks exact observer history_limit")
    try:
        history_limit = int(history_limit_raw)
    except (TypeError, ValueError) as exc:
        raise ExchangeMutationBarrier("R1 validity history_limit is invalid") from exc
    if history_limit < 205 or history_limit > 240:
        raise ExchangeMutationBarrier("R1 validity history_limit outside supported window")
    closed = tuple(item for item in candles if item.is_closed and item.timeframe == "5")
    if len(closed) < history_limit:
        raise ExchangeMutationBarrier("R1 validity lacks full causal ATR history")
    zone = compute_r1_l53_stable_zone(closed[-history_limit:])
    if zone is None:
        return False, "R1_SIGNAL_RULE_INVALIDATED"
    current_entry = zone.support_top if side == "Buy" else zone.resistance_bottom
    if current_entry != original_entry:
        return False, "R1_EXACT_ENTRY_LEVEL_CHANGED"
    return True, "R1_SIGNAL_STILL_VALID"


def _cancel_entry_limit(
    connection: psycopg.Connection,
    key: str,
    secret: str,
    *,
    order_id: str,
    symbol: str,
    command_id: str,
    reason: str,
) -> None:
    api_post(
        "/v5/order/cancel",
        {"category": "linear", "symbol": symbol, "orderId": order_id},
        key,
        secret,
        accepted_codes=(110001,),
    )
    try:
        reconcile(connection, key, secret, reason)
        reservation_state = resolve_cancelled_entry_reservation_after_reconcile(
            connection,
            command_id=command_id,
            exchange_order_id=order_id,
            cancel_reason=reason,
        )
        connection.commit()
        if reservation_state == "RECONCILIATION_REQUIRED":
            raise ExchangeMutationBarrier(
                f"{reason}: cancel did not prove deterministic fill state"
            )
    except ExchangeMutationBarrier:
        raise
    except Exception as exc:
        raise ExchangeMutationBarrier(
            f"{reason}: cancel reconciliation failed: {exc}"
        ) from exc


def cancel_expired_entry_limits(
    connection: psycopg.Connection, key: str, secret: str, now_ms: int
) -> None:
    """Cancel limit Entries only by their exact Strategy-owned lifetime contract."""

    rows = connection.execute(
        """SELECT o.order_id,o.symbol,c.command_id,c.requested_at_epoch_ms,c.payload_json
             FROM runtime.hot_orders o
             JOIN runtime.trade_commands c ON o.order_link_id=c.command_id
            WHERE c.command_type='entry' AND c.state='completed'"""
    ).fetchall()
    for order_id, symbol, command_id, requested_at, raw_payload in rows:
        payload = json.loads(raw_payload)
        if Decimal(str(payload.get("entry_offset_pct") or 0)) <= 0:
            continue
        lifetime_mode = str(payload.get("entry_lifetime_mode") or "TIME_TTL").upper()
        if lifetime_mode == "SIGNAL_VALIDITY":
            still_valid, reason = _r1_signal_validity(payload, str(symbol))
            if still_valid:
                continue
            _cancel_entry_limit(
                connection,
                key,
                secret,
                order_id=str(order_id),
                symbol=str(symbol),
                command_id=str(command_id),
                reason=reason,
            )
            continue
        if lifetime_mode != "TIME_TTL":
            raise ExchangeMutationBarrier(
                f"unsupported entry_lifetime_mode={lifetime_mode}"
            )
        raw_ttl = payload.get("entry_limit_ttl_seconds")
        if raw_ttl is None:
            raise ExchangeMutationBarrier(
                "TIME_TTL limit Entry is missing Strategy-owned entry_limit_ttl_seconds"
            )
        ttl_seconds = int(raw_ttl)
        if ttl_seconds <= 0:
            raise ExchangeMutationBarrier(
                "TIME_TTL limit Entry has non-positive Strategy-owned TTL"
            )
        if now_ms - int(requested_at) < ttl_seconds * 1000:
            continue
        _cancel_entry_limit(
            connection,
            key,
            secret,
            order_id=str(order_id),
            symbol=str(symbol),
            command_id=str(command_id),
            reason="expired_entry_limit",
        )


def command_worker_loop(key: str, secret: str) -> None:
    connection = db("cripta-private-command")
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
                except PreMutationSafetyBlock as exc:
                    handle_pre_mutation_safety_block(connection, None, exc)
                except ExchangeMutationBarrier as exc:
                    handle_exchange_mutation_barrier(
                        connection, key, secret, None, exc
                    )
                except Exception:
                    connection.rollback()
                next_limit_cleanup = time.monotonic() + 1
            pickup_window_ms = 10_000 if entry_policy == "base_entry_v1" else SIGNAL_PICKUP_WINDOW_MS
            fresh_after=max(
                now_ms-pickup_window_ms,
                int(gate[1] or 0),
                int(settings[3] or 0),
            )
            signals = []
            if ENTRY_COMMAND_SOURCE == "LEGACY_V1":
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
                if geometry is None:
                    record_entry_decision(
                        connection, signal_id, symbol, direction, signal_at_ms,
                        "запрещён",
                        "нет причинной неизменяемой геометрии Entry; "
                        "нет immutable Entry handoff с strategy initial protection",
                        entry_policy=entry_policy,
                        policy_version="1.0.0-owner-live" if entry_policy=="m3_full_live_v1" else "entry-policy-v1",
                    )
                    continue
                geometry_payload = (
                    geometry[3] if isinstance(geometry[3], dict) else json.loads(str(geometry[3]))
                )
                initial_protection = geometry_payload.get("initial_protection")
                if not isinstance(initial_protection, dict):
                    record_entry_decision(
                        connection, signal_id, symbol, direction, signal_at_ms,
                        "запрещён", "нет strategy initial protection в immutable Entry handoff",
                        entry_policy=entry_policy,
                    )
                    continue
                if (
                    str(initial_protection.get("strategy_id") or "") != str(geometry[1])
                    or str(initial_protection.get("strategy_version") or "") != str(geometry[2])
                ):
                    record_entry_decision(
                        connection, signal_id, symbol, direction, signal_at_ms,
                        "запрещён", "strategy initial protection не соответствует Entry handoff",
                        entry_policy=entry_policy,
                    )
                    continue
                cid="auto-"+hashlib.sha256(str(signal_id).encode()).hexdigest()[:28]
                body={"stake_usdt":settings[0],"leverage":settings[1],"side":"Buy" if direction=="long" else "Sell","price":price,"signal_id":signal_id,"entry_offset_pct":settings[4],"entry_limit_ttl_seconds":settings[5],"entry_policy":entry_policy,"policy_version":"1.0.0-owner-live" if entry_policy=="m3_full_live_v1" else "entry-policy-v1","bot_instance_id":BOT_INSTANCE_ID,"geometry_handoff_id":geometry[0],"strategy_id":geometry[1],"strategy_version":geometry[2],"initial_protection":initial_protection}
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
        filled_entries=connection.execute("""SELECT c.command_id,c.symbol,max(e.exec_time_ms),c.payload_json
            FROM runtime.trade_commands c JOIN runtime.executions e ON e.order_link_id=c.command_id
            WHERE c.command_type='entry' AND c.state='completed'
              AND COALESCE((e.payload_json::jsonb->>'closedSize')::numeric,0)=0
            GROUP BY c.command_id,c.symbol,c.payload_json""").fetchall()
        for entry_id,symbol,fill_time_ms,raw_entry_payload in filled_entries:
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
            entry_payload = (
                raw_entry_payload
                if isinstance(raw_entry_payload, dict)
                else json.loads(str(raw_entry_payload))
            )
            initial_protection = entry_payload.get("initial_protection")
            if not isinstance(initial_protection, dict):
                raise RuntimeError("filled Entry lost immutable initial protection contract")
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
            universal_lineage = None
            if binding is None:
                universal_lineage = load_universal_entry_lineage(
                    connection,
                    command_id=str(entry_id),
                    command_payload=entry_payload,
                )
                if (
                    str(entry_payload.get("source") or "") == "universal_entry"
                    and universal_lineage is None
                ):
                    raise RuntimeError("Universal Entry fill lost durable request lineage")
            if execution_rows and (binding is not None or universal_lineage is not None):
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
                exchange_order_ids = sorted({str(row[1]) for row in execution_rows})
                client_order_ids = sorted({str(row[2]) for row in execution_rows})
                execution_ids = [str(row[0]) for row in execution_rows]
                if binding is not None:
                    owner_bot = str(binding[2])
                    owner_strategy_id = str(binding[3])
                    owner_strategy_version = str(binding[4])
                    owner_signal_id = str(binding[1])
                    geometry_handoff_id = binding[0]
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
                            position_id, trade_id, owner_bot, owner_strategy_id,
                            owner_strategy_version, owner_signal_id, entry_id,
                            geometry_handoff_id, symbol, position_row[0],
                            actual_entry, Decimal(str(position_row[1])), first_fill_ms,
                            json.dumps(exchange_order_ids),
                            json.dumps(client_order_ids),
                            json.dumps(execution_ids),
                            f"BYBIT:UNIFIED:LINEAR:USDT:{symbol}:{position_idx}",
                            position_idx,
                        ),
                    )
                else:
                    assert universal_lineage is not None
                    persist_universal_strategy_position(
                        connection,
                        lineage=universal_lineage,
                        position_id=position_id,
                        trade_id=trade_id,
                        entry_command_id=str(entry_id),
                        symbol=str(symbol),
                        side=str(position_row[0]),
                        actual_avg_fill=actual_entry,
                        actual_qty=Decimal(str(position_row[1])),
                        fill_at=datetime.fromtimestamp(first_fill_ms / 1000, tz=UTC),
                        exchange_order_ids=exchange_order_ids,
                        client_order_ids=client_order_ids,
                        execution_ids=execution_ids,
                        position_idx=position_idx,
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
                (init_id,symbol,json.dumps({"entry_command_id":entry_id,"actual_entry":position_row[2],"actual_size":position_row[1],"initial_protection":initial_protection}),int(time.time()*1000)))
        connection.commit()
        # V36: BE/trailing/close decisions belong to Exit. This worker only executes commands.
        row=connection.execute("""SELECT command_id,command_type,symbol,payload_json FROM runtime.trade_commands
            WHERE state='queued' ORDER BY (left(command_id,4)='web-') DESC, requested_at_epoch_ms LIMIT 1""").fetchone()
        if not row:
            # SELECT polling starts an implicit transaction; close it before sleep.
            connection.commit()
            time.sleep(0.25)
            continue
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
                finalize_failed_entry_command_reservation(
                    connection,
                    command_id=command_id,
                    reason=f"ENTRY_BLOCKED:{readiness_reason}",
                    mutation_ambiguous=False,
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
        try:
            execute_command(connection, key, secret, row)
        except PreMutationSafetyBlock as exc:
            handle_pre_mutation_safety_block(connection, command_id, exc)
        except ExchangeMutationBarrier as exc:
            handle_exchange_mutation_barrier(
                connection, key, secret, command_id, exc
            )
        except Exception as exc:
            connection.rollback()
            reservation_state = finalize_failed_entry_command_reservation(
                connection,
                command_id=command_id,
                reason=f"{type(exc).__name__}:{exc}",
                mutation_ambiguous=False,
            )
            connection.execute(
                """UPDATE runtime.trade_commands
                   SET state='failed',finished_at_epoch_ms=%s,error=%s
                   WHERE command_id=%s""",
                (
                    int(time.time() * 1000),
                    f"{type(exc).__name__}: {exc}",
                    command_id,
                ),
            )
            connection.commit()
            if reservation_state == "RECONCILIATION_REQUIRED":
                handle_exchange_mutation_barrier(
                    connection,
                    key,
                    secret,
                    command_id,
                    ExchangeMutationBarrier(
                        f"post-ack Entry failure requires reconciliation: {exc}"
                    ),
                )


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
                  extract(epoch from fill_at)*1000,position_idx,entry_command_id,
                  entry_execution_request_id
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
            current_stop = Decimal(str(current.get("stopLoss") or 0))
            protection_confirmed = current_stop > 0
            connection.execute(
                """UPDATE runtime.position_ownership
                   SET state='OPEN',
                       close_link_status='OPEN',
                       initial_protection_confirmed_at=
                           CASE
                             WHEN %s AND initial_protection_confirmed_at IS NULL
                             THEN to_timestamp(%s/1000.0)
                             ELSE initial_protection_confirmed_at
                           END,
                       initial_protection_evidence=
                           CASE
                             WHEN %s
                             THEN %s::jsonb
                             ELSE initial_protection_evidence
                           END
                   WHERE position_id=%s""",
                (
                    protection_confirmed,
                    now,
                    protection_confirmed,
                    json.dumps(
                        {
                            "source": "BYBIT_POSITION_RECONCILIATION",
                            "symbol": symbol,
                            "position_idx": position_idx,
                            "stopLoss": current.get("stopLoss"),
                            "takeProfit": current.get("takeProfit"),
                            "observed_at_epoch_ms": now,
                        },
                        ensure_ascii=False,
                    ),
                    position_id,
                ),
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
            release_position_capital_reservation(
                connection,
                position_id=position_id,
                entry_execution_request_id=None if row[9] is None else str(row[9]),
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
        release_position_capital_reservation(
            connection,
            position_id=position_id,
            entry_execution_request_id=None if row[9] is None else str(row[9]),
        )


def collect_position_mode_states(
    connection: psycopg.Connection,
    key: str,
    secret: str,
    now_ms: int,
    *,
    force_refresh: bool = False,
) -> list[dict[str, object]]:
    """GET-only per-symbol mode observations for active Strategy universes."""
    with connection.transaction():
        rows = connection.execute(
            """SELECT DISTINCT jsonb_array_elements_text(ep.plan_json->'symbols') AS symbol
                 FROM strategy_entry.strategy_activations a
                 JOIN strategy_entry.entry_plans ep
                   ON ep.strategy_id=a.strategy_id
                  AND ep.strategy_version=a.strategy_version
                  AND ep.strategy_config_fingerprint=a.strategy_config_fingerprint
                WHERE a.enabled=true
                ORDER BY symbol"""
        ).fetchall()
    symbols = [str(row[0]) for row in rows if str(row[0]).strip()]
    if not symbols:
        return []
    refresh_seconds = int(os.environ.get("CRIPTA_POSITION_MODE_REFRESH_SECONDS", "30") or 30)
    observed_at = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
    if refresh_seconds > 0 and not force_refresh:
        cutoff = observed_at - timedelta(seconds=refresh_seconds)
        with connection.transaction():
            recent = {
                str(row[0])
                for row in connection.execute(
                    """SELECT instrument
                         FROM runtime.position_mode_states
                        WHERE account_ref='BYBIT:UNIFIED'
                          AND product_category='LINEAR'
                          AND observed_at >= %s
                          AND instrument = ANY(%s)
                        GROUP BY instrument""",
                    (cutoff, symbols),
                ).fetchall()
            }
        if recent == set(symbols):
            return []
        if not force_refresh:
            # Periodic account reconciliation must stay cheap. Refresh at most
            # one stale mode proof per cycle; 90s validity lets the five R1
            # symbols stay staggered without blocking the critical account read.
            symbols = [symbol for symbol in symbols if symbol not in recent][:1]
    freshness_seconds = int(os.environ.get("CRIPTA_POSITION_MODE_FRESHNESS_SECONDS", "0") or 0)
    result: list[dict[str, object]] = []
    for symbol in symbols:
        body, _ = api_get(
            "/v5/position/list",
            {"category": "linear", "symbol": symbol},
            key,
            secret,
        )
        if int(body.get("retCode", -1)) != 0:
            raise ExchangeReadUnavailable(
                f"position-mode probe rejected for {symbol}: "
                f"retCode={body.get('retCode')} retMsg={body.get('retMsg')}"
            )
        items = (body.get("result") or {}).get("list") or []
        indexes = sorted({int(item.get("positionIdx", -1)) for item in items})
        if indexes and set(indexes).issubset({0}):
            mode = "ONE_WAY"
            position_idx: int | None = 0
        elif set(indexes).intersection({1, 2}):
            mode = "HEDGE"
            position_idx = None
        else:
            mode = "UNKNOWN"
            position_idx = None
        state_ref = "pmode-" + hashlib.sha256(
            f"BYBIT|BYBIT:UNIFIED|LINEAR|{symbol}|{mode}|{indexes}|{now_ms}".encode()
        ).hexdigest()[:32]
        result.append(
            {
                "position_mode_state_ref": state_ref,
                "exchange": "BYBIT",
                "account_ref": "BYBIT:UNIFIED",
                "product_category": "LINEAR",
                "instrument": symbol,
                "position_mode": mode,
                "position_idx": position_idx,
                "observed_at": observed_at,
                "received_at": datetime.now(UTC),
                "fresh_until": observed_at
                + timedelta(seconds=max(0, freshness_seconds)),
                "provenance": {
                    "source": "BYBIT_PRIVATE_REST_POSITION_LIST",
                    "position_indexes": indexes,
                    "freshness_seconds": freshness_seconds,
                },
            }
        )
    return result


def persist_position_mode_states(
    connection: psycopg.Connection,
    states: list[dict[str, object]],
) -> None:
    for item in states:
        connection.execute(
            """INSERT INTO runtime.position_mode_states(
                   position_mode_state_ref,exchange,account_ref,product_category,
                   instrument,position_mode,position_idx,observed_at,received_at,
                   fresh_until,provenance
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT(position_mode_state_ref) DO NOTHING""",
            (
                item["position_mode_state_ref"],
                item["exchange"],
                item["account_ref"],
                item["product_category"],
                item["instrument"],
                item["position_mode"],
                item["position_idx"],
                item["observed_at"],
                item["received_at"],
                item["fresh_until"],
                json.dumps(item["provenance"], ensure_ascii=False),
            ),
        )


def _account_generation_id(started_ms: int, reason: str) -> str:
    return "acctgen-" + hashlib.sha256(
        f"{BOT_INSTANCE_ID}|{PROCESS_STARTED_AT_MS}|{started_ms}|{reason}".encode()
    ).hexdigest()[:32]


def _begin_account_state_generation(
    connection: psycopg.Connection,
    *,
    generation_id: str,
    started_ms: int,
    reason: str,
) -> None:
    with connection.transaction():
        connection.execute(
            """INSERT INTO runtime.account_state_generations(
                   generation_id,account_ref,reason,state,started_at,position_mode_refs,
                   error,created_at,updated_at)
               VALUES(%s,%s,%s,'COLLECTING',to_timestamp(%s / 1000.0),'{}'::jsonb,
                      '',clock_timestamp(),clock_timestamp())""",
            (generation_id, ACCOUNT_REF, reason, started_ms),
        )


def reconcile(
    connection: psycopg.Connection, key: str, secret: str, reason: str
) -> tuple[int, int]:
    started = int(time.time() * 1000)
    generation_id = _account_generation_id(started, reason)
    _begin_account_state_generation(
        connection,
        generation_id=generation_id,
        started_ms=started,
        reason=reason,
    )
    try:
        # Inventory first, exact position-mode proofs next, wallet last. The
        # generation is admitted only as one COMPLETE unit; component ages are
        # never compared independently for real Entry.
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
        rejected = [
            (name, payload)
            for name, payload in (("positions", positions), ("orders", orders))
            if int(payload.get("retCode", -1)) != 0
        ]
        if rejected:
            detail = "; ".join(
                f"{name}:retCode={payload.get('retCode')} retMsg={payload.get('retMsg')}"
                for name, payload in rejected
            )
            raise ExchangeReadUnavailable(f"reconciliation read rejected: {detail}")

        position_list = [
            p for p in ((positions.get("result") or {}).get("list") or [])
            if float(p.get("size") or 0) != 0
        ]
        order_list = (orders.get("result") or {}).get("list") or []
        active_order_list = [
            item
            for item in order_list
            if str(item.get("orderStatus") or "") in {"New", "PartiallyFilled", "Untriggered"}
            and str(item.get("reduceOnly") or "false").lower() != "true"
            and str(item.get("closeOnTrigger") or "false").lower() != "true"
        ]

        mode_observed_ms = int(time.time() * 1000)
        position_mode_states = collect_position_mode_states(
            connection,
            key,
            secret,
            mode_observed_ms,
            force_refresh=True,
        )

        fetch_history = reason != "periodic"
        if not fetch_history:
            current_keys = {
                (str(item.get("symbol") or ""), int(item.get("positionIdx") or 0))
                for item in position_list
            }
            with connection.transaction():
                owned_keys = {
                    (str(row[0]), int(row[1] or 0))
                    for row in connection.execute(
                        """SELECT symbol,position_idx FROM runtime.position_ownership
                           WHERE state='OPEN' AND close_link_status='OPEN'"""
                    ).fetchall()
                }
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
            if int(order_history_response.get("retCode", -1)) != 0:
                raise ExchangeReadUnavailable(
                    "order-history reconciliation rejected: "
                    f"retCode={order_history_response.get('retCode')} "
                    f"retMsg={order_history_response.get('retMsg')}"
                )
            order_history = (order_history_response.get("result") or {}).get("list") or []

        wallet, _ = api_get(
            "/v5/account/wallet-balance",
            {"accountType": "UNIFIED"},
            key,
            secret,
        )
        if int(wallet.get("retCode", -1)) != 0:
            raise ExchangeReadUnavailable(
                "reconciliation read rejected: "
                f"wallet:retCode={wallet.get('retCode')} retMsg={wallet.get('retMsg')}"
            )
        account = ((wallet.get("result") or {}).get("list") or [{}])[0]
        if str(account.get("accountType") or "") != "UNIFIED":
            raise ExchangeReadUnavailable("reconciliation wallet accountType is not UNIFIED")

        now = int(time.time() * 1000)
        mode_refs = {
            str(item["instrument"]): str(item["position_mode_state_ref"])
            for item in position_mode_states
        }
        available = account_available_usdt(account)
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (ACCOUNT_REF,),
            )
            connection.execute("DELETE FROM runtime.hot_positions")
            connection.execute("DELETE FROM runtime.hot_orders")
            for p in position_list:
                upsert_position(connection, p, now)
            for o in order_list:
                upsert_order(connection, o, now)
            persist_position_mode_states(connection, position_mode_states)
            for item in order_history:
                upsert_exchange_order_history(connection, item, now)
            upsert_wallet(connection, account, now)
            reconcile_position_ownership(connection, position_list, now, order_history)
            connection.execute(
                """UPDATE runtime.account_state_generations
                      SET state='COMPLETE',
                          completed_at=clock_timestamp(),
                          account_type=%s,
                          total_equity=%s,
                          wallet_balance=%s,
                          available_balance=%s,
                          positions_count=%s,
                          active_orders_count=%s,
                          position_mode_refs=%s::jsonb,
                          wallet_payload=%s::jsonb,
                          error='',
                          updated_at=clock_timestamp()
                    WHERE generation_id=%s AND state='COLLECTING'""",
                (
                    str(account.get("accountType") or ""),
                    str(account.get("totalEquity") or "0"),
                    str(account.get("totalWalletBalance") or "0"),
                    str(available),
                    len(position_list),
                    len(active_order_list),
                    json.dumps(mode_refs, ensure_ascii=False),
                    json.dumps(account, ensure_ascii=False),
                    generation_id,
                ),
            )
            connection.execute(
                """INSERT INTO runtime.reconciliation_runs(
                    started_at_epoch_ms,finished_at_epoch_ms,reason,ok,positions,orders,error)
                    VALUES(%s,%s,%s,1,%s,%s,'')""",
                (started, now, reason, len(position_list), len(order_list)),
            )
        return len(position_list), len(order_list)
    except Exception as exc:
        connection.rollback()
        finished = int(time.time() * 1000)
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (ACCOUNT_REF,),
            )
            connection.execute(
                """UPDATE runtime.account_state_generations
                      SET state='FAILED',completed_at=clock_timestamp(),
                          error=%s,updated_at=clock_timestamp()
                    WHERE generation_id=%s AND state='COLLECTING'""",
                (f"{type(exc).__name__}: {exc}", generation_id),
            )
            connection.execute(
                """INSERT INTO runtime.reconciliation_runs(
                    started_at_epoch_ms,finished_at_epoch_ms,reason,ok,positions,orders,error)
                    VALUES(%s,%s,%s,0,0,0,%s)""",
                (started, finished, reason, f"{type(exc).__name__}: {exc}"),
            )
        if isinstance(exc, ExchangeReadUnavailable):
            raise
        if isinstance(
            exc,
            (TimeoutError, urllib.error.URLError, OSError, ValueError),
        ):
            raise ExchangeReadUnavailable(
                f"reconciliation read unavailable: {type(exc).__name__}:{exc}"
            ) from exc
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
            order_status = str(item.get("orderStatus") or "")
            if order_status in {"Filled", "Cancelled", "Rejected", "Deactivated"}:
                # Preserve the exact terminal Exchange event before removing it
                # from the current-order projection. This lets Universal Entry
                # deterministically release a no-fill reservation/slot even
                # when Bybit cancels PostOnly itself rather than our strategy
                # cancellation worker initiating the cancel.
                upsert_exchange_order_history(connection, item, now)
                connection.execute(
                    "DELETE FROM runtime.hot_orders WHERE order_id=%s",
                    (item.get("orderId", ""),),
                )
                if order_status in {"Cancelled", "Rejected", "Deactivated"}:
                    order_link_id = str(item.get("orderLinkId") or "")
                    order_id = str(item.get("orderId") or "")
                    if order_link_id and order_id:
                        raw_cause = str(
                            item.get("rejectReason")
                            or item.get("cancelType")
                            or "Cancelled"
                        ).strip()
                        resolve_cancelled_entry_reservation_after_reconcile(
                            connection,
                            command_id=order_link_id,
                            exchange_order_id=order_id,
                            cancel_reason=f"BYBIT:{raw_cause}",
                        )
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
    connection = db("cripta-private-ws")
    recovering = False
    restore_gate_after_reconnect = False
    reconnect_started_ms = 0
    while running:
        try:
            hot_positions, hot_orders = reconcile(
                connection,
                key,
                secret,
                "startup" if reconnects == 0 else "reconnect",
            )
            ws = websocket.create_connection(PRIVATE_URL, timeout=10, enable_multithread=False)
            ws.settimeout(1)
            auth(ws, key, secret)
            ws.send(json.dumps({"op": "subscribe", "args": ["order.linear", "execution.linear", "position.linear", "wallet"]}, separators=(",", ":")))
            connection_event(connection, "private", "connected", reconnects=reconnects)
            atomic_status("private", {"state": "connected", "connected_at_epoch": int(time.time()), "reconnects": reconnects, "last_message_epoch": None, "hot_positions": hot_positions, "hot_orders": hot_orders})
            if recovering:
                restored = False
                if restore_gate_after_reconnect:
                    restored = restore_r1_gate_after_verified_reconnect(
                        connection,
                        reconnect_started_ms=reconnect_started_ms,
                        hot_positions=hot_positions,
                        hot_orders=hot_orders,
                    )
                connection_event(
                    connection,
                    "private",
                    "reconnect_verified",
                    reconnects=reconnects,
                    gate_restored=restored,
                    gate_was_open=restore_gate_after_reconnect,
                )
                recovering = False
                restore_gate_after_reconnect = False
                reconnect_started_ms = 0
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
                    reconcile_started_monotonic = time.monotonic()
                    try:
                        hot_positions, hot_orders = reconcile(
                            connection, key, secret, "periodic"
                        )
                    except ExchangeReadUnavailable as exc:
                        previous = status.get("private", {})
                        connection_event(
                            connection,
                            "private",
                            "reconciliation_degraded",
                            error=f"{type(exc).__name__}: {exc}",
                            reconnects=reconnects,
                        )
                        atomic_status(
                            "private",
                            {
                                "state": "connected",
                                "connected_at_epoch": previous.get("connected_at_epoch"),
                                "reconnects": reconnects,
                                "last_message_epoch": previous.get("last_message_epoch"),
                                "hot_positions": previous.get("hot_positions", 0),
                                "hot_orders": previous.get("hot_orders", 0),
                                "reconciliation_error": f"{type(exc).__name__}: {exc}",
                            },
                        )
                        # Keep the healthy private WS connected. Entry admission
                        # independently requires a <=15s successful reconciliation.
                        next_reconcile = time.monotonic() + 1.0
                        continue
                    previous = status.get("private", {})
                    atomic_status("private", {"state": "connected", "connected_at_epoch": previous.get("connected_at_epoch"), "reconnects": reconnects, "last_message_epoch": previous.get("last_message_epoch"), "hot_positions": hot_positions, "hot_orders": hot_orders})
                    cadence = 1.0 if hot_positions else 5.0
                    next_reconcile = max(
                        reconcile_started_monotonic + cadence,
                        time.monotonic() + 0.25,
                    )
        except Exception as exc:
            if not recovering:
                try:
                    restore_gate_after_reconnect = mainnet_gate_enabled(connection)
                except Exception:
                    restore_gate_after_reconnect = False
                    connection.rollback()
                reconnect_started_ms = int(time.time() * 1000)
                recovering = True
            disarm_new_entries(connection, PRIVATE_RECONNECT_GATE_REASON)
            reconnects += 1
            connection_event(connection, "private", "reconnecting", error=f"{type(exc).__name__}: {exc}", reconnects=reconnects)
            atomic_status("private", {"state": "reconnecting", "reconnects": reconnects, "error": f"{type(exc).__name__}: {exc}"})
            time.sleep(min(30, reconnects))


def trade_loop(key: str, secret: str) -> None:
    reconnects = 0
    connection = db("cripta-private-trade")
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
    bootstrap = db("cripta-private-bootstrap")
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
