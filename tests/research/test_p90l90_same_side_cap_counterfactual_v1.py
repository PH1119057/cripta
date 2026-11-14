"""#638: isolated native sidecap + fee model regression, no real server data.

CI runner injects scripts.research stubs; spawn a clean interpreter from
source root for all pure tests. Also verify absolute CLI entrypoint from cwd
OUTSIDE checkout. No server data/DB/Exchange touched.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TESTS = r'''
from __future__ import annotations
import importlib
import sys
from decimal import Decimal as D
from pathlib import Path

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path.cwd()))
m=importlib.import_module("scripts.research.p90l90_same_side_cap_counterfactual_v1")
eng=importlib.import_module("scripts.research.p90l90_unified_depth_three_period_v1")
st=(D("80"),D("120"),D("40"))

def ev(sym,side,t,seq):
    return {"symbol":sym,"side":side,"PREV7D":True,
            "depth_pct":"0","width_pct":"0.50","price":"100",
            "event_id":f"{sym}:{seq}:{side}","ts":repr(t),
            "trade_id":f"native:{sym}:{seq}"}

def add(book,sym,side,t,seq=1):
    book.handle_unified(sym,t,seq,D("100"),(ev(sym,side,t,seq),),st,0)

def test_exact_variant_contract_and_paths():
    assert m.CAPS==(5,4,3,2)
    assert m.FEES==("TAKER_TAKER","ORIGINAL_MAKER_TP")
    assert len(m.CASES)==8
    assert all(n.startswith("CAP") and "__" in n for n in m.CASES)
    assert all(
        m.output(per).as_posix().startswith("/data/cripta/research/runs/")
        and m.output(per).name==m.LEAF
        for per in ("29d","90d","183d")
    )
    assert eng.old.TAKER==m.TAKER
    assert eng.old.MAKER==m.ORIGINAL_MAKER

def test_cap_two_rejects_new_third_long_even_with_free_cash():
    cap2=m.CapBook(2,"TAKER_TAKER")
    cap5=m.CapBook(5,"ORIGINAL_MAKER_TP")
    for j,sym in enumerate(("LTCUSDT","DOTUSDT","ARBUSDT")):
        add(cap2,sym,"LONG",1000.0+j)
        add(cap5,sym,"LONG",1000.0+j)
    assert cap2.counts["accepted"]==2
    assert cap5.counts["accepted"]==3
    assert cap2.counts["same_side_cap_blocked_candidate"]==1
    assert cap2.counts["busy_or_same_tick"]==1
    assert cap2.counts["qualified_candidate"]==3
    assert cap2.max_same_side_slots==2
    assert cap5.max_same_side_slots==3
    assert cap2.pos["ARBUSDT"] is None
    assert cap5.pos["ARBUSDT"] is not None
    assert cap2.reserved==D(400)
    assert eng.old.MAKER==m.ORIGINAL_MAKER

def test_long_and_short_own_caps_shared_cash_and_same_coin_exclusion():
    b=m.CapBook(2,"TAKER_TAKER")
    add(b,"LTCUSDT","LONG",2000.0)
    add(b,"DOTUSDT","LONG",2001.0)
    add(b,"ARBUSDT","SHORT",2002.0)
    add(b,"APTUSDT","SHORT",2003.0)
    assert b.counts["accepted"]==4
    assert b.max_same_side_slots==2
    # Same-side is full, but the opposite side has independent capacity;
    # capital is still one shared wallet and one slot per symbol.
    add(b,"INJUSDT","LONG",2004.0)
    assert b.counts["same_side_cap_blocked_candidate"]==1
    assert b.reserved==D(800)
    assert b.wallet-b.reserved < D(200.55)
    b.handle_unified(
        "LTCUSDT",2010.0,2,D("100"),
        (ev("LTCUSDT","SHORT",2010.0,2),),st,0
    )
    assert b.counts["accepted"]==4
    assert b.counts["busy_or_same_tick"]==2
    assert b.pos["LTCUSDT"].side=="LONG"

def test_cap_does_not_block_existing_position_exit_or_own_pause():
    b=m.CapBook(2,"ORIGINAL_MAKER_TP")
    add(b,"LTCUSDT","LONG",3000.0)
    add(b,"DOTUSDT","LONG",3001.0)
    add(b,"ARBUSDT","LONG",3002.0)
    assert b.counts["same_side_cap_blocked_candidate"]==1
    b.handle_unified("LTCUSDT",3010.0,2,D("90"),(),st,0)
    assert b.counts["exit_HARD_STOP"]==1
    assert b.pos["LTCUSDT"] is None
    add(b,"ARBUSDT","LONG",3020.0,3)
    assert b.counts["accepted"]==3
    b.handle_unified("DOTUSDT",3030.0,2,D("90"),(),st,0)
    assert b.counts["second48_pause_triggers"]==1
    add(b,"APTUSDT","SHORT",3040.0)
    assert b.counts["paused_candidate"]==1
    assert b.pos["APTUSDT"] is None
    assert eng.old.MAKER==m.ORIGINAL_MAKER

def test_maker_sensitivity_taker_taker_changes_actual_fee_cash():
    original=m.CapBook(2,"ORIGINAL_MAKER_TP")
    conservative=m.CapBook(2,"TAKER_TAKER")
    for b in (original,conservative):
        add(b,"LTCUSDT","LONG",4000.0)
        # Favorable first resting TP touch after the Entry native tick.
        b.handle_unified("LTCUSDT",4010.0,2,D("120"),(),st,0)
    assert eng.old.MAKER==m.ORIGINAL_MAKER
    assert original.counts["closed"]==1
    assert conservative.counts["closed"]==1
    a,c=original.trades[0],conservative.trades[0]
    assert a["maker_exit"] and c["maker_exit"]
    assert D(c["exit_fee_usdt"])>D(a["exit_fee_usdt"])
    assert D(c["net_usdt"])<D(a["net_usdt"])
    assert conservative.wallet<original.wallet

def test_checkpoint_restores_cap_cash_fee_and_pause_queue():
    book=m.CapBook(3,"TAKER_TAKER")
    add(book,"LTCUSDT","SHORT",5000.0)
    book.last_stops.extend([3000.0,4000.0])
    book.pause_until=5000.0+48*3600
    snap=m.freeze(book)
    copy=m.CapBook(3,"TAKER_TAKER")
    m.restore(copy,snap)
    assert m.freeze(copy)==snap
    assert copy.pos["LTCUSDT"].side=="SHORT"
    assert copy.wallet==book.wallet
    assert copy.margin==book.margin
    assert copy.pause_until==book.pause_until
    assert eng.old.MAKER==m.ORIGINAL_MAKER

for n,f in sorted(globals().copy().items()):
    if n.startswith("test_") and callable(f):f()
'''


def test_native_book_exact_causal_sidecap_and_fees():
    root = Path(__file__).resolve().parents[2]
    r = subprocess.run(
        [sys.executable, "-B", "-c", TESTS],
        cwd=root, text=True, capture_output=True,
        timeout=70, check=False,
    )
    assert r.returncode == 0, r.stdout + "\n" + r.stderr


def test_direct_absolute_cli_help_from_outside_checkout():
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts/research/p90l90_same_side_cap_counterfactual_v1.py"
    r = subprocess.run(
        [sys.executable, "-B", str(script), "--help"],
        cwd=root.parent, text=True, capture_output=True,
        timeout=45, check=False,
    )
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
    assert "--period" in r.stdout
    assert "--mode" in r.stdout
