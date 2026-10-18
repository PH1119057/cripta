#!/usr/bin/env python3
"""Bybit ONE EXCHANGE joint 5m factor exploratory check, read-only, no writes.
The OI endpoint timestamps need semantic validation before trading use.
Uses data at time T: previous 5m taker flow and spot trade, OI point <=T,
derivatives last trade preceding T. Label: derivative close from T to T+5m.
No fees or OOS. Missing spot bin is not equal to missing source.
"""
import csv, gzip, io, json, urllib.parse, urllib.request, statistics, argparse
from datetime import datetime,timezone
from pathlib import Path
SYM=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT="/data/cripta/datasets/raw/20260518_20260816"
LO=1779148800000
END=LO+86400000
def get(url):
    with urllib.request.urlopen(url,timeout=15) as r:return r.read()
def oi(s):
    records={};cursor="";seen=set()
    while True:
        q={"category":"linear","symbol":s,"intervalTime":"5min","startTime":str(LO),
           "endTime":str(END-1),"limit":"200"}
        if cursor:q["cursor"]=cursor
        obj=json.loads(get("https://api.bybit.com/v5/market/open-interest?"+urllib.parse.urlencode(q)))
        if obj.get("retCode")!=0:raise ValueError(str(obj.get("retMsg")))
        result=obj["result"]
        for row in result["list"]:
            t=int(row["timestamp"])
            if LO<=t<END:records[t]=float(row["openInterest"])
        nxt=result.get("nextPageCursor") or ""
        if not nxt or nxt==cursor or nxt in seen:break
        seen.add(cursor);cursor=nxt
    return records
def series(s):
    f=Path(ROOT)/s/"public_trades"/f"{s}2026-05-19.csv.gz"
    future_last=[None]*288;delta=[0.]*288
    with gzip.open(f,"rt",newline="") as inp:
        for row in csv.DictReader(inp):
            k=(int(float(row["timestamp"])*1000)-LO)//300000
            if 0<=k<288:
                future_last[k]=float(row["price"])
                delta[k]+=float(row["foreignNotional"])*(1 if row["side"]=="Buy" else -1)
    url=f"https://public.bybit.com/spot/{s}/{s}_2026-05-19.csv.gz"
    rows=csv.DictReader(io.StringIO(gzip.decompress(get(url)).decode("utf-8-sig")))
    spot_last=[None]*288
    for row in rows:
        k=(int(row["timestamp"])-LO)//300000
        if 0<=k<288:spot_last[k]=float(row["price"])
    return future_last,delta,spot_last
def evaluate(s):
    open_i=oi(s)
    fut,delta,spot=series(s)
    counters={"rows":0,"spot_valid":0,"oi_valid":0,"triple_valid":0,
              "same_dir_flow":0,"same_dir_flow_correct":0,
              "flow_oi_expand":0,"flow_oi_expand_correct":0,
              "flow_oi_contract":0,"flow_oi_contract_correct":0,
              "basis_against_flow":0,"basis_against_flow_correct":0}
    # Observations at T are followed by return from completed prior bin close to NEXT completed bin close.
    # Use T indexes 1..286, past information in previous bin i-1.
    for i in range(1,287):
        t=LO+i*300000
        oi_t=open_i.get(t);oi_p=open_i.get(t-300000)
        if oi_t is not None and oi_p is not None:counters["oi_valid"]+=1
        if spot[i-1] is not None:counters["spot_valid"]+=1
        if any(v is None for v in (oi_t,oi_p,spot[i-1],fut[i-1],fut[i])):continue
        if not delta[i-1] or not oi_p:continue
        direction=1 if delta[i-1]>0 else -1
        future_ret=fut[i]/fut[i-1]-1
        correct=int(direction*future_ret>0)
        oi_change=oi_t/oi_p-1
        basis=fut[i-1]/spot[i-1]-1
        counters["triple_valid"]+=1
        counters["same_dir_flow"]+=1;counters["same_dir_flow_correct"]+=correct
        if oi_change>0:
            counters["flow_oi_expand"]+=1;counters["flow_oi_expand_correct"]+=correct
        elif oi_change<0:
            counters["flow_oi_contract"]+=1;counters["flow_oi_contract_correct"]+=correct
        if direction*basis<0:
            counters["basis_against_flow"]+=1;counters["basis_against_flow_correct"]+=correct
    return counters
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol",choices=SYM,required=True)
    args=parser.parse_args()
    print(json.dumps({"symbol":args.symbol,"status":"RESEARCH_ONLY_EXPLORATORY","result":evaluate(args.symbol)},indent=2))
if __name__=="__main__":main()
