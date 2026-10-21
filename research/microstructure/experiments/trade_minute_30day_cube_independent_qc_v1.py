#!/usr/bin/env python3
"""Independent read-only QC for CRIPTA 30-day six-factor physical measure cube.

EFFECTIVE_ACTOR: cripta
READ_PATHS: /data/cripta/research/runs/trade_minute_cube_20261021_30d_v1
OUTPUT: stdout JSON only; no server filesystem mutation or DB/exchange access.
Historical feed receipt/observed_at is UNKNOWN, must never be fabricated.
This checker validates source lineage, row cardinality, timing and quality labels;
it does NOT certify predictive value, source capture completeness or RESEARCH-READY.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import pwd
import stat

ROOT=pathlib.Path("/data/cripta/research/runs/trade_minute_cube_20261021_30d_v1")
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
DAY0=dt.date(2026,5,18)
DAYS=30
FIELDS=("venue_id","instrument_id","market_source_id","indicator_id","formula_version",
        "window_start_utc","derived_window_end_utc","observed_at_utc","verified_available_after_utc",
        "value","unit","quality","availability_quality","aggregation_operator",
        "source_archive_sha256","input_fill_count")
FEATURE_IDS=(1,2,3,5,6,7)
UNITS=("ratio","fill_count","fill_count_change","USDT","USDT","USDT_change")
AGGS=("NON_AGGREGATABLE","ADDITIVE","NON_AGGREGATABLE",
      "ADDITIVE","NON_AGGREGATABLE","NON_AGGREGATABLE")
PRIMARY="trade_minute_factors_v2_exact_timestamp_floor"

def sha256_file(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()

def check(symbol):
    if os.geteuid()!=pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    if symbol not in SYMS:raise ValueError("UNKNOWN_SYMBOL")
    inp=ROOT/(symbol+"_6factor.csv.gz")
    man=ROOT/(symbol+"_MANIFEST.json")
    if not inp.is_file() or not man.is_file():
        raise FileNotFoundError("CUBE_OR_MANIFEST_MISSING")
    if inp.is_symlink() or man.is_symlink():raise ValueError("SYMLINK_NOT_ALLOWED")
    m=json.loads(man.read_text(encoding="utf-8"))
    if m.get("status")!="PILOT_PHYSICAL_MEASURE_CUBE_NOT_RESEARCH_READY":
        raise ValueError("UNEXPECTED_READINESS")
    if m.get("instrument_id")!=symbol or m.get("source_days")!=DAYS:
        raise ValueError("MANIFEST_SCOPE")
    if m.get("actual_source_arrival_clock")!="NOT_CAPTURED":
        raise ValueError("FABRICATED_RECEIPT_CLOCK")
    if m.get("verified_available_after")!="UNKNOWN":
        raise ValueError("FABRICATED_AVAILABILITY")
    if m.get("indicator_time_rows")!=DAYS*1440*len(FEATURE_IDS):
        raise ValueError("MANIFEST_CARDINALITY")
    if m.get("output_file")!=str(inp) or m.get("output_sha256")!=sha256_file(inp):
        raise ValueError("MANIFEST_OUTPUT_SHA")
    if m.get("output_bytes")!=inp.stat().st_size:
        raise ValueError("MANIFEST_OUTPUT_SIZE")
    src_rows=m.get("source_inputs",[])
    if len(src_rows)!=DAYS:raise ValueError("MANIFEST_DAY_FILES")
    dayhash={}
    for i,src in enumerate(src_rows):
        expected_day=(DAY0+dt.timedelta(days=i)).isoformat()
        if src.get("day")!=expected_day:raise ValueError("DAY_MANIFEST_ORDER")
        raw=src.get("source_sha256")
        if not isinstance(raw,str) or len(raw)!=64:
            raise ValueError("BAD_SOURCE_SHA")
        dayhash[expected_day]=raw
    mismatches={}
    def fail(code):
        mismatches[code]=mismatches.get(code,0)+1
    nulls={str(x):0 for x in FEATURE_IDS}
    rows=0;minutes=0;nonempty=0
    current_day=None
    total_fills_by_day={}
    with gzip.open(inp,"rt",newline="",encoding="utf-8") as f:
        reader=csv.DictReader(f)
        if tuple(reader.fieldnames or [])!=FIELDS:raise ValueError("HEADER_MISMATCH")
        for row in reader:
            day_index=rows//(1440*len(FEATURE_IDS))
            minute_index=(rows//len(FEATURE_IDS))%1440
            feature_index=rows%len(FEATURE_IDS)
            if day_index>=DAYS:
                fail("TOO_MANY_ROWS")
                break
            day=(DAY0+dt.timedelta(days=day_index))
            minute_start=dt.datetime.combine(day,dt.time(0),dt.timezone.utc)+dt.timedelta(minutes=minute_index)
            minute_end=minute_start+dt.timedelta(minutes=1)
            ident=FEATURE_IDS[feature_index]
            if row["venue_id"]!="Bybit" or row["instrument_id"]!=symbol:
                fail("INSTRUMENT_IDENTITY")
            if row["market_source_id"]!="BYBIT_PUBLIC_TRADES_ARCHIVE":
                fail("SOURCE_IDENTITY")
            if row["formula_version"]!=PRIMARY:fail("FORMULA_VARIANT")
            if row["indicator_id"]!=str(ident):fail("FACTOR_ORDER")
            if row["window_start_utc"]!=minute_start.isoformat():
                fail("MINUTE_START")
            if row["derived_window_end_utc"]!=minute_end.isoformat():
                fail("DERIVED_END")
            if row["observed_at_utc"] or row["verified_available_after_utc"]:
                fail("UNPROVEN_CAUSAL_CLOCK")
            if row["unit"]!=UNITS[feature_index]:fail("UNIT")
            if row["aggregation_operator"]!=AGGS[feature_index]:
                fail("AGGREGATION")
            if row["source_archive_sha256"]!=dayhash[day.isoformat()]:
                fail("SOURCE_LINEAGE")
            if row["availability_quality"]!="UNKNOWN_NO_RECEIPT_PUBLICATION_CLOCK":
                fail("SOURCE_AVAILABILITY_CLASSIFICATION")
            try:
                fills=int(row["input_fill_count"])
                if fills<0:fail("NEGATIVE_PRINT_COUNT")
                if row["value"]:
                    value=float(row["value"])
                    if not math.isfinite(value):fail("NONFINITE_VALUE")
                    if ident in (1,2) and value<0:fail("NEGATIVE_NONNEG_FACTOR")
                    if ident==2 and value!=fills:fail("TRADE_RATE_COUNT_PARITY")
                    if row["quality"] not in (
                        "VALUE_ARCHIVE_NO_PRINTS_EVENT_COMPLETENESS_UNVERIFIED",
                        "VALUE_NUMERIC_QC_PASS_SOURCE_COMPLETENESS_UNVERIFIED"):
                        fail("NONEMPTY_QUALITY")
                else:
                    nulls[str(ident)]+=1
                    if row["quality"]!="NO_DATA_INSUFFICIENT_LOOKBACK_OR_UNDEFINED":
                        fail("MISSING_QUALITY")
                if minute_index<60 and ident==1 and row["value"]:
                    fail("RVOL_WARMUP_LOOKAHEAD")
                if ident==2:
                    total_fills_by_day[day.isoformat()]=total_fills_by_day.get(day.isoformat(),0)+fills
                    minutes+=1;nonempty+=int(fills>0)
            except (ValueError,TypeError):
                fail("BAD_VALUE_OR_FILL_COUNT")
            rows+=1
    if rows!=DAYS*1440*len(FEATURE_IDS):fail("ROW_COUNT")
    for day,expected in zip(dayhash,src_rows):
        if total_fills_by_day.get(day)!=expected.get("source_fills"):
            fail("DAILY_INPUT_FILLS")
    if nulls!=m.get("null_counts"):fail("NULL_COUNTS")
    mode=stat.S_IMODE(inp.stat().st_mode)
    if inp.stat().st_uid!=os.geteuid():fail("FILE_OWNER")
    return dict(instrument_id=symbol,output_file=str(inp),manifest_file=str(man),
        output_sha256=m.get("output_sha256"),rows=rows,minute_rows=minutes,
        nonempty_minute_rows=nonempty,nulls=nulls,source_days=DAYS,
        file_mode=oct(mode),mismatches=mismatches,pass_all=not mismatches,
        gate="CONTROL_PHYSICAL_CUBE_QC_ONLY_NOT_RESEARCH_READY")

if __name__=="__main__":
    arg=argparse.ArgumentParser()
    arg.add_argument("--symbol",required=True,choices=SYMS)
    a=arg.parse_args()
    print(json.dumps(check(a.symbol),ensure_ascii=False,separators=(",",":")))
