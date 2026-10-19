#!/usr/bin/env python3
"""Independent Decimal audit of #4 large-fill share and #8 timestamp-side streaks.
Read-only historical public trades. Sample aggregation only, NOT order attribution.
"""
import argparse,csv,gzip,json,statistics,datetime as dt
from pathlib import Path
from decimal import Decimal
COHORT=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(symbol,day):
 if symbol not in COHORT:raise ValueError("symbol")
 day_start=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/symbol/"public_trades"/f"{symbol}{day}.csv.gz"
 buckets=[[] for _ in range(1440)]
 groups=[];total=0;prev_ts=None
 with gzip.open(path,"rt") as stream:
  for r in csv.DictReader(stream):
   ts=Decimal(r["timestamp"])
   if prev_ts is not None and ts<prev_ts:raise ValueError("nonmonotone timestamp")
   prev_ts=ts
   k=int((ts-day_start)//60)
   if not 0<=k<1440:continue
   if r["symbol"]!=symbol or r["side"] not in ("Buy","Sell"):raise ValueError("invalid row")
   value=Decimal(r["foreignNotional"]);buckets[k].append(value);total+=1
   key=(ts,r["side"])
   if groups and groups[-1][0]==key:
    groups[-1][1]+=1
   else:groups.append([key,1])
 if not groups:raise ValueError("no fills")
 streaks=[];runlen=0;side=None
 for g in groups:
  if g[0][1]==side:runlen+=1
  else:
   if runlen:streaks.append(runlen)
   runlen=1;side=g[0][1]
 streaks.append(runlen)
 streaks.sort()
 ratios=[]
 for i in range(60,1440):
  history=sorted(z for bucket in buckets[i-60:i] for z in bucket)
  if len(history)<100 or not buckets[i]:continue
  cutoff=history[int(Decimal("0.99")*(len(history)-1))]
  current=buckets[i];den=sum(current,Decimal(0))
  if den:ratios.append(sum((v for v in current if v>=cutoff),Decimal(0))/den)
 ratios.sort()
 return dict(symbol=symbol,date=day,fills=total,groups=len(groups),groups_multiple=sum(g[1]>1 for g in groups),
 streak_count=len(streaks),streak_median=str(statistics.median(streaks)),streak_p90=streaks[int(Decimal("0.9")*(len(streaks)-1))],
 streak_max=max(streaks),large_share_eligible=len(ratios),
 large_share_median=str(statistics.median(ratios)) if ratios else None,
 large_share_p90=str(ratios[int(Decimal("0.9")*(len(ratios)-1))]) if ratios else None,
 limitations=["Independent Decimal exact-timestamp and exact-foreignNotional implementation","Full previous 60 min, ex-post descriptive results only","Timestamp+side groups are not parent orders"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=COHORT);p.add_argument("--date",required=True);a=p.parse_args()
 print(json.dumps(run(a.symbol,a.date)))
