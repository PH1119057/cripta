#!/usr/bin/env python3
"""Research-only SECOND48 on/off full native portfolio counterfactual #866."""
from __future__ import annotations
import argparse, hashlib, heapq, importlib.util, json, os, shutil, time
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

SYMS=("LTCUSDT","DOTUSDT","ARBUSDT","APTUSDT","INJUSDT")
CASES=("TPONLY_BASELINE","SOFT_STOP_18H")
QPERIODS=("183d","90d","29d")
ORIGINAL_JOBS={
 "183d":"C2-R866-SS18-N100-P183-FULL1-20261119",
 "90d":"C2-R866-SS18-N100-FULL1-20261119",
 "29d":"C2-R866-SS18-N100-FULL1-20261119",
}
SOURCE_COMMIT="6ee3c557db939fb5352cbdc59fbde2a60c7f3d61"
EXPECTED_BASE_SOURCE_SHA="a9ea1d19defce039fdb02d7465cdc39930ed949616c41ee95fa30bc6fb5fea48"

def sha(p):
 with Path(p).open("rb") as fd:return hashlib.file_digest(fd,"sha256").hexdigest()

def main_driver():
 path=Path(__file__).resolve().parent/"source_snapshot/p90l90_soft_stop_2025_183d_replay_v1.py"
 if sha(path)!=EXPECTED_BASE_SOURCE_SHA:raise ValueError("2025 and 2026 source driver hash drift")
 spec=importlib.util.spec_from_file_location("research_866_softstop_2025_pinned",path)
 mod=importlib.util.module_from_spec(spec)
 spec.loader.exec_module(mod)
 return mod

def no_pause(standard):
 class No48(standard):
  def handle(self,*args,**kwargs):
   if self.pause_until!=float("-inf"):
    raise ValueError("NO48 previously retained a pause before next native event")
   result=super().handle(*args,**kwargs)
   self.pause_until=float("-inf")
   return result
 return No48

def read_legacy(period):
 root=Path("/srv/cripta-share/reports/jobs")/ORIGINAL_JOBS[period]/("soft_stop_"+period)
 report=json.loads((root/"result.json").read_text())
 if report["status"]!="QC_PASS" or report["period"]!=period:
  raise ValueError("original report not QC-pass")
 ledger=root/"trade_ledger.jsonl"
 if sha(ledger)!=report["ledger_sha256"]:raise ValueError("original ledger sha drift")
 buckets=defaultdict(list)
 for line in ledger.open():
  row=json.loads(line)
  buckets[row["portfolio_scenario"]].append(row)
 if set(buckets)!=set(CASES):raise ValueError("original outcome cases mismatch")
 return report,buckets

def equality(a,b):
 fields=("candidate_event_id","symbol","side","entry_ts","exit_ts",
 "entry_price","exit_price","exit_reason","gross_usdt","entry_fee_usdt",
 "exit_fee_usdt","net_usdt","maker_exit")
 if len(a)!=len(b):raise ValueError("original vs replay baseline ledger row count drift")
 for i,(x,y) in enumerate(zip(a,b)):
  for key in fields:
   if x.get(key)!=y.get(key):raise ValueError("original vs replay ledger drift: "+str((i,key,x.get(key),y.get(key))))
  if x.get("soft_stop_cause")!=y.get("soft_stop_cause"):
   raise ValueError("original vs replay cause drift")

def stats(src,b,report):
 out=src.stats(b,report)
 out["would_trigger_second48_if_enabled"]=b.count["pause_triggers"]
 return out

def timeline(src,period):
 src.ROOTS.update({"90d":src.tp.ROOT,"29d":src.r29.ROOT})
 src.STATES.update({"90d":"l53_clean_v2","29d":"l53_clean_29d"})
 if period=="183d":
  data=src.load_2025_frozen()
  streams={s:src.bull25.native_ticks(data[s]) for s in SYMS}
  start,end=src.bull25.START_TS,src.bull25.END_TS
 elif period=="90d":
  data={s:src.tp.old.previous.inputs(s,src.tp.old.previous.RAW_DEFAULT,src.tp.old.previous.FROZEN_DEFAULT) for s in SYMS}
  streams={s:src.tp.old.previous.ticks(data[s]) for s in SYMS}
  start,end=src.tp.old.entry90.START_TS,src.tp.old.entry90.END_TS
 elif period=="29d":
  data={s:src.r29.read_inputs(s) for s in SYMS}
  streams={s:src.r29.native_ticks(data[s]) for s in SYMS}
  start,end=src.r29.FIRST,src.r29.END
 else:raise ValueError("invalid period")
 geo,checks=src.h9_states(period,data)
 return data,streams,start,end,geo,checks

def run(period,smoke_hours):
 src=main_driver()
 if src.tp.old.base.NOMINAL not in (D("10"),D("100")):raise ValueError("research notional mismatched")
 src.tp.old.base.NOMINAL=D("100")
 data,streams,start,end,geo,geometry_parity=timeline(src,period)
 no_tp=no_pause(src.tp.TPOnlyBook)
 no_ss=no_pause(src.SSBook)
 books={
  "TPONLY_BASELINE":src.tp.TPOnlyBook(),
  "SOFT_STOP_18H":src.SSBook(geo),
  "TPONLY_NO_SECOND48":no_tp(),
  "SOFT_NO_SECOND48":no_ss(geo),
 }
 paused_to_no={"TPONLY_BASELINE":"TPONLY_NO_SECOND48","SOFT_STOP_18H":"SOFT_NO_SECOND48"}
 blocked={k:[] for k in CASES}
 heap=[]
 for i,s in enumerate(SYMS):
  nxt=next(streams[s],None)
  if nxt is None:raise ValueError("no native data")
  heapq.heappush(heap,(nxt[0],i,nxt))
 count=0;day=None
 cutoff=end if smoke_hours==0 else min(end,start+smoke_hours*3600)
 while heap:
  t,i,z=heapq.heappop(heap)
  if t>=cutoff:break
  _,seq,px,_=z
  symbol=SYMS[i]
  events=data[symbol]["events"].get(seq,())
  state=data[symbol]["states"].get(int(t//300)*300)
  for name,book in books.items():
   if not events and book.pos[symbol] is None:continue
   before=book.count["blocked_global_pause"]
   book.handle(symbol,t,seq,px,events,state)
   delta=book.count["blocked_global_pause"]-before
   if delta:
    if name not in paused_to_no:raise ValueError("NO48 never blocks global pause")
    qualified=[e for e in events if e["PREV7D"]]
    if len(qualified)!=delta or delta!=1 or book.pending:
     raise ValueError("direct SECOND48 blocked signal not uniquely identified")
    e=qualified[0]
    blocked[name].append({"event_id":e["event_id"],"ts":str(e["ts"]),
     "side":e["side"],"symbol":symbol})
  count+=1
  d=int(t//86400)
  if d!=day:
   day=d
   print(json.dumps({"phase":"SECOND48_NATIVE_COUNTERFACTUAL","period":period,
    "utc_day_epoch":d,"ticks":count}),flush=True)
  nxt=next(streams[symbol],None)
  if nxt is not None:heapq.heappush(heap,(nxt[0],i,nxt))
 results={k:b.finish() for k,b in books.items()}
 candidate_counts={name:r["counts"].get("qualified_candidate",0) for name,r in results.items()}
 if len(set(candidate_counts.values()))!=1:raise ValueError("input candidate parity failure")
 if any(books[k].count["blocked_global_pause"] for k in ("TPONLY_NO_SECOND48","SOFT_NO_SECOND48")):
  raise ValueError("no-pause book suppressed candidates")
 for name in CASES:
  if len(blocked[name])!=books[name].count["blocked_global_pause"]:
   raise ValueError("blocked evidence missing on paused portfolio "+name)
 if smoke_hours:
  print("SMOKE_PASS",json.dumps({"period":period,"ticks":count,
   "qualified_candidates":candidate_counts,"blocked":{k:len(v) for k,v in blocked.items()},
   "accepted":{k:v["counts"].get("accepted",0) for k,v in results.items()}}),flush=True)
  return
 original,ledger=read_legacy(period)
 if count!=original["native_ticks"]:raise ValueError("native tick coverage mismatch")
 if geometry_parity!=original["geometry_parity"]:raise ValueError("H9 geometry parity source mismatch")
 for name in CASES:
  equality(ledger[name],books[name].trades)
  if original["scenarios"][name]["counts"]!=results[name]["counts"]:
   raise ValueError("reference pause scenario account mismatch")
  if original["scenarios"][name]["net_taker_taker_usdt"]!=str(
    sum((src.tt_net(r) for r in books[name].trades),D(0))):
   raise ValueError("reference pause portfolio PnL mismatch")
 out_parent=os.environ.get("CRIPTA_REPORT_DIR")
 if not out_parent:raise ValueError("full calculation requires approved runner spool")
 target=Path(out_parent)/("second48_counterfactual_"+period)
 if target.exists() or target.is_symlink():raise FileExistsError("immutable output collision")
 if not os.access(target.parent,os.W_OK|os.X_OK):raise PermissionError("research output inaccessible")
 detail={}
 for paused,no in paused_to_no.items():
  post={r["candidate_event_id"]:r for r in books[no].trades}
  cens={r["entry_event_id"]:r for r in results[no]["censored"]}
  all_accepted=set(post)|set(cens)
  ledger_paused={r["candidate_event_id"] for r in books[paused].trades}
  tally=Counter();tally_side=defaultdict(Counter);sum_money=defaultdict(lambda:D(0))
  for e in blocked[paused]:
   eid=e["event_id"];candidate_side=e["side"]
   if eid in post:
    reason=post[eid]["exit_reason"];fate="NO48_"+reason
    sum_money[fate]+=src.tt_net(post[eid])
   elif eid in cens:
    fate="NO48_CENSORED_OPEN"
   else:fate="NO48_NOT_ACCEPTED_DIFFERENT_OCCUPANCY"
   tally[fate]+=1;tally_side[candidate_side][fate]+=1
   if eid in ledger_paused:raise ValueError("blocked candidate also closed in paused scenario")
  if sum(tally.values())!=len(blocked[paused]):raise ValueError("blocked tally mismatch")
  only_no=all_accepted-{r["candidate_event_id"] for r in books[paused].trades}
  detail[paused]={"direct_blocked_total":len(blocked[paused]),
   "direct_blocked_fate_in_no48":dict(tally),
   "direct_blocked_side_fates":{k:dict(v) for k,v in tally_side.items()},
   "direct_blocked_no48_closed_net_by_reason_usdt":{k:str(v) for k,v in sum_money.items()},
   "accepted_no48_minus_paused":results[no]["counts"]["accepted"]-results[paused]["counts"]["accepted"],
   "no48_accepted_not_in_paused_closed_count":len(only_no),
   "original_pause_triggers":results[paused]["counts"]["pause_triggers"]}
 summ={k:stats(src,b,results[k]) for k,b in books.items()}
 report={"research":"P90L90_SECOND48_ON_OFF_NATIVE_COUNTERFACTUAL_V1","status":"QC_PASS",
  "period":period,"native_ticks":count,"evaluation_utc":[start,end],
  "nominal_usdt_per_entry":"100","fee_model":"TAKER/TAKER primary, TP maker sensitivity separate",
  "entry":"P90L90 RAW H9 L53 PREV7D",
  "exit":"TPONLY dynamic opposite RAW L53, Hard Stop -10%, SOFT optional L53 -4% width>=4 or H9+18h, no trailing/BE",
  "pause":"SECOND48 on if 2 actual Hard Stops in rolling 48h, blocks only new Entry for 48h",
  "no_pause":"same exact Exit and occupancy semantics, SECOND48 suppression disabled per tick only",
  "source_original_driver_sha256":EXPECTED_BASE_SOURCE_SHA,
  "source_original_git_commit":SOURCE_COMMIT,
  "geometry_parity":geometry_parity,
  "scenarios":summ,"direct_blocked_counterfactual":detail,
  "source_snapshot_sha256":{},"ledger_sha256":None,"blocked_sha256":None,
  "ledger_rows":sum(len(b.trades) for b in books.values()),
  "censored_by_case":{k:v["censored"] for k,v in results.items()},
  "unmodeled":["funding","spread","slippage","liquidity impact","exchange order fills"]}
 target.mkdir(mode=0o770)
 snaps=target/"source_snapshot"
 snaps.mkdir(mode=0o770)
 source_files=(Path(__file__),Path(src.__file__),Path(src.tp.__file__),Path(src.tp.old.__file__),
     Path(src.tp.old.previous.__file__),Path(src.tp.old.base.__file__),
     Path(src.r29.__file__),Path(src.r29.entry.__file__),
     Path(src.bull25.__file__),Path(src.bull25.entry.__file__))
 for f in source_files:
  target_file=snaps/f.name
  shutil.copyfile(f,target_file)
  report["source_snapshot_sha256"][f.name]=sha(target_file)
 ledger_file=target/"trade_ledger.jsonl"
 with ledger_file.open("x") as fd:
  for name,book in books.items():
   for row in book.trades:
    fd.write(json.dumps({**row,"portfolio_scenario":name},sort_keys=True,separators=(",",":"))+"\n")
 blocked_file=target/"direct_blocked_candidate.jsonl"
 with blocked_file.open("x") as fd:
  for name,events in blocked.items():
   for e in events:
    fd.write(json.dumps({**e,"portfolio_scenario":name},sort_keys=True,separators=(",",":"))+"\n")
 report["ledger_sha256"]=sha(ledger_file)
 report["blocked_sha256"]=sha(blocked_file)
 with (target/"result.json").open("x") as fd:
  json.dump(report,fd,indent=2,sort_keys=True);fd.write("\n")
 print("PERIOD_COMPLETE",json.dumps({"period":period,"native_ticks":count,
  "accepted":{k:r["counts"]["accepted"] for k,r in results.items()},
  "net_taker_taker":{k:z["net_taker_taker_usdt"] for k,z in summ.items()},
  "direct_blocked_fates":{k:v["direct_blocked_fate_in_no48"] for k,v in detail.items()},
  "output":str(target)}),flush=True)

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument("--period",choices=QPERIODS,default="183d")
 parser.add_argument("--all-periods",action="store_true")
 parser.add_argument("--smoke-hours",type=int,default=0)
 args=parser.parse_args()
 if (os.geteuid(),os.getegid())!=(107,109):raise PermissionError("approved cripta UID107 GID109 required")
 if not 0<=args.smoke_hours<=24:raise ValueError("smoke-hours must be bounded")
 for period in (QPERIODS if args.all_periods else (args.period,)):
  run(period,args.smoke_hours)
if __name__=="__main__":main()
