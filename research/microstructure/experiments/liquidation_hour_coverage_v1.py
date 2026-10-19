#!/usr/bin/env python3
"""#24 liquidation observed-hour file inventory, read-only, no missing-as-zero."""
import argparse,json
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT=Path("/data/cripta/datasets/external/cryptohftdata/bybit_liquidations_v1/raw/2026-05-19")
def calc(sym):
 hours=[];total=0
 for h in range(24):
  p=ROOT/f"{h:02d}"/f"{sym}_liquidations.parquet.zst"
  if p.is_file():
   size=p.stat().st_size
   hours.append(dict(hour=f"{h:02d}",bytes=size,nonempty=size>0))
   total+=size
 missing=[f"{h:02d}" for h in range(24) if f"{h:02d}" not in {x["hour"] for x in hours}]
 return dict(symbol=sym,day="2026-05-19",observed_hours=[x["hour"] for x in hours],
  present_count=len(hours),missing_hours=missing,total_compressed_bytes=total,
  nonempty_hours=sum(x["nonempty"] for x in hours),file_manifest=hours,
  status="PARTIAL_HOURLY_SOURCE_COVERAGE" if len(hours)<24 else "FILES_PRESENT_NOT_EVENT_COVERAGE_PROVEN",
  warning="File presence does NOT establish completeness of every exchange liquidation event; missing hour means NO_DATA, not zero")
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);a=p.parse_args();print(json.dumps(calc(a.symbol)))
