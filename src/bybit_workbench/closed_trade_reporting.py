"""Operator-only closed-trade projection. Never writes Strategy metrics or ownership."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal


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
        command, symbol, raw, order_id, exec_id, side, qty, price, fee, when = row
        entries.append((str(command),str(symbol),str(order_id),str(exec_id),
                        str(side),Decimal(str(qty)),Decimal(str(price)),
                        Decimal(str(fee)),int(str(when))))
    result = []
    for command,symbol,_order_id,_exec_id,side,qty,price,fee,when in entries:
        if qty <= 0:
            continue
        # No subsequent same-symbol opening before this close may be assigned
        # to the earlier control trade.
        next_entries = [t for t in entries if t[1] == symbol and t[8] > when]
        upper = min((t[8] for t in next_entries), default=2**63-1)
        candidates = []
        for x in exit_rows:
            xsym,xorder,xid,xside,xqty,xprice,xfee,xtime,raw = x
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
        xsym,xorder,xid,xside,xqty,xprice,xfee,xtime,raw = candidates[0]
        exit_price=Decimal(str(xprice))
        gross=(exit_price-price)*qty if side == "Buy" else (price-exit_price)*qty
        net=gross-fee-Decimal(str(xfee))
        result.append({
            "cycle_key": "owner-control:" + command,
            "provenance": "OWNER_CONTROL",
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
    """Dedupe by actual exit execIds; never assign a guessed Strategy identity."""
    seen: set[str] = set()
    out = []
    for item in [*standard,*recovered,*control]:
        raw_ids = item.get("exec_ids", [])
        ids = (
            {str(x) for x in raw_ids if x}
            if isinstance(raw_ids, (list, tuple, set, frozenset))
            else set()
        )
        if not ids or ids & seen:
            continue
        seen.update(ids)
        x = dict(item)
        x.setdefault("provenance", "STRATEGY")
        x.setdefault("cycle_key", "strategy:" + ":".join(sorted(ids)))
        out.append(x)
    return sorted(out, key=lambda x: int(str(x["closed_at_epoch_ms"])), reverse=True)
