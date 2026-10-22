#!/usr/bin/env python3
"""CRIPTA RESEARCH ONLY: trade microstructure 4-factor per-minute exact-event-time pilot.
Source is immutable Bybit public-trades; no trading/DB/network or canonical policy.
Feature bar timestamp denotes *exchange* end of minute, not receiver availability.
"""
import argparse
import csv
import datetime as dt
from decimal import Decimal
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import sys

SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
RAW=Path("/data/cripta/datasets/raw/20260518_20260816")
MAN=Path("/srv/cripta/source_checkout/research/microstructure/TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json")
RUNS=Path("/data/cripta/research/runs/microstructure_joint_4f_20261023")
FORMULAS={
  "indicator_4":"quote_notional_share_of_minute_fills_above_previous_60_complete_minutes_exact_p99",
  "indicator_8":"max_same_side_streak_of_contiguous_timestamp_plus_side_groups_within_one_minute",
  "indicator_27":"median_strictly_positive_gap_milliseconds_between_adjacent_fills_in_one_minute",
  "indicator_28":"p90_quote_notional_per_fill_in_complete_current_minute",
  "indicator_2":"number_of_fills_in_complete_minute",
  "indicator_5":"buy_quote_notional_minus_sell_quote_notional_in_complete_minute",
}

def sha256(path):
 h=hashlib.sha256()
 with path.open("rb") as f:
  for chunk in iter(lambda:f.read(1048576),b""):h.update(chunk)
 return h.hexdigest()

def percentile_sorted(a,q):
 return a[int(q*(len(a)-1))] if a else None

def write_atomic(path,payload):
 tmp=path.with_name(path.name+".partial")
 with tmp.open("w",encoding="utf-8") as f:
  json.dump(payload,f,ensure_ascii=False,sort_keys=True,indent=2)
  f.write("\n")
  f.flush()
  os.fsync(f.fileno())
 tmp.replace(path)

def read_source_scope(symbol,day,minutes):
 utc=dt.datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
 if not dt.date(2026,5,18)<=utc.date()<=dt.date(2026,6,16):
  raise ValueError("OUTSIDE_FROZEN_FIRST_MONTH")
 start=Decimal(int(utc.timestamp()))
 raw=RAW/symbol/"public_trades"/f"{symbol}{day}.csv.gz"
 if not raw.is_file():raise FileNotFoundError(raw)
 man=json.loads(MAN.read_text())
 matches=[x for x in man["source_files"] if x["symbol"]==symbol and x["date_utc"]==day]
 if len(matches)!=1:raise ValueError("SOURCE_SCOPE_MANIFEST_MISSING_OR_AMBIGUOUS")
 realhash=sha256(raw)
 if realhash!=matches[0]["compressed_sha256"]:raise ValueError("SOURCE_SHA256_MISMATCH")
 return start,raw,realhash

def compute(symbol,day,minutes,start,raw):
 samples=[{"amounts":[],"gaps_ms":[],"fills":0,"buy":0.0,"sell":0.0,
  "last":None,"high":None,"low":None,"last_ts":None,"group_key":None,
  "group_streak":0,"max_group_streak":0,"groups":0,"ties":0} for _ in range(minutes)]
 processed=0
 prev_ts=None
 with gzip.open(raw,"rt",newline="") as f:
  for row in csv.DictReader(f):
   t=Decimal(row["timestamp"])
   if prev_ts is not None and t<prev_ts:raise ValueError("RAW_TS_NONMONOTONE")
   prev_ts=t
   i=int((t-start)//60)
   if i>=minutes:break
   if i<0:raise ValueError("RAW_BEFORE_DAY")
   if row["symbol"]!=symbol or row["side"] not in ("Buy","Sell"):
    raise ValueError("RAW_SYMBOL_OR_SIDE")
   amt=float(row["foreignNotional"]);price=float(row["price"])
   if not(math.isfinite(amt) and amt>=0 and math.isfinite(price) and price>0):
    raise ValueError("RAW_BAD_VALUE")
   m=samples[i]
   m["fills"]+=1;m["amounts"].append(amt)
   if row["side"]=="Buy":m["buy"]+=amt
   else:m["sell"]+=amt
   if m["last_ts"] is not None:
    delta=(t-m["last_ts"])*Decimal(1000)
    if delta>0:m["gaps_ms"].append(float(delta))
    elif delta==0:m["ties"]+=1
    else:raise ValueError("MINUTE_TS_NONMONOTONE")
   m["last_ts"]=t
   m["last"]=price
   m["high"]=max(price,m["high"]) if m["high"] is not None else price
   m["low"]=min(price,m["low"]) if m["low"] is not None else price
   key=(t,row["side"])
   if key != m["group_key"]:
    m["groups"]+=1
    m["group_streak"]=m["group_streak"]+1 if m["group_key"] is not None and m["group_key"][1]==row["side"] else 1
    m["max_group_streak"]=max(m["max_group_streak"],m["group_streak"])
    m["group_key"]=key
   processed+=1
 out=[]
 for i,m in enumerate(samples):
  a=m["amounts"]
  ratio=None;hist_n=0;hist_p99=None
  if i>=60 and a:
   hist=[x for b in samples[i-60:i] for x in b["amounts"]]
   hist_n=len(hist)
   if hist_n>=100:
    hist.sort();hist_p99=percentile_sorted(hist,.99)
    denom=sum(a)
    if denom>0:ratio=sum(x for x in a if x>=hist_p99)/denom
  if m["fills"]==0 and any((m["last"] is not None,m["groups"]!=0,m["gaps_ms"])):
   raise ValueError("EMPTY_MINUTE_BAD_STATE")
  p90=percentile_sorted(sorted(a),.9) if a else None
  gap=statistics.median(m["gaps_ms"]) if m["gaps_ms"] else None
  out.append(dict(minute_index=i,symbol=symbol,date_utc=day,
     event_minute_utc=(dt.datetime.fromtimestamp(float(start),tz=dt.timezone.utc)+dt.timedelta(minutes=i)).isoformat(),
     event_window_end_exclusive_utc=(dt.datetime.fromtimestamp(float(start),tz=dt.timezone.utc)+dt.timedelta(minutes=i+1)).isoformat(),
     observed_at_utc=None,available_after_utc=None,
     quality="OBSERVED_EXCHANGE_TAPE" if a else "NO_DATA",
     factor_4_large_trade_share=ratio,prior60_n=hist_n,prior60_p99=hist_p99,
     factor_8_max_timestamp_side_group_streak=m["max_group_streak"] if a else None,
     factor_27_median_positive_gap_ms=gap,
     factor_28_minute_fill_notional_p90_usdt=p90,
     factor_2_trade_rate=m["fills"] if a else None,
     factor_5_delta_usdt=m["buy"]-m["sell"] if a else None,
     input_fill_count=m["fills"],timestamp_side_groups=m["groups"],
     tied_adjacent_fill_timestamps=m["ties"],price_close=m["last"],
     price_high=m["high"],price_low=m["low"]))
 labels={str(h):{"eligible":0,"positive":0,"negative":0,"zero":0,"mean_return_bps":None} for h in (5,15,60)}
 for h in (5,15,60):
  returns=[]
  for i in range(minutes-h):
   if not out[i]["input_fill_count"]:continue
   if not all(out[k]["input_fill_count"]>0 for k in range(i+1,i+h+1)):continue
   v=10000*math.log(out[i+h]["price_close"]/out[i]["price_close"])
   returns.append(v)
  labels[str(h)]={"eligible":len(returns),"positive":sum(v>0 for v in returns),
    "negative":sum(v<0 for v in returns),"zero":sum(v==0 for v in returns),
    "mean_return_bps":sum(returns)/len(returns) if returns else None}
 return out,labels,processed

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--symbol",choices=SYMS,required=True)
 p.add_argument("--date",required=True)
 p.add_argument("--minutes",type=int,default=120)
 a=p.parse_args()
 if not(120<=a.minutes<=1440):raise ValueError("WINDOW_MUST_INCLUDE_PRIOR60_AND_FUTURE60")
 import pwd
 if os.geteuid()!=pwd.getpwnam("cripta").pw_uid:raise PermissionError("ACTOR_NOT_CRIPTA")
 if not os.access(RUNS.parent,os.W_OK|os.X_OK):raise PermissionError("RUN_PARENT_NOT_WRITABLE")
 start,raw,hashsource=read_source_scope(a.symbol,a.date,a.minutes)
 run=RUNS/(a.symbol+"_"+a.date+"_"+str(a.minutes)+"m_v1")
 if run.exists():raise FileExistsError("NO_OVERWRITE_EXISTING_RUN "+str(run))
 run.mkdir(parents=True,exist_ok=False)
 scriptpath=Path(__file__).resolve()
 snapshot=run/"source_snapshot.py"
 shutil.copyfile(scriptpath,snapshot)
 command="sudo -u cripta python3 -B "+str(scriptpath)+" --symbol "+a.symbol+" --date "+a.date+" --minutes "+str(a.minutes)
 write_atomic(run/"run_manifest.json",dict(
  status="RUNNING",symbol=a.symbol,date=a.date,minutes=a.minutes,uid=os.geteuid(),
  source_file=str(raw),source_sha256=hashsource,
  program=str(scriptpath),program_sha256=sha256(scriptpath),
  snapshot_sha256=sha256(snapshot),command=command,formula_contract=FORMULAS,
  source_receipt_clock="NOT AVAILABLE IN PUBLIC TRADE GZIP",
  note="PILOT PHYSICAL MEASUREMENT, NOT INDEPENDENT DERIVED QC, NOT RESEARCH READY"))
 rows,labels,processed=compute(a.symbol,a.date,a.minutes,start,raw)
 if sum(x["input_fill_count"] for x in rows)!=processed:raise ValueError("ROW_SUM_FAILURE")
 if any(r["factor_4_large_trade_share"] is not None for r in rows[:60]):
  raise ValueError("PRIOR60_LEAKAGE")
 if any(r["input_fill_count"]==0 and r["factor_28_minute_fill_notional_p90_usdt"] is not None for r in rows):
  raise ValueError("MISSING_NOT_NULL")
 with gzip.open(run/"physical_features.jsonl.gz","wt",encoding="utf-8") as f:
  for row in rows:f.write(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n")
 outputs={"physical_features.jsonl.gz":sha256(run/"physical_features.jsonl.gz")}
 result=dict(status="PILOT_COMPLETED_NOT_INDEPENDENT_QC",
  actor_uid=os.geteuid(),instrument=a.symbol,day=a.date,minutes=a.minutes,
  raw_sha256=hashsource,source_count_prefix=processed,
  physical_rows=len(rows),observed_minutes=sum(x["input_fill_count"]>0 for x in rows),
  large_share_eligible_minutes=sum(x["factor_4_large_trade_share"] is not None for x in rows),
  streak_observed_minutes=sum(x["factor_8_max_timestamp_side_group_streak"] is not None for x in rows),
  duration_observed_minutes=sum(x["factor_27_median_positive_gap_ms"] is not None for x in rows),
  size_observed_minutes=sum(x["factor_28_minute_fill_notional_p90_usdt"] is not None for x in rows),
  labels=labels,outputs_sha256=outputs,program_sha256=sha256(snapshot),
  limitations=["Pilot only; no independent four-factor full-row QC","historical exchange event-time not receiver as-of",
  "large fill 99th percentile uses previous complete 60 minutes, sample >=100",
  "timestamp-side grouping does not identify parent orders","minute p90 is known at complete bar, not during that minute",
  "future label inside partial/day boundary only",
  "previous-60-minute threshold resets at UTC day start: first 60 minutes deliberately NO_DATA, not full-month continuity",
  "not OOS, no fee/PnL or Strategy implication"])
 write_atomic(run/"result.json",result)
 write_atomic(run/"run_manifest.json",{**json.loads((run/"run_manifest.json").read_text()),"status":"PILOT_COMPLETE","outputs_sha256":outputs,"result_sha256":sha256(run/"result.json")})
 print(json.dumps(dict(run_dir=str(run),**result),ensure_ascii=False,separators=(",",":")))

if __name__=="__main__":main()
