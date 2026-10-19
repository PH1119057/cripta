#!/usr/bin/env python3
"""Independent Decimal #16 absorption-proxy cohort QC, read-only."""
import argparse,csv,gzip,json,datetime as dt
from decimal import Decimal
from pathlib import Path
COHORT=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,day):
 if sym not in COHORT:raise ValueError("symbol")
 zero=Decimal(0);origin=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}{day}.csv.gz"
 first=[None]*1440;last=[None]*1440;flow=[zero for _ in range(1440)];count=[0]*1440
 with gzip.open(path,"rt") as f:
  for r in csv.DictReader(f):
   m=int((Decimal(r["timestamp"])-origin)//60)
   if not 0<=m<1440:continue
   if r["side"] not in ("Buy","Sell"):raise ValueError("side")
   p=Decimal(r["price"]);v=Decimal(r["foreignNotional"])
   if first[m] is None:first[m]=p
   last[m]=p;flow[m]+=v if r["side"]=="Buy" else -v;count[m]+=1
 eligible=large=against=aligned=unchanged=unknown=bid_resisted=ask_resisted=0
 for i in range(60,1440):
  if count[i]==0 or flow[i]==0:unknown+=1;continue
  hist=sorted(abs(flow[j]) for j in range(i-60,i) if count[j]>0)
  if len(hist)<30:unknown+=1;continue
  eligible+=1;quantile=hist[int(Decimal("0.9")*(len(hist)-1))]
  if abs(flow[i])<quantile:continue
  large+=1;direction=flow[i]*(last[i]-first[i])
  if direction<0:
   against+=1
   if flow[i]>0:bid_resisted+=1
   else:ask_resisted+=1
  elif direction>0:aligned+=1
  else:unchanged+=1
 return dict(symbol=sym,day=day,eligible=eligible,unusual_flow=large,against_flow_price_move=against,aligned_flow_price_move=aligned,zero_price_move=unchanged,unavailable=unknown,buy_flow_resisted=bid_resisted,sell_flow_resisted=ask_resisted,partition=large==against+aligned+unchanged,scope="independent entire-day summary not per-minute persisted hashes")
if __name__=="__main__":
 a=argparse.ArgumentParser();a.add_argument("--symbol",required=True,choices=COHORT);a.add_argument("--date",required=True);v=a.parse_args();print(json.dumps(run(v.symbol,v.date)))
