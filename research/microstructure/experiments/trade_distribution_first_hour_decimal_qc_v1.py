#!/usr/bin/env python3
"""Read-only bounded first-N-minute independent Decimal distribution, stdout JSON."""
import argparse,csv,gzip,json,statistics,datetime as dt
from decimal import Decimal
from pathlib import Path
S=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def calculate(symbol,day,minutes=60):
 if symbol not in S or not 1<=minutes<=1440:raise ValueError("scope")
 t0=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/symbol/"public_trades"/f"{symbol}{day}.csv.gz"
 qty=[];stamps=[];buy=sell=0
 with gzip.open(path,"rt") as f:
  for r in csv.DictReader(f):
   t=Decimal(r["timestamp"])
   if t<t0 or t>=t0+minutes*60:continue
   if r["symbol"]!=symbol or r["side"] not in ("Buy","Sell"):raise ValueError("identity")
   stamps.append(t);qty.append(Decimal(r["foreignNotional"]))
   if r["side"]=="Buy":buy+=1
   else:sell+=1
 if not qty:raise ValueError("empty")
 if any(b<a for a,b in zip(stamps,stamps[1:])):raise ValueError("clock inversion")
 gaps=[(b-a)*1000 for a,b in zip(stamps,stamps[1:]) if b>a]
 ties=sum(b==a for a,b in zip(stamps,stamps[1:]))
 seq=sorted(qty);p95=seq[int(Decimal(".95")*(len(seq)-1))];p99=seq[int(Decimal(".99")*(len(seq)-1))]
 return dict(symbol=symbol,date=day,minutes=minutes,fills=len(qty),buy_fills=buy,sell_fills=sell,positive_gap_median_ms=str(statistics.median(gaps)) if gaps else None,tie_fraction=str(Decimal(ties)/Decimal(len(qty)-1)) if len(qty)>1 else None,p95=str(p95),p99=str(p99),weighted_share_ge_p99=str(sum((x for x in seq if x>=p99),Decimal(0))/sum(seq,Decimal(0))),status="INDEPENDENT_DECIMAL_FIRST_HOUR_QC_ONLY")
if __name__=="__main__":
 a=argparse.ArgumentParser();a.add_argument("--symbol",required=True,choices=S);a.add_argument("--date",default="2026-05-21");a.add_argument("--minutes",type=int,default=60);z=a.parse_args();print(json.dumps(calculate(z.symbol,z.date,z.minutes)))
