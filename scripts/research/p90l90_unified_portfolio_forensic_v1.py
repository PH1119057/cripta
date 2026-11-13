#!/usr/bin/env python3
"""#638: deterministic READ-ONLY, post-QC forensic of the 29d/90d/183d V2 books.

Exact data source: the three immutable, independently QC-verified V2 Data-disk
result leaves, *not* a re-run, resampled portfolio, live service or old V1.

Actor: cripta (UID 107/GID 109), execute with python3 -B.
READ: manifest.json, status.json, result.json, qc.json, trade_ledger.jsonl,
      checkpoints/day_NNN.json.gz at the three exact paths in ROOTS.
WRITE/CREATE/RENAME/DELETE: none. DB/Exchange/Strategy/LIVE: none.
Output: bounded JSON to stdout only; no disk artifacts or source mutations.

Definitions:
- closed_net: sum native trade_ledger rows' net_usdt, modeled fees included.
- hardstop_net: native EXIT_REASON == HARD_STOP; all other exits separated.
- terminal_equity: result's marked equity including *gross* unrealized open
  PnL and modeled prior entry fees, but not hypothetical future exit costs.
- monthly accepted, stops, denials and net are exact deltas of cumulative
  immutable checkpoint counters, with each date denoting that UTC day.
- daily marks are as recorded by the producer, not newly constructed bars.
- threshold is an arithmetic break-even hard-stop *frequency* assuming the
  period-specific observed mean payoffs, not a causal policy recommendation.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
from decimal import Decimal as D
from pathlib import Path

ROOTS = {
    "29d": Path(
        "/data/cripta/research/runs/p90l90_prev7d_29d_20261025/results/"
        "p90l90_unified_depth0_vs_0to15_capital_20261113_v2"
    ),
    "90d": Path(
        "/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/results/"
        "p90l90_unified_depth0_vs_0to15_capital_20261113_v2"
    ),
    "183d": Path(
        "/data/cripta/research/runs/p90l90_bull_2025_holdout_v1/results/"
        "p90l90_unified_depth0_vs_0to15_capital_20261113_v2"
    ),
}
PINNED_HEADS = {
    "29d": "cd28332a5b487b8bffd5a56e9c168f74f2f15e8e",
    "90d": "500e4b381642d98d117c5b57f62fd81418156cb7",
    "183d": "d90e0a93117c5e7edc99ef6bd6ad3b0423e6c7ea",
}
START_UTC = {
    "29d": dt.date(2026, 9, 21),
    "90d": dt.date(2026, 5, 18),
    "183d": dt.date(2025, 4, 1),
}
FOCUS = "ORIGINAL_0TO15__fixed__SECOND48"
MODEL = "P90L90_UNIFIED_DEPTH0_VS_0TO15_THREE_PERIODS_CAPITAL_V2"
CHECK_FLAGS = (
    "source_candidate_count_parity",
    "original_native_reference_trade_parity",
    "native_full_replay_own_cash_state",
    "full_8_case_matrix_verified",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def money(value: D) -> str:
    return str(value.quantize(D("0.01")))


def pct(value: D) -> str:
    return str(value.quantize(D("0.0001")))


def totals(rows: list[dict]) -> dict:
    """Pure, independently testable modeled money summary."""
    total_net = sum((D(t["net_usdt"]) for t in rows), D(0))
    fees = sum(
        (D(t["entry_fee_usdt"]) + D(t["exit_fee_usdt"]) for t in rows),
        D(0),
    )
    stoppers = [r for r in rows if r["exit_reason"] == "HARD_STOP"]
    rest = [r for r in rows if r["exit_reason"] != "HARD_STOP"]
    stops_net = sum((D(r["net_usdt"]) for r in stoppers), D(0))
    others_net = total_net - stops_net
    stop_mean = stops_net / len(stoppers) if stoppers else D(0)
    other_mean = others_net / len(rest) if rest else D(0)
    denom = other_mean - stop_mean
    threshold = (other_mean / denom * 100) if (
        stoppers and rest and stop_mean < 0 < other_mean and denom != 0
    ) else None
    return {
        "closed": len(rows),
        "profitable": sum(D(r["net_usdt"]) > 0 for r in rows),
        "losing": sum(D(r["net_usdt"]) < 0 for r in rows),
        "hardstops": len(stoppers),
        "hardstop_rate_pct": pct(D(100) * len(stoppers) / len(rows)) if rows else "0",
        "hardstop_net_usdt": money(stops_net),
        "other_exits_net_usdt": money(others_net),
        "closed_net_after_fees_usdt": money(total_net),
        "modeled_fees_usdt": money(fees),
        "gross_before_fees_usdt": money(total_net + fees),
        "avg_hardstop_net_usdt": money(stop_mean),
        "avg_other_exit_net_usdt": money(other_mean),
        "arithmetic_breakeven_hardstop_rate_pct": (
            pct(threshold) if threshold is not None else None
        ),
    }


def read_verified(period: str) -> tuple[dict, dict, dict, dict[str, list[dict]], list[dict]]:
    root = ROOTS[period]
    if root.is_symlink():
        raise ValueError("symlink output leaf forbidden")
    status = json.loads((root / "status.json").read_text())
    result = json.loads((root / "result.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    qc = json.loads((root / "qc.json").read_text())
    if (
        status.get("stage") != "COMPLETED_INDEPENDENT_QC_PASS"
        or status.get("source_head") != PINNED_HEADS[period]
        or result.get("source_head") != PINNED_HEADS[period]
        or manifest.get("source_head") != PINNED_HEADS[period]
        or status.get("days_done") != int(period.removesuffix("d"))
        or result.get("period") != period
        or result.get("research") != MODEL
        or len(result.get("cases", {})) != 8
        or result.get("source_pins") != manifest.get("source_pins")
        or set(result["cases"]) != set(qc.get("cases", {}))
        or any(qc.get(flag) is not True for flag in CHECK_FLAGS)
    ):
        raise ValueError(period + ": expected immutable 8-case QC PASS missing or drifted")
    for filename, field in (
        ("result.json", "result_sha256"),
        ("trade_ledger.jsonl", "ledger_sha256"),
        ("qc.json", "qc_sha256"),
    ):
        if digest(root / filename) != status.get(field):
            raise ValueError(period + ": status SHA mismatch for " + filename)
        if filename != "qc.json" and digest(root / filename) != qc.get(field):
            raise ValueError(period + ": independent QC SHA mismatch for " + filename)

    rows: dict[str, list[dict]] = collections.defaultdict(list)
    with (root / "trade_ledger.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            name = row.pop("research_case")
            if name not in result["cases"]:
                raise ValueError("unknown case in native ledger")
            rows[name].append(row)
    for name, summary in result["cases"].items():
        if len(rows[name]) != summary["counts"]["closed"]:
            raise ValueError("closed event count disagrees with native ledger")
        independently_net = sum((D(row["net_usdt"]) for row in rows[name]), D(0))
        if independently_net != D(summary["money"]["closed_net_usdt"]):
            raise ValueError("closed net does not reconcile")

    # Validate the *complete* immutable checkpoint chain, not just its final hash.
    prior = ""
    daily = []
    prior_book = {"accepted": 0, "exit_HARD_STOP": 0, "capital_blocked_candidate": 0}
    prior_net = D(0)
    for day in range(1, int(period.removesuffix("d")) + 1):
        file = root / "checkpoints" / ("day_%03d.json.gz" % day)
        if file.is_symlink() or not file.is_file():
            raise ValueError("missing/unsafe immutable checkpoint")
        raw = file.read_bytes()
        saved = json.loads(gzip.decompress(raw))
        if (saved.get("days_done") != day
                or saved.get("period") != period
                or saved.get("source_pins") != result["source_pins"]
                or saved.get("previous_checkpoint_sha256") != prior
                or set(saved.get("books", {})) != set(result["cases"])):
            raise ValueError("checkpoint chain/content drift at day %d" % day)
        prior = hashlib.sha256(raw).hexdigest()
        book = saved["books"][FOCUS]
        counts = book["counts"]
        cumul = {k: int(counts.get(k, 0)) for k in prior_book}
        if any(cumul[k] < prior_book[k] for k in cumul):
            raise ValueError("nonmonotone cumulative counts")
        closed_net = D(book["money"].get("closed_net_usdt", "0"))
        marked = D(book["daily_marks"][-1][1])
        date = START_UTC[period] + dt.timedelta(days=day - 1)
        daily.append({
            "date": date.isoformat(),
            "accepted": cumul["accepted"] - prior_book["accepted"],
            "stops": cumul["exit_HARD_STOP"] - prior_book["exit_HARD_STOP"],
            "denials": cumul["capital_blocked_candidate"]
            - prior_book["capital_blocked_candidate"],
            "closed_net_change": closed_net - prior_net,
            "end_equity": marked,
        })
        prior_book, prior_net = cumul, closed_net
    if prior != qc.get("last_checkpoint_sha256") or prior != result.get(
        "final_checkpoint_sha256"
    ):
        raise ValueError("terminal checkpoint QC SHA mismatch")
    return status, result, qc, rows, daily


def clusters(rows: list[dict], limit: int = 6) -> list[dict]:
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        if row["exit_reason"] != "HARD_STOP":
            continue
        date = dt.datetime.fromtimestamp(
            float(row["exit_ts"]), dt.timezone.utc
        ).date().isoformat()
        grouped[date].append(row)
    ranked = sorted(
        grouped.items(),
        key=lambda item: (
            sum((D(x["net_usdt"]) for x in item[1]), D(0)),
            -len(item[1]),
            item[0],
        ),
    )
    return [
        {
            "utc_date": date,
            "stops": len(g),
            "net_after_fees_usdt": money(sum((D(x["net_usdt"]) for x in g), D(0))),
            "all_open_before_first_stop": max(
                D(x["entry_ts"]) for x in g
            ) < min(D(x["exit_ts"]) for x in g),
            "symbols_side": [x["symbol"] + ":" + x["side"] for x in g],
        }
        for date, g in ranked[:limit]
    ]


def timeline(daily: list[dict]) -> dict:
    months: dict[str, dict] = {}
    for x in daily:
        month = x["date"][:7]
        if month not in months:
            months[month] = {
                "accepted": 0, "hardstops": 0, "capital_denials": 0,
                "closed_net": D(0), "end_equity": D(0),
            }
        row = months[month]
        row["accepted"] += x["accepted"]
        row["hardstops"] += x["stops"]
        row["capital_denials"] += x["denials"]
        row["closed_net"] += x["closed_net_change"]
        row["end_equity"] = x["end_equity"]

    def first(test):
        return next((x["date"] for x in daily if test(x)), None)

    return {
        "last_entry_utc_day": next(
            (x["date"] for x in reversed(daily) if x["accepted"] > 0), None
        ),
        "first_capital_denial_utc_day": first(lambda x: x["denials"] > 0),
        "first_marked_equity_below_500_utc_day": first(
            lambda x: x["end_equity"] < 500
        ),
        "first_marked_equity_below_200_utc_day": first(
            lambda x: x["end_equity"] < 200
        ),
        "months": {
            name: {
                **{k: val for k, val in record.items()
                   if k not in ("closed_net", "end_equity")},
                "closed_net_after_fees_usdt": money(record["closed_net"]),
                "end_marked_equity_usdt": money(record["end_equity"]),
            }
            for name, record in months.items()
        },
    }


def by_key(rows: list[dict], field: str) -> dict:
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        groups[row[field]].append(row)
    return {
        k: {
            "closed": len(data),
            "hardstops": sum(x["exit_reason"] == "HARD_STOP" for x in data),
            "closed_net_after_fees_usdt": money(
                sum((D(x["net_usdt"]) for x in data), D(0))
            ),
        }
        for k, data in sorted(groups.items())
    }


def analyze() -> dict:
    if os.name != "posix":
        raise OSError("pinned CRIPTA Data-disk reader runs on Linux")
    if (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("only actual read/research actor cripta may run forensic")
    matrix: dict[str, dict] = {}
    focus: dict[str, dict] = {}
    evidence = {}
    for period in ("29d", "90d", "183d"):
        status, result, qc, ledger, daily = read_verified(period)
        evidence[period] = {
            "source_head": status["source_head"],
            "result_sha256": status["result_sha256"],
            "ledger_sha256": status["ledger_sha256"],
            "qc_sha256": status["qc_sha256"],
            "days_qc_verified": status["days_done"],
            "complete_chain_verified": True,
        }
        for name, summary in result["cases"].items():
            raw = totals(ledger[name])
            matrix.setdefault(name, {})[period] = {
                "equity_return_pct": pct(D(summary["equity_return_pct"])),
                "terminal_marked_equity_usdt": money(
                    D(summary["equity_with_open_unrealized_gross_usdt"])
                ),
                "capital_denials": summary["capital_blocked_candidate_count"],
                **raw,
            }
        selected = ledger[FOCUS]
        focus[period] = {
            "timeline": timeline(daily),
            "worst_hardstop_clusters": clusters(selected),
            "by_symbol": by_key(selected, "symbol"),
            "by_side": by_key(selected, "side"),
            "pause_triggers": result["cases"][FOCUS]["pause_trigger_count"],
            "paused_candidates": result["cases"][FOCUS]["counts"].get(
                "paused_candidate", 0
            ),
            "open_censored_positions": len(result["cases"][FOCUS]["open_censored"]),
        }
    return {
        "research_result": "POST_QC_HARDSTOP_AND_CAPITAL_FORENSIC_V1",
        "portfolio": "TRUE_UNIFIED_LONG_SHORT_5_COIN_8_CASES",
        "scope": "immutable historic V2 results; no new Strategy or policy",
        "source_data_evidence": evidence,
        "matrix": dict(sorted(matrix.items())),
        "focus_case": FOCUS,
        "focus": focus,
        "limitations": [
            "Three historical periods are not independent fully blinded OOS.",
            "Hardstop frequency arithmetic is descriptive, not causal.",
            "Marked open positions exclude hypothetical future exit commissions.",
            "Spread, slippage, funding, latency and real exchange fills absent.",
            "Do not equate capital-denial counts with distinct possible fills.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("focus", "all"), default="focus")
    args = parser.parse_args()
    report = analyze()
    if args.scope == "focus":
        report["matrix"] = {FOCUS: report["matrix"][FOCUS]}
    print(json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
