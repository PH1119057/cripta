#!/usr/bin/env python3
"""Independent #21 previous-60-minute print excursion and reclaim count QC."""
import argparse,csv,gzip,json,datetime as dt
from collections import deque
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,day):
 if sym not in SYMS:raise ValueError("symbol")
 start=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 source=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}{day}.csv.gz"
 candles=[[] for _ in range(1440)]
 with gzip.open(source,"rt") as f:
  for r in csv.DictReader(f):
   idx=int((Decimal(r["timestamp"])-start)//60)
   if 0<=idx<1440:candles[idx].append(Decimal(r["price"]))
 result={"eligible_minutes":0,"missing_or_incomplete_windows":0,"high_sweeps":0,"high_reclaims_after_sweep":0,"low_sweeps":0,"low_reclaims_after_sweep":0}
 prior=deque(maxlen=60)
 for i in range(1440):
  today=candles[i]
  if i>=60:
   if len(prior)<60 or not today or any(not x for x in prior):result["missing_or_incomplete_windows"]+=1
   else:
    result["eligible_minutes"]+=1
    floor=min(min(block) for block in prior);ceil=max(max(block) for block in prior)
    high_first=next((j for j,p in enumerate(today) if p>ceil),None)
    low_first=next((j for j,p in enumerate(today) if p<floor),None)
    if high_first is not None:
     result["high_sweeps"]+=1
     if any(p<=ceil for p in today[high_first+1:]):result["high_reclaims_after_sweep"]+=1
    if low_first is not None:
     result["low_sweeps"]+=1
     if any(p>=floor for p in today[low_first+1:]):result["low_reclaims_after_sweep"]+=1
  prior.append(today)
 return dict(symbol=sym,date=day,**result,scope="Independent print-order reconstruction; not book sweep nor guaranteed same-timestamp physical order")
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--date",required=True);a=p.parse_args();print(json.dumps(run(a.symbol,a.date)))
