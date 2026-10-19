#!/usr/bin/env python3
"""Independent first-hour per-minute top-book spread/near-mid depths QC; read-only."""
import argparse,json,zipfile,statistics,datetime as dt
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,minutes):
 base=int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()*1000)
 p=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"orderbook"/f"2026-05-19_{sym}_ob200.data.zip"
 books=[{},{}];last={};messages=snapshots=0
 with zipfile.ZipFile(p) as z:
  with z.open(z.namelist()[0]) as fh:
   for raw in fh:
    e=json.loads(raw);ts=int(e["ts"])
    if ts>=base+minutes*60000:break
    if ts<base:continue
    if e["type"]=="snapshot":
     books=[{Decimal(price):Decimal(size) for price,size in e["data"][key] if Decimal(size)>0} for key in ("b","a")];snapshots+=1
    else:
     for ix,key in enumerate(("b","a")):
      book=books[ix]
      for price,size in e["data"][key]:
       px=Decimal(price);q=Decimal(size)
       if q>0:book[px]=q
       else:book.pop(px,None)
    bid=max(books[0]);ask=min(books[1]);mid=(bid+ask)/2
    lo=mid*Decimal("0.9995");hi=mid*Decimal("1.0005")
    bd=sum((price*qty for price,qty in books[0].items() if lo<=price<=mid),Decimal(0))
    ad=sum((price*qty for price,qty in books[1].items() if mid<=price<=hi),Decimal(0))
    idx=(ts-base)//60000
    last[idx]=(float((ask-bid)/mid*10000),float((bd-ad)/(bd+ad)) if bd+ad else None)
    messages+=1
 vals=list(last.values())
 return dict(symbol=sym,minutes=minutes,source_messages=messages,snapshots=snapshots,minute_states=len(vals),
 median_spread_bps=round(statistics.median(v[0] for v in vals),4),
 median_imbalance=round(statistics.median(v[1] for v in vals if v[1] is not None),4),
 status="INDEPENDENT_DECIMAL_FIRST_HOUR_QC")
if __name__=="__main__":
 a=argparse.ArgumentParser();a.add_argument("--symbol",required=True,choices=SYMS);a.add_argument("--minutes",type=int,default=60);v=a.parse_args();print(json.dumps(run(v.symbol,v.minutes)))
