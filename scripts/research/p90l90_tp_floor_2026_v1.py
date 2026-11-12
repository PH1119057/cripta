#!/usr/bin/env python3
"""Research-only P90L90 dynamic-TP eligibility sweep: 2026 29d / 90d.

One native Bybit tick merge feeds 14 strictly independent books:
LONG_ONLY / SHORT_ONLY x gross TP-floor {0.50,...,1.10}.
No Strategy, LIVE, DB, runtime, orders or canonical policy changes.
Original 2026 Entry / frozen geometry / TRAIL035 / HARD_STOP / SECOND48
are reused byte-for-byte. Only take_profit_eligible threshold varies.
"""
from __future__ import annotations
import argparse
import hashlib
import heapq
import json
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
    if not (JOB_ID.startswith("C2-R635-TP29-") or JOB_ID.startswith("C2-R635-TP90-")):
        raise ValueError("unexpected research job identity")
    ROOT = pathlib.Path.cwd().resolve()
    expected = pathlib.Path("/data/cripta/jobs/running") / JOB_ID / "bundle"
    if ROOT != expected.resolve() or ROOT.is_symlink():
        raise PermissionError("job output must be its approved writable data-disk spool")
else:
    ROOT = pathlib.Path("/data/cripta/research/runs/p90l90_dynamic_tp_floor_sweep_20261112_v1")
FLOORS = tuple(D(x) for x in ("0.50","0.60","0.70","0.80","0.90","1.00","1.10"))
SIDES = ("LONG_ONLY", "SHORT_ONLY")
COINS = ("LTCUSDT","DOTUSDT","ARBUSDT","APTUSDT","INJUSDT")
THRESHOLD = D("0.50")
ORIGINAL_ELIGIBLE = base.take_profit_eligible

def sha(file):
    h = hashlib.sha256()
    with pathlib.Path(file).open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""):
            h.update(b)
    return h.hexdigest()

def eligible(side, entry, level):
    return base.favorable(side, entry, level) >= THRESHOLD

def preflight(period):
    if period not in ("29d","90d"):
        raise ValueError("unsupported period")
    if os.geteuid()!=107 or os.getegid()!=109:
        raise PermissionError("must run as cripta")
    if not ROOT.is_dir() or ROOT.is_symlink() or ROOT.stat().st_uid!=107:
        raise PermissionError("prepared research root missing or wrong actor")
    src=ROOT/"source_snapshot"/"p90l90_tp_floor_2026_v1.py"
    if src.resolve()!=pathlib.Path(__file__).resolve() or not src.is_file():
        raise RuntimeError("expected frozen driver snapshot")
    if not os.access(ROOT,os.W_OK|os.X_OK):
        raise PermissionError("research root is not writable by cripta")
    free=os.statvfs(ROOT).f_bavail*os.statvfs(ROOT).f_frsize
    if free < 12*2**30:
        raise RuntimeError("research free disk below bounded 12GiB guard")
    if (ROOT/period).exists():
        raise FileExistsError("immutable period result path already exists")
    if base.take_profit_eligible is not ORIGINAL_ELIGIBLE:
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
    global THRESHOLD
    src=preflight(period)
    data,streams,end,refs=get_inputs(period)
    start=sep.entry.START_TS if period=="29d" else ninety.START_TS
    limit=min(end,start+smoke_hours*3600) if smoke_hours else end
    books={(s,f):embargo.EntryEmbargo(2,48) for s in SIDES for f in FLOORS}
    assert len(books)==14 and len(set(map(id,books.values())))==14
    heap=[]
    for i,s in enumerate(COINS):
        tick=next(streams[s],None)
        if tick is None:raise ValueError("empty source stream "+s)
        heapq.heappush(heap,(tick[0],i,tick))
    base.take_profit_eligible=eligible
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
                    THRESHOLD=f
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
                    "floor_050_closed_long":books[("LONG_ONLY",D(".50"))].count["closed"]}),flush=True)
                last_beat=time.monotonic()
            nxt=next(streams[sym],None)
            if nxt is not None:heapq.heappush(heap,(nxt[0],priority,nxt))
    finally:
        base.take_profit_eligible=ORIGINAL_ELIGIBLE
    if smoke_hours:
        print(json.dumps({"status":"SMOKE_PASS_NO_WRITES","period":period,
          "native_ticks":n,"scenarios":{s+"|"+str(f):dict(b.count) for (s,f),b in books.items()}},
          separators=(",",":")),flush=True)
        return
    totals={}
    for s in SIDES:
        old=[json.loads(l) for l in refs[s].open() if l.strip()]
        new=books[(s,D(".50"))].trades
        if old!=new:
            raise RuntimeError("baseline 0.50 trade-by-trade parity failed "+period+" "+s+
                               " old="+str(len(old))+" new="+str(len(new)))
    print(json.dumps({"stage":"BASELINE_PARITY_PASS","period":period,
        "long":len(books[("LONG_ONLY",D(".50"))].trades),
        "short":len(books[("SHORT_ONLY",D(".50"))].trades),"ticks":n}),flush=True)
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
            "threshold_pct":str(floor),"direction":side,
            "counts":summary["counts"],"money":summary["money"],
            "closed_net_usdt_original_10_notional":str(closed_net),
            "gross_exits_by_reason":dict(gross_events),"closed_by_coin":dict(per_coin),
            "censored":summary["censored"],
            "ledger_sha256":None
        }
    out=ROOT/period
    out.mkdir(mode=0o2770)
    for (side,floor),book in books.items():
        label=side.lower()+"_floor"+str(floor).replace(".","p")
        leaf=out/(label+".jsonl")
        with leaf.open("x",encoding="utf8") as g:
            for row in book.trades:
                g.write(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n")
            g.flush();os.fsync(g.fileno())
        reports[side+"|"+str(floor)]["ledger_sha256"]=sha(leaf)
    manifest={
      "status":"RESEARCH_QC_PASS_FROZEN_ENTRY_2026","period":period,
      "purpose":"vary only dynamic TP eligibility threshold; no fixed percentage TP inserted",
      "thresholds_gross_pct":[str(f) for f in FLOORS],
      "sides":SIDES,"SECOND48":"independent by side and floor",
      "trailing_activation_pct":"0.50","trailing_distance_pp":"0.35","hardstop_pct":"-10",
      "source":"native Bybit trades, original frozen P90L90 2026 candidates and geometry",
      "derived_script_sha256":sha(src),
      "dependency_source_sha256":{p.name:sha(p) for p in (SOURCE/"scripts"/"research").glob("p90l90_*_v1.py") if p.name in (
          "p90l90_portfolio_pause_v1.py","p90l90_entry_only_hardstop_embargo_v1.py","p90l90_29d_exit_replay_v1.py")},
      "base_source_sha256":sha(SOURCE/"scripts/research/p90l90_exit_90d_v2.py"),
      "baseline_050_exact_ledger_parity":True,
      "raw_ticks_evaluated":n,"scenarios":reports,
      "limitations":["historical research proxy, not exchange fills",
        "funding, spread, slippage, latency not modeled", "different 2025 Entry depth not compared here"]}
    with (out/"result.json").open("x",encoding="utf8") as g:
        json.dump(manifest,g,ensure_ascii=False,sort_keys=True,indent=2)
        g.write("\n");g.flush();os.fsync(g.fileno())
    print(json.dumps({"stage":"PERIOD_QC_PASS","period":period,"tick_count":n,
        "result":str(out/"result.json"),"source_sha256":sha(src),
        "baseline":{s:len(books[(s,D(".50"))].trades) for s in SIDES}}),flush=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--period",choices=("29d","90d"),required=True)
    p.add_argument("--smoke-hours",type=int,default=0)
    a=p.parse_args()
    if not 0<=a.smoke_hours<=6:raise ValueError("smoke must be 0..6h")
    run(a.period,a.smoke_hours)

if __name__=="__main__":main()
