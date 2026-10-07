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
MUTATION_CLOCK_MAX_ABS_OFFSET_MS = 500.0
PRIVATE_RECONNECT_GATE_REASON = "private WS reconnect: verification pending"
PRIVATE_RECONNECT_RECOVERED_REASON = (
    "private WS reconnect recovered: verified R1 arm restored"
)


class ExchangeMutationBarrier(RuntimeError):
    """Mutation outcome or immediate post-mutation truth is uncertain."""


class UnsafeBybitClock(ExchangeMutationBarrier):
    """Local/Bybit clock evidence is outside the mutation safety limit."""


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
    for attempt in range(2):
        timestamp_ms = max(int(time.time() * 1000), last_timestamp_ms + 1)
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
    elif kind in {"break_even", "current_stop", "initial_pr