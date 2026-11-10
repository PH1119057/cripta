#!/usr/bin/env python3
"""Read-only 2025 P90L90 weekly per-symbol LONG/SHORT modeled cash flow.

Not a new backtest. Reads original immutable 183 UTC daily checkpoint chain
and previously independently certified 16-case shared-wallet portfolio.
Matches the sum of five per-symbol cash flows to original recorded wallet
after EVERY completed ISO week, exact Decimal equality. This is cash PnL:
entry fee on entry week; closed gross minus exit fee on exit week.
Open-position unrealized marks are intentionally EXCLUDED.
"""
from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import hashlib
import json
import os
import sys
import zlib
from decimal import Decimal as D
from pathlib import Path

SOURCE = Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as ch  # noqa: E402

SIDE_NAMES = {"LONG_ONLY", "SHORT_ONLY"}
STATUS_NAMES = {
    "LONG_ONLY": "INDEPENDENT_QC_PASS_SAVED_2025_LONG_16_CASES",
    "SHORT_ONLY": "INDEPENDENT_QC_PASS_SAVED_2025_SHORT_16_CASES",
}
INDEPENDENT_ROOT = ch.old.RESULTS / "portfolio_independent_qc_2025_v1"


def utc_week(ts: str | float) -> str:
    iso = dt.datetime.fromtimestamp(float(ts), dt.UTC).date().isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def portfolio_cash_events(book: dict) -> dict[str, dict[str, D]]:
    cash = collections.defaultdict(lambda: collections.defaultdict(lambda: D(0)))
    for trade in book["trades"]:
        sym = trade["symbol"]
        if sym not in ch.old.SYMBOLS:
            raise ValueError("unknown portfolio trade symbol")
        cash[utc_week(trade["entry_ts"])][sym] -= D(trade["entry_fee_usdt"])
        cash[utc_week(trade["exit_ts"])][sym] += (
            D(trade["gross_usdt"]) - D(trade["exit_fee_usdt"])
        )
    for sym, pos in book["pos"].items():
        if pos is not None:
            if sym not in book["open_fee"]:
                raise ValueError("open position lacks charged entry fee")
            cash[utc_week(pos.entry_ts)][sym] -= book["open_fee"][sym]
    return cash


def group_weeks() -> list[tuple[str, str, str, int, int]]:
    grouped = collections.OrderedDict()
    for i in range(ch.TOTAL_DAYS):
        day = dt.date(2025, 4, 1) + dt.timedelta(days=i)
        iso = day.isocalendar()
        week = f"{iso.year}-W{iso.week:02d}"
        grouped.setdefault(week, []).append((i, day.isoformat()))
    return [
        (name, rows[0][1], rows[-1][1], len(rows), rows[-1][0])
        for name, rows in grouped.items()
    ]


def assemble(
    state: dict, week_end_books: dict[int, dict],
) -> dict:
    weeks = group_weeks()
    result = {}
    for scenario in ch.old.SCENARIOS:
        for mode in ch.old.CASH_MODES:
            key = ch.key(scenario, mode)
            book = state["books"][key]
            events = portfolio_cash_events(book)
            wallet = ch.old.START_WALLET
            periods = []
            for week, date_start, date_end, day_count, index in weeks:
                coins = {
                    sym: events[week][sym] for sym in ch.old.SYMBOLS
                }
                change = sum(coins.values(), D(0))
                wallet += change
                expected = week_end_books[index][key]["wallet"]
                if wallet != expected:
                    raise ValueError(
                        f"weekly cash-by-coin != original checkpoint wallet "
                        f"{key} {week}: delta={wallet-expected}"
                    )
                periods.append({
                    "week": week, "start_utc": date_start,
                    "end_utc": date_end, "days": day_count,
                    "coins_usdt": {sym: str(v) for sym, v in coins.items()},
                    "week_cash_delta_usdt": str(change),
                    "wallet_end_usdt": str(wallet),
                })
            if wallet != book["wallet"]:
                raise ValueError("wallet mismatch at final 2025 checkpoint")
            result[key] = periods
    return result


def validated(side: str, head: str) -> dict:
    if os.geteuid() != 107 or ch.old.entry.git_head() != head:
        raise PermissionError("read-only weekly audit requires current cripta source")
    if side not in SIDE_NAMES:
        raise ValueError("invalid original LONG/SHORT side")
    folder = ch.PARTS / side.lower()
    state, manifest, day_sha = ch.load_parts(folder)
    if (
        state is None or state["next_day"] != ch.TOTAL_DAYS
        or manifest["side"] != side
    ):
        raise ValueError("complete 183-day side checkpoint absent")
    ch.pin_current(manifest["source_pin_sha256"], manifest["source_head_first"])
    original = ch.FINAL / side.lower() / "result.json"
    independent = INDEPENDENT_ROOT / side.lower() / "qc_independent.json"
    if not original.is_file() or not independent.is_file():
        raise ValueError("side portfolio independent final cash QC absent")
    audit = json.loads(independent.read_text(encoding="utf-8"))
    report = json.loads(original.read_text(encoding="utf-8"))
    if (
        audit["status"] != STATUS_NAMES[side]
        or audit["final_report_sha256"] != ch.old.sha(original)
        or audit["checkpoint_last_manifest_sha256"] != day_sha
        or report["native_ticks_evaluation"] != state["ticks"]
        or audit["cases_verified"] != 16
    ):
        raise ValueError("existing independent portfolio QC identity mismatch")
    ends = group_weeks()
    week_end_books = {
        index: ch.decode(json.loads(
            (folder / ch.part_name(index) / "state.json").read_text()
        ))["books"]
        for _, _, _, _, index in ends
    }
    cash = assemble(state, week_end_books)
    if len(cash) != 16:
        raise ValueError("expected exactly 16 2025 original cash scenarios")
    return {
        "research": "P90L90_2025_WEEKLY_SIDE_SYMBOL_CASHFLOW_V1",
        "status": "WEEKLY_16_CASES_EXACT_CASH_RECONCILIATION_PASS",
        "side": side, "source_head": head,
        "checkpoint_days": ch.TOTAL_DAYS,
        "checkpoint_last_manifest_sha256": day_sha,
        "independent_16_case_qc_sha256": ch.old.sha(independent),
        "full_portfolio_result_sha256": audit["final_report_sha256"],
        "week_count": len(ends), "symbols": list(ch.old.SYMBOLS),
        "explanation": (
            "entry fee at UTC entry week, realized gross minus exit fee at UTC "
            "exit week; includes fees for still open positions, excludes "
            "unrealized mark and projected future exit fee; 1000 USDT original "
            "cash wallet per scenario; no funding, spread or slippage modeled"
        ),
        "weekly_cashflow": cash,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--side", required=True, choices=sorted(SIDE_NAMES))
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--compressed", action="store_true")
    args = parser.parse_args()
    obj = validated(args.side, args.source_head)
    data = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    if args.compressed:
        print(json.dumps({
            "encoding": "zlib+base64",
            "sha256": hashlib.sha256(data).hexdigest(),
            "compressed_base64": base64.b64encode(zlib.compress(data, 9)).decode(),
        }, sort_keys=True))
    else:
        print(data.decode())


if __name__ == "__main__":
    main()
