"""Operator-only closed-trade projection. Never writes Strategy metrics or ownership."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal


def _account_scope(status: object, account: object) -> tuple[str, str | None]:
    """DB provenance, never an inferred TradingAccount from price/provider IDs."""
    normalized = str(status or "LEGACY_UNKNOWN")
    account_id = None if account is None else str(account)
    if normalized == "PROVEN":
        if not account_id:
            raise ValueError("PROVEN closed trade requires trading_account_id")
        return normalized, account_id
    if normalized == "LEGACY_UNKNOWN":
        if account_id is not None:
            raise ValueError("LEGACY_UNKNOWN closed trade cannot claim an account")
        return normalized, None
    raise ValueError("unknown closed trade identity_status: " + normalized)


def _ledger_scope(item: Mapping[str, object]) -> tuple[str, str | None]:
    if item.get("provenance") == "RECOVERED_UNOWNED":
        if item.get("trading_account_id") is not None:
            raise ValueError("RECOVERED_UNOWNED cannot claim a TradingAccount")
        return "RECOVERED_UNOWNED", None
    return _account_scope(item.get("identity_status"), item.get("trading_account_id"))


def recovered_closed_cycles(rows: Sequence[Sequence[object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for r in rows:
        (
            reservation, symbol, side, qty, exit_price, entry_fee,
            exit_fee, gross, net, evidence, recovered_at,
        ) = r
        if not isinstance(evidence, dict):
            import json
            if not isinstance(evidence, (str, bytes, bytearray)):
                raise ValueError("closed-cycle evidence must be JSON")
            evidence = json.loads(evidence)
        if not isinstance(evidence, dict):
            raise ValueError("closed-cycle evidence must be an object")
        ids = evidence.get("exit_execution_ids") or []
        if not ids or evidence.get("source") != "EXACT_BYBIT_EXECUTION_HISTORY":
            continue
        result.append({
            "cycle_key": "recovered:" + str(reservation),
            "provenance": "RECOVERED_UNOWNED",
            "identity_status": "LEGACY_UNKNOWN", "trading_account_id": None,
            "strategy_id": None, "strategy_version": None,
            "symbol": str(symbol), "side": str(side),
            "qty": float(str(qty)), "price": float(str(exit_price)),
            "closed_at_epoch_ms": int(evidence["closed_at_epoch_ms"]),
            "gross_pnl": float(str(gross)), "entry_fee": float(str(entry_fee)),
            "exit_fee": float(str(exit_fee)), "net_pnl": float(str(net)),
            "actual_net_pnl": None, "economics_completeness": "PARTIAL_NO_FUNDING",
            "reason": "восстановлено по исполнениям Bybit",
            "exec_ids": list(ids),
        })
    return result


def owner_controlled_closed_cycles(
    entry_rows: Sequence[Sequence[object]], exit_rows: Sequence[Sequence[object]]
) -> list[dict[str, object]]:
    """Admit only isolated exact-quantity closes, never guessed partial cycles."""
    entries = []
    for row in entry_rows:
        if len(row) not in (10, 12):
            raise ValueError("control entry identity columns incomplete")
        command, symbol, raw, order_id, exec_id, side, qty, price, fee, when = row[:10]
        scope = _account_scope(*(row[10:12] if len(row) == 12 else (None, None)))
        entries.append((str(command),str(symbol),str(order_id),str(exec_id),
                        str(side),Decimal(str(qty)),Decimal(str(price)),
                        Decimal(str(fee)),int(str(when)),scope))
    result: list[dict[str, object]] = []
    for command,symbol,_order_id,_exec_id,side,qty,price,fee,when,scope in entries:
        if qty <= 0:
            continue
        # No subsequent same-symbol opening before this close may be assigned
        # to the earlier control trade.
        next_entries = [t for t in entries if t[1] == symbol and t[8] > when
                        and t[9] == scope]
        upper = min((t[8] for t in next_entries), default=2**63-1)
        candidates = []
        for x in exit_rows:
            if len(x) not in (9, 11):
                raise ValueError("control exit identity columns incomplete")
            xsym,xorder,xid,xside,xqty,xprice,xfee,xtime,raw = x[:9]
            exit_scope = _account_scope(*(x[9:11] if len(x) == 11 else (None, None)))
            if exit_scope != scope:
                continue
            if str(xsym) != symbol or str(xside) == side or not (when < int(str(xtime)) < upper):
                continue
            if not isinstance(raw, dict):
                import json
                if not isinstance(raw, (str, bytes, bytearray)):
                    raise ValueError("exit evidence must be JSON")
                raw = json.loads(raw)
            if not isinstance(raw, dict):
                raise ValueError("exit evidence must be an object")
            if Decimal(str(raw.get("closedSize") or 0)) != qty:
                continue
            if Decimal(str(xqty)) != qty:
                continue
            candidates.append(x)
        if len(candidates) != 1:
            continue
        xsym,xorder,xid,xside,xqty,xprice,xfee,xtime,raw = candidates[0][:9]
        exit_price=Decimal(str(xprice))
        gross=(exit_price-price)*qty if side == "Buy" else (price-exit_price)*qty
        net=gross-fee-Decimal(str(xfee))
        result.append({
            "cycle_key": "owner-control:" + command,
            "provenance": "OWNER_CONTROL",
            "identity_status": scope[0], "trading_account_id": scope[1],
            "strategy_id": None, "strategy_version": None,
            "symbol": symbol, "side": side, "qty": float(str(qty)),
            "price": float(str(exit_price)), "closed_at_epoch_ms": int(str(xtime)),
            "gross_pnl": float(str(gross)), "entry_fee": float(fee),
            "exit_fee": float(str(xfee)), "net_pnl": float(str(net)),
            "actual_net_pnl": None, "economics_completeness": "PARTIAL_NO_FUNDING",
            "reason": "контрольная сделка Bybit", "exec_ids": [str(xid)],
        })
    return result


def merge_evidenced_closed_trades(
    standard: Sequence[Mapping[str, object]],
    recovered: Sequence[Mapping[str, object]],
    control: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Dedupe only inside proven account/legacy scope, never across accounts."""
    seen: set[tuple[str, str | None, str]] = set()
    out = []
    for item in [*standard,*recovered,*control]:
        raw_ids = item.get("exec_ids", [])
        ids = (
            {str(x) for x in raw_ids if x}
            if isinstance(raw_ids, (list, tuple, set, frozenset))
            else set()
        )
        scope = _ledger_scope(item)
        keys = {(scope[0], scope[1], exec_id) for exec_id in ids}
        if not keys or keys & seen:
            continue
        seen.update(keys)
        x = dict(item)
        x.setdefault("provenance", "STRATEGY")
        x.setdefault("identity_status", "LEGACY_UNKNOWN")
        x.setdefault("trading_account_id", None)
        x.setdefault(
            "cycle_key",
            "strategy:" + scope[0] + ":" + (scope[1] or "UNATTRIBUTED")
            + ":" + ":".join(sorted(ids)),
        )
        out.append(x)
    return sorted(out, key=lambda x: int(str(x["closed_at_epoch_ms"])), reverse=True)
