#!/usr/bin/env python3
"""#16 cautious absorption candidate using own-symbol prior60m signed-flow baseline."""
import argparse,csv,gzip,datetime as dt,json
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym):
 start=Decimal(int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()))
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}2026-05-19.csv.gz"
 m=[dict(first=None,last=None,delta=Decimal(0),turnover=Decimal(0),count=0) for _ in range(1440)]
 with gzip.open(path,"rt") as f:
  for r in csv.DictReader(f):
   i=int((Decimal(r["timestamp"])-start)//60)
   if not 0<=i<1440:continue
   x=m[i];p=Decimal(r["price"]);v=Decimal(r["foreignNotional"])
   if x["first"] is None:x["first"]=p
   x["last"]=p;x["delta"]+=v if r["side"]=="Buy" else -v;x["turnover"]+=v;x["count"]+=1
 eligible=0;unusual=0;no_follow=0;aligned=0;flat=0;unknown=0;long_absorb=0;short_absorb=0
 for i in range(60,1440):
  x=m[i]
  if x["count"]==0 or x["delta"]==0:unknown+=1;continue
  prior=sorted(abs(t["delta"]) for t in m[i-60:i] if t["count"]>0)
  if len(prior)<30:unknown+=1;continue
  eligible+=1;threshold=prior[min(len(prior)-1,int((len(prior)-1)*.9))]
  if abs(x["delta"])<threshold:continue
  unusual+=1
  move=x["last"]-x["first"];effect=x["delta"]*move
  if effect<0:
   no_follow+=1
   if x["delta"]>0:long_absorb+=1
   else:short_absorb+=1
  elif effect>0:aligned+=1
  else:flat+=1
 return dict(symbol=sym,window="2026-05-19 full UTC day",prior_rolling_minutes=60,threshold="prior 60 nonempty 1m absolute signed quote-notional p90, no current minute",eligible=eligible,unusual_flow=unusual,
  against_flow_price_move=no_follow,aligned_flow_price_move=aligned,zero_price_move=flat,unavailable=unknown,
  buy_flow_resisted=long_absorb,sell_flow_resisted=short_absorb,
  qc_unusual_partition=unusual==no_follow+aligned+flat,
  status="PHYSICAL_ABSORPTION_PROXY_NOT_VERIFIED",
  warnings=["Flow and price response are both observed after minute close; not intraminute early signal","Against-flow direction is not proof of resting liquidity absorption","Minutes with no events or insufficient baseline are NO_DATA","No OOS, controls, fee-adjusted strategy performance"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",choices=SYMS,required=True);a=p.parse_args();print(json.dumps(run(a.symbol)))
