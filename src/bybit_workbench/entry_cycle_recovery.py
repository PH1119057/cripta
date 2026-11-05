"""Exact current-cycle proof. Candidate discovery never grants ownership.

The credential-local reader supplies complete, time-bounded signed history and
stable before/after position inventories. This module sends no Exchange POST,
changes no Strategy policy and never infers a cycle from matching quantities.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class RecoveryCycleUnproven(RuntimeError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise RecoveryCycleUnproven("F01_" + reason)


def _number(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
        _require(result.is_finite(), "NONFINITE_AMOUNT")
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise RecoveryCycleUnproven("F01_INVALID_AMOUNT") from None


def position_fingerprint(position: Mapping[str, Any]) -> str:
    """Version plus physical identity, not mark/PnL fluctuations or defaults."""
    required = ("symbol", "positionIdx", "side", "size", "avgPrice", "seq", "updatedTime")
    _require(all(k in position for k in required), "POSITION_VERSION_MISSING")
    _require(int(str(position["seq"])) >= 0, "POSITION_SEQUENCE_UNPROVEN")
    data = {k: str(position[k]) for k in required}
    for k in ("size", "avgPrice"):
        data[k] = str(_number(position[k]).normalize())
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class RecoveryInventory:
    account: str
    connector: str
    start_ms: int
    end_ms: int
    observed_ms: int
    positions: tuple[Mapping[str, Any], ...]
    executions: tuple[Mapping[str, Any], ...]
    orders: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class CurrentEntryCycle:
    entry_command_id: str
    position: Mapping[str, Any]
    filled_qty: Decimal
    remaining_qty: Decimal
    entry_avg_fill: Decimal
    first_fill_ms: int
    entry_execution_ids: tuple[str, ...]
    entry_order_ids: tuple[str, ...]
    close_execution_ids: tuple[str, ...]
    fingerprint: str
    inventory: RecoveryInventory

    def certificate(self) -> dict[str, Any]:
        inventory = self.inventory
        return {
            "schema": "EXACT_CURRENT_ENTRY_CYCLE_V1",
            "account": inventory.account, "connector": inventory.connector,
            "entry_command_id": self.entry_command_id,
            "filled_qty": str(self.filled_qty), "remaining_qty": str(self.remaining_qty),
            "entry_execution_ids": list(self.entry_execution_ids),
            "entry_order_ids": list(self.entry_order_ids),
            "close_execution_ids": list(self.close_execution_ids),
            "position_fingerprint": self.fingerprint,
            "history_start_ms": inventory.start_ms, "history_end_ms": inventory.end_ms,
            "observed_ms": inventory.observed_ms,
        }


def prove_current_entry_cycle(
    *, inventory: RecoveryInventory, account: str, connector: str,
    entry_command_id: str, symbol: str,
    local_entry_executions: Sequence[Mapping[str, Any]],
    position: Mapping[str, Any], now_ms: int,
) -> CurrentEntryCycle:
    """Prove one uninterrupted Entry cycle, including partial fills/reductions.

    A foreign opening, zero crossing/reopen, missing/contradictory execution or
    changed current snapshot rejects the candidate even if side/size/price match.
    """
    _require(inventory.account == account and inventory.connector == connector,
             "ACCOUNT_CONNECTOR_MISMATCH")
    _require(0 <= now_ms - inventory.observed_ms <= 15_000, "INVENTORY_STALE")
    _require(0 <= inventory.observed_ms - inventory.end_ms <= 15_000,
             "HISTORY_SNAPSHOT_WINDOW_EXPIRED")
    _require(bool(local_entry_executions), "ENTRY_EXECUTIONS_MISSING")
    fingerprint = position_fingerprint(position)
    matches = [p for p in inventory.positions if p.get("symbol") == symbol
               and str(p.get("positionIdx")) == str(position.get("positionIdx"))]
    _require(len(matches) == 1 and position_fingerprint(matches[0]) == fingerprint,
             "CURRENT_POSITION_CHANGED")
    current = matches[0]
    side, idx = str(current["side"]), int(str(current["positionIdx"]))
    _require(side in {"Buy", "Sell"}, "POSITION_SIDE_INVALID")
    expected = {str(e["exec_id"]): e for e in local_entry_executions}
    _require(len(expected) == len(local_entry_executions), "DUPLICATE_LOCAL_EXECUTION")
    first_ms = min(int(str(e["exec_time_ms"])) for e in local_entry_executions)
    _require(0 < inventory.start_ms <= first_ms <= inventory.end_ms,
             "HISTORY_DOES_NOT_COVER_ENTRY")
    signed: dict[str, Mapping[str, Any]] = {}
    for native_execution in inventory.executions:
        if (native_execution.get("symbol") != symbol
                or native_execution.get("execType") != "Trade"):
            continue
        name = str(native_execution.get("execId") or "")
        _require(bool(name), "EXECUTION_ID_MISSING")
        _require(name not in signed or dict(signed[name]) == dict(native_execution),
                 "EXECUTION_CONFLICT")
        signed[name] = native_execution
    for name, local in expected.items():
        row = signed.get(name)
        _require(row is not None, "LOCAL_EXECUTION_NOT_IN_SIGNED_HISTORY")
        assert row is not None
        for local_key, native in (("order_id", "orderId"), ("order_link_id", "orderLinkId"),
                                  ("side", "side"), ("exec_time_ms", "execTime")):
            _require(str(local[local_key]) == str(row.get(native)), "EXECUTION_IDENTITY_DRIFT")
        for local_key, native in (("exec_qty", "execQty"), ("exec_price", "execPrice")):
            _require(_number(local[local_key]) == _number(row.get(native)),
                     "EXECUTION_AMOUNT_DRIFT")
    entry_rows = [r for r in signed.values() if r.get("orderLinkId") == entry_command_id[:36]]
    _require({str(r["execId"]) for r in entry_rows} == set(expected),
             "ENTRY_EXECUTION_SET_INCOMPLETE")
    order_ids = {str(r.get("orderId") or "") for r in entry_rows}
    _require(len(order_ids) == 1 and "" not in order_ids, "ENTRY_ORDER_AMBIGUOUS")
    order_id = next(iter(order_ids))
    entry_order = inventory.orders.get(order_id)
    _require(entry_order is not None, "ENTRY_ORDER_UNPROVEN")
    assert entry_order is not None
    _require(entry_order.get("orderLinkId") == entry_command_id[:36]
             and entry_order.get("symbol") == symbol and entry_order.get("side") == side
             and int(str(entry_order.get("positionIdx"))) == idx, "ENTRY_ORDER_SCOPE_MISMATCH")
    total = sum((_number(r.get("execQty")) for r in entry_rows), Decimal(0))
    _require(total > 0 and total == _number(entry_order.get("cumExecQty")),
             "ENTRY_CUMULATIVE_FILL_MISMATCH")
    remaining, opened = Decimal(0), Decimal(0)
    close_ids: list[str] = []
    rows = sorted(signed.values(), key=lambda r: (int(str(r["execTime"])),
                  int(str(r.get("seq") or 0)), str(r["execId"])))
    # Opposite-side events at one timestamp/sequence have no proven ordering.
    groups: dict[tuple[int, int], set[str]] = {}
    for row in rows:
        at = int(str(row["execTime"]))
        if at < first_ms:
            continue
        _require(at <= inventory.end_ms, "EXECUTION_OUTSIDE_CERTIFIED_WINDOW")
        order = inventory.orders.get(str(row.get("orderId") or ""))
        _require(order is not None, "EXECUTION_ORDER_MISSING")
        assert order is not None
        _require(order.get("symbol") == symbol and order.get("side") == row.get("side"),
                 "EXECUTION_ORDER_SCOPE_MISMATCH")
        if int(str(order.get("positionIdx"))) != idx:
            continue
        group = (at, int(str(row.get("seq") or 0)))
        groups.setdefault(group, set()).add(str(row.get("side")))
        _require(len(groups[group]) == 1, "EXECUTION_ORDERING_AMBIGUOUS")
        qty, closed = _number(row.get("execQty")), _number(row.get("closedSize"))
        _require(qty > 0 and 0 <= closed <= qty, "EXECUTION_QUANTITY_INVALID")
        if str(row.get("orderId")) == order_id:
            _require(row.get("side") == side and closed == 0, "ENTRY_REVERSES_POSITION")
            opened += qty
            remaining += qty
        else:
            _require(row.get("side") != side and closed == qty and 0 < qty <= remaining,
                     "FOREIGN_OPEN_OR_REVERSE")
            remaining -= qty
            close_ids.append(str(row["execId"]))
            _require(remaining > 0, "ENTRY_CYCLE_ALREADY_CLOSED")
    _require(opened == total and remaining == _number(current["size"]) and remaining > 0,
             "CURRENT_REMAINDER_MISMATCH")
    avg = _number(entry_order.get("avgPrice"))
    _require(avg > 0 and avg == _number(current["avgPrice"]), "CURRENT_ENTRY_PRICE_MISMATCH")
    return CurrentEntryCycle(entry_command_id, current, total, remaining, avg, first_ms,
                             tuple(str(r["execId"]) for r in entry_rows),
                             (order_id,), tuple(close_ids), fingerprint, inventory)


def load_current_entry_cycle(
    connection: Any, *, account: str, connector: str, entry_command_id: str,
    symbol: str, inventory_provider: Callable[[int], RecoveryInventory], now_ms: Callable[[], int],
) -> CurrentEntryCycle:
    """Read exact local refs, then validate them against independent signed facts."""
    closed = connection.execute(
        "SELECT 1 FROM runtime.position_ownership WHERE entry_command_id=%s "
        "AND trading_account_id=%s AND state='CLOSED'", (entry_command_id, account),
    ).fetchone()
    _require(closed is None, "ENTRY_CYCLE_ALREADY_CLOSED")
    rows = connection.execute(
        """SELECT exec_id,order_id,order_link_id,exec_time_ms,side,exec_qty,exec_price
             FROM runtime.executions
            WHERE order_link_id=%s AND trading_account_id=%s AND identity_status='PROVEN'
            ORDER BY exec_time_ms,exec_id""", (entry_command_id, account),
    ).fetchall()
    _require(bool(rows), "ENTRY_EXECUTIONS_MISSING")
    fields = ("exec_id", "order_id", "order_link_id", "exec_time_ms",
              "side", "exec_qty", "exec_price")
    local = [dict(zip(fields, row, strict=True)) for row in rows]
    # This is a distinct, immutable exact CLOSED representation; no guessed
    # position_ownership row is created merely to make candidate filtering work.
    for order_id, execution_ids in connection.execute(
        "SELECT entry_order_id,entry_execution_ids FROM runtime.recovered_closed_entry_cycles "
        "WHERE entry_order_id=ANY(%s)", (sorted({str(r["order_id"]) for r in local}),),
    ).fetchall():
        ids = json.loads(execution_ids) if isinstance(execution_ids, str) else execution_ids
        _require(not any(str(r["order_id"]) == str(order_id) and r["exec_id"] in ids
                         for r in local), "ENTRY_CYCLE_ALREADY_CLOSED")
    first_ms = min(int(str(r["exec_time_ms"])) for r in local)
    inventory = inventory_provider(first_ms)
    entry_orders = {str(r.get("orderId")) for r in inventory.executions
                    if r.get("orderLinkId") == entry_command_id[:36]
                    and r.get("symbol") == symbol and r.get("execType") == "Trade"}
    _require(len(entry_orders) == 1, "ENTRY_ORDER_AMBIGUOUS")
    order = inventory.orders.get(next(iter(entry_orders)))
    _require(order is not None, "ENTRY_ORDER_UNPROVEN")
    assert order is not None
    current = [p for p in inventory.positions if p.get("symbol") == symbol
               and str(p.get("positionIdx")) == str(order.get("positionIdx"))]
    _require(len(current) == 1, "CURRENT_POSITION_MISSING_OR_AMBIGUOUS")
    return prove_current_entry_cycle(
        inventory=inventory, account=account, connector=connector,
        entry_command_id=entry_command_id, symbol=symbol, local_entry_executions=local,
        position=current[0], now_ms=now_ms(),
    )


def project_cycle_executions(connection: Any, cycle: CurrentEntryCycle) -> None:
    """Idempotently retain exact signed Entry/reduction facts, never rewrite them.

    Partial reductions must survive the next restart/full close; a derived
    remainder certificate alone is not sufficient Exchange-to-DB projection.
    Caller owns the account transaction/lock and validates Strategy lineage.
    """
    ids = set(cycle.entry_execution_ids) | set(cycle.close_execution_ids)
    rows = {str(e["execId"]): e for e in cycle.inventory.executions
            if str(e.get("execId")) in ids}
    existing = connection.execute(
        """SELECT exec_id,order_id,order_link_id,side,exec_qty,exec_price,exec_time_ms
             FROM runtime.executions WHERE trading_account_id=%s AND exec_id=ANY(%s)""",
        (cycle.inventory.account, sorted(ids)),
    ).fetchall()
    for stored in existing:
        native = rows[str(stored[0])]
        expected = (str(native["orderId"]), str(native["orderLinkId"]), str(native["side"]),
                    _number(native["execQty"]), _number(native["execPrice"]),
                    int(str(native["execTime"])))
        actual = (str(stored[1]), str(stored[2]), str(stored[3]), _number(stored[4]),
                  _number(stored[5]), int(stored[6]))
        _require(actual == expected, "LOCAL_CYCLE_EXECUTION_CONFLICT")
    for row in rows.values():
        _require(_number(row.get("execPrice")) > 0 and "execFee" in row,
                 "SIGNED_EXECUTION_ECONOMICS_INCOMPLETE")
        _number(row["execFee"])
    for row in rows.values():
        connection.execute(
            """INSERT INTO runtime.executions(
                 exec_id,order_id,order_link_id,symbol,side,exec_qty,exec_price,exec_fee,
                 exec_time_ms,received_at_epoch_ms,payload_json,
                 trading_account_id,account_connector_id,identity_status)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PROVEN')
               ON CONFLICT(trading_account_id,exec_id) DO NOTHING""",
            (row["execId"], row["orderId"], row["orderLinkId"], row["symbol"], row["side"],
             str(row["execQty"]), str(row["execPrice"]), str(row["execFee"]),
             int(str(row["execTime"])), cycle.inventory.observed_ms, json.dumps(dict(row)),
             cycle.inventory.account, cycle.inventory.connector),
        )


class SignedRecoveryInventoryReader:
    """Account-local signed GET adapter. Complete windows, stable inventory, no POST."""

    def __init__(self, signed_get: Callable[..., Mapping[str, Any]], *,
                 account: str, connector: str, clock_ms: Callable[[], int],
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.get = signed_get
        self.account, self.connector = account, connector
        self.clock_ms, self.monotonic = clock_ms, monotonic

    def read(self, start_ms: int) -> RecoveryInventory:
        deadline = self.monotonic() + 30

        def pages(endpoint: str, params: dict[str, str]) -> list[Mapping[str, Any]]:
            result: list[Mapping[str, Any]] = []
            seen: set[str] = set()
            for _ in range(100):
                _require(self.monotonic() < deadline, "HISTORY_READ_BUDGET_EXPIRED")
                try:
                    reply = self.get(endpoint, dict(params))
                except Exception:
                    raise RecoveryCycleUnproven("F01_SIGNED_READ_UNAVAILABLE") from None
                _require(type(reply.get("retCode")) is int and reply["retCode"] == 0,
                         "SIGNED_HISTORY_REJECTED")
                data = reply.get("result")
                _require(isinstance(data, Mapping) and isinstance(data.get("list"), list),
                         "HISTORY_PAGE_INVALID")
                assert isinstance(data, Mapping)
                items = data["list"]
                _require(all(isinstance(r, Mapping) for r in items), "HISTORY_ROW_INVALID")
                result.extend(items)
                cursor = data.get("nextPageCursor", "")
                _require(isinstance(cursor, str), "HISTORY_CURSOR_INVALID")
                if not cursor:
                    return result
                _require(cursor not in seen, "HISTORY_CURSOR_REPEATED")
                seen.add(cursor)
                params["cursor"] = cursor
            raise RecoveryCycleUnproven("F01_HISTORY_PAGE_BUDGET_EXPIRED")

        pos_params = {"category": "linear", "settleCoin": "USDT", "limit": "200"}
        before = pages("/v5/position/list", dict(pos_params))
        before = [p for p in before if _number(p.get("size")) > 0]
        end_ms = self.clock_ms()
        _require(0 < start_ms <= end_ms, "HISTORY_WINDOW_INVALID")
        executions: list[Mapping[str, Any]] = []
        orders: dict[str, Mapping[str, Any]] = {}
        week = 7 * 24 * 3600 * 1000
        _require(end_ms - start_ms <= 2 * 366 * 24 * 3600 * 1000, "HISTORY_OUTSIDE_RETENTION")
        for lo in range(start_ms, end_ms + 1, week):
            params = {"category": "linear", "settleCoin": "USDT",
                      "startTime": str(lo), "endTime": str(min(end_ms, lo + week - 1))}
            executions.extend(pages("/v5/execution/list", {**params, "limit": "100"}))
            for order in pages("/v5/order/history", {**params, "limit": "50"}):
                orders[str(order.get("orderId") or "")] = order
        # A resting Entry can have been created before its first fill/window.
        for order_id in {str(r.get("orderId") or "") for r in executions
                         if r.get("execType") == "Trade"} - set(orders):
            rows = pages("/v5/order/history", {"category": "linear", "orderId": order_id})
            if not rows:
                # An actively partially-filled order need not be in terminal
                # history yet. Its exact authenticated live order is valid
                # evidence; symbol-only or guessed order matches are not.
                rows = pages("/v5/order/realtime", {"category": "linear", "orderId": order_id,
                                                   "openOnly": "0"})
            _require(len(rows) == 1 and rows[0].get("orderId") == order_id,
                     "EXACT_ORDER_HISTORY_MISSING")
            orders[order_id] = rows[0]
        after = pages("/v5/position/list", dict(pos_params))
        after = [p for p in after if _number(p.get("size")) > 0]
        _require(sorted(map(position_fingerprint, before)) ==
                 sorted(map(position_fingerprint, after)),
                 "POSITION_CHANGED_DURING_HISTORY")
        observed = self.clock_ms()
        _require(observed - end_ms <= 15_000, "HISTORY_SNAPSHOT_WINDOW_EXPIRED")
        _require(all(int(str(p["updatedTime"])) <= end_ms for p in after),
                 "POSITION_OVERTAKES_HISTORY")
        return RecoveryInventory(self.account, self.connector, start_ms, end_ms, observed,
                                 tuple(after), tuple(executions), orders)
