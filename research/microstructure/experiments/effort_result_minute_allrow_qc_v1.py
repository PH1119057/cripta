#!/usr/bin/env python3
"""#15 full row independent Decimal QC, no writes."""
import csv,gzip,json,argparse,datetime as dt
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,day):
 origin=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}{day}.csv.gz"
 first=[None]*1440;last=[None]*1440;volume=[Decimal(0) for _ in range(1440)];n=[0]*1440
 with gzip.open(path,"rt") as stream:
  for r in csv.DictReader(stream):
   i=int((Decimal(r["timestamp"])-origin)//60)
   if not 0<=i<1440:continue
   price=Decimal(r["price"]);size=Decimal(r["foreignNotional"])
   if first[i] is None:first[i]=price
   last[i]=price;volume[i]+=size;n[i]+=1
 values=[]
 for i in range(1440):
  if n[i] and volume[i]>0:values.append((i,abs(last[i]-first[i])*Decimal(10000)*Decimal(1000000)/(first[i]*volume[i])))
 return values,n
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--date",default="2026-05-20");a=p.parse_args()
 import urllib.request
 u="https://raw.githubusercontent.com/PH1119057/cripta/eb265ca28f0102717117b343d3c6dcece445c8fa/research/microstructure/experiments/effort_result_minute_v2.py"
 b=urllib.request.urlopen(u,timeout=15).read();namespace={"__name__":"qc_primary"};exec(compile(b,u,"exec"),namespace)
 # independent first/last formula compared to direct minute-level primary formula, not just overall quantiles
 origin=Decimal(int(dt.datetime.strptime(a.date,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 raw=Path("/data/cripta/datasets/raw/20260518_20260816")/a.symbol/"public_trades"/f"{a.symbol}{a.date}.csv.gz"
 buckets=[[] for _ in range(1440)]
 with gzip.open(raw,"rt") as stream:
  for row in csv.DictReader(stream):
   i=int((Decimal(row["timestamp"])-origin)//60)
   if 0<=i<1440:buckets[i].append(row)
 independent,counts=run(a.symbol,a.date)
 mismatches=[];valid=0
 for i,rows in enumerate(buckets):
  if not rows:continue
  turnover=sum((Decimal(r["foreignNotional"]) for r in rows),Decimal(0))
  if turnover<=0:continue
  primary=abs(Decimal(rows[-1]["price"])/Decimal(rows[0]["price"])-1)*Decimal(10000)/(turnover/Decimal(1000000))
  secondary=dict(independent)[i]
  valid+=1
  if abs(primary-secondary)>Decimal("0.000000001")*max(Decimal(1),abs(primary)):
   mismatches.append(i)
 print(json.dumps(dict(symbol=a.symbol,day=a.date,rows=1440,valid=valid,empty=sum(c==0 for c in counts),mismatches=len(mismatches),mismatch_minute_indices=mismatches[:12],pass_qc=not mismatches,remark="two independent formula rearrangements and independent minute aggregation; no source publication availability QC")))
