"""Synthetic regression for causal pre-Entry research-only five-coin features.

Repo-wide test runner may inject fake scripts.research modules, so test pure
functions in an isolated -B subprocess. No server Data/DB/network is accessed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = r'''
import importlib
import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
m=importlib.import_module("scripts.research.p90l90_preentry_crosscoin_forensic_v1")
assert len(m.SYMBOLS)==5 and m.FOCUS.endswith("__fixed__SECOND48")

def source(direction):
    d={}
    for symbol in m.SYMBOLS:
        d[symbol]={}
        for i in range(15):
            t=i*300
            # All five coins share same adverse directional trend.
            price=100+(i*direction)
            d[symbol][t]=(price,0.4,1.5)
    return d

def trade(side="LONG",t="3660"):
    return {"entry_ts":t,"symbol":"APTUSDT","side":side}

def test_causal_previous_complete_5m_and_full_peer_breadth():
    down=source(-1)
    got=m.preentry_features(down,trade())
    assert got["asof_5m_complete_ts"]==3600
    assert got["lag_seconds"]==60.0
    assert got["peer_adverse_15m_count"]==4
    assert got["peer_mean_adverse_15m_pct"]>0
    assert got["own_adverse_60m_pct"]>0
    assert got["own_atr_pct_mid"]==0.4
    assert got["own_width_pct_mid"]==1.5
    # At the exact boundary, the current just-completed candle is rejected.
    boundary=m.preentry_features(down,trade(t="3600"))
    assert boundary["asof_5m_complete_ts"]==3300
    assert boundary["lag_seconds"]==300.0
    # Full opposite-side symmetry: rising cross-coin prices adverse to SHORT.
    up=source(+1)
    assert m.preentry_features(up,trade("SHORT"))["peer_adverse_15m_count"]==4
    assert m.preentry_features(down,trade("SHORT"))["peer_adverse_15m_count"]==0

def test_no_data_is_never_silent_neutral():
    down=source(-1)
    down["INJUSDT"].pop(2700)
    assert m.preentry_features(down,trade()) is None
    down=source(-1)
    down["LTCUSDT"].pop(3600)
    assert m.preentry_features(down,trade()) is None

def fake(stop,coin,side,month,n,net="1"):
    return {
        "hardstop":stop,"symbol":coin,"side":side,"entry_month":month,
        "net_usdt":net,"exit_day":"2025-04-06",
        "features":{k:(n if k!="peer_adverse_15m_count" else int(n))
                    for k in m.FEATURES}
    }

def test_controls_are_same_symbol_side_month_only():
    all_rows=[
        fake(True,"APTUSDT","LONG","2025-04",4,"-101"),
        fake(False,"APTUSDT","LONG","2025-04",1),
        fake(False,"APTUSDT","LONG","2025-04",3),
        fake(False,"DOTUSDT","LONG","2025-04",0),
        fake(False,"APTUSDT","SHORT","2025-04",0),
        fake(False,"APTUSDT","LONG","2025-05",0),
        fake(True,"INJUSDT","SHORT","2025-04",2,"-101"),
    ]
    got=m.matched(all_rows)
    assert got["stops"]==2
    assert got["stops_with_same_period_symbol_side_month_controls"]==1
    assert got["unmatched_stops"]==1
    feat=got["features"]["own_adverse_15m_pct"]
    assert feat["matched_hardstop_mean"]==4.0
    assert feat["matched_control_mean"]==2.0
    assert feat["matched_delta"]==2.0

def test_breadth_cells_are_descriptive_net_after_fees():
    rows=[fake(True,"APTUSDT","LONG","2025-04",4,"-101.14"),
          fake(False,"APTUSDT","LONG","2025-04",4,"4.18"),
          fake(False,"APTUSDT","LONG","2025-04",0,"2.00")]
    b=m.breadth_summary(rows)
    assert b["4"]=={"trades":2,"stops":1,"stop_rate_pct":50.0,
                    "closed_net_after_fees_usdt":"-96.96"}
    assert b["1"]["stop_rate_pct"] is None
    c=m.exit_day_clusters(rows)
    assert c==[]

for name,fun in list(globals().items()):
    if name.startswith("test_") and callable(fun):fun()
'''


def test_preentry_five_coin_causality_and_controls_without_server():
    repo = Path(__file__).resolve().parents[2]
    r = subprocess.run(
        [sys.executable, "-B", "-c", SCRIPT],
        cwd=repo, capture_output=True, text=True, check=False, timeout=60,
    )
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
