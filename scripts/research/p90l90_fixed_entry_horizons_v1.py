#!/usr/bin/env python3
"""P90L90 90d: time-exit original accepted entries, without replacement entries.

RESEARCH-ONLY FILESYSTEM CONTRACT:
actor cripta UID107; read historical Entry/Exit and native public trades.
CREATE/WRITE only a unique --output leaf under the existing 90d research results
directory; no RENAME/DELETE, database, service, production or LIVE actions.
Uses first actual ordered Bybit tick >= entry+H, not OHLC or interpolation.
Original trades closed strictly before age H retain their original exit.
Archive-right-censored positions remain right-censored if H is out of range.
This is a FIXED ENTRY diagnostic, not the sequential slot-releasing replay.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research.p90l90_entry_90d_v2 import (
    END_TS,
    RAW_DEFAULT,
    SYMBOLS,
    sha256,
)

ROOT = Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024")
HOURS = (3, 6, 12, 18, 24, 36, 48)
SOURCE = "PREV7D_TRAIL035"
EXPERIMENT = "P90L90_90D_FIXED_ENTRY_TIME_CAP_V1"


def existing_entry(symbol: str, entry_root: Path, exit_root: Path):
    manifest = json.loads((entry_root / "manifest.json").read_text())
    if manifest["symbol"] != symbol or not manifest["complete_90d_input"]:
        raise ValueError("wrong original 90d frozen entry")
    original = json.loads((exit_root / "result.json").read_text())
    ledger = exit_root / f"trade_ledger_{symbol}.jsonl"
    if sha256(ledger) != original["trade_ledger_sha256"]:
        raise ValueError("old exit ledger SHA mismatch")
    rows = [
        json.loads(line)
        for line in ledger.open()
        if json.loads(line)["scenario"] == SOURCE
    ]
    info = original["scenarios"][SOURCE]
    census = info.get("censored_position")
    if len(rows) != info["counts"]["closed"]:
        raise ValueError("baseline closed count mismatch")
    if len(rows) + int(census is not None) != info["counts"]["accepted"]:
        raise ValueError("baseline accepted count mismatch")
    if sum((D(x["net_usdt"]) for x in rows), D(0)) != D(info["money"]["net_usdt"]):
        raise ValueError("baseline net mismatch")
    return manifest, original, rows, census


def preflight(output: Path) -> None:
    if os.geteuid() != 107:
        raise PermissionError("research actor must be cripta uid107")
    if not output.is_absolute() or output.exists():
        raise FileExistsError("unique absolute output must not exist")
    parent = ROOT / "results"
    if output.parent.resolve() != parent:
        raise ValueError("output must be direct leaf of approved results")
    if parent.stat().st_uid != 107 or not os.access(parent, os.W_OK | os.X_OK):
        raise PermissionError("research parent not writable by cripta")


def make_tasks(rows: list[dict], census: dict | None) -> tuple:
    """Return per-horizon prior exits and sorted first-tick sampling deadlines."""
    hmap = {h: [] for h in HOURS}
    tasks = []
    entries = rows + ([census] if census is not None else [])
    for i, pos in enumerate(entries):
        opened = float(pos["entry_ts"])
        closed_ts = float(pos["exit_ts"]) if "exit_ts" in pos else None
        for h in HOURS:
            due = opened + h * 3600
            if closed_ts is not None and closed_ts < due:
                hmap[h].append(("ORIGINAL", i, pos))
            elif due >= END_TS:
                hmap[h].append(("RIGHT_CENSORED", i, pos))
            else:
                tasks.append((due, i, h))
    tasks.sort()
    return entries, hmap, tasks


def sample_first_ticks(symbol: str, manifest: dict, tasks: list, raw: Path):
    """Sparse deadline scan, hashes every used source day before opening."""
    sampled = {}
    idx = 0
    accessed = []
    for info in manifest["inputs"]:
        if idx >= len(tasks):
            break
        match = re.search(r"(\d{4}-\d{2}-\d{2})\.csv\.gz$", info["file"])
        if match is None:
            raise ValueError("unknown native trade day filename")
        day = datetime.strptime(match.group(1), "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        day_end = day.timestamp() + 86400
        if tasks[idx][0] >= day_end:
            continue
        file = raw / symbol / "public_trades" / info["file"]
        if sha256(file) != info["sha256"]:
            raise ValueError("native trade file SHA mismatch: " + str(file))
        accessed.append(info["file"])
        with gzip.open(file, "rt", newline="") as stream:
            for row in csv.DictReader(stream):
                ts = float(row["timestamp"])
                if ts >= END_TS:
                    break
                while idx < len(tasks) and tasks[idx][0] <= ts:
                    due, original_idx, hours = tasks[idx]
                    sampled[(original_idx, hours)] = (
                        ts, row["price"], row["trdMatchID"], info["file"]
                    )
                    idx += 1
                    if idx >= len(tasks):
                        break
                if idx >= len(tasks):
                    break
    if idx != len(tasks):
        raise ValueError(f"missing first tick for {len(tasks) - idx} deadlines")
    return sampled, accessed


def original_financial(pos: dict):
    return {
        "reason": pos["exit_reason"],
        "net": D(pos["net_usdt"]),
        "fee_in": D(pos["entry_fee_usdt"]),
        "fee_out": D(pos["exit_fee_usdt"]),
        "gross": D(pos["gross_usdt"]),
    }


def run(symbol: str, raw: Path, output: Path) -> dict:
    preflight(output)
    entry = ROOT / "results" / f"entry_{symbol[:-4]}_FULL90"
    old_exit = ROOT / "results" / f"exit_{symbol[:-4]}_FULL90"
    manifest, original, rows, census = existing_entry(symbol, entry, old_exit)
    positions, prior_exits, tasks = make_tasks(rows, census)
    sampled, accessed = sample_first_ticks(symbol, manifest, tasks, raw)

    report = {
        "research": EXPERIMENT,
        "symbol": symbol,
        "status": "HISTORICAL_TICK_PROXY_NO_EXCHANGE_FILL",
        "source_entry_sha256": manifest["events_sha256"],
        "source_exit_sha256": original["trade_ledger_sha256"],
        "original_closed": len(rows),
        "original_censored": int(census is not None),
        "baseline_net_usdt": original["scenarios"][SOURCE]["money"]["net_usdt"],
        "same_original_entries": len(positions),
        "native_days_sampled": len(accessed),
        "source_sample_days": accessed,
        "native_deadlines_sampled": len(tasks),
        "unmodeled": ["funding", "slippage", "spread", "latency"],
        "scenarios": {},
    }
    for h in HOURS:
        reasons = defaultdict(lambda: {"n": 0, "winners": 0, "losers": 0, "net": D(0)})
        money = defaultdict(lambda: D(0))
        count = Counter()
        for mode, i, pos in prior_exits[h]:
            if mode == "RIGHT_CENSORED":
                count["censored_open"] += 1
                continue
            f = original_financial(pos)
            stat = reasons[f["reason"]]
            stat["n"] += 1
            stat["winners"] += int(f["net"] > 0)
            stat["losers"] += int(f["net"] < 0)
            stat["net"] += f["net"]
            count["closed"] += 1
            count["original_early_exits"] += 1
            count["winners"] += int(f["net"] > 0)
            count["losers"] += int(f["net"] < 0)
            for label, v in (
                ("net", f["net"]), ("gross", f["gross"]),
                ("entry_fees", f["fee_in"]), ("exit_fees", f["fee_out"]),
            ):
                money[label] += v
        for due, i, kh in tasks:
            if kh != h:
                continue
            t, price, tid, name = sampled[(i, h)]
            pos = positions[i]
            old = SimpleNamespace(side=pos["side"], entry=D(pos["entry_price"]))
            gross, entry_fee, exit_fee, net = base.gross_and_net(
                old, D(price), False
            )
            stat = reasons["TIME_LIMIT"]
            stat["n"] += 1
            stat["winners"] += int(net > 0)
            stat["losers"] += int(net < 0)
            stat["net"] += net
            count["closed"] += 1
            count["time_exits"] += 1
            count["winners"] += int(net > 0)
            count["losers"] += int(net < 0)
            money["net"] += net
            money["gross"] += gross
            money["entry_fees"] += entry_fee
            money["exit_fees"] += exit_fee
        if count["closed"] + count["censored_open"] != len(positions):
            raise ValueError("fixed original entry count mismatch")
        if money["gross"] - money["entry_fees"] - money["exit_fees"] != money["net"]:
            raise ValueError("fee reconciliation mismatch")
        if count["original_early_exits"] + count["time_exits"] != count["closed"]:
            raise ValueError("closed count mismatch")
        if count["winners"] + count["losers"] != count["closed"]:
            raise ValueError("flat outcome requires separate modeling")
        report["scenarios"][f"H{h:02d}"] = {
            "counts": dict(count),
            "money_usdt": {label: str(value) for label, value in money.items()},
            "by_reason": {
                reason: {label: str(v) if label == "net" else v
                         for label, v in group.items()}
                for reason, group in reasons.items()
            },
        }

    output.mkdir(mode=0o770)
    (output / "result.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", choices=SYMBOLS, required=True)
    parser.add_argument("--raw-root", type=Path, default=RAW_DEFAULT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.symbol, args.raw_root, args.output)
    print(json.dumps({
        "symbol": result["symbol"],
        "status": result["status"],
        "days_sampled": result["native_days_sampled"],
        "scenarios": {k: v["money_usdt"]["net"]
                      for k, v in result["scenarios"].items()},
    }), flush=True)


if __name__ == "__main__":
    main()
