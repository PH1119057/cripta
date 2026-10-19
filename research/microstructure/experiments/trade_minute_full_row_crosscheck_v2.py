#!/usr/bin/env python3
"""Independent all-minute 6-factor comparison vs candidate exact timestamp floor; read-only source, compact QC stdout."""
import argparse,datetime as dt,csv,gzip,json,math
from decimal import Decimal
from pathlib import Path
COHORT=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,day):
 if sym not in COHORT:raise ValueError("symbol")
 source=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}{day}.csv.gz"
 epoch=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 counts=[0]*1440;buy=[Decimal(0) for _ in range(1440)];sell=[Decimal(0) for _ in range(1440)]
 with gzip.open(source,"rt") as stream:
  for r in csv.DictReader(stream):
   t=Decimal(r["timestamp"]);idx=int((t-epoch)//60)
   if 0<=idx<1440:
    counts[idx]+=1
    (buy if r["side"]=="Buy" else sell)[idx]+=Decimal(r["foreignNotional"])
 signed=[buy[i]-sell[i] for i in range(1440)]
 notion=[buy[i]+sell[i] for i in range(1440)]
 cvd=Decimal(0);prev_count=None;prev_delta=None;rows=[]
 for i in range(1440):
  cvd+=signed[i]
  baseline=sum(notion[i-60:i],Decimal(0))/Decimal(60) if i>=60 else None
  rows.append(dict(fill_count=counts[i],trade_rate_accel_per_min=counts[i]-prev_count if prev_count is not None else None,buy_notional_usd=float(buy[i]),sell_notional_usd=float(sell[i]),delta_usd=float(signed[i]),cvd_from_day_start_usd=float(cvd),delta_accel_usd=float(signed[i]-prev_delta) if prev_delta is not None else None,rvol_60m=float(notion[i]/baseline) if baseline is not None and baseline>0 else None))
  prev_count=counts[i];prev_delta=signed[i]
 return rows
if __name__=="__main__":
 a=argparse.ArgumentParser();a.add_argument("--symbol",required=True,choices=COHORT);a.add_argument("--date",default="2026-05-20");x=a.parse_args()
 # Exact primary version pinned to this same immutable source commit
 import urllib.request
 url="https://raw.githubusercontent.com/PH1119057/cripta/48fd2e2aedae9e9d49df4f8be346ae01879b9bfe/research/microstructure/experiments/trade_minute_factors_v2_exact_timestamp_floor.py"
 mod={"__name__":"source_import"};exec(compile(urllib.request.urlopen(url,timeout=15).read(),url,"exec"),mod)
 primary=mod["calculate"](x.symbol,x.date)["rows"];independent=run(x.symbol,x.date)
 assert len(primary)==len(independent)==1440
 tested=("fill_count","trade_rate_accel_per_min","buy_notional_usd","sell_notional_usd","delta_usd","cvd_from_day_start_usd","delta_accel_usd","rvol_60m")
 stats={k:dict(checked=0,mismatches=0,max_abs_error=0.0) for k in tested}
 for old,new in zip(primary,independent):
  for key in tested:
   p=old[key];q=new[key];z=stats[key];z["checked"]+=1
   if p is None or q is None:
    if p is not q:z["mismatches"]+=1
   elif key in ("fill_count","trade_rate_accel_per_min"):
    if p!=q:z["mismatches"]+=1
   else:
    diff=abs(p-q);z["max_abs_error"]=max(z["max_abs_error"],diff)
    if not math.isclose(p,q,rel_tol=1e-10,abs_tol=1e-6):z["mismatches"]+=1
 print(json.dumps(dict(symbol=x.symbol,day=x.date,minutes=1440,metrics=stats,all_pass=all(v["mismatches"]==0 for v in stats.values()),semantics="Independent decimal aggregation converted to float, per-minute numeric tolerance 1e-10 relative/1e-6 absolute; no source publication QC")))
