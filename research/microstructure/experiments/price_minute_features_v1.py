#!/usr/bin/env python3
"""Read-only Bybit causal price feature minute series. No research labels or combinations."""
import argparse,csv,gzip,json,datetime as dt,math,statistics
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT="/data/cripta/datasets/raw/20260518_20260816"
def calculate(s,day="2026-05-19"):
 if s not in SYMS:raise ValueError("symbol")
 t0=int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
 p=Path(ROOT)/s/"public_trades"/f"{s}{day}.csv.gz"
 minute=[None]*1440;n=0;laststamp=None
 with gzip.open(p,"rt") as f:
  for a in csv.DictReader(f):
   ts=float(a["timestamp"])
   if laststamp is not None and ts<laststamp:raise ValueError("not sorted")
   laststamp=ts;k=int((ts-t0)//60)
   if 0<=k<1440:
    minute[k]=float(a["price"]);n+=1
 last=None;last_idx=None;cl=[];rs=[];rows=[]
 for i,c in enumerate(minute):
  if c is not None:last=c;last_idx=i
  cl.append(last)
  r=math.log(last/cl[i-1]) if last is not None and i and cl[i-1] else None
  rs.append(r)
  # Prior 60 observed minute-close returns, latest included (so available at minute end).
  rets=[v for v in rs[max(1,i-59):i+1] if v is not None]
  ac=None
  if len(rets)==60:
   xa=rets[:-1];xb=rets[1:];ma=sum(xa)/len(xa);mb=sum(xb)/len(xb)
   num=sum((a-ma)*(b-mb) for a,b in zip(xa,xb))
   den=math.sqrt(sum((a-ma)**2 for a in xa)*sum((b-mb)**2 for b in xb))
   ac=num/den if den>0 else None
  prev=cl[max(0,i-60):i]
  hi=max((v for v in prev if v is not None),default=None)
  lo=min((v for v in prev if v is not None),default=None)
  rows.append({"minute":i,"available_after_utc":dt.datetime.fromtimestamp(t0+(i+1)*60,dt.timezone.utc).isoformat(),
               "trade_present":c is not None,"last_close":last,"last_close_age_minutes":i-last_idx if last_idx is not None else None,
               "returns_autocorr_lag1_prev60":ac,"prior60_close_high":hi,"prior60_close_low":lo,
               "above_prior60_high":bool(last>hi) if last is not None and hi is not None else None,
               "below_prior60_low":bool(last<lo) if last is not None and lo is not None else None})
 assert len(rows)==1440
 return {"symbol":s,"fills":n,"rows":rows}
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",choices=SYMS,required=True);p.add_argument("--summary",action="store_true");a=p.parse_args()
 r=calculate(a.symbol)
 if a.summary:r["rows"]=[r["rows"][0],r["rows"][60],r["rows"][-1]]
 print(json.dumps(r))
