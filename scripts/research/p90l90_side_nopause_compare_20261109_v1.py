#!/usr/bin/env python3
"""Research-only 90d/29d independent LONG/SHORT NO_PAUSE native-tick replay.

Filters side before slot/Entry decisions. Preserves frozen P90L90 PREV7D
H9 candidates, TRAIL035, L53 opposite TP and -10% hard stop. Disables only
global SECOND48 new-Entry embargo with EntryEmbargo(0, None).
Verifies exact historical mixed no-pause BASELINE trade-by-trade parity.
Fixed 200 or 20% realized-wallet capped margin, 5x, taker/taker 0.055%
each side. Historical proxy, no live Exchange fill, spread, funding.
Writer cripta UID107: only CREATE new immutable results child and WRITE
result, two side trade ledgers, source_snapshot on /data/cripta/research.
No DB, systemd, trading runtime, Strategy mutation, RENAME or DELETE.
Bounded smoke has zero filesystem writes.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import heapq
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from decimal import Decimal as D, getcontext
from pathlib import Path

getcontext().prec = 38
sys.path.insert(0, "/srv/cripta/source_checkout")
from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as old  # noqa: E402

SYMBOLS = tuple(old.SYMBOLS)
SIDES = ("LONG_ONLY", "SHORT_ONLY")
RESEARCH = "P90L90_SIDE_NOPAUSE_VS_SECOND48_NATIVE_REPLAY_V1"

CLI = argparse.ArgumentParser()
CLI.add_argument("--source-head", required=True)
CLI.add_argument("--period", choices=("90d", "29d"), required=True)
CLI.add_argument("--smoke-hours", type=int, default=0)
ARGS = CLI.parse_args()
PERIOD = ARGS.period
ROOT = old.previous.ROOT if PERIOD == "90d" else old.sources29.ROOT
RESULTS = ROOT / "results"
OUT = RESULTS / "p90l90_side_nopause_compare_20261109_v1"
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
        if direction_events or books[book_name].pos[symbol] is not None:
            books[book_name].handle(symbol, t, seq, px, direction_events, state)
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
print(json.dumps({
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
for book_name in SIDES:
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
    "pause_policy": "NO_PAUSE: EntryEmbargo(0,None); original hardstop retained",
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
        side: hashlib.sha256(ledger_payloads[side]).hexdigest() for side in SIDES
    },
    "ledger_rows": {side: len(books[side].trades) for side in SIDES},
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
for side in SIDES:
    leaf = OUT / ("trade_ledger_" + side.lower() + ".jsonl")
    leaf.write_bytes(ledger_payloads[side])
    assert hashlib.sha256(leaf.read_bytes()).hexdigest() == result["ledger_sha256"][side]
(OUT / "result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
assert json.loads((OUT / "result.json").read_text())["control_reference_trade_parity"]
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
