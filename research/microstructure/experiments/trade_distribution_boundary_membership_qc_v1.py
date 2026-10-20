#!/usr/bin/env python3
"""Read-only per-fill timestamp/window membership parity: float primary vs Decimal exact."""
import argparse,csv,datetime as dt,gzip,json
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def check(sym,day,minutes=1440):
 if sym not in SYMS or not 1<=minutes<=1440:raise ValueError("scope")
 sec=int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
 lo=Decimal(sec);hi=lo+Decimal(minutes*60);fl=float(sec)*1000;fh=fl+minutes*60000
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}{day}.csv.gz"
 n=dec_n=float_n=0;window_mismatches=minute_mismatches=0;examples=[]
 with gzip.open(path,"rt") as f:
  for line in csv.DictReader(f):
   if line["symbol"]!=sym:raise ValueError("symbol mismatch")
   x=line["timestamp"];d=Decimal(x);v=float(x)*1000
   exact=lo<=d<hi;approx=fl<=v<fh
   n+=1;dec_n+=exact;float_n+=approx
   minute_wrong=False
   if exact and approx:
    im=int((d-lo)//Decimal(60));jm=int((v-fl)//60000)
    minute_wrong=im!=jm
   if exact!=approx:window_mismatches+=1
   if minute_wrong:minute_mismatches+=1
   if (exact!=approx or minute_wrong) and len(examples)<6:
    examples.append(dict(timestamp=x,decimal_in=exact,float_in=approx,minute_decimal=int((d-lo)//60),minute_float=int((v-fl)//60000)))
 return dict(symbol=sym,day=day,minutes=minutes,raw_records=n,decimal_in_window=dec_n,float_in_window=float_n,window_mismatches=window_mismatches,minute_bucket_mismatches=minute_mismatches,examples=examples,status="READ_ONLY_EXACT_TIMESTAMP_MEMBERSHIP_QC",scope="Comparing historical primary float(timestamp)*1000 to exact Decimal second timestamps; no price-size QC")
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--date",default="2026-05-21");p.add_argument("--minutes",type=int,default=1440);a=p.parse_args();print(json.dumps(check(a.symbol,a.date,a.minutes)))
