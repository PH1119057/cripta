#!/usr/bin/env python3
"""#30 top-five book-depth shock/recovery candidate; quote-USDT and right-censoring, read-only."""
import argparse,datetime as dt,json,zipfile
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
def depth(book,reverse):
 return sum((p*book[p] for p in sorted(book,reverse=reverse)[:5]),Decimal(0))
def run(sym,minutes,window_ms=60000,day="2026-05-19"):
 start=int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()*1000)
 path=ROOT/sym/"orderbook"/f"{day}_{sym}_ob200.data.zip"
 books=[{},{}];last=[None,None];active=[None,None]
 results=[{"episodes":0,"recovered":0,"expired":0,"censored":0,"recovery_ms":[]} for _ in range(2)]
 snapshots=deltas=0
 with zipfile.ZipFile(path) as z:
  with z.open(z.namelist()[0]) as f:
   for raw in f:
    o=json.loads(raw);ts=int(o["ts"])
    if ts>=start+minutes*60000:break
    if ts<start:continue
    d=o["data"]
    if o["type"]=="snapshot":
     for idx in (0,1):
      if active[idx] is not None:results[idx]["censored"]+=1;active[idx]=None
     books=[{Decimal(p):Decimal(q) for p,q in d[k] if Decimal(q)>0} for k in ("b","a")]
     last=[depth(books[0],True),depth(books[1],False)]
     snapshots+=1;continue
    if not snapshots:continue
    for idx,k in enumerate(("b","a")):
     b=books[idx];r=results[idx]
     for pp,qq in d[k]:
      p=Decimal(pp);q=Decimal(qq)
      if q<=0:b.pop(p,None)
      else:b[p]=q
     current=depth(b,idx==0)
     pending=active[idx]
     if pending is not None:
      t,target=pending
      if ts-t>window_ms:r["expired"]+=1;active[idx]=None
      elif current>=target:r["recovered"]+=1;r["recovery_ms"].append(ts-t);active[idx]=None
     if active[idx] is None and last[idx] is not None and current<last[idx]:
      active[idx]=(ts,last[idx]);r["episodes"]+=1
     last[idx]=current
    deltas+=1
 for idx in (0,1):
  if active[idx] is not None:results[idx]["censored"]+=1
 for r in results:
  a=sorted(r.pop("recovery_ms"))
  r["median_recovery_ms"]=a[len(a)//2] if a else None
  r["qc_partition"]=r["episodes"]==r["recovered"]+r["expired"]+r["censored"]
 return dict(symbol=sym,minutes=minutes,snapshots=snapshots,deltas=deltas,
  scope="exact venue instrument, top-five price-key depths by side, summed quote USDT",
  shock="any observed fall in top-five side depth from previous message; one pending episode per side",
  recovery="side depth at or above pre-fall value within 60000 ms",
  bid=results[0],ask=results[1],status="TOP5_BOOK_STOCK_RECOVERY_CANDIDATE_NOT_ORDER_LEVEL_RESILIENCY",
  caveats=["Membership/price re-ranking changes top-five stock without proved order cancellations","Same total depth may reappear at different levels or from different participants","One pending episode per side, no overlap independence or provider-latency guarantees","Censored episodes not counted as failures","No future/PnL/OOS conclusion"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--minutes",type=int,default=10);p.add_argument("--date",default="2026-05-19");a=p.parse_args();print(json.dumps(run(a.symbol,a.minutes,day=a.date)))
