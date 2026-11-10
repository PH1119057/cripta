#!/usr/bin/env python3
"""2025 P90L90 per-direction native portfolio, SECOND48 and W7H24 thresholds.

Reuses original immutable TRAIL035/RAW-opposite-L5 TP/-10% HARD_STOP,
EntryEmbargo, per-tick source ordering and EXACT 2026 original 1000USDT/5x
cash model. No retune, DB, StrategyCard, Exchange, deploy or LIVE.

Side-specific stream allows independent bounded jobs. 2026 exact ledger+cash
parity is mandatory before any 2025 financial replay is eligible.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import gzip
import hashlib
import heapq
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict, deque
from decimal import Decimal as D, getcontext
from pathlib import Path

getcontext().prec = 38
SOURCE = Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as embargo  # noqa: E402
from scripts.research import p90l90_exit_90d_v2 as base  # noqa: E402
from scripts.research import p90l90_bull2025_entry_v1 as entry  # noqa: E402

ROOT = Path("/data/cripta/research/runs/p90l90_bull_2025_holdout_v1")
RESULTS = ROOT / "results"
OLD = Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/results")
SYMBOLS = tuple(embargo.SYMBOLS)
SIDES = ("LONG_ONLY", "SHORT_ONLY")
THRESHOLDS_BPS = (150, 200, 250, 300, 350, 400)
MIN_CLOSES = 20
MODE_ROOT = {
    "parity-2026": "portfolio_parity_2026_v1",
    "full-2025": "portfolio_full_2025_v1",
}
SELF_REL = "scripts/research/p90l90_bull2025_portfolio_v1.py"
CHECKED_SOURCE = (
    "scripts/research/p90l90_entry_only_hardstop_embargo_v1.py",
    "scripts/research/p90l90_exit_90d_v2.py",
    "scripts/research/p90l90_rate_threshold_w7h24_20261110_v1.py",
)
RATE = D("0.00055")
BEG = D(0)
END = D(0)
books = {}
reports = {}
last_prices = {}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def github_source_head() -> str:
    return subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], check=True,
        capture_output=True, text=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    ).stdout.strip()


def preflight(mode: str, side: str, commit: str) -> Path:
    if os.geteuid() != 107 or github_source_head() != commit:
        raise PermissionError("exact cripta actor and source main HEAD needed")
    if len(commit) != 40 or any(x not in "0123456789abcdef" for x in commit):
        raise ValueError("invalid source HEAD")
    if mode not in MODE_ROOT or side not in SIDES or SYMBOLS != (
        "LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT"
    ):
        raise ValueError("frozen direction/cohort/period mismatch")
    raw = json.loads((ROOT / "run_manifest.json").read_text())
    if raw.get("status") != "INPUT_COMPLETE_QC_PASS_NO_STRATEGY_REPLAY":
        raise RuntimeError("2025 raw source not verified")
    for rel in CHECKED_SOURCE:
        if sha(SOURCE / rel) != raw["source_snapshot_sha256"][rel]:
            raise RuntimeError("immutable old P90L90 trading research source drifted")
    if not RESULTS.is_dir() or RESULTS.is_symlink() or RESULTS.stat().st_uid != 107:
        raise PermissionError("research data-disk results owner mismatch")
    if not os.access(RESULTS, os.W_OK | os.X_OK):
        raise PermissionError("research results inaccessible to cripta")
    if shutil.disk_usage(RESULTS).free < 15 * 2**30:
        raise RuntimeError("15GiB data-disk reserve breached")
    output = RESULTS / MODE_ROOT[mode] / side.lower()
    if output.exists() or output.is_symlink() or output.parent.is_symlink():
        raise FileExistsError("immutable portfolio output collision")
    if output.parent.exists() and (
        output.parent.stat().st_uid != 107
        or not os.access(output.parent, os.W_OK | os.X_OK)
    ):
        raise PermissionError("portfolio output parent not cripta owned/writable")
    coins_mode = "parity-2026" if mode == "parity-2026" else "full-2025"
    for symbol in SYMBOLS:
        path = RESULTS / entry.OUT_DIRS[coins_mode] / symbol / "qc.json"
        if not path.is_file():
            raise RuntimeError("missing exact 2026/2025 Entry candidate gate: "+symbol)
        candidate = json.loads(path.read_text())
        good = ("EXACT_2026_CANDIDATE_LEDGER_PARITY_PASS"
                if mode == "parity-2026" else "QC_PASS_2025_ENTRY_CANDIDATE_ONLY_NO_EXITS")
        if candidate.get("status") != good:
            raise RuntimeError("Entry gate not met for "+symbol)
    if mode == "full-2025":
        for prior in SIDES:
            ref = RESULTS / MODE_ROOT["parity-2026"] / prior.lower() / "qc.json"
            if not ref.is_file() or json.loads(ref.read_text()).get("status") != "EXACT_2026_PORTFOLIO_LEDGER_CASH_PARITY_PASS":
                raise RuntimeError("both 2026 side portfolio parity gates required")
    return output


def load(symbol: str, mode: str, start: datetime.datetime,
         end: datetime.datetime) -> dict:
    if mode == "parity-2026":
        raw = Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/inputs")
        frozen = Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/results/l53_clean_v2/parts")
    else:
        raw = ROOT / "inputs"
        frozen = RESULTS / "l53_geometry_full_2025_v1/parts"
    candidates = RESULTS / entry.OUT_DIRS[mode] / symbol
    manifest = json.loads((candidates / "manifest.json").read_text())
    qc = json.loads((candidates / "qc.json").read_text())
    events_file = candidates / f"candidates_{symbol}.jsonl"
    if sha(events_file) != manifest["events_sha256"] or (
        qc["events_sha256"] != manifest["events_sha256"]
    ):
        raise ValueError("frozen Entry candidate SHA mismatch")
    states_file = frozen / f"states_{symbol}.csv.gz"
    if sha(states_file) != manifest["frozen_l53_sha256"]:
        raise ValueError("frozen L5 state SHA mismatch")
    states, _, prev7, meta = entry.load_frozen_period(states_file, start, end)
    if len(states) != int((end-start).days)*288 or len(prev7) != int((end-start).days):
        raise ValueError("missing complete causal L5/PREV7D state coverage")
    events, _ = base.read_events(events_file, symbol)
    if sum(map(len, events.values())) != manifest["events"]:
        raise ValueError("missing or duplicate raw Entry candidate")
    return {
        "symbol": symbol, "raw_root": raw, "manifest": manifest,
        "events": events, "states": states, "seen_rows": 0,
        "seen_events": 0, "eval_rows": 0, "event_sha256": manifest["events_sha256"]
    }


def native_ticks(data: dict, start_ts: int, end_ts: int):
    """Identical native original gzip tick index, including warmup source offsets."""
    symbol = data["symbol"]
    seq = 0
    last_ts = None
    for item in data["manifest"]["inputs"]:
        path = data["raw_root"] / symbol / "public_trades" / item["file"]
        if sha(path) != item["sha256"]:
            raise ValueError("immutable native raw trade source hash drift")
        day_rows = 0
        with gzip.open(path, "rt", newline="") as stream:
            for row in csv.DictReader(stream):
                seq += 1
                day_rows += 1
                ts = float(row["timestamp"])
                if last_ts is not None and ts < last_ts:
                    raise ValueError("Bybit raw timestamp moved backward")
                last_ts = ts
                if ts < start_ts:
                    continue
                if ts >= end_ts:
                    raise ValueError("native raw source beyond study END")
                px = D(row["price"])
                events = data["events"].get(seq, ())
                for ev in events:
                    if (ev["trade_id"] != row["trdMatchID"]
                        or float(ev["ts"]) != ts or D(ev["price"]) != px):
                        raise ValueError("candidate source-order and tick id parity FAIL")
                data["seen_events"] += len(events)
                data["eval_rows"] += 1
                yield ts, seq, px, row["trdMatchID"]
        if day_rows != int(item["rows"]):
            raise ValueError("native source day row-count mismatch: "+path.name)
    data["seen_rows"] = seq
    if seq != sum(int(x["rows"]) for x in data["manifest"]["inputs"]):
        raise ValueError("native source total count mismatch")
    if data["seen_events"] != sum(map(len, data["events"].values())):
        raise ValueError("not all Entry candidate source ticks replayed")


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

def run(mode: str, side: str, commit: str, smoke_hours: int) -> dict:
    global books, reports, last_prices, BEG, END
    if not 0 <= smoke_hours <= 24:
        raise ValueError("smoke 0..24h")
    output = preflight(mode, side, commit)
    first, last, _, _ = entry.EVALUATION[mode]
    start = datetime.datetime.fromisoformat(first)
    end = datetime.datetime.fromisoformat(last)
    start_ts, end_ts = int(start.timestamp()), int(end.timestamp())
    BEG, END = D(start_ts), D(end_ts)
    data = {s: load(s, mode, start, end) for s in SYMBOLS}
    streams = {s: native_ticks(data[s], start_ts, end_ts) for s in SYMBOLS}
    heap = []
    for priority, symbol in enumerate(SYMBOLS):
        first_row = next(streams[symbol], None)
        if first_row is None:
            raise ValueError("empty native source "+symbol)
        heapq.heappush(heap, (first_row[0], priority, first_row))
    books = {side: embargo.EntryEmbargo(0, None),
             f"{side}_SECOND48": embargo.EntryEmbargo(2, 48)}
    books.update({
        f"{side}_B{bps}_W7_H24": embargo.EntryEmbargo(0, None)
        for bps in THRESHOLDS_BPS
    })
    report_events = defaultdict(list)
    signal_events = {bps: [] for bps in THRESHOLDS_BPS}
    rolling = deque()
    n = 0
    last_prices = {}
    previous_day = None
    qualifier = Counter()
    limit = end_ts if smoke_hours == 0 else start_ts+smoke_hours*3600
    while heap:
        t, idx, (ts, seq, px, trade_id) = heapq.heappop(heap)
        if t >= limit:
            break
        symbol = SYMBOLS[idx]
        all_events = data[symbol]["events"].get(seq, ())
        direction_events = tuple(
            e for e in all_events if e["side"] == side.split("_")[0]
        )
        qualifier[symbol] += sum(bool(e.get("PREV7D")) for e in direction_events)
        state = data[symbol]["states"].get(int(t//300)*300)
        shadow = books[side]
        before = shadow.count["closed"]
        if direction_events or shadow.pos[symbol] is not None:
            shadow.handle(symbol, t, seq, px, direction_events, state)
        if shadow.count["closed"] != before:
            assert shadow.count["closed"] == before+1
            close = shadow.trades[-1]
            assert close["symbol"] == symbol
            assert close["exit_source_order"] == seq
            assert float(close["exit_ts"]) == t
            hard = close["exit_reason"] == "HARD_STOP"
            while rolling and rolling[0][0] <= t - 7*86400:
                rolling.popleft()
            rolling.append((t,int(hard)))
            if hard and t >= start_ts + 7*86400 and len(rolling) >= MIN_CLOSES:
                stopped = sum(x[1] for x in rolling)
                for bps in THRESHOLDS_BPS:
                    if stopped * 10000 < len(rolling) * bps:
                        continue
                    event = {
                        "ts": repr(t), "symbol": symbol, "source_order": seq,
                        "lookback_days": 7, "closed_trades": len(rolling),
                        "hardstops": stopped,
                        "rate_pct": str(D(100)*D(stopped)/D(len(rolling))),
                        "threshold_pct": str(D(bps)/D(100)),
                    }
                    signal_events[bps].append(event)
                    name = f"{side}_B{bps}_W7_H24"
                    book = books[name]
                    previous = book.pause_until
                    book.pause_until = max(previous, t + 24*3600)
                    book.count["signal_triggers"] += 1
                    if t >= previous:
                        book.count["pause_starts"] += 1
                    elif book.pause_until > previous:
                        book.count["pause_extensions"] += 1
                    report_events[name].append({
                        **event, "pause_hours": 24,
                        "previous_pause_end": repr(previous),
                        "new_pause_end": repr(book.pause_until),
                    })
        for bps in THRESHOLDS_BPS:
            name = f"{side}_B{bps}_W7_H24"
            book = books[name]
            if direction_events or book.pos[symbol] is not None:
                book.handle(symbol, t, seq, px, direction_events, state)
        second = books[f"{side}_SECOND48"]
        if direction_events or second.pos[symbol] is not None:
            second.handle(symbol, t, seq, px, direction_events, state)
        last_prices[symbol] = px
        n += 1
        day = int(t//86400)
        if previous_day != day:
            previous_day = day
            print(json.dumps({
                "stage": "REPLAY", "mode": mode, "side": side,
                "utc_day": datetime.datetime.fromtimestamp(
                    t, datetime.timezone.utc
                ).strftime("%Y-%m-%d"),
                "ticks": n, "shadow_closed": shadow.count["closed"],
                "shadow_hardstops": shadow.count["close_HARD_STOP"],
            }), flush=True)
        nxt = next(streams[symbol], None)
        if nxt is not None:
            heapq.heappush(heap, (nxt[0], idx, nxt))
    if smoke_hours:
        print(json.dumps({"stage":"SMOKE_PASS_NO_WRITES", "side":side,
                          "ticks":n,"hours":smoke_hours}),flush=True)
        return {"status":"SMOKE_PASS_NO_WRITES","ticks":n}
    expected_ticks = 0
    for symbol in SYMBOLS:
        d = data[symbol]
        expected = sum(
            int(f["rows"]) for f in d["manifest"]["inputs"]
            if f["file"][len(symbol):len(symbol)+10] >= start.date().isoformat()
        )
        assert d["seen_rows"] == sum(int(f["rows"]) for f in d["manifest"]["inputs"])
        assert d["seen_events"] == sum(map(len,d["events"].values()))
        assert d["eval_rows"] == expected
        expected_ticks += expected
    if n != expected_ticks:
        raise RuntimeError(f"raw native coverage mismatch {n}!={expected_ticks}")
    if mode == "full-2025" and n <= 68988768:
        raise RuntimeError("2025 183d evaluation must not equal 90d tick coverage")
    reports = {name: book.finish() for name, book in books.items()}
    assert reports[side]["counts"]["qualified_candidate"] == sum(qualifier.values())
    for name in books:
        assert reports[name]["counts"]["qualified_candidate"] == sum(qualifier.values())
        assert not books[name].pending
        assert all(row["side"]==side.split("_")[0] for row in books[name].trades)
    money = {}
    ledgers = {}
    for name in books:
        models = {}
        for cap in ("fixed","compound_capped"):
            c = cash(name,cap)
            c["capital_admission_warning"] = (
                c["blocked_count"] > 0 or D(c["min_free_margin_usdt"]) < 0
            )
            models[cap]=c
        money[name]=models
        ledgers[name]="".join(
            json.dumps(row,sort_keys=True,separators=(",",":"))+"\n"
            for row in books[name].trades
        ).encode()
    if mode == "parity-2026":
        prior = json.loads((OLD/"p90l90_rate_threshold_w7h24_20261110_v1/result.json").read_text())
        if prior["raw_native_ticks_evaluated"] != n or (
            reports[side]["counts"] != prior["direction_counts"][side]
        ):
            raise RuntimeError("2026 no-pause direction closed-trade parity FAILED")
        for name in books:
            if name.endswith("_SECOND48"):
                ref = OLD / (f"p90l90_{side.split('_')[0].lower()}only_full90_20261109_v1")
                old_result = json.loads((ref/"result.json").read_text())
                want_hash = old_result[f"{side.split('_')[0].lower()}_only_ledger_sha256"]
                if reports[name]["counts"] != old_result[f"{side.split('_')[0].lower()}_only_counts"]:
                    raise RuntimeError("SECOND48 2026 counts parity failed")
                ref_cash = old_result["cash_models"]
            else:
                want_hash = prior["ledger_sha256"][name]
                want_count = (
                    prior["direction_counts"][side] if name == side
                    else prior["scenario_counts"][name]
                )
                if reports[name]["counts"] != want_count:
                    raise RuntimeError("W7H24 2026 counts parity FAILED "+name)
                ref_cash = prior["cash_models"][name]
            if hashlib.sha256(ledgers[name]).hexdigest() != want_hash:
                raise RuntimeError("2026 trade ledger exact SHA mismatch "+name)
            for model in ("fixed","compound_capped"):
                for field in ("final_wallet_usdt","equity_mark_to_last_trade_usdt",
                              "hypothetical_equity_after_taker_close_usdt"):
                    if money[name][model][field] != ref_cash[model][field]:
                        raise RuntimeError("2026 exact money parity failed "+name+":"+model+":"+field)
        for bps in THRESHOLDS_BPS:
            ref = prior["signal_events"][f"{side}_B{bps}_W7"]
            if signal_events[bps] != ref:
                raise RuntimeError("2026 W7H24 exact trigger time/event parity failure")
        status="EXACT_2026_PORTFOLIO_LEDGER_CASH_PARITY_PASS"
    else:
        status="COMPLETE_2025_HISTORICAL_PROXY_NOT_LIVE"
    # Write only once every native tick, Exit ledger, rate signal and
    # 2026 reference parity completed; research data disk only.
    output.parent.mkdir(mode=0o2770,exist_ok=True)
    output.mkdir(mode=0o2770,exist_ok=False)
    snapshot=output/"source_snapshot"
    snapshot.mkdir(mode=0o2770)
    source_hashes={}
    for rel in (SELF_REL,*CHECKED_SOURCE,"scripts/research/p90l90_bull2025_entry_v1.py"):
        orig=SOURCE/rel;dest=snapshot/orig.name
        shutil.copyfile(orig,dest)
        source_hashes[dest.name]=sha(dest)
        if sha(orig)!=source_hashes[dest.name]:
            raise RuntimeError("research source snapshot drift")
    ledger_hashes={}
    for name,raw in ledgers.items():
        file=output/f"trade_ledger_{name.lower()}.jsonl"
        file.write_bytes(raw)
        ledger_hashes[name]=sha(file)
        if ledger_hashes[name]!=hashlib.sha256(raw).hexdigest():
            raise RuntimeError("written ledger hash mismatch")
    result={
        "research":"P90L90_2025_BULL_PREV7D_SECOND48_W7H24_V1",
        "status":status,"period":mode,"direction":side,
        "period_utc":[first,last],"symbols":list(SYMBOLS),
        "input_source_commit":commit,"native_ticks_evaluated":n,
        "source_eval_rows_by_symbol":{s:data[s]["eval_rows"] for s in SYMBOLS},
        "candidate_sha256_by_symbol":{s:data[s]["event_sha256"] for s in SYMBOLS},
        "scenario_counts":{name:reports[name]["counts"] for name in books},
        "hardstop_events":reports[side]["observed_hardstop_events"],
        "pause_policy":"W7H24: shadow >= threshold after NEW hardstop, min20 closes, complete 7d maturity, 24h pause only new Entry, auto resume; SECOND48: scenario 2nd hardstop in 48h ->48h pause; no flatten",
        "thresholds_pct":[str(D(b)/D(100)) for b in THRESHOLDS_BPS],
        "signal_events":{f"{side}_B{b}_W7":signal_events[b] for b in THRESHOLDS_BPS},
        "scenario_events":dict(report_events),
        "cash_models":money,"ledger_sha256":ledger_hashes,
        "source_snapshot_sha256":source_hashes,
        "fee_model":"0.055% taker entry + 0.055% taker exit, 5x, 1000USDT independent wallet/side/scenario",
        "unmodeled":["bid_ask_spread","funding","latency","actual_slippage","exchange_liquidation","position_step_rounding"],
        "research_limitations":"Regime-conditioned known bullish calendar window; not blind untouched OOS; historical proxy with separate open positions censored at end.",
    }
    with (output/"result.json").open("x") as f:
        json.dump(result,f,sort_keys=True,indent=2);f.write("\n")
    with (output/"qc.json").open("x") as f:
        json.dump({"status":status,"native_ticks":n,
                   "source_head":commit,"all_parity":mode=="parity-2026",
                   "source_sha256":source_hashes,"ledger_sha256":ledger_hashes},
                  f,sort_keys=True,indent=2);f.write("\n")
    (output/"source_commit.txt").write_text(commit+"\n")
    print(json.dumps({"stage":"PORTFOLIO_REPLAY_COMPLETE_QC_PASS","mode":mode,
                      "direction":side,"native_ticks":n,
                      "equity_by_book":{n:round(float(m["compound_capped"]["equity_mark_to_last_trade_usdt"]),2) for n,m in money.items()},
                      "status":status}),flush=True)
    return result


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--mode",choices=MODE_ROOT,required=True)
    p.add_argument("--direction",choices=SIDES,required=True)
    p.add_argument("--source-head",required=True)
    p.add_argument("--smoke-hours",type=int,default=0)
    a=p.parse_args()
    run(a.mode,a.direction,a.source_head,a.smoke_hours)


if __name__ == "__main__":
    main()
