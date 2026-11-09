#!/usr/bin/env python3
"""Research-only causal 7d/24h Hard Stop-rate threshold sweep.

Six independent thresholds 1.5/2/2.5/3/3.5/4 percent on each LONG/SHORT
no-pause shadow's last seven complete calendar days of closed trades.
Only a new shadow HARD_STOP can trigger, after 7-day maturation and >=20
closed trades. Qualifying rate >= threshold pauses only fresh same-side
Entries across five coins for 24h, extends on later shadow hardstops;
existing positions carry on with original Exit and no flatten.
The 2.5% scenario must match exact prior native replay on journals,
counters, signals, and fixed/compound/slippage cash models.
No production DB, runtime, Strategy, deploy, Exchange or LIVE.
Research output only a new immutable child, cripta UID107; smoke no writes.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import heapq
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict, deque
from decimal import Decimal as D, getcontext
from pathlib import Path

getcontext().prec = 38
sys.path.insert(0, "/srv/cripta/source_checkout")
from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as old  # noqa: E402

SYMBOLS = tuple(old.SYMBOLS)
SIDES = ("LONG_ONLY", "SHORT_ONLY")
RESEARCH = "P90L90_RATE_THRESHOLD_W7H24_V1"
WINDOW_DAYS = (7,)
PAUSE_HOURS = (24,)
MIN_CLOSES = 20
THRESHOLDS_BPS = (150, 200, 250, 300, 350, 400)

CLI = argparse.ArgumentParser()
CLI.add_argument("--source-head", required=True)
CLI.add_argument("--period", choices=("90d", "29d"), required=True)
CLI.add_argument("--smoke-hours", type=int, default=0)
ARGS = CLI.parse_args()
PERIOD = ARGS.period
ROOT = old.previous.ROOT if PERIOD == "90d" else old.sources29.ROOT
RESULTS = ROOT / "results"
OUT = RESULTS / "p90l90_rate_threshold_w7h24_20261110_v1"
AVAILABLE = tuple(w for w in WINDOW_DAYS if w*86400 <=
                  (old.entry90.END_TS-old.entry90.START_TS if PERIOD=="90d"
                   else old.entry29.END_TS-old.entry29.START_TS))
UNAVAILABLE = tuple(w for w in WINDOW_DAYS if w not in AVAILABLE)
BASELINE = RESULTS / "p90l90_side_nopause_compare_20261109_v1"
PRIOR_RATE25 = RESULTS / "p90l90_rate25_7_10_30_pause3_6_24_20261109_v1"
REF = RESULTS / ("entry_only_embargo_" + PERIOD + "_20261026_v1")
START_TS = old.entry90.START_TS if PERIOD == "90d" else old.entry29.START_TS
END_TS = old.entry90.END_TS if PERIOD == "90d" else old.entry29.END_TS
EXPECTED_CLOSED = 730 if PERIOD == "90d" else 328
EXPECTED_TICKS = 68988768 if PERIOD == "90d" else 43124572
EXPECTED_CANDIDATES = 3959 if PERIOD == "90d" else 886
assert old.previous.COHORT == "PREV7D"
assert len(ARGS.source_head) == 40
assert all(c in "0123456789abcdef" for c in ARGS.source_head)
assert 0 <= ARGS.smoke_hours <= 24
assert os.geteuid() == 107, "wrong research writer actor"
assert RESULTS.stat().st_uid == 107
assert os.access(RESULTS, os.W_OK | os.X_OK)
assert not OUT.exists() and not OUT.is_symlink(), "immutable output collision"
assert OUT.parent.resolve() == RESULTS.resolve()
assert subprocess.run(
    ["git", "-C", "/srv/cripta/source_checkout", "rev-parse", "HEAD"],
    check=True, text=True, stdout=subprocess.PIPE,
    env={**os.environ,"GIT_OPTIONAL_LOCKS":"0"}
).stdout.strip() == ARGS.source_head, "source mirror identity mismatch"
expected_start = datetime.datetime(
    2026 if PERIOD == "90d" else 2026,
    5 if PERIOD == "90d" else 9,
    18 if PERIOD == "90d" else 21,
    tzinfo=datetime.timezone.utc,
)
expected_end = datetime.datetime(
    2026, 8 if PERIOD == "90d" else 10,
    16 if PERIOD == "90d" else 20,
    tzinfo=datetime.timezone.utc,
)
assert START_TS == expected_start.timestamp()
assert END_TS == expected_end.timestamp()

reference_manifest = json.loads((REF / "result.json").read_text())
refbytes = (REF / "trade_ledger.jsonl").read_bytes()
assert hashlib.sha256(refbytes).hexdigest() == reference_manifest["ledger_sha256"]
reference = []
for line in refbytes.splitlines():
    row = json.loads(line)
    if row["portfolio_scenario"] == "BASELINE":
        row.pop("portfolio_scenario")
        reference.append(row)
assert len(reference) == EXPECTED_CLOSED
expected_control = reference_manifest["scenarios"]["BASELINE"]
print(json.dumps({
    "stage": "PREFLIGHT_PASS", "uid": os.geteuid(),
    "ref_closed": len(reference), "period": PERIOD,
    "output": str(OUT), "source_head": ARGS.source_head
}), flush=True)

if PERIOD == "90d":
    data = {
        s: old.previous.inputs(
            s, old.previous.RAW_DEFAULT, old.previous.FROZEN_DEFAULT
        ) for s in SYMBOLS
    }
    streams = {s: old.previous.ticks(data[s]) for s in SYMBOLS}
else:
    data = {s: old.sep.read_inputs(s) for s in SYMBOLS}
    streams = {s: old.sep.native_ticks(data[s]) for s in SYMBOLS}
heap = []
for priority, symbol in enumerate(SYMBOLS):
    first = next(streams[symbol], None)
    if first is None:
        raise ValueError("empty Bybit 29d native ticker " + symbol)
    heapq.heappush(heap, (first[0], priority, first))

books = {
    "CONTROL": old.EntryEmbargo(0, None),
    "LONG_ONLY": old.EntryEmbargo(0, None),
    "SHORT_ONLY": old.EntryEmbargo(0, None),
}
for direction in SIDES:
    for threshold_bps in THRESHOLDS_BPS:
        books[f"{direction}_B{threshold_bps}_W7_H24"] = old.EntryEmbargo(0,None)
signal_deques = {side:deque() for side in SIDES}
signal_events = {(side,bps):[] for side in SIDES for bps in THRESHOLDS_BPS}
scenario_events = defaultdict(list)
n = 0
n = 0
previous_day = None
last_prices = {}
candidate_counts = Counter()
limit = END_TS if not ARGS.smoke_hours else (
    START_TS + ARGS.smoke_hours * 3600
)
while heap:
    t, idx, (ts, seq, px, trade_id) = heapq.heappop(heap)
    if t >= limit:
        break
    symbol = SYMBOLS[idx]
    events = data[symbol]["events"].get(seq, ())
    byside = {
        "LONG_ONLY": tuple(e for e in events if e["side"] == "LONG"),
        "SHORT_ONLY": tuple(e for e in events if e["side"] == "SHORT"),
    }
    candidate_counts[(symbol, "all")] += sum(bool(e.get("PREV7D")) for e in events)
    for book_name, direction_events in byside.items():
        candidate_counts[(symbol, book_name)] += sum(
            bool(e.get("PREV7D")) for e in direction_events
        )
    state = data[symbol]["states"].get(int(t // 300) * 300)
    if events or books["CONTROL"].pos[symbol] is not None:
        books["CONTROL"].handle(symbol, t, seq, px, events, state)
    for book_name, direction_events in byside.items():
        # Shadow baseline MUST handle first: its closure becomes observable
        # on this exact current source tick, never before this tick.
        shadow = books[book_name]
        prev_closed = shadow.count["closed"]
        if direction_events or shadow.pos[symbol] is not None:
            shadow.handle(symbol, t, seq, px, direction_events, state)
        if shadow.count["closed"] != prev_closed:
            assert shadow.count["closed"] == prev_closed + 1
            close = shadow.trades[-1]
            assert close["symbol"] == symbol
            assert close["exit_source_order"] == seq
            assert float(close["exit_ts"]) == t
            hard = close["exit_reason"] == "HARD_STOP"
            q = signal_deques[book_name]
            while q and q[0][0] <= t-7*86400:
                q.popleft()
            q.append((t,int(hard)))
            if hard and t>=START_TS+7*86400 and len(q)>=MIN_CLOSES:
                stopped=sum(x[1] for x in q)
                for threshold_bps in THRESHOLDS_BPS:
                    if stopped*10000 < len(q)*threshold_bps:
                        continue
                    info = {
                        "ts":repr(t),"symbol":symbol,"source_order":seq,
                        "lookback_days":7,"closed_trades":len(q),
                        "hardstops":stopped,
                        "rate_pct":str(D(100)*D(stopped)/D(len(q))),
                        "threshold_pct":str(D(threshold_bps)/D(100)),
                    }
                    signal_events[(book_name,threshold_bps)].append(info)
                    case=f"{book_name}_B{threshold_bps}_W7_H24"
                    controlled=books[case]
                    previous_end=controlled.pause_until
                    controlled.pause_until=max(previous_end,t+24*3600)
                    controlled.count["signal_triggers"]+=1
                    if t>=previous_end:
                        controlled.count["pause_starts"]+=1
                    elif controlled.pause_until>previous_end:
                        controlled.count["pause_extensions"]+=1
                    scenario_events[case].append({
                        **info,"pause_hours":24,
                        "previous_pause_end":repr(previous_end),
                        "new_pause_end":repr(controlled.pause_until),
                    })
        for threshold_bps in THRESHOLDS_BPS:
            case=f"{book_name}_B{threshold_bps}_W7_H24"
            controlled=books[case]
            if direction_events or controlled.pos[symbol] is not None:
                controlled.handle(symbol,t,seq,px,direction_events,state)
    last_prices[symbol] = px
    last_prices[symbol] = px
    n += 1
    day = int(t // 86400)
    if previous_day != day:
        previous_day = day
        print(json.dumps({
            "stage": "REPLAY", "utc": datetime.datetime.fromtimestamp(
                t, datetime.timezone.utc
            ).strftime("%Y-%m-%d"), "ticks": n,
            "long_accepted": books["LONG_ONLY"].count["accepted"],
            "short_accepted": books["SHORT_ONLY"].count["accepted"],
        }), flush=True)
    nxt = next(streams[symbol], None)
    if nxt is not None:
        heapq.heappush(heap, (nxt[0], idx, nxt))

if ARGS.smoke_hours:
    assert all(x["side"] == "LONG" for x in books["LONG_ONLY"].trades)
    assert all(x["side"] == "SHORT" for x in books["SHORT_ONLY"].trades)
    assert not any(x.stops for x in books.values())
    print(json.dumps({
        "stage": "SMOKE_PASS", "hours": ARGS.smoke_hours, "ticks": n,
        "no_writes": True, "counts": {name: dict(b.count) for name, b in books.items()}
    }), flush=True)
    sys.exit(0)

expected_eval = 0
for symbol in SYMBOLS:
    row = data[symbol]
    if PERIOD == "90d":
        assert row["seen_candidate"] == sum(map(len, row["events"].values()))
        files = row["manifest"]["inputs"]
        assert len(files) == 100, "missing 10d warmup or 90d data"
        assert files[0]["file"].endswith("2026-05-08.csv.gz")
        assert files[10]["file"].endswith("2026-05-18.csv.gz")
        assert files[-1]["file"].endswith("2026-08-15.csv.gz")
        assert row["read_count"] == sum(
            int(v["rows"]) for v in files
        ), "full 100d source coverage mismatch"
        # The reader checks all 100 source days; its yield excludes 10d warmup.
        expected_eval += sum(int(v["rows"]) for v in files[10:])
    else:
        assert row["seen_events"] == sum(map(len, row["events"].values()))
        assert row["seen_rows"] == sum(
            v["rows"] for v in row["manifest"]["inputs"]
        )
        assert len(row["manifest"]["inputs"]) == 39
        expected_eval += sum(v["rows"] for v in row["manifest"]["inputs"][10:])
assert n == expected_eval == EXPECTED_TICKS, (
    "native tick coverage", n, expected_eval, EXPECTED_TICKS
)
reports = {name: book.finish() for name, book in books.items()}
assert books["CONTROL"].trades == reference, "exact control ledger parity FAILED"
assert reports["CONTROL"]["counts"] == expected_control["counts"]
assert reports["CONTROL"]["money"] == expected_control["money"]
assert reports["CONTROL"]["counts"]["closed"] == EXPECTED_CLOSED
for side in SIDES:
    expected = side.split("_")[0]
    rows = books[side].trades
    censored = reports[side]["censored"]
    assert all(x["side"] == expected for x in rows)
    assert all(x["side"] == expected for x in censored)
    assert reports[side]["counts"]["accepted"] == len(rows) + len(censored)
    assert reports[side]["counts"]["qualified_candidate"] == sum(
        candidate_counts[(s, side)] for s in SYMBOLS
    )
assert sum(candidate_counts[(s, "all")] for s in SYMBOLS) == EXPECTED_CANDIDATES
assert all(not books[s].stops for s in books)
for side in SIDES:
    for threshold_bps in THRESHOLDS_BPS:
        case=f"{side}_B{threshold_bps}_W7_H24"
        controlled=reports[case]
        assert controlled["counts"]["qualified_candidate"] == sum(
            candidate_counts[(sym,side)] for sym in SYMBOLS
        )
        assert controlled["counts"].get("signal_triggers",0) == len(
            signal_events[(side,threshold_bps)]
        )
        assert controlled["counts"]["accepted"] == (
            controlled["counts"]["closed"]+
            controlled["counts"]["censored_open"]
        )
        assert not books[case].stops and not books[case].pending
        assert all(z["side"]==side.split("_")[0]
                   for z in books[case].trades)
print(json.dumps({
    "stage": "REPLAY_QC_PASS"print(json.dumps({
    "stage": "REPLAY_QC_PASS", "ticks": n,
    "control_closed": EXPECTED_CLOSED, "period": PERIOD,
    "counts": {side: reports[side]["counts"] for side in SIDES}
}), flush=True)

RATE=D("0.00055")
BEG=D(str(START_TS))
END=D(str(END_TS))
def cash(book_name:str,mode:str,slip_bps:str="0",fees:str="taker"):
    original=books[book_name].trades
    censored=reports[book_name]["censored"]
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
        pos=books[book_name].pos[x["symbol"]]
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
    for i in range((int(END-BEG)+7*86400-1)//(7*86400)):
        left=BEG+D(i*7*86400)
        right=min(BEG+D((i+1)*7*86400),END)
        week_exits=[x for x in exits if left<=D(x["exit_ts"])<right]
        snapshots=[x for x in balances if x[0]<right]
        ending=snapshots[-1][1] if snapshots else basewallet
        weeks.append({"week":i+1,"closed":len(week_exits),"closed_trade_net_usdt":str(sum((D(x["net"]) for x in week_exits),D(0))),"wallet_usdt":str(ending),"wallet_change_usdt":str(ending-basewallet)})
        basewallet=ending
    result={"mode":mode,"slippage_bps_per_side":slip_bps,"fee_model":fees,"starting_deposit_usdt":"1000","final_wallet_usdt":str(wallet),"realized_wallet_net_usdt":str(wallet-D(1000)),"open_margin_reserved_usdt":str(reserved),"free_margin_usdt":str(wallet-reserved),"open_unrealized_at_last_trade_usdt":str(unreal),"equity_mark_to_last_trade_usdt":str(wallet+unreal),"hypothetical_equity_after_taker_close_usdt":str(wallet+unreal-estimated_exit_fee),"censored_open":censored_list,"max_concurrent_positions":max_concurrent,"max_reserved_margin_usdt":str(max_reserved),"min_free_margin_usdt":str(minfree[0]),"max_realized_wallet_drawdown_usdt":str(maxdd[0]),"max_realized_wallet_drawdown_pct_of_peak":str(maxdd[0]/maxdd[2]*D(100)),"capped_entries":len(caps),"capped_examples":caps[:12],"blocked_or_missing_entries":blocked[:12],"blocked_count":len(blocked),"money":{k:str(v) for k,v in money.items()},"by_coin":{k:{key:str(v) for key,v in val.items()} for k,val in sorted(bycoin.items())},"weeks":weeks}
    return result

books_money = {}
ledger_payloads = {}
for book_name in books:
    models = {}
    for mode in ("fixed", "compound_capped"):
        models[mode] = cash(book_name, mode)
        if models[mode]["blocked_count"] or D(models[mode]["min_free_margin_usdt"]) < 0:
            models[mode]["capital_admission_warning"] = True
        assert models[mode]["max_concurrent_positions"] <= 5
    for bps in ("1", "2", "5", "10"):
        model = cash(book_name, "compound_capped", bps)
        if model["blocked_count"] or D(model["min_free_margin_usdt"]) < 0:
            model["capital_admission_warning"] = True
        models["compound_slippage_" + bps + "bps"] = model
    books_money[book_name] = models
    ledger_payloads[book_name] = (
        "".join(json.dumps(x, sort_keys=True, separators=(",", ":")) + "\n"
                for x in books[book_name].trades).encode()
    )

# The no-pause controls must EXACTLY match immutable prior side books.
baseline = json.loads((BASELINE/"result.json").read_text())
assert baseline["status"] == "COMPLETE_HISTORICAL_PROXY_NOT_LIVE"
assert baseline["raw_native_ticks_evaluated"] == n
assert baseline["control_counts"] == reports["CONTROL"]["counts"]
for side in SIDES:
    leaf=BASELINE/("trade_ledger_"+side.lower()+".jsonl")
    raw=leaf.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==baseline["ledger_sha256"][side]
    assert books[side].trades == [json.loads(z) for z in raw.splitlines()]
    assert reports[side]["counts"] == baseline["direction_counts"][side]
    for mode in ("fixed","compound_capped"):
        for metric in ("final_wallet_usdt","equity_mark_to_last_trade_usdt"):
            assert D(books_money[side][mode][metric]) == D(
                baseline["cash_models"][side][mode][metric]
            ), ("no-pause cash parity",side,mode,metric)
# Exact cross-run parity to the complete and immutable 2.5% W7 H24 case.
prior_bytes=(PRIOR_RATE25/"result.json").read_bytes()
prior_rate25=json.loads(prior_bytes)
assert prior_rate25["status"]=="COMPLETE_HISTORICAL_PROXY_NOT_LIVE"
assert prior_rate25["raw_native_ticks_evaluated"]==n
assert prior_rate25["no_pause_control_exact_trade_cash_parity"] is True
for side in SIDES:
    case=f"{side}_B250_W7_H24"
    old_case=f"{side}_W7_H24"
    old_leaf=PRIOR_RATE25/("trade_ledger_"+old_case.lower()+".jsonl")
    old_raw=old_leaf.read_bytes()
    assert hashlib.sha256(old_raw).hexdigest()==prior_rate25["ledger_sha256"][old_case]
    assert books[case].trades == [json.loads(x) for x in old_raw.splitlines()]
    assert reports[case]["counts"] == prior_rate25["scenario_counts"][old_case]
    prior_signals=prior_rate25["signal_events"][f"{side}_W7"]
    new_signals=signal_events[(side,250)]
    assert len(new_signals)==len(prior_signals)
    for fresh,previous in zip(new_signals,prior_signals):
        assert {k:v for k,v in fresh.items() if k!="threshold_pct"} == previous
    for mode in books_money[case]:
        for field in ("final_wallet_usdt","equity_mark_to_last_trade_usdt",
                      "hypothetical_equity_after_taker_close_usdt",
                      "realized_wallet_net_usdt"):
            assert D(books_money[case][mode][field]) == D(
                prior_rate25["cash_models"][old_case][mode][field]
            ),("rate25 money parity",side,mode,field)
print(json.dumps({"stage":"OLD_RATE25_PARITY_PASS","period":PERIOD,
                  "reference_sha256":hashlib.sha256(prior_bytes).hexdigest()}),
      flush=True)

engine_files = [
    Path(__file__), Path(old.__file__), Path(old.previous.__file__),
    Path(old.base.__file__), Path(old.entry90.__file__),
    Path(old.entry29.__file__),
    Path(old.sep.__file__), Path(old.sources29.__file__),
]
engine_hashes = {
    p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in engine_files
}
result = {
    "research": RESEARCH,
    "status": "COMPLETE_HISTORICAL_PROXY_NOT_LIVE",
    "period": PERIOD,
    "period_utc": [expected_start.isoformat(), expected_end.isoformat()],
    "symbols": list(SYMBOLS),
    "source_head_verified_main": ARGS.source_head,
    "raw_native_ticks_evaluated": n,
    "source_rows_by_symbol": {
        s: (data[s]["read_count"] if PERIOD == "90d" else data[s]["seen_rows"])
        for s in SYMBOLS
    },
    "candidate_sha256_by_symbol": {
        s: data[s]["manifest"]["events_sha256"] for s in SYMBOLS
    },
    "candidate_qualified_by_coin": {
        s: {
            "all": candidate_counts[(s, "all")],
            "long": candidate_counts[(s, "LONG_ONLY")],
            "short": candidate_counts[(s, "SHORT_ONLY")],
        } for s in SYMBOLS
    },
    "control_reference_ledger_sha256": hashlib.sha256(refbytes).hexdigest(),
    "control_reference_trade_parity": True,
    "pause_policy": (
        "SHADOW no-pause rolling 7 days; >=exact threshold on new "
        "shadow hardstop; minimum 20 closes; complete lookback; "
        "24h direction-wide new-entry embargo across five symbols; "
        "auto-resume, new hardstop may extend; no open-position flatten"
    ),
    "thresholds_pct":[str(D(bps)/D(100)) for bps in THRESHOLDS_BPS],
    "prior_rate25_reference_sha256":hashlib.sha256(prior_bytes).hexdigest(),
    "prior_rate25_cash_ledger_signal_parity":True,
    "min_closed_trades": MIN_CLOSES,
    "window_days_requested": list(WINDOW_DAYS),
    "window_days_evaluable": list(AVAILABLE),
    "window_days_not_evaluable": list(UNAVAILABLE),
    "pause_hours":list(PAUSE_HOURS),
    "no_pause_reference_sha256_by_side":{
        side:baseline["ledger_sha256"][side] for side in SIDES
    },
    "no_pause_control_exact_trade_cash_parity":True,
    "signal_events":{
        f"{side}_B{bps}_W7":signal_events[(side,bps)]
        for side in SIDES for bps in THRESHOLDS_BPS
    },
    "scenario_events":dict(scenario_events),
    "scenario_counts":{
        case: reports[case]["counts"]
        for case in books if case not in ("CONTROL","LONG_ONLY","SHORT_ONLY")
    },
    "control_counts": reports["CONTROL"]["counts"],
    "direction_counts": {side: reports[side]["counts"] for side in SIDES},
    "direction_hardstop_events": {
        side: reports[side]["observed_hardstop_events"] for side in SIDES
    },
    "direction_pause_triggers": {
        side: reports[side]["hardstop_triggers"] for side in SIDES
    },
    "direction_censored_open": {
        side: reports[side]["censored"] for side in SIDES
    },
    "cash_models": books_money,
    "ledger_sha256": {
        side: hashlib.sha256(ledger_payloads[side]).hexdigest() for side in books
    },
    "ledger_rows": {side: len(books[side].trades) for side in books},
    "source_snapshot_sha256": engine_hashes,
    "notional_model": "1000 USDT each independent scenario, 5x leverage",
    "fee_model": "conservative taker/taker 0.055% each side",
    "unmodeled": [
        "bid_ask_spread", "funding", "latency", "exchange_liquidation",
        "position_step_rounding", "actual_margin_liquidation",
        "floating_PnL_based_available_margin",
    ],
    "research_limitations": (
        "Historical proxy; studied 90d/29d periods, not untouched OOS. "
        "No spread, funding, execution equivalence or live fill."
    ),
}
# Write only after all native source and exact reference control QC passed.
OUT.mkdir(mode=0o2770)
snapshot = OUT / "source_snapshot"
snapshot.mkdir(mode=0o2770)
for path in engine_files:
    shutil.copyfile(path, snapshot / path.name)
for side in books:
    leaf = OUT / ("trade_ledger_" + side.lower() + ".jsonl")
    leaf.write_bytes(ledger_payloads[side])
    assert hashlib.sha256(leaf.read_bytes()).hexdigest() == result["ledger_sha256"][side]
(OUT / "result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
assert json.loads((OUT / "result.json").read_text())["control_reference_trade_parity"]
# Canonical research reproducibility/launch metadata, all written in
# this new data-disk leaf only after every source/parity/accounting QC.
(OUT/"run_manifest.json").write_text(
    json.dumps(result,sort_keys=True,indent=2)+"\n")
(OUT/"source_commit.txt").write_text(ARGS.source_head+"\n")
(OUT/"command.txt").write_text(
    f"python3 -B {Path(__file__)} --period {PERIOD} "
    f"--source-head {ARGS.source_head} --smoke-hours 0\n")
(OUT/"environment.txt").write_text(json.dumps({
    "python":sys.version,"uid":os.geteuid(),"gid":os.getegid(),
    "source_head":ARGS.source_head},sort_keys=True)+"\n")
(OUT/"input_provenance.json").write_text(json.dumps({
    "old_baseline_path":str(BASELINE/"result.json"),
    "old_baseline_ledger_sha":baseline["ledger_sha256"],
    "prior_rate25_path":str(PRIOR_RATE25/"result.json"),
    "prior_rate25_sha256":hashlib.sha256(prior_bytes).hexdigest(),
    "candidate_sha256":result["candidate_sha256_by_symbol"],
    "source_input_manifest":{
        symbol:data[symbol]["manifest"]["inputs"] for symbol in SYMBOLS},
},sort_keys=True,indent=2)+"\n")
(OUT/"source_sha256.txt").write_text(
    "".join(f"{h}  source_snapshot/{filename}\n"
            for filename,h in sorted(engine_hashes.items())))
(OUT/"logs").mkdir(mode=0o2770)
(OUT/"logs"/"qc_summary.json").write_text(json.dumps({
    "status":"PASS","native_ticks":n,
    "mixed_reference_exact":True,"side_no_pause_ledger_exact":True,
    "side_no_pause_cash_exact":True,"no_flatten":True,
    "signal_counts":{f"{a}_B{b}_W7":len(signal_events[(a,b)])
                     for a in SIDES for b in THRESHOLDS_BPS},
    "prior_rate25_cash_ledger_signal_parity":True,
},sort_keys=True,indent=2)+"\n")

print(json.dumps({
    "stage": "COMPLETE", "result": str(OUT / "result.json"),
    "native_ticks": n, "control_closed": EXPECTED_CLOSED,
    "period": PERIOD,
    "counts": result["direction_counts"],
    "balances": {
        side: {
            mode: {
                "wallet": books_money[side][mode]["final_wallet_usdt"],
                "equity": books_money[side][mode]["equity_mark_to_last_trade_usdt"],
                "capital_blocked": books_money[side][mode]["blocked_count"],
            } for mode in ("fixed", "compound_capped")
        } for side in SIDES
    }
}, sort_keys=True), flush=True)
