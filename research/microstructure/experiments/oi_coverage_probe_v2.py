#!/usr/bin/env python3
"""#23 bounded filesystem historical OI evidence discovery; read only, NO_DATA is not zero."""
import argparse,os,json,datetime as dt
from pathlib import Path
ROOTS=[Path("/data/cripta/datasets/raw/20260518_20260816"),Path("/data/cripta/datasets/external")]
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,day='2026-05-19'):
 # Never descend whole dataset. Inspect exact symbol root and shallow source family names only.
 out=[]
 for root in ROOTS:
  if not root.exists():continue
  if root.name=="20260518_20260816":
   d=root/sym
   if d.is_dir():
    for child in d.iterdir():
     if "oi" in child.name.lower() or "open_interest" in child.name.lower():
      out.append({"path":str(child),"is_dir":child.is_dir()})
  else:
   for child in root.iterdir():
    if "oi" in child.name.lower() or "open_interest" in child.name.lower():
     out.append({"path":str(child),"is_dir":child.is_dir(),"requires_source_specific_coverage_qc":True})
 return dict(symbol=sym,day_utc=day,exact_day_valid_oi_series_verified=False,
 shallow_candidate_paths=out,status="HISTORICAL_OI_NOT_ESTABLISHED" if not out else "CANDIDATES_REQUIRE_COVERAGE_QC",
 warning="No observed OI means NO_DATA, not zero; folder-name inspection cannot prove absence across all possible providers.")
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--date",default="2026-05-19");a=p.parse_args();print(json.dumps(run(a.symbol,a.date)))
