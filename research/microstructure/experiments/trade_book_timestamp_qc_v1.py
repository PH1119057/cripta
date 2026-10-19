#!/usr/bin/env python3
"""Read-only #12 timestamp/order QC, separate trade and book feeds, never infer a global sequence."""
import argparse,csv,datetime as dt,gzip,json,zipfile
from decimal import Decimal
from pathlib import Path
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def inspect(sym,minutes):
 start=int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()*1000)
 end=start+60000*minutes
 result={}
 path=ROOT/sym/"public_trades"/f"{sym}2026-05-19.csv.gz"
 with gzip.open(path,"rt") as f:
  reader=csv.DictReader(f);fields=reader.fieldnames
  last=None;n=back=ties=0
  for x in reader:
   t=int(Decimal(x["timestamp"])*1000)
   if not start<=t<end:continue
   if last is not None:
    back+=int(t<last);ties+=int(t==last)
   last=t;n+=1
  result["trades"]={"rows":n,"backward_timestamps":back,"same_millisecond_adjacent":ties,"columns":fields}
 book=ROOT/sym/"orderbook"/f"2026-05-19_{sym}_ob200.data.zip"
 with zipfile.ZipFile(book) as z:
  with z.open(z.namelist()[0]) as f:
   last=None;n=back=ties=snaps=0;keys=set();data_keys=set()
   for raw in f:
    x=json.loads(raw);t=int(x["ts"])
    if t>=end:break
    if t<start:continue
    keys.update(x.keys());data_keys.update(x["data"].keys())
    if last is not None:
     back+=int(t<last);ties+=int(t==last)
    last=t;n+=1;snaps+=int(x["type"]=="snapshot")
   result["book"]={"rows":n,"snapshots":snaps,"backward_timestamps":back,"same_millisecond_adjacent":ties,"top_level_keys":sorted(keys),"data_keys":sorted(data_keys)}
 return dict(symbol=sym,minutes=minutes,series=result,
  status="WITHIN_FEED_TIMESTAMP_QC_ONLY",limitations=["No proof of global trade/book sequence","Millisecond conversion can create ties from submillisecond timestamps","Fields present do not establish server arrival/publication time","No cancellation-vs-execution attribution"])
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",required=True,choices=SYMS);p.add_argument("--minutes",type=int,default=10);a=p.parse_args();print(json.dumps(inspect(a.symbol,a.minutes)))
