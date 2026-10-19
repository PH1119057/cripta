#!/usr/bin/env python3
"""Read-only exploratory event-time joins: same-price book depletion vs observed fills.
Do not infer cancellations by subtraction: feeds have no guaranteed common sequence."""
import argparse,csv,datetime as dt,gzip,json,zipfile,bisect
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
def run(symbol,minutes):
 start=int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()*1000)
 tradepath=ROOT/symbol/"public_trades"/f"{symbol}2026-05-19.csv.gz"
 bookpath=ROOT/symbol/"orderbook"/f"2026-05-19_{symbol}_ob200.data.zip"
 # Exact price, same passive side: Buy executions consume Ask, Sell consume Bid.
 trades=defaultdict(list);ntrades=0
 with gzip.open(tradepath,"rt") as f:
  for row in csv.DictReader(f):
   ts=int(Decimal(row["timestamp"])*1000)
   if not start<=ts<start+minutes*60000:continue
   key=(("a" if row["side"]=="Buy" else "b"),row["price"])
   trades[key].append(ts);ntrades+=1
 for arr in trades.values():arr.sort()
 b={};a={};snap=0;events=0;drops=0;matched=0;unmatched=0
 byside={k:{"decrease_events":0,"same_price_trade_within_1000ms":0,"without_trade_within_1000ms":0} for k in ("b","a")}
 with zipfile.ZipFile(bookpath) as z:
  with z.open(z.namelist()[0]) as f:
   for raw in f:
    e=json.loads(raw);ts=int(e["ts"])
    if ts>=start+minutes*60000:break
    if ts<start:continue
    data=e["data"]
    if e["type"]=="snapshot":
     b={Decimal(p):Decimal(q) for p,q in data["b"] if Decimal(q)>0}
     a={Decimal(p):Decimal(q) for p,q in data["a"] if Decimal(q)>0}
     snap+=1;continue
    if not snap:continue
    for side,book in (("b",b),("a",a)):
     for pp,qq in data[side]:
      p=Decimal(pp);q=Decimal(qq);old=book.get(p,Decimal(0))
      if old>q:
       drops+=1;byside[side]["decrease_events"]+=1
       arr=trades.get((side,str(p)),[])
       j=bisect.bisect_left(arr,ts-1000)
       present=j<len(arr) and arr[j]<=ts+1000
       if present:matched+=1;byside[side]["same_price_trade_within_1000ms"]+=1
       else:unmatched+=1;byside[side]["without_trade_within_1000ms"]+=1
      if q<=0:book.pop(p,None)
      else:book[p]=q
    events+=1
 return dict(symbol=symbol,minutes=minutes,trades=ntrades,snapshots=snap,book_deltas=events,
             reductions=drops,with_near_trade=matched,without_near_trade=unmatched,
             by_passive_side=byside,qc_partition=(drops==matched+unmatched),
             status="TEMPORAL_ASSOCIATION_ONLY_NOT_CANCELLATION_ATTRIBUTION",
             limits=["1-second symmetric window is exploratory and includes future trades; NOT an as-of trading feature",
                     "Feeds may have publication lag / same-ms ordering ambiguity",
                     "A trade may match multiple book updates; this is presence, not quantity allocation",
                     "No near trade is not proof of cancellation; match is not proof of execution cause"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS)
 p.add_argument("--minutes",type=int,default=10);args=p.parse_args()
 print(json.dumps(run(args.symbol,args.minutes)))
