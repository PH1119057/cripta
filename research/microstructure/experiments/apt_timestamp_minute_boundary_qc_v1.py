#!/usr/bin/env python3
"""Read-only investigate exact timestamp minute-boundary disagreement, no writes."""
import csv,gzip,json,datetime as dt
from decimal import Decimal
from pathlib import Path
day="2026-05-20";sym="APTUSDT"
start=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
p=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}{day}.csv.gz"
matches=[]
with gzip.open(p,"rt") as f:
 for n,r in enumerate(csv.DictReader(f)):
  d=Decimal(r["timestamp"])-start
  exact=int(d//60);rounded=(round(float(r["timestamp"])*1000)-int(start)*1000)//60000
  if exact!=rounded:
   matches.append(dict(row=n,raw_timestamp=r["timestamp"],exact_minute=exact,primary_rounded_minute=rounded,side=r["side"],foreignNotional=r["foreignNotional"]))
print(json.dumps(dict(symbol=sym,day=day,source=str(p),discrepancy_rows=len(matches),examples=matches[:15],all_mismatch_minutes=sorted(set(x["exact_minute"] for x in matches)))))
