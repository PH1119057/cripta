#!/usr/bin/env python3
"""#13 causal exact-price recovery episodes following visible size decreases; read only."""
import argparse,datetime as dt,json,zipfile
from decimal import Decimal
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,minutes,window_ms=60000):
 start=int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()*1000)
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"orderbook"/f"2026-05-19_{sym}_ob200.data.zip"
 books=[{},{}];episodes=[{},{}];result=[dict(events=0,recovered=0,expired=0,open=0,recovery_ms=[]) for _ in range(2)];snaps=0;deltas=0
 with zipfile.ZipFile(path) as z:
  with z.open(z.namelist()[0]) as f:
   for raw in f:
    o=json.loads(raw);ts=int(o["ts"])
    if ts>=start+minutes*60000:break
    if ts<start:continue
    d=o["data"]
    if o["type"]=="snapshot":
     # Reset episodes at source discontinuity: no false outcomes.
     for k in range(2):
      result[k]["open"]+=len(episodes[k]);episodes[k].clear()
     books=[{Decimal(p):Decimal(q) for p,q in d[k] if Decimal(q)>0} for k in ("b","a")]
     snaps+=1;continue
    if not snaps:continue
    for idx,k in enumerate(("b","a")):
     book=books[idx];active=episodes[idx];res=result[idx]
     for p,(t,target) in list(active.items()):
      if ts-t>window_ms:res["expired"]+=1;del active[p]
     for pp,qq in d[k]:
      p=Decimal(pp);q=Decimal(qq);old=book.get(p,Decimal(0))
      if p in active and q>=active[p][1]:
       t,_=active.pop(p);res["recovered"]+=1;res["recovery_ms"].append(ts-t)
      elif old>0 and q<old and p not in active:
       active[p]=(ts,old);res["events"]+=1
      if q<=0:book.pop(p,None)
      else:book[p]=q
    deltas+=1
 for idx in range(2):result[idx]["open"]+=len(episodes[idx])
 for r in result:
  times=sorted(r.pop("recovery_ms"))
  r["median_recovery_ms"]=times[len(times)//2] if times else None
  r["qc_episode_partition"]=(r["events"]==r["recovered"]+r["expired"]+r["open"])
 return dict(symbol=sym,minutes=minutes,snapshots=snaps,delta_events=deltas,window_ms=window_ms,
  bid=result[0],ask=result[1],status="RECOVERY_EPISODE_CANDIDATE_SMOKE",
  caveats=["Exact price level may be repriced/recycled; observed replenishment not proven same participant","At most one pending episode per price; repeated reductions are not new independent episodes","Open means right-censored, not recovery failure","No source publication-lag test or predictive result"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--minutes",type=int,default=10);a=p.parse_args();print(json.dumps(run(a.symbol,a.minutes)))
