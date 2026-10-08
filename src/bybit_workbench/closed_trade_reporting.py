"""Operator-only closed-trade projection. Never writes Strategy metrics or ownership."""
from __future__ import annotations

from decimal import Decimal


def recovered_closed_cycles(rows):
    result = []
    for r in rows:
        reservation, symbol, side, qty, exit_price, entry_fee, exit_fee, gross, net, evidence, recovered_at = r
        if not isinstance(evidence, dict):
            import json
            evidence = json.loads(evidence)
        ids = evidence.get("exit_execution_ids") or []
        if not ids or evidence.get("source") != "EXACT_BYBIT_EXECUTION_HISTORY":
            continue
        result.append({
            "cycle_key": "recovered:" + str(reservation),
            "provenance": "RECOVERED_UNOWNED",
            "strategy_id": None, "strategy_version": None,
            "symbol": str(symbol), "side": str(side),
            "qty": float(qty), "price": float(exit_price),
            "closed_at_epoch_ms": int(evidence["closed_at_epoch_ms"]),
            "gross_pnl": float(gross), "entry_fee": float(entry_fee),
            "exit_fee": float(exit_fee), "net_pnl": float(net),
            "actual_net_pnl": None, "economics_completeness": "PARTIAL_NO_FUNDING",
            "reason": "восстановлено по исполнениям Bybit",
            "exec_ids": list(ids),
        })
    return result


def owner_controlled_closed_cycles(entry_rows, exit_rows):
    """Admit only isolated exact-quantity closes, never guessed partial cycles."""
    entries = []
    for row in entry_rows:
        command, symbol, raw, order_id, exec_id, side, qty, price, fee, when = row
        entries.append((str(command),str(symbol),str(order_id),str(exec_id),
                        str(side),Decimal(str(qty)),Decimal(str(price)),
                        Decimal(str(fee)),int(when)))
    result = []
    for command,symbol,order_id,exec_id,side,qty,price,fee,when in entries:
        if qty <= 0:
            continue
        # No subsequent same-symbol opening before this close may be assigned
        # to the earlier control trade.
        next_entries = [t for t in entries if t[1] == symbol and t[8] > when]
        upper = min((t[8] for t in next_entries), default=2**63-1)
        candidates = []
        for x in exit_rows:
            xsym,xorder,xid,xside,xqty,xprice,xfee,xtime,raw = x
            if str(xsym) != symbol or str(xside) == side or not (when < int(xtime) < upper):
                continue
            if not isinstance(raw, dict):
                import json
                raw = json.loads(raw)
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
            "symbol": symbol, "side": side, "qty": float(qty),
            "price": float(exit_price), "closed_at_epoch_ms": int(xtime),
            "gross_pnl": float(gross), "entry_fee": float(fee),
            "exit_fee": float(xfee), "net_pnl": float(net),
            "actual_net_pnl": None, "economics_completeness": "PARTIAL_NO_FUNDING",
            "reason": "контрольная сделка Bybit", "exec_ids": [str(xid)],
        })
    return result


def merge_evidenced_closed_trades(standard, recovered, control):
    """Dedupe by actual exit execIds; never assign a guessed Strategy identity."""
    seen = set()
    out = []
    for item in [*standard,*recovered,*control]:
        ids = set(str(x) for x in item.get("exec_ids", []) if x)
        if not ids or ids & seen:
            continue
        seen.update(ids)
        x = dict(item)
        x.setdefault("provenance", "STRATEGY")
        x.setdefault("cycle_key", "strategy:" + ":".join(sorted(ids)))
        out.append(x)
    return sorted(out, key=lambda x: int(x["closed_at_epoch_ms"]), reverse=True)
