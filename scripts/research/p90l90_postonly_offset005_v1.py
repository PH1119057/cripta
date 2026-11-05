#!/usr/bin/env python3
"""P90L90 five-symbol optimistic passive-entry sensitivity research (never LIVE).

READ: verified native publicTrade files, immutable P90L90 candidates/frozen states
     and historical five-coin SECOND48 control ledger.
WRITE: exactly one fresh child of existing data-disk research results;
       no runtime, DB, exchange, or source-checkout changes.
ACTOR: cripta (current approved research actor), not root/sentinelx.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import stat
from collections import Counter
from decimal import Decimal as D, ROUND_HALF_EVEN
from pathlib import Path

from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research import p90l90_portfolio_pause_v1 as previous
from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as embargo

ROOT = previous.ROOT
SYMBOLS = tuple(previous.SYMBOLS)
RESULT_PARENT = ROOT / "results"
RESULT_NAME = "p90l90_postonly_offset005_global_second48_20261105_v1"
SCENARIOS = {"NEXT_5M": None, "TTL_30M": 1800, "TTL_60M": 3600}
OFFSET = D("0.0005")
CONTROL_NET = D("14.087200729269")
CONTROL_ENTRIES = 696
CONTROL_HARDSTOPS = 15

_old_initial = base.initial_position
_old_money = base.gross_and_net


def _initial(event, seq, bucket, cohort, kind):
    position = _old_initial(event, seq, bucket, cohort, kind)
    position.maker_entry_proxy = bool(event.get("_postonly_proxy", False))
    return position


def _money(position, exit_price, maker_exit):
    gross, entry_fee, exit_fee, net = _old_money(position, exit_price, maker_exit)
    if getattr(position, "maker_entry_proxy", False):
        entry_fee = (base.NOMINAL * base.MAKER).quantize(
            base.MONEY_QUANTUM, rounding=ROUND_HALF_EVEN
        )
        net = gross - entry_fee - exit_fee
    return gross, entry_fee, exit_fee, net


class PassiveBook(embargo.EntryEmbargo):
    """One common SECOND48 event domain, independent per-symbol occupied slot."""

    def __init__(self, ttl_seconds):
        super().__init__(2, 48)
        self.ttl_seconds = ttl_seconds
        self.orders = {}
        self.extra = Counter()

    def tick(self, symbol, t, seq, price, trade_id, events, state):
        initial_position = self.pos[symbol]
        fill_candidate = ()
        pending = self.orders.get(symbol)
        if pending is not None:
            if t >= pending["expires"]:
                del self.orders[symbol]
                self.extra["expired"] += 1
            elif seq > pending["seq"] and (
                (pending["side"] == "LONG" and price <= pending["limit"])
                or (pending["side"] == "SHORT" and price >= pending["limit"])
            ):
                fill_candidate = ({
                    **pending["event"],
                    "price": str(pending["limit"]),
                    "ts": repr(t),
                    "trade_id": trade_id,
                    "_postonly_proxy": True,
                },)
                del self.orders[symbol]
                self.extra["price_touches"] += 1

        if initial_position is not None or fill_candidate:
            self.handle(symbol, t, seq, price, fill_candidate, state)

        if fill_candidate:
            if self.pos[symbol] is not None and self.pos[symbol] is not initial_position:
                self.extra["proxy_filled"] += 1
            else:
                self.extra["touch_rejected"] += 1

        for event in events:
            if not event["PREV7D"]:
                continue
            self.extra["signals"] += 1
            if initial_position is not None or self.pos[symbol] is not None or fill_candidate:
                self.extra["busy_signals"] += 1
                continue
            if symbol in self.orders:
                self.extra["pending_signals"] += 1
                continue
            if t < self.pause_until:
                self.extra["paused_signals"] += 1
                continue

            side = event["side"]
            limit = D(event["price"]) * (
                D("1") - OFFSET if side == "LONG" else D("1") + OFFSET
            )
            expiry = ((int(t // 300) + 1) * 300
                      if self.ttl_seconds is None else t + self.ttl_seconds)
            self.orders[symbol] = {
                "event": event, "seq": seq, "side": side,
                "limit": limit, "expires": min(expiry, previous.END_TS),
            }
            self.extra["placed"] += 1

    def report(self):
        result = self.finish()
        counts = result["counts"]
        money = result["money"]
        accepted = counts.get("accepted", 0)
        closed = counts.get("closed", 0)
        censored = counts.get("censored_open", 0)
        assert accepted == closed + censored == self.extra["proxy_filled"]
        assert (D(money["gross_usdt"]) - D(money["entry_fees_usdt"])
                - D(money["exit_fees_usdt"]) == D(money["net_usdt"]))
        extra = dict(self.extra)
        extra["pending_at_end"] = len(self.orders)
        return {
            "entries": accepted, "closed": closed, "censored": censored,
            "hardstops": counts.get("close_HARD_STOP", 0),
            "pause_triggers": counts.get("pause_triggers", 0),
            "gross_usdt": money["gross_usdt"],
            "entry_fees_usdt": money["entry_fees_usdt"],
            "exit_fees_usdt": money["exit_fees_usdt"],
            "net_usdt": money["net_usdt"], "orders": extra,
        }


def preflight(output):
    if os.geteuid() != 107:
        raise PermissionError("wrong research actor: expected cripta UID 107")
    if not RESULT_PARENT.is_dir():
        raise FileNotFoundError(str(RESULT_PARENT))
    s = RESULT_PARENT.stat()
    if s.st_uid != os.geteuid() or not os.access(RESULT_PARENT, os.W_OK | os.X_OK):
        raise PermissionError("research destination not writable by actor")
    if output.parent != RESULT_PARENT or output.name != RESULT_NAME:
        raise ValueError("output must be the exact approved research child")
    if output.exists():
        raise FileExistsError("refuse duplicate/restart over existing research run")


def run(*, smoke_ticks=0, output=None):
    if output is not None:
        preflight(output)
    base.initial_position = _initial
    base.gross_and_net = _money

    data = {s: previous.inputs(s, previous.RAW_DEFAULT, previous.FROZEN_DEFAULT)
            for s in SYMBOLS}
    control = embargo.EntryEmbargo(2, 48)
    books = {name: PassiveBook(ttl) for name, ttl in SCENARIOS.items()}
    streams = {s: previous.ticks(data[s]) for s in SYMBOLS}
    heap = []
    for index, symbol in enumerate(SYMBOLS):
        tick = next(streams[symbol], None)
        if tick is None:
            raise ValueError("missing raw publicTrade source: " + symbol)
        heapq.heappush(heap, (tick[0], index, tick))
    ticks = 0
    while heap:
        _, index, (t, seq, price, trade_id) = heapq.heappop(heap)
        symbol = SYMBOLS[index]
        events = data[symbol]["events"].get(seq, ())
        state = data[symbol]["states"].get(int(t // 300) * 300)

        if events or control.pos[symbol] is not None:
            control.handle(symbol, t, seq, price, events, state)
        for book in books.values():
            if events or book.pos[symbol] is not None or symbol in book.orders:
                book.tick(symbol, t, seq, price, trade_id, events, state)

        ticks += 1
        if ticks % 10_000_000 == 0:
            print(json.dumps({"phase": "PROGRESS", "ticks": ticks}), flush=True)
        if smoke_ticks and ticks >= smoke_ticks:
            break
        nxt = next(streams[symbol], None)
        if nxt is not None:
            heapq.heappush(heap, (nxt[0], index, nxt))

    if smoke_ticks:
        response = {
            "phase": "SMOKE", "status": "PASS", "ticks": ticks,
            "scenarios": {
                key: {"placed": book.extra["placed"],
                      "filled": book.extra["proxy_filled"],
                      "expired": book.extra["expired"],
                      "position_slots": sum(p is not None for p in book.pos.values())}
                for key, book in books.items()
            },
        }
        print(json.dumps(response, sort_keys=True), flush=True)
        return response

    refdir = ROOT / "results" / "entry_only_embargo_90d_20261026_v1"
    reference = json.loads((refdir / "result.json").read_text())
    original = [json.loads(line) for line in
                (refdir / "trade_ledger.jsonl").open()
                if '"portfolio_scenario":"SECOND_48H"' in line]
    for row in original:
        row.pop("portfolio_scenario")
    if control.trades != original:
        raise AssertionError("full five-coin baseline trade-by-trade parity FAIL")
    check = control.finish()
    if (check["counts"]["accepted"] != CONTROL_ENTRIES or
        check["counts"]["close_HARD_STOP"] != CONTROL_HARDSTOPS or
        D(check["money"]["net_usdt"]) != CONTROL_NET or
        D(reference["net_pnl_usdt"]["SECOND_48H"]) != CONTROL_NET):
        raise AssertionError("five-coin baseline aggregate parity FAIL")

    reports = {key: book.report() for key, book in books.items()}
    for row in reports.values():
        row["delta_vs_taker_usdt"] = str(D(row["net_usdt"]) - CONTROL_NET)
    result = {
        "phase": "FINAL", "status": "RESEARCH_RESULT_PENDING_INDEPENDENT_QC",
        "ticks": ticks, "period_utc": ["2026-05-18T00:00:00Z", "2026-08-16T00:00:00Z"],
        "symbols": SYMBOLS, "one_common_second48": True,
        "original_ledger_parity": "PASS", "fees_reconciliation": "PASS",
        "control": {"entries": CONTROL_ENTRIES, "hardstops": CONTROL_HARDSTOPS,
                    "net_usdt": str(CONTROL_NET)},
        "variants": reports, "independent_qc": "NOT_CHECKED",
        "fill_semantics": "optimistic next public trade touch at ideal unrounded limit",
        "limitations": ["no orderbook or queue", "no tickSize rounding",
                        "maker acceptance unproven", "spread/slippage/funding/latency unmodeled"],
    }
    if output is not None:
        output.mkdir(mode=0o2770)
        target = output / "result.json"
        with target.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        result["result_path"] = str(target)
        result["result_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    print(json.dumps(result, sort_keys=True), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-ticks", type=int, default=0)
    parser.add_argument("--write-result", action="store_true")
    arguments = parser.parse_args()
    if arguments.smoke_ticks < 0:
        raise ValueError("negative smoke ticks")
    if arguments.smoke_ticks and arguments.write_result:
        raise ValueError("smoke must never write output")
    output = RESULT_PARENT / RESULT_NAME if arguments.write_result else None
    run(smoke_ticks=arguments.smoke_ticks, output=output)


if __name__ == "__main__":
    main()
