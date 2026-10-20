#!/usr/bin/env python3
"""#13 full-day clock-gap and stale pending episode audit; read-only, NOT correction."""
import argparse,datetime as dt,json,zipfile
from decimal import Decimal
from pathlib import Path
S=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,day):
 start=int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()*1000)
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"orderbook"/f"{day}_{sym}_ob200.data.zip"
 books=[{},{}];pending=[{},{}];cnt=[dict(new=0,recovered=0,expired=0,open=0,stale_before_cleanup=0,max_pending_age_before_cleanup_ms=0) for _ in range(2)]
 snaps=deltas=0;last_ts=None;max_gap=0;gaps_over_60s=0;nonmonotone=0
 with zipfile.ZipFile(path) as z:
  with z.open(z.namelist()[0]) as stream:
   for raw in stream:
    e=json.loads(raw);ts=int(e["ts"])
    if ts<start:continue
    if ts>=start+86400000:break
    if last_ts is not None:
     if ts<last_ts:nonmonotone+=1
     gap=ts-last_ts;max_gap=max(max_gap,gap)
     if gap>60000:gaps_over_60s+=1
    last_ts=ts
    if e["type"]=="snapshot":
     for j in range(2):cnt[j]["open"]+=len(pending[j]);pending[j].clear()
     books=[{Decimal(p):Decimal(q) for p,q in e["data"][k] if Decimal(q)>0} for k in ("b","a")]
     snaps+=1;continue
    if not snaps:continue
    for j,k in enumerate(("b","a")):
     active=pending[j];book=books[j];c=cnt[j]
     for price,(t,target) in list(active.items()):
      age=ts-t;c["max_pending_age_before_cleanup_ms"]=max(c["max_pending_age_before_cleanup_ms"],age)
      if age>60000:c["stale_before_cleanup"]+=1;c["expired"]+=1;del active[price]
     for pp,qq in e["data"][k]:
      price=Decimal(pp);qty=Decimal(qq);old=book.get(price,Decimal(0))
      if price in active and qty>=active[price][1]:active.pop(price);c["recovered"]+=1
      elif old>0 and qty<old and price not in active:active[price]=(ts,old);c["new"]+=1
      if qty<=0:book.pop(price,None)
      else:book[price]=qty
    deltas+=1
 end=start+86400000
 for j in range(2):
  c=cnt[j];c["open_at_end"]=len(pending[j])
  c["older_than_60s_at_end"]=sum(end-t>60000 for t,target in pending[j].values())
  c["partition_pass"]=c["new"]==c["recovered"]+c["expired"]+c["open"]+c["open_at_end"]
 return dict(symbol=sym,day=day,snapshots=snaps,deltas=deltas,max_message_gap_ms=max_gap,message_gaps_over_60s=gaps_over_60s,nonmonotone_ts=nonmonotone,bid=cnt[0],ask=cnt[1],warning="Counts only source-event cleanup; no exchange delivery latency or interval ownership proof")
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=S);p.add_argument("--date",default="2026-05-20");a=p.parse_args();print(json.dumps(run(a.symbol,a.date)))
