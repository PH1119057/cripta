"""GET-only independent Bybit SUB safety facts, never exchange mutation.

Caller separately proves the same mounted connector's UID via protection_probe.
No private UID, auth header, credential, or raw payload is persisted here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from .transport import LifecycleAccountReadTransport


@dataclass(frozen=True)
class AccountSafetyFacts:
    total_equity: Decimal
    available_balance: Decimal
    wallet_balance: Decimal
    open_orders: int
    source: str = "SIGNED_BYBIT_ACCOUNT_GET"


def _positive_number(raw: object, label: str) -> Decimal:
    try:
        number = Decimal(str(raw))
    except (ValueError, ArithmeticError) as exc:
        raise ValueError("ACCOUNT_SAFETY_NUMERIC_INVALID:" + label) from exc
    if not number.is_finite() or number < 0:
        raise ValueError("ACCOUNT_SAFETY_NUMERIC_INVALID:" + label)
    return number


def validate_wallet(wallet: Mapping[str, object]) -> tuple[Decimal, Decimal, Decimal]:
    if wallet.get("retCode") != 0:
        raise RuntimeError("ACCOUNT_SAFETY_WALLET_GET_REJECTED")
    result = wallet.get("result")
    if not isinstance(result, Mapping) or not isinstance(result.get("list"), list):
        raise RuntimeError("ACCOUNT_SAFETY_WALLET_INCOMPLETE")
    entries = result["list"]
    if len(entries) != 1 or not isinstance(entries[0], Mapping):
        raise RuntimeError("ACCOUNT_SAFETY_WALLET_SCOPE_AMBIGUOUS")
    row = entries[0]
    if row.get("accountType") != "UNIFIED":
        raise RuntimeError("ACCOUNT_SAFETY_WALLET_TYPE_MISMATCH")

    account_wide = (
        row.get("totalEquity"),
        row.get("totalAvailableBalance"),
        row.get("totalWalletBalance"),
    )
    present = tuple(value not in (None, "") for value in account_wide)
    if all(present):
        return (
            _positive_number(account_wide[0], "totalEquity"),
            _positive_number(account_wide[1], "totalAvailableBalance"),
            _positive_number(account_wide[2], "totalWalletBalance"),
        )
    if any(present):
        raise RuntimeError("ACCOUNT_SAFETY_WALLET_SCOPE_MIXED")

    coins = row.get("coin")
    if not isinstance(coins, list):
        raise RuntimeError("ACCOUNT_SAFETY_ISOLATED_USDT_MISSING")
    usdt_rows = [
        item for item in coins
        if isinstance(item, Mapping) and item.get("coin") == "USDT"
    ]
    if len(usdt_rows) != 1:
        raise RuntimeError("ACCOUNT_SAFETY_ISOLATED_USDT_AMBIGUOUS")
    usdt = usdt_rows[0]
    equity = _positive_number(usdt.get("equity"), "coin.USDT.equity")
    wallet_balance = _positive_number(
        usdt.get("walletBalance"), "coin.USDT.walletBalance"
    )
    reserved = sum(
        (
            _positive_number(usdt.get(name) or "0", "coin.USDT." + name)
            for name in ("totalOrderIM", "totalPositionIM", "locked")
        ),
        Decimal("0"),
    )
    available = max(Decimal("0"), wallet_balance - reserved)
    return equity, available, wallet_balance


async def read_account_safety(reader: LifecycleAccountReadTransport) -> AccountSafetyFacts:
    wallet = await reader.get(
        "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, authenticated=True,
    )
    equity, available, balance = validate_wallet(wallet)
    seen_cursors: set[str] = set()
    seen_orders: set[str] = set()
    cursor = ""
    for _ in range(20):
        params = {"category": "linear", "settleCoin": "USDT", "openOnly": "0", "limit": "50"}
        if cursor:
            params["cursor"] = cursor
        reply = await reader.get("/v5/order/realtime", params, authenticated=True)
        if reply.get("retCode") != 0:
            raise RuntimeError("ACCOUNT_SAFETY_OPEN_ORDERS_GET_REJECTED")
        result = reply.get("result")
        if not isinstance(result, Mapping) or not isinstance(result.get("list"), list):
            raise RuntimeError("ACCOUNT_SAFETY_OPEN_ORDERS_INCOMPLETE")
        rows = result["list"]
        for row in rows:
            if not isinstance(row, Mapping) or not row.get("orderId"):
                raise RuntimeError("ACCOUNT_SAFETY_ORDER_PROVENANCE_MISSING")
            order_id = str(row["orderId"])
            if order_id in seen_orders:
                raise RuntimeError("ACCOUNT_SAFETY_DUPLICATE_OPEN_ORDER")
            seen_orders.add(order_id)
        following = str(result.get("nextPageCursor") or "")
        if not following:
            return AccountSafetyFacts(equity, available, balance, len(seen_orders))
        if following in seen_cursors:
            raise RuntimeError("ACCOUNT_SAFETY_PAGINATION_REPEATED")
        seen_cursors.add(following)
        cursor = following
    raise RuntimeError("ACCOUNT_SAFETY_OPEN_ORDER_PAGINATION_INCOMPLETE")
