#!/usr/bin/env python3
"""Independent Decimal execution-gap and fill-size distribution QC; ex-post only."""
import argparse,csv,gzip,json,statistics,datetime as dt
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(symbol,day):
 if symbol not in SYMS:raise ValueError("symbol")
 p=Path("/data/cripta/datasets/raw/20260518_20260816")/symbol/"public_trades"/f"{symbol}{day}.csv.gz"
 start=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 values=[];positive=[];buys=sells=0;prev=None;ties=0
 with gzip.open(p,"rt") as f:
  for row in csv.DictReader(f):
   stamp=Decimal(row["timestamp"])
   if prev is not None and stamp<prev:raise ValueError("source reversed")
   if not start<=stamp<start+86400:continue
   if row["symbol"]!=symbol:raise ValueError("wrong instrument")
   if row["side"]=="Buy":buys+=1
   elif row["side"]=="Sell":sells+=1
   else:raise ValueError("wrong side")
   if prev is not None:
    d=(stamp-prev)*Decimal(1000)
    if d>0:positive.append(d)
    elif d==0:ties+=1
   prev=stamp
   values.append(Decimal(row["foreignNotional"]))
 ordered=sorted(values);n=len(ordered)
 if not n:raise ValueError("empty source")
 p95=ordered[int(Decimal("0.95")*(n-1))]
 p99=ordered[int(Decimal("0.99")*(n-1))]
 weighted=sum((v for v in ordered if v>=p99),Decimal(0))/sum(ordered,Decimal(0))
 return dict(symbol=symbol,date_utc=day,fills=n,buy_fills=buys,sell_fills=sells,
 positive_intertimestamp_gap_median_ms=str(statistics.median(positive)) if positive else None,
 fraction_adjacent_same_timestamp=str(Decimal(ties)/Decimal(n-1)) if n>1 else None,
 fill_notional_usd_p95_ex_post=str(p95),fill_notional_usd_p99_ex_post=str(p99),
 fraction_notional_fills_ge_ex_post_p99=str(weighted),
 status="INDEPENDENT_DECIMAL_EX_POST_DISTRIBUTION_NOT_CAUSAL",
 disclaimer="P95/P99 computed using all day fills and cannot be used as within-day as-of features")
if __name__=="__main__":
 x=argparse.ArgumentParser();x.add_argument("--symbol",required=True,choices=SYMS);x.add_argument("--date",required=True);a=x.parse_args();print(json.dumps(run(a.symbol,a.date)))
