#!/usr/bin/env python3
"""Research-only, bounded exact-public-archive reacquisition proof.

One symbol, at most 7 days, no raw writes and no deletion.
For each archive compare local bytes with newly streamed remote bytes by SHA256.
All results are research evidence; NOT a raw-retention/deletion authorization.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import pwd
import shutil
import sys
import threading
import time
import urllib.request
from urllib.error import URLError, HTTPError

RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
RUNS = pathlib.Path("/data/cripta/research/runs")
ALLOWED = ("TRXUSDT",)
SRC_COMMIT = "3e3a77407c948483c174cde90d1b0954b1874540"
MAX_ARCHIVE = 60 * 1024 * 1024
RESERVE = 1024 * 1024 * 1024


def encode(x):
    return (json.dumps(x,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()


def write_unique(path, data):
    temp = path.with_name(path.name+".part")
    if path.exists() or temp.exists():
        raise FileExistsError(str(path))
    with temp.open("xb") as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    temp.rename(path)


def hash_stream(f, max_bytes):
    h=hashlib.sha256();md5=hashlib.md5();n=0
    while True:
        block=f.read(1024*1024)
        if not block:break
        n+=len(block)
        if n>max_bytes:raise ValueError("SOURCE_EXCEEDS_BOUNDED_SIZE")
        h.update(block);md5.update(block)
    return n,h.hexdigest(),md5.hexdigest()


def exact_sources(symbol, day):
    d=day.isoformat()
    return [
        ("public_trades",RAW/symbol/"public_trades"/f"{symbol}{d}.csv.gz",
         f"https://public.bybit.com/trading/{symbol}/{symbol}{d}.csv.gz"),
        ("orderbook",RAW/symbol/"orderbook"/f"{d}_{symbol}_ob200.data.zip",
         f"https://quote-saver.bycsi.com/orderbook/linear/{symbol}/{d}_{symbol}_ob200.data.zip"),
    ]


def verify_one(kind, path, url):
    t=time.monotonic()
    source_size=path.stat().st_size
    if not 0<source_size<=MAX_ARCHIVE:
        raise ValueError("RAW_SIZE_BOUND_FAIL")
    with path.open("rb") as f:
        local_n,local_sha,local_md5=hash_stream(f,MAX_ARCHIVE)
    req=urllib.request.Request(
        url,headers={"User-Agent":"cripta-archive-reacquisition-verification/1"})
    with urllib.request.urlopen(req,timeout=25) as resp:
        if resp.status!=200:raise RuntimeError(f"REMOTE_HTTP_{resp.status}")
        header_length=resp.headers.get("Content-Length")
        etag=resp.headers.get("ETag")
        last=resp.headers.get("Last-Modified")
        remote_n,remote_sha,remote_md5=hash_stream(resp,MAX_ARCHIVE)
    exact=(local_n==remote_n==source_size and local_sha==remote_sha)
    if header_length and remote_n!=int(header_length):
        exact=False
    if etag and etag.strip('\"').lower()!=remote_md5.lower():
        exact=False
    return {"source_kind":kind,"local":str(path),"remote_url":url,
        "local_bytes":local_n,"remote_bytes":remote_n,
        "sha256_local":local_sha,"sha256_reacquired":remote_sha,
        "md5_remote":remote_md5,"etag":etag,
        "last_modified":last,
        "match":bool(exact),"elapsed_seconds":round(time.monotonic()-t,3),
        "remote_available_now":True,"availability_guaranteed_future":False,
        "raw_purge_authorized":False}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--symbol",required=True,choices=ALLOWED)
    ap.add_argument("--start",required=True)
    ap.add_argument("--days",required=True,type=int)
    ap.add_argument("--run-id",required=True)
    args=ap.parse_args()
    if not 1<=args.days<=7:raise ValueError("MAX_DAYS_7")
    date=dt.date.fromisoformat(args.start)
    if date<dt.date(2026,5,18) or date+dt.timedelta(days=args.days)>dt.date(2026,8,16):
        raise ValueError("OUTSIDE_FROZEN_ARCHIVE")
    desired=f"raw_reacquire_{args.symbol}_{date.strftime('%Y%m%d')}_{args.days}d_20261105"
    if args.run_id!=desired:raise ValueError("RUN_ID_MISMATCH")
    if os.geteuid()!=pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_MUST_BE_CRIPTA")
    out=RUNS/args.run_id
    if out.exists() or not os.access(RUNS,os.W_OK|os.X_OK):
        raise RuntimeError("TARGET_EXISTS_OR_PARENT_NOT_WRITABLE")
    if shutil.disk_usage(RUNS).free<RESERVE:
        raise RuntimeError("DATA_DISK_RESERVE")
    days=[date+dt.timedelta(days=i) for i in range(args.days)]
    sources=[(day,k,p,url) for day in days for k,p,url in exact_sources(args.symbol,day)]
    for _,_,p,_ in sources:
        if not p.is_file() or not os.access(p,os.R_OK):
            raise RuntimeError("MISSING_OR_UNREADABLE_LOCAL_SOURCE:"+str(p))
        if p.stat().st_size>MAX_ARCHIVE:raise RuntimeError("INPUT_SIZE_CAP:"+str(p))
    os.umask(0o007)
    out.mkdir(mode=0o2770)
    snapshot=out/"source_snapshot";snapshot.mkdir(mode=0o2770)
    src=globals().get("__snapshot_source__")
    if not isinstance(src,str):
        src=pathlib.Path(__file__).read_text(encoding="utf-8")
    write_unique(snapshot/"raw_reacquisition_proof_v1.py",src.encode("utf-8"))
    code_sha=hashlib.sha256(src.encode()).hexdigest()
    write_unique(out/"source_commit.txt",(SRC_COMMIT+"\n").encode())
    write_unique(out/"source_sha256.txt",(code_sha+"  source_snapshot/raw_reacquisition_proof_v1.py\n").encode())
    write_unique(out/"command.txt",encode({"symbol":args.symbol,"start":args.start,"days":args.days,"run_id":args.run_id}))
    write_unique(out/"environment.txt",encode({"actor":"cripta","uid":os.geteuid(),"python":sys.version,"pid":os.getpid(),"source_commit":SRC_COMMIT}))
    write_unique(out/"run_manifest.json",encode({"status":"RUNNING","attempted_files":len(sources),
        "source_commit":SRC_COMMIT,"source_script_sha256":code_sha,
        "raw_delete":False,"scope":"reacquisition research evidence only"}))
    results=out/"results";results.mkdir(mode=0o2770)
    start_time=time.monotonic()
    phase={"stage":"START","processed":0,"total":len(sources)}
    done=threading.Event()
    def heart():
        while not done.wait(24):
            print(json.dumps({"stage":"HEARTBEAT","phase":dict(phase),
                "elapsed_s":round(time.monotonic()-start_time,1)}),flush=True)
    thread=threading.Thread(target=heart,daemon=True);thread.start()
    all_= []
    try:
        for idx,(day,kind,path,url) in enumerate(sources):
            phase.update({"stage":"VERIFY_REMOTE","day":day.isoformat(),"kind":kind,"processed":idx})
            print(json.dumps(phase),flush=True)
            result=verify_one(kind,path,url)
            result["utc_date"]=day.isoformat()
            write_unique(results/f"{day.isoformat()}_{kind}.json",encode(result))
            all_.append(result)
            phase.update({"processed":idx+1})
            print(json.dumps({"stage":"FILE_CHECKED","processed":idx+1,"total":len(sources),
                "day":day.isoformat(),"kind":kind,"sha256_identical":result["match"],
                "download_bytes":result["remote_bytes"],"elapsed_s":round(time.monotonic()-start_time,1)}),flush=True)
            if not result["match"]:
                raise ValueError("REACQUIRED_SHA_NOT_IDENTICAL:"+str(path))
    finally:
        done.set();thread.join(timeout=2)
    summary={"status":"SOURCE_REACQUISITION_EXACT_SHA256_PASS_NOT_PURGE_AUTHORIZATION",
        "symbol":args.symbol,"start":args.start,"days":args.days,
        "verified_files":len(all_),"verified_bytes":sum(r["local_bytes"] for r in all_),
        "sha_equal_files":sum(bool(r["match"]) for r in all_),
        "duration_seconds":round(time.monotonic()-start_time,2),
        "files":all_,"raw_purge_authorized":False,
        "reasons":["Source may not remain available in future","Consumer/read-model replacements not proven",
            "No full strategy-agnostic compact feature coverage","Per-file deletion not approved in this script"]}
    write_unique(out/"result.json",encode(summary))
    result_sha=hashlib.sha256((out/"result.json").read_bytes()).hexdigest()
    write_unique(out/"final_manifest.json",encode({"status":summary["status"],
        "result_sha256":result_sha,"source_sha256":code_sha,"raw_purge_authorized":False}))
    print(json.dumps({"stage":"COMPLETE","files":len(all_),"identical":summary["sha_equal_files"],
        "bytes":summary["verified_bytes"],"seconds":summary["duration_seconds"],
        "result_path":str(out/"result.json"),"raw_deleted":0}),flush=True)


if __name__=="__main__":
    main()
