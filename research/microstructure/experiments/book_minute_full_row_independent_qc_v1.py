#!/usr/bin/env python3
"""Independent all-minute Decimal book bid/ask 5bp depth, imbalance, spread QC, read-only."""
import argparse,datetime as dt,json,zipfile,math
from pathlib import Path
from decimal import Decimal
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
def audit(sym):
 if sym not in SYMS:raise ValueError("symbol")
 start=int(dt.datetime(2026,5,19,tzinfo=dt.timezone.utc).timestamp()*1000)
 path=Path("/data/cripta/datasets/raw/20260518_20260816")/sym/"orderbook"/f"2026-05-19_{sym}_ob200.data.zip"
 books=[{},{}];last=[None]*1440;messages=0
 with zipfile.ZipFile(path) as z:
  with z.open(z.namelist()[0]) as stream:
   for raw in stream:
    o=json.loads(raw);ts=int(o["ts"])
    if ts>=start+86400000:break
    if ts<start:continue
    if o["type"]=="snapshot":
     books=[{Decimal(p):Decimal(q) for p,q in o["data"][s] if Decimal(q)>0} for s in ("b","a")]
    else:
     for i,side in enumerate(("b","a")):
      for p,q in o["data"][side]:
       px=Decimal(p);size=Decimal(q)
       if size:books[i][px]=size
       else:books[i].pop(px,None)
    bid=max(books[0]);ask=min(books[1]);mid=(bid+ask)/2
    bd=sum((p*q for p,q in books[0].items() if mid*Decimal("0.9995")<=p<=mid),Decimal(0))
    ad=sum((p*q for p,q in books[1].items() if mid<=p<=mid*Decimal("1.0005")),Decimal(0))
    last[(ts-start)//60000]=(float(bd),float(ad),float((bd-ad)/(bd+ad)) if bd+ad else None,float((ask-bid)/mid*10000))
    messages+=1
 return last,messages
if __name__=="__main__":
 x=argparse.ArgumentParser();x.add_argument("--symbol",required=True,choices=SYMS);a=x.parse_args()
 import urllib.request
 url="https://raw.githubusercontent.com/PH1119057/cripta/b1862ab491666433a7da49c42e7f403e3cfa791d/research/microstructure/experiments/book_minute_three_v1.py"
 mod={"__name__":"qc_module"};exec(compile(urllib.request.urlopen(url,timeout=15).read(),url,"exec"),mod)
 primary=mod["calculate"](a.symbol,1440)
 independent,messages=audit(a.symbol)
 keys=("bid_depth_usd","ask_depth_usd","imbalance","spread_bps")
 errors={k:{"mismatch":0,"max_abs":0.0,"first_indices":[]} for k in keys}
 for i,(x,y) in enumerate(zip(primary["minutes"],independent)):
  if x is None or y is None:
   if x is not None or y is not None:raise ValueError("coverage mismatch")
   continue
  for k,z in zip(keys,y):
   p=x[k];e=errors[k]
   if p is None or z is None:
    mismatch=p is not None or z is not None
   else:
    diff=abs(p-z);e["max_abs"]=max(e["max_abs"],diff)
    mismatch=not math.isclose(p,z,rel_tol=1e-10,abs_tol=1e-6)
   if mismatch:
    e["mismatch"]+=1
    if len(e["first_indices"])<8:e["first_indices"].append(i)
 print(json.dumps({"symbol":a.symbol,"total_source_messages":messages,"primary_source_messages":primary["source_messages"],"minutes":1440,"fields":errors,"pass":messages==primary["source_messages"] and all(v["mismatch"]==0 for v in errors.values()),"scope":"all-minute independent Decimal vs primary float, four physical fields, numeric tolerance 1e-10/1e-6; availability not proven"}))
