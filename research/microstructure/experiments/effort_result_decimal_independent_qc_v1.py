#!/usr/bin/env python3
"""Independent exact Decimal #15 minute effort-result summary QC. Research only."""
import argparse,csv,gzip,json,datetime as dt
from pathlib import Path
from decimal import Decimal
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def check(sym,day):
 if sym not in SYMS:raise ValueError("symbol")
 start=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}{day}.csv.gz"
 first=[None]*1440;last=[None]*1440;notional=[Decimal(0) for _ in range(1440)];counts=[0]*1440
 with gzip.open(path,"rt") as g:
  for r in csv.DictReader(g):
   minute=int((Decimal(r["timestamp"])-start)//60)
   if not 0<=minute<1440:continue
   px=Decimal(r["price"])
   if first[minute] is None:first[minute]=px
   last[minute]=px;notional[minute]+=Decimal(r["foreignNotional"]);counts[minute]+=1
 ratios=[];empty=zero=0
 for i in range(1440):
  if counts[i]==0:empty+=1;continue
  if notional[i]<=0:zero+=1;continue
  ratios.append(abs(last[i]-first[i])/first[i]*Decimal(10000)*Decimal(1000000)/notional[i])
 ratios.sort();n=len(ratios)
 return dict(symbol=sym,day=day,minutes=1440,fills=sum(counts),valid=n,empty=empty,zero_turnover=zero,
 median=str(ratios[n//2]) if n else None,p90=str(ratios[min(n-1,(n-1)*9//10)]) if n else None,
 qc="DECIMAL_INDEPENDENT_SUMMARY_NOT_PER_MINUTE_HASH")
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--date",required=True)
 a=p.parse_args();print(json.dumps(check(a.symbol,a.date)))
