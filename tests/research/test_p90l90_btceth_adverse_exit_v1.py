"""Deterministic invariants for research-only BTC/ETH adverse Exit replay."""
import importlib
from decimal import Decimal as D


def model():
    module=importlib.import_module("scripts.research.p90l90_btceth_adverse_exit_90d_v1")
    return module.MarketExitPortfolio, module.VARIANTS


STATE=(D("80"),D("120"),D("1"))


def entry(symbol, t, side="LONG"):
    return ({"symbol":symbol,"price":"100","side":side,"ts":repr(t),
             "event_id":symbol+":"+repr(t),"trade_id":"test",
             "PREV7D":True},)


def test_market_exit_only_after_entry_and_when_both_risk_and_loss_gates_hold():
    Book, variants = model()
    book=Book(variants["H06_X5_L2"])
    book.handle_market("APTUSDT",100,1,D("100"),entry("APTUSDT",100),STATE,(1,9))
    assert book.count["accepted"]==1
    book.handle_market("APTUSDT",101,2,D("99"),(),STATE,(1,6))
    assert book.count["closed"]==0
    book.handle_market("APTUSDT",102,3,D("97"),(),STATE,(-1,9))
    assert book.count["closed"]==0
    book.handle_market("APTUSDT",103,4,D("97"),(),STATE,(1,4.9))
    assert book.count["closed"]==0
    book.handle_market("APTUSDT",104,5,D("97"),(),STATE,(1,5.1))
    assert book.count["close_BTC_ETH_ADVERSE_EXIT"]==1
    assert book.count["closed"]==1
    assert book.trades[0]["exit_price"]=="97"
    assert book.trades[0]["exit_reason"]=="BTC_ETH_ADVERSE_EXIT"
    assert not book.trades[0]["maker_exit"]
    assert book.finish()["counts"]["censored_open"]==0


def test_risk_exit_does_not_reenter_on_same_tick():
    Book, variants = model()
    book=Book(variants["H06_X4_L1"])
    book.handle_market("LTCUSDT",100,1,D("100"),entry("LTCUSDT",100),STATE,None)
    book.handle_market("LTCUSDT",200,2,D("98"),
                       entry("LTCUSDT",200),STATE,(1,4.5))
    assert book.count["accepted"]==1
    assert book.count["busy_or_same_tick"]==1
    assert book.count["close_BTC_ETH_ADVERSE_EXIT"]==1


def test_existing_hard_stop_not_relabelled_as_market_exit():
    Book, variants = model()
    book=Book(variants["H06_X5_L2"])
    book.handle_market("INJUSDT",100,1,D("100"),entry("INJUSDT",100),STATE,None)
    book.handle_market("INJUSDT",200,2,D("89"),(),STATE,(1,99))
    assert book.count["close_HARD_STOP"]==1
    assert book.count["close_BTC_ETH_ADVERSE_EXIT"]==0
    assert book.finish()["counts"]["closed"]==1


def test_short_risk_direction_reverses():
    Book, variants = model()
    book=Book(variants["H06_X5_L2"])
    book.handle_market("DOTUSDT",100,1,D("100"),
                       entry("DOTUSDT",100,"SHORT"),STATE,None)
    book.handle_market("DOTUSDT",150,2,D("103"),(),STATE,(1,9))
    assert book.count["closed"]==0
    book.handle_market("DOTUSDT",200,3,D("103"),(),STATE,(-1,6))
    assert book.count["close_BTC_ETH_ADVERSE_EXIT"]==1
