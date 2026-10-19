#!/usr/bin/env python3
"""#12 candidate: visible same-price depletion vs replenishment, not proven cancellation."""
import argparse,datetime as dt,json,zipfile
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def calc(symbol,minutes):
 start=int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()*1000)
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/symbol/"orderbook"/f"2026-05-19_{symbol}_ob200.data.zip"
 books=[{},{}];stats=[dict(decrease=Decimal(0),increase=Decimal(0),down_events=0,up_events=0) for _ in range(2)]
 snaps=0;deltas=0
 with zipfile.ZipFile(path) as z:
  with z.open(z.namelist()[0]) as fh:
   for raw in fh:
    event=json.loads(raw);ts=int(event["ts"])
    if ts>=start+minutes*60000:break
    if ts<start:continue
    d=event["data"]
    if event["type"]=="snapshot":
     books=[{Decimal(p):Decimal(q) for p,q in d[k] if Decimal(q)>0} for k in ("b","a")];snaps+=1;continue
    if not snaps:continue
    for ix,k in enumerate(("b","a")):
     b=books[ix];s=stats[ix]
     for pp,qq in d[k]:
      p=Decimal(pp);q=Decimal(qq);old=b.get(p,Decimal(0))
      if old>0:
       change=p*(q-old)
       if change<0:s["decrease"]-=change;s["down_events"]+=1
       elif change>0:s["increase"]+=change;s["up_events"]+=1
      if q<=0:b.pop(p,None)
      else:b[p]=q
    deltas+=1
 return dict(symbol=symbol,minutes=minutes,snapshots=snaps,delta_events=deltas,unit="USDT quote notional at exact price",sides={name:{k:str(v) for k,v in s.items()} for name,s in zip(("bid","ask"),stats)},status="VISIBLE_DEPLETION_CANDIDATE_ONLY",cautions=["Cannot separate cancellation from executed trade","Only levels with prior positive quantity; new-level additions deliberately excluded","No multi-day or predictive validation"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",choices=SYMS,required=True);p.add_argument("--minutes",type=int,default=10);a=p.parse_args();print(json.dumps(calc(a.symbol,a.minutes)))
