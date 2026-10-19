#!/usr/bin/env python3
"""Research-only Bybit public-trades minute-level factor readiness v1.

Explicit exchange timestamp floor-to-ms for minute membership (no nearest-ms rounding). Stdlib only. Immutable source reads, JSON summary to stdout, no filesystem writes.
Each factor at minute m is available only AFTER that minute closes.
History-based RVOL requires 60 *previous* minute observations.
Price metrics carry forward most recent trade close, explicitly marked stale.
No labels, combinations, trading policy or profitability inference.
"""
import argparse,csv,datetime as dt,gzip,json,math,statistics
from decimal import Decimal
from pathlib import Path

SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT="/data/cripta/datasets/raw/20260518_20260816"
def calculate(symbol, day, root=ROOT):
    if symbol not in SYMS:raise ValueError("symbol")
    start=int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()*1000)
    path=Path(root)/symbol/"public_trades"/f"{symbol}{day}.csv.gz"
    m=[{"count":0,"notional":0.,"signed":0.,"buy":0.,"sell":0.,"last":None} for _ in range(1440)]
    prior_ts=None; total=0
    with gzip.open(path,"rt",newline="") as f:
        for row in csv.DictReader(f):
            ts=int(Decimal(row["timestamp"])*1000)
            if prior_ts is not None and ts<prior_ts:raise ValueError("nonmonotone timestamp")
            prior_ts=ts
            if not start<=ts<start+86400000:continue
            if row["symbol"]!=symbol or row["side"] not in ("Buy","Sell"):raise ValueError("row identity")
            p=float(row["price"]);v=float(row["foreignNotional"])
            if not math.isfinite(p) or not math.isfinite(v) or p<=0 or v<0:raise ValueError("bad numeric")
            x=m[(ts-start)//60000]
            x["count"]+=1;x["notional"]+=v;x["signed"]+=(1 if row["side"]=="Buy" else -1)*v
            x["buy" if row["side"]=="Buy" else "sell"]+=v
            x["last"]=p;total+=1
    cvd=0.;prev_count=None;prev_signed=None;prev_close=None;prev_return=None
    output=[];last=None;last_min=None
    for i,x in enumerate(m):
        if x["last"] is not None:last=x["last"];last_min=i
        cvd+=x["signed"]
        r=math.log(last/prev_close) if last is not None and prev_close is not None else None
        baseline=sum(a["notional"] for a in m[i-60:i])/60 if i>=60 else None
        row={"minute_utc":(dt.datetime.fromtimestamp((start+i*60000)/1000,dt.timezone.utc)).isoformat(),
             "available_after_utc":(dt.datetime.fromtimestamp((start+(i+1)*60000)/1000,dt.timezone.utc)).isoformat(),
             "fill_count":x["count"],"trade_rate_per_min":x["count"],
             "trade_rate_accel_per_min":x["count"]-prev_count if prev_count is not None else None,
             "buy_notional_usd":x["buy"],"sell_notional_usd":x["sell"],
             "delta_usd":x["signed"],"cvd_from_day_start_usd":cvd,
             "delta_accel_usd":x["signed"]-prev_signed if prev_signed is not None else None,
             "rvol_60m":x["notional"]/baseline if baseline is not None and baseline>0 else None,
             "close":last,"close_staleness_minutes":i-last_min if last_min is not None else None,
             "log_return_1m":r}
        output.append(row)
        prev_count=x["count"];prev_signed=x["signed"];prev_close=last;prev_return=r
    assert sum(r["fill_count"] for r in output)==total
    assert all(r["rvol_60m"] is None for r in output[:60])
    assert all((r["buy_notional_usd"]+r["sell_notional_usd"])>=0 for r in output)
    return {"algorithm":"trade_minute_factors_v2_exact_timestamp_floor","symbol":symbol,"date":day,"source":str(path),
            "source_fills":total,"minutes":len(output),"nonempty_trade_minutes":sum(r["fill_count"]>0 for r in output),
            "rvol_eligible_minutes":sum(r["rvol_60m"] is not None for r in output),
            "metrics":["trade_rate","trade_rate_accel","buy_sell_delta","CVD","delta_accel","RVOL"],
            "first":output[0],"minute60":output[60],"last":output[-1],
            "rows":output}
if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol",choices=SYMS,required=True);p.add_argument("--date",default="2026-05-19")
    p.add_argument("--root",default=ROOT);p.add_argument("--summary-only",action="store_true")
    a=p.parse_args();v=calculate(a.symbol,a.date,a.root)
    if a.summary_only:v.pop("rows")
    print(json.dumps(v,ensure_ascii=False))
