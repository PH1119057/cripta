#!/usr/bin/env python3
"""#21 causal prior60 trade-extrema sweep then intraminute reclaim by ordered fills."""
import argparse,csv,gzip,datetime as dt,json
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym):
 start=Decimal(int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()))
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}2026-05-19.csv.gz"
 prices=[[] for _ in range(1440)]
 with gzip.open(path,"rt") as f:
  for r in csv.DictReader(f):
   i=int((Decimal(r["timestamp"])-start)//60)
   if 0<=i<1440:prices[i].append(Decimal(r["price"]))
 high_sweeps=high_reclaims=low_sweeps=low_reclaims=eligible=missing=0
 for i in range(60,1440):
  previous=prices[i-60:i]
  if any(not row for row in previous) or not prices[i]:
   missing+=1;continue
  eligible+=1
  upper=max(max(row) for row in previous);lower=min(min(row) for row in previous)
  high_seen=False;low_seen=False;high_done=False;low_done=False
  for p in prices[i]:
   if p>upper:high_seen=True
   elif high_seen and p<=upper:high_done=True
   if p<lower:low_seen=True
   elif low_seen and p>=lower:low_done=True
  high_sweeps+=high_seen;high_reclaims+=high_done
  low_sweeps+=low_seen;low_reclaims+=low_done
 return dict(symbol=sym,lookback_minutes=60,eligible_minutes=eligible,missing_or_incomplete_windows=missing,
 high_sweeps=high_sweeps,high_reclaims_after_sweep=high_reclaims,
 low_sweeps=low_sweeps,low_reclaims_after_sweep=low_reclaims,
 qc_reclaims_bounded=high_reclaims<=high_sweeps and low_reclaims<=low_sweeps,
 temporal="reference prior completed 60 minutes only; event path within minute available only as fills arrive",
 status="TRADE_TAPE_SEQUENCE_CANDIDATE_NOT_BOOK_SWEEP_VERIFIED",
 caveats=["Price extrema from prints, not resting depth or executions through book levels","No deduplication of same-timestamp ties into proven exchange ordering","No OOS or future outcome tested"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);a=p.parse_args();print(json.dumps(run(a.symbol)))
