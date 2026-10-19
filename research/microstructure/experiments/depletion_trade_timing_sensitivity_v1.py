#!/usr/bin/env python3
"""#12 read-only sensitivity of book reductions to exact-price passive-side trade timing."""
import argparse,bisect,csv,datetime as dt,gzip,json,zipfile
from decimal import Decimal
from collections import defaultdict
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
WINDOWS=(0,100,500,1000,5000)
def run(sym,minutes):
 start=int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()*1000)
 trades=defaultdict(list);n=0
 with gzip.open(ROOT/sym/"public_trades"/f"{sym}2026-05-19.csv.gz","rt") as f:
  for row in csv.DictReader(f):
   t=int(Decimal(row["timestamp"])*1000)
   if start<=t<start+minutes*60000:
    trades[(("a" if row["side"]=="Buy" else "b"),Decimal(row["price"]))].append(t);n+=1
 for a in trades.values():a.sort()
 buckets={str(w):{"symmetric":0,"past_only":0,"future_only":0} for w in WINDOWS}
 books=[{},{}];snap=0;deltas=0;decreases=0
 with zipfile.ZipFile(ROOT/sym/"orderbook"/f"2026-05-19_{sym}_ob200.data.zip") as z:
  with z.open(z.namelist()[0]) as f:
   for raw in f:
    e=json.loads(raw);t=int(e["ts"])
    if t>=start+minutes*60000:break
    if t<start:continue
    data=e["data"]
    if e["type"]=="snapshot":
     books=[{Decimal(p):Decimal(q) for p,q in data[k] if Decimal(q)>0} for k in ("b","a")];snap+=1;continue
    if not snap:continue
    for ix,k in enumerate(("b","a")):
     book=books[ix]
     for pp,qq in data[k]:
      p=Decimal(pp);q=Decimal(qq)
      if book.get(p,Decimal(0))>q:
       decreases+=1;times=trades.get((k,p),())
       for w in WINDOWS:
        left=bisect.bisect_left(times,t-w);mid=bisect.bisect_right(times,t);right=bisect.bisect_right(times,t+w)
        v=buckets[str(w)]
        v["past_only"]+=int(left<mid)
        v["future_only"]+=int(mid<right)
        v["symmetric"]+=int(left<right)
      if q<=0:book.pop(p,None)
      else:book[p]=q
    deltas+=1
 return dict(symbol=sym,minutes=minutes,trades=n,book_deltas=deltas,snapshots=snap,price_level_reductions=decreases,matched=buckets,
  qc_monotone=all(buckets[str(a)]["symmetric"]<=buckets[str(b)]["symmetric"] for a,b in zip(WINDOWS,WINDOWS[1:])),
  status="TIMING_SENSITIVITY_NOT_FILL_CANCEL_ATTRIBUTION",
  caveats=["Time windows use exchange timestamps, not verified observation or publication timestamps","Past-only is chronological by timestamp, not proven known to trading actor","Symmetric windows include hindsight future events","Matching trade can be reused for multiple book reductions; no quantity reconciliation","Cannot estimate cancelled volume from unmatched events"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--minutes",type=int,default=10);a=p.parse_args()
 print(json.dumps(run(a.symbol,a.minutes)))
