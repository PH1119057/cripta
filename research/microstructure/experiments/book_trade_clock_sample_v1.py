#!/usr/bin/env python3
"""#12 bounded first-N record chronology sample. No full gzip pass or inference of common clock."""
import argparse,csv,gzip,json,zipfile,datetime as dt
from decimal import Decimal
from pathlib import Path
S=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
R=Path("/data/cripta/datasets/raw/20260518_20260816")
def run(sym,limit):
 day="2026-05-19";lo=int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()*1000)
 p=R/sym/"public_trades"/f"{sym}{day}.csv.gz"
 trades=[];timestamps=[]
 with gzip.open(p,"rt") as f:
  for row in csv.DictReader(f):
   t=int(Decimal(row["timestamp"])*1000)
   if t<lo:continue
   if t>=lo+600000:break
   timestamps.append(t)
   if len(trades)<3:trades.append({"time_ms":t,"price":row["price"],"side":row["side"]})
   if len(timestamps)>=limit:break
 book=[];n=0;ctsdiff=[];seq=[];updates=[]
 p=R/sym/"orderbook"/f"{day}_{sym}_ob200.data.zip"
 with zipfile.ZipFile(p) as z:
  with z.open(z.namelist()[0]) as f:
   for raw in f:
    x=json.loads(raw);t=int(x["ts"])
    if t<lo:continue
    if t>=lo+600000:break
    c=x.get("cts")
    if isinstance(c,(int,float)):ctsdiff.append(t-int(c))
    seq.append(x["data"].get("seq"));updates.append(x["data"].get("u"))
    if len(book)<3:book.append({"ts":t,"cts":c,"type":x["type"],"seq":x["data"].get("seq"),"u":x["data"].get("u")})
    n+=1
    if n>=limit:break
 ordered=lambda xs: all(x<=y for x,y in zip(xs,xs[1:])) if all(isinstance(x,(int,float)) for x in xs) else None
 return {"symbol":sym,"max_rows_each":limit,"trade_rows":len(timestamps),"book_rows":n,"first_trade_records":trades,"first_book_records":book,"ts_minus_cts_ms_minmax":[min(ctsdiff),max(ctsdiff)] if ctsdiff else None,"book_seq_monotonic":ordered(seq),"book_update_id_monotonic":ordered(updates),"status":"BOUNDED_SAMPLE_ONLY","limitations":["No validated cross-feed publish/receive time","Sampling first N does not prove whole-day continuity"]}
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--symbol",choices=S,required=True);p.add_argument("--limit",type=int,default=500);a=p.parse_args();print(json.dumps(run(a.symbol,a.limit)))
