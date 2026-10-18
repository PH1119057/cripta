#!/usr/bin/env python3
"""Read-only price-identity-aware top-N visible orderbook flow. Not cancellations or parent orders."""
import argparse,datetime as dt,json,zipfile,statistics
from pathlib import Path
from decimal import Decimal
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,minutes):
 start=int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()*1000)
 path=ROOT/sym/"orderbook"/f"2026-05-19_{sym}_ob200.data.zip"
 bid={};ask={};seen=False;events=0;snapshots=0;minute=[dict(bid_usdt=0.,ask_usdt=0.,paired=0) for _ in range(minutes)]
 with zipfile.ZipFile(path) as z:
  with z.open(z.namelist()[0]) as fh:
   for raw in fh:
    o=json.loads(raw);ts=int(o["ts"])
    if ts>=start+minutes*60000:break
    if ts<start:continue
    typ=o["type"];data=o["data"]
    if typ=="snapshot":
     bid={Decimal(p):Decimal(v) for p,v in data["b"] if Decimal(v)>0}
     ask={Decimal(p):Decimal(v) for p,v in data["a"] if Decimal(v)>0}
     seen=True;snapshots+=1;continue
    if not seen:continue
    oldb=sorted(bid,reverse=True)[:5];olda=sorted(ask)[:5]
    # Snapshot rebuild and exact price-key updates. Flow is computed on
    # intersection of old/new top-five *price identities*, not rank indices.
    beforeb=bid.copy();beforea=ask.copy()
    for src,book in ((data["b"],bid),(data["a"],ask)):
     for pp,qq in src:
      p=Decimal(pp);q=Decimal(qq)
      if q<=0:book.pop(p,None)
      else:book[p]=q
    newb=sorted(bid,reverse=True)[:5];newa=sorted(ask)[:5]
    commonb=set(oldb)&set(newb);commona=set(olda)&set(newa)
    k=(ts-start)//60000;m=minute[k]
    # Only common price keys prevent rank-reindexing from creating artificial
    # liquidity additions/removals. Lost/entered keys are excluded as coverage.
    for p in commonb:m["bid_usdt"]+=float(p*(bid[p]-beforeb[p]))
    for p in commona:m["ask_usdt"]+=float(p*(ask[p]-beforea[p]))
    m["paired"]+=1;events+=1
 totals={"bid_usdt":sum(m["bid_usdt"] for m in minute),"ask_usdt":sum(m["ask_usdt"] for m in minute)}
 return dict(symbol=sym,minutes=minutes,snapshots=snapshots,delta_events=events,minutes_with_updates=sum(m["paired"]>0 for m in minute),
             fixed_price_key_flow_bid_usdt=round(totals["bid_usdt"],2),fixed_price_key_flow_ask_usdt=round(totals["ask_usdt"],2),
             signed_visible_flow_usdt=round(totals["bid_usdt"]-totals["ask_usdt"],2),sample_minute0=minute[0],
             limitations=["Changes of top-five membership excluded, so not total book OFI","Visible quantity change cannot separate fills and cancels","Book snapshot reinitializes reference; no flow inferred over resets","Values are descriptive for completed minutes and exclude other venues"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--minutes",type=int,default=60);a=p.parse_args();print(json.dumps(run(a.symbol,a.minutes)))
