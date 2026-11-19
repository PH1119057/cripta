#!/usr/bin/env python3
"""Research only: P90L90 sequential full native tick portfolio SOFT_STOP 90d then 29d."""
from __future__ import annotations
import argparse,csv,gzip,hashlib,heapq,json,os,shutil
from collections import Counter,deque,defaultdict
from decimal import Decimal as D, ROUND_HALF_EVEN
from pathlib import Path
from scripts.research import p90l90_tp_only_second48_v1 as tp
from scripts.research import p90l90_29d_exit_replay_v1 as r29

SYMS=tuple(tp.SYMBOLS)
ROOTS={"90d":tp.ROOT,"29d":r29.ROOT}
STATES={"90d":"l53_clean_v2","29d":"l53_clean_29d"}
OUT="p90l90_soft_stop_h9_18h_l53_m4w4_replay_20261119_v1"
Q=D("0.000000000001")
NOMINAL=D("100")
REFERENCE_NOMINAL=D("10")
TAKER=D("0.00055")
MAKER=D("0.0002")

def recomputed_money(row, nominal):
 ep=D(row["entry_price"]);xp=D(row["exit_price"])
 gross=(nominal*(xp/ep-1)*(1 if row["side"]=="LONG" else -1)).quantize(Q,rounding=ROUND_HALF_EVEN)
 entry_fee=(nominal*TAKER).quantize(Q,rounding=ROUND_HALF_EVEN)
 exit_fee=(nominal*xp/ep*(MAKER if row["maker_exit"] else TAKER)).quantize(Q,rounding=ROUND_HALF_EVEN)
 return dict(gross_usdt=gross,entry_fee_usdt=entry_fee,exit_fee_usdt=exit_fee,
             net_usdt=gross-entry_fee-exit_fee)

def digest(p):
 with Path(p).open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()
def h9_states(period,ds):
 geo={};checks={}
 for sym in SYMS:
  path=ROOTS[period]/"results"/STATES[period]/"parts"/("states_"+sym+".csv.gz")
  if digest(path)!=ds[sym]["manifest"]["frozen_l53_sha256"]:raise ValueError("frozen L53 SHA mismatch "+sym)
  hi,lo,fs=deque(),deque(),{};i=0
  with gzip.open(path,"rt") as f:
   for z in csv.DictReader(f):
    b=int(z["state_effective_ts"]);h=D(z["source_high"]);l=D(z["source_low"])
    while hi and hi[-1][1]<=h:hi.pop()
    hi.append((i,h))
    while lo and lo[-1][1]>=l:lo.pop()
    lo.append((i,l))
    while hi and hi[0][0]<=i-108:hi.popleft()
    while lo and lo[0][0]<=i-108:lo.popleft()
    if i>=107:fs[b]=(lo[0][1],hi[0][1])
    i+=1
  uni={}
  for b,v in fs.items():
   anchor=b//900*900
   if anchor in fs:
    x=fs[anchor];uni[b]=(min(v[0],x[0]),max(v[1],x[1]))
  cnt=0
  for events in ds[sym]["events"].values():
   for e in events:
    b=int(e["state_effective_ts"]);a=b//900*900
    if b not in fs or a not in fs:raise ValueError("H9 unavailable "+sym)
    if (*fs[b],*fs[a])!=(D(e["h9_5m_low"]),D(e["h9_5m_high"]),D(e["h9_15m_low"]),D(e["h9_15m_high"])):
     raise ValueError("H9 parity mismatch "+sym)
    cnt+=1
  checks[sym]={"exact_candidate_parity":cnt,"reconstructed_states":len(uni)}
  geo[sym]=uni
 return geo,checks

class SSBook(tp.TPOnlyBook):
 def __init__(self,geo):
  super().__init__()
  self.geo=geo;self.causes=Counter()
 def handle(self,symbol,t,seq,px,candidates,state):
  bucket=int(t//300)*300
  cur=self.pos[symbol];closed=False;before=self.count["close_HARD_STOP"]
  if cur is not None:
   wanted=None if state is None else (state[1] if cur.side=="LONG" else state[0])
   nxt,ex=tp.tp_only_on_trade(cur,px,seq,bucket,wanted)
   cause=None
   if ex is None:
    if state is None or bucket not in self.geo[symbol]:
     raise ValueError("missing causal state during active position")
    hg=self.geo[symbol][bucket][1 if cur.side=="LONG" else 0]
    if cur.h9_first is None and tp.old.base.favorable(cur.side,cur.entry,hg)<=D(0):
     cur.h9_first=max(bucket,cur.entry_ts)
    lg=state[1 if cur.side=="LONG" else 0]
    raw=tp.old.base.favorable(cur.side,cur.entry,lg)<=D("-4") and state[2]>=D("4")
    h9timer=cur.h9_first is not None and t>=cur.h9_first+18*3600
    if raw or h9timer:
     cause="L53_RAW_MINUS4_WIDTH4" if raw else "H9_LOSS_PLUS18H"
     nxt=None;ex=("SOFT_STOP",px,False)
   self.pos[symbol]=nxt
   if ex is not None:
    reason,exitpx,maker=ex
    gross,enfee,exfee,net=tp.old.base.gross_and_net(cur,exitpx,maker)
    row=tp.old.base.trade_record(symbol,"PREV7D","SOFT18",cur,seq,t,exitpx,reason,maker,gross,enfee,exfee,net)
    row["soft_stop_cause"]=cause
    row["first_h9_loss_ts"]=cur.h9_first
    self.trades.append(row)
    self.diagnostics.append({"symbol":symbol,"side":cur.side,"candidate_event_id":cur.entry_event_id,
     "entry_ts":repr(cur.entry_ts),"exit_ts":repr(t),"exit_reason":reason,
     "soft_stop_cause":cause,"trailing_armed":False,"hard_stop":str(cur.stop),
     "tp_target_at_exit":None if cur.tp is None else str(cur.tp)})
    if reason=="SOFT_STOP":self.causes[cause]+=1
    for k,v in (("gross_usdt",gross),("entry_fees_usdt",enfee),("exit_fees_usdt",exfee),
                ("net_usdt",net),("positive_net_usdt",max(net,D(0))),
                ("negative_net_usdt",min(net,D(0)))):self.money[k]+=v
    self.count["closed"]+=1;self.count["close_"+reason]+=1
    self.count["win" if net>0 else "loss" if net<0 else "flat"]+=1
    closed=True
  for e in candidates:
   if not e["PREV7D"]:continue
   self.count["qualified_candidate"]+=1
   if self.pos[symbol] is not None or closed:
    self.count["busy_or_same_tick"]+=1;continue
   if t<self.pause_until or self.pending:
    self.count["blocked_global_pause"]+=1;continue
   if state is None:raise ValueError("accepted Entry missing state")
   p=tp.old.base.initial_position(e,seq,bucket,"PREV7D","SOFT18")
   target=state[1 if p.side=="LONG" else 0]
   if tp.old.base.take_profit_eligible(p.side,px,target):
    p.tp=target;p.tp_live_after_seq=seq
   p.h9_first=None
   self.pos[symbol]=p;self.count["accepted"]+=1
  # Only actual HARD_STOP contributes to SECOND48.
  if self.count["close_HARD_STOP"]!=before:
   self.all_stops.append({"ts":repr(t),"symbol":symbol})
   self.last_stops.append(t)
   while self.last_stops and t-self.last_stops[0]>tp.HARDSTOP_WINDOW_S:self.last_stops.popleft()
   if len(self.last_stops)>=2:
    self.pause_until=max(self.pause_until,t+48*3600)
    self.count["pause_triggers"]+=1
    self.stops.append({"symbol":symbol,"ts":repr(t),"cooldown_end_ts":repr(self.pause_until),
      "prior_stops_in_48h_including_current":len(self.last_stops),"target_positions":0})
 def finish(self):
  z=super().finish()
  assert z["counts"].get("close_SOFT_STOP",0)==sum(self.causes.values())
  z["soft_stop_causes"]=dict(self.causes)
  z["model"]="SOFT_STOP RAW L53 -4% width>=4% OR H9 +18h; NO trailing; SECOND48 only HARD_STOP"
  return z

def tt_net(r):
 ep=D(r["entry_price"]);xp=D(r["exit_price"])
 fee=(NOMINAL*xp/ep*TAKER).quantize(Q,rounding=ROUND_HALF_EVEN)
 return D(r["gross_usdt"])-D(r["entry_fee_usdt"])-fee

def stats(book,report):
 rows=book.trades
 out={"counts":report["counts"],"money":report["money"],
      "net_taker_taker_usdt":str(sum((tt_net(r) for r in rows),D(0))),
      "soft_stop_causes":report.get("soft_stop_causes",{}),"by_reason":{},"by_symbol":{},
      "by_side":{}}
 for key in set(r["exit_reason"] for r in rows):
  a=[r for r in rows if r["exit_reason"]==key]
  out["by_reason"][key]={"n":len(a),"net_taker_taker_usdt":str(sum((tt_net(r) for r in a),D(0)))}
 for sym in SYMS:
  a=[r for r in rows if r["symbol"]==sym]
  out["by_symbol"][sym]={"n":len(a),"net_taker_taker_usdt":str(sum((tt_net(r) for r in a),D(0))),
   "soft_stop":sum(r["exit_reason"]=="SOFT_STOP" for r in a),
   "hard_stop":sum(r["exit_reason"]=="HARD_STOP" for r in a)}
 for side in ("LONG","SHORT"):
  a=[r for r in rows if r["side"]==side]
  out["by_side"][side]={"n":len(a),"net_taker_taker_usdt":str(sum((tt_net(r) for r in a),D(0))),
   "soft_stop":sum(r["exit_reason"]=="SOFT_STOP" for r in a)}
 return out

def replay(period,smoke_hours=0):
 if (os.geteuid(),os.getegid())!=(107,109):raise PermissionError("wrong research actor")
 if period not in ROOTS or smoke_hours<0 or smoke_hours>24:raise ValueError("invalid replay scope")
 # Autonomous runner has ProtectSystem=strict; spool within its approved
 # CRIPTA_REPORT_DIR and publish immutable results later to research archive.
 report_parent=os.environ.get("CRIPTA_REPORT_DIR")
 if not smoke_hours and report_parent:
  output=Path(report_parent)/("soft_stop_"+period)
 else:
  output=ROOTS[period]/"results"/OUT
 if output.exists() and not smoke_hours:raise FileExistsError("immutable output exists "+str(output))
 if not os.access(output.parent,os.W_OK|os.X_OK):raise PermissionError("research spool parent not writable")
 # Research-only, process-local notional switch: all existing Entry/Exit geometry
 # uses percentages. The imported historical engine uses NOMINAL in its fee/PnL.
 if tp.old.base.NOMINAL not in (REFERENCE_NOMINAL,NOMINAL):
  raise ValueError("unexpected upstream baseline nominal")
 tp.old.base.NOMINAL=NOMINAL
 if period=="90d":
  data={s:tp.old.previous.inputs(s,tp.old.previous.RAW_DEFAULT,tp.old.previous.FROZEN_DEFAULT) for s in SYMS}
  streams={s:tp.old.previous.ticks(data[s]) for s in SYMS}
  first,end=tp.old.entry90.START_TS,tp.old.entry90.END_TS
 else:
  data={s:r29.read_inputs(s) for s in SYMS}
  streams={s:r29.native_ticks(data[s]) for s in SYMS}
  first,end=r29.FIRST,r29.END
 geo,parity=h9_states(period,data)
 books={"TPONLY_BASELINE":tp.TPOnlyBook(),"SOFT_STOP_18H":SSBook(geo)}
 heap=[]
 for ix,s in enumerate(SYMS):
  z=next(streams[s],None)
  if z is None:raise ValueError("empty native source "+s)
  heapq.heappush(heap,(z[0],ix,z))
 cutoff=end if not smoke_hours else min(end,first+smoke_hours*3600)
 ticks=0;day=None
 while heap:
  t,ix,row=heapq.heappop(heap)
  if t>=cutoff:break
  ts,seq,px,tradeid=row;s=SYMS[ix]
  cand=data[s]["events"].get(seq,())
  state=data[s]["states"].get(int(t//300)*300)
  for book in books.values():
   if cand or book.pos[s] is not None:book.handle(s,t,seq,px,cand,state)
  ticks+=1
  today=int(t//86400)
  if today!=day:
   day=today
   print(json.dumps({"phase":"NATIVE_REPLAY","period":period,"utc_day_epoch":today,"ticks":ticks}),flush=True)
  nx=next(streams[s],None)
  if nx is not None:heapq.heappush(heap,(nx[0],ix,nx))
 reports={name:book.finish() for name,book in books.items()}
 if reports["TPONLY_BASELINE"]["counts"]["qualified_candidate"]!=reports["SOFT_STOP_18H"]["counts"]["qualified_candidate"]:
  raise ValueError("candidate parity fails between books")
 if reports["SOFT_STOP_18H"]["counts"].get("close_PROTECTED_STOP",0):raise ValueError("trailing not removed")
 if period=="90d" and not smoke_hours:
  reference=ROOTS[period]/"results/p90l90_tp_only_global_second48_20261108_v1/trade_ledger.jsonl"
  original=[json.loads(x) for x in reference.open() if '"portfolio_scenario":"TP_ONLY_HARDSTOP10_SECOND48"' in x]
  actual=books["TPONLY_BASELINE"].trades
  if len(original)!=len(actual):raise ValueError("90d control count mismatch")
  for a,b in zip(original,actual):
   for f in ("candidate_event_id","entry_ts","exit_ts","entry_price","exit_price",
              "exit_reason","maker_exit"):
    if str(a[f])!=str(b[f]):raise ValueError("90d per-trade baseline identity mismatch field="+f)
   for field,amount in recomputed_money(a,REFERENCE_NOMINAL).items():
    if D(a[field])!=amount:raise ValueError("90d 10-USDT reference amount mismatch "+field)
   for field,amount in recomputed_money(a,NOMINAL).items():
    if D(b[field])!=amount:raise ValueError("90d 100-USDT replay amount mismatch "+field)
 sums={k:stats(b,reports[k]) for k,b in books.items()}
 c=set(r["candidate_event_id"] for r in books["TPONLY_BASELINE"].trades)
 s=set(r["candidate_event_id"] for r in books["SOFT_STOP_18H"].trades)
 result={"research":"P90L90_SOFT_STOP_18H_FULL_PORTFOLIO_V1","period":period,
  "status":"SMOKE_PASS" if smoke_hours else "QC_PASS","evaluation_utc":[first,cutoff],
  "source":"frozen causal P90L90 PREV7D native ordered Bybit trades",
  "mode":"NO TRAILING/NO BE; hardstop gross -10%; original opposite dynamic RAW L5-3 TP >=+0.5%",
  "soft_stop":"L5-3 opposite RAW edge <= -4% from Entry WITH width>=4% OR first favorable H9 RAW edge <= Entry plus 18h",
  "second48":"second actual HARD_STOP in 48h pauses NEW Entry 48h; SOFT_STOP does not trigger",
  "notional_usdt_per_entry":str(NOMINAL),"theoretical_five_slot_capital_usdt":str(NOMINAL*len(SYMS)),
   "cost":"TAKER/TAKER primary, maker resting TP supplementary, no liquidity/price-impact model",
  "unmodeled":["slippage","spread","funding","latency","real_exchange_fills"],
  "symbols":list(SYMS),"geometry_parity":parity,"source_entry_sha256":{
    s:data[s]["manifest"]["events_sha256"] for s in SYMS},
  "native_ticks":ticks,"scenarios":sums,"source_snapshot_sha256":{},
  "output_spool_path":str(output),
  "requires_copy_after_qc_to_research_archive":bool(report_parent and not smoke_hours),
  "counterfactual_entry_difference":{"soft_only_closed":len(s-c),"baseline_only_closed":len(c-s),
    "sample_soft_only":sorted(s-c)[:10]},
  "90d_exact_baseline_parity":period=="90d" and not smoke_hours,
  "90d_price_entry_exit_identity_10usd_reference_and_100usd_money_parity":period=="90d" and not smoke_hours,
  "censored_not_realized":True}
 if smoke_hours:
  print("SMOKE_PASS",json.dumps({"period":period,"ticks":ticks,"parity":parity,
   "counts":{k:v["counts"] for k,v in sums.items()}},sort_keys=True),flush=True)
  return result
 output.mkdir(mode=0o770)
 snap=output/"source_snapshot";snap.mkdir(mode=0o770)
 for file in (Path(__file__),Path(tp.__file__),Path(tp.old.__file__),
              Path(tp.old.previous.__file__),Path(tp.old.base.__file__),
              Path(tp.old.entry90.__file__),Path(r29.__file__),
              Path(r29.sources.__file__),Path(r29.entry.__file__)):
  dest=snap/file.name
  shutil.copyfile(file,dest)
  result["source_snapshot_sha256"][file.name]=digest(dest)
 ledger=output/"trade_ledger.jsonl"
 with ledger.open("x",encoding="utf-8") as fd:
  for name,book in books.items():
   for row in book.trades:
    fd.write(json.dumps({**row,"portfolio_scenario":name},sort_keys=True,separators=(",",":"))+"\n")
 result["ledger_sha256"]=digest(ledger)
 result["ledger_rows"]=sum(len(book.trades) for book in books.values())
 with (output/"result.json").open("x",encoding="utf-8") as fd:
  json.dump(result,fd,sort_keys=True,indent=2);fd.write("\n")
 print("PERIOD_COMPLETE",json.dumps({"period":period,"output":str(output),"net_TT":{
    k:v["net_taker_taker_usdt"] for k,v in sums.items()},"causes":reports["SOFT_STOP_18H"].get("soft_stop_causes",{})},
    sort_keys=True),flush=True)
 return result

def main():
 p=argparse.ArgumentParser()
 p.add_argument("--period",choices=("90d","29d","both"),required=True)
 p.add_argument("--smoke-hours",type=int,default=0)
 a=p.parse_args()
 for period in (("90d","29d") if a.period=="both" else (a.period,)):
  replay(period,a.smoke_hours)
if __name__=="__main__":main()
