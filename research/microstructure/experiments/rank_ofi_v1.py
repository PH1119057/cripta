#!/usr/bin/env python3
"""RESEARCH ONLY: rank-level OFI and visible top-book reductions.

Read-only on historical Bybit ob200. No database, cache or other file writes.
Rank-level OFI uses price-conditional transitions, but shifts of deeper
ranks can reflect level reindexing. Visible reductions are NOT pure cancels.
"""
import argparse
import glob
import json
import zipfile
import datetime as dt

COHORT=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT="/data/cripta/datasets/raw/20260518_20260816"

def calculate(symbol,date,minutes=60,root=ROOT,levels=5):
    if symbol not in COHORT or not 1<=minutes<=1440 or not 1<=levels<=10:raise ValueError("parameters")
    lo=int(dt.datetime.strptime(date,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()*1000)
    match=glob.glob(f"{root}/{symbol}/orderbook/{date}_{symbol}_ob200.data.zip")
    if len(match)!=1:raise FileNotFoundError(str(match))
    b={};a={};pb=pa=None;ofi=[0.]*levels;dec=[0.,0.];n=0
    with zipfile.ZipFile(match[0]) as z:
        with z.open(z.namelist()[0]) as stream:
            for raw in stream:
                o=json.loads(raw)
                if o["ts"]>=lo+minutes*60000:break
                if o["ts"]<lo:continue
                d=o["data"]
                if o["type"]=="snapshot":
                    b={float(p):float(v) for p,v in d["b"]}
                    a={float(p):float(v) for p,v in d["a"]}
                    pb=pa=None
                    continue
                for changes,book in ((d["b"],b),(d["a"],a)):
                    for p,v in changes:
                        p=float(p);v=float(v)
                        if v==0:book.pop(p,None)
                        else:book[p]=v
                if not b or not a:raise ValueError("Empty book after delta")
                cb=[(p,b[p]) for p in sorted(b,reverse=True)[:levels]]
                ca=[(p,a[p]) for p in sorted(a)[:levels]]
                if pb is not None and len(cb)==levels and len(ca)==levels:
                    for j in range(levels):
                        price,qty=cb[j];last_price,last_qty=pb[j]
                        bd=qty if price>last_price else qty-last_qty if price==last_price else -last_qty
                        price,qty=ca[j];last_price,last_qty=pa[j]
                        ad=qty if price<last_price else qty-last_qty if price==last_price else -last_qty
                        ofi[j]+=bd-ad
                    for j,(old,new) in enumerate(((pb,cb),(pa,ca))):
                        if old[0][0]==new[0][0]:
                            diff=new[0][1]-old[0][1]
                            if diff<0:dec[j]-=diff
                    n+=1
                pb,pa=cb,ca
    return {"algorithm":"rank_ofi_v1","status":"RESEARCH_ONLY_NOT_VALIDATED",
            "symbol":symbol,"date_utc":date,"minutes":minutes,"message_transitions":n,
            "rank_ofi_base_asset_units":ofi,
            "visible_top_bid_ask_reductions_base_units":dec,
            "warnings":["Not order-level OFI; rank shifts may reindex levels.",
                        "Visible reductions combine trades and cancellations; NOT Pulling Rate.",
                        "Raw base-unit series not comparable across symbols.",
                        "No OOS or execution-cost test."]}

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol",choices=COHORT,required=True)
    p.add_argument("--date",required=True)
    p.add_argument("--minutes",type=int,default=60)
    p.add_argument("--levels",type=int,default=5)
    p.add_argument("--root",default=ROOT)
    a=p.parse_args()
    print(json.dumps(calculate(a.symbol,a.date,a.minutes,a.root,a.levels),indent=2))
