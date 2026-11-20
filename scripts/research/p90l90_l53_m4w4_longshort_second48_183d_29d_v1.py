#!/usr/bin/env python3
"""Issue #866: same independent LONG/SHORT L53-only Soft Stop on 183d + 29d.

FOUR independent side-scoped five-symbol portfolios:
LONG_S48_ON/OFF, SHORT_S48_ON/OFF.
TWO full-tape immutable parity controls: MIXED_S48_ON/OFF.
Per-period frozen 183d/29d candidate, PREV7D, H9 Entry, L5-3 TP and -10% HS.
Only Exit SOFT_STOP: opposite closed RAW L5-3 adverse >=4pp from Entry
with working zone width >=4pp. NO TRAIL, BE, H9 Exit or H9 time logic.
Each independent side has its OWN rolling-48h second hardstop Entry embargo;
no cross-side cooldown and no side netting. RESEARCH ONLY; NO LIVE.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import heapq
import importlib.util
import json
import os
import shutil
from collections import Counter
from decimal import Decimal as D, ROUND_HALF_EVEN
from pathlib import Path

SYMS=("LTCUSDT","DOTUSDT","ARBUSDT","APTUSDT","INJUSDT")
CASES=("LONG_S48_ON","LONG_S48_OFF","SHORT_S48_ON","SHORT_S48_OFF")
CONTROLS=("MIXED_S48_ON","MIXED_S48_OFF")
PARENT="p90l90_l53_only_m4w4_second48_3period_v3.py"
PARENT_SHA="1a2dd293c27f5aa2f7be07eb81d5aaeed8e7570c12dd152e9db6ce504410cdc0"
REFERENCE_JOB="C2-R866-L53ONLY-3P-S48-V3-20261120"
NOTIONAL=D("100")
FEE=D("0.00055")
MONEY_Q=D("0.000000000001")
UTC=dt.timezone.utc

def sha(p:Path)->str:
 with p.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()

def load_pinned():
 p=Path(__file__).resolve().parent/PARENT
 if not p.is_file() or sha(p)!=PARENT_SHA:
  raise ValueError("pinned L53 baseline source missing or changed")
 spec=importlib.util.spec_from_file_location("pinned_l53_866_side_v1",p)
 v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
 if v.SYMS!=SYMS or v.NOTIONAL!=NOTIONAL:
  raise ValueError("pinned original Entry/nominal/symbol contract drift")
 return v

def direction_candidates(candidates,side):
 return tuple(x for x in candidates if x["side"]==side)

def unit_tests(v):
 v.unit_tests()  # 26 pinned L5-3 boundary/stop-priority assertions
 events=(
  {"side":"LONG","PREV7D":True,"event_id":"L"},
  {"side":"SHORT","PREV7D":True,"event_id":"S"})
 assert [z["event_id"] for z in direction_candidates(events,"LONG")]==["L"]
 assert [z["event_id"] for z in direction_candidates(events,"SHORT")]==["S"]
 src,_=v.original_modules()
 src.tp.old.base.NOMINAL=NOTIONAL
 start=1750000000//300*300
 # LONG and SHORT on SAME symbol must be independently occupiable.
 books={side:v.model_factory(src,True) for side in ("LONG","SHORT")}
 for side in ("LONG","SHORT"):
  ev={"side":side,"price":"100","ts":str(start),"event_id":side,
      "trade_id":"synthetic-"+side,"PREV7D":True}
  books[side].handle("LTCUSDT",float(start+60),1,D("100"),(ev,),
                     (D("95"),D("105"),D("2")))
  assert books[side].count["accepted"]==1 and books[side].pos["LTCUSDT"] is not None
 assert books["LONG"].pos["LTCUSDT"] is not books["SHORT"].pos["LTCUSDT"]
 # Long two true -10% HS within 48h: LONG new-Entry embargo; SHORT unaffected.
 for symbol,t in (("LTCUSDT",start+120),("DOTUSDT",start+240)):
  if symbol=="DOTUSDT":
   ev={"side":"LONG","price":"100","ts":str(start+180),
       "event_id":"L2","trade_id":"L2","PREV7D":True}
   books["LONG"].handle(symbol,float(start+180),2,D("100"),(ev,),
                         (D("95"),D("105"),D("2")))
  books["LONG"].handle(symbol,float(t),3,D("89"),(),
                        (D("90"),D("95"),D("5")))
 assert books["LONG"].count["close_HARD_STOP"]==2
 assert books["LONG"].count["pause_triggers"]>=1
 assert books["SHORT"].count.get("pause_triggers",0)==0
 ev_long={"side":"LONG","price":"100","ts":str(start+300),
          "event_id":"L3","trade_id":"L3","PREV7D":True}
 ev_short={"side":"SHORT","price":"100","ts":str(start+300),
           "event_id":"S3","trade_id":"S3","PREV7D":True}
 books["LONG"].handle("APTUSDT",float(start+300),4,D("100"),(ev_long,),
                      (D("95"),D("105"),D("2")))
 books["SHORT"].handle("APTUSDT",float(start+300),4,D("100"),(ev_short,),
                       (D("95"),D("105"),D("2")))
 assert books["LONG"].count["blocked_global_pause"]==1
 assert books["SHORT"].count["accepted"]==2
 print("UNIT_PASS",json.dumps({"pin_source_sha":PARENT_SHA,
  "L53_exact_boundary_tests":26,"side_filter":True,
  "parallel_opposite_slots":True,"side_scoped_second48":True}),flush=True)

def reference(period:str):
 root=Path("/srv/cripta-share/reports/jobs")/REFERENCE_JOB/("l53_only_"+period)
 j=json.loads((root/"result.json").read_text())
 led=root/"trade_ledger.jsonl"
 if j["period"]!=period or j["status"]!="QC_PASS" or sha(led)!=j["ledger_sha256"]:
  raise ValueError("immutable full "+period+" mixed L53 reference QC missing")
 rows={"L53_ONLY_S48_ON":[],"L53_ONLY_S48_OFF":[]}
 for line in led.open():
  a=json.loads(line)
  if a["portfolio_scenario"] in rows:rows[a["portfolio_scenario"]].append(a)
 assert len(rows["L53_ONLY_S48_ON"])==j["scenarios"]["L53_ONLY_S48_ON"]["counts"]["closed"]
 assert len(rows["L53_ONLY_S48_OFF"])==j["scenarios"]["L53_ONLY_S48_OFF"]["counts"]["closed"]
 return j,rows

def normalized_net_taker(rows):
 gross=sum((D(r["gross_usdt"]) for r in rows),D(0))
 entry=sum((D(r["entry_fee_usdt"]) for r in rows),D(0))
 exitfee=sum(((NOTIONAL*D(r["exit_price"])/D(r["entry_price"])*FEE)
              .quantize(MONEY_Q,rounding=ROUND_HALF_EVEN) for r in rows),D(0))
 return gross-entry-exitfee

def replay(period:str,smoke_hours:int):
 v=load_pinned()
 src,_=v.original_modules()
 data,streams,first,end,geometry,geometry_parity=v.load_input(src,period)
 del geometry  # H9 is Entry only; H9 exit/continuity never evaluated.
 expected,reference_rows=reference(period)
 assert len(data)==5 and tuple(SYMS)==tuple(v.SYMS)
 books={c:v.model_factory(src,c.endswith("_ON")) for c in CASES+CONTROLS}
 heap=[]
 for i,s in enumerate(SYMS):
  first_tick=next(streams[s],None)
  if first_tick is None:raise ValueError("empty frozen native market for "+s)
  heapq.heappush(heap,(first_tick[0],i,first_tick))
 n=0;day=None
 cutoff=min(end,first+smoke_hours*3600) if smoke_hours else end
 while heap:
  t,idx,event=heapq.heappop(heap)
  if t>=cutoff:break
  _,seq,px,_=event
  sym=SYMS[idx]
  events=data[sym]["events"].get(seq,())
  state=data[sym]["states"].get(int(t//300)*300)
  for name,book in books.items():
   candidates=events if name.startswith("MIXED") else direction_candidates(events,name.split("_")[0])
   if candidates or book.pos[sym] is not None:
    book.handle(sym,t,seq,px,candidates,state)
  n+=1
  today=int(t//86400)
  if today!=day:
   day=today
   print(json.dumps({"phase":"L53_LONG_SHORT_NATIVE","period":period,"day_epoch":day,"ticks":n}),flush=True)
  next_tick=next(streams[sym],None)
  if next_tick is not None:heapq.heappush(heap,(next_tick[0],idx,next_tick))
 reports={name:book.finish() for name,book in books.items()}
 for name in books:
  x=reports[name];c=x["counts"]
  if c.get("accepted",0)!=c.get("closed",0)+c.get("censored_open",0):
   raise ValueError("accepted != closed+censored in "+name)
  if name.endswith("_OFF") and (c.get("blocked_global_pause",0) or c.get("pause_triggers",0)):
   raise ValueError("SECOND48 OFF mutated "+name)
  if c.get("close_PROTECTED_STOP",0):raise ValueError("trailing stop unexpectedly active")
  if set(x["soft_stop_causes"])-{"L53_RAW_MINUS4_WIDTH4"}:
   raise ValueError("unknown SoftStop "+name)
  if name.startswith("LONG") and any(z["side"]!="LONG" for z in books[name].trades):
   raise ValueError("LONG scenario contains SHORT")
  if name.startswith("SHORT") and any(z["side"]!="SHORT" for z in books[name].trades):
   raise ValueError("SHORT scenario contains LONG")
 assert n>0
 for pause in ("ON","OFF"):
  lc=reports["LONG_S48_"+pause]["counts"].get("qualified_candidate",0)
  sc=reports["SHORT_S48_"+pause]["counts"].get("qualified_candidate",0)
  mc=reports["MIXED_S48_"+pause]["counts"].get("qualified_candidate",0)
  if lc+sc!=mc:raise ValueError("direction candidate partition "+pause)
 if smoke_hours:
  print("SMOKE_PASS",json.dumps({"hours":smoke_hours,"ticks":n,
   "counts":{k:{"qualified":z["counts"].get("qualified_candidate",0),
    "accepted":z["counts"].get("accepted",0),
    "closed":z["counts"].get("closed",0)} for k,z in reports.items()}},
   sort_keys=True),flush=True)
  return
 if n!=expected["native_ticks"] or geometry_parity!=expected["geometry_parity"]:
  raise ValueError("full-period native source or geometric-causality drift")
 for flag in ("ON","OFF"):
  key="MIXED_S48_"+flag
  refkey="L53_ONLY_S48_"+flag
  v.same_classical_result(reference_rows[refkey],books[key].trades)
  if reports[key]["counts"]!=expected["scenarios"][refkey]["counts"]:
   raise ValueError("full native mixed control count drift "+flag)
  if normalized_net_taker(books[key].trades)!=D(expected["scenarios"][refkey]["net_taker_taker_usdt"]):
   raise ValueError("full native mixed control fee/PnL drift "+flag)
 result_dir=os.environ.get("CRIPTA_REPORT_DIR")
 if not result_dir:raise ValueError("cannot write results without approved runner path")
 out=Path(result_dir)/("l53_longshort_"+period)
 if out.exists() or out.is_symlink() or not os.access(out.parent,os.W_OK|os.X_OK):
  raise PermissionError("immutable research target collision or parent inaccessible")
 trade_rows=[]
 report_cases={}
 for case in CASES:
  b=books[case];r=reports[case]
  per_coin={}
  for sym in SYMS:
   selected=[x for x in b.trades if x["symbol"]==sym]
   z=Counter(x["exit_reason"] for x in selected)
   per_coin[sym]={"closed":len(selected),"hard_stop":z["HARD_STOP"],
    "soft_stop":z["SOFT_STOP"],"tp":z["RESTING_TP"]+z["MARKETABLE_TP"],
    "net_taker_taker_usdt":str(normalized_net_taker(selected))}
  totals=src.stats(b,r)
  if normalized_net_taker(b.trades)!=D(totals["net_taker_taker_usdt"]):
   raise ValueError("fee model parity "+case)
  report_cases[case]={**totals,"by_symbol":per_coin,
   "raw_observed_hardstops":b.all_stops,
   "second48_actual_pause_triggers":b.stops}
  trade_rows.extend({**z,"portfolio_scenario":case} for z in b.trades)
 for flag in ("ON","OFF"):
  lc=books["LONG_S48_"+flag];sc=books["SHORT_S48_"+flag]
  assert len(lc.all_stops)==reports["LONG_S48_"+flag]["counts"].get("close_HARD_STOP",0)
  assert len(sc.all_stops)==reports["SHORT_S48_"+flag]["counts"].get("close_HARD_STOP",0)
 cross={}
 for flag in ("ON","OFF"):
  l=books["LONG_S48_"+flag].trades
  s=books["SHORT_S48_"+flag].trades
  lh=[z for z in l if z["exit_reason"]=="HARD_STOP"]
  sh=[z for z in s if z["exit_reason"]=="HARD_STOP"]
  a=[x for x in s if any(h["symbol"]==x["symbol"] and
      D(0)<D(x["entry_ts"])-D(h["exit_ts"])<=D(48*3600) for h in lh)]
  b=[x for x in l if any(h["symbol"]==x["symbol"] and
      D(0)<D(x["entry_ts"])-D(h["exit_ts"])<=D(48*3600) for h in sh)]
  pairs=sum(abs(D(x["exit_ts"])-D(y["exit_ts"]))<=D(48*3600) for x in lh for y in sh)
  cross[flag]={"long_hardstop_to_short_entry_same_coin_48h":
    {"closed_accepted":len(a),"hardstop":sum(x["exit_reason"]=="HARD_STOP" for x in a),
     "net_taker_taker_usdt":str(normalized_net_taker(a))},
   "short_hardstop_to_long_entry_same_coin_48h":
    {"closed_accepted":len(b),"hardstop":sum(x["exit_reason"]=="HARD_STOP" for x in b),
     "net_taker_taker_usdt":str(normalized_net_taker(b))},
   "cross_side_hardstop_pairs_within_48h":pairs}
 manifest={
  "research":"P90L90_L53_M4W4_LONGSHORT_SECOND48_183D_29D_V1",
  "status":"QC_PASS","period":period,"period_utc":[first,end],
  "symbols":SYMS,"native_ticks":n,
  "entry":"P90L90 causal H9 5m/15m exact RAW relevant side, RAW_H9_UNION_SPAN depth 0..15%, native ordered RAW L53 first crossing, PREV7D previous seven FULL UTC days median L53 width +/-0.5pp",
  "exit":"NO TRAIL/BE/H9 Exit; priority original hard stop -10% and opposite RAW L53 dynamic TP, then additional first-event SOFT_STOP iff adverse opposite L53 boundary >=4pp from Entry AND current working L53 width >=4pp, exit at triggering native price",
  "second48":"Within each side strategy independently, second actual same-strategy hardstop rolling 48h blocks new Entry that side for 48h on all five symbols; OFF has no pause; never forces close",
  "position":"one slot per symbol per direction/book; LONG/SHORT independent and may overlap opposite side on same coin; no same-tick reentry or flip; terminal open censored not realized",
  "cost":"100 USDT notional per position; TAKER/TAKER 0.055% each side primary net after fees, marketable native tick assumption; no spread/funding/slippage/live fills",
  "filters":{"PREV7D":True,"H9_ENTRY":True,"H9_EXIT":False,
    "L53_RAW_ADVERSE_GE_4PP":True,"L53_WIDTH_GE_4PP":True,
    "TRAIL035":False,"BREAK_EVEN":False,"STAY":False,"OI":False,
    "SECOND48":"ON and OFF distinct per LONG/SHORT"},
  "cases":CASES,"scenarios":report_cases,"cross_side_signals":cross,
  "mixed_control_full_ledger_parity":"PASS_ON_AND_OFF",
  "mixed_reference_job":REFERENCE_JOB,
  "mixed_reference_ledger_sha256":expected["ledger_sha256"],
  "parent_script_sha256":PARENT_SHA,
  "legacy_source_sha256":v.SRC_SHA,
  "second48_source_sha256":v.COUNTER_SHA,
  "geometry_parity":geometry_parity,
  "ledger_rows":len(trade_rows),"ledger_sha256":None,
  "source_snapshot_sha256":{},"unmodeled":
    ["funding","spread","slippage","market impact","margin liquidation","real exchange fills"],
  "not_pristine_blinded_oos":True}
 out.mkdir(mode=0o770)
 snap=out/"source_snapshot";snap.mkdir(mode=0o770)
 sources=[Path(__file__),Path(v.__file__),Path(src.__file__),Path(src.tp.__file__),
   Path(src.tp.old.__file__),Path(src.tp.old.base.__file__),
   Path(src.tp.old.previous.__file__),Path(src.tp.old.entry90.__file__),
   Path(src.r29.__file__),Path(src.bull25.__file__),Path(src.bull25.entry.__file__)]
 for p in sources:
  target=snap/p.name;shutil.copyfile(p,target)
  manifest["source_snapshot_sha256"][p.name]=sha(target)
 ledger=out/"trade_ledger.jsonl"
 with ledger.open("x") as f:
  for z in trade_rows:f.write(json.dumps(z,sort_keys=True,separators=(",",":"))+"\n")
 manifest["ledger_sha256"]=sha(ledger)
 with (out/"result.json").open("x") as f:json.dump(manifest,f,indent=2,sort_keys=True);f.write("\n")
 print("RESEARCH_COMPLETE",json.dumps({"status":"QC_PASS","period":period,"report":str(out),
  "native_ticks":n,"cases":{k:{"closed":v["counts"].get("closed",0),
    "hard":v["counts"].get("close_HARD_STOP",0),
    "soft":v["counts"].get("close_SOFT_STOP",0),
    "net_tt":v["net_taker_taker_usdt"]} for k,v in report_cases.items()}},
   sort_keys=True),flush=True)

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument("--unit-only",action="store_true")
 parser.add_argument("--smoke-hours",type=int,default=0)
 parser.add_argument("--period",choices=("183d","29d"),default="183d")
 parser.add_argument("--both-periods",action="store_true")
 args=parser.parse_args()
 if (os.geteuid(),os.getegid())!=(107,109):
  raise PermissionError("approved cripta research actor only")
 if not (0<=args.smoke_hours<=12):raise ValueError("bounded real smoke max 12 hours")
 v=load_pinned()
 unit_tests(v)
 if args.unit_only:return
 for period in (("183d","29d") if args.both_periods else (args.period,)):
  replay(period,args.smoke_hours)

if __name__=="__main__":main()
