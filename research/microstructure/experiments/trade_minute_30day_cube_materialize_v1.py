#!/usr/bin/env python3
"""CRIPTA research-only 30-day historical PHYSICAL MEASURE cube materializer.

SCRIPT: trade_minute_30day_cube_materialize_v1.py
EFFECTIVE_ACTOR: Unix cripta (verified by pwd.getpwnam)
READ_PATHS: /data/cripta/datasets/raw/20260518_20260816/{symbol}/public_trades;
  /srv/cripta/source_checkout/research/microstructure/{raw manifest,Q C results,primary script}
WRITE_PATHS: /data/cripta/research/runs/trade_minute_cube_20261021_30d_v1/{symbol}_6factor.csv.gz
  plus {symbol}_MANIFEST.json (only new files, no overwrite).
CREATE_CHILD_PARENTS: /data/cripta/research/runs; created run root itself (mode 2770).
RENAME_DELETE_PARENTS: new research run root only; atomically rename new *.part to final; no deletes.
SYSTEMD_SANDBOX: none, direct actor-bound allowlisted python3 invocation, -B.
DB_ROLE: none. EXPECTED_OWNER/GROUP/MODE/ACL: cripta:cripta, dir 2770,
  files 660 subject to existing inherited POSIX ACL, no privilege escalation.
PREFLIGHT: exact owner/group/mode/ACL and actual-actor traversal/create/rename
  independently verified on 2026-10-21; script rechecks actual actor and targets.
No Strategy/Entry/Exit/Execution changes, no OOS, no implied receipt/observed timing.
"""
import argparse,csv,datetime as dt,gzip,hashlib,importlib.util,io,json,os,pwd,stat,sys,time
from pathlib import Path

BASE=Path("/srv/cripta/source_checkout/research/microstructure")
RAW=Path("/data/cripta/datasets/raw/20260518_20260816")
PARENT=Path("/data/cripta/research/runs")
OUT=PARENT/"trade_minute_cube_20261021_30d_v1"
PRIMARY=BASE/"experiments/trade_minute_factors_v2_exact_timestamp_floor.py"
PRIMARY_BLOB="71e4851faebd388fd2a6db3d93dca2ca2cb28c07"
MANIFEST=BASE/"TRADE_TAPE_RAW_30DAY_150_SOURCE_MANIFEST_2026-10-21.json"
MANIFEST_BLOB="30b87fe11fb3a1bfc6fb59c8866091e92e823203"
SYMS=("APTUSDT","INJUSDT","DOTUSDT","LTCUSDT","ARBUSDT")
START=dt.date(2026,5,18)
FEATURES=(
    (1,"rvol_60m","ratio","NON_AGGREGATABLE"),
    (2,"trade_rate_per_min","fill_count","ADDITIVE"),
    (3,"trade_rate_accel_per_min","fill_count_change","NON_AGGREGATABLE"),
    (5,"delta_usd","USDT","ADDITIVE"),
    (6,"cvd_from_day_start_usd","USDT","NON_AGGREGATABLE"),
    (7,"delta_accel_usd","USDT_change","NON_AGGREGATABLE"))
FIELDS=("venue_id","instrument_id","market_source_id","indicator_id","formula_version",
        "window_start_utc","derived_window_end_utc","observed_at_utc","verified_available_after_utc",
        "value","unit","quality","availability_quality","aggregation_operator",
        "source_archive_sha256","input_fill_count")

def blob(path):
    b=path.read_bytes()
    return hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()

def source_hash(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1048576),b""):h.update(chunk)
    return h.hexdigest()

def prereq(sym):
    uid=pwd.getpwnam("cripta").pw_uid
    if os.geteuid()!=uid:raise PermissionError("EFFECTIVE_ACTOR_NOT_CRIPTA")
    if sym not in SYMS:raise ValueError("INSTRUMENT_ID")
    ps=PARENT.stat()
    if ps.st_uid!=uid or not os.access(PARENT,os.W_OK|os.X_OK):
        raise PermissionError("RESEARCH_PARENT_CREATE_PERMISSION_FAIL")
    if blob(PRIMARY)!=PRIMARY_BLOB:raise ValueError("PRIMARY_CODE_BLOB_DRIFT")
    if blob(MANIFEST)!=MANIFEST_BLOB:raise ValueError("RAW_MANIFEST_BLOB_DRIFT")
    if OUT.exists():
        st=OUT.stat()
        if st.st_uid!=uid or not os.access(OUT,os.W_OK|os.X_OK):
            raise PermissionError("OUTPUT_ROOT_PERMISSION_FAIL")
    else:
        OUT.mkdir(mode=0o2770)
    if (OUT/(sym+"_6factor.csv.gz")).exists() or (OUT/(sym+"_MANIFEST.json")).exists():
        raise FileExistsError("IMMUTABLE_OUTPUT_ALREADY_EXISTS")
    raw_manifest=json.loads(MANIFEST.read_text())
    index={r["date_utc"]:r for r in raw_manifest["source_files"] if r["symbol"]==sym}
    if len(index)!=30:raise ValueError("EXPECTED_30_DAY_SOURCE_MANIFEST")
    qc_path=BASE/(sym.replace("USDT","")+"_30DAY_FULLROW_QC_RESULT_2026-10-21.json")
    qc=json.loads(qc_path.read_text())
    if qc["symbol"]!=sym or len(qc["daily"])!=30:raise ValueError("INDEPENDENT_QC_MISSING")
    checks={r["day"]:r for r in qc["daily"]}
    return index,checks

def run(sym):
    os.umask(0o007)
    index,checks=prereq(sym)
    spec=importlib.util.spec_from_file_location("pinned_primary",PRIMARY)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    part=OUT/(sym+"_6factor.csv.gz.part")
    result_path=OUT/(sym+"_6factor.csv.gz")
    man_part=OUT/(sym+"_MANIFEST.json.part")
    man_path=OUT/(sym+"_MANIFEST.json")
    if part.exists() or man_part.exists():raise FileExistsError("PARTIAL_OUTPUT_PRESENT_STOP")
    fd=os.open(part,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o660)
    total=0;nulls={str(i):0 for i,_,_,_ in FEATURES};raw_counts=[];started=time.monotonic()
    with os.fdopen(fd,"wb") as raw:
        with gzip.GzipFile(fileobj=raw,mode="wb",mtime=0) as gz:
            with io.TextIOWrapper(gz,encoding="utf-8",newline="") as txt:
                wr=csv.writer(txt,lineterminator="\n")
                wr.writerow(FIELDS)
                for day_idx in range(30):
                    day=(START+dt.timedelta(days=day_idx)).isoformat()
                    source=RAW/sym/"public_trades"/(sym+day+".csv.gz")
                    sha=source_hash(source)
                    if sha!=index[day]["compressed_sha256"]:raise ValueError("SOURCE_HASH_DRIFT "+day)
                    q=checks[day]
                    if (q.get("source_sha256",q.get("raw_gz_sha256"))!=sha or
                        not q.get("qc_pass",q.get("full_1440_8fields_pass",False))):
                        raise ValueError("INDEPENDENT_QC_NOT_PASS "+day)
                    v=mod.calculate(sym,day)
                    expected=q.get("source_fills",q.get("source_rows"))
                    if v["source_fills"]!=expected or len(v["rows"])!=1440:
                        raise ValueError("PRIMARY_SOURCE_COUNT_DRIFT "+day)
                    raw_counts.append(dict(day=day,source_sha256=sha,source_fills=expected))
                    for row in v["rows"]:
                        for ident,key,unit,agg in FEATURES:
                            value=row[key]
                            if value is None:
                                nulls[str(ident)]+=1
                                quality="NO_DATA_INSUFFICIENT_LOOKBACK_OR_UNDEFINED"
                            elif row["fill_count"]==0:
                                quality="VALUE_ARCHIVE_NO_PRINTS_EVENT_COMPLETENESS_UNVERIFIED"
                            else:
                                quality="VALUE_NUMERIC_QC_PASS_SOURCE_COMPLETENESS_UNVERIFIED"
                            wr.writerow(("Bybit",sym,"BYBIT_PUBLIC_TRADES_ARCHIVE",ident,
                                "trade_minute_factors_v2_exact_timestamp_floor",
                                row["minute_utc"],row["available_after_utc"],"","",
                                "" if value is None else repr(value),unit,quality,
                                "UNKNOWN_NO_RECEIPT_PUBLICATION_CLOCK",agg,sha,
                                row["fill_count"]))
                            total+=1
                    elapsed=time.monotonic()-started
                    done=day_idx+1
                    progress=dict(stage="PHYSICAL_CUBE_6FACTOR",symbol=sym,
                        processed_days=done,total_days=30,percent=round(done*100/30,2),
                        elapsed_seconds=round(elapsed,2),eta_seconds=round(elapsed*(30-done)/done,2),
                        cache="MISS_STREAMING_SOURCE",heartbeat=True)
                    print(json.dumps(progress,separators=(",",":")),file=sys.stderr,flush=True)
    if total!=30*1440*len(FEATURES):raise ValueError("OUTPUT_ROW_COUNT_MISMATCH")
    os.rename(part,result_path)
    sha=source_hash(result_path)
    output=dict(status="PILOT_PHYSICAL_MEASURE_CUBE_NOT_RESEARCH_READY",actor="cripta",
        instrument_id=sym,utc_period_start="2026-05-18",utc_period_end="2026-06-16",
        source_days=30,minute_rows=30*1440,factor_ids=[x[0] for x in FEATURES],
        indicator_time_rows=total,null_counts=nulls,source_inputs=raw_counts,
        output_file=str(result_path),output_sha256=sha,output_bytes=result_path.stat().st_size,
        primary_source_blob=PRIMARY_BLOB,source_manifest_blob=MANIFEST_BLOB,
        actual_source_arrival_clock="NOT_CAPTURED",verified_available_after="UNKNOWN",
        clock_cutoff="derived UTC minute close, NOT verified receipt time",
        quality_gate="NUMERIC_QC_PRIOR_VERIFIED; EXCHANGE_COMPLETENESS_AND_FULL_STUDY_PERIOD_OPEN",
        no_future_labels=True,after_fees_PnL="NOT_CALCULATED")
    md=json.dumps(output,ensure_ascii=False,indent=2)+"\n"
    f=os.open(man_part,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o660)
    with os.fdopen(f,"w",encoding="utf-8") as out:out.write(md)
    os.rename(man_part,man_path)
    print(json.dumps(dict(instrument_id=sym,source_days=30,indicator_time_rows=total,
        output_sha256=sha,output_bytes=output["output_bytes"],output_file=str(result_path),
        manifest_file=str(man_path),readiness="NOT_RESEARCH_READY"),separators=(",",":")))

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--symbol",choices=SYMS,required=True)
    args=parser.parse_args()
    run(args.symbol)
