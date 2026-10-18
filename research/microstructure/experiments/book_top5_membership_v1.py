#!/usr/bin/env python3
"""Research-only #10: decompose exact-price top-five book changes and membership reclassification."""
import argparse,datetime as dt,json,zipfile
from pathlib import Path
from decimal import Decimal
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,minutes):
 start=int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()*1000)
 path=ROOT/sym/"orderbook"/f"2026-05-19_{sym}_ob200.data.zip"
 b={};a={};snap=0;deltas=0
 # All three components are measured in quote USD; membership is a boundary/re-ranking effect.
 sums={side:{k:Decimal(0) for k in ("common","entry","exit","entry_from_outside","exit_to_outside","true_added","true_removed")} for side in ("bid","ask")}
 mins=set()
 with zipfile.ZipFile(path) as z:
  with z.open(z.namelist()[0]) as fh:
   for raw in fh:
    o=json.loads(raw);ts=int(o["ts"])
    if ts>=start+minutes*60000:break
    if ts<start:continue
    data=o["data"]
    if o["type"]=="snapshot":
     b={Decimal(p):Decimal(q) for p,q in data["b"] if Decimal(q)>0}
     a={Decimal(p):Decimal(q) for p,q in data["a"] if Decimal(q)>0}
     snap+=1;continue
    if snap==0:continue
    for side,book,updates,reverse in (("bid",b,data["b"],True),("ask",a,data["a"],False)):
     oldkeys=set(sorted(book,reverse=reverse)[:5]);prev={p:book[p] for p in oldkeys}
     before_keys=set(book)
     for pp,qq in updates:
      p=Decimal(pp);q=Decimal(qq)
      if q<=0:book.pop(p,None)
      else:book[p]=q
     newkeys=set(sorted(book,reverse=reverse)[:5])
     common=oldkeys&newkeys
     enter=newkeys-oldkeys
     leave=oldkeys-newkeys
     m=sums[side]
     m["common"]+=sum((p*(book[p]-prev[p]) for p in common),Decimal(0))
     m["entry"]+=sum((p*book[p] for p in enter),Decimal(0))
     m["exit"]-=sum((p*prev[p] for p in leave),Decimal(0))
     m["entry_from_outside"]+=sum((p*book[p] for p in enter if p in before_keys),Decimal(0))
     m["exit_to_outside"]-=sum((p*prev[p] for p in leave if p in book),Decimal(0))
     m["true_added"]+=sum((p*book[p] for p in enter if p not in before_keys),Decimal(0))
     m["true_removed"]-=sum((p*prev[p] for p in leave if p not in book),Decimal(0))
    deltas+=1;mins.add((ts-start)//60000)
 out={}
 for side in ("bid","ask"):
  d=sums[side];out[side]={k:round(float(v),2) for k,v in d.items()}
  out[side]["top5_stock_change"]=round(float(d["common"]+d["entry"]+d["exit"]),2)
 return dict(symbol=sym,minutes=minutes,snapshots=snap,delta_events=deltas,active_minutes=len(mins),components=out,
 limitations=["Top-five membership entry/exit includes rank-boundary reclassification, not all genuine market new/cancel flows","This is decomposed visible stock change, not standard price-change-sensitive OFI","No fill/cancel attribution, predictive validation, or strategy policy"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--minutes",type=int,default=10);a=p.parse_args();print(json.dumps(run(a.symbol,a.minutes)))
