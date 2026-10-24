#!/usr/bin/env python3
"""P90L90 full 90 UTC-day v2 raw crossing Entry candidates (research only).

Canon: docs/RESEARCH_COMPUTE_RULES_RU.md §22.1, 2026-10-24.
No exchange order or fill, no production Strategy/Exit, no LIVE.
All raw input is read-only. Output is only the explicit research output directory.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext
from pathlib import Path
from statistics import median

getcontext().prec = 50  # Preserve CLEAN v1 40-digit decimal source identities.
D = Decimal
SYMBOLS = ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
START = datetime(2026, 5, 18, tzinfo=timezone.utc)
END = datetime(2026, 8, 16, tzinfo=timezone.utc)
MAY11 = datetime(2026, 5, 11, tzinfo=timezone.utc)
START_TS, END_TS, MAY11_TS = (
    int(x.timestamp()) for x in (START, END, MAY11)
)
RAW_DEFAULT = Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/inputs")
FROZEN_DEFAULT = Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/results/l53_clean_v2/parts")
OUTPUT_ROOT = Path("/data/cripta/research/runs")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_day(ts: int):
    return datetime.fromtimestamp(ts, timezone.utc).date()


def load_frozen(path: Path):
    """Use only the separately frozen and ATR200-reseeded P90L90 L5 CLEAN v2."""
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
            if not MAY11_TS <= ts < END_TS:
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
            if START_TS <= ts < END_TS:
                states[ts] = (low, high, width)
                all_widths.append(width)
    if len(states) != 90 * 288:
        raise ValueError("full 90-day frozen L5 coverage missing")
    for i in range((END.date() - MAY11.date()).days):
        day = MAY11.date() + timedelta(days=i)
        if len(by_day[day]) != 288:
            raise ValueError("May11 onward must contain all 288 causal L5 states per UTC day")
    stamps = sorted(states)
    if stamps[0] != START_TS or stamps[-1] != END_TS - 300:
        raise ValueError("90-day boundary L5 state missing")
    if any(b - a != 300 for a, b in zip(stamps, stamps[1:])):
        raise ValueError("L5-3 timeline gap")
    fixed = median(all_widths)
    prev7 = {}
    for day in sorted(by_day):
        prev = [day - timedelta(days=lag) for lag in range(1, 8)]
        if len(by_day[day]) == 288 and all(len(by_day[p]) == 288 for p in prev):
            prev7[day] = median(v for p in prev for v in by_day[p])
    if len(prev7) != 90 or set(prev7) != {START.date() + timedelta(days=i) for i in range(90)}:
        raise ValueError("PREV7D must be ready for all 90 complete UTC days")
    return states, fixed, prev7, {"states": len(states), "first": stamps[0], "last": stamps[-1], "prior_complete_days": 7, "l5_source": "CLEAN_V2"}


class RollingClosedBars:
    """Track raw completed candles, Wilder ATR200, and consecutive bars."""

    def __init__(self, seconds: int, depth: int):
        self.seconds, self.depth = seconds, depth
        self.bucket = None
        self.current = None
        self.bars = deque(maxlen=depth)
        self.atr_seed = []
        self.atr = None
        self.prev_close = None
        self.last_close_ts = None
        self.gaps = 0

    def update(self, timestamp: float, px: D):
        bucket = int(timestamp // self.seconds) * self.seconds
        if self.bucket is None:
            self.bucket = bucket
            self.current = [px, px, px]
            return
        if bucket == self.bucket:
            self.current[0] = max(self.current[0], px)
            self.current[1] = min(self.current[1], px)
            self.current[2] = px
            return
        if bucket < self.bucket:
            raise ValueError("raw trades not chronological")
        if bucket != self.bucket + self.seconds:
            self.gaps += 1
            self.bars.clear()
            self.atr_seed.clear()
            self.atr = None
            self.prev_close = None
        else:
            hi, lo, close = self.current
            tr = hi - lo if self.prev_close is None else max(
                hi - lo, abs(hi - self.prev_close), abs(lo - self.prev_close)
            )
            self.prev_close = close
            if self.atr is None:
                self.atr_seed.append(tr)
                if len(self.atr_seed) == 200:
                    self.atr = sum(self.atr_seed, D(0)) / D(200)
            else:
                self.atr = (self.atr * D(199) + tr) / D(200)
            self.bars.append((hi, lo))
            self.last_close_ts = bucket
        self.bucket = bucket
        self.current = [px, px, px]

    def extrema(self):
        if self.atr is None or len(self.bars) != self.depth:
            return None
        return (min(row[1] for row in self.bars), max(row[0] for row in self.bars))


def geometry_candidates(l5, h5, h15):
    """Pure independent geometry, no width or occupancy filter."""
    if h5 is None or h15 is None:
        return {}
    lo5, hi5 = h5
    lo15, hi15 = h15
    low, high, _ = l5
    span = max(hi5, hi15) - min(lo5, lo15)
    if span <= 0:
        return {}
    result = {}
    for side, concordant, numerator, boundary in (
        ("LONG", lo5 == lo15, low - lo5, low),
        ("SHORT", hi5 == hi15, hi5 - high, high),
    ):
        pct = D(100) * numerator / span
        if concordant and D(0) <= pct <= D(15):
            result[side] = {
                "edge": boundary, "numerator": numerator, "denominator": span,
                "depth_pct": pct, "h9_5m_low": lo5, "h9_5m_high": hi5,
                "h9_15m_low": lo15, "h9_15m_high": hi15,
            }
    return result


def crossing(side: str, previous: D, current: D, edge: D) -> bool:
    if side == "LONG":
        return previous > edge >= current
    if side == "SHORT":
        return previous < edge <= current
    raise ValueError(side)


def source_paths(root: Path, symbol: str, first: str, last: str):
    begin = datetime.strptime(first, "%Y-%m-%d").date()
    finish = datetime.strptime(last, "%Y-%m-%d").date()
    days = [begin + timedelta(days=i) for i in range((finish - begin).days + 1)]
    paths = [root / symbol / "public_trades" / f"{symbol}{day.isoformat()}.csv.gz" for day in days]
    if any(not p.is_file() or p.stat().st_size <= 0 for p in paths):
        missing = [str(p) for p in paths if not p.is_file() or p.stat().st_size <= 0]
        raise FileNotFoundError("Missing/empty files: " + repr(missing[:4]))
    return paths


def process(symbol: str, raw: Path, frozen: Path, output: Path,
            warmup_first: str = "2026-05-08", through: str = "2026-08-15"):
    if symbol not in SYMBOLS:
        raise ValueError("symbol not in owner cohort")
    if output.exists():
        raise FileExistsError("immutable research output already exists: " + str(output))
    states_path = frozen / f"states_{symbol}.csv.gz"
    states, fixed, prev7, state_meta = load_frozen(states_path)
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
                        elif bucket >= START_TS:
                            counters["h9_not_ready_full90"] += 1
                if START_TS <= t < END_TS and prev_px is not None and eligible:
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
    complete = (warmup_first == "2026-05-08" and through == "2026-08-15")
    if not complete:
        raise ValueError("P90L90 V2 forbids partial 90-day input")
    source_manifest = raw.parent / f"inputs/manifest_{symbol}.json"
    if not source_manifest.exists() or len(json.loads(source_manifest.read_text())["files"]) != 100:
        raise ValueError("incomplete native Bybit 100-day source provenance")
    manifest = {
        "research": "P90L90_ENTRY_90D_V2", "canon": "RESEARCH_COMPUTE §22.2",
        "symbol": symbol,
        "status": "FULL_90D_ENTRY_CANDIDATE_ONLY_NO_FILL_NO_EXIT",
        "complete_90d_input": complete,
        "source_first_day": warmup_first,
        "source_last_day": through,
        "window": ["2026-05-18T00:00Z", "2026-08-16T00:00Z"],
        "comparison_window": ["2026-05-18T00:00Z", "2026-08-16T00:00Z"],
        "frozen_l53_sha256": sha256(states_path), "frozen_l53": state_meta,
        "median_FIXED90": str(fixed), "medians_PREV7D": {str(k): str(v) for k, v in prev7.items()},
        "counts": dict(counters), "inputs": dataset_files,
        "source_row_sequence": "original gzip file order, chronological UTC days",
        "price_precision": "Decimal(source string)",
        "selection": "RAW_H9_UNION_SPAN, side RAW equality, 0..15%, first adjacent tick crossing",
        "FIXED90_LOOKAHEAD": True, "PREV7D_WIDTH_LOOKAHEAD": False, "FULL_90D_UTC_COVERAGE": True,
    }
    if output.parent != output.parent.resolve() or output.parent != output.parent.absolute():
        raise ValueError("output must be absolute resolved")
    if OUTPUT_ROOT not in output.parents:
        raise ValueError("only /data/cripta/research/runs output permitted")
    output.mkdir(parents=False, exist_ok=False)
    events = output / f"candidates_{symbol}.jsonl"
    events.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    manifest["events_sha256"] = sha256(events)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True, choices=SYMBOLS)
    p.add_argument("--raw-root", type=Path, default=RAW_DEFAULT)
    p.add_argument("--frozen-root", type=Path, default=FROZEN_DEFAULT)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--warmup-first", choices=["2026-05-08"], default="2026-05-08")
    p.add_argument("--through", default="2026-08-15")
    args = p.parse_args()
    if not args.output.is_absolute():
        p.error("--output must be absolute")
    report = process(args.symbol, args.raw_root, args.frozen_root, args.output,
                     warmup_first=args.warmup_first, through=args.through)
    print(json.dumps({"status": report["status"], "counts": report["counts"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
