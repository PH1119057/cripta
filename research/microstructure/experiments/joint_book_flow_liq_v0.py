#!/usr/bin/env python3
"""Research-only Bybit joint book / flow / liquidation 5m pilot, UTC 2026-05-19.
Read-only; stdout JSON. No trading effect. Missing liquidation source != zero.
Book snapshot replaces state; deltas mutate. For each completed 5m bin use
last book observation, flow inside bin, and next-bin return as LABEL only.
"""
import argparse,csv,gzip,zipfile,json,math,statistics
from pathlib import Path
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
START=1779148800000
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
LIQ=Path("/data/cripta/research/runs/strategy_full20_liq_20260922/liquidations")

def analyze(symbol,minutes=120):
    if symbol not in SYMS or minutes%5 or not 15<=minutes<=1440:raise ValueError("symbol/minutes")
    bins=minutes//5
    trades=[{"signed_usd":0.,"usd":0.,"n":0,"last":None} for _ in range(bins)]
    with gzip.open(ROOT/symbol/"public_trades"/(symbol+"2026-05-19.csv.gz"),"rt") as f:
        for a in csv.DictReader(f):
            k=(int(float(a["timestamp"])*1000)-START)//300000
            if not 0<=k<bins:continue
            v=float(a["foreignNotional"]);o=trades[k];o["usd"]+=v;o["n"]+=1
            o["signed_usd"]+=v*(1 if a["side"]=="Buy" else -1)
            o["last"]=float(a["price"])
    liq=[{"n":0,"usd":0.} for _ in range(bins)]
    with gzip.open(LIQ/(symbol+".csv.gz"),"rt") as f:
        for a in csv.DictReader(f):
            k=(int(a["observed_timestamp_us"])//1000-START)//300000
            if 0<=k<bins:
                liq[k]["n"]+=1
                liq[k]["usd"]+=float(a["price"])*float(a["size"])
    book=[None]*bins
    bid={};ask={};snapshot=False;observations=0
    with zipfile.ZipFile(ROOT/symbol/"orderbook"/("2026-05-19_"+symbol+"_ob200.data.zip")) as z:
        with z.open(z.namelist()[0]) as f:
            for raw in f:
                a=json.loads(raw);ts=int(a["ts"])
                if ts>=START+minutes*60000:break
                if ts<START:continue
                d=a["data"]
                if a["type"]=="snapshot":
                    bid={float(p):float(q) for p,q in d["b"]};ask={float(p):float(q) for p,q in d["a"]};snapshot=True
                else:
                    for entries,side in ((d["b"],bid),(d["a"],ask)):
                        for p,q in entries:
                            p=float(p);q=float(q)
                            if q==0:side.pop(p,None)
                            else:side[p]=q
                if not snapshot or not bid or not ask:continue
                bb=max(bid);aa=min(ask)
                if bb>=aa:raise ValueError("crossed book")
                mid=(bb+aa)/2
                b5=sum(p*q for p,q in bid.items() if mid*.9995<=p<=mid)
                a5=sum(p*q for p,q in ask.items() if mid<=p<=mid*1.0005)
                k=(ts-START)//300000
                if 0<=k<bins:
                    book[k]={"spread_bps":(aa-bb)/mid*10000,
                             "imbalance5":(b5-a5)/(b5+a5) if b5+a5 else None,
                             "depth5_usd":b5+a5,
                             "last_ts":ts}
                observations+=1
    # Anchor at completed bin i and label return over next completed bin.
    rows=[]
    for i in range(bins-1):
        a=trades[i];future=trades[i+1];b=book[i]
        if not b or a["last"] is None or future["last"] is None:continue
        if a["usd"]<=0:continue
        flow=a["signed_usd"]/a["usd"]
        direction=1 if flow>0 else -1 if flow<0 else 0
        label=(future["last"]/a["last"]-1)*100
        rows.append({"flow":flow,"direction":direction,"imbalance":b["imbalance5"],
                     "spread":b["spread_bps"],"depth":b["depth5_usd"],
                     "liq_n":liq[i]["n"],"liq_usd":liq[i]["usd"],
                     "label_pct":label,"age_sec":(START+(i+1)*300000-b["last_ts"])/1000})
    # Descriptive non-overlap 5min points; not independent event episodes.
    def summary(r):
        n=len(r);matches=sum(x["direction"]*x["label_pct"]>0 for x in r)
        return {"n":n,"direction_matches":matches,"pct":round(100*matches/n,2) if n else None}
    subsets={"all":rows,"flow_and_book_agree":[x for x in rows if x["imbalance"] is not None and x["direction"]*x["imbalance"]>0],
             "flow_and_book_disagree":[x for x in rows if x["imbalance"] is not None and x["direction"]*x["imbalance"]<0],
             "liq_observed":[x for x in rows if x["liq_n"]>0]}
    return {"symbol":symbol,"minutes":minutes,"book_msgs_processed":observations,
            "joined_rows":len(rows),"groups":{k:summary(v) for k,v in subsets.items()},
            "limits":["Descriptive only; no OI/Spot in this script, no OOS, costs or market controls.",
                      "Liquidation rows reflect incomplete provider capture; unknown must not become zero.",
                      "Book ts assumed event ordering pending ts/cts and freshness QC.",
                      "Relative side depth moves with mid; not OFI or true cancellations.",
                      "No trade decision allowed."]}

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--symbol",choices=SYMS,required=True)
    p.add_argument("--minutes",type=int,default=120);a=p.parse_args()
    print(json.dumps(analyze(a.symbol,a.minutes),ensure_ascii=False))
