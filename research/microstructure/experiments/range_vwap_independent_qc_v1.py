#!/usr/bin/env python3
"""Independent read-only QC for #19 and #22; one symbol, one UTC day; stdout only."""
import argparse,csv,gzip,hashlib,json,datetime as dt
from decimal import Decimal
from pathlib import Path
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym):
 day="2026-05-19"; t0=Decimal(int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()))
 path=ROOT/sym/"public_trades"/f"{sym}{day}.csv.gz"
 h=hashlib.sha256(); b=path.read_bytes();h.update(b)
 counts=[0]*1440;close=[None]*1440;byprice={};vol=Decimal(0);notional=Decimal(0)
 maxerr=Decimal(0);mismatch=0;outside=0;order=0;prev=None;precision=0;n=0
 with gzip.open(path,"rt") as f:
  reader=csv.DictReader(f);cols=reader.fieldnames
  for x in reader:
   ts=Decimal(x["timestamp"]);ticks=ts*10000
   if ticks!=ticks.to_integral_value():precision+=1
   if prev is not None and ts<prev:order+=1
   prev=ts
   i=int((ts-t0)//60)
   if i<0 or i>=1440:outside+=1;continue
   if x["symbol"]!=sym:raise ValueError("wrong symbol")
   p=Decimal(x["price"]);q=Decimal(x["size"]);f=Decimal(x["foreignNotional"])
   err=abs(p*q-f)
   if err>maxerr:maxerr=err
   if err>Decimal("0.000001"):mismatch+=1
   if p<=0 or q<=0:raise ValueError("invalid quantity/price")
   n+=1;counts[i]+=1;close[i]=p;vol+=q;notional+=f
   byprice[p]=byprice.get(p,Decimal(0))+q
 valid=[]
 for i in range(39,1440):
  w=close[i-39:i+1]
  if any(v is None for v in w):continue
  old=max(w[:20])-min(w[:20]);new=max(w[20:])-min(w[20:])
  if old>0:valid.append(new/old)
 valid.sort();median=(valid[(len(valid)-1)//2]+valid[len(valid)//2])/2 if valid else None
 poc=min(byprice,key=lambda p:(-byprice[p],p))
 return dict(symbol=sym,source=str(path),sha256=h.hexdigest(),source_bytes=len(b),fields=cols,fills=n,
  minute_rows=len(counts),active_minutes=sum(x>0 for x in counts),range_valid=len(valid),range_median=str(median),
  vwap=str(notional/vol),poc=str(poc),size_total=str(vol),notional_total=str(notional),
  foreign_notional_mismatches=mismatch,max_foreign_notional_abs_error=str(maxerr),
  unsorted=order,outside_day=outside,sub_0_1ms_precision=precision,
  minute0_count=counts[0],minute39_count=counts[39],minute1439_count=counts[-1])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);a=p.parse_args()
 print(json.dumps(run(a.symbol),ensure_ascii=False))
