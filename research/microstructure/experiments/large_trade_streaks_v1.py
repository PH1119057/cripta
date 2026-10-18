#!/usr/bin/env python3
"""RESEARCH ONLY. Read-only causal large-fill share and timestamp-side streak inventory.
No database, file writes, exchange or trading actions. stdout JSON.
Identical timestamp+side aggregation is a heuristic, NOT an order-ID reconstruction.
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import statistics
from pathlib import Path

COHORT=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
DEFAULT_ROOT="/data/cripta/datasets/raw/20260518_20260816"

def calculate(symbol,day,root=DEFAULT_ROOT,minutes=1440):
    if symbol not in COHORT or not 1 <= minutes <= 1440:
        raise ValueError("Unsupported symbol or window")
    lo=int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()*1000)
    path=Path(root)/symbol/"public_trades"/f"{symbol}{day}.csv.gz"
    m=[[] for _ in range(minutes)]
    grouped=[]
    with gzip.open(path,"rt",newline="") as stream:
        for row in csv.DictReader(stream):
            t=float(row["timestamp"])
            k=(int(t*1000)-lo)//60000
            if not 0<=k<minutes: continue
            if row["symbol"]!=symbol or row["side"] not in ("Buy","Sell"):
                raise ValueError("Bad symbol/side")
            v=float(row["foreignNotional"])
            if v<0: raise ValueError("Negative notional")
            sign=1 if row["side"]=="Buy" else -1
            m[k].append(v)
            key=(t,sign)
            if grouped and grouped[-1][0]==key:
                grouped[-1][1]+=v
                grouped[-1][2]+=1
            else: grouped.append([key,v,1])
    if not grouped: raise ValueError("No executions in requested window")
    run=1
    streaks=[]
    for i in range(1,len(grouped)):
        if grouped[i][0][1]==grouped[i-1][0][1]:run+=1
        else:streaks.append(run);run=1
    streaks.append(run)
    sorted_streaks=sorted(streaks)
    ratios=[]
    # Full previous 60 minutes only. Threshold never includes current minute.
    for i in range(60,minutes):
        history=sorted(v for j in range(i-60,i) for v in m[j])
        if len(history)<100 or not m[i]:continue
        threshold=history[int(.99*(len(history)-1))]
        denom=sum(m[i])
        if denom:ratios.append(sum(v for v in m[i] if v>=threshold)/denom)
    ratios.sort()
    return {"status":"RESEARCH_ONLY_NOT_VALIDATED",
            "algorithm":"large_trade_streaks_v1",
            "symbol":symbol,"date_utc":day,"minutes":minutes,"input":str(path),
            "group_count":len(grouped),
            "groups_with_multiple_fills":sum(g[2]>1 for g in grouped),
            "streak_count":len(streaks),"streak_median":statistics.median(streaks),
            "streak_p90":sorted_streaks[int(.9*(len(streaks)-1))],
            "streak_max":max(streaks),
            "causal_large_share_median":statistics.median(ratios) if ratios else None,
            "causal_large_share_p90":ratios[int(.9*(len(ratios)-1))] if ratios else None,
            "eligible_minutes_large_share":len(ratios),
            "limitations":["Timestamp+side does not identify parent order.",
                           "Full 60-min history required; minute lacks threshold if <100 prior executions.",
                           "No OOS, fees or trade performance assessed."]}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol",required=True,choices=COHORT)
    p.add_argument("--date",required=True)
    p.add_argument("--minutes",type=int,default=1440)
    p.add_argument("--root",default=DEFAULT_ROOT)
    a=p.parse_args()
    print(json.dumps(calculate(a.symbol,a.date,a.root,a.minutes),ensure_ascii=False,indent=2))

if __name__=="__main__":main()
