#!/usr/bin/env python3
"""Bybit orderbook three single-source indicators, research only, read-only.
Snapshot resets book. Deltas change price levels. Minute-end state only.
"""
import argparse,zipfile,json,statistics
from pathlib import Path
START=1779148800000
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
def calculate(sym):
    if sym not in SYMS:raise ValueError("symbol")
    file=ROOT/sym/"orderbook"/("2026-05-19_"+sym+"_ob200.data.zip")
    b={};a={};last=[None]*1440;msgs=0;snaps=0;prev=-1
    with zipfile.ZipFile(file) as z:
        for name in z.namelist():
            if not name.endswith((".data",".jsonl")):continue
            with z.open(name) as f:
                for line in f:
                    e=json.loads(line);ts=int(e["ts"])
                    if ts<START:continue
                    if ts>=START+86400000:break
                    if ts<prev:raise ValueError("timestamp reversal")
                    prev=ts
                    if e["type"]=="snapshot":
                        b={float(p):float(q) for p,q in e["data"]["b"]}
                        a={float(p):float(q) for p,q in e["data"]["a"]};snaps+=1
                    else:
                        for levels,side in ((e["data"]["b"],b),(e["data"]["a"],a)):
                            for price,quantity in levels:
                                p=float(price);q=float(quantity)
                                if q==0:side.pop(p,None)
                                else:side[p]=q
                    if not b or not a:raise ValueError("empty state")
                    bid=max(b);ask=min(a)
                    if bid>=ask:raise ValueError("crossed book")
                    mid=(bid+ask)/2
                    bd=sum(p*q for p,q in b.items() if mid*.9995<=p<=mid)
                    ad=sum(p*q for p,q in a.items() if mid<=p<=mid*1.0005)
                    last[(ts-START)//60000]={"imbalance":(bd-ad)/(bd+ad) if bd+ad else None,
                        "bid_depth_usd":bd,"ask_depth_usd":ad,
                        "spread_bps":(ask-bid)/mid*10000,
                        "age_ms":START+(((ts-START)//60000)+1)*60000-ts}
                    msgs+=1
    valid=[x for x in last if x is not None]
    return {"symbol":sym,"source_messages":msgs,"snapshots":snaps,"minute_states":len(valid),
      "median_spread_bps":round(statistics.median(x["spread_bps"] for x in valid),4),
      "median_imbalance":round(statistics.median(x["imbalance"] for x in valid if x["imbalance"] is not None),4),
      "max_age_ms":max(x["age_ms"] for x in valid),
      "output_semantics":"value available strictly after that UTC minute closes","minutes":last}
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--symbol",choices=SYMS,required=True);p.add_argument("--summary",action="store_true")
    v=p.parse_args();r=calculate(v.symbol)
    if v.summary:r.pop("minutes")
    print(json.dumps(r))
