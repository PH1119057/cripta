#!/usr/bin/env python3
"""#638 causal pre-Entry, read-only five-coin market movement forensic.

Actor: cripta UID107:GID109; interpreter: python3 -B.
Read: exact three immutable V2 QC-approved result leaves and their frozen
L5-3 5m states/manifest/QC on /data/cripta/research/runs.
Writes/creates/renames/deletes: NONE. JSON only on stdout.
No DB/Exchange/production service, new Strategy, replay, LIVE or policy.
Future Hard Stop label is joined only AFTER causal pre-Entry feature formation.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import sys
from pathlib import Path

# Direct absolute-file invocation does not place repository root on sys.path.
# Keep import resolution pinned to this exact detached GitHub checkout.
SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_unified_portfolio_forensic_v1 as prior

SYMBOLS = ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
FOCUS = prior.FOCUS
GEOM = {
    "183d": Path("/data/cripta/research/runs/p90l90_bull_2025_holdout_v1/results/l53_geometry_full_2025_v1"),
    "90d": Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/results/l53_clean_v2"),
    "29d": Path("/data/cripta/research/runs/p90l90_prev7d_29d_20261025/results/l53_clean_29d"),
}
FEATURES = (
    "own_adverse_15m_pct",
    "own_adverse_60m_pct",
    "peer_adverse_15m_count",
    "peer_mean_adverse_15m_pct",
    "own_atr_pct_mid",
    "own_width_pct_mid",
)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(2 ** 20), b""):
            h.update(block)
    return h.hexdigest()


def geometry_identity(period: str, symbol: str) -> tuple[Path, str, int]:
    root = GEOM[period]
    path = root / "parts" / ("states_" + symbol + ".csv.gz")
    if root.is_symlink() or path.is_symlink():
        raise ValueError("unsafe symlink frozen geometry")
    if period == "183d":
        meta = json.loads((root / "qc.json").read_text())
        if meta.get("status") != "QC_PASS_FULL_2025_GEOMETRY_NOT_TRADING":
            raise ValueError("2025 frozen geometry QC not PASS")
        rec = meta["by_symbol"][symbol]
    elif period == "90d":
        meta = json.loads((root / "result_manifest.json").read_text())
        if (meta.get("status") != "COMPLETE"
                or meta.get("dataset") != "L5-3 CLEAN P90L90"
                or set(meta.get("symbols", [])) != set(SYMBOLS)):
            raise ValueError("90d geometry not complete")
        rec = next(x for x in meta["per_symbol"] if x["symbol"] == symbol)
        if (meta["files"]["parts/" + path.name]["sha256"]
                != rec["states_sha256"]):
            raise ValueError("90d geometry manifest internal mismatch")
    else:
        rec = json.loads((root / ("manifest_" + symbol + ".json")).read_text())
        if (rec.get("research") != "P90L90_SEP29_L53_CAUSAL_V1"
                or rec.get("symbol") != symbol or rec.get("files") != 39):
            raise ValueError("29d geometry manifest identity mismatch")
    expected, count = rec["states_sha256"], int(rec["states"])
    if sha(path) != expected:
        raise ValueError("frozen geometry SHA mismatch: " + symbol + period)
    return path, expected, count


def read_geometry(period: str) -> tuple[dict, dict]:
    """Only completed 5m price/ATR/width fields, no future states."""
    states: dict[str, dict[int, tuple[float, float, float]]] = {}
    proof = {}
    for symbol in SYMBOLS:
        path, expected, n = geometry_identity(period, symbol)
        rows = {}
        prev = None
        with gzip.open(path, "rt", newline="") as f:
            csv_rows = csv.DictReader(f)
            required = {"symbol", "state_effective_ts",
                        "source_bar_start_ts", "source_bar_end_ts",
                        "source_close", "atr_pct_mid", "working_width_pct_mid"}
            if not required.issubset(csv_rows.fieldnames or []):
                raise ValueError("missing state source fields")
            for row in csv_rows:
                t = int(row["state_effective_ts"])
                if (row["symbol"] != symbol or t % 300 != 0
                        or int(row["source_bar_start_ts"]) != t - 300
                        or int(row["source_bar_end_ts"]) != t
                        or (prev is not None and prev + 300 != t)):
                    raise ValueError("invalid completed bar continuity")
                v = (float(row["source_close"]), float(row["atr_pct_mid"]),
                     float(row["working_width_pct_mid"]))
                if (not all(map(math.isfinite, v))
                        or v[0] <= 0 or v[1] < 0):
                    raise ValueError("invalid causal state values")
                rows[t] = v
                prev = t
        if len(rows) != n:
            raise ValueError("wrong bar count")
        states[symbol] = rows
        proof[symbol] = {
            "rows": n, "sha256": expected,
            "first_state_ts": min(rows), "last_state_ts": max(rows),
        }
    return states, proof


def preentry_features(
    states: dict[str, dict[int, tuple[float, float, float]]],
    trade: dict,
) -> dict | None:
    entry = float(trade["entry_ts"])
    if not math.isfinite(entry):
        raise ValueError("invalid entry time")
    # Strictly before exact Entry (conservative for exact 5m boundaries).
    t = (math.ceil(entry / 300) - 1) * 300
    if not t < entry:
        raise ValueError("lookahead in as-of selection")
    symbol, side = trade["symbol"], trade["side"]
    if symbol not in SYMBOLS or side not in ("LONG", "SHORT"):
        raise ValueError("unknown symbol / position direction")
    adverse = -1 if side == "LONG" else 1
    prices = {}
    for coin in SYMBOLS:
        src = states[coin]
        a, b, c = src.get(t), src.get(t - 900), src.get(t - 3600)
        if a is None or b is None or c is None:
            return None  # explicit NO_DATA; no as-of substitution
        prices[coin] = {
            "m15": (a[0] / b[0] - 1) * 100,
            "m60": (a[0] / c[0] - 1) * 100,
            "atr": a[1], "width": a[2],
        }
    own = prices[symbol]
    peer = [prices[c] for c in SYMBOLS if c != symbol]
    return {
        "asof_5m_complete_ts": t,
        "lag_seconds": round(entry - t, 6),
        "own_adverse_15m_pct": round(adverse * own["m15"], 8),
        "own_adverse_60m_pct": round(adverse * own["m60"], 8),
        "peer_adverse_15m_count": sum(adverse * v["m15"] > 0 for v in peer),
        "peer_mean_adverse_15m_pct": round(
            sum(adverse * v["m15"] for v in peer) / 4, 8
        ),
        "own_atr_pct_mid": round(own["atr"], 8),
        "own_width_pct_mid": round(own["width"], 8),
    }


def matched(samples: list[dict]) -> dict:
    stops = [s for s in samples if s["hardstop"]]
    other = [s for s in samples if not s["hardstop"]]
    groups = collections.defaultdict(list)
    for c in other:
        groups[(c["symbol"], c["side"], c["entry_month"])].append(c)
    pairs = [
        (s, groups[(s["symbol"], s["side"], s["entry_month"])])
        for s in stops if groups[(s["symbol"], s["side"], s["entry_month"])]
    ]
    def mean(values):
        return round(sum(values) / len(values), 6) if values else None
    return {
        "stops": len(stops),
        "nonstops": len(other),
        "stops_with_same_period_symbol_side_month_controls": len(pairs),
        "unmatched_stops": len(stops) - len(pairs),
        "features": {
            name: {
                "raw_hardstop_mean": mean([float(s["features"][name])
                                           for s in stops]),
                "raw_nonstop_mean": mean([float(s["features"][name])
                                          for s in other]),
                "matched_hardstop_mean": mean(
                    [float(s["features"][name]) for s, _ in pairs]),
                "matched_control_mean": mean([
                    sum(float(c["features"][name]) for c in matches) /
                    len(matches) for _, matches in pairs
                ]),
                "matched_delta": mean([
                    float(s["features"][name]) -
                    sum(float(c["features"][name]) for c in matches) /
                    len(matches)
                    for s, matches in pairs
                ]),
            } for name in FEATURES
        },
    }


def breadth_summary(samples: list[dict]) -> dict:
    result = {}
    for n in range(5):
        rows = [s for s in samples
                if s["features"]["peer_adverse_15m_count"] == n]
        stops = sum(s["hardstop"] for s in rows)
        net = sum((prior.D(s["net_usdt"]) for s in rows), prior.D(0))
        result[str(n)] = {
            "trades": len(rows),
            "stops": stops,
            "stop_rate_pct": round(stops * 100 / len(rows), 4)
            if rows else None,
            "closed_net_after_fees_usdt": prior.money(net),
        }
    return result


def exit_day_clusters(samples: list[dict]) -> list[dict]:
    grouped = collections.defaultdict(list)
    for s in samples:
        if s["hardstop"]:
            grouped[s["exit_day"]].append(s)
    result = []
    for day, group in sorted(grouped.items()):
        if len(group) < 2:
            continue
        result.append({
            "utc_exit_date": day,
            "hardstops": len(group),
            "symbol_sides": [s["symbol"] + ":" + s["side"] for s in group],
            "mean_peer_adverse_15m_count_at_entry": round(
                sum(s["features"]["peer_adverse_15m_count"] for s in group)
                / len(group), 4
            ),
            "mean_own_adverse_15m_pct_at_entry": round(
                sum(s["features"]["own_adverse_15m_pct"] for s in group)
                / len(group), 6
            ),
        })
    return result


def analyze(periods=("183d", "90d", "29d")) -> dict:
    if os.name != "posix" or (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("requires exact cripta read/research actor")
    results = {}
    for period in periods:
        # Independent 24-case result/QC/provenance/ledger/checkpoint SHA gates.
        status, result, qc, ledger, daily = prior.read_verified(period)
        states, proof = read_geometry(period)
        samples = []
        no_data = 0
        for row in ledger[FOCUS]:
            f = preentry_features(states, row)
            if f is None:
                no_data += 1
                continue
            t = dt.datetime.fromtimestamp(float(row["entry_ts"]), dt.timezone.utc)
            exit_time = dt.datetime.fromtimestamp(
                float(row["exit_ts"]), dt.timezone.utc
            )
            samples.append({
                "symbol": row["symbol"], "side": row["side"],
                "entry_month": t.strftime("%Y-%m"),
                "exit_day": exit_time.date().isoformat(),
                "hardstop": row["exit_reason"] == "HARD_STOP",
                "net_usdt": row["net_usdt"], "features": f,
            })
        if len(samples) + no_data != len(ledger[FOCUS]):
            raise ValueError("full closed universe reconciliation failed")
        results[period] = {
            "producer_head": status["source_head"],
            "ledger_sha256": status["ledger_sha256"],
            "qc_sha256": status["qc_sha256"],
            "completed_bars": proof,
            "closed": len(ledger[FOCUS]),
            "features_complete": len(samples),
            "NO_DATA": no_data,
            "coverage": "FULL" if no_data == 0 else "PARTIAL_NO_DATA",
            "hardstops_with_features": sum(s["hardstop"] for s in samples),
            "matched_controls": matched(samples),
            "peer_adverse_breadth": breadth_summary(samples),
            "same_exit_day_stop_clusters": exit_day_clusters(samples),
        }
    return {
        "research": "P90L90_CAUSAL_PREENTRY_CROSSCOIN_FORENSIC_V1",
        "portfolio_case": FOCUS,
        "causal_cutoff": "last exact completed 5m state strictly before Entry",
        "matching": "same period + symbol + side + entry UTC month",
        "results": results,
        "limitations": [
            "Historical descriptive association, NOT blind OOS or causal model.",
            "Only four peer coins in this five-coin basket; no BTC/ETH/broad market.",
            "Controls may be confounded by calendar and dependent stop clusters.",
            "Hard Stop is a future label, not an input feature.",
            "Market costs fixed to original replay; no funding/slippage/spread/latency.",
            "Five breadth cells are descriptions, NOT policy cutoffs or replayed filters.",
            "Missing closed states are counted as NO_DATA, never zero.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=("183d", "90d", "29d", "all"),
                        default="183d")
    args = parser.parse_args()
    selected = ("183d", "90d", "29d") if args.period == "all" else (args.period,)
    print(json.dumps(analyze(selected), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
