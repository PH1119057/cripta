#!/usr/bin/env python3
"""#17 descriptive 5-minute aggressor-flow decay; next 5m direction is FUTURE LABEL only."""
import argparse,csv,gzip,datetime as dt,json
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def calc(sym,day="2026-05-19"):
 start=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}{day}.csv.gz"
 b=[dict(first=None,last=None,flow=Decimal(0),n=0) for _ in range(288)]
 with gzip.open(path,"rt") as f:
  for r in csv.DictReader(f):
   i=int((Decimal(r["timestamp"])-start)//300)
   if not 0<=i<288:continue
   x=b[i];p=Decimal(r["price"]);v=Decimal(r["foreignNotional"])
   if x["first"] is None:x["first"]=p
   x["last"]=p;x["flow"]+=v if r["side"]=="Buy" else -v;x["n"]+=1
 results={k:{"n":0,"future_reversal":0,"future_continuation":0,"future_flat":0} for k in ("flow_decay","flow_nondecay")}
 insufficient=0;eligible=0;ratios=[]
 for i in range(2,287):
  pp,pr,cur,nxt=b[i-2],b[i-1],b[i],b[i+1]
  if any(x["n"]==0 for x in (pp,pr,cur,nxt)) or pr["flow"]==0 or cur["flow"]==0:
   insufficient+=1;continue
  direction=pr["last"]-pp["last"]
  if direction==0 or direction*cur["flow"]<=0 or pr["flow"]*cur["flow"]<=0:continue
  ratio=abs(cur["flow"])/abs(pr["flow"]);ratios.append(ratio);eligible+=1
  group="flow_decay" if ratio<1 else "flow_nondecay"
  state=results[group];state["n"]+=1
  future=nxt["last"]-cur["last"]
  if future*direction<0:state["future_reversal"]+=1
  elif future*direction>0:state["future_continuation"]+=1
  else:state["future_flat"]+=1
 ratios.sort()
 return dict(symbol=sym,blocks_5m=288,events_eligible=eligible,missing_or_zero_flow=insufficient,
   current_flow_ratio_median=str(ratios[len(ratios)//2]) if ratios else None,results=results,
   qc_partition=all(x["n"]==x["future_reversal"]+x["future_continuation"]+x["future_flat"] for x in results.values()),
   temporal_semantics="decay condition at end of 5m block i, next-block close outcome available only at end of i+1",
   status="5M_FLOW_DECAY_DESCRIPTIVE_FUTURE_LABEL_ONLY",
   caveats=["Ratio<1 is physical decay definition, not trading threshold","Adjacent 5m study anchors may overlap in their evidence windows","Future returns are labels, never available as features","Not physical proof of seller/buyer exhaustion or OOS edge"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--date",default="2026-05-19");a=p.parse_args();print(json.dumps(calc(a.symbol,a.date)))
