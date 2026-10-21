#!/usr/bin/env python3
"""CRIPTA independent historical association screen, NOT a trading signal.

READ ONLY, exact actor cripta. Inputs are frozen 30-day Bybit compressed public
trade archives and independently QC-verified physical six-factor minute cube.
Three exploratory future labels: 5, 15, 60 complete-minute price returns.
Temporal train 20 days / validation 3 / held-out late 7; all 60m label
endpoints are strictly within the same split. Train-only q20/q80 feature
cutoffs. Negative control: prior-day same-minute feature placebo, holdout
days 2–7 only. Outcome is historical, NOT causal historical availability,
not RESEARCH-READY, not tradable, and not after-commission P&L.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pwd
import statistics
import time
from decimal import Decimal
from pathlib import Path

SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
HORIZONS=(5,15,60)
IDS=(1,2,3,5,6,7)
DAY0=dt.date(2026,5,18)
DAYS=30
ROOT=Path("/data/cripta/datasets/raw/20260518_20260816")
BASE=Path("/data/cripta/research/runs/trade_minute_cube_20261021_30d_v1")
CODE=Path("/srv/cripta/source_checkout/research/microstructure")
SOURCE_MANIFEST=CODE/"TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
SOURCE_MANIFEST_BLOB="30b87fe11fb3a1bfc6fb59c8866091e92e823203"
CUBE_QC=CODE/"TRADE_MINUTE_5COINS_30DAY_6FACTOR_CUBE_QC_2026-10-21.json"
CUBE_QC_BLOB="deeafdc1b460195f53831fd7c718d46ec8d80d52"
SPLITS={"train":(0,20),"validation":(20,23),"late_holdout":(23,30)}

def git_blob(path):
    b=path.read_bytes()
    return hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1048576),b""):
            h.update(block)
    return h.hexdigest()

def q(a,p):
    s=sorted(a)
    return s[int(p*(len(s)-1))] if s else None

def mean(a):
    return sum(a)/len(a) if a else None

def pearson(xs,ys):
    n=len(xs)
    if n<3:return None
    ax=sum(xs)/n;ay=sum(ys)/n
    ssx=sum((v-ax)**2 for v in xs)
    ssy=sum((v-ay)**2 for v in ys)
    if not ssx or not ssy:return None
    return sum((x-ax)*(y-ay) for x,y in zip(xs,ys))/(ssx*ssy)**0.5

def summarize(f, returns, counts, low,high, offset=0):
    lows=[];highs=[];n=0;xs=[];ys=[];daybin={}
    for i in range(offset,len(f)):
        if returns[i] is None or counts[i]==0 or f[i] is None:continue
        x=f[i];y=returns[i];n+=1;xs.append(x);ys.append(y)
        group="hi" if x>=high else ("lo" if x<=low else None)
        if group:
            (highs if group=="hi" else lows).append(y)
            day=i//1440
            if day not in daybin:daybin[day]={"hi":[],"lo":[]}
            daybin[day][group].append(y)
    daily_lifts=[mean(v["hi"])-mean(v["lo"]) for v in daybin.values() if v["hi"] and v["lo"]]
    return dict(eligible_labels=n,high_count=len(highs),low_count=len(lows),
        avg_future_ret_bps=round(mean(ys),5) if ys else None,
        high_mean_bps=round(mean(highs),5) if highs else None,
        low_mean_bps=round(mean(lows),5) if lows else None,
        high_minus_low_bps=round(mean(highs)-mean(lows),5) if highs and lows else None,
        linear_r=round(pearson(xs,ys),6) if pearson(xs,ys) is not None else None,
        daily_lift_days=len(daily_lifts),
        daily_lift_positive_days=sum(v>0 for v in daily_lifts),
        day_median_high_minus_low_bps=round(statistics.median(daily_lifts),5) if daily_lifts else None)

def compute(sym,days=30):
    if os.geteuid()!=pwd.getpwnam("cripta").pw_uid:raise PermissionError("ACTOR")
    if sym not in SYMS or days not in (2,30):raise ValueError("SCOPE")
    if git_blob(SOURCE_MANIFEST)!=SOURCE_MANIFEST_BLOB:raise ValueError("SOURCE_MANIFEST_DRIFT")
    if git_blob(CUBE_QC)!=CUBE_QC_BLOB:raise ValueError("CUBE_CHECKPOINT_DRIFT")
    records=json.loads(SOURCE_MANIFEST.read_text())["source_files"]
    refs={x["date_utc"]:x for x in records if x["symbol"]==sym}
    qc=json.loads(CUBE_QC.read_text())
    cube_rec=next((x for x in qc["by_symbol"] if x["symbol"]==sym),None)
    if cube_rec is None or not cube_rec["independent_qc_pass"]:raise ValueError("UNVERIFIED_CUBE")
    cube=BASE/(sym+"_6factor.csv.gz")
    if sha(cube)!=cube_rec["sha"]:raise ValueError("CUBE_SHA_DRIFT")
    nmin=days*1440
    features={i:[None]*nmin for i in IDS}
    fills=[0]*nmin
    with gzip.open(cube,"rt",newline="") as fp:
        reader=csv.DictReader(fp)
        for index,row in enumerate(reader):
            if index>=nmin*len(IDS):break
            minute=index//len(IDS);indic=IDS[index%len(IDS)]
            if row["instrument_id"]!=sym or row["indicator_id"]!=str(indic):
                raise ValueError("CUBE_ORDER")
            if row["observed_at_utc"] or row["verified_available_after_utc"]:
                raise ValueError("UNEXPECTED_RECEIPT_CLOCK")
            if index%len(IDS)==0:
                fills[minute]=int(row["input_fill_count"])
            if row["value"]:
                value=float(row["value"])
                if not math.isfinite(value):raise ValueError("NONFINITE_FACTOR")
                features[indic][minute]=value
        if index+1<nmin*len(IDS):raise ValueError("SHORT_CUBE")
    close=[None]*nmin;trade_count=[0]*nmin
    sha_list=[];total=0
    t0=time.monotonic()
    for day_ix in range(days):
        day=DAY0+dt.timedelta(days=day_ix)
        name=day.isoformat()
        raw=ROOT/sym/"public_trades"/(sym+name+".csv.gz")
        expected=refs[name]["compressed_sha256"]
        dig=sha(raw)
        if dig!=expected:raise ValueError("TRADE_SHA_DRIFT "+name)
        sha_list.append(dict(date_utc=name,raw_sha256=dig))
        start=int(dt.datetime.combine(day,dt.time(0),dt.timezone.utc).timestamp())
        last_ts=None
        with gzip.open(raw,"rt",newline="") as fp:
            for row in csv.DictReader(fp):
                t=Decimal(row["timestamp"])
                if last_ts is not None and t<last_ts:raise ValueError("TIMESTAMP_NONMONOTONE")
                last_ts=t
                minute=int((t-Decimal(start))//60)
                if minute<0 or minute>=1440:raise ValueError("RAW_DAY_OUTSIDE")
                ix=day_ix*1440+minute
                if row["symbol"]!=sym:raise ValueError("SYMBOL_IDENTITY")
                price=float(row["price"])
                if price<=0 or not math.isfinite(price):raise ValueError("BAD_PRICE")
                close[ix]=price
                trade_count[ix]+=1;total+=1
        for ix in range(day_ix*1440,(day_ix+1)*1440):
            if trade_count[ix]!=fills[ix]:
                raise ValueError("CUBE_RAW_FILL_COUNT_DRIFT "+name+" minute="+str(ix))
    if days==2:
        return dict(status="SMOKE_ONLY",instrument_id=sym,source_days=2,
                    verified_files=len(sha_list),source_fills=total,
                    active_minutes=sum(v>0 for v in trade_count),
                    cube_rows_read=nmin*len(IDS),cube_source_sha256_verified=True,
                    source_list=sha_list,seconds=round(time.monotonic()-t0,3))
    labels={}
    outcome_coverage={}
    for horizon in HORIZONS:
        labels[horizon]={}
        outcome_coverage[horizon]={}
        for fold,(a,b) in SPLITS.items():
            start=a*1440;stop=b*1440
            vals=[None]*nmin
            for t in range(start,stop-horizon):
                future=t+horizon
                if close[t] is None or close[future] is None:continue
                vals[t]=10000*math.log(close[future]/close[t])
            labels[horizon][fold]=vals
            outcome_coverage[horizon][fold]=sum(x is not None for x in vals)
    grid=[]
    for ident in IDS:
        arr=features[ident]
        train_a,train_b=SPLITS["train"]
        observed_train=[arr[i] for i in range(train_a*1440,train_b*1440)
                        if arr[i] is not None and trade_count[i]>0]
        low=q(observed_train,.20);high=q(observed_train,.80)
        if low is None or high is None:raise ValueError("MISSING_TRAIN_THRESHOLDS")
        for horizon in HORIZONS:
            row=dict(indicator_id=ident,horizon_minutes=horizon,
                     train_threshold_low_q20=round(low,9),
                     train_threshold_high_q80=round(high,9),
                     train_nonempty_feature_minutes=len(observed_train),fold={})
            for fold in SPLITS:
                a,b=SPLITS[fold]
                start=a*1440;stop=b*1440
                f=arr[start:stop]
                vals=labels[horizon][fold][start:stop]
                counts=trade_count[start:stop]
                row["fold"][fold]=summarize(f,vals,counts,low,high)
            a,b=SPLITS["late_holdout"]
            start=(a+1)*1440;stop=b*1440
            shifted=arr[start-1440:stop-1440]
            placebo=summarize(shifted,labels[horizon]["late_holdout"][start:stop],
                              trade_count[start:stop],low,high)
            row["late_holdout_prior_day_feature_placebo"]=placebo
            grid.append(row)
    return dict(
        status="EX_POST_HISTORICAL_ASSOCIATION_ONLY_NOT_RESEARCH_READY_NOT_CAUSAL_SIGNAL",
        instrument_id=sym,source="Bybit linear public trade prints",
        source_fills=total,source_days=days,source_sha256_files_verified=len(sha_list),
        physical_cube_sha256=cube_rec["sha"],physical_cube_rows=nmin*len(IDS),
        cube_independent_qc_already_passed=True,
        physical_indicators=list(IDS),horizons_minutes=list(HORIZONS),
        label_definition="10000*ln(last_trade_price_in_minute_t_plus_h / last_trade_price_in_minute_t); both minute buckets must have real fills; target within same fold",
        train="2026-05-18..2026-06-06",validation="2026-06-07..2026-06-09",
        late_holdout="2026-06-10..2026-06-16",
        train_deciles="q20 q80 fitted on TRAIN features only, no label-dependent threshold fitting",
        placebo="previous-day same-clock-minute feature (holdout last six days), thresholds frozen on train",
        folds_purged_at_end_by_horizon=True,
        minute_anchor="ex-post minute last execution price, source event clock only; unknown actual observed_at",
        per_horizon_label_coverage=outcome_coverage,
        grid=grid,compute_seconds=round(time.monotonic()-t0,2),
        caveats=["Overlapping labels and serial dependence; minute counts are not independent observations",
                "No receiver observed_at or source publication clock: no causal decision-time certification",
                "This is exploratory, not a preregistered signal, trading backtest or net after-commission PnL",
                "Single 30-day historical period cannot establish transferable predictive edge",
                "No across-coin pooled effect or policy threshold selection"])

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--symbol",required=True,choices=SYMS)
    ap.add_argument("--smoke",action="store_true")
    args=ap.parse_args()
    print(json.dumps(compute(args.symbol,days=2 if args.smoke else 30),
                     ensure_ascii=False,separators=(",",":")))
