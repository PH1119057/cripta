"""#638 concurrent portfolio position exposure / native stop-cluster tests.

All synthetic (NO server Data or Exchange required). Also execute the exact
absolute-path CLI through a subprocess from outside the repo checkout.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PURE_TESTS = r'''
import sys
from decimal import Decimal as D
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from scripts.research import p90l90_stop_exposure_forensic_v1 as m

BASE = D("1743897600")

def row(uid, coin, side, lo, hi, *, stop=False, censored=False, net="-101.10"):
    return {
        "id":uid,"symbol":coin,"side":side,
        "start":BASE+D(lo),"end":BASE+D(hi),
        "margin":D(200),"notional":D(1000),
        "closed":not censored,"stop":stop,
        "exit_reason":"HARD_STOP" if stop else ("OPEN_CENSORED" if censored else "PROTECTED_STOP"),
        "net":None if censored else D(net)
    }

def test_actual_overlap_not_just_calendar_day():
    # A and B both open before first stop. C enters after both stop.
    a=row("a","APTUSDT","LONG",1,11,stop=True)
    b=row("b","ARBUSDT","LONG",3,12,stop=True)
    c=row("c","INJUSDT","LONG",13,14,stop=True)
    d=row("d","DOTUSDT","SHORT",4,8,net="4.18")
    e=row("e","LTCUSDT","SHORT",10,20,censored=True)
    allpos=[a,b,c,d,e]
    occ=m.sweep(allpos,BASE,BASE+D(20))
    assert occ["max_simultaneous_slots"]==3
    assert occ["max_same_side_slots"]==2
    assert occ["max_total_notional_usdt"]=="3000.00"
    assert occ["max_same_side_notional_usdt"]=="2000.00"
    assert occ["time_fraction_pct_by_active_slots"]["0"]==5.0
    assert occ["avg_active_slots_time_weighted"]==1.7
    assert m.exposure(a,allpos,"exit")["same"]==2
    assert m.exposure(a,allpos,"exit")["opposite"]==1
    assert m.exposure(b,allpos,"entry")["same"]==2
    assert m.exposure(c,allpos,"entry")["same"]==1
    comps=m.stop_overlap_components([a,b,c])
    assert len(comps)==1
    group=comps[0]
    assert group["component_stop_positions"]==2
    assert group["peak_stop_victims_concurrently_open"]==2
    assert group["already_open_victims_before_first_stop"]==2
    assert group["net_hardstop_loss_after_fees_usdt"]=="-202.20"
    days=m.utc_day_groups([a,b,c,d])
    assert len(days)==1
    assert days[0]["hardstops_on_day"]==3
    assert days[0]["stop_victims_preexisting_before_first_stop"]==2
    assert days[0]["same_day_stops_all_preexisting_at_first"] is False
    assert days[0]["whole_utc_day_closed_net_after_fees_usdt"]=="-299.12"
    bins=m.stop_exposure_by_entry([a,b,c,d],allpos)
    assert bins["1"]["hardstops"]==2
    assert bins["2"]["hardstops"]==1
    assert bins["1"]["closed_net_after_fees_usdt"]=="-198.02"
    assert bins["2"]["closed_net_after_fees_usdt"]=="-101.10"

def test_exact_boundary_excludes_same_ts_peer_and_censored_is_active():
    p=row("p","APTUSDT","LONG",1,10,stop=True)
    q=row("q","LTCUSDT","LONG",10,20,censored=True)
    r=row("r","INJUSDT","SHORT",5,20,censored=True)
    assert m.exposure(p,[p,q,r],"exit")["same"]==1
    assert m.exposure(q,[p,q,r],"entry")["same"]==1
    assert m.exposure(p,[p,q,r],"exit")["opposite"]==1
    occ=m.sweep([p,q,r],BASE,BASE+D(20))
    assert occ["max_simultaneous_slots"]==2

def test_fail_closed_on_overlap_same_symbol_and_duplicate():
    a=row("a","APTUSDT","LONG",1,10,stop=True)
    b=row("b","APTUSDT","SHORT",2,12)
    try:m.sweep([a,b],BASE,BASE+D(20))
    except ValueError as e:assert "same-symbol" in str(e)
    else:raise AssertionError("did not enforce same-symbol exclusive slot")
    try:m.normalize(
      [{"entry_trade_id":"x","symbol":"APTUSDT","side":"LONG",
        "entry_ts":"10","exit_ts":"11","margin_usdt":"200",
        "notional_usdt":"1000","exit_reason":"HARD_STOP","net_usdt":"-101"},
       {"entry_trade_id":"x","symbol":"DOTUSDT","side":"LONG",
        "entry_ts":"10","exit_ts":"11","margin_usdt":"200",
        "notional_usdt":"1000","exit_reason":"HARD_STOP","net_usdt":"-101"}],
      [],D(20))
    except ValueError as e:assert "duplicate" in str(e)
    else:raise AssertionError("did not detect duplicate native position")

def test_no_assumption_that_all_transitive_victims_are_simultaneous():
    a=row("a","APTUSDT","LONG",1,10,stop=True)
    b=row("b","ARBUSDT","LONG",7,15,stop=True)
    c=row("c","DOTUSDT","LONG",13,20,stop=True)
    comp=m.stop_overlap_components([a,b,c])[0]
    assert comp["component_stop_positions"]==3
    assert comp["peak_stop_victims_concurrently_open"]==2
    assert comp["already_open_victims_before_first_stop"]==2

for name,fn in list(globals().items()):
    if name.startswith("test_") and callable(fn):fn()
'''


def test_overlap_exposure_pure_functions_without_server_data():
    repo = Path(__file__).resolve().parents[2]
    p = subprocess.run(
        [sys.executable, "-B", "-c", PURE_TESTS], cwd=repo,
        capture_output=True, text=True, timeout=40, check=False,
    )
    assert p.returncode == 0, p.stdout + "\n" + p.stderr


def test_absolute_path_cli_from_outside_checkout():
    repo = Path(__file__).resolve().parents[2]
    p = subprocess.run(
        [
            sys.executable, "-B",
            str(repo / "scripts/research/p90l90_stop_exposure_forensic_v1.py"),
            "--help",
        ],
        cwd=repo.parent, capture_output=True, text=True, timeout=30, check=False,
    )
    assert p.returncode == 0, p.stdout + "\n" + p.stderr
    assert "--period" in p.stdout
