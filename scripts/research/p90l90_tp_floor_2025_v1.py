#!/usr/bin/env python3
"""2025 183d strict-depthzero P90L90: seven independent dynamic TP eligibility floors.

Reuse byte-pinned historical 2025 native Entry/geometry/Exit and SECOND48.
The +0.50 case must reproduce the original published ledger EXACTLY.
Daily immutable source-SHA checkpoints; never mutate old research or LIVE.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
from collections import Counter
from decimal import Decimal as D

SRC=pathlib.Path("/srv/cripta/source_checkout")
sys.path.insert(0,str(SRC))
from scripts.research import p90l90_bull2025_portfolio_v1 as old
from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as engine
from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research import p90l90_bull2025_entry_v1 as entry

ROOT=pathlib.Path("/data/cripta/research/runs/p90l90_dynamic_tp_floor_sweep_20261112_v1")
OUT=ROOT/"2025"
CHECKPOINT=OUT/"checkpoint"
FLOORS=tuple(D(x) for x in ("0.50","0.60","0.70","0.80","0.90","1.00","1.10"))
SIDES=("LONG_ONLY","SHORT_ONLY")
DEFAULT=D("0.50")
ACTIVE=DEFAULT
ORIGINAL_ELIGIBLE=base.take_profit_eligible
ORIGINAL_HANDLE=old.Book.handle

def sha(path):
 h=hashlib.sha256()
 with pathlib.Path(path).open("rb") as f:
  for block in iter(lambda:f.read(1048576),b""):h.update(block)
 return h.hexdigest()

def eligibility(side,entry_price,target):
 return base.favorable(side,entry_price,target)>=ACTIVE

def with_floor(self,symbol,t,seq,px,events,state,priority):
 global ACTIVE
 ACTIVE=D(self.floor)
 try:
  qualified=tuple(e for e in events if e["side"]==self.side
                  and e["PREV7D"] is True and D(str(e["depth_pct"]))==0)
  return ORIGINAL_HANDLE(self,symbol,t,seq,px,qualified,state,priority)
 finally:
  ACTIVE=DEFAULT

def pin_source():
 if os.geteuid()!=107 or os.getegid()!=109:raise PermissionError("actor must be cripta")
 snap=ROOT/"source_snapshot"/"p90l90_tp_floor_2025_v1.py"
 if pathlib.Path(__file__).resolve()!=snap.resolve() or snap.is_symlink():raise RuntimeError("not executing frozen script snapshot")
 if not ROOT.is_dir() or ROOT.stat().st_uid!=107 or not os.access(ROOT,os.W_OK|os.X_OK):raise PermissionError("research data disk actor access")
 if os.statvfs(ROOT).f_bavail*os.statvfs(ROOT).f_frsize<12*2**30:raise RuntimeError("research remaining disk <12 GiB")
 if not snap.is_file():raise RuntimeError("missing driver")
 reference=old.RESULTS/"portfolio_depthzero_2025_v1/full/long_only/result.json"
 meta=json.loads(reference.read_text())
 for rel,expected in meta["source_pin_files_sha256"].items():
  p=SRC/rel
  if not p.is_file() or sha(p)!=expected:raise RuntimeError("historical 2025 baseline source drift "+rel)
 return snap,meta["source_pin_files_sha256"]

def data_sources():
 data={}
 for sym in old.SYMBOLS:
  folder=old.RESULTS/"entry_full_2025_v1"/sym
  mf=json.loads((folder/"manifest.json").read_text())
  cand=folder/("candidates_"+sym+".jsonl")
  fp=old.FROZEN_PARENT/("states_"+sym+".csv.gz")
  if mf["symbol"]!=sym or len(mf["inputs"])!=193:raise RuntimeError("source manifest mismatch")
  if sha(cand)!=mf["events_sha256"] or sha(fp)!=mf["frozen_l53_sha256"]:raise RuntimeError("frozen candidate/state SHA failure")
  states,_,med7,meta=entry.load_frozen_period(fp,old.EVAL_START,old.EVAL_END)
  assert len(states)==183*288 and len(med7)==183
  events,counters=base.read_events(cand,sym)
  assert counters["raw_candidates"]==mf["events"]
  data[sym]={"manifest":mf,"events":events,"states":states,"symbol":sym}
 return data

def fresh_books():
 books={}
 for side in SIDES:
  for floor in FLOORS:
   book=old.Book(side,"SECOND48","fixed",[])
   book.floor=str(floor)
   books[side+"|"+str(floor)]=book
 return books

def write_checkpoint(day,state,source_sha):
 name="day_"+str(day).zfill(3)+".json"
 dest=CHECKPOINT/name
 if dest.exists():raise FileExistsError(str(dest))
 tmp=CHECKPOINT/(name+".pending")
 if tmp.exists():raise RuntimeError("stale partial checkpoint; owner forensic required")
 payload={"research":"P90L90_TP_FLOOR_2025_V1","completed_day_zero_based":day,
          "next_day":state["next_day"],"source_sha":source_sha,
          "state":engine.encode(state)}
 with tmp.open("x",encoding="utf-8") as f:
  json.dump(payload,f,sort_keys=True,separators=(",",":"))
  f.write("\n");f.flush();os.fsync(f.fileno())
 tmp.rename(dest)
 return sha(dest)

def resume_state(data,books,source_sha):
 items=sorted(CHECKPOINT.glob("day_*.json"))
 if not items:
  return engine.blank_state(data),0
 if len(items)>183:raise RuntimeError("too many checkpoints")
 for i,path in enumerate(items):
  if path.name!="day_"+str(i).zfill(3)+".json":raise RuntimeError("noncontiguous checkpoints")
 if list(CHECKPOINT.glob("*.pending")):raise RuntimeError("incomplete prior checkpoint")
 obj=json.loads(items[-1].read_text())
 if obj["source_sha"]!=source_sha or obj["next_day"]!=len(items) or obj["completed_day_zero_based"]!=len(items)-1:raise RuntimeError("resume checkpoint identity mismatch")
 state=engine.decode(obj["state"])
 assert state["next_day"]==len(items)
 engine.restore_books(books,state["books"])
 return state,len(items)

def run(pilot_days):
 global ACTIVE
 snap,pins=pin_source()
 old.START_WALLET=D("1000000000000")
 old.FIXED_MARGIN=D("20")
 old.LEVERAGE=D("5")
 assert old.FIXED_MARGIN*old.LEVERAGE==D("100")
 if pilot_days and OUT.exists():raise FileExistsError("pilot is read-only")
 if not pilot_days and OUT.exists() and not CHECKPOINT.is_dir():raise RuntimeError("unknown output directory")
 data=data_sources()
 books=fresh_books()
 if pilot_days:
  state=engine.blank_state(data);start=0
 else:
  OUT.mkdir(mode=0o2770,exist_ok=True)
  CHECKPOINT.mkdir(mode=0o2770,exist_ok=True)
  state,start=resume_state(data,books,sha(snap))
 base.take_profit_eligible=eligibility
 old.Book.handle=with_floor
 now=time.monotonic()
 try:
  stop=min(183,start+pilot_days) if pilot_days else 183
  for i in range(start,stop):
   engine.replay_day(i,state,data,books,"LONG_ONLY")
   if not pilot_days:
    saved=write_checkpoint(i,state,sha(snap))
    print(json.dumps({"stage":"DAY_PERSISTED","day":i+1,"of":183,
                      "native_ticks":state["ticks"],"checkpoint_sha":saved,
                      "elapsed_s":round(time.monotonic()-now,2)}),flush=True)
 except:
  raise
 finally:
  old.Book.handle=ORIGINAL_HANDLE
  base.take_profit_eligible=ORIGINAL_ELIGIBLE
  ACTIVE=DEFAULT
 if pilot_days:
  print(json.dumps({"status":"SMOKE_PASS_NO_WRITES","days":pilot_days,
    "native_ticks":state["ticks"],
    "counts":{k:dict(book.counts) for k,book in books.items()}}),flush=True)
  return
 assert state["next_day"]==183
 for side in SIDES:
  book=books[side+"|0.50"]
  folder=old.RESULTS/"portfolio_depthzero_2025_v1/full"/side.lower()
  ref=[json.loads(l) for l in (folder/"ledger_SECOND48_fixed.jsonl").open() if l.strip()]
  if book.trades!=ref:
   raise RuntimeError("strict-depthzero baseline 0.50 ledger parity FAILURE "+side+" expected="+str(len(ref))+" actual="+str(len(book.trades)))
 print(json.dumps({"stage":"BASELINE_PARITY_PASS","long":len(books["LONG_ONLY|0.50"].trades),"short":len(books["SHORT_ONLY|0.50"].trades)}),flush=True)
 report={}
 for key,book in books.items():
  detail=book.finish(state["prices"],old.END_TS)
  side,floor=key.split("|")
  assert detail["ledger_rows"]==len(book.trades)
  assert D(detail["closed_net_after_fees_usdt"])==sum((D(r["net_usdt"]) for r in book.trades),D(0))
  label=side.lower()+"_floor"+floor.replace(".","p")
  f=OUT/(label+".jsonl")
  with f.open("x",encoding="utf-8") as stream:
   for row in book.trades:stream.write(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n")
   stream.flush();os.fsync(stream.fileno())
  report[key]={"counts":detail["counts"],"closed_net_usdt":detail["closed_net_after_fees_usdt"],
     "wallet_change_includes_open_entry_fee":detail["wallet_change_includes_open_entry_fees_usdt"],
     "exit_reasons":dict(Counter(x["exit_reason"] for x in book.trades)),
     "censored":detail["open_censored"],"ledger_sha256":sha(f)}
 final={"status":"RESEARCH_QC_PASS_TP_FLOOR_2025","period":"2025_183D",
       "baseline_050_exact_ledger_parity":True,
       "entry":"2025 P90L90 strict H9 depth0 PREV7D +/-0.50pp",
       "thresholds_gross_pct":[str(f) for f in FLOORS],
       "exit":"dynamic RAW L5-3 opposite; only admissibility threshold changed; TRAIL035 +0.5 / 0.35pp; HS -10",
       "SECOND48":"independent for each side and each threshold; no forced flatten",
       "nominal_usdt_per_trade":"100","cash_constraint":"unconstrained virtual base; fixed 20 USD synthetic margin x5",
       "source_driver_sha256":sha(snap),"source_original_sha256":pins,
       "native_ticks_evaluation":state["ticks"],"days":183,"books":report,
       "omissions":["funding","spread","slippage","latency","exchange real fills"]}
 with (OUT/"result.json").open("x",encoding="utf8") as stream:
  json.dump(final,stream,indent=2,ensure_ascii=False);stream.write("\n");stream.flush();os.fsync(stream.fileno())
 print(json.dumps({"stage":"2025_QC_PASS","result":str(OUT/"result.json"),"ticks":state["ticks"]}),flush=True)

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--pilot-days",type=int,default=0)
 args=p.parse_args()
 if args.pilot_days not in (0,1):raise ValueError("pilot-days must be 0 or 1")
 run(args.pilot_days)
if __name__=="__main__":main()
