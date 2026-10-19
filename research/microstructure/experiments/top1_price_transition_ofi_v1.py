#!/usr/bin/env python3
"""Research-only top-of-book OFI, exact best-price transitions; stdout, no writes."""
import argparse,datetime as dt,json,zipfile
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
def ofi(sym,minutes):
 start=int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()*1000)
 path=ROOT/sym/"orderbook"/f"2026-05-19_{sym}_ob200.data.zip"
 books=[{},{}];prev=None;events=0;snapshots=0
 totals=[Decimal(0),Decimal(0)];categories={"bid_up":0,"bid_flat":0,"bid_down":0,"ask_down":0,"ask_flat":0,"ask_up":0}
 with zipfile.ZipFile(path) as z:
  with z.open(z.namelist()[0]) as fh:
   for raw in fh:
    m=json.loads(raw);ts=int(m["ts"])
    if ts>=start+minutes*60000:break
    if ts<start:continue
    d=m["data"]
    if m["type"]=="snapshot":
     books=[{Decimal(p):Decimal(q) for p,q in d[k] if Decimal(q)>0} for k in ("b","a")]
     snapshots+=1;prev=None
     continue
    for book,changes in zip(books,(d["b"],d["a"])):
     for pp,qq in changes:
      p=Decimal(pp);q=Decimal(qq)
      if q<=0:book.pop(p,None)
      else:book[p]=q
    if not books[0] or not books[1]:raise ValueError("empty book")
    bp=max(books[0]);ap=min(books[1]);now=((bp,books[0][bp]),(ap,books[1][ap]))
    if prev is not None:
     pb,pbv=prev[0];pa,pav=prev[1];nb,nbv=now[0];na,nav=now[1]
     bid=nbv if nb>pb else nbv-pbv if nb==pb else -pbv
     ask=nav if na<pa else nav-pav if na==pa else -pav
     totals[0]+=bid;totals[1]+=ask
     categories["bid_up" if nb>pb else "bid_flat" if nb==pb else "bid_down"]+=1
     categories["ask_down" if na<pa else "ask_flat" if na==pa else "ask_up"]+=1
     events+=1
    prev=now
 return {"symbol":sym,"minutes":minutes,"snapshots":snapshots,"paired_deltas":events,"bid_ofi_base":str(totals[0]),"ask_ofi_base":str(totals[1]),"ofi_bid_minus_ask_base":str(totals[0]-totals[1]),"price_cases":categories,"status":"TOP1_CAUSAL_SMOKE_ONLY","warnings":["Best-level OFI only, not full multi-level OFI","One observation after each delta; snapshot resets break pairwise continuity","No fill/cancellation identification; no predictive claim"]}
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--minutes",type=int,default=10);a=p.parse_args();print(json.dumps(ofi(a.symbol,a.minutes)))
