#!/usr/bin/env python3
"""Issue #866: full native LONG/SHORT initial Hard Stop sweep -10 to -2%, three periods.

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
STOPS=tuple(range(10,1,-1))
SIDES=("LONG","SHORT")
FLAGS=("ON","OFF")
CASES=tuple(f"{side}_S48_{flag}_HS{depth}" for depth in STOPS for side in SIDES for flag in FLAGS)
CONTROLS=()
PARENT="p90l90_l53_only_m4w4_second48_hsdepth_v1.py"
PARENT_SHA="a03dd4125156ba8b38c2af603f9df3991ae0206f57a1328de5f920a3f04e036b"
REFERENCE_JOB="C2-R866-L53ONLY-3P-S48-V3-20261120"
SIDE_REFERENCES={"183d":"C2-R866-L53-LS-S48-183D-29D-20261120","90d":"C2-R866-L53-LS-S48-90D-PREP-20261120","29d":"C2-R866-L53-LS-S48-183D-29D-20261120"}
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


def case(side,flag,depth):
 return f"{side}_S48_{flag}_HS{depth}"

def side_reference(period):
 root=Path("/srv/cripta-share/reports/jobs")/SIDE_REFERENCES[period]/("l53_longshort_"+period)
 j=json.loads((root/"result.json").read_text())
 ledger=root/"trade_ledger.jsonl"
 if j["period"]!=period or j["status"]!="QC_PASS" or sha(ledger)!=j["ledger_sha256"]:
  raise ValueError("immutable full native side reference QC fail "+period)
 rows={f"{side}_S48_{flag}":[] for side in SIDES for flag in FLAGS}
 for line in ledger.open():
  x=json.loads(line);k=x["portfolio_scenario"]
  if k in rows:rows[k].append(x)
 for k,v in rows.items():
  if len(v)!=j["scenarios"][k]["counts"]["closed"]:raise ValueError("side baseline count drift")
 return j,rows

def stop_unit_tests(v):
 src,_=v.original_modules();src.tp.old.base.NOMINAL=NOTIONAL
 at=1750000000//300*300
 for depth in STOPS:
  for side in SIDES:
   for flag in FLAGS:
    enabled=flag=="ON";book=v.model_factory(src,enabled,D(depth))
    e={"side":side,"price":"100","ts":str(at),"event_id":side,
       "trade_id":"synthetic","PREV7D":True}
    state=(D("95"),D("105"),D("2"))
    book.handle("LTCUSDT",float(at+30),1,D(100),(e,),state)
    pos=book.pos["LTCUSDT"]
    expected=src.tp.old.base.threshold_price(side,D(100),-D(depth))
    if pos is None or pos.stop!=expected:raise ValueError(("initial stop wrong",side,depth,expected))
    near=expected+D("0.01") if side=="LONG" else expected-D("0.01")
    book.handle("LTCUSDT",float(at+31),2,near,(),state)
    if book.count.get("close_HARD_STOP",0):raise ValueError("pre-stop trigger")
    book.handle("LTCUSDT",float(at+32),3,expected,(),state)
    if len(book.trades)!=1 or book.trades[0]["exit_reason"]!="HARD_STOP":
     raise ValueError(("HS priority wrong",side,depth))
    if book.count.get("close_SOFT_STOP",0):raise ValueError("unexpected Soft Stop")
 print("STOP_LEVEL_UNIT_PASS",json.dumps({"nine_depths":list(STOPS),
 "sides":SIDES,"second48_modes":FLAGS,"threshold_boundary_exact":True}),flush=True)

def replay(period:str,smoke_hours:int):
 v=load_pinned()
 src,_=v.original_modules()
 data,streams,start,end,geometry,geometry_parity=v.load_input(src,period)
 del geometry
 mixed,_=reference(period)
 baseline,refrows=side_reference(period)
 if set(data)!=set(SYMS) or len(streams)!=5:raise ValueError("universe mismatch")
 books={case(side,flag,depth):v.model_factory(src,flag=="ON",D(depth))
        for depth in STOPS for side in SIDES for flag in FLAGS}
 assert set(books)==set(CASES)
 lg=[(k,b) for k,b in books.items() if k.startswith("LONG")]
 sh=[(k,b) for k,b in books.items() if k.startswith("SHORT")]
 heap=[]
 for i,sym in enumerate(SYMS):
  first=next(streams[sym],None)
  if first is None:raise ValueError("empty native stream "+sym)
  heapq.heappush(heap,(first[0],i,first))
 total=0;day=None
 cutoff=min(end,start+smoke_hours*3600) if smoke_hours else end
 while heap:
  t,idx,e=heapq.heappop(heap)
  if t>=cutoff:break
  _,seq,px,_=e;sym=SYMS[idx]
  candidates=data[sym]["events"].get(seq,())
  le=direction_candidates(candidates,"LONG")
  se=direction_candidates(candidates,"SHORT")
  state=data[sym]["states"].get(int(t//300)*300)
  for name,book in lg:
   if le or book.pos[sym] is not None:book.handle(sym,t,seq,px,le,state)
  for name,book in sh:
   if se or book.pos[sym] is not None:book.handle(sym,t,seq,px,se,state)
  total+=1
  today=int(t//86400)
  if today!=day:
   day=today
   print("TICK_PROGRESS",json.dumps({"period":period,"day_epoch":day,"ticks":total,
      "expected_native_ticks":baseline["native_ticks"],"cases":len(books)}),flush=True)
  nxt=next(streams[sym],None)
  if nxt is not None:heapq.heappush(heap,(nxt[0],idx,nxt))
 reports={k:b.finish() for k,b in books.items()}
 if total<=0:raise ValueError("empty replay")
 for name,r in reports.items():
  c=r["counts"]
  if c.get("accepted",0)!=c.get("closed",0)+c.get("censored_open",0):
   raise ValueError("position conservation "+name)
  if "_S48_OFF_" in name and (c.get("blocked_global_pause",0) or c.get("pause_triggers",0)):
   raise ValueError("SECOND48 OFF mutated "+name)
  if c.get("close_PROTECTED_STOP",0) or set(r["soft_stop_causes"])-{"L53_RAW_MINUS4_WIDTH4"}:
   raise ValueError("Exit contract drift "+name)
  if name.startswith("LONG") and any(x["side"]!="LONG" for x in books[name].trades):
   raise ValueError("LONG contains SHORT")
  if name.startswith("SHORT") and any(x["side"]!="SHORT" for x in books[name].trades):
   raise ValueError("SHORT contains LONG")
  if len(books[name].all_stops)!=c.get("close_HARD_STOP",0):
   raise ValueError("Hard Stop sequence mismatch "+name)
 for depth in STOPS:
  for flag in FLAGS:
   l=reports[case("LONG",flag,depth)]["counts"].get("qualified_candidate",0)
   r=reports[case("SHORT",flag,depth)]["counts"].get("qualified_candidate",0)
   baseline_n=(reports[case("LONG","ON",10)]["counts"].get("qualified_candidate",0)
                +reports[case("SHORT","ON",10)]["counts"].get("qualified_candidate",0))
   if l+r!=baseline_n:raise ValueError("candidate event stream changed")
 if smoke_hours:
  print("SMOKE_PASS",json.dumps({"period":period,"hours":smoke_hours,
   "native_ticks":total,"cases":len(books),
   "accepted_HS10":{k:v["counts"].get("accepted",0) for k,v in reports.items() if k.endswith("HS10")},
   "accepted_HS2":{k:v["counts"].get("accepted",0) for k,v in reports.items() if k.endswith("HS2")}},
   sort_keys=True),flush=True)
  return
 if total!=baseline["native_ticks"] or total!=mixed["native_ticks"] or geometry_parity!=baseline["geometry_parity"] or geometry_parity!=mixed["geometry_parity"]:
  raise ValueError("native trade / geometry coverage parity fail")
 for side in SIDES:
  for flag in FLAGS:
   name=f"{side}_S48_{flag}"
   k=case(side,flag,10)
   v.same_classical_result(refrows[name],books[k].trades)
   if reports[k]["counts"]!=baseline["scenarios"][name]["counts"]:
    raise ValueError("HS10 exact count baseline drift "+name)
   if normalized_net_taker(books[k].trades)!=D(baseline["scenarios"][name]["net_taker_taker_usdt"]):
    raise ValueError("HS10 exact fee/PnL drift "+name)
 target_root=os.environ.get("CRIPTA_REPORT_DIR")
 if not target_root:raise ValueError("missing approved runner report directory")
 target=Path(target_root)/("hs_depth_"+period)
 if target.exists() or target.is_symlink() or not os.access(target.parent,os.W_OK|os.X_OK):
  raise PermissionError("immutable HS sweep result collision or no access")
 scenarios={};ledger_rows=[]
 for name in CASES:
  book=books[name];r=reports[name];z=r["counts"]
  by_symbol={}
  for sym in SYMS:
   selected=[x for x in book.trades if x["symbol"]==sym]
   c=Counter(x["exit_reason"] for x in selected)
   by_symbol[sym]={"closed":len(selected),"hard_stop":c["HARD_STOP"],
    "soft_stop":c["SOFT_STOP"],"tp":c["RESTING_TP"]+c["MARKETABLE_TP"],
    "net_taker_taker_usdt":str(normalized_net_taker(selected))}
  stats=src.stats(book,r)
  if normalized_net_taker(book.trades)!=D(stats["net_taker_taker_usdt"]):
   raise ValueError("fee/PnL rounding mismatch "+name)
  scenarios[name]={**stats,"hardstop_depth_pct":int(name.rsplit("HS",1)[1]),
   "by_symbol":by_symbol,"pause_triggers":z.get("pause_triggers",0),
   "blocked_candidates":z.get("blocked_global_pause",0)}
  ledger_rows.extend({**x,"portfolio_scenario":name} for x in book.trades)
 report={"research":"P90L90_L53_LONGSHORT_HS10_TO_HS2_SWEEP_V1","status":"QC_PASS",
 "period":period,"period_utc":[start,end],"symbols":SYMS,
 "native_ticks":total,"hardstop_levels_pct":STOPS,
 "entry":baseline["entry"],
 "exit":"Unchanged dynamic opposite RAW L53 TP; Soft Stop L53 RAW adverse 4pp with working width >=4pp; only initial hard stop moves -10,-9,-8,-7,-6,-5,-4,-3,-2 percent. Original native first-event priority; no BE/Trail/H9 Exit.",
 "position":baseline["position"],"second48":baseline["second48"],
 "filters":baseline["filters"],"cost":baseline["cost"],
 "analysis":"Each depth/side/SECOND48 flag separately recalculates occupancy, events and second-HS 48h pause. Research only.",
 "scenarios":scenarios,"source_snapshot_sha256":{},"base_driver_sha256":PARENT_SHA,
 "legacy_source_sha256":v.SRC_SHA,"geometry_parity":geometry_parity,
 "reference_job":SIDE_REFERENCES[period],"reference_sha256":baseline["ledger_sha256"],
 "baseline_hs10_exact_ledger_parity":"PASS_4_CASES","ledger_rows":len(ledger_rows),
 "ledger_sha256":None,"not_pristine_blinded_oos":True,
 "unmodeled":["funding","spread","slippage","market impact","liquidations","real exchange fills"]}
 target.mkdir(mode=0o770)
 snap=target/"source_snapshot";snap.mkdir(mode=0o770)
 sources=[Path(__file__),Path(v.__file__),Path(src.__file__),Path(src.tp.__file__),
  Path(src.tp.old.__file__),Path(src.tp.old.base.__file__),
  Path(src.tp.old.previous.__file__),Path(src.tp.old.entry90.__file__),
  Path(src.r29.__file__),Path(src.bull25.__file__),Path(src.bull25.entry.__file__)]
 for p in sources:
  dst=snap/p.name;shutil.copyfile(p,dst)
  report["source_snapshot_sha256"][p.name]=sha(dst)
 ledger=target/"trade_ledger.jsonl"
 with ledger.open("x") as f:
  for x in ledger_rows:f.write(json.dumps(x,sort_keys=True,separators=(",",":"))+"\n")
 report["ledger_sha256"]=sha(ledger)
 with (target/"result.json").open("x") as f:
  json.dump(report,f,indent=2,sort_keys=True);f.write("\n")
 print("RESEARCH_COMPLETE",json.dumps({"status":"QC_PASS","period":period,
  "ticks":total,"path":str(target),"rows":len(ledger_rows),
  "HS10_baseline":"FULL_LEDGER_PARITY",
  "cases":{name:{"closed":a["counts"].get("closed",0),
   "hard":a["counts"].get("close_HARD_STOP",0),
   "soft":a["counts"].get("close_SOFT_STOP",0),
   "net_after_fees":a["net_taker_taker_usdt"]} for name,a in scenarios.items()}},
  sort_keys=True),flush=True)

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument("--period",choices=("183d","90d","29d"),required=True)
 parser.add_argument("--smoke-hours",type=int,default=0)
 parser.add_argument("--unit-only",action="store_true")
 a=parser.parse_args()
 if (os.geteuid(),os.getegid())!=(107,109):raise PermissionError("research actor cripta required")
 if a.smoke_hours not in (0,1,2,4,8,12):raise ValueError("bounded smoke only")
 v=load_pinned();unit_tests(v);stop_unit_tests(v)
 if not a.unit_only:replay(a.period,a.smoke_hours)

if __name__=="__main__":main()
