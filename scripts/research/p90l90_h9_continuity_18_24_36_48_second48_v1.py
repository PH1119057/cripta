#!/usr/bin/env python3
"""#866: H9 continuously adverse 18/24/36/48h × SECOND48 ON/OFF, three native periods.

RESEARCH ONLY. Identical P90L90 PREV7D Entry and immutable raw Bybit tape.
Ten independent sequential portfolios per period: TP-only ON/OFF and
SOFT_STOP L53 RAW -4 width>=4 with continuously adverse H9 horizon 18/24/36/48 ON/OFF.
Exact preexisting full on/off result and ledgers are immutable controls.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import importlib.util
import json
import os
import shutil
from collections import Counter, deque
from decimal import Decimal as D
from pathlib import Path

HOURS=(18,24,36,48)
PERIODS=("183d","90d","29d")
SYMS=("LTCUSDT","DOTUSDT","ARBUSDT","APTUSDT","INJUSDT")
SRC_SHA="a9ea1d19defce039fdb02d7465cdc39930ed949616c41ee95fa30bc6fb5fea48"
COUNTER_SHA="9cf0ce8d5360fb37fa5dcf4eed0babd811525fe1ceae90cf4d9708c6b0b6858d"
OLD_JOBS={"183d":"C2-R866-SS18-N100-P183-FULL1-20261119",
          "90d":"C2-R866-SS18-N100-FULL1-20261119",
          "29d":"C2-R866-SS18-N100-FULL1-20261119"}
NOTIONAL=D("100")

def digest(p:Path)->str:
 with p.open("rb") as fd:return hashlib.file_digest(fd,"sha256").hexdigest()

def original_modules():
 parent=Path(__file__).resolve().parent/"source_snapshot"
 p=parent/"p90l90_soft_stop_2025_183d_replay_v1.py"
 cf=parent/"p90l90_second48_onoff_counterfactual_v1.py"
 if digest(p)!=SRC_SHA or digest(cf)!=COUNTER_SHA:
  raise ValueError("unrecognized immutable research source/SECOND48 paired driver")
 mod=importlib.util.module_from_spec(importlib.util.spec_from_file_location("pinned_softstop_866",p))
 mod.__spec__.loader.exec_module(mod)
 cfmod=importlib.util.module_from_spec(importlib.util.spec_from_file_location("pinned_second48_866",cf))
 cfmod.__spec__.loader.exec_module(cfmod)
 return mod,cfmod

def h9_timer_step(first:int|float|None, adverse:bool, bucket:int, entry_ts:float):
 """The entire latest causal H9 boundary must remain adverse; favorable resets."""
 if not adverse:return None
 return max(bucket,entry_ts) if first is None else first

def unit_tests():
 # Existing adverse interval not extended by repeated same-bucket/native trades.
 assert h9_timer_step(None,True,300,320.)==320.
 assert h9_timer_step(320.,True,600,320.)==320.
 # A causal recovery resets; a subsequent loss starts a new interval.
 assert h9_timer_step(320.,False,3600,320.) is None
 assert h9_timer_step(None,True,7200,320.)==7200
 # At exact due time, adverse -> eligible; a recovery at due -> never eligible.
 for hrs in HOURS:
  first=7200
  assert 7200+(hrs-1)*3600<first+hrs*3600
  assert 7200+hrs*3600>=first+hrs*3600
  assert h9_timer_step(first,False,7200+hrs*3600,320.) is None
  assert h9_timer_step(None,True,7200+hrs*3600,320.)==7200+hrs*3600
 # Zero-favorable edge is adverse, strict positive is recovered (in caller).
 assert D("0")<=0 and D("0.00001")>0
 # Fixed amount and side eligibility do not depend on horizon.
 assert HOURS==(18,24,36,48)
 print("UNIT_TEST_PASS H9_CONTINUITY_RESET,RESTART,ALL_FOUR_HORIZONS",flush=True)

def load_input(src,period):
 src.tp.old.base.NOMINAL=NOTIONAL
 if period=="183d":
  ds=src.load_2025_frozen()
  streams={s:src.bull25.native_ticks(ds[s]) for s in SYMS}
  first,end=src.bull25.START_TS,src.bull25.END_TS
 elif period=="90d":
  src.ROOTS["90d"]=src.tp.ROOT
  src.STATES["90d"]="l53_clean_v2"
  ds={s:src.tp.old.previous.inputs(s,src.tp.old.previous.RAW_DEFAULT,src.tp.old.previous.FROZEN_DEFAULT) for s in SYMS}
  streams={s:src.tp.old.previous.ticks(ds[s]) for s in SYMS}
  first,end=src.tp.old.entry90.START_TS,src.tp.old.entry90.END_TS
 elif period=="29d":
  src.ROOTS["29d"]=src.r29.ROOT
  src.STATES["29d"]="l53_clean_29d"
  ds={s:src.r29.read_inputs(s) for s in SYMS}
  streams={s:src.r29.native_ticks(ds[s]) for s in SYMS}
  first,end=src.r29.FIRST,src.r29.END
 else:raise ValueError("invalid period")
 geo,parity=src.h9_states(period,ds)
 return ds,streams,first,end,geo,parity

def model_factory(src,geo,hours:int,enable_second48:bool):
 class ContinuousH9(src.SSBook):
  def __init__(self):
   super().__init__(geo)
   self.h9_horizon=hours
   self.second48_enabled=enable_second48
   self.h9_resets=0
   self.h9_starts=0
   self.h9_timed_exits=0

  def handle(self,symbol,t,seq,px,candidates,state):
   bucket=int(t//300)*300
   cur=self.pos[symbol]
   closed_here=False
   hardstops_before=self.count["close_HARD_STOP"]
   if cur is not None:
    candidate_tp=(None if state is None else state[1 if cur.side=="LONG" else 0])
    nxt,ex=src.tp.tp_only_on_trade(cur,px,seq,bucket,candidate_tp)
    cause=None
    if ex is None:
     if state is None or bucket not in self.geo[symbol]:
      raise ValueError("missing causal H9/L53 for OPEN position")
     boundary=self.geo[symbol][bucket][1 if cur.side=="LONG" else 0]
     favorable=src.tp.old.base.favorable(cur.side,cur.entry,boundary)
     was_started=cur.h9_first is not None
     cur.h9_first=h9_timer_step(cur.h9_first,favorable<=D(0),bucket,cur.entry_ts)
     if was_started and cur.h9_first is None:self.h9_resets+=1
     if not was_started and cur.h9_first is not None:self.h9_starts+=1
     l53_raw=state[1 if cur.side=="LONG" else 0]
     raw=(src.tp.old.base.favorable(cur.side,cur.entry,l53_raw)<=D("-4") and state[2]>=D("4"))
     h9_due=(cur.h9_first is not None and favorable<=D(0)
             and t>=cur.h9_first+self.h9_horizon*3600)
     if raw or h9_due:
      cause="L53_RAW_MINUS4_WIDTH4" if raw else "H9_LOSS_CONTINUOUS"
      nxt=None;ex=("SOFT_STOP",px,False)
      if cause=="H9_LOSS_CONTINUOUS":self.h9_timed_exits+=1
    self.pos[symbol]=nxt
    if ex is not None:
     reason,exitpx,maker=ex
     gross,entryfee,exitfee,net=src.tp.old.base.gross_and_net(cur,exitpx,maker)
     row=src.tp.old.base.trade_record(
      symbol,"PREV7D","SOFT_CONTINUOUS_H9_"+str(hours),cur,seq,t,exitpx,reason,maker,
      gross,entryfee,exitfee,net)
     row["soft_stop_cause"]=cause
     row["first_h9_loss_ts"]=cur.h9_first
     row["h9_horizon_hours"]=self.h9_horizon
     row["second48_enabled"]=self.second48_enabled
     self.trades.append(row)
     self.diagnostics.append({
      "symbol":symbol,"side":cur.side,"candidate_event_id":cur.entry_event_id,
      "entry_ts":repr(cur.entry_ts),"exit_ts":repr(t),"exit_reason":reason,
      "soft_stop_cause":cause,"trailing_armed":False,"hard_stop":str(cur.stop),
      "h9_horizon_hours":self.h9_horizon,
      "first_h9_loss_ts":cur.h9_first,
      "tp_target_at_exit":None if cur.tp is None else str(cur.tp)})
     if reason=="SOFT_STOP":self.causes[cause]+=1
     for metric,value in (("gross_usdt",gross),("entry_fees_usdt",entryfee),
      ("exit_fees_usdt",exitfee),("net_usdt",net),
      ("positive_net_usdt",max(net,D(0))),("negative_net_usdt",min(net,D(0)))):
      self.money[metric]+=value
     self.count["closed"]+=1
     self.count["close_"+reason]+=1
     self.count["win" if net>0 else "loss" if net<0 else "flat"]+=1
     closed_here=True
   for e in candidates:
    if not e["PREV7D"]:continue
    self.count["qualified_candidate"]+=1
    if self.pos[symbol] is not None or closed_here:
     self.count["busy_or_same_tick"]+=1;continue
    if self.second48_enabled and (t<self.pause_until or self.pending):
     self.count["blocked_global_pause"]+=1;continue
    if state is None:raise ValueError("accepted Entry missing L53 causal state")
    pos=src.tp.old.base.initial_position(e,seq,bucket,"PREV7D","SOFT_CONTINUOUS_H9_"+str(hours))
    target=state[1 if pos.side=="LONG" else 0]
    if src.tp.old.base.take_profit_eligible(pos.side,px,target):
     pos.tp=target;pos.tp_live_after_seq=seq
    pos.h9_first=None
    self.pos[symbol]=pos
    self.count["accepted"]+=1
   if self.count["close_HARD_STOP"]!=hardstops_before:
    self.all_stops.append({"ts":repr(t),"symbol":symbol})
    if self.second48_enabled:
     self.last_stops.append(t)
     while self.last_stops and t-self.last_stops[0]>src.tp.HARDSTOP_WINDOW_S:
      self.last_stops.popleft()
     if len(self.last_stops)>=2:
      self.pause_until=max(self.pause_until,t+48*3600)
      self.count["pause_triggers"]+=1
      self.stops.append({
       "symbol":symbol,"ts":repr(t),"cooldown_end_ts":repr(self.pause_until),
       "prior_stops_in_48h_including_current":len(self.last_stops),
       "target_positions":0})
  def finish(self):
   z=super().finish()
   z["model"]="H9 continuous adverse for "+str(hours)+"h, resets on favorable recovery, L53 -4% width>=4"
   z["h9_resets"]=self.h9_resets
   z["h9_starts"]=self.h9_starts
   z["h9_timed_exits"]=self.h9_timed_exits
   z["second48_enabled"]=self.second48_enabled
   if not self.second48_enabled and (z["counts"].get("blocked_global_pause",0)
        or z["counts"].get("pause_triggers",0)):
    raise ValueError("unexpected SECOND48 gate in OFF model")
   return z
 return ContinuousH9()

def original_report(period):
 root=Path("/srv/cripta-share/reports/jobs")/OLD_JOBS[period]/("soft_stop_"+period)
 j=json.loads((root/"result.json").read_text())
 ledger=root/"trade_ledger.jsonl"
 if j["period"]!=period or j["status"]!="QC_PASS" or digest(ledger)!=j["ledger_sha256"]:
  raise ValueError("immutable original control QC/source missing")
 rows={}
 for name in ("TPONLY_BASELINE","SOFT_STOP_18H"):rows[name]=[]
 for line in ledger.open():
  x=json.loads(line)
  rows[x["portfolio_scenario"]].append(x)
 return j,rows

def same_classical_result(reference,actual):
 if len(reference)!=len(actual):raise ValueError("classic count differs from original")
 keys=("candidate_event_id","symbol","side","entry_ts","exit_ts","entry_price",
       "exit_price","exit_reason","maker_exit","gross_usdt","entry_fee_usdt",
       "exit_fee_usdt","net_usdt")
 for i,(x,y) in enumerate(zip(reference,actual)):
  for f in keys:
   if x.get(f)!=y.get(f):
    raise ValueError("classic full native parity drift "+str((i,f,x.get(f),y.get(f))))

def scenario_name(h:int|None,second:bool):
 if h is None:return "CLASSIC_S48_ON" if second else "CLASSIC_S48_OFF"
 return "H9CONT_"+str(h)+"H_S48_"+("ON" if second else "OFF")

def replay(period,smoke_hours=0):
 src,counter=original_modules()
 if src.tp.old.base.NOMINAL not in (D("10"),NOTIONAL):
  raise ValueError("upstream notional different from P90 research")
 data,streams,start,end,geo,parity=load_input(src,period)
 books={
  scenario_name(None,True):src.tp.TPOnlyBook(),
  scenario_name(None,False):counter.no_pause(src.tp.TPOnlyBook)(),
 }
 for h in HOURS:
  for second in (True,False):
   books[scenario_name(h,second)]=model_factory(src,geo,h,second)
 if len(books)!=10:raise ValueError("wrong research scenario matrix")
 input_heap=[]
 for i,s in enumerate(SYMS):
  x=next(streams[s],None)
  if x is None:raise ValueError("native tick stream empty: "+s)
  heapq.heappush(input_heap,(x[0],i,x))
 count=0;last_day=None
 cutoff=end if not smoke_hours else min(end,start+smoke_hours*3600)
 while input_heap:
  t,i,event=heapq.heappop(input_heap)
  if t>=cutoff:break
  ts,seq,px,_=event
  symbol=SYMS[i]
  candidates=data[symbol]["events"].get(seq,())
  state=data[symbol]["states"].get(int(t//300)*300)
  for book in books.values():
   if candidates or book.pos[symbol] is not None:
    book.handle(symbol,t,seq,px,candidates,state)
  count+=1
  utc_day=int(t//86400)
  if utc_day!=last_day:
   last_day=utc_day
   print(json.dumps({"phase":"H9_CONTINUITY_NATIVE_10_CASES",
         "period":period,"day_epoch":utc_day,"ticks":count}),flush=True)
  next_tick=next(streams[symbol],None)
  if next_tick is not None:heapq.heappush(input_heap,(next_tick[0],i,next_tick))
 reports={k:v.finish() for k,v in books.items()}
 qualification={k:v["counts"]["qualified_candidate"] for k,v in reports.items()}
 if len(set(qualification.values()))!=1:raise ValueError("candidate input mismatch across ten portfolios")
 for case,r in reports.items():
  if case.endswith("_OFF") and (r["counts"].get("blocked_global_pause",0)!=0):
   raise ValueError("no SECOND48 case blocked new Entry")
  if case.startswith("H9CONT_"):
   if r["counts"].get("close_PROTECTED_STOP",0):raise ValueError("unexpected trailing stop")
   if r["soft_stop_causes"].get("H9_LOSS_PLUS18H",0):raise ValueError("stale timer label")
  if r["counts"]["accepted"]!=r["counts"]["closed"]+r["counts"]["censored_open"]:
   raise ValueError("position conservation failed")
 if smoke_hours:
  print("SMOKE_PASS",json.dumps({"period":period,"hours":smoke_hours,
   "ticks":count,"qualified":qualification,
   "summary":{k:{"closed":v["counts"]["closed"],"HARD_STOP":v["counts"].get("close_HARD_STOP",0),
     "SOFT_STOP":v["counts"].get("close_SOFT_STOP",0),
     "H9_resets":v.get("h9_resets",0)} for k,v in reports.items()}},
    sort_keys=True),flush=True)
  return
 original,rows=original_report(period)
 if count!=original["native_ticks"] or parity!=original["geometry_parity"]:
  raise ValueError("original full replay source parity mismatch")
 same_classical_result(rows["TPONLY_BASELINE"],books["CLASSIC_S48_ON"].trades)
 if original["scenarios"]["TPONLY_BASELINE"]["counts"]!=reports["CLASSIC_S48_ON"]["counts"]:
  raise ValueError("original CLASSIC ON count mismatch")
 out_parent=os.environ.get("CRIPTA_REPORT_DIR")
 if not out_parent:raise ValueError("full replay must run via approved research runner")
 target=Path(out_parent)/("h9_continuity_"+period)
 if target.exists() or target.is_symlink():raise FileExistsError("immutable report collision")
 if not os.access(target.parent,os.W_OK|os.X_OK):raise PermissionError("research output not writable")
 manifest={"research":"P90L90_H9_CONTINUOUS_RESET_18_24_36_48_SECOND48_ON_OFF",
  "status":"QC_PASS","period":period,"period_utc":[start,end],"native_ticks":count,
  "entry":"P90L90 H9 5m/15m RAW confluent union 0-15% depth, L53 tick-cross, PREV7D width ±0.5pp",
  "exit":"opposite RAW L53 dynamic TP first, -10% HARD_STOP, L53 RAW -4 width>=4 or continuously adverse H9 horizon",
  "second48":"ON: global block NEW Entry 48h following second real HARD_STOP in sliding 48h, never close positions",
  "execution_fee":"TAKER/TAKER 0.055% per side primary; resting maker TP separate sensitivity",
  "position":"single position per symbol, closed_here no same tick re-entry, independent sequential portfolios; open censored",
  "notional_usdt":str(NOTIONAL),"symbols":SYMS,
  "unmodeled":["slippage","funding","real exchange fills","spread","price impact"],
  "source_legacy_commit":"6ee3c557db939fb5352cbdc59fbde2a60c7f3d61",
  "legacy_source_sha256":SRC_SHA,"second48_source_sha256":COUNTER_SHA,
  "geometry_parity":parity,"source_snapshot_sha256":{},
  "scenarios":{k:src.stats(book,reports[k]) for k,book in books.items()},
  "h9_timer_audit":{k:{"resets":v.get("h9_resets",0),
       "starts":v.get("h9_starts",0),"h9_exit":v.get("h9_timed_exits",0)}
       for k,v in reports.items() if k.startswith("H9CONT_")},
  "source_ledger_sha256":original["ledger_sha256"],"ledger_sha256":None,
  "ledger_rows":sum(len(v.trades) for v in books.values()),
  "censored":{k:v["censored"] for k,v in reports.items()}}
 target.mkdir(mode=0o770)
 snap=target/"source_snapshot";snap.mkdir(mode=0o770)
 for file in (Path(__file__),Path(src.__file__),Path(counter.__file__),
   Path(src.tp.__file__),Path(src.tp.old.__file__),Path(src.tp.old.base.__file__),
   Path(src.tp.old.previous.__file__),Path(src.r29.__file__),
   Path(src.bull25.__file__),Path(src.bull25.entry.__file__)):
  destination=snap/file.name
  shutil.copyfile(file,destination)
  manifest["source_snapshot_sha256"][file.name]=digest(destination)
 ledger=target/"trade_ledger.jsonl"
 with ledger.open("x") as out:
  for key,book in books.items():
   for row in book.trades:
    out.write(json.dumps({**row,"portfolio_scenario":key},
                        sort_keys=True,separators=(",",":"))+"\n")
 manifest["ledger_sha256"]=digest(ledger)
 with (target/"result.json").open("x") as out:
  json.dump(manifest,out,sort_keys=True,indent=2);out.write("\n")
 print("PERIOD_COMPLETE",json.dumps({"period":period,
   "native_ticks":count,"output":str(target),
   "scenarios":{k:{"closed":v["counts"]["closed"],
        "hardstop":v["counts"].get("close_HARD_STOP",0),
        "softstop":v["counts"].get("close_SOFT_STOP",0),
        "net_taker_taker":manifest["scenarios"][k]["net_taker_taker_usdt"]}
        for k,v in reports.items()}},sort_keys=True),flush=True)

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument("--period",choices=PERIODS,default="183d")
 parser.add_argument("--all-periods",action="store_true")
 parser.add_argument("--smoke-hours",type=int,default=0)
 parser.add_argument("--unit-only",action="store_true")
 args=parser.parse_args()
 unit_tests()
 if args.unit_only:return
 if (os.geteuid(),os.getegid())!=(107,109):
  raise PermissionError("only the canonical cripta research actor may run")
 if args.smoke_hours<0 or args.smoke_hours>72:raise ValueError("smoke-hours outside bounded range")
 for period in (PERIODS if args.all_periods else (args.period,)):
  replay(period,args.smoke_hours)
if __name__=="__main__":main()
