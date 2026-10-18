#!/usr/bin/env python3
"""Bybit single-symbol causal #19 Range Compression and #22 running VWAP/POC.
Research only: stdout JSON, no filesystem or exchange writes.
Minute i uses completed fills <= minute boundary, is available AFTER its end.
"""
import argparse, csv, datetime as dt, gzip, json, hashlib
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

SYMBOLS = ("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
ROOT = "/data/cripta/datasets/raw/20260518_20260816"

def calculate(symbol, day="2026-05-19"):
    if symbol not in SYMBOLS:
        raise ValueError("symbol outside owner-approved five")
    t0 = int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
    path = Path(ROOT)/symbol/"public_trades"/(symbol+day+".csv.gz")
    minute_close = [None]*1440
    minute_count = [0]*1440
    minute_notional = [Decimal(0)]*1440
    minute_volume = [Decimal(0)]*1440
    price_volume_by_minute = [defaultdict(Decimal) for _ in range(1440)]
    last_tick = None
    input_count = 0
    with gzip.open(path,"rt",newline="") as f:
        for trade in csv.DictReader(f):
            ts = Decimal(trade["timestamp"])
            tick = int(ts*10000)
            if last_tick is not None and tick < last_tick:
                raise ValueError("unsorted timestamps")
            last_tick = tick
            i = int((ts-Decimal(t0))//Decimal(60))
            if not 0 <= i < 1440:
                continue
            p = Decimal(trade["price"])
            # Bybit public trades exposes size; use price * size for VWAP,
            # rather than an unverified external notional field.
            if "size" in trade and trade["size"] not in ("",None):
                qty = Decimal(trade["size"])
            elif "volume" in trade and trade["volume"] not in ("",None):
                qty = Decimal(trade["volume"])
            else:
                raise ValueError("missing trade size column")
            if qty <= 0 or p <= 0:
                raise ValueError("invalid price or size")
            minute_close[i] = p
            minute_count[i] += 1
            minute_volume[i] += qty
            minute_notional[i] += p*qty
            price_volume_by_minute[i][p] += qty
            input_count += 1
    closes=[]
    cumulative_n=Decimal(0)
    cumulative_q=Decimal(0)
    price_volume=defaultdict(Decimal)
    rows=[]
    for i in range(1440):
        closes.append(minute_close[i])
        compression=None
        old_range=None
        new_range=None
        if i>=39 and all(c is not None for c in closes[i-39:i+1]):
            prev=closes[i-39:i-19]
            curr=closes[i-19:i+1]
            old_range=max(prev)-min(prev)
            new_range=max(curr)-min(curr)
            if old_range>0:
                compression=new_range/old_range
        cumulative_n+=minute_notional[i]
        cumulative_q+=minute_volume[i]
        for p,v in price_volume_by_minute[i].items():
            price_volume[p]+=v
        vwap=cumulative_n/cumulative_q if cumulative_q else None
        poc=min(price_volume, key=lambda p:(-price_volume[p],p)) if price_volume else None
        rows.append({
            "minute":i,
            "available_after_utc":dt.datetime.fromtimestamp(t0+(i+1)*60,dt.timezone.utc).isoformat(),
            "trade_count":minute_count[i],
            "trade_present":minute_count[i]>0,
            "compression_20m_vs_previous_20m":str(compression) if compression is not None else None,
            "previous_20m_close_range":str(old_range) if old_range is not None else None,
            "current_20m_close_range":str(new_range) if new_range is not None else None,
            "running_vwap":str(vwap) if vwap is not None else None,
            "running_poc_exact_price":str(poc) if poc is not None else None,
            "running_volume":str(cumulative_q)
        })
    valid=[Decimal(x["compression_20m_vs_previous_20m"]) for x in rows if x["compression_20m_vs_previous_20m"] is not None]
    valid.sort()
    median=str((valid[(len(valid)-1)//2]+valid[len(valid)//2])/2) if valid else None
    # Deterministic canonical serialization of every completed-minute row, not only sampled rows.
    row_hash=hashlib.sha256()
    for record in rows:
        row_hash.update((json.dumps(record,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\\n").encode("utf-8"))
    return {"symbol":symbol,"date":day,"source":str(path),"fills":input_count,
            "minute_rows_sha256":row_hash.hexdigest(),"row_count_verified":len(rows),
            "minutes_with_trades":sum(v>0 for v in minute_count),
            "rows_count":len(rows),"range_valid_count":len(valid),
            "range_median":median,
            "day_vwap_check_only_ex_post":rows[-1]["running_vwap"],
            "day_poc_check_only_ex_post":rows[-1]["running_poc_exact_price"],
            "last_cumulative_volume":rows[-1]["running_volume"],
            "sample_rows":[rows[0],rows[39],rows[60],rows[-1]]}

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--symbol",required=True,choices=SYMBOLS)
    ap.add_argument("--date",default="2026-05-19")
    a=ap.parse_args()
    print(json.dumps(calculate(a.symbol,a.date),ensure_ascii=False))
