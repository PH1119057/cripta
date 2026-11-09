#!/usr/bin/env python3
"""Research-only 90d independent P90L90 LONG admission replay.

GH main versioned source; separate causal CONTROL and LONG_ONLY portfolios.
Original frozen candidate stream, native Bybit publicTrade ordering, one
slot per symbol, TRAIL035/RAW-L53 dynamic TP/-10pct hard stop; SECOND48 only
blocks new Entry in same book. A bounded smoke performs zero writes. Full run
writes one immutable data-disk child of the approved P90L90 results root.
No DB, Exchange, production code, StrategyCard, or LIVE changes.

FS contract: actor cripta uid107; READ immutable P90L90 raw, frozen states,
reference result/ledger, GitHub-main research source; CREATE direct new
results/p90l90_longonly_full90_20261109_v1 child owned cripta; WRITE
result.json, trade_ledger_longonly.jsonl, source_snapshot copies only.
No RENAME/DELETE, no service systemd sandbox, no DB role.
"""
from __future__ import annotations
import os,sys,heapq,json,hashlib,shutil,datetime,argparse
from pathlib import Path
from decimal import Decimal as D,getcontext
from collections import defaultdict,Counter
getcontext().prec=38
CLI=argparse.ArgumentParser(description="Research-only P90L90 LONG native trade replay")
CLI.add_argument("--source-head",required=True)
CLI.add_argument("--smoke-hours",type=int,default=0)
ARGS=CLI.parse_args()
assert len(ARGS.source_head)==40 and all(c in "0123456789abcdef" for c in ARGS.source_head)
assert 0<=ARGS.smoke_hours<=24,"invalid bounded smoke"
SOURCE_HEAD=ARGS.source_head
SMOKE_HOURS=ARGS.smoke_hours
sys.path.insert(0,"/srv/cripta/source_checkout")
from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as old
prev=old.previous
SYMBOLS=tuple(old.SYMBOLS)
ROOT=prev.ROOT
OUT=ROOT/"results"/"p90l90_longonly_full90_20261109_v1"
REF=ROOT/"results"/"p90l90_tp_only_global_second48_20261108_v1"
RESEARCH="P90L90_LONGONLY_FULL90_NATIVE_REPLAY_COMPOUND_V1"
assert os.geteuid()==107,"wrong actor"
assert not OUT.exists() and not OUT.is_symlink(),"immutable output collision"
assert OUT.parent.resolve()==(ROOT/"results").resolve(),"unexpected write parent"
assert OUT.parent.stat().st_uid==107 and os.access(OUT.parent,os.W_OK|os.X_OK),"writer not authorized"
assert old.entry90.START_TS==datetime.datetime(2026,5,18,tzinfo=datetime.timezone.utc).timestamp()
assert old.entry90.END_TS==datetime.datetime(2026,8,16,tzinfo=datetime.timezone.utc).timestamp()
reference_manifest=json.load(open(REF/"result.json"))
reference_data=(REF/"trade_ledger.jsonl").read_bytes()
assert hashlib.sha256(reference_data).hexdigest()==reference_manifest["trade_ledger.jsonl"]["sha256"],"baseline reference SHA drift"
reference=[]
for line in reference_data.splitlines():
    r=json.loads(line)
    if r["portfolio_scenario"]=="LEGACY_L53_SECOND48":
        r.pop("portfolio_scenario")
        reference.append(r)
assert len(reference)==692
print(json.dumps({"stage":"PREFLIGHT_PASS","euid":os.geteuid(),"git_main_source":"97da374ac38f1b2feaddc94081c7ac2820dc421f","output":str(OUT),"reference_closed":len(reference)}),flush=True)
data={s:prev.inputs(s,prev.RAW_DEFAULT,prev.FROZEN_DEFAULT) for s in SYMBOLS}
streams={s:prev.ticks(data[s]) for s in SYMBOLS}
heap=[]
for i,s in enumerate(SYMBOLS):
    item=next(streams[s],None)
    if item is None:raise ValueError("empty stream "+s)
    heapq.heappush(heap,(item[0],i,item))
books={"CONTROL":old.EntryEmbargo(2,48),"LONG_ONLY":old.EntryEmbargo(2,48)}
n=0
day=None
last_prices={}
raw_candidate_count=Counter()
while heap:
    t,idx,(ts,seq,px,trade_id)=heapq.heappop(heap)
    if SMOKE_HOURS and t>=old.entry90.START_TS+SMOKE_HOURS*3600:break
    symbol=SYMBOLS[idx]
    events=data[symbol]["events"].get(seq,())
    long=tuple(e for e in events if e["side"]=="LONG")
    raw_candidate_count[(symbol,"all")]+=sum(bool(e.get(prev.COHORT)) for e in events)
    raw_candidate_count[(symbol,"long")]+=sum(bool(e.get(prev.COHORT)) for e in long)
    state=data[symbol]["states"].get(int(t//300)*300)
    if events or books["CONTROL"].pos[symbol] is not None:
        books["CONTROL"].handle(symbol,t,seq,px,events,state)
    if long or books["LONG_ONLY"].pos[symbol] is not None:
        books["LONG_ONLY"].handle(symbol,t,seq,px,long,state)
    last_prices[symbol]=px
    n+=1
    thisday=int(t//86400)
    if thisday!=day:
        day=thisday
        print(json.dumps({"stage":"REPLAY","utc":datetime.datetime.fromtimestamp(t,datetime.timezone.utc).strftime("%Y-%m-%d"),"ticks":n,"accepted_long":books["LONG_ONLY"].count["accepted"],"closed_long":books["LONG_ONLY"].count["closed"]}),flush=True)
    nxt=next(streams[symbol],None)
    if nxt is not None:heapq.heappush(heap,(nxt[0],idx,nxt))
if SMOKE_HOURS:
    assert all(row["side"]=="LONG" for row in books["LONG_ONLY"].trades)
    print(json.dumps({"stage":"SMOKE_PASS","hours":SMOKE_HOURS,"native_ticks":n,"control":dict(books["CONTROL"].count),"long_only":dict(books["LONG_ONLY"].count),"no_writes":True}),flush=True)
    sys.exit(0)
assert n>68000000 and n<90000000,"unexpected native tick coverage"
report={name:book.finish() for name,book in books.items()}
for sym in SYMBOLS:
    z=data[sym]
    assert z["seen_candidate"]==sum(map(len,z["events"].values())),"candidate coverage "+sym
    assert z["read_count"]==sum(int(x["rows"]) for x in z["manifest"]["inputs"]),"source row coverage "+sym
assert books["CONTROL"].trades==reference,"CONTROL TRADE-BY-TRADE PARITY FAILED"
assert report["CONTROL"]["counts"]==reference_manifest["scenarios"]["LEGACY_L53_SECOND48"]["counts"],"control count parity"
assert all(x["side"]=="LONG" for x in books["LONG_ONLY"].trades),"LONG-only contains SHORT exit"
assert all(x["side"]=="LONG" for x in report["LONG_ONLY"]["censored"]),"LONG-only contains SHORT censored"
assert report["LONG_ONLY"]["counts"]["qualified_candidate"]==sum(raw_candidate_count[(s,"long")] for s in SYMBOLS),"long candidate mismatch"
print(json.dumps({"stage":"REPLAY_QC_PASS","raw_ticks":n,"control_closed":len(reference),"long_counts":report["LONG_ONLY"]["counts"],"long_hardstops":len(report["LONG_ONLY"]["observed_hardstop_events"]),"long_pause_triggers":len(report["LONG_ONLY"]["hardstop_triggers"])}),flush=True)
original=books["LONG_ONLY"].trades
censored=report["LONG_ONLY"]["censored"]
RATE=D("0.00055")
BEG=D(str(old.entry90.START_TS))
END=D(str(old.entry90.END_TS))
def cash(mode:str,slip_bps:str="0",fees:str="taker"):
    slip=D(slip_bps)/D(10000)
    items=[]
    for x in original:
        items.append((D(str(x["entry_ts"])),1,x))
        items.append((D(str(x["exit_ts"])),0,x))
    for x in censored:items.append((D(str(x["entry_ts"])),1,x))
    items.sort(key=lambda z:(z[0],z[1],SYMBOLS.index(z[2]["symbol"])))
    wallet=D(1000)
    reserved=D(0)
    active={}
    caps=[]
    blocked=[]
    stats=Counter()
    money=defaultdict(lambda:D(0))
    bycoin=defaultdict(lambda:defaultdict(lambda:D(0)))
    exits=[]
    peak=wallet
    maxdd=(D(0),None,D(1000))
    minfree=(wallet,None)
    max_concurrent=0
    max_reserved=D(0)
    balances=[]
    for t,kind,x in items:
        key=x.get("candidate_event_id") or x.get("entry_event_id")
        if kind==1:
            ideal=D(200) if mode=="fixed" else wallet/D(5)
            free=wallet-reserved
            allowed=max(D(0),free/(D(1)+D(5)*(RATE+slip)))
            margin=min(ideal,allowed) if mode=="compound_capped" else ideal
            if margin<ideal:
                caps.append({"ts":str(t),"symbol":x["symbol"],"wanted":str(ideal),"actual":str(margin)})
            if margin<=0:
                blocked.append({"ts":str(t),"key":key,"free":str(free)})
                continue
            entry_fee=margin*D(5)*RATE
            slip_enter=margin*D(5)*slip
            wallet-=entry_fee+slip_enter
            money["entry_fees"]+=entry_fee
            money["slippage"]+=slip_enter
            reserved+=margin
            active[key]={"margin":margin,"fee":entry_fee,"slip":slip_enter,"entry_ts":t,"side":x["side"],"symbol":x["symbol"]}
            max_concurrent=max(max_concurrent,len(active))
            max_reserved=max(max_reserved,reserved)
        else:
            if key not in active:
                blocked.append({"ts":str(t),"key":key,"missing_exit":True})
                continue
            p=active.pop(key)
            margin=p["margin"]
            nominal=margin*D(5)
            ratio=D(x["exit_price"])/D(x["entry_price"])
            gross=D(x["gross_usdt"])*nominal/D(10)
            fee_out=(nominal*ratio*RATE if fees=="taker" else D(x["exit_fee_usdt"])*nominal/D(10))
            slip_out=nominal*ratio*slip
            wallet+=gross-fee_out-slip_out
            reserved-=margin
            money["gross"]+=gross
            money["exit_fees"]+=fee_out
            money["slippage"]+=slip_out
            net=gross-p["fee"]-p["slip"]-fee_out-slip_out
            money["closed_net"]+=net
            money["hardstop_net" if x["exit_reason"]=="HARD_STOP" else "nonhardstop_net"]+=net
            bycoin[x["symbol"]]["net"]+=net
            bycoin[x["symbol"]]["closed"]+=1
            bycoin[x["symbol"]]["hardstops"]+=int(x["exit_reason"]=="HARD_STOP")
            stats["closed"]+=1
            stats["hardstops"]+=int(x["exit_reason"]=="HARD_STOP")
            exits.append({"exit_ts":str(t),"symbol":x["symbol"],"reason":x["exit_reason"],"net":str(net),"margin":str(margin)})
        if wallet>peak:peak=wallet
        dd=peak-wallet
        if dd>maxdd[0]:maxdd=(dd,t,peak)
        free=wallet-reserved
        if free<minfree[0]:minfree=(free,t)
        balances.append((t,wallet,reserved,free))
    unreal=D(0)
    estimated_exit_fee=D(0)
    censored_list=[]
    for x in censored:
        key=x["entry_event_id"]
        if key not in active:
            censored_list.append({"symbol":x["symbol"],"rejected":True})
            continue
        p=active[key]
        pos=books["LONG_ONLY"].pos[x["symbol"]]
        assert pos is not None and pos.entry_event_id==key
        mark=last_prices[x["symbol"]]
        ratio=mark/pos.entry
        gross=p["margin"]*D(5)*(D(1)-ratio if pos.side=="SHORT" else ratio-D(1))
        fee=p["margin"]*D(5)*ratio*RATE
        unreal+=gross
        estimated_exit_fee+=fee
        censored_list.append({"symbol":x["symbol"],"side":pos.side,"margin_usdt":str(p["margin"]),"entry_price":str(pos.entry),"last_price":str(mark),"unrealized_usdt":str(gross)})
    weeks=[]
    basewallet=D(1000)
    for i in range(13):
        left=BEG+D(i*7*86400)
        right=min(BEG+D((i+1)*7*86400),END)
        week_exits=[x for x in exits if left<=D(x["exit_ts"])<right]
        snapshots=[x for x in balances if x[0]<right]
        ending=snapshots[-1][1] if snapshots else basewallet
        weeks.append({"week":i+1,"closed":len(week_exits),"closed_trade_net_usdt":str(sum((D(x["net"]) for x in week_exits),D(0))),"wallet_usdt":str(ending),"wallet_change_usdt":str(ending-basewallet)})
        basewallet=ending
    result={"mode":mode,"slippage_bps_per_side":slip_bps,"fee_model":fees,"starting_deposit_usdt":"1000","final_wallet_usdt":str(wallet),"realized_wallet_net_usdt":str(wallet-D(1000)),"open_margin_reserved_usdt":str(reserved),"free_margin_usdt":str(wallet-reserved),"open_unrealized_at_last_trade_usdt":str(unreal),"equity_mark_to_last_trade_usdt":str(wallet+unreal),"hypothetical_equity_after_taker_close_usdt":str(wallet+unreal-estimated_exit_fee),"censored_open":censored_list,"max_concurrent_positions":max_concurrent,"max_reserved_margin_usdt":str(max_reserved),"min_free_margin_usdt":str(minfree[0]),"max_realized_wallet_drawdown_usdt":str(maxdd[0]),"max_realized_wallet_drawdown_pct_of_peak":str(maxdd[0]/maxdd[2]*D(100)),"capped_entries":len(caps),"capped_examples":caps[:12],"blocked_or_missing_entries":blocked[:12],"blocked_count":len(blocked),"money":{k:str(v) for k,v in money.items()},"by_coin":{k:{key:str(v) for key,v in val.items()} for k,val in sorted(bycoin.items())},"weeks":weeks}
    return result
scenarios={}
for m in ("fixed","compound_capped"):
    scenarios[m]=cash(m)
    if scenarios[m]["blocked_count"] or D(scenarios[m]["min_free_margin_usdt"])<0:
        scenarios[m]["capital_admission_warning"]=True
    assert scenarios[m]["max_concurrent_positions"]<=5
    assert scenarios[m]["min_free_margin_usdt"] is not None
for bp in ("1","2","5","10"):
    s=cash("compound_capped",bp)
    if s["blocked_count"]:s["capital_admission_warning"]=True
    scenarios["compound_slippage_"+bp+"bps"]=s
source_files=[Path(__file__),Path(old.__file__),Path(prev.__file__),Path(old.base.__file__),Path(old.entry90.__file__)]
src_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
long_ledger_bytes=("".join(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n" for row in original)).encode()
result={
 "research":RESEARCH,"status":"COMPLETE_HISTORICAL_PROXY_NOT_LIVE","period_utc":["2026-05-18T00:00:00Z","2026-08-16T00:00:00Z"],"symbols":list(SYMBOLS),
 "source_head_verified_main":SOURCE_HEAD,
 "method":"Full native tick five-coin merge, original P90L90 PREV7D RAW H9 Entry+L5-3 TRAIL035 Exit, second hardstop in rolling 48h pauses NEW entries globally for 48h, never flattens. Long-only direction filter BEFORE occupancy and SECOND48; independent original mixed control processed in parallel, exact ledger parity.",
 "source_rows_by_symbol":{s:data[s]["read_count"] for s in SYMBOLS},
 "candidate_sha256_by_symbol":{s:data[s]["manifest"]["events_sha256"] for s in SYMBOLS},
 "candidate_qualified_by_coin":{s:{"all":raw_candidate_count[(s,"all")],"long":raw_candidate_count[(s,"long")]} for s in SYMBOLS},
 "raw_native_ticks_evaluated":n,
 "control_reference_trade_ledger_sha256":reference_manifest["trade_ledger.jsonl"]["sha256"],
 "control_reference_trade_parity":True,
 "control_counts":report["CONTROL"]["counts"],
 "long_only_counts":report["LONG_ONLY"]["counts"],
 "long_only_net_historical_fee_model_usdt_10_notional":report["LONG_ONLY"]["money"]["net_usdt"],
 "long_only_hardstop_events":report["LONG_ONLY"]["observed_hardstop_events"],
 "long_only_pause_triggers":report["LONG_ONLY"]["hardstop_triggers"],
 "long_only_censored_open":censored,
 "cash_models":scenarios,
 "fee_model":"Conservative taker/taker 0.055% each side, with separate historical maker-exit cost sensitivity not calculated here",
 "leverage":5,"fixed_margin_usdt":200,"compound_margin_fraction_of_realized_wallet":0.2,
 "unmodeled":["bid_ask_spread","slippage_baseline","funding","latency","exchange_liquidation","position_step_rounding","historical_lot_size","floating_PnL_based_available_margin"],
 "long_only_ledger_sha256":hashlib.sha256(long_ledger_bytes).hexdigest(),
 "long_only_ledger_rows":len(original),
 "source_snapshot_sha256":src_hashes,
}
# Only after QC: immutable research output under the established data-disk actor/path contract.
OUT.mkdir(mode=0o2770)
snap=OUT/"source_snapshot"
snap.mkdir(mode=0o2770)
for p in source_files:shutil.copyfile(p,snap/p.name)
(OUT/"trade_ledger_longonly.jsonl").write_bytes(long_ledger_bytes)
(OUT/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
assert hashlib.sha256((OUT/"trade_ledger_longonly.jsonl").read_bytes()).hexdigest()==result["long_only_ledger_sha256"]
assert json.loads((OUT/"result.json").read_text())["control_reference_trade_parity"] is True
print(json.dumps({"stage":"COMPLETE","result":str(OUT/"result.json"),"control_parity":True,"native_ticks":n,"long_counts":result["long_only_counts"],"fixed":{"wallet":scenarios["fixed"]["final_wallet_usdt"],"equity":scenarios["fixed"]["equity_mark_to_last_trade_usdt"]},"compound":{"wallet":scenarios["compound_capped"]["final_wallet_usdt"],"equity":scenarios["compound_capped"]["equity_mark_to_last_trade_usdt"],"capped":scenarios["compound_capped"]["capped_entries"]}},sort_keys=True),flush=True)
