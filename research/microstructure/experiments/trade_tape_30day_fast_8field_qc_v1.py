#!/usr/bin/env python3
"""Research-only independent Decimal 8-field QC of 30-day Bybit trade-minute factors.
Read-only: immutable gzip inputs, GitHub-pinned primary from operational mirror,
no file writes/network/DB/trading. Actual receiver clock unknown.
"""
import argparse,csv,datetime as dt,gzip,hashlib,importlib.util,json,math,time
from decimal import Decimal
from pathlib import Path

COHORT=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
START=dt.date(2026,5,18)
END=dt.date(2026,6,16)
PRIMARY=Path("/srv/cripta/source_checkout/research/microstructure/experiments/trade_minute_factors_v2_exact_timestamp_floor.py")
PRIMARY_BLOB="71e4851faebd388fd2a6db3d93dca2ca2cb28c07"
RAW=Path("/data/cripta/datasets/raw/20260518_20260816")
KEYS=("fill_count","buy_notional_usd","sell_notional_usd","delta_usd","cvd_from_day_start_usd","trade_rate_accel_per_min","delta_accel_usd","rvol_60m")

def calc_one(mod,sym,day):
    p=RAW/sym/"public_trades"/(sym+day+".csv.gz")
    start=Decimal(int(dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()))
    independent=[[0,Decimal(0),Decimal(0)] for _ in range(1440)]
    prior=None;bad_id=bad_num=outside=0;min_timestamp=None;max_timestamp=None
    with gzip.open(p,"rt",newline="") as f:
        for r in csv.DictReader(f):
            ts=Decimal(r["timestamp"])
            if prior is not None and ts<prior:raise ValueError("NONMONOTONE_SOURCE")
            prior=ts
            ix=int((ts-start)//60)
            if not 0<=ix<1440:outside+=1;continue
            bad_id+=int(r["symbol"]!=sym or r["side"] not in ("Buy","Sell"))
            price=Decimal(r["price"]);base=Decimal(r["size"]);volume=Decimal(r["foreignNotional"])
            bad_num+=int(price<=0 or base<=0 or volume<0 or
                abs(price*base-volume)>Decimal("0.00000001") or
                Decimal(r["homeNotional"])!=base)
            row=independent[ix];row[0]+=1
            row[1 if r["side"]=="Buy" else 2]+=volume
            min_timestamp=ts if min_timestamp is None else min(min_timestamp,ts)
            max_timestamp=ts if max_timestamp is None else max(max_timestamp,ts)
    primary=mod.calculate(sym,day)
    mismatches=dict.fromkeys(KEYS,0)
    total_count=0;cvd=Decimal(0);prev_count=prev_delta=None;active=0
    totals=[r[1]+r[2] for r in independent]
    for idx,(cnt,buy,sell) in enumerate(independent):
        delta=buy-sell;cvd+=delta;active+=int(cnt>0);total_count+=cnt
        hist=sum(totals[max(0,idx-60):idx],Decimal(0))
        expected=(cnt,buy,sell,delta,cvd,
                  cnt-prev_count if prev_count is not None else None,
                  delta-prev_delta if prev_delta is not None else None,
                  totals[idx]/(hist/60) if idx>=60 and hist>0 else None)
        src=primary["rows"][idx]
        for key,e in zip(KEYS,expected):
            a=src[key]
            good=((e is None and a is None) if e is None or a is None else
                  math.isclose(float(e),float(a),rel_tol=1e-10,abs_tol=1e-6))
            mismatches[key]+=int(not good)
        prev_count=cnt;prev_delta=delta
    digest=hashlib.sha256()
    with p.open("rb") as stream:
        for chunk in iter(lambda:stream.read(1048576),b""):digest.update(chunk)
    return dict(day=day,source_fills=total_count,nonempty_minutes=active,
                source_sha256=digest.hexdigest(),source_compressed_bytes=p.stat().st_size,
                first_timestamp=str(min_timestamp),last_timestamp=str(max_timestamp),
                bad_identity=bad_id,bad_numeric=bad_num,outside_utc_day=outside,
                mismatches=mismatches,qc_pass=not any(mismatches.values()) and
                bad_id==0 and bad_num==0 and outside==0 and total_count==primary["source_fills"])

def period(symbol,days=30):
    if symbol not in COHORT or not 1<=days<=30:raise ValueError("SCOPE")
    b=PRIMARY.read_bytes();blob=hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()
    if blob!=PRIMARY_BLOB:raise ValueError("UNVERIFIED_PRIMARY_BLOB")
    sp=importlib.util.spec_from_file_location("pinned_primary",PRIMARY)
    mod=importlib.util.module_from_spec(sp);sp.loader.exec_module(mod)
    t=time.monotonic();daily=[]
    for k in range(days):
        day=(START+dt.timedelta(days=k))
        if day>END:raise ValueError("OUT_OF_BOUNDS")
        daily.append(calc_one(mod,symbol,day.isoformat()))
    return dict(symbol=symbol,start=str(START),end=daily[-1]["day"],days=days,
                primary_blob=blob,rows=sum(x["source_fills"] for x in daily),
                minutes=days*1440,field_comparisons=days*1440*len(KEYS),
                mismatches=sum(sum(x["mismatches"].values()) for x in daily),
                all_daily_qc_pass=all(x["qc_pass"] for x in daily),
                elapsed_seconds=round(time.monotonic()-t,2),daily=daily,
                status="30D_CONTROL_SAMPLE_NUMERIC_QC_NOT_RESEARCH_READY",
                warnings=["UTC midnight resets CVD and RVOL history for first 60m in each file",
                          "Exchange event timestamps only; receipt and publication clocks unknown",
                          "Only 8 minute-field QC; not indicator #4/#8/#27/#28 independent row QC",
                          "No OOS or net-after-fees trade results"])
if __name__=="__main__":
    a=argparse.ArgumentParser()
    a.add_argument("--symbol",choices=COHORT,required=True)
    a.add_argument("--days",type=int,default=30)
    x=a.parse_args()
    print(json.dumps(period(x.symbol,x.days),separators=(",",":")))
