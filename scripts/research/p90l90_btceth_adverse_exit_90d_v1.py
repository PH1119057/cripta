#!/usr/bin/env python3
"""Independent research-only P90L90 BTC+ETH adverse-move Exit full causal replay.

No production DB, Strategy, service, exchange, LIVE, or deployment operations.
Replays native ordered Bybit ticks for five symbols, with independent portfolio
occupancy per Exit candidate; exact NO_MARKET baseline parity is mandatory.
"""
from __future__ import annotations

import argparse
import csv
import heapq
import json
import os
import shutil
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path
from statistics import median

from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research import p90l90_portfolio_pause_v1 as prior
from scripts.research.p90l90_entry_90d_v2 import (
    START_TS, END_TS, RAW_DEFAULT, FROZEN_DEFAULT, SYMBOLS, sha256,
)

ROOT = prior.ROOT
MARKET = Path("/data/cripta/research/runs/market_bull_bear_timeline_20261009/results/hourly_state.csv")
RESEARCH = "P90L90_BTC_ETH_ADVERSE_EXIT_FULL_TICK_V1"
# Key: market lookback in hours, multiple of 168-hour causal self-median,
# minimum absolute open-position adverse move (%), availability delay seconds.
VARIANTS = {
    "BASELINE": None,
    "H01_X3_L2": (1, 3, 2, 0),
    "H03_X3_L2": (3, 3, 2, 0),
    "H06_X2_L2": (6, 2, 2, 0),
    "H06_X3_L2": (6, 3, 2, 0),
    "H06_X4_L1": (6, 4, 1, 0),
    "H06_X4_L2": (6, 4, 2, 0),
    "H06_X5_L1": (6, 5, 1, 0),
    "H06_X5_L2": (6, 5, 2, 0),
    "H06_X5_L2_DELAY300": (6, 5, 2, 300),
    "H06_X5_L2_DELAY3600": (6, 5, 2, 3600),
}


def build_market(path: Path):
    """Signal at fully closed hour, with 168 *previous* hourly h-returns only."""
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    ts = [int(row["ts"]) for row in rows]
    btc = [float(row["btc"]) for row in rows]
    eth = [float(row["eth"]) for row in rows]
    if len(ts) < 2300 or any(b-a != 3600 for a, b in zip(ts, ts[1:])):
        raise ValueError("BTC/ETH hourly coverage discontinuous or insufficient")
    if ts[0] > START_TS-7*86400+6*3600 or ts[-1] < END_TS:
        raise ValueError("BTC/ETH hourly coverage does not span full study")
    if any(not (x > 0 and x < float("inf")) for x in btc+eth):
        raise ValueError("invalid BTC/ETH hourly source close")
    signals = {}
    for hours in (1, 3, 6):
        returns = [None] * len(ts)
        for i in range(hours, len(ts)):
            returns[i] = (
                (btc[i]/btc[i-hours]-1)*100,
                (eth[i]/eth[i-hours]-1)*100,
            )
        flags = [None] * len(ts)
        for i in range(hours+168, len(ts)):
            earlier = returns[i-168:i]
            if len(earlier) != 168 or any(p is None for p in earlier):
                continue
            rb, re = returns[i]
            mb = median(abs(p[0]) for p in earlier)
            me = median(abs(p[1]) for p in earlier)
            if mb <= 0 or me <= 0:
                continue
            if rb < 0 and re < 0:
                flags[i] = (1, min(-rb/mb, -re/me))
            elif rb > 0 and re > 0:
                flags[i] = (-1, min(rb/mb, re/me))
        signals[hours] = flags
    return ts, signals, sha256(path)


class MarketExitPortfolio(prior.Portfolio):
    def __init__(self, variant):
        super().__init__(None)
        self.hours, self.multiple, self.loss_pct, self.delay = variant
        self.market_exits = []

    def handle_market(self, symbol, t, seq, px, candidates, state, flag):
        """Original same-tick Exit first, then optional causal BTC+ETH Exit."""
        bucket = int(t//300)*300
        current = self.pos[symbol]
        closed_here = False
        if current is not None:
            tp = None if state is None else (
                state[1] if current.side == "LONG" else state[0]
            )
            nxt, ex = base.on_trade(current, px, seq, bucket, tp, prior.KIND)
            if ex is None and flag is not None and seq > current.entry_seq:
                risk_side = 1 if current.side == "LONG" else -1
                move = base.favorable(current.side, current.entry, px)
                if (flag[0] == risk_side and flag[1] >= self.multiple
                        and move <= -D(self.loss_pct)):
                    nxt, ex = None, ("BTC_ETH_ADVERSE_EXIT", px, False)
            self.pos[symbol] = nxt
            if ex is not None:
                reason, exit_px, maker = ex
                gross, entry_fee, exit_fee, net = base.gross_and_net(
                    current, exit_px, maker
                )
                row = base.trade_record(symbol, prior.COHORT, prior.KIND,
                                        current, seq, t, exit_px, reason,
                                        maker, gross, entry_fee, exit_fee, net)
                self.trades.append(row)
                for metric, val in (
                    ("gross_usdt", gross), ("entry_fees_usdt", entry_fee),
                    ("exit_fees_usdt", exit_fee), ("net_usdt", net),
                    ("positive_net_usdt", max(net, D(0))),
                    ("negative_net_usdt", min(net, D(0))),
                ):
                    self.money[metric] += val
                self.count["closed"] += 1
                self.count["close_"+reason] += 1
                self.count["win" if net > 0 else "loss" if net < 0 else "flat"] += 1
                if reason == "BTC_ETH_ADVERSE_EXIT":
                    self.market_exits.append({
                        "symbol": symbol, "side": current.side,
                        "entry_ts": repr(current.entry_ts), "exit_ts": repr(t),
                        "exit_price": str(exit_px), "net_usdt": str(net),
                    })
                closed_here = True
        for ev in candidates:
            if not ev[prior.COHORT]:
                continue
            self.count["qualified_candidate"] += 1
            if self.pos[symbol] is not None or closed_here:
                self.count["busy_or_same_tick"] += 1
                continue
            if state is None:
                raise ValueError("accepted Entry missing frozen state")
            pos = base.initial_position(ev, seq, bucket, prior.COHORT, prior.KIND)
            tp = state[1] if pos.side == "LONG" else state[0]
            if base.take_profit_eligible(pos.side, px, tp):
                pos.tp = tp
                pos.tp_live_after_seq = seq
            self.pos[symbol] = pos
            self.count["accepted"] += 1


def process(output: Path, smoke_hours: int = 0):
    prior.preflight(output)
    if not MARKET.is_file():
        raise FileNotFoundError("hourly market data unavailable")
    market_times, signals, market_sha = build_market(MARKET)
    inputs = {s: prior.inputs(s, RAW_DEFAULT, FROZEN_DEFAULT) for s in SYMBOLS}
    books = {key: (prior.Portfolio(None) if config is None
                   else MarketExitPortfolio(config))
             for key, config in VARIANTS.items()}
    generators = {s: prior.ticks(inputs[s]) for s in SYMBOLS}
    heap = []
    for priority, s in enumerate(SYMBOLS):
        first = next(generators[s], None)
        if first is None:
            raise ValueError("empty trade source "+s)
        heapq.heappush(heap, (first[0], priority, first))
    # A closed hourly bar becomes available strictly AFTER its close timestamp.
    # Both instant and delayed market feeds have their own causal cursor.
    cursors = {0:-1, 300:-1, 3600:-1}
    limit = END_TS if not smoke_hours else START_TS + smoke_hours*3600
    last_day = None
    while heap:
        t, priority, (timestamp, seq, px, trade_id) = heapq.heappop(heap)
        if t >= limit:
            break
        symbol = SYMBOLS[priority]
        for delay in cursors:
            while (cursors[delay]+1 < len(market_times)
                   and market_times[cursors[delay]+1]+delay < t):
                cursors[delay] += 1
        event_list = inputs[symbol]["events"].get(seq, ())
        state = inputs[symbol]["states"].get(int(t//300)*300)
        for name, book in books.items():
            if event_list or book.pos[symbol] is not None:
                if name == "BASELINE":
                    book.handle(symbol, t, seq, px, event_list, state)
                else:
                    index = cursors[book.delay]
                    flag = None if index < 0 else signals[book.hours][index]
                    book.handle_market(symbol, t, seq, px, event_list, state, flag)
        day = int(t//86400)
        if day != last_day:
            last_day = day
            print(json.dumps({"phase":"REPLAY", "utc_day_epoch":day,
                              "native_tick":repr(t)}), flush=True)
        following = next(generators[symbol], None)
        if following is not None:
            heapq.heappush(heap, (following[0], priority, following))
    stats = {name: book.finish() for name, book in books.items()}
    if smoke_hours:
        return {"status":"SMOKE_PASS", "hours":smoke_hours,
                "market_sha256":market_sha,
                "counts":{k:v["counts"] for k,v in stats.items()}}
    # Exact trade-by-trade identity against independent original ledgers.
    for sym in SYMBOLS:
        actual = [row for row in books["BASELINE"].trades if row["symbol"] == sym]
        if actual != inputs[sym]["orig_rows"]:
            raise ValueError("BASELINE TRADE-BY-TRADE PARITY FAIL "+sym)
        old = inputs[sym]["orig_stat"]["money"]["net_usdt"]
        if sum((D(row["net_usdt"]) for row in actual), D(0)) != D(old):
            raise ValueError("BASELINE NET PARITY FAIL "+sym)
    if (stats["BASELINE"]["counts"]["closed"] != 730
            or stats["BASELINE"]["counts"]["close_HARD_STOP"] != 21
            or D(stats["BASELINE"]["money"]["net_usdt"]) != D("9.420400617051")):
        raise ValueError("BASELINE FIVE-COIN PARITY FAIL")
    out = {
        "research":RESEARCH, "status":"COMPLETE_HISTORICAL_PROXY_NOT_LIVE",
        "period_utc":["2026-05-18T00:00:00Z","2026-08-16T00:00:00Z"],
        "coins":list(SYMBOLS), "entry":"PREV7D", "exit":"TRAIL035",
        "model":("Exact native per-symbol tick replay, independent coin slots per "
                 "scenario, causal closed-hour BTC+ETH returns normalized to prior "
                 "168 valid hourly h-returns per coin, separate indicator lookback "
                 "and position loss gate, no same-tick close/reentry, existing "
                 "protection evaluated before additional Exit, modeled taker cost."),
        "feed_latency_seconds":"scenario-specific: 0, 300, 3600; strictly timestamp later than close+delay",
        "unmodeled":["bid_ask_spread","slippage","funding","execution_latency_beyond_feed_delay"],
        "baseline_trade_by_trade_parity":True,
        "hourly_market_sha256":market_sha,
        "source_rows_by_symbol":{s:inputs[s]["read_count"] for s in SYMBOLS},
        "entry_events_sha256_by_symbol":{s:inputs[s]["manifest"]["events_sha256"] for s in SYMBOLS},
        "reference_exit_sha256_by_symbol":{s:inputs[s]["orig_sha"] for s in SYMBOLS},
        "scenarios":stats,
    }
    output.mkdir(mode=0o770)
    source_dir = output/"source_snapshot"
    source_dir.mkdir(mode=0o770)
    source_paths = [Path(__file__), Path(prior.__file__),
                    Path(base.__file__)]
    source_hashes = {}
    for p in source_paths:
        target = source_dir/p.name
        shutil.copyfile(p, target)
        source_hashes[p.name] = sha256(target)
    out["source_snapshot_sha256"] = source_hashes
    ledger = output/"trade_ledger.jsonl"
    with ledger.open("x", encoding="utf-8") as stream:
        for name, book in books.items():
            for row in book.trades:
                stream.write(json.dumps({**row,"portfolio_scenario":name},
                                        sort_keys=True,separators=(",",":"))+"\n")
    out["ledger_sha256"] = sha256(ledger)
    out["ledger_rows"] = sum(len(b.trades) for b in books.values())
    (output/"result.json").write_text(
        json.dumps(out,sort_keys=True,indent=2)+"\n",encoding="utf-8"
    )
    return {"status":"COMPLETE", "baseline_parity":True,
            "results":{k:{"counts":v["counts"],"money":v["money"]}
                       for k,v in stats.items()},
            "output":str(output/"result.json")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--smoke-hours",type=int,default=0)
    args = parser.parse_args()
    if not 0 <= args.smoke_hours <= 24:
        raise ValueError("smoke hours out of bounds")
    result = process(args.output,args.smoke_hours)
    print(json.dumps(result,sort_keys=True),flush=True)


if __name__ == "__main__":
    main()
