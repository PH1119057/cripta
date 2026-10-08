from decimal import Decimal
import copy

from bybit_workbench.exchange_recovery import prove_orphan_closed_cycle


def _cycle(n="0"):
    entry_order = {"orderId": f"entry{n}", "symbol": f"COIN{n}USDT",
                   "side": "Buy", "orderStatus": "Filled", "cumExecQty": "1.3"}
    buy = {"execId": f"buy{n}", "orderId": f"entry{n}",
           "symbol": f"COIN{n}USDT", "side": "Buy", "execQty": "1.3",
           "execPrice": "7.32", "execFee": "0.0019032", "execTime": "1000"}
    sell = {"execId": f"sell{n}", "orderId": f"exit{n}",
            "symbol": f"COIN{n}USDT", "side": "Sell", "execQty": "1.3",
            "execPrice": "7.257", "execFee": "0.00518876", "execTime": "2000"}
    history = {f"exit{n}": {"orderStatus": "Filled",
                            "symbol": f"COIN{n}USDT", "side": "Sell"}}
    return entry_order, [buy], [sell], history


def _prove(c):
    return prove_orphan_closed_cycle(
        entry_order=c[0], entry_executions=c[1],
        subsequent_executions=c[2], terminal_orders=c[3])


def test_inj_exact_roundtrip_after_fees():
    proof = _prove(_cycle())
    assert proof is not None
    assert proof.quantity == Decimal("1.3")
    assert proof.entry_vwap == Decimal("7.32")
    assert proof.exit_vwap == Decimal("7.257")
    assert proof.gross_pnl == Decimal("-0.0819")
    assert proof.net_without_funding == Decimal("-0.08899196")


def test_sixty_orphan_roundtrips_recover_without_collisions():
    proofs = [_prove(_cycle(str(i))) for i in range(60)]
    assert all(proofs)
    assert len({p.entry_order_id for p in proofs}) == 60
    assert proofs == [_prove(_cycle(str(i))) for i in range(60)]


def test_same_side_intervening_fill_remains_ambiguous():
    c = _cycle()
    c[2].insert(0, {"execId": "scalein", "orderId": "scale",
                    "symbol": "COIN0USDT", "side": "Buy",
                    "execQty": "0.2", "execPrice": "7", "execTime": "1500"})
    assert _prove(c) is None


def test_missing_exit_order_evidence_remains_ambiguous():
    c = _cycle()
    c[3].clear()
    assert _prove(c) is None


def test_partial_exit_does_not_count_as_closed():
    c = _cycle()
    c[2][0]["execQty"] = "0.4"
    assert _prove(c) is None


def test_duplicate_fill_identity_does_not_double_count():
    c = _cycle()
    c[1].append(copy.deepcopy(c[1][0]))
    assert _prove(c) is None


def test_future_reopen_after_proven_close_does_not_change_old_cycle():
    c = _cycle()
    c[2].append({"execId": "next", "orderId": "other",
                 "symbol": "COIN0USDT", "side": "Buy",
                 "execQty": "2", "execPrice": "7", "execTime": "3000"})
    assert _prove(c) is not None
