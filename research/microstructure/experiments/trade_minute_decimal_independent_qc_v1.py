#!/usr/bin/env python3
"""Independent Decimal QC of #1/#2/#3/#5/#6/#7 trade-minute factors; no writes."""
import argparse,csv,gzip,json,datetime as dt
from decimal import Decimal
from pathlib import Path
COHORT=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def calculate(symbol,date):
 if symbol not in COHORT:raise ValueError("symbol")
 start=Decimal(int(dt.datetime.strptime(date,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 counts=[0]*1440
 buys=[Decimal(0)]*1440
 sells=[Decimal(0)]*1440
 p=Path("/data/cripta/datasets/raw/20260518_20260816")/symbol/"public_trades"/f"{symbol}{date}.csv.gz"
 prev=None
 with gzip.open(p,"rt") as file:
  for r in csv.DictReader(file):
   t=Decimal(r["timestamp"])
   if prev is not None and t<prev:raise ValueError("out of order")
   prev=t
   i=int((t-start)//Decimal(60))
   if not 0<=i<1440:continue
   if r["symbol"]!=symbol or r["side"] not in ("Buy","Sell"):raise ValueError("row identity")
   v=Decimal(r["foreignNotional"]);counts[i]+=1
   if r["side"]=="Buy":buys[i]+=v
   else:sells[i]+=v
 totals=[buys[i]+sells[i] for i in range(1440)]
 delta=[buys[i]-sells[i] for i in range(1440)]
 cvd=Decimal(0);prev_d=None;prev_c=None
 diffs=[];accels=[];cvds=[];rv=[]
 for i in range(1440):
  cvd+=delta[i];cvds.append(cvd)
  diffs.append(counts[i]-prev_c if prev_c is not None else None)
  accels.append(delta[i]-prev_d if prev_d is not None else None)
  prev_c=counts[i];prev_d=delta[i]
  baseline=sum(totals[i-60:i],Decimal(0))/60 if i>=60 else None
  rv.append(totals[i]/baseline if baseline is not None and baseline>0 else None)
 return dict(symbol=symbol,date=date,fills=sum(counts),active_minutes=sum(x>0 for x in counts),
 minutes=1440,rvol_eligible=sum(x is not None for x in rv),total_buy_usd=str(sum(buys,Decimal(0))),
 total_sell_usd=str(sum(sells,Decimal(0))),last_cvd_usd=str(cvds[-1]),
 minute0=dict(trade_rate=counts[0],trade_rate_accel=diffs[0],delta_usd=str(delta[0]),delta_accel=None,cvd_usd=str(cvds[0])),
 minute60=dict(trade_rate=counts[60],trade_rate_accel=diffs[60],delta_usd=str(delta[60]),delta_accel=str(accels[60]),cvd_usd=str(cvds[60]),rvol=str(rv[60]) if rv[60] is not None else None),
 last=dict(trade_rate=counts[-1],trade_rate_accel=diffs[-1],delta_usd=str(delta[-1]),delta_accel=str(accels[-1]),cvd_usd=str(cvds[-1]),rvol=str(rv[-1]) if rv[-1] is not None else None),
 qc="DECIMAL_INDEPENDENT_AGGREGATES_AND_SPOT_ROWS_ONLY")
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=COHORT);p.add_argument("--date",required=True);a=p.parse_args();print(json.dumps(calculate(a.symbol,a.date)))
