#!/usr/bin/env python3
"""One-day causal minute evidence pilot. Never deletes or modifies raw inputs."""
import os, pathlib, importlib.util, datetime as dt, json, gzip, hashlib, shutil, stat, pwd, time, math
from decimal import Decimal

SYMBOL="APTUSDT"
DAY=dt.date(2026,5,18)
BASE=pathlib.Path("/srv/cripta/source_checkout/research/microstructure/experiments")
ROOT=pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
PARENT=pathlib.Path("/data/cripta/research/runs")
DEST=PARENT/"minute_snapshot_pilot_APTUSDT_20260518_20261105"
CODE_FILES=(
    "trade_minute_factors_v2_exact_timestamp_floor.py",
    "r158_firstmonth_book9_11_29_utcclip_v2.py",
    "r158_firstmonth_book12_visible_decrease_v1.py",
    "trade_minute_full_row_crosscheck_v2.py",
    "r158_firstmonth_book9_11_29_utcclip_independent_qc_v2.py",
    "r158_firstmonth_book12_visible_decrease_independent_qc_v1.py",
)
EXPECTED_SHA={
 "trade_minute_factors_v2_exact_timestamp_floor.py":"2e2b21544b28d83a5d4c9de9a1bf4b855b5a6046057cfd5aed2b06362343f85e",
 "r158_firstmonth_book9_11_29_utcclip_v2.py":"0c2c094592a4adf84c59b4c7bacecb4bd286091d95d1016613844caae4491cae",
 "r158_firstmonth_book12_visible_decrease_v1.py":"197ec366a9988800e6c8838c9ad36937fc7331480fc2eeb6ac0579f53f28e9ea",
}
EXPECTED_GZIP_SHA="99339d033b2cc60f8dabdad4387700f45b96d7d41223987888b364c6f060ed64"
EXPECTED_GZIP_SIZE=268455
EXPECTED_TRADE_SHA="5b5c51902944f3cb6dc8be4914d6ff0b2e45b1aa965d010ee9cecfe028b1dbf4"
EXPECTED_BOOK_SHA="7c1719473b7105b8a4210ffd0fb2f702139bd1398af772c2fc3ba34f94494beb"

def digest(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""):
            h.update(b)
    return h.hexdigest()

def module(n):
    p=BASE/n
    sp=importlib.util.spec_from_file_location("snap_"+n.replace(".","_"),p)
    m=importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m

def write_new(path,binary):
    # For this new run only: no overwrite, no delete of any existing file.
    if path.exists() or pathlib.Path(str(path)+".part").exists():
        raise FileExistsError(str(path))
    tmp=pathlib.Path(str(path)+".part")
    with tmp.open("xb") as f:
        f.write(binary)
        f.flush()
        os.fsync(f.fileno())
    tmp.rename(path)

def jsonbytes(value):
    return (json.dumps(value,sort_keys=True,indent=2,ensure_ascii=False)+"\n").encode()

def main():
    started=time.monotonic()
    if os.geteuid()!=pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("RESEARCH_WRITER_MUST_BE_CRIPTA")
    if DEST.exists():
        raise FileExistsError("IMMUTABLE_RUN_ALREADY_EXISTS")
    if not os.access(PARENT,os.W_OK|os.X_OK):
        raise PermissionError("PARENT_CREATE_DENIED")
    if shutil.disk_usage(PARENT).free<1024**3:
        raise RuntimeError("DATA_DISK_BELOW_SAFETY_RESERVE")
    raw_tr=ROOT/SYMBOL/"public_trades"/f"{SYMBOL}{DAY.isoformat()}.csv.gz"
    raw_ob=ROOT/SYMBOL/"orderbook"/f"{DAY.isoformat()}_{SYMBOL}_ob200.data.zip"
    for f in (raw_tr,raw_ob):
        if not f.is_file() or not os.access(f,os.R_OK):
            raise FileNotFoundError(str(f))
    src_hash={n:digest(BASE/n) for n in CODE_FILES}
    if any(src_hash[n]!=v for n,v in EXPECTED_SHA.items()):
        raise ValueError("EXISTING_RESEARCH_CODE_DRIFT")
    inputs={str(p):{"sha256":digest(p),"bytes":p.stat().st_size} for p in (raw_tr,raw_ob)}
    if inputs[str(raw_tr)]["sha256"]!=EXPECTED_TRADE_SHA or inputs[str(raw_ob)]["sha256"]!=EXPECTED_BOOK_SHA:
        raise ValueError("FROZEN_INPUT_SHA_MISMATCH")
    os.umask(0o007)
    DEST.mkdir(mode=0o2770)
    source_dir=DEST/"source_snapshot"
    source_dir.mkdir(mode=0o2770)
    write_new(source_dir/"native_minute_snapshot_pilot_v1.py",globals()["__snapshot_source__"].encode())
    for n in CODE_FILES:
        write_new(source_dir/n,(BASE/n).read_bytes())
    write_new(DEST/"run_manifest.json",jsonbytes({
        "status":"RUNNING","actor":"cripta","scope":"APTUSDT ONE DAY, RESEARCH ONLY",
        "period":"2026-05-18T00:00:00Z/2026-05-19T00:00:00Z",
        "source_commit":"50309343ed46b034a7d10c1da8c965e8be8344a1",
        "source_hashes":src_hash,"input_provenance":inputs,
        "raw_delete_allowed":False,"no_exchange_or_db_mutation":True,
    }))
    t=module(CODE_FILES[0]);ob=module(CODE_FILES[1]);dec=module(CODE_FILES[2])
    tq=module(CODE_FILES[3]);bq=module(CODE_FILES[4]);dq=module(CODE_FILES[5])
    trades=t.calculate(SYMBOL,DAY.isoformat())
    br,bmeta=ob.day_rows(SYMBOL,DAY,raw_ob)
    dr,dmeta=dec.snapshots_and_deltas(DAY,raw_ob,SYMBOL)
    if not (len(trades["rows"])==len(br)==len(dr)==1440):
        raise ValueError("MINUTE_GRID_NOT_1440")
    if bmeta["messages"]!=dmeta["messages"] or bmeta["snapshots"]!=dmeta["snapshots"]:
        raise ValueError("BOOK_PRIMARY_READER_DISAGREEMENT")
    if trades["source_fills"]!=187338 or bmeta["messages"]!=680886:
        raise ValueError("SOURCE_EVENT_COUNT_DISAGREEMENT")
    checks={"book":0,"decrease":0,"trade":0}
    states,mc,sc,aux=bq.source_minute_states(SYMBOL,DAY,raw_ob)
    if mc!=bmeta["messages"] or sc!=bmeta["snapshots"]:
        raise ValueError("BOOK_QC_TOTAL_MISMATCH")
    epoch=int(dt.datetime.combine(DAY,dt.time(),dt.UTC).timestamp()*1000)
    for i,(row,state) in enumerate(zip(br,states)):
        expected=bq.expected_from_state(state,epoch,i)
        for k in ("factor_9_queue_imbalance_near5bp","factor_11_bid_depth_usdt_near5bp","factor_11_ask_depth_usdt_near5bp","factor_29_spread_bps","best_bid_quote","best_ask_quote","quality","last_book_event_timestamp_ms","book_event_age_to_derived_end_ms"):
            a=row[k];b=expected[k]
            if k in ("quality","last_book_event_timestamp_ms","book_event_age_to_derived_end_ms"):
                good=a==b
            else:
                good=(a is None and b is None) or (a is not None and b is not None and Decimal(str(a))==b)
            if not good:raise ValueError(f"BOOK_QC_MISMATCH_{i}_{k}")
            checks["book"]+=1
    qs,qtot,qsuffix=dq.independently_measure(DAY,raw_ob,SYMBOL)
    if qtot["messages"]!=bmeta["messages"]:
        raise ValueError("DECREASE_QC_TOTAL_MISMATCH")
    for i,(row,expected) in enumerate(zip(dr,qs)):
        for k,j in (("book_messages","messages"),("snapshots","snapshots"),("deltas","deltas"),("bid_decrease_events","bid_count"),("ask_decrease_events","ask_count"),("bid_visible_decrease_usdt","bid_quote"),("ask_visible_decrease_usdt","ask_quote")):
            if Decimal(str(row[k]))!=Decimal(str(expected[j])):
                raise ValueError(f"DECREASE_QC_MISMATCH_{i}_{k}")
            checks["decrease"]+=1
    iq=tq.run(SYMBOL,DAY.isoformat())
    for i,(a,b) in enumerate(zip(trades["rows"],iq)):
        for k in ("fill_count","trade_rate_accel_per_min","buy_notional_usd","sell_notional_usd","delta_usd","cvd_from_day_start_usd","delta_accel_usd","rvol_60m"):
            x=a[k];y=b[k]
            good=(x is None and y is None) or (x is not None and y is not None and (x==y if k in ("fill_count","trade_rate_accel_per_min") else math.isclose(x,y,rel_tol=1e-10,abs_tol=1e-6)))
            if not good:raise ValueError(f"TRADE_QC_MISMATCH_{i}_{k}")
            checks["trade"]+=1
    if checks!={"book":12960,"decrease":10080,"trade":11520}:
        raise ValueError("QC_COMPARISON_COUNT_CHANGED")
    rows=[]
    for tr,bo,drop in zip(trades["rows"],br,dr):
        rows.append({
            "utc_minute":tr["minute_utc"],
            "derived_minute_end":tr["available_after_utc"],
            "verified_receiver_available_after":None,
            "instrument":"BYBIT_LINEAR:"+SYMBOL,
            "trade_source":"BYBIT_PUBLIC_TRADES_ARCHIVE",
            "book_source":"BYBIT_OB200_ARCHIVE",
            "trade":{key:tr.get(key) for key in ("fill_count","trade_rate_accel_per_min","buy_notional_usd","sell_notional_usd","delta_usd","cvd_from_day_start_usd","delta_accel_usd","rvol_60m","close","close_staleness_minutes","log_return_1m")},
            "book":{key:bo.get(key) for key in ("factor_9_queue_imbalance_near5bp","factor_11_bid_depth_usdt_near5bp","factor_11_ask_depth_usdt_near5bp","factor_29_spread_bps","best_bid_quote","best_ask_quote","last_book_event_timestamp_ms","book_event_age_to_derived_end_ms","book_levels_bid","book_levels_ask","quality")},
            "book_observations":{key:(str(drop.get(key)) if isinstance(drop.get(key),Decimal) else drop.get(key)) for key in ("book_messages","snapshots","deltas","bid_decrease_events","ask_decrease_events","bid_visible_decrease_usdt","ask_visible_decrease_usdt")},
        })
    payload="\n".join(json.dumps(x,sort_keys=True,separators=(",",":")) for x in rows).encode()
    gz=gzip.compress(payload,compresslevel=6,mtime=0)
    gzsha=hashlib.sha256(gz).hexdigest()
    if len(gz)!=EXPECTED_GZIP_SIZE or gzsha!=EXPECTED_GZIP_SHA:
        raise ValueError(f"RECORDED_IN_MEMORY_PILOT_PARITY_FAIL bytes={len(gz)} sha={gzsha}")
    out=DEST/f"{SYMBOL}_{DAY.isoformat()}_minute.jsonl.gz"
    write_new(out,gz)
    with gzip.open(out,"rt",encoding="utf-8") as data:
        rows_read=[json.loads(line) for line in data]
    if len(rows_read)!=1440 or rows_read[0]["utc_minute"]!="2026-05-18T00:00:00+00:00" or rows_read[-1]["utc_minute"]!="2026-05-18T23:59:00+00:00":
        raise ValueError("PERSISTED_READBACK_FAILED")
    if digest(out)!=gzsha:
        raise ValueError("PERSISTED_SHA_MISMATCH")
    result={"status":"PILOT_PERSISTED_INDEPENDENT_NUMERIC_QC_PASS_NOT_RAW_REPLACEMENT",
        "instrument":"BYBIT_LINEAR:"+SYMBOL,"period":DAY.isoformat(),"minute_rows":1440,
        "source_trades":187338,"source_book_events":680886,
        "independent_comparisons":checks,"independent_mismatches":0,
        "output_file":out.name,"output_bytes":out.stat().st_size,"output_sha256":gzsha,
        "input_provenance":inputs,"source_sha256":src_hash,"builder_sha256":digest(source_dir/"native_minute_snapshot_pilot_v1.py"),
        "raw_to_snapshot_ratio":round((raw_tr.stat().st_size+raw_ob.stat().st_size)/len(gz),3),
        "provenance_quality":"NATIVE_SOURCE_TIME_ONLY_RECEIVER_UNKNOWN",
        "book_source_continuity":"NOT_VERIFIED",
        "not_preserved":["exact native trade sequence","individual OB200 deltas","full orderbook event tape","full 30-indicator set"],
        "raw_purge_allowed":False,"source_commit":"50309343ed46b034a7d10c1da8c965e8be8344a1",
        "elapsed_seconds":round(time.monotonic()-started,2)}
    write_new(DEST/"result.json",jsonbytes(result))
    write_new(DEST/"final_manifest.json",jsonbytes({"status":"COMPLETE_FOR_BOUNDED_PILOT_ONLY","result_sha256":digest(DEST/"result.json"),"raw_purge_allowed":False}))
    print(json.dumps({"status":result["status"],"path":str(DEST),"output_bytes":result["output_bytes"],"output_sha256":gzsha,"qc_total":sum(checks.values()),"qc_mismatches":0,"raw_deletions":0,"elapsed_seconds":result["elapsed_seconds"]},ensure_ascii=False))
if __name__=="__main__":
    main()
