#!/usr/bin/env python3
"""Full native Bybit 29-day holdout: P90L90 PREV7D TRAIL035 two independent Exits.

No Strategy, DB, runtime, order, or LIVE operations; historical proxy only.
Inputs Sep11-Oct19, evaluation [Sep21,Oct20) 2026 UTC, five same fixed
10 USDT per-slot notional, modeled taker/maker fees, zero reinvestment.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import heapq
import json
import os
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from statistics import median
from typing import Iterator

from scripts.research import p90l90_29d_sources_v1 as sources
from scripts.research import p90l90_entry_sep29_v1 as entry
from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research import p90l90_portfolio_pause_v1 as portfolio
from scripts.research import p90l90_btceth_adverse_exit_90d_v1 as market_impl
from scripts.research.p90l90_btceth_adverse_exit_90d_v1 import MarketExitPortfolio

ROOT = sources.ROOT
FIRST = entry.START_TS
END = entry.END_TS
COHORT = "PREV7D"
KIND = "TRAIL035"
NOMINAL_DEPOSIT = D("50")
DAYS = 29
RESULTS = ROOT / "results"
MKT = ROOT / "market/btc_eth_hourly_closed.csv"
VARIANT = (6, 5, 2, 0)
SYMBOLS = sources.SYMBOLS


def market_signals():
    """Both coin hourly closes are available strictly after close hour timestamp."""
    if sources.hash_file(MKT) != json.loads(
        (MKT.parent / "manifest.json").read_text()
    )["csv_sha256"]:
        raise ValueError("Bybit market immutable CSV SHA mismatch")
    rows = list(csv.DictReader(MKT.open(encoding="utf-8", newline="")))
    ts = [int(r["ts"]) for r in rows]
    btc = [float(r["btc"]) for r in rows]
    eth = [float(r["eth"]) for r in rows]
    if (len(ts) != int((sources.MARKET_END - sources.MARKET_FIRST).total_seconds()/3600)
            or ts[0] > FIRST - (168+6)*3600
            or ts[-1] < END
            or any(b-a != 3600 for a,b in zip(ts,ts[1:]))
            or any(not 0 < x < float("inf") for x in btc+eth)):
        raise ValueError("missing causal BTC+ETH market hour coverage")
    h = 6
    returns = [None]*len(ts)
    for i in range(h,len(ts)):
        returns[i] = ((btc[i]/btc[i-h]-1)*100,
                      (eth[i]/eth[i-h]-1)*100)
    flags = [None]*len(ts)
    for i in range(h+168,len(ts)):
        prev = returns[i-168:i]
        mb = median(abs(r[0]) for r in prev)
        me = median(abs(r[1]) for r in prev)
        if mb <= 0 or me <= 0:
            continue
        rb,re = returns[i]
        if rb < 0 and re < 0:
            flags[i] = (1,min(-rb/mb,-re/me))
        elif rb > 0 and re > 0:
            flags[i] = (-1,min(rb/mb,re/me))
    return ts,flags


def read_inputs(sym: str):
    if sym not in SYMBOLS:
        raise ValueError("unapproved symbol")
    folder = RESULTS / f"entry_{sym.removesuffix('USDT')}_SEP29"
    manifest = json.loads((folder/"manifest.json").read_text())
    if (manifest["research"] != "P90L90_ENTRY_SEP29_V1"
            or manifest["symbol"] != sym
            or not manifest["complete_29d_input"]
            or manifest["frozen_l53"]["states"] != DAYS*288):
        raise ValueError("incorrect immutable 29d Entry manifest")
    state_path = entry.FROZEN_DEFAULT / f"states_{sym}.csv.gz"
    cand = folder / f"candidates_{sym}.jsonl"
    if (sources.hash_file(state_path) != manifest["frozen_l53_sha256"]
            or sources.hash_file(cand) != manifest["events_sha256"]):
        raise ValueError("Entry candidate/state SHA drift")
    states,_,medians,meta = entry.load_frozen(state_path)
    if len(states) != DAYS*288 or len(medians) != DAYS or meta["states"] != DAYS*288:
        raise ValueError("Entry state or PREV7D readiness failed")
    events,_ = base.read_events(cand,sym)
    raw_manifest = json.loads((ROOT/"inputs"/f"manifest_{sym}.json").read_text())
    if len(manifest["inputs"]) != 39 or len(raw_manifest["files"]) != 39:
        raise ValueError("missing Bybit raw days")
    by_name = {x["file"]:x for x in raw_manifest["files"]}
    if len(by_name) != 39:
        raise ValueError("duplicate raw source day")
    for f in manifest["inputs"]:
        if (f["file"] not in by_name or f["sha256"] != by_name[f["file"]]["sha256"]
                or f["rows"] != by_name[f["file"]]["rows"]):
            raise ValueError("raw/Entry independent source mismatch")
    return {"events":events, "states":states, "manifest":manifest, "symbol":sym,
            "seen_events":0, "seen_rows":0}


def native_ticks(data) -> Iterator[tuple[float,int,D,str]]:
    sym = data["symbol"]
    seq = 0
    prev = None
    all_events = data["events"]
    for item in data["manifest"]["inputs"]:
        path = ROOT / "inputs" / sym / "public_trades" / item["file"]
        if sources.hash_file(path) != item["sha256"]:
            raise ValueError("Bybit raw native day SHA drift")
        rows = 0
        with gzip.open(path,"rt",newline="") as stream:
            for rec in csv.DictReader(stream):
                seq += 1
                rows += 1
                ts = float(rec["timestamp"])
                if prev is not None and ts < prev:
                    raise ValueError("native tick time regressed")
                prev=ts
                if ts < FIRST:
                    continue
                if ts >= END:
                    raise ValueError("out-of-window raw native trade")
                px=D(rec["price"])
                for ev in all_events.get(seq,()):
                    if (ev["trade_id"]!=rec["trdMatchID"] or
                            float(ev["ts"])!=ts or D(ev["price"])!=px):
                        raise ValueError("candidate source-order mismatch")
                data["seen_events"] += len(all_events.get(seq,()))
                yield ts,seq,px,rec["trdMatchID"]
        if rows != item["rows"]:
            raise ValueError("native source daily row mismatch")
    data["seen_rows"] = seq
    if (seq != sum(x["rows"] for x in data["manifest"]["inputs"])
            or data["seen_events"] != sum(len(v) for v in all_events.values())):
        raise ValueError("unconsumed candidate or source rows")


def by_coin_reason(rows):
    d=defaultdict(lambda:defaultdict(lambda:{
        "closed":0, "positive":0, "negative":0, "net_usdt":D(0),
        "positive_usdt":D(0), "negative_usdt":D(0),
    }))
    for row in rows:
        value=D(row["net_usdt"])
        a=d[row["symbol"]][row["exit_reason"]]
        a["closed"]+=1
        a["positive"]+=int(value>0)
        a["negative"]+=int(value<0)
        a["net_usdt"]+=value
        a["positive_usdt"]+=max(D(0),value)
        a["negative_usdt"]+=min(D(0),value)
    return {sym:{reason:{k:str(v) if isinstance(v,D) else v for k,v in stats.items()}
                 for reason,stats in v.items()}
            for sym,v in d.items()}


def run(output:Path,smoke_hours:int=0):
    if os.geteuid()!=107:
        raise PermissionError("run under cripta uid107")
    if not output.is_absolute() or output.parent.resolve()!=RESULTS.resolve():
        raise ValueError("research results path only")
    if output.exists():
        raise FileExistsError("immutable previous run must not be overwritten")
    if not os.access(RESULTS,os.W_OK|os.X_OK):
        raise PermissionError("results dir not writable by cripta")
    ts,flags=market_signals()
    data={s:read_inputs(s) for s in SYMBOLS}
    books={"BASELINE":portfolio.Portfolio(None),
           "H06_X5_L2":MarketExitPortfolio(VARIANT)}
    ticks={s:native_ticks(data[s]) for s in SYMBOLS}
    heap=[]
    for index,s in enumerate(SYMBOLS):
        first=next(ticks[s],None)
        if first is None:raise ValueError("empty symbol native source")
        heapq.heappush(heap,(first[0],index,first))
    market_idx=-1
    prev_day=None
    limit = END if not smoke_hours else min(END,FIRST+smoke_hours*3600)
    while heap:
        _,priority,row=heapq.heappop(heap)
        t,seq,px,_=row
        if t>=limit:break
        sym=SYMBOLS[priority]
        while market_idx+1<len(ts) and ts[market_idx+1]<t:
            market_idx+=1
        event=data[sym]["events"].get(seq,())
        state=data[sym]["states"].get(int(t//300)*300)
        if event or books["BASELINE"].pos[sym] is not None:
            books["BASELINE"].handle(sym,t,seq,px,event,state)
        if event or books["H06_X5_L2"].pos[sym] is not None:
            flag=flags[market_idx] if market_idx>=0 else None
            books["H06_X5_L2"].handle_market(sym,t,seq,px,event,state,flag)
        day=int(t//86400)
        if day!=prev_day:
            prev_day=day
            print(json.dumps({"phase":"REPLAY","utc_day":str(datetime.fromtimestamp(t,timezone.utc).date())}),flush=True)
        following=next(ticks[sym],None)
        if following is not None:
            heapq.heappush(heap,(following[0],priority,following))
    if smoke_hours:
        return {"status":"SMOKE", "scenarios":{s:b.finish()["counts"] for s,b in books.items()}}
    for s in SYMBOLS:
        if data[s]["seen_events"]!=sum(len(v) for v in data[s]["events"].values()):
            raise ValueError("missing events")
    stats={name:book.finish() for name,book in books.items()}
    total={name:D(st["money"]["net_usdt"]) for name,st in stats.items()}
    out={"research":"P90L90_PREV7D_SEP29_BTCETH_EXIT_V1",
         "status":"COMPLETE_HISTORICAL_PROXY_NOT_LIVE",
         "period_utc":["2026-09-21T00:00:00Z","2026-10-20T00:00:00Z"],
         "evaluated_days":DAYS,"symbols":list(SYMBOLS),
         "entry":"fresh P90L90 PREV7D raw first crossing; width +/-0.5pp",
         "exit_base":"TRAIL035 plus native opposite RAW TP, hard stop -10%",
         "exit_market":"both BTC ETH adverse 6h >=5x preceding 168h medians, own loss >=2%",
         "model":"native Bybit public ticks; one slot per coin per independently replayed scenario",
         "money_basis":"5x10USDT theoretical capital 50 USDT, fixed nominal no compounding",
         "unmodeled":["slippage","bid_ask_spread","funding","real_execution_latency"],
         "market_price_source_sha":sources.hash_file(MKT),
         "source_rows_by_symbol":{s:data[s]["seen_rows"] for s in SYMBOLS},
         "candidate_sha_by_symbol":{s:data[s]["manifest"]["events_sha256"] for s in SYMBOLS},
         "scenarios":stats,
         "coin_and_reason":{name:by_coin_reason(book.trades)
                            for name,book in books.items()},
         "net_pnl_usdt":{name:str(n) for name,n in total.items()},
         "roi_29d_pct":{name:str(n/NOMINAL_DEPOSIT*D(100)) for name,n in total.items()},
         "mean_daily_roi_pct":{name:str(n/NOMINAL_DEPOSIT*D(100)/D(DAYS)) for name,n in total.items()}}
    output.mkdir(mode=0o2770)
    snapshot=output/"source_snapshot"
    snapshot.mkdir(mode=0o2770)
    source_list=[Path(__file__),Path(entry.__file__),Path(sources.__file__),
                 Path(base.__file__),Path(portfolio.__file__),Path(market_impl.__file__)]
    snapshots={}
    for f in source_list:
        to=snapshot/f.name
        shutil.copyfile(f,to)
        snapshots[f.name]=sources.hash_file(to)
    out["source_snapshot_sha256"]=snapshots
    ledger=output/"trade_ledger.jsonl"
    with ledger.open("x") as fh:
        for name,book in books.items():
            for row in book.trades:
                fh.write(json.dumps({**row,"portfolio_scenario":name},
                                     sort_keys=True,separators=(",",":"))+"\n")
    out["ledger_sha256"]=sources.hash_file(ledger)
    out["ledger_rows"]=sum(len(b.trades) for b in books.values())
    (output/"result.json").write_text(json.dumps(out,sort_keys=True,indent=2)+"\n")
    return {"status":"COMPLETE","result":str(output/"result.json"),
            "roi_29d_pct":out["roi_29d_pct"],
            "mean_daily_roi_pct":out["mean_daily_roi_pct"]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True,type=Path)
    p.add_argument("--smoke-hours",type=int,default=0)
    a=p.parse_args()
    if a.smoke_hours<0 or a.smoke_hours>24:raise ValueError("smoke range")
    print(json.dumps(run(a.output,a.smoke_hours),sort_keys=True),flush=True)


if __name__=="__main__":
    main()
