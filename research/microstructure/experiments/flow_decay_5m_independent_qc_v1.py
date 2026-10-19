#!/usr/bin/env python3
"""Independent #17 five-minute flow-decay with future-return labels, read-only."""
import argparse,csv,gzip,json,datetime as dt
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def calculate(sym,day):
 if sym not in SYMS:raise ValueError("symbol")
 t0=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
 p=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"public_trades"/f"{sym}{day}.csv.gz"
 rows=[[] for _ in range(288)]
 with gzip.open(p,"rt") as f:
  for r in csv.DictReader(f):
   k=int((Decimal(r["timestamp"])-t0)//300)
   if 0<=k<288:rows[k].append((Decimal(r["price"]),Decimal(r["foreignNotional"])*(1 if r["side"]=="Buy" else -1)))
 close=[v[-1][0] if v else None for v in rows]
 flow=[sum((d for _,d in v),Decimal(0)) for v in rows]
 tally={"flow_decay":{"n":0,"future_reversal":0,"future_continuation":0,"future_flat":0},"flow_nondecay":{"n":0,"future_reversal":0,"future_continuation":0,"future_flat":0}}
 ratios=[];not_enough=0
 for i in range(2,287):
  if any(not rows[j] for j in (i-2,i-1,i,i+1)) or not flow[i-1] or not flow[i]:
   not_enough+=1;continue
  direction=close[i-1]-close[i-2]
  if not direction or direction*flow[i]<=0 or flow[i-1]*flow[i]<=0:continue
  ratio=abs(flow[i])/abs(flow[i-1]);ratios.append(ratio)
  key="flow_decay" if ratio<1 else "flow_nondecay"
  v=tally[key];v["n"]+=1
  future=close[i+1]-close[i]
  if future*direction<0:v["future_reversal"]+=1
  elif future*direction>0:v["future_continuation"]+=1
  else:v["future_flat"]+=1
 ratios.sort()
 return dict(symbol=sym,day=day,blocks=288,eligible=len(ratios),insufficient=not_enough,ratio_median=str(ratios[len(ratios)//2]) if ratios else None,groups=tally,partition=all(v["n"]==v["future_reversal"]+v["future_continuation"]+v["future_flat"] for v in tally.values()),warning="Future next-block label is NOT a causal feature")
if __name__=="__main__":
 a=argparse.ArgumentParser();a.add_argument("--symbol",required=True,choices=SYMS);a.add_argument("--date",required=True);v=a.parse_args();print(json.dumps(calculate(v.symbol,v.date)))
