#!/usr/bin/env python3
"""#866 HS-depth sweep derivative, ONLY hardstop pct is variable; L5-3 4/4 unchanged.

Original source below retained byte-identically except model_factory hardstop_pct injection.

Research-only sequential full native publicTrade replay, three 183d/90d/29d
periods with four independent portfolios (classic TP-only and L53-only,
each with SECOND48 ON/OFF). P90L90 Entry H9/PREV7D stays identical.
Native tick, closed-state and old 2025/2026 driver SHA pinned; no LIVE.
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

CASES=("CLASSIC_S48_ON","CLASSIC_S48_OFF","L53_ONLY_S48_ON","L53_ONLY_S48_OFF")
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

def unit_tests():
 src,_=original_modules()
 src.tp.old.base.NOMINAL=NOTIONAL
 sym="LTCUSDT"
 t0=1750000000//300*300
 passed=0
 for pause in (True,False):
  for side in ("LONG","SHORT"):
   for width in (D("3.9999"),D("4")):
    for adverse in (D("3.9999"),D("4"),D("4.1")):
     book=model_factory(src,pause)
     ev={"side":side,"price":"100","ts":str(t0),
         "event_id":"synthetic","trade_id":"synthetic"}
     book.pos[sym]=src.tp.old.base.initial_position(ev,1,t0,"PREV7D","L53_ONLY")
     # LONG adverse upper boundary below Entry; SHORT adverse lower above Entry.
     l53=(D("100")-adverse if side=="LONG" else D("100")+adverse)
     state=(D("92") if side=="LONG" else l53,
            l53 if side=="LONG" else D("109"),width)
     book.handle(sym,float(t0+60),2,D("100"),(),state)
     should_close=(width>=D("4") and adverse>=D("4"))
     actual=book.pos[sym] is None
     if should_close!=actual:raise AssertionError((pause,side,width,adverse,actual))
     if should_close:
      assert len(book.trades)==1
      assert book.trades[0]["exit_reason"]=="SOFT_STOP"
      assert book.trades[0]["soft_stop_cause"]=="L53_RAW_MINUS4_WIDTH4"
     passed+=1
  # HardStop wins precedence even when L53 adverse/width condition is true.
  book=model_factory(src,pause)
  ev={"side":"LONG","price":"100","ts":str(t0),
      "event_id":"hs","trade_id":"hs"}
  book.pos[sym]=src.tp.old.base.initial_position(ev,1,t0,"PREV7D","L53_ONLY")
  book.handle(sym,float(t0+60),2,D("89"),(),(D("90"),D("95"),D("5")))
  assert book.trades[-1]["exit_reason"]=="HARD_STOP"
  assert book.trades[-1]["soft_stop_cause"] is None
  passed+=1
 print("UNIT_TEST_PASS",passed,"L53_LONG_SHORT_WIDTH_ADVERSE_SECOND48_AND_STOP_PRECEDENCE",flush=True)

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

def model_factory(src,enable_second48:bool,hardstop_pct:D=D("10")):
 if hardstop_pct not in tuple(D(i) for i in range(2,11)):
  raise ValueError("Hard Stop must be 2..10 whole percentages")
 class L53Only(src.SSBook):
  def __init__(self):
   # The H9 geometry is deliberately NOT read by this Exit policy.
   super().__init__({})
   self.second48_enabled=enable_second48

  def handle(self,symbol,t,seq,px,candidates,state):
   bucket=int(t//300)*300
   cur=self.pos[symbol]
   closed_here=False
   before=self.count["close_HARD_STOP"]
   if cur is not None:
    candidate_tp=None if state is None else state[1 if cur.side=="LONG" else 0]
    nxt,ex=src.tp.tp_only_on_trade(cur,px,seq,bucket,candidate_tp)
    cause=None
    if ex is None:
     if state is None:raise ValueError("L53 missing causal closed 5m state in OPEN position")
     l53_raw=state[1 if cur.side=="LONG" else 0]
     raw=(src.tp.old.base.favorable(cur.side,cur.entry,l53_raw)<=D("-4")
          and state[2]>=D("4"))
     if raw:
      cause="L53_RAW_MINUS4_WIDTH4"
      nxt=None;ex=("SOFT_STOP",px,False)
    self.pos[symbol]=nxt
    if ex is not None:
     reason,exitpx,maker=ex
     gross,entryfee,exitfee,net=src.tp.old.base.gross_and_net(cur,exitpx,maker)
     row=src.tp.old.base.trade_record(symbol,"PREV7D","L53_ONLY_M4W4",
      cur,seq,t,exitpx,reason,maker,gross,entryfee,exitfee,net)
     row["soft_stop_cause"]=cause
     row["h9_exit_enabled"]=False
     row["second48_enabled"]=self.second48_enabled
     self.trades.append(row)
     self.diagnostics.append({"symbol":symbol,"side":cur.side,
      "candidate_event_id":cur.entry_event_id,"entry_ts":repr(cur.entry_ts),
      "exit_ts":repr(t),"exit_reason":reason,"soft_stop_cause":cause,
      "trailing_armed":False,"hard_stop":str(cur.stop),
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
    pos=src.tp.old.base.initial_position(e,seq,bucket,"PREV7D","L53_ONLY_M4W4")
    pos.stop=src.tp.old.base.threshold_price(pos.side,pos.entry,-hardstop_pct)
    target=state[1 if pos.side=="LONG" else 0]
    if src.tp.old.base.take_profit_eligible(pos.side,px,target):
     pos.tp=target;pos.tp_live_after_seq=seq
    self.pos[symbol]=pos
    self.count["accepted"]+=1
   if self.count["close_HARD_STOP"]!=before:
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
   z["model"]="L53-only opposite RAW adverse >=4pp from Entry AND working width>=4%; no H9 Exit or timer; TP+HardStop first"
   z["second48_enabled"]=self.second48_enabled
   if z["soft_stop_causes"].get("H9_LOSS_PLUS18H",0):
    raise ValueError("H9 timer unexpectedly in L53-only")
   if not self.second48_enabled and (z["counts"].get("blocked_global_pause",0)
      or z["counts"].get("pause_triggers",0)):
    raise ValueError("SECOND48 mutated OFF portfolio")
   return z
 return L53Only()

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

def scenario_name(l53:bool,second:bool)->str:
 return ("L53_ONLY" if l53 else "CLASSIC")+"_S48_"+("ON" if second else "OFF")

def replay(period:str,smoke_hours:int=0):
 src,counter=original_modules()
 if src.tp.old.base.NOMINAL not in (D("10"),NOTIONAL):
  raise ValueError("unexpected pinned research notional")
 data,streams,start,end,geo,geometry_parity=load_input(src,period)
 del geo  # H9 geometry is NOT supplied to L53-only Exit; still validates source provenance.
 books={
  "CLASSIC_S48_ON":src.tp.TPOnlyBook(),
  "CLASSIC_S48_OFF":counter.no_pause(src.tp.TPOnlyBook)(),
  "L53_ONLY_S48_ON":model_factory(src,True),
  "L53_ONLY_S48_OFF":model_factory(src,False)}
 assert set(books)==set(CASES)
 heap=[]
 for idx,s in enumerate(SYMS):
  first=next(streams[s],None)
  if first is None:raise ValueError("empty frozen native source "+s)
  heapq.heappush(heap,(first[0],idx,first))
 count=0;last_day=None
 cutoff=end if not smoke_hours else min(end,start+smoke_hours*3600)
 while heap:
  t,idx,event=heapq.heappop(heap)
  if t>=cutoff:break
  _,seq,px,_=event
  symbol=SYMS[idx]
  candidates=data[symbol]["events"].get(seq,())
  state=data[symbol]["states"].get(int(t//300)*300)
  for book in books.values():
   if candidates or book.pos[symbol] is not None:
    book.handle(symbol,t,seq,px,candidates,state)
  count+=1
  day=int(t//86400)
  if day!=last_day:
   last_day=day
   print(json.dumps({"phase":"L53_ONLY_NATIVE_4_CASES",
    "period":period,"utc_day_epoch":day,"ticks":count}),flush=True)
  next_tick=next(streams[symbol],None)
  if next_tick is not None:heapq.heappush(heap,(next_tick[0],idx,next_tick))
 reports={k:b.finish() for k,b in books.items()}
 qualified={k:r["counts"].get("qualified_candidate",0) for k,r in reports.items()}
 if len(set(qualified.values()))!=1:
  raise ValueError("candidate population differs between portfolios")
 for case,r in reports.items():
  counts=r["counts"]
  if counts.get("accepted",0)!=counts.get("closed",0)+counts.get("censored_open",0):
   raise ValueError("position conservation "+case)
  if case.endswith("_OFF") and counts.get("blocked_global_pause",0):
   raise ValueError("SECOND48 OFF blocked Entry "+case)
  if case.startswith("L53_ONLY"):
   if counts.get("close_PROTECTED_STOP",0):raise ValueError("unexpected trailing stop")
   if set(r["soft_stop_causes"])-{"L53_RAW_MINUS4_WIDTH4"}:
    raise ValueError("unapproved SoftStop cause "+case)
  if count<1:raise ValueError("zero native ticks")
 if smoke_hours:
  print("SMOKE_PASS",json.dumps({"period":period,"hours":smoke_hours,
   "ticks":count,"qualified":qualified,
   "cases":{k:{"accepted":v["counts"].get("accepted",0),"closed":v["counts"].get("closed",0),
      "hardstop":v["counts"].get("close_HARD_STOP",0),
      "softstop":v["counts"].get("close_SOFT_STOP",0)} for k,v in reports.items()}},
   sort_keys=True),flush=True)
  return
 original,rows=original_report(period)
 if (count!=original["native_ticks"] or geometry_parity!=original["geometry_parity"]):
  raise ValueError("H9/Entry source version or frozen raw tick parity drift")
 same_classical_result(rows["TPONLY_BASELINE"],books["CLASSIC_S48_ON"].trades)
 if original["scenarios"]["TPONLY_BASELINE"]["counts"]!=reports["CLASSIC_S48_ON"]["counts"]:
  raise ValueError("native classic ON count parity differs")
 if period=="183d":
  # Additional independent 2025 classic SECOND48 OFF full ledger parity.
  reference=Path("/srv/cripta-share/reports/jobs/C2-R866-S48-183D-FULL1-20261120/second48_counterfactual_183d")
  j=json.loads((reference/"result.json").read_text())
  q=reference/"trade_ledger.jsonl"
  if j["status"]!="QC_PASS" or digest(q)!=j["ledger_sha256"]:
   raise ValueError("2025 independent second48 OFF reference corrupted")
  rr=[json.loads(line) for line in q.open() if json.loads(line)["portfolio_scenario"]=="TPONLY_NO_SECOND48"]
  same_classical_result(rr,books["CLASSIC_S48_OFF"].trades)
 output_root=os.environ.get("CRIPTA_REPORT_DIR")
 if not output_root:raise ValueError("native full replay requires approved runner report path")
 target=Path(output_root)/("l53_only_"+period)
 if target.exists() or target.is_symlink():raise FileExistsError("immutable research result already exists")
 if not os.access(target.parent,os.W_OK|os.X_OK):raise PermissionError("research output parent blocked")
 manifest={
  "status":"QC_PASS","research":"P90L90_L53_ONLY_SOFTSTOP_M4W4_4_CASES_ON_OFF",
  "period":period,"period_utc":[start,end],"native_ticks":count,
  "entry":"P90L90 5m/15m H9 EXACT RAW SIDE EQUALITY, RAW UNION depth 0-15%; L53 RAW first ordered native crossing; PREV7D 7 previous fully closed UTC days width median +/-0.5pp",
  "exit":"first-event existing HARD_STOP -10%, resting opposite RAW L53 TP, marketable opposite L53 TP; only additional SOFT_STOP on L53 opposite RAW >=4pp adverse from Entry with current working width>=4%; no H9 timer/trigger",
  "second48":"ON second actual HARD_STOP rolling 48h blocks NEW Entry for 48h across five symbols; OFF no block; does not close open positions",
  "exec_cost":"100 USDT nominal, TAKER/TAKER 0.055% per side primary; resting maker TP sensitivity separately in money",
  "position":"one open position per symbol; same-tick re-Entry ignored; future candidate while occupied ignored; terminal open censored; no flip/hedge",
  "filters_flags":{"PREV7D":True,"H9_Entry":True,"H9_Exit":False,
   "L53_only_m4_width4":True,"TRAIL":False,"BE":False,"STAY":False,
   "OI":False,"cooldown_other_than_SECOND48":False},
  "symbols":SYMS,"notional_usdt":str(NOTIONAL),
  "unmodeled":["funding","slippage","spread","market impact","real Bybit fills"],
  "geometry_parity":geometry_parity,
  "legacy_source_sha256":SRC_SHA,"second48_source_sha256":COUNTER_SHA,
  "original_reference_ledger_sha256":original["ledger_sha256"],
  "scenarios":{k:src.stats(book,reports[k]) for k,book in books.items()},
  "censored":{k:v["censored"] for k,v in reports.items()},
  "source_snapshot_sha256":{},"ledger_sha256":None,
  "ledger_rows":sum(len(b.trades) for b in books.values())}
 target.mkdir(mode=0o770)
 snap=target/"source_snapshot";snap.mkdir(mode=0o770)
 for file in (Path(__file__),Path(src.__file__),Path(counter.__file__),
   Path(src.tp.__file__),Path(src.tp.old.__file__),Path(src.tp.old.base.__file__),
   Path(src.tp.old.previous.__file__),Path(src.r29.__file__),
   Path(src.bull25.__file__),Path(src.bull25.entry.__file__)):
  dest=snap/file.name;shutil.copyfile(file,dest)
  manifest["source_snapshot_sha256"][file.name]=digest(dest)
 ledger=target/"trade_ledger.jsonl"
 with ledger.open("x") as out:
  for case,book in books.items():
   for row in book.trades:
    out.write(json.dumps({**row,"portfolio_scenario":case},
      sort_keys=True,separators=(",",":"))+"\n")
 manifest["ledger_sha256"]=digest(ledger)
 with (target/"result.json").open("x") as out:
  json.dump(manifest,out,indent=2,sort_keys=True);out.write("\n")
 print("PERIOD_COMPLETE",json.dumps({"period":period,"ticks":count,
  "report_path":str(target),"scenarios":{
   k:{"closed":v["counts"].get("closed",0),"soft":v["counts"].get("close_SOFT_STOP",0),
      "hard":v["counts"].get("close_HARD_STOP",0),
      "net_tt":manifest["scenarios"][k]["net_taker_taker_usdt"]}
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
