#!/usr/bin/env python3
"""Research-only P90L90 fixed protective stop sweep: 2026 29d / 90d.

One native Bybit tick merge feeds 16 strictly independent books:
LONG_ONLY / SHORT_ONLY x {native TRAIL035, FIX_040,...,FIX_100}.
No Strategy, LIVE, DB, runtime, orders or canonical policy changes.
Original Entry/geometry/RAW TP/HARD_STOP/SECOND48 untouched. FIX_X
activates once at X favorable and freezes protective stop at Entry +0.15%;
no following trail. Native TRAIL035 is exact untouched moving-trail control.
"""
from __future__ import annotations
import argparse
import hashlib
import heapq
import json
from decimal import ROUND_HALF_EVEN
import os
import pathlib
import shutil
import sys
import time
from collections import Counter
from decimal import Decimal as D

SOURCE = pathlib.Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research import p90l90_portfolio_pause_v1 as ninety
from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as embargo
from scripts.research import p90l90_29d_exit_replay_v1 as sep

# The installed systemd research job runner owns a sandboxed data-disk job spool.
# In that mode the worker writes only to its own bundled job directory;
# the independent runner moves the whole bundle to completed/failed on exit.
JOB_ID = os.environ.get("CRIPTA_JOB_ID", "")
if JOB_ID:
    if not (JOB_ID.startswith("C2-R837-F29-") or JOB_ID.startswith("C2-R837-F90-")):
        raise ValueError("unexpected research job identity")
    ROOT = pathlib.Path.cwd().resolve()
    expected = pathlib.Path("/data/cripta/jobs/running") / JOB_ID / "bundle"
    if ROOT != expected.resolve() or ROOT.is_symlink():
        raise PermissionError("job output must be its approved writable data-disk spool")
else:
    ROOT = pathlib.Path("/data/cripta/research/runs/p90l90_fixed_stop_sweep_20261118_v1")
CASES = ("TRAIL035","FIX_040","FIX_050","FIX_060","FIX_070","FIX_080","FIX_090","FIX_100")
ACTIVATIONS = {("FIX_"+str(i).zfill(3)): D(i)/D(100) for i in (40,50,60,70,80,90,100)}
SIDES = ("LONG_ONLY", "SHORT_ONLY")
COINS = ("LTCUSDT","DOTUSDT","ARBUSDT","APTUSDT","INJUSDT")
ACTIVE_CASE = "TRAIL035"
ORIGINAL_ELIGIBLE = base.take_profit_eligible
ORIGINAL_ON_TRADE = base.on_trade

def sha(file):
    h = hashlib.sha256()
    with pathlib.Path(file).open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""):
            h.update(b)
    return h.hexdigest()

def fixed_on_trade(pos, px, seq, bucket, candidate_tp, kind):
    """Native TP/HS priority and gross fills; fixed +0.15 stop, never trails."""
    if ACTIVE_CASE == "TRAIL035":
        return ORIGINAL_ON_TRADE(pos, px, seq, bucket, candidate_tp, kind)
    if kind != "TRAIL035" or ACTIVE_CASE not in ACTIVATIONS:
        raise ValueError("invalid fixed-stop scenario")
    if seq <= pos.entry_seq:
        return pos, None
    if base.hits_stop(pos.side, px, pos.stop):
        return None, ("PROTECTED_STOP" if pos.protected else "HARD_STOP", px, False)
    if (pos.tp is not None and seq > pos.tp_live_after_seq
            and base.hits_tp(pos.side, px, pos.tp)):
        return None, ("RESTING_TP", pos.tp, True)
    if bucket != pos.state_bucket:
        pos.state_bucket = bucket
        if (candidate_tp is not None
                and ORIGINAL_ELIGIBLE(pos.side, pos.entry, candidate_tp)):
            if base.hits_tp(pos.side, px, candidate_tp):
                return None, ("MARKETABLE_TP", px, False)
            pos.tp = candidate_tp
            pos.tp_live_after_seq = seq
    favor = base.favorable(pos.side, pos.entry, px)
    if favor > pos.best_favorable:
        pos.best_favorable = favor
    if not pos.protected and pos.best_favorable >= ACTIVATIONS[ACTIVE_CASE]:
        fixed = base.threshold_price(pos.side, pos.entry, D("0.15"))
        tighter = fixed > pos.stop if pos.side == "LONG" else fixed < pos.stop
        if not tighter:
            raise RuntimeError("fixed stop must tighten initial hardstop")
        pos.stop = fixed
        pos.protected = True
        pos.protection_seq = seq
    return pos, None

def preflight(period):
    if period not in ("29d","90d"):
        raise ValueError("unsupported period")
    if os.geteuid()!=107 or os.getegid()!=109:
        raise PermissionError("must run as cripta")
    if not ROOT.is_dir() or ROOT.is_symlink() or ROOT.stat().st_uid!=107:
        raise PermissionError("prepared research root missing or wrong actor")
    src=ROOT/"source_snapshot"/"p90l90_fixed_stop_sweep_2026_v1.py"
    if src.resolve()!=pathlib.Path(__file__).resolve() or not src.is_file():
        raise RuntimeError("expected frozen driver snapshot")
    if not os.access(ROOT,os.W_OK|os.X_OK):
        raise PermissionError("research root is not writable by cripta")
    free=os.statvfs(ROOT).f_bavail*os.statvfs(ROOT).f_frsize
    if free < 5*2**30:
        raise RuntimeError("research free disk below bounded 12GiB guard")
    if (ROOT/period).exists():
        raise FileExistsError("immutable period result path already exists")
    if base.take_profit_eligible is not ORIGINAL_ELIGIBLE or base.on_trade is not ORIGINAL_ON_TRADE:
        raise RuntimeError("original TP callback was modified externally")
    return src

def get_inputs(period):
    if period=="29d":
        data={s:sep.read_inputs(s) for s in COINS}
        streams={s:sep.native_ticks(data[s]) for s in COINS}
        end=sep.entry.END_TS
        refroot=sep.ROOT/"results"/"p90l90_directiononly_29d_20261109_v1"
        refs={
         "LONG_ONLY":refroot/"trade_ledger_long_only.jsonl",
         "SHORT_ONLY":refroot/"trade_ledger_short_only.jsonl"
        }
    else:
        data={s:ninety.inputs(s,ninety.RAW_DEFAULT,ninety.FROZEN_DEFAULT) for s in COINS}
        streams={s:ninety.ticks(data[s]) for s in COINS}
        end=ninety.END_TS
        root=ninety.ROOT/"results"
        refs={
          "LONG_ONLY":root/"p90l90_longonly_full90_20261109_v1"/"trade_ledger_longonly.jsonl",
          "SHORT_ONLY":root/"p90l90_shortonly_full90_20261109_v1"/"trade_ledger_shortonly.jsonl"
        }
    return data,streams,end,refs

def run(period,smoke_hours):
    global ACTIVE_CASE
    src=preflight(period)
    data,streams,end,refs=get_inputs(period)
    start=sep.entry.START_TS if period=="29d" else ninety.START_TS
    limit=min(end,start+smoke_hours*3600) if smoke_hours else end
    books={(s,f):embargo.EntryEmbargo(2,48) for s in SIDES for f in CASES}
    assert len(books)==16 and len(set(map(id,books.values())))==16
    heap=[]
    for i,s in enumerate(COINS):
        tick=next(streams[s],None)
        if tick is None:raise ValueError("empty source stream "+s)
        heapq.heappush(heap,(tick[0],i,tick))
    base.on_trade=fixed_on_trade
    startclock=time.monotonic()
    last_beat=startclock;day=None;n=0
    try:
        while heap:
            t,priority,(_,seq,px,trade_id)=heapq.heappop(heap)
            if t>=limit:break
            sym=COINS[priority]
            events=data[sym]["events"].get(seq,())
            state=data[sym]["states"].get(int(t//300)*300)
            byside={s:tuple(e for e in events if e["side"]==s.split("_")[0])
                    for s in SIDES}
            for (s,f),book in books.items():
                selected=byside[s]
                if selected or book.pos[sym] is not None:
                    ACTIVE_CASE=f
                    book.handle(sym,t,seq,px,selected,state)
            n+=1
            daynow=int(t//86400)
            if daynow!=day:
                day=daynow
                print(json.dumps({"stage":"DAY","period":period,
                    "day_epoch_utc":day,"ticks":n,
                    "elapsed_s":round(time.monotonic()-startclock,1)}),flush=True)
            if time.monotonic()-last_beat>=25:
                print(json.dumps({"stage":"HEARTBEAT","period":period,"ticks":n,
                    "elapsed_s":round(time.monotonic()-startclock,1),
                    "trail035_closed_long":books[("LONG_ONLY","TRAIL035")].count["closed"]}),flush=True)
                last_beat=time.monotonic()
            nxt=next(streams[sym],None)
            if nxt is not None:heapq.heappush(heap,(nxt[0],priority,nxt))
    finally:
        base.on_trade=ORIGINAL_ON_TRADE
        ACTIVE_CASE="TRAIL035"
    if smoke_hours:
        print(json.dumps({"status":"SMOKE_PASS_NO_WRITES","period":period,
          "native_ticks":n,"scenarios":{s+"|"+str(f):dict(b.count) for (s,f),b in books.items()}},
          separators=(",",":")),flush=True)
        return
    totals={}
    for s in SIDES:
        old=[json.loads(l) for l in refs[s].open() if l.strip()]
        new=books[(s,"TRAIL035")].trades
        if old!=new:
            raise RuntimeError("baseline 0.50 trade-by-trade parity failed "+period+" "+s+
                               " old="+str(len(old))+" new="+str(len(new)))
    print(json.dumps({"stage":"BASELINE_PARITY_PASS","period":period,
        "long":len(books[("LONG_ONLY","TRAIL035")].trades),
        "short":len(books[("SHORT_ONLY","TRAIL035")].trades),"ticks":n}),flush=True)
    reports={}
    for (side,floor),book in books.items():
        summary=book.finish()
        rows=book.trades
        assert summary["counts"]["closed"]==len(rows)
        assert summary["counts"]["accepted"]==len(rows)+summary["counts"]["censored_open"]
        closed_net=sum((D(x["net_usdt"]) for x in rows),D(0))
        assert closed_net==D(summary["money"]["net_usdt"])
        gross_events=Counter(r["exit_reason"] for r in rows)
        per_coin=Counter(r["symbol"] for r in rows)
        key=side+"|"+str(floor)
        reports[key]={
            "activation_pct":str(ACTIVATIONS.get(floor, D("0.50"))),"case":floor,"direction":side,
            "counts":summary["counts"],"money":summary["money"],
            "closed_net_usdt_original_10_notional":str(closed_net),
            "gross_exits_by_reason":dict(gross_events),"closed_by_coin":dict(per_coin),
            "censored":summary["censored"],
            "ledger_sha256":None
        }
    out=ROOT/period
    out.mkdir(mode=0o770)
    for (side,floor),book in books.items():
        label=side.lower()+"_"+floor.lower()
        leaf=out/(label+".jsonl")
        with leaf.open("x",encoding="utf8") as g:
            for row in book.trades:
                g.write(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n")
            g.flush();os.fsync(g.fileno())
        reports[side+"|"+str(floor)]["ledger_sha256"]=sha(leaf)
    manifest={
      "status":"RESEARCH_INTERNAL_QC_PASS_FIXEDSTOP_2026","period":period,
      "purpose":"original moving TRAIL035 control vs seven one-time fixed +0.15 stops; dynamic TP unchanged",
      "cases":CASES,"fixed_activation_pct":{k:str(v) for k,v in ACTIVATIONS.items()},
      "sides":SIDES,"SECOND48":"independent by side and floor",
      "original_moving_trail_activation_pct":"0.50","original_moving_trail_distance_pp":"0.35","fixed_gross_protection_pct":"0.15","fixed_stop_never_moves":True,"hardstop_pct":"-10",
      "source":"native Bybit trades, original frozen P90L90 2026 candidates and geometry",
      "derived_script_sha256":sha(src),
      "dependency_source_sha256":{p.name:sha(p) for p in (SOURCE/"scripts"/"research").glob("p90l90_*_v1.py") if p.name in (
          "p90l90_portfolio_pause_v1.py","p90l90_entry_only_hardstop_embargo_v1.py","p90l90_29d_exit_replay_v1.py")},
      "base_source_sha256":sha(SOURCE/"scripts/research/p90l90_exit_90d_v2.py"),
      "baseline_trail035_exact_ledger_parity":True,
      "raw_ticks_evaluated":n,"scenarios":reports,
      "limitations":["historical research proxy, not exchange fills",
        "funding, spread, slippage, latency not modeled", "different 2025 Entry depth not compared here"]}
    with (out/"result.json").open("x",encoding="utf8") as g:
        json.dump(manifest,g,ensure_ascii=False,sort_keys=True,indent=2)
        g.write("\n");g.flush();os.fsync(g.fileno())
    print(json.dumps({"stage":"PERIOD_QC_PASS","period":period,"tick_count":n,
        "result":str(out/"result.json"),"source_sha256":sha(src),
        "baseline":{s:len(books[(s,"TRAIL035")].trades) for s in SIDES}}),flush=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--period",choices=("29d","90d"),required=True)
    p.add_argument("--smoke-hours",type=int,default=0)
    a=p.parse_args()
    if not 0<=a.smoke_hours<=6:raise ValueError("smoke must be 0..6h")
    run(a.period,a.smoke_hours)

if __name__=="__main__":main()