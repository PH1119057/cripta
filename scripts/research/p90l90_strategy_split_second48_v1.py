#!/usr/bin/env python3
"""Exact 90-day P90L90 causal replay: two independent Strategy SECOND48 domains.

Research-only, on the data disk, actor cripta. This is the frozen original
P90L90 Entry/Exit/native-tick simulator executed independently for the exact
two owner-approved coin universes, with SECOND_48H and no-embargo baseline.
All raw Bybit data stay in place; no PG/runtime/Exchange commands.

Writes one immutable direct-child output per Strategy plus one summary child,
all under the existing P90L90 warmup research results directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from decimal import Decimal
from pathlib import Path

from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as replay

OWNER_DECISION = "2026-10-28-issue-243-STRATEGY_VERSION-SECOND48"
GROUPS = (
    ("MAIN_LTC_INJ", ("LTCUSDT", "INJUSDT")),
    ("SUB_DOT_ARB_APT", ("DOTUSDT", "ARBUSDT", "APTUSDT")),
)
SCENARIOS = {"BASELINE": (0, None), "SECOND_48H": (2, 48)}
ROOT = replay.previous.ROOT
RESULTS = ROOT / "results"
ORIGINAL_RESULT = RESULTS / "entry_only_embargo_90d_20261026_v1" / "result.json"


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _output_path(name: str) -> Path:
    result = RESULTS / name
    if result.is_symlink() or result.exists() or result.parent.resolve() != RESULTS.resolve():
        raise FileExistsError(f"immutable output already exists: {result}")
    return result


def run(*, suffix: str, smoke_hours: int) -> dict[str, object]:
    if os.geteuid() != 107:
        raise PermissionError("P90L90 research actor must be cripta uid107")
    if not suffix or len(suffix) > 64 or any(
        ch not in "abcdefghijklmnopqrstuvwxyz0123456789_-"
        for ch in suffix
    ):
        raise ValueError("suffix must be lowercase safe filename token")
    if not 0 <= smoke_hours <= 24:
        raise ValueError("bounded smoke must be in 0..24h")
    if RESULTS.stat().st_uid != 107 or not os.access(RESULTS, os.W_OK | os.X_OK):
        raise PermissionError("P90L90 data-disk results parent is not writable by cripta")
    names = {name: _output_path(f"p90l90_strategy_second48_{name.lower()}_{suffix}")
             for name, _ in GROUPS}
    summary_path = _output_path(f"p90l90_strategy_second48_dual_{suffix}")
    if not ORIGINAL_RESULT.is_file():
        raise FileNotFoundError("canonical original P90L90 baseline manifest missing")
    original = json.loads(ORIGINAL_RESULT.read_text())
    if original["status"] != "COMPLETE_HISTORICAL_PROXY_NOT_LIVE" or (
        tuple(original["symbols"]) != tuple(replay.previous.SYMBOLS)
    ):
        raise ValueError("original P90L90 research manifest identity drift")
    if original["baseline_trade_by_trade_parity"] is not True:
        raise ValueError("original baseline not trade-by-trade verified")
    if {symbol for _, group in GROUPS for symbol in group} != set(original["symbols"]):
        raise ValueError("owner-approved Strategy universes not exact historical set")
    if len({sym for _, group in GROUPS for sym in group}) != 5:
        raise ValueError("Strategy universe overlap/duplicate")
    replay.SCENARIOS = SCENARIOS
    per_strategy = {}
    totals = {k: Decimal("0") for k in SCENARIOS}
    total_rows = 0
    for name, symbols in GROUPS:
        replay.SYMBOLS = symbols
        # The frozen reference gets only this Strategy's native-tick stream.
        # Its per-book Hard Stop state therefore cannot affect another Strategy.
        response = replay.run("90d", names[name], smoke_hours)
        if smoke_hours:
            if response["status"] != "SMOKE_PASS":
                raise RuntimeError("split P90L90 smoke did not pass")
            per_strategy[name] = response
            continue
        if response["status"] != "COMPLETE":
            raise RuntimeError("split P90L90 Strategy replay incomplete")
        report = json.loads((names[name] / "result.json").read_text())
        if report["baseline_trade_by_trade_parity"] is not True or (
            set(report["scenarios"]) != set(SCENARIOS)
        ) or tuple(report["symbols"]) != symbols:
            raise ValueError("split P90L90 Strategy lineage/parity mismatch")
        for key in SCENARIOS:
            totals[key] += Decimal(report["net_pnl_usdt"][key])
        total_rows += sum(int(x) for x in report["source_rows"].values())
        per_strategy[name] = {
            "strategy_symbols": list(symbols),
            "source_result": str(names[name] / "result.json"),
            "result_sha256": _hash(names[name] / "result.json"),
            "trade_ledger_sha256": report["ledger_sha256"],
            "modeled_net_after_fees_usdt": report["net_pnl_usdt"],
            "raw_native_trade_source_rows": report["source_rows"],
            "baseline_trade_by_trade_parity": True,
        }
    if smoke_hours:
        return {
            "status": "SMOKE_PASS",
            "research": "P90L90_STRATEGY_SPLIT_SECOND48_V1",
            "hours": smoke_hours,
            "strategies": per_strategy,
            "DB_WRITES": 0,
        }
    if totals["BASELINE"] != Decimal(original["net_pnl_usdt"]["BASELINE"]):
        raise ValueError("split portfolios' baseline after-fee net != five-coin baseline")
    if total_rows != sum(int(x) for x in original["source_rows"].values()):
        raise ValueError("split portfolios did not cover all native source rows")
    result = {
        "status": "COMPLETE_HISTORICAL_PROXY_NOT_LIVE",
        "research": "P90L90_STRATEGY_SPLIT_SECOND48_V1",
        "owner_decision": OWNER_DECISION,
        "period_utc": original["period_utc"],
        "strategy_scope": "STRATEGY_VERSION",
        "SECOND48": {
            "hardstop_count": 2, "rolling_hours": 48,
            "new_entry_pause_hours": 48, "close_existing_positions": False,
        },
        "scenarios": list(SCENARIOS),
        "strategies": per_strategy,
        "totals_net_after_fees_usdt": {
            key: str(value) for key, value in totals.items()
        },
        "historical_global_secondary_net_after_fees_usdt":
            original["net_pnl_usdt"]["SECOND_48H"],
        "global_vs_split_comparable": False,
        "baseline_trade_by_trade_parity": True,
        "raw_native_trade_source_rows": total_rows,
        "unmodeled": original["unmodeled"],
        "no_exchange_orders": True,
        "no_strategy_activation": True,
    }
    summary_path.mkdir(mode=0o2770)
    snap = summary_path / "source_snapshot"
    snap.mkdir(mode=0o2770)
    for source in (
        Path(__file__), Path(replay.__file__), Path(replay.previous.__file__),
        Path(replay.base.__file__),
    ):
        dest = snap / source.name
        shutil.copyfile(source, dest)
    result["source_snapshot_sha256"] = {
        path.name: _hash(path) for path in snap.iterdir()
    }
    result["original_manifest_sha256"] = _hash(ORIGINAL_RESULT)
    with (summary_path / "result.json").open("x") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    result["output"] = str(summary_path / "result.json")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--smoke-hours", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(
        run(suffix=args.suffix, smoke_hours=args.smoke_hours), sort_keys=True
    ), flush=True)


if __name__ == "__main__":
    main()
