#!/usr/bin/env python3
"""#14 exploratory signed-flow and next-completed-minute price response; future labels isolated."""
import argparse,csv,gzip,datetime as dt,json
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def calc(symbol,day="2026-05-19"):
 start=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/symbol/"public_trades"/f"{symbol}{day}.csv.gz"
 mins=[{"signed":Decimal(0),"turnover":Decimal(0),"last":None,"count":0} for _ in range(1440)]
 with gzip.open(path,"rt") as f:
  for r in csv.DictReader(f):
   i=int((Decimal(r["timestamp"])-start)//60)
   if not 0<=i<1440:continue
   price=Decimal(r["price"]);v=Decimal(r["foreignNotional"]);d=Decimal(1) if r["side"]=="Buy" else Decimal(-1)
   x=mins[i];x["signed"]+=d*v;x["turnover"]+=v;x["last"]=price;x["count"]+=1
 prev=None;valid=0;favorable=0;unfavorable=0;flat=0;zero=0;no_data=0;signed_effects=[]
 for i in range(1439):
  a=mins[i];b=mins[i+1]
  # Feature as of end of i: minute signed-flow; future label: next-minute return from
  # last price of i to last price of i+1, available only AFTER i+1 closes.
  if not a["count"] or not b["count"] or a["signed"]==0:
   no_data+=1;continue
  r=(b["last"]-a["last"])/a["last"]*10000
  effect=(Decimal(1) if a["signed"]>0 else Decimal(-1))*r
  signed_effects.append(effect);valid+=1
  if effect>0:favorable+=1
  elif effect<0:unfavorable+=1
  else:flat+=1
 signed_effects.sort()
 return dict(symbol=symbol,source=str(path),minutes=1440,pairs_checked=1439,valid=valid,favorable=favorable,unfavorable=unfavorable,flat=flat,no_data=no_data,
  median_signed_next_minute_return_bps=str(signed_effects[valid//2]) if valid else None,
  metric="sign(current completed 1m taker notional delta) * next completed 1m close-to-close return in basis points",
  available_after_feature="end_of_minute_i",future_label_available_after="end_of_minute_i_plus_1",
  status="EXPLORATORY_FUTURE_LABEL_NOT_CAUSAL_FEATURE",
  cautions=["Future return is a label, not an input feature","Minute-level pairing cannot identify physical single-trade price impact","No control matching, fee net PnL, OOS or inference of alpha"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--date",default="2026-05-19");a=p.parse_args();print(json.dumps(calc(a.symbol,a.date)))
