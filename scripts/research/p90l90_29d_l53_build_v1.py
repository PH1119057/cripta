#!/usr/bin/env python3
"""Causal, independent L5-3 state warmup for the Sept-Oct P90L90 OOS study.

Uses the reviewed immutable L5-3 CLEAN algorithm, not previous 29d
working-range Entry artifacts. Writes new files only on data research disk.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scripts.research import p90l90_l53_clean_v2 as clean
from scripts.research import p90l90_29d_sources_v1 as sources

OUTPUT = sources.ROOT / "results/l53_clean_29d"


def process(symbol: str):
    if symbol not in sources.SYMBOLS:
        raise ValueError("outside owner-approved five-symbol cohort")
    if os.geteuid() != 107:
        raise PermissionError("actor must be cripta uid107")
    if not sources.ROOT.is_dir():
        raise FileNotFoundError("source acquisition not complete")
    manifest = sources.ROOT / "inputs" / f"manifest_{symbol}.json"
    d = json.loads(manifest.read_text())
    if d["symbol"] != symbol or len(d["files"]) != 39:
        raise ValueError("incomplete raw source lineage")
    for item in d["files"]:
        path = sources.ROOT / "inputs" / symbol / "public_trades" / item["file"]
        if sources.hash_file(path) != item["sha256"]:
            raise ValueError("input SHA drift")
    parent = OUTPUT / "parts"
    if not os.access(sources.ROOT, os.W_OK | os.X_OK):
        raise PermissionError("research output root not writable")
    if (parent / f"states_{symbol}.csv.gz").exists() or (
        parent / f"touches_{symbol}.csv.gz"
    ).exists():
        raise FileExistsError("immutable L5 symbol output collision")
    parent.mkdir(parents=True, exist_ok=True, mode=0o2770)
    meta = clean.process_symbol(
        symbol, str(sources.ROOT / "inputs"), str(parent),
        first_date="2026-09-11", last_date="2026-10-19",
    )
    output = OUTPUT / f"manifest_{symbol}.json"
    output.write_text(json.dumps({
        "research": "P90L90_SEP29_L53_CAUSAL_V1",
        "source_manifest_sha256": sources.hash_file(manifest),
        "symbol": symbol,
        "snapshot_source": "scripts/research/p90l90_l53_clean_v2.py",
        **meta,
    }, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"phase": "L53_DONE", "symbol": symbol, **meta}), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=sources.SYMBOLS, required=True)
    a = p.parse_args()
    process(a.symbol)


if __name__ == "__main__":
    main()
