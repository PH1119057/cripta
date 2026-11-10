#!/usr/bin/env python3
"""2025 rising-market P90L90 causal Entry candidate replay, data-only.

Mathematical geometry/crossing/closed-bar algorithms are imported unchanged from
the audited 2026 P90L90 Entry v2. The raw-tick event loop below is an exact
source copy of that v2, with ONLY period boundaries and frozen-state duration
parameterized; the old 2026 case must exactly match all five reference
candidate JSONL SHA256 before the 2025 case can run.

No fills, no exits, no financial outcome, no trading runtime/DB/exchange/LIVE.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext
from pathlib import Path
from statistics import median

SOURCE = Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_entry_90d_v2 as old  # noqa: E402

getcontext().prec = 50
D = Decimal
RollingClosedBars = old.RollingClosedBars
geometry_candidates = old.geometry_candidates
crossing = old.crossing
source_paths = old.source_paths
utc_day = old.utc_day
sha256 = old.sha256
SYMBOLS = tuple(old.SYMBOLS)
ROOT = Path("/data/cripta/research/runs/p90l90_bull_2025_holdout_v1")
RESULTS = ROOT / "results"
OLD = Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024")
OLD_RAW = OLD / "inputs"
OLD_FROZEN = OLD / "results/l53_clean_v2/parts"
OLD_REFERENCE = OLD / "results"
SELF_REL = "scripts/research/p90l90_bull2025_entry_v1.py"
ORIGINAL_REL = "scripts/research/p90l90_entry_90d_v2.py"
OUT_DIRS = {"parity-2026": "entry_parity_2026_v1", "full-2025": "entry_full_2025_v1"}
EVALUATION = {
    "parity-2026": ("2026-05-18T00:00:00+00:00", "2026-08-16T00:00:00+00:00",
                    "2026-05-08", "2026-08-15"),
    "full-2025": ("2025-04-01T00:00:00+00:00", "2025-10-01T00:00:00+00:00",
                   "2025-03-22", "2025-09-30"),
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def git_head() -> str:
    return subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], check=True,
        capture_output=True, text=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    ).stdout.strip()


def preflight(mode: str, symbol: str, commit: str) -> tuple[Path, Path, Path]:
    if os.geteuid() != 107 or git_head() != commit:
        raise PermissionError("exact cripta UID107 / source GitHub main SHA required")
    if len(commit) != 40 or any(x not in "0123456789abcdef" for x in commit):
        raise ValueError("invalid source HEAD")
    if mode not in EVALUATION or symbol not in SYMBOLS:
        raise ValueError("frozen P90L90 mode/coin identity mismatch")
    acquisition = json.loads((ROOT / "run_manifest.json").read_text())
    if acquisition.get("status") != "INPUT_COMPLETE_QC_PASS_NO_STRATEGY_REPLAY":
        raise ValueError("2025 archive not complete")
    if sha(SOURCE / ORIGINAL_REL) != acquisition["source_snapshot_sha256"][ORIGINAL_REL]:
        raise RuntimeError("original 2026 Entry algorithm changed")
    if not ROOT.is_dir() or ROOT.stat().st_uid != 107:
        raise PermissionError("research output root identity mismatch")
    if shutil.disk_usage(ROOT).free < 16 * 2**30:
        raise RuntimeError("data-disk reserve for Entry replay below 16GiB")
    if RESULTS.is_symlink():
        raise PermissionError("research results root symlink")
    if RESULTS.exists() and (
        RESULTS.stat().st_uid != 107 or not os.access(RESULTS, os.W_OK | os.X_OK)
    ):
        raise PermissionError("research results parent actor mismatch")
    parent = RESULTS / OUT_DIRS[mode]
    out = parent / symbol
    if parent.is_symlink() or out.exists() or out.is_symlink():
        raise FileExistsError("immutable candidate output collision")
    if parent.exists() and (
        parent.stat().st_uid != 107 or not os.access(parent, os.W_OK | os.X_OK)
    ):
        raise PermissionError("research Entry parent actor mismatch")
    if mode == "parity-2026":
        raw, frozen = OLD_RAW, OLD_FROZEN
        if not (OLD_REFERENCE / f"entry_{symbol[:-4] if symbol != 'ARBUSDT' else 'ARB'}_FULL90").is_dir():
            # Avoid name inference if the existing old archive has changed.
            raise RuntimeError("2026 old Entry reference missing")
    else:
        raw = ROOT / "inputs"
        frozen = RESULTS / "l53_geometry_full_2025_v1/parts"
        fq = RESULTS / "l53_geometry_full_2025_v1/qc.json"
        if not fq.is_file() or json.loads(fq.read_text()).get("status") != "QC_PASS_FULL_2025_GEOMETRY_NOT_TRADING":
            raise RuntimeError("2025 full frozen geometry not verified")
        for s in SYMBOLS:
            q = RESULTS / OUT_DIRS["parity-2026"] / s / "qc.json"
            if not q.is_file() or json.loads(q.read_text()).get("status") != "EXACT_2026_CANDIDATE_LEDGER_PARITY_PASS":
                raise RuntimeError("five-coin 2026 Entry reference parity not complete")
    return raw, frozen, out



def load_frozen_period(path: Path, start: datetime, end: datetime):
    """Use only the separately frozen and ATR200-reseeded P90L90 L5 CLEAN v2."""
    ready = start - timedelta(days=7)
    ready_ts = int(ready.timestamp())
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    days = (end.date() - start.date()).days
    states = {}
    all_widths = []
    by_day = defaultdict(list)
    seen_timestamps = set()
    with gzip.open(path, "rt", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {
            "state_effective_ts", "source_bar_end_ts", "range_low",
            "range_high", "working_width_pct_lower", "lower_inner",
            "upper_inner", "symbol",
        }
        if not required.issubset(set(reader.fieldnames or ())):
            raise ValueError("L5-3 frozen schema mismatch")
        for row in reader:
            ts = int(row["state_effective_ts"])
            if not ready_ts <= ts < end_ts:
                continue
            if ts in seen_timestamps or ts != int(row["source_bar_end_ts"]):
                raise ValueError("duplicate or noncausal L5-3 state")
            if ts % 300 != 0:
                raise ValueError("unaligned L5-3 state")
            low, high = D(row["range_low"]), D(row["range_high"])
            lower, upper = D(row["lower_inner"]), D(row["upper_inner"])
            width = D(row["working_width_pct_lower"])
            if not all(x.is_finite() for x in (low, high, lower, upper, width)):
                raise ValueError("nonfinite geometry")
            if lower <= 0 or width != (upper - lower) / lower * D(100):
                # Original CLEAN v1 persists decimal strings rounded by its
                # own 40-digit precision. Allow only that precision noise.
                if lower <= 0 or abs(width - (upper - lower) / lower * D(100)) > D("1e-32"):
                    raise ValueError("frozen width identity failed")
            seen_timestamps.add(ts)
            by_day[utc_day(ts)].append(width)
            if start_ts <= ts < end_ts:
                states[ts] = (low, high, width)
                all_widths.append(width)
    if len(states) != days * 288:
        raise ValueError("full window frozen L5 coverage missing")
    for i in range((end.date() - ready.date()).days):
        day = ready.date() + timedelta(days=i)
        if len(by_day[day]) != 288:
            raise ValueError("7-day prewarm onward must contain all 288 causal L5 states per UTC day")
    stamps = sorted(states)
    if stamps[0] != start_ts or stamps[-1] != end_ts - 300:
        raise ValueError("window boundary L5 state missing")
    if any(b - a != 300 for a, b in zip(stamps, stamps[1:])):
        raise ValueError("L5-3 timeline gap")
    fixed = median(all_widths)
    prev7 = {}
    for day in sorted(by_day):
        prev = [day - timedelta(days=lag) for lag in range(1, 8)]
        if len(by_day[day]) == 288 and all(len(by_day[p]) == 288 for p in prev):
            prev7[day] = median(v for p in prev for v in by_day[p])
    if len(prev7) != days or set(prev7) != {start.date() + timedelta(days=i) for i in range(days)}:
        raise ValueError("PREV7D must be ready for all 90 complete UTC days")
    return states, fixed, prev7, {"states": len(states), "first": stamps[0], "last": stamps[-1], "prior_complete_days": 7, "l5_source": "CLEAN_V2"}

def process(symbol: str, raw: Path, frozen: Path, output: Path,
            start: datetime, end: datetime, warmup_first: str, through: str,
            source_head: str, mode: str):
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    if symbol not in SYMBOLS:
        raise ValueError("symbol not in owner cohort")
    if output.exists():
        raise FileExistsError("immutable research output already exists: " + str(output))
    states_path = frozen / f"states_{symbol}.csv.gz"
    states, fixed, prev7, state_meta = load_frozen_period(states_path, start, end)
    paths = source_paths(raw, symbol, warmup_first, through)
    h5, h15 = RollingClosedBars(300, 108), RollingClosedBars(900, 36)
    counters = Counter()
    current_bucket = None
    eligible = {}
    prev_px = prev_t = None
    prior_move = None
    last_ids = set()
    seen = set()
    trade_index = 0
    dataset_files = []
    lines = []
    for path in paths:
        # Exact input provenance. Source is never modified.
        input_hash = sha256(path)
        day_rows = 0
        with gzip.open(path, "rt", newline="") as stream:
            reader = csv.DictReader(stream)
            required = {"timestamp", "price", "symbol", "tickDirection", "trdMatchID"}
            if not required.issubset(set(reader.fieldnames or ())):
                raise ValueError("Bybit raw schema mismatch: " + str(path))
            for rec in reader:
                trade_index += 1
                day_rows += 1
                t = float(rec["timestamp"])
                px = D(rec["price"])
                if not math.isfinite(t) or not px.is_finite() or px <= 0 or rec["symbol"] != symbol:
                    raise ValueError("invalid raw row")
                if prev_t is not None and t < prev_t:
                    raise ValueError("unsorted Bybit timestamp")
                if prev_t is None or t != prev_t:
                    last_ids.clear()
                if rec["trdMatchID"] in last_ids:
                    raise ValueError("duplicate trade ID at same timestamp")
                last_ids.add(rec["trdMatchID"])
                move = 1 if prev_px is not None and px > prev_px else -1 if prev_px is not None and px < prev_px else 0
                expected = (
                    "PlusTick" if move > 0 else "MinusTick" if move < 0 else
                    "ZeroPlusTick" if prior_move == 1 else "ZeroMinusTick" if prior_move == -1 else None
                )
                if expected and rec["tickDirection"] != expected:
                    raise ValueError("trade-direction ordering inconsistency")
                if move:
                    prior_move = move
                h5.update(t, px)
                h15.update(t, px)
                bucket = int(t // 300) * 300
                if bucket != current_bucket:
                    current_bucket = bucket
                    seen = set()
                    eligible = {}
                    l5 = states.get(bucket)
                    if l5 is not None:
                        if h5.last_close_ts != bucket or h15.last_close_ts is None or h15.last_close_ts > bucket:
                            raise ValueError("noncausal H9 timing")
                        h5ext, h15ext = h5.extrema(), h15.extrema()
                        if h5ext and h15ext:
                            eligible = geometry_candidates(l5, h5ext, h15ext)
                        elif bucket >= start_ts:
                            counters["h9_not_ready_full90"] += 1
                if start_ts <= t < end_ts and prev_px is not None and eligible:
                    for side, g in eligible.items():
                        if side in seen or not crossing(side, prev_px, px, g["edge"]):
                            continue
                        seen.add(side)
                        width = states[bucket][2]
                        day = utc_day(bucket)
                        passes_fixed = abs(width - fixed) <= D("0.5")
                        if day not in prev7:
                            raise ValueError("PREV7D not ready in full 90-day evaluation")
                        passes_prev = abs(width - prev7[day]) <= D("0.5")
                        counters["raw_crossing"] += 1
                        counters[side + "_raw_crossing"] += 1
                        if passes_fixed:
                            counters["FIXED90"] += 1
                        if passes_prev:
                            counters["PREV7D"] += 1
                        event = {
                            "event_id": f"{symbol}:{bucket}:{side}", "symbol": symbol,
                            "side": side, "source_order": trade_index,
                            "ts": repr(t), "state_effective_ts": bucket,
                            "trade_id": rec["trdMatchID"], "price": str(px),
                            "previous_tick_price": str(prev_px),
                            "previous_tick_ts": repr(prev_t),
                            "same_timestamp_pair": prev_t == t,
                            "l53_low_raw": str(states[bucket][0]),
                            "l53_high_raw": str(states[bucket][1]),
                            "width_pct": str(width), "FIXED90": passes_fixed,
                            "PREV7D": passes_prev,
                            "depth_pct": str(g["depth_pct"]),
                            "depth_numerator": str(g["numerator"]),
                            "raw_h9_union_span": str(g["denominator"]),
                            "h9_5m_low": str(g["h9_5m_low"]),
                            "h9_5m_high": str(g["h9_5m_high"]),
                            "h9_15m_low": str(g["h9_15m_low"]),
                            "h9_15m_high": str(g["h9_15m_high"]),
                            "candidate_only": True, "confirmed_exchange_fill": False,
                        }
                        lines.append(json.dumps(event, sort_keys=True, separators=(",", ":")))
                prev_px, prev_t = px, t
        dataset_files.append({"file": path.name, "rows": day_rows, "sha256": input_hash})
        counters["raw_rows"] += day_rows
    if h5.gaps or h15.gaps or counters["h9_not_ready_full90"]:
        raise ValueError(f"missing H9 bars: 5m={h5.gaps},15m={h15.gaps},common={counters['h9_not_ready_full90']}")
    return finalize_result(
        symbol, raw, frozen, output, start, end, warmup_first, through,
        dataset_files, counters, state_meta, fixed, prev7, lines, source_head, mode,
    )


def finalize_result(
    symbol: str, raw: Path, frozen: Path, output: Path,
    start: datetime, end: datetime, warmup_first: str, through: str,
    raw_files: list[dict], counters: Counter, state_meta: dict,
    fixed: D, prev7: dict, lines: list[str], source_head: str, mode: str,
) -> dict:
    """QC every input source hash before creating a candidate output."""
    expected_days = (end.date()-start.date()).days + 10
    if len(raw_files) != expected_days:
        raise ValueError("incomplete 10-day warmup / evaluation native archive")
    if mode == "full-2025":
        qc = json.loads((ROOT / "qc_full.json").read_text())
        for entry in raw_files:
            name = entry["file"]
            key = f"{symbol}/public_trades/{name}"
            expected = qc["by_day"].get(key)
            if expected is None or expected["sha256"] != entry["sha256"] or (
                expected["rows"] != entry["rows"]
            ):
                raise RuntimeError("2025 source-day SHA/count mismatch: " + key)
    else:
        baseline = json.loads((OLD_REFERENCE / f"entry_{symbol[:-4]}_FULL90/manifest.json").read_text())
        for a, b in zip(raw_files, baseline["inputs"], strict=True):
            if a != b:
                raise RuntimeError("2026 exact input-day provenance parity failure")
    if not output.is_absolute() or RESULTS.resolve() not in output.resolve().parents:
        raise ValueError("Entry must write only to exact research data-disk root")
    output.mkdir(mode=0o2770, parents=False, exist_ok=False)
    leaf = output / f"candidates_{symbol}.jsonl"
    leaf.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf8")
    manifest = {
        "research": "P90L90_ENTRY_BULL2025_V1" if mode == "full-2025"
        else "P90L90_ENTRY_90D_V2",
        "status": "FULL_2025_ENTRY_CANDIDATE_ONLY_NO_FILL_NO_EXIT"
        if mode == "full-2025" else "FULL_90D_ENTRY_CANDIDATE_ONLY_NO_FILL_NO_EXIT",
        "symbol": symbol,
        "window": [start.isoformat(), end.isoformat()],
        "source_first_day": warmup_first,
        "source_last_day": through,
        "complete_window_input": True,
        "full_closed_UTC_days": (end.date()-start.date()).days,
        "frozen_l53_sha256": sha(frozen / f"states_{symbol}.csv.gz"),
        "frozen_l53": state_meta,
        "median_FIXED90": str(fixed),
        "medians_PREV7D": {str(k):str(v) for k,v in prev7.items()},
        "counts": dict(counters),
        "inputs": raw_files,
        "events_sha256": sha(leaf),
        "events": len(lines),
        "source_row_sequence": "original gzip file order, chronological UTC days",
        "selection": "RAW_H9_UNION_SPAN side RAW equality 0..15%, PREV7D +-0.5pp, first adjacent tick crossing",
        "FIXED90_LOOKAHEAD": True,
        "PREV7D_WIDTH_LOOKAHEAD": False,
        "candidate_only": True,
        "confirmed_exchange_fill": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2)+"\n")
    return manifest


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=OUT_DIRS, required=True)
    p.add_argument("--symbol", choices=SYMBOLS, required=True)
    p.add_argument("--source-head", required=True)
    args = p.parse_args()
    start_s, end_s, first, last = EVALUATION[args.mode]
    start, end = datetime.fromisoformat(start_s), datetime.fromisoformat(end_s)
    raw, frozen, out = preflight(args.mode, args.symbol, args.source_head)
    out.parent.mkdir(mode=0o2770, parents=True, exist_ok=True)
    # All old 2026 event fields are generated by the SAME original raw-tick loop
    # above. Compare exact SHA256 of the output JSONL against frozen source.
    print(json.dumps({
        "stage": "ENTRY_NATIVE_REPLAY_START", "mode": args.mode,
        "symbol": args.symbol, "window": [start_s,end_s],
        "source_head": args.source_head,
    }), flush=True)
    manifest = process(args.symbol, raw, frozen, out, start, end, first, last,
                       args.source_head, args.mode)
    if args.mode == "parity-2026":
        reference = json.loads(
            (OLD_REFERENCE / f"entry_{args.symbol[:-4]}_FULL90/manifest.json").read_text()
        )
        if manifest["events_sha256"] != reference["events_sha256"] or (
            manifest["counts"] != reference["counts"]
        ):
            raise RuntimeError("2026 exact Entry candidate event/count parity failed")
        status = "EXACT_2026_CANDIDATE_LEDGER_PARITY_PASS"
    else:
        status = "QC_PASS_2025_ENTRY_CANDIDATE_ONLY_NO_EXITS"
    qc = {
        "status": status, "mode": args.mode, "symbol": args.symbol,
        "source_head": args.source_head, "events_sha256": manifest["events_sha256"],
        "events": manifest["events"], "counts": manifest["counts"],
        "raw_rows": manifest["counts"].get("raw_rows"),
        "frozen_l53_sha256": manifest["frozen_l53_sha256"],
        "original_entry_core_sha256": sha(SOURCE / ORIGINAL_REL),
        "new_runner_sha256": sha(SOURCE / SELF_REL),
    }
    with (out/"qc.json").open("x",encoding="utf8") as stream:
        json.dump(qc,stream,sort_keys=True,indent=2)
        stream.write("\n")
    (out/"source_commit.txt").write_text(args.source_head+"\n")
    print(json.dumps({"stage":"ENTRY_NATIVE_REPLAY_QC_PASS","mode":args.mode,
                      "symbol":args.symbol,"raw_rows":qc["raw_rows"],
                      "events":qc["events"],"events_sha256":qc["events_sha256"],
                      "status":status}),flush=True)


if __name__ == "__main__":
    main()
