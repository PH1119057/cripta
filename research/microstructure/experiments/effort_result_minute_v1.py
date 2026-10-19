#!/usr/bin/env python3
"""#15 explicit effort/result proxy: observed 1m absolute return bps per 1m USDT turnover in millions."""
import argparse,csv,gzip,datetime as dt,json
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym):
 start=Decimal(int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()))
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}2026-05-19.csv.gz"
 m=[dict(first=None,last=None,turnover=Decimal(0),count=0) for _ in range(1440)]
 with gzip.open(path,"rt") as f:
  for r in csv.DictReader(f):
   i=int((Decimal(r["timestamp"])-start)//60)
   if i<0 or i>=1440:continue
   x=m[i];p=Decimal(r["price"])
   if x["first"] is None:x["first"]=p
   x["last"]=p;x["turnover"]+=Decimal(r["foreignNotional"]);x["count"]+=1
 values=[];empty=0;zero=0
 for x in m:
  if not x["count"]:empty+=1;continue
  if x["turnover"]<=0:zero+=1;continue
  excursion=abs(x["last"]/x["first"]-1)*Decimal(10000)
  values.append(excursion/(x["turnover"]/Decimal(1000000)))
 values.sort()
 n=len(values)
 return dict(symbol=sym,minute_rows=1440,valid=n,empty_minutes=empty,zero_turnover=zero,
  median_abs_intraminute_bps_per_million_usdt=str(values[n//2]) if n else None,
  p90_abs_intraminute_bps_per_million_usdt=str(values[min(n-1,int((n-1)*.9))]) if n else None,
  definition="abs(last_trade_price/first_trade_price - 1)*10000 / (sum(foreignNotional) / 1000000)",
  available_after="end of corresponding UTC minute",status="PHYSICAL_PROXY_SINGLE_DAY_NOT_NORMALIZED_FOR_COIN_LIQUIDITY",
  caveats=["1m intraminute excursion is endpoint change, not high-low range","Dividing by small volumes can amplify ratio; OOS/self-baselines required","Not a profit estimate or marginal causal impact of an order"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",choices=SYMS,required=True);a=p.parse_args();print(json.dumps(run(a.symbol)))
