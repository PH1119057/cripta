"""Issue #241: stepped +0.5pp MFE stair-stop and unchanged native L53 TP.

Research-only isolated module import; mirror original exact baseline behavior.
"""
import importlib.util
import sys
from decimal import Decimal as D
from pathlib import Path
from types import ModuleType

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_PRIOR = sys.modules.get("scripts.research")
_FRESH = ModuleType("scripts.research")
_FRESH.__path__ = [str(_SCRIPTS / "research")]
sys.modules["scripts.research"] = _FRESH
try:
    spec = importlib.util.spec_from_file_location(
        "_study_stair050", _SCRIPTS / "research"
        / "p90l90_l53_stair050_lock020_tp_v1.py")
    assert spec is not None and spec.loader is not None
    study = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = study
    spec.loader.exec_module(study)
finally:
    if _PRIOR is None:
        sys.modules.pop("scripts.research", None)
    else:
        sys.modules["scripts.research"] = _PRIOR


def entry(symbol, t, side="LONG"):
    return ({
        "PREV7D": True, "side": side, "symbol": symbol,
        "price": "100", "ts": repr(t),
        "event_id": f"{symbol}:{t}:{side}",
        "trade_id": f"tr:{symbol}:{t}:{side}",
    },)


STATE = (D("99"), D("101"), D("2"))


def test_first_step_exact_trigger_and_no_continuous_trailing():
    pos = study.old.base.initial_position(
        entry("DOTUSDT", 900)[0], 1, 900, "PREV7D", "STAIR050"
    )
    pos.tp = D("101")
    pos.tp_live_after_seq = 1
    result, close = study.stair_lock_on_trade(
        pos, D("100.49"), 2, 900, D("101"))
    assert result is pos and close is None
    assert pos.stop == D("90") and not pos.protected
    result, close = study.stair_lock_on_trade(
        pos, D("100.50"), 3, 900, D("101"))
    assert close is None and pos.protected
    assert pos.stop == D("100.200") and pos.protection_seq == 3
    result, close = study.stair_lock_on_trade(
        pos, D("100.90"), 4, 900, D("101"))
    assert close is None
    assert pos.stop == D("100.200"), "stop must NOT follow price"


def test_protection_activates_for_next_tick_only():
    book = study.StairLockBook()
    symbol = "DOTUSDT"
    book.handle(symbol, 900, 1, D("100"), entry(symbol, 900), STATE)
    book.handle(symbol, 901, 2, D("100.5"), (), STATE)
    assert book.pos[symbol] is not None
    assert book.pos[symbol].stop == D("100.200")
    book.handle(symbol, 902, 3, D("100.19"), (), STATE)
    assert book.pos[symbol] is None
    assert book.trades[0]["exit_reason"] == "PROTECTED_STOP"
    assert D(book.trades[0]["net_usdt"]) > 0
    assert book.protection_activations == 1


def test_reaches_original_native_tp_after_activation():
    book = study.StairLockBook()
    s = "DOTUSDT"
    book.handle(s, 900, 1, D("100"), entry(s, 900), STATE)
    book.handle(s, 901, 2, D("100.5"), (), STATE)
    book.handle(s, 902, 3, D("100.9"), (), STATE)
    assert book.pos[s] is not None and book.pos[s].stop == D("100.200")
    book.handle(s, 903, 4, D("101"), (), STATE)
    assert book.pos[s] is None
    assert book.trades[0]["exit_reason"] == "RESTING_TP"
    assert D(book.trades[0]["exit_price"]) == D("101")
    assert len(book.diagnostics) == 1


def test_causal_moving_tp_target_after_new_5m_bucket():
    book = study.StairLockBook()
    s = "DOTUSDT"
    book.handle(s, 900, 1, D("100"), entry(s, 900), STATE)
    book.handle(s, 901, 2, D("100.5"), (), STATE)
    state2 = (D("99"), D("102"), D("3"))
    book.handle(s, 1200, 3, D("100.8"), (), state2)
    assert book.pos[s].tp == D("102")
    book.handle(s, 1201, 4, D("102"), (), state2)
    assert book.trades[0]["exit_reason"] == "RESTING_TP"
    assert D(book.trades[0]["net_usdt"]) > D("0.18")


def test_short_stair_stop_mirrors_long():
    book = study.StairLockBook()
    s = "APTUSDT"
    book.handle(s, 900, 1, D("100"), entry(s, 900, "SHORT"), STATE)
    book.handle(s, 901, 2, D("99.5"), (), STATE)
    assert book.pos[s].stop == D("99.800")
    book.handle(s, 902, 3, D("99.1"), (), STATE)
    assert book.pos[s].stop == D("99.800")
    book.handle(s, 903, 4, D("99.81"), (), STATE)
    assert book.pos[s] is None
    assert book.trades[0]["exit_reason"] == "PROTECTED_STOP"


def test_hardstop_still_initial_negative_ten_percent():
    book = study.StairLockBook()
    s = "LTCUSDT"
    book.handle(s, 900, 1, D("100"), entry(s, 900), STATE)
    book.handle(s, 901, 2, D("89.9"), (), STATE)
    assert book.trades[0]["exit_reason"] == "HARD_STOP"
    assert book.trades[0]["exit_price"] == "89.9"
    assert book.protection_activations == 0


def test_second_hardstop_entry_embargo_not_flatten():
    book = study.StairLockBook()
    for s in ("DOTUSDT", "LTCUSDT", "APTUSDT"):
        book.handle(s, 900, 1, D("100"), entry(s, 900), STATE)
    book.handle("DOTUSDT", 901, 2, D("89"), (), STATE)
    book.handle("LTCUSDT", 902, 2, D("89"), (), STATE)
    assert book.count["pause_triggers"] == 1
    assert book.pos["APTUSDT"] is not None
    book.handle("INJUSDT", 903, 1, D("100"), entry("INJUSDT", 903), STATE)
    assert book.pos["INJUSDT"] is None
    assert book.count["blocked_global_pause"] == 1
    assert book.count.get("close_PORTFOLIO_FLATTEN", 0) == 0
    assert book.finish()["counts"]["close_HARD_STOP"] == 2


def test_histogram_boundaries_loss_and_positive():
    rows = [
        {"symbol": "LTCUSDT", "net_usdt": "-1.0",
         "exit_reason": "HARD_STOP"},
        {"symbol": "LTCUSDT", "net_usdt": "0.0499999",
         "exit_reason": "PROTECTED_STOP"},
        {"symbol": "LTCUSDT", "net_usdt": "0.05",
         "exit_reason": "PROTECTED_STOP"},
        {"symbol": "LTCUSDT", "net_usdt": "0.10",
         "exit_reason": "RESTING_TP"},
        {"symbol": "LTCUSDT", "net_usdt": "0.20",
         "exit_reason": "RESTING_TP"},
        {"symbol": "LTCUSDT", "net_usdt": "0.25",
         "exit_reason": "RESTING_TP"},
    ]
    h = study.histogram(rows)["LTCUSDT"]["bins"]
    assert h["LOSS"] == 1
    assert h["0_TO_0P5"] == 1
    assert h["0P5_TO_1"] == 1
    assert h["1_TO_1P5"] == 1
    assert h["2_TO_2P5"] == 1
    assert h["GE_2P5"] == 1


def test_empty_and_accounting_corruption_fail_closed():
    b = study.StairLockBook()
    assert b.finish()["counts"].get("accepted", 0) == 0
    assert not b.trades and not b.diagnostics
    b = study.StairLockBook()
    b.count["accepted"] = 1
    try:
        b.finish()
    except ValueError as e:
        assert "accounting mismatch" in str(e)
    else:
        raise AssertionError("accepted/closed invariant not enforced")

def test_unlimited_half_percent_step_schedule():
    assert study.locked_profit_pct(D("0.49999999")) is None
    levels = [
        ("0.5", "0.2"), ("0.999999", "0.2"),
        ("1", "0.5"), ("1.499999", "0.5"),
        ("1.5", "1.0"), ("1.999999", "1.0"),
        ("2", "1.5"), ("2.499999", "1.5"),
        ("2.5", "2.0"), ("3", "2.5"),
        ("10", "9.5"), ("100", "99.5"),
    ]
    for mfe,lock in levels:
        assert study.locked_profit_pct(D(mfe)) == D(lock)
    assert study.achieved_step_index(D("100")) == 200


def test_long_stop_ratchets_stepwise_and_never_looser():
    pos=study.old.base.initial_position(
        entry("DOTUSDT",900)[0],1,900,"PREV7D","STAIR050")
    pos.tp=D("120")
    pos.tp_live_after_seq=1
    for seq,price,stop in [
        (2,"100.5","100.2"),(3,"100.9","100.2"),
        (4,"101","100.5"),(5,"101.49","100.5"),
        (6,"101.5","101"),(7,"102","101.5"),
        (8,"102.5","102"),(9,"102.7","102"),
        (10,"102.3","102"),
    ]:
        nxt,ex=study.stair_lock_on_trade(pos,D(price),seq,900,D("120"))
        assert nxt is pos and ex is None and pos.stop==D(stop)
    nxt,ex=study.stair_lock_on_trade(pos,D("101.99"),11,900,D("120"))
    assert nxt is None and ex[0]=="PROTECTED_STOP"


def test_gap_over_multiple_steps_and_short_mirror():
    pos=study.old.base.initial_position(
        entry("DOTUSDT",900)[0],1,900,"PREV7D","STAIR050")
    pos.tp=D("120")
    pos.tp_live_after_seq=1
    result,close=study.stair_lock_on_trade(pos,D("103.1"),2,900,D("120"))
    assert result is pos and close is None and pos.stop==D("102.5")
    assert pos.protection_seq==2
    result,close=study.stair_lock_on_trade(pos,D("102.499"),3,900,D("120"))
    assert result is None and close[0]=="PROTECTED_STOP"
    pos=study.old.base.initial_position(
        entry("APTUSDT",900,"SHORT")[0],1,900,"PREV7D","STAIR050")
    pos.tp=D("80")
    pos.tp_live_after_seq=1
    for seq,price,stop in [
        (2,"99.5","99.8"),(3,"99","99.5"),
        (4,"98.5","99"),(5,"98","98.5"),(6,"97.9","98.5"),
    ]:
        nxt,ex=study.stair_lock_on_trade(pos,D(price),seq,900,D("80"))
        assert nxt is pos and ex is None and pos.stop==D(stop)
    nxt,ex=study.stair_lock_on_trade(pos,D("98.51"),7,900,D("80"))
    assert nxt is None and ex[0]=="PROTECTED_STOP"


def test_stair_updates_audited_and_single_initial_activation():
    book=study.StairLockBook()
    s="DOTUSDT"
    state=(D("99"),D("110"),D("10"))
    book.handle(s,900,1,D("100"),entry(s,900),state)
    for seq,price in [(2,"100.50"),(3,"101.00"),(4,"101.50"),(5,"101.65")]:
        book.handle(s,900+seq-1,seq,D(price),(),state)
    assert book.stop_step_updates==3
    assert book.protection_activations==1
    assert book.step_reached_count=={1:1,2:1,3:1}
    assert book.pos[s].stop==D("101")
    book.handle(s,906,6,D("100.99"),(),state)
    assert book.trades[0]["exit_reason"]=="PROTECTED_STOP"
    assert book.diagnostics[0]["achieved_stair_index"]==3
    assert book.diagnostics[0]["locked_profit_pct"]=="1.0"
    report=book.finish()
    assert report["max_step_index"]==3
    assert report["step_reached_count"]=={"1":1,"2":1,"3":1}
