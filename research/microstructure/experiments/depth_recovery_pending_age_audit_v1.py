#!/usr/bin/env python3
"""#13 read-only end-of-window pending-age audit; no write, no algorithm rewrite."""
import argparse,datetime as dt,json,zipfile
from pathlib import Path
from decimal import Decimal
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def run(sym,day,minutes=1440,window=60000):
 start=int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()*1000)
 endpoint=start+minutes*60000
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"orderbook"/f"{day}_{sym}_ob200.data.zip"
 books=[{},{}];pending=[{},{}];count=[{"new":0,"recovered":0,"expired_on_next_delta":0,"snapshot_censored":0} for _ in range(2)]
 snapshots=deltas=0;last_ts=None
 with zipfile.ZipFile(path) as z:
  with z.open(z.namelist()[0]) as src:
   for line in src:
    e=json.loads(line);ts=int(e["ts"])
    if ts<start:continue
    if ts>=endpoint:break
    last_ts=ts
    if e["type"]=="snapshot":
     for i in range(2):
      count[i]["snapshot_censored"]+=len(pending[i]);pending[i].clear()
     books=[{Decimal(p):Decimal(q) for p,q in e["data"][k] if Decimal(q)>0} for k in ("b","a")]
     snapshots+=1;continue
    if not snapshots:continue
    for i,k in enumerate(("b","a")):
     book=books[i];active=pending[i];ctr=count[i]
     for px,(t,target) in list(active.items()):
      if ts-t>window:ctr["expired_on_next_delta"]+=1;del active[px]
     for pp,qq in e["data"][k]:
      price=Decimal(pp);size=Decimal(qq);old=book.get(price,Decimal(0))
      if price in active and size>=active[price][1]:
       ctr["recovered"]+=1;del active[price]
      elif old>0 and size<old and price not in active:
       active[price]=(ts,old);ctr["new"]+=1
      if size<=0:book.pop(price,None)
      else:book[price]=size
    deltas+=1
 for i in range(2):
  ctr=count[i];ctr["open_at_end"]=len(pending[i])
  ctr["older_than_60s_at_end"]=sum(endpoint-t>window for t,target in pending[i].values())
  ctr["right_censored_within_60s"]=sum(endpoint-t<=window for t,target in pending[i].values())
  ctr["identity_partition_pass"]=(ctr["new"]==ctr["recovered"]+ctr["expired_on_next_delta"]+ctr["snapshot_censored"]+ctr["open_at_end"])
 return dict(symbol=sym,day=day,minutes=minutes,source_messages=snapshots+deltas,last_source_ts=last_ts,observation_endpoint=endpoint,source_snapshots=snapshots,bid=count[0],ask=count[1],scope="end-window pending-age inventory only; whether no-event intervals imply observable continuous coverage is separate QC")
if __name__=="__main__":
 a=argparse.ArgumentParser();a.add_argument("--symbol",required=True,choices=SYMS);a.add_argument("--date",default="2026-05-20");a.add_argument("--minutes",type=int,default=1440);v=a.parse_args();print(json.dumps(run(v.symbol,v.date,v.minutes)))
