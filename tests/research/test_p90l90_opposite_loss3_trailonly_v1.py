"""Research #690 TRAIL_ONLY causal semantics; run isolated native subprocesses."""
import subprocess
import sys
from pathlib import Path

CASES_2026 = r"""
from decimal import Decimal as D
from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research import p90l90_opposite_loss3_trailonly_2026_v1 as ex

def e(side, price, uid):
    return {"symbol": "LTCUSDT", "side": side, "price": str(price),
            "ts": "100", "event_id": uid, "trade_id": uid, "PREV7D": True}

def opp(side, price="100"):
    return base.initial_position(e(side, price, "opp-"+side),
                                 1, 0, "PREV7D", "TRAIL035")

def open_new(side, price, opposite, cap="TRAIL_ONLY"):
    b = ex.TimeBook(cap, "ALL")
    b.handle("LTCUSDT", 100, 50, D(str(price)),
             (e(side, price, "new-"+side),),
             (D("95"), D("106")), opponent=opposite)
    assert b.count["accepted"] == 1, b.count
    return b, b.pos["LTCUSDT"]

def tests():
    b,p = open_new("LONG", "103", opp("SHORT"))
    assert getattr(p,"_r690_special",False)
    assert p.tp == D("106")  # native structural TP, NOT opponent stop 110
    assert p.tp_live_after_seq == 50
    assert b.special_entries[0]["tp_policy"] == "DYNAMIC_RAW_L5_3_UNMODIFIED"
    assert b.special_entries[0]["opponent_unrealized_gross_pct"] == "-3.00"
    assert "target_price" not in b.special_entries[0]
    print("PASS exact -3 LONG and original structural TP")

    b,p = open_new("SHORT", "97", opp("LONG"))
    assert getattr(p,"_r690_special",False) and p.tp == D("95")
    assert b.special_entries[0]["opponent_unrealized_gross_pct"] == "-3.00"
    print("PASS exact -3 SHORT")

    for old_side,new_side,price in (("SHORT","LONG","102.99"),
                                    ("LONG","SHORT","97.01")):
        b,p = open_new(new_side,price,opp(old_side))
        assert not getattr(p,"_r690_special",False)
        assert b.special_entries == []
    b,p = open_new("LONG","103",None)
    assert not getattr(p,"_r690_special",False)
    b,p = open_new("LONG","103",opp("SHORT"),cap=None)
    assert not getattr(p,"_r690_special",False)
    assert p.tp==D("106")
    print("PASS below -3, no opponent, baseline no-cap")

    b,p = open_new("LONG","103",opp("SHORT"))
    initial = p.stop
    keep,out = ex.special_on_trade(p,D("104.225"),51,0,D("107"),"TRAIL035")
    assert out is None and keep is p and p.stop==initial
    keep,out = ex.special_on_trade(p,D("104.236"),52,0,D("107"),"TRAIL035")
    assert out is None and p.protected and p.stop==D("103.206")
    print("PASS special trail +1.2 activation and 1.0pp distance")

    keep,out=ex.special_on_trade(p,D("103.9"),53,300,D("107"),"TRAIL035")
    assert out is None and p.tp==D("107") and p.tp_live_after_seq==53
    assert p.stop==D("103.206")
    print("PASS dynamic structural TP reposition and preserved protected stop")
    keep,out=ex.special_on_trade(p,D("105"),54,600,D("104.5"),"TRAIL035")
    assert keep is None and out==("MARKETABLE_TP",D("105"),False)
    print("PASS new immediately marketable RAW L5-3 TP")

    b,p = open_new("LONG","103",opp("SHORT"))
    keep,out=ex.special_on_trade(p,D("106"),50,0,D("107"),"TRAIL035")
    assert keep is p and out is None
    keep,out=ex.special_on_trade(p,D("106"),51,0,D("107"),"TRAIL035")
    assert keep is None and out==("RESTING_TP",D("106"),True)
    print("PASS resting RAW L5-3 TP priority after entry record")

    b,p = open_new("LONG","103",opp("SHORT"))
    initial = p.stop
    keep,out=ex.special_on_trade(p,D("104.236"),51,0,D("107"),"TRAIL035")
    keep,out=ex.special_on_trade(p,p.stop,52,0,D("107"),"TRAIL035")
    assert keep is None and out[0]=="PROTECTED_STOP"
    print("PASS trailing stop exit, native stop priority")

    b,p = open_new("LONG","103",opp("SHORT"))
    orig_on=base.on_trade
    assert orig_on is ex.ORIGINAL_ON_TRADE
    assert base.take_profit_eligible is ex.ORIGINAL_TP_ELIGIBILITY
    print("PASS no shared callback leaks")

tests()
"""

CASES_2025 = r"""
from decimal import Decimal as D
from scripts.research import p90l90_opposite_loss3_trailonly_2025_v1 as ex
old = ex.old
base = ex.base

def ev(side,price,ident):
    return {"symbol":"LTCUSDT","side":side,"price":str(price),
            "ts":"100","event_id":ident,"trade_id":ident,
            "PREV7D":True,"depth_pct":"0"}

old.START_WALLET = D("1000000000000")
old.FIXED_MARGIN = D("20")
old.LEVERAGE = D("5")
books = ex.new_books("ALL")
assert len(books) == 4
ex.PAIRED_BOOKS = books
ex.LAST_NATIVE_TICK = None
old.Book.handle = ex.paired_handle
try:
    ex.paired_handle(
        books["SHORT_ONLY|TRAIL_ONLY"],"LTCUSDT",100,10,D(100),
        (ev("SHORT",100,"a"),),(D(96),D(106)),0)
    assert books["SHORT_ONLY|TRAIL_ONLY"].pos["LTCUSDT"] is not None
    ex.paired_handle(
        books["LONG_ONLY|TRAIL_ONLY"],"LTCUSDT",101,11,D(103),
        (ev("LONG",103,"b"),),(D(96),D(106)),0)
    p=books["LONG_ONLY|TRAIL_ONLY"].pos["LTCUSDT"]
    assert p is not None and getattr(p,"_r690_special",False)
    assert p.tp==D(106) and p.tp!=D(110)
    assert len(books["LONG_ONLY|TRAIL_ONLY"].special_entries)==1
    assert len(books["LONG_ONLY|NO_CAP"].special_entries)==0
    print("PASS 2025 paired -3% new Entry and native RAW TP")

    ex.paired_handle(
        books["LONG_ONLY|TRAIL_ONLY"],"LTCUSDT",102,12,D("104.236"),
        (), (D(96),D(106)),0)
    assert p.protected and p.stop==D("103.206")
    print("PASS 2025 special +1.2/1.0 trail")

    extras={key:{"active_special":[sym for sym,pos in book.pos.items()
                                  if pos is not None
                                  and getattr(pos,"_r690_special",False)],
                 "special_ids":sorted(book.special_ids),
                 "special_entries":book.special_entries}
            for key,book in books.items()}
    assert ex.engine.decode(ex.engine.encode(extras)) == extras
    print("PASS 2025 paired checkpoint roundtrip")

finally:
    old.Book.handle=ex.ORIGINAL_HANDLE
    ex.PAIRED_BOOKS=None
assert base.on_trade is ex.ORIGINAL_ON_TRADE
assert base.take_profit_eligible is ex.ORIGINAL_ELIGIBLE
print("PASS 2025 clean callback restoration")
"""


def _verify(cases: str, count: int) -> None:
    result = subprocess.run(
        [sys.executable, "-B", "-c", cases],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS ") == count, result.stdout


def test_trail_only_2026_exact_contract() -> None:
    _verify(CASES_2026, 9)


def test_trail_only_2025_paired_and_checkpoint_contract() -> None:
    _verify(CASES_2025, 4)
