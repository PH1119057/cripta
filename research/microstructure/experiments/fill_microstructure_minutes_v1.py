#!/usr/bin/env python3
"""As-of Bybit fill-distribution research features; stdlib/read-only.

Each minute's features use only completed current minute and preceding 60m.
Rolling large-fill P99 threshold uses ONLY preceding 60 minutes. Co-timestamp
same-side groups are a heuristic, never asserted as parent taker orders.
No output writes, trading action, models or future labels.
"""
import argparse,csv,datetime as dt,gzip,json,math,statistics
from decimal import Decimal
from collections import deque
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT="/data/cripta/datasets/raw/20260518_20260816"

def quantile(a,p):
    if not a:return None
    v=sorted(a)
    return v[int(p*(len(v)-1))]

def calculate(sym,day="2026-05-19",root=ROOT):
    if sym not in SYMS:raise ValueError("symbol")
    lo=int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()*10000)
    path=Path(root)/sym/"public_trades"/f"{sym}{day}.csv.gz"
    minutes=[[] for _ in range(1440)]
    groups=[[] for _ in range(1440)]
    prev_ts=None;last_key=None; n=0; last_gminute=None
    with gzip.open(path,"rt",newline="") as f:
        for a in csv.DictReader(f):
            ts=int(Decimal(a["timestamp"])*10000)
            if prev_ts is not None and ts<prev_ts:raise ValueError("out of order")
            prev_ts=ts
            k=(ts-lo)//600000
            if not 0<=k<1440:continue
            if a["symbol"]!=sym or a["side"] not in ("Buy","Sell"):raise ValueError("identity")
            qty=float(a["foreignNotional"])
            if not math.isfinite(qty) or qty<0:raise ValueError("notional")
            sign=1 if a["side"]=="Buy" else -1
            minutes[k].append(qty);n+=1
            key=(ts,sign)
            if key==last_key and last_gminute==k:
                groups[k][-1][1]+=qty;groups[k][-1][2]+=1
            else:
                groups[k].append([sign,qty,1,ts])
            last_key=key;last_gminute=k
    result=[];history=deque(maxlen=60);current_streak=0;last_sign=None;previous_gts=None
    for i in range(1440):
        x=minutes[i]; gs=groups[i]
        prior=[v for block in history for v in block]
        threshold=quantile(prior,.99) if len(prior)>=100 else None
        amt=sum(x)
        # A minute without qualifying large fill has measured zero share,
        # but a missing prior reference distribution remains null.
        large_share=sum(v for v in x if v>=threshold)/amt if threshold is not None and amt>0 else (0.0 if threshold is not None else None)
        positive_intervals=[]
        lengths=[]
        for sign,notional,fills,ts in gs:
            if sign==last_sign:current_streak+=1
            else:current_streak=1
            last_sign=sign;lengths.append(current_streak)
            if previous_gts is not None and ts>previous_gts:positive_intervals.append(ts-previous_gts)
            previous_gts=ts
        record={"minute_index":i,"available_after_utc":dt.datetime.fromtimestamp((lo+(i+1)*600000)/10000,dt.timezone.utc).isoformat(),
                "fill_count":len(x),"timestamp_side_groups":len(gs),
                "large_fill_share_prior60m_p99":large_share,
                "fill_notional_p50_usd":quantile(x,.5),"fill_notional_p90_usd":quantile(x,.9),
                "fill_notional_p99_usd":quantile(x,.99),
                "streak_end_groups":current_streak if gs else None,
                "streak_max_within_minute":max(lengths) if lengths else None,
                "positive_intergroup_gap_median_ms":statistics.median(positive_intervals)/10 if positive_intervals else None,
                "zero_adjacent_fill_ts_fraction":(sum(g[2]-1 for g in gs)/max(1,len(x)-1)) if len(x)>1 else None,
                "threshold_prior_fills":len(prior)}
        result.append(record);history.append(x)
    assert sum(r["fill_count"] for r in result)==n
    return {"symbol":sym,"algorithm":"fill_microstructure_minutes_v1","read_only":True,
            "rows":result,"fills":n,"source":str(path),
            "warnings":["Per-minute fill quantiles are descriptive after minute close, not predictions.",
                        "Exact 0.1 ms timestamp grouping (Decimal seconds*10000) matches archive precision; not parent orders.",
                        "Inter-group gap uses exchange rounded millisecond timestamp; same-timestamp opposite-side zero-gap events excluded."]}

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--symbol",choices=SYMS,required=True)
    p.add_argument("--day",default="2026-05-19");p.add_argument("--root",default=ROOT);p.add_argument("--summary",action="store_true")
    a=p.parse_args();r=calculate(a.symbol,a.day,a.root)
    if a.summary:r["rows"]=[r["rows"][0],r["rows"][60],r["rows"][-1]]
    print(json.dumps(r,ensure_ascii=False))
