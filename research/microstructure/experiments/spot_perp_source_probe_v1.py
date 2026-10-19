#!/usr/bin/env python3
"""#25 bounded read-only Bybit spot/perpetual source discovery for exact day."""
import argparse,json
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOTS=[Path("/data/cripta/datasets/raw/20260518_20260816"),Path("/data/cripta/datasets/external")]
def run(sym):
 candidates=[];date="2026-05-19"
 for root in ROOTS:
  if not root.is_dir():continue
  if root.name=="20260518_20260816":
   names=[f"{sym}_SPOT",f"SPOT_{sym}",f"spot_{sym}",f"{sym.lower()}_spot", "spot"]
   for name in names:
    p=root/name
    if p.exists():candidates.append(str(p))
   inst=root/sym
   if inst.is_dir():
    for p in inst.iterdir():
     if "spot" in p.name.lower():candidates.append(str(p))
  else:
   for p in root.iterdir():
    if "spot" in p.name.lower():candidates.append(str(p))
 return dict(symbol=sym,day=date,spot_historical_series_verified=False,
  shallow_spot_candidate_paths=candidates,status="SPOT_SOURCE_UNVERIFIED",
  caution="Only bounded candidate names are probed; absence is not proof that no spot data exist anywhere.")
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",choices=SYMS,required=True);a=p.parse_args();print(json.dumps(run(a.symbol)))
