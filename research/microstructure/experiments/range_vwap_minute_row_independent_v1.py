#!/usr/bin/env python3
"""Independent full-minute-row QC for #19/#22, source trades only, stdout-only."""
import argparse,csv,gzip,hashlib,json,datetime as dt
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,date):
 t0=int(dt.datetime.strptime(date,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
 p=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}{date}.csv.gz"
 bins=[[] for _ in range(1440)]
 with gzip.open(p,"rt") as f:
  for x in csv.DictReader(f):
   j=int((Decimal(x["timestamp"])-Decimal(t0))//60)
   if 0<=j<1440:bins[j].append((Decimal(x["price"]),Decimal(x["size"])))
 close=[a[-1][0] if a else None for a in bins]
 pricevol=defaultdict(Decimal);cn=Decimal(0);cv=Decimal(0);rows=[];good=0
 for i,arr in enumerate(bins):
  for price,qty in arr:
   pricevol[price]+=qty;cv+=qty;cn+=price*qty
  previous=close[i-39:i-19] if i>=39 else []
  current=close[i-19:i+1] if i>=39 else []
  old=new=ratio=None
  if len(previous)==20 and len(current)==20 and all(v is not None for v in previous+current):
   old=max(previous)-min(previous);new=max(current)-min(current)
   if old>0:ratio=new/old;good+=1
  poc=min(pricevol,key=lambda v:(-pricevol[v],v)) if pricevol else None
  row={"minute":i,"available_after_utc":dt.datetime.fromtimestamp(t0+(i+1)*60,dt.timezone.utc).isoformat(),
       "trade_count":len(arr),"trade_present":len(arr)>0,
       "compression_20m_vs_previous_20m":str(ratio) if ratio is not None else None,
       "previous_20m_close_range":str(old) if old is not None else None,
       "current_20m_close_range":str(new) if new is not None else None,
       "running_vwap":str(cn/cv) if cv else None,
       "running_poc_exact_price":str(poc) if poc is not None else None,
       "running_volume":str(cv)}
  rows.append(row)
 h=hashlib.sha256()
 for row in rows:h.update((json.dumps(row,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\\n").encode())
 return {"symbol":sym,"date":date,"minute_rows":len(rows),"valid_compression":good,
   "independent_sha256":h.hexdigest(),"sample_rows":[rows[0],rows[39],rows[60],rows[-1]],
   "status":"INDEPENDENT_FULL_ROW_HASH_QC"}
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--date",default="2026-05-20");a=p.parse_args();print(json.dumps(run(a.symbol,a.date)))
