#!/usr/bin/env python3
"""Bounded counterfactual: maker touch after 441 frozen P90L90 protected stops.

NOT a new trailing Strategy, actual Exchange maker fill, or full-portfolio replay.
Each historical protected-stop exit stays fixed. For each such exit, model a
PostOnly reduceOnly limit at that exit's native public-trade print (optimistic).
A later native trade crossing the limit is a fill PROXY; at expiry, next print
is a forced taker fallback PROXY. Baseline trail/TP/Entry/occupancy is unchanged.
This isolates possible fee savings versus adverse delay, NOT compound PnL.

Effective actor: cripta, UID 107. Immutable reads: native publicTrade,
P90L90 frozen/reference manifests, and historical SECOND48 90d trade ledger.
Create/write only one new child under approved data-disk results parent.
No DB, service, exchange, LIVE or source mirror mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D, ROUND_HALF_EVEN
from pathlib import Path

from scripts.research import p90l90_portfolio_pause_v1 as source
from scripts.research import p90l90_exit_90d_v2 as accounting

PARENT = source.ROOT / "results"
OUT_NAME = "p90l90_protected_stop_maker_probe_20261105_v1"
OUT = PARENT / OUT_NAME
CONTROL = PARENT / "entry_only_embargo_90d_20261026_v1" / "trade_ledger.jsonl"
WINDOWS = (5, 30, 60, 300)
EXPECTED_STOPS = 441
NOMINAL = D("10")
QUANTUM = D("0.000000000001")


def financial(side: str, entry: D, exit_price: D, maker: bool, entry_fee: D) -> D:
    exit_notional = NOMINAL * exit_price / entry
    gross = (exit_notional - NOMINAL) * (D(1) if side == "LONG" else D(-1))
    gross = gross.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    fee = (exit_notional * (accounting.MAKER if maker else accounting.TAKER)).quantize(
        QUANTUM, rounding=ROUND_HALF_EVEN)
    return gross - entry_fee - fee


def matches(side: str, price: D, limit: D) -> bool:
    return price >= limit if side == "LONG" else price <= limit


def load_exits() -> dict[str, list[dict]]:
    rows = defaultdict(list)
    reasons = Counter()
    control_hash = source.sha256(CONTROL)
    with CONTROL.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("portfolio_scenario") != "SECOND_48H":
                continue
            reasons[row["exit_reason"]] += 1
            if row["exit_reason"] == "PROTECTED_STOP":
                assert row["maker_exit"] is False
                rows[row["symbol"]].append(row)
    if sum(reasons.values()) != 692 or reasons["PROTECTED_STOP"] != EXPECTED_STOPS:
        raise ValueError("original common SECOND48 historical exit count mismatch")
    if any(rows[s][j]["exit_source_order"] >= rows[s][j + 1]["exit_source_order"]
           for s in rows for j in range(len(rows[s]) - 1)):
        raise ValueError("reference exit order must be strictly increasing per symbol")
    return dict(rows), reasons, control_hash


def preflight(output: Path) -> None:
    if os.geteuid() != 107:
        raise PermissionError("research runs only as cripta uid=107")
    if output != OUT or output.exists():
        raise ValueError("refuse duplicate result or unexpected output")
    if not PARENT.is_dir() or PARENT.stat().st_uid != 107:
        raise PermissionError("unexpected research parent owner/path")
    if not os.access(PARENT, os.W_OK | os.X_OK):
        raise PermissionError("no parent write/traverse for exact writer")


def run(*, output: Path | None, smoke_trades: int = 0) -> dict:
    if output is not None:
        preflight(output)
    exits, reasons, control_sha = load_exits()
    counts = {str(w): Counter() for w in WINDOWS}
    deltas = {str(w): D(0) for w in WINDOWS}
    detailed = []
    data_manifests = {}
    matched_stops = 0
    native_ticks = 0
    for symbol in source.SYMBOLS:
        data = source.inputs(symbol, source.RAW_DEFAULT, source.FROZEN_DEFAULT)
        rows = exits.get(symbol, [])
        data_manifests[symbol] = {
            "entry_events_sha256": data["manifest"]["events_sha256"],
            "frozen_states_sha256": data["manifest"]["frozen_l53_sha256"],
            "raw_files": data["manifest"]["inputs"],
        }
        pending: dict[int, dict] = {}
        idx = 0
        for t, seq, price, trade_id in source.ticks(data):
            native_ticks += 1
            if idx < len(rows) and seq == int(rows[idx]["exit_source_order"]):
                item = rows[idx]
                if t != float(item["exit_ts"]) or price != D(item["exit_price"]):
                    raise AssertionError("original exit source print differs: " + symbol)
                # The modeled order is only placed AFTER the stop-causing tick.
                pending[idx] = {
                    "row": item, "limit": price, "placed_at": t,
                    "maker": {}, "fallback": {},
                }
                matched_stops += 1
                idx += 1
            for key, attempt in tuple(pending.items()):
                row = attempt["row"]
                for window in WINDOWS:
                    if window in attempt["maker"] or window in attempt["fallback"]:
                        continue
                    # No post-facto fill on exact trigger record.
                    if seq <= int(row["exit_source_order"]):
                        continue
                    if t < attempt["placed_at"] + window and matches(
                        row["side"], price, attempt["limit"]
                    ):
                        attempt["maker"][window] = (t, attempt["limit"], trade_id)
                    elif t >= attempt["placed_at"] + window:
                        attempt["fallback"][window] = (t, price, trade_id)
                if len(attempt["maker"]) + len(attempt["fallback"]) == len(WINDOWS):
                    append_attempt(attempt, symbol, counts, deltas, detailed)
                    pending.pop(key)
            if smoke_trades and matched_stops >= smoke_trades and not pending:
                break
        for attempt in pending.values():
            append_attempt(attempt, symbol, counts, deltas, detailed)
        if smoke_trades and matched_stops >= smoke_trades:
            break
        if idx != len(rows):
            raise ValueError("not all frozen stop events located: " + symbol)
    if not smoke_trades and matched_stops != EXPECTED_STOPS:
        raise ValueError("not all 441 stop prints matched")
    result = {
        "status": "SMOKE" if smoke_trades else "RESEARCH_PENDING_INDEPENDENT_QC",
        "contract": "frozen exit-only optimistic post-stop maker-price touch, then taker fallback",
        "not_full_portfolio_replay": True,
        "period": ["2026-05-18T00:00:00Z", "2026-08-16T00:00:00Z"],
        "symbols": list(source.SYMBOLS), "nominal_per_position_usdt": "10",
        "original_reasons": dict(reasons), "original_control_sha256": control_sha,
        "matched_protected_stop_prints": matched_stops,
        "native_ticks_scanned": native_ticks,
        "seconds_waited": list(WINDOWS),
        "results": {str(w): {
            **dict(counts[str(w)]),
            "delta_usdt_after_model_fees": str(deltas[str(w)]),
            "fee_only_upper_bound_if_all_maker_usdt": str(
                sum((NOMINAL * D("0.00035") for _ in range(matched_stops)), D(0))
            ),
        } for w in WINDOWS},
        "independent_qc": "NOT_CHECKED",
        "limitations": [
            "post-trigger maker order could be rejected as crossing bid/ask",
            "native public-trade touch cannot guarantee queue fill",
            "no tickSize rounding, order book, spread or latency",
            "no re-entry/portfolio occupancy/SECOND48 recomputation",
            "does not implement 5-minute candle-based trailing",
            "taker fallback price assumes the first available native trade after expiry",
            "results cannot be scaled to 1000 USDT without depth/queue execution study",
        ]
    }
    if output is not None:
        import sys
        source_bytes = Path(__file__).read_bytes()
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        output.mkdir(mode=0o2770)
        snap = output / "source_snapshot"
        snap.mkdir(mode=0o2770)
        (snap / Path(__file__).name).write_bytes(source_bytes)
        (output / "source_sha256.txt").write_text(source_sha + "\n")
        (output / "environment.txt").write_text(
            f"uid={os.geteuid()}\npython={sys.version}\n")
        (output / "command.txt").write_text(
            "python3 -m scripts.research.p90l90_protected_stop_maker_probe_v1 --write-result\n")
        (output / "input_provenance.json").write_text(
            json.dumps(data_manifests, indent=2, sort_keys=True) + "\n")
        (output / "run_manifest.json").write_text(
            json.dumps({"source_sha256": source_sha, "control_sha256": control_sha,
                        "run_id": OUT_NAME, "control_stop_count": EXPECTED_STOPS,
                        "windows": WINDOWS}, indent=2) + "\n")
        (output / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        (output / "modeled_exit_ledger.jsonl").write_text(
            "".join(json.dumps(x, sort_keys=True) + "\n" for x in detailed))
        result["result_path"] = str(output / "result.json")
        result["result_sha256"] = source.sha256(output / "result.json")
        result["research_source_sha256"] = source_sha
    print(json.dumps(result, sort_keys=True), flush=True)
    return result


def append_attempt(attempt, symbol, counts, deltas, detailed):
    row = attempt["row"]
    base_net = D(row["net_usdt"])
    for window in WINDOWS:
        key = str(window)
        if window in attempt["maker"]:
            ts, px, trade_id = attempt["maker"][window]
            outcome = "MAKER_TOUCH_PROXY"
            maker = True
        elif window in attempt["fallback"]:
            ts, px, trade_id = attempt["fallback"][window]
            outcome = "TAKER_FALLBACK_PROXY"
            maker = False
        else:
            counts[key]["unresolved_at_period_end"] += 1
            continue
        candidate = financial(row["side"], D(row["entry_price"]), px,
                              maker, D(row["entry_fee_usdt"]))
        change = candidate - base_net
        counts[key][outcome] += 1
        if change < 0:
            counts[key]["worse_than_original"] += 1
        elif change > 0:
            counts[key]["better_than_original"] += 1
        deltas[key] += change
        detailed.append({
            "symbol": symbol, "window_seconds": window,
            "entry_ts": row["entry_ts"], "exit_ts": row["exit_ts"],
            "original_stop_source_order": row["exit_source_order"],
            "original_exit_price": row["exit_price"],
            "original_net_usdt": row["net_usdt"],
            "proxy_exit_ts": ts, "proxy_exit_price": str(px),
            "proxy_net_usdt": str(candidate),
            "delta_net_usdt": str(change), "outcome": outcome,
            "native_trade_id": trade_id,
        })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-trades", type=int, default=0)
    parser.add_argument("--write-result", action="store_true")
    args = parser.parse_args()
    if args.smoke_trades < 0 or (args.smoke_trades and args.write_result):
        raise ValueError("invalid smoke/write combination")
    run(output=OUT if args.write_result else None, smoke_trades=args.smoke_trades)


if __name__ == "__main__":
    main()
