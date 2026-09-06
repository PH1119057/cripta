from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bybit_workbench.mayak.research.historical_signal_backfill import load_baseline
from bybit_workbench.research.mtf_entry_v3 import EntryResearchV3Config, download_open_interest

VERSION = "mayak-oi5m-source-backfill-v1"
SOURCE_SEMANTICS = (
    "Bybit V5 linear open-interest intervalTime=5min; exact public historical API points"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _window(baseline: Path) -> tuple[list[Any], datetime, datetime]:
    signals = load_baseline(baseline)
    if not signals:
        raise ValueError("baseline has no signals")
    start = datetime.fromtimestamp(min(item.touch_epoch for item in signals), UTC) - timedelta(
        hours=2
    )
    end = datetime.fromtimestamp(max(item.touch_epoch for item in signals), UTC) + timedelta(
        minutes=5
    )
    start = start.replace(minute=(start.minute // 5) * 5, second=0, microsecond=0)
    end = end.replace(minute=(end.minute // 5) * 5, second=0, microsecond=0)
    end += timedelta(minutes=5)
    return signals, start, end


def _write_rows(path: Path, rows: Sequence[tuple[datetime, Any]]) -> None:
    tmp = path.with_name(path.name + ".partial")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "open_interest"])
        writer.writeheader()
        for observed_at, value in rows:
            writer.writerow(
                {"timestamp": observed_at.astimezone(UTC).isoformat(), "open_interest": str(value)}
            )
    os.replace(tmp, path)


def run(
    *,
    baseline: Path,
    symbols: tuple[str, ...],
    output_dir: Path,
    source_commit: str,
    endpoint: str = "https://api.bybit.kz",
    workers: int = 3,
    expected_signals: int | None = None,
) -> dict[str, Any]:
    if len(source_commit) != 40 or any(ch not in "0123456789abcdef" for ch in source_commit):
        raise ValueError("source_commit must be a full lowercase Git SHA")
    if workers <= 0:
        raise ValueError("workers must be positive")
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("symbols must be a non-empty unique tuple")
    signals, start, end = _window(baseline)
    if expected_signals is not None and len(signals) != expected_signals:
        raise ValueError(
            f"baseline signal count mismatch expected={expected_signals} actual={len(signals)}"
        )
    baseline_symbols = {item.symbol for item in signals}
    if baseline_symbols != set(symbols):
        raise ValueError(
            f"baseline/symbol panel mismatch baseline={sorted(baseline_symbols)} "
            f"requested={sorted(symbols)}"
        )

    data_dir = output_dir / "oi_5m"
    data_dir.mkdir(parents=True, exist_ok=True)

    def one(symbol: str) -> dict[str, Any]:
        started = time.monotonic()
        config = EntryResearchV3Config(symbol=symbol, endpoint=endpoint)
        rows = download_open_interest(config, start_at=start, end_at=end)
        if not rows:
            raise RuntimeError(f"empty OI result for {symbol}")
        path = data_dir / f"{symbol}.csv"
        _write_rows(path, rows)
        result = {
            "symbol": symbol,
            "rows": len(rows),
            "first": rows[0][0].astimezone(UTC).isoformat(),
            "last": rows[-1][0].astimezone(UTC).isoformat(),
            "sha256": _sha256(path),
            "elapsed_s": round(time.monotonic() - started, 3),
        }
        print("OI_DONE " + json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        return result

    files: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(workers, len(symbols))) as pool:
        futures = {pool.submit(one, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            files.append(future.result())
    files.sort(key=lambda item: str(item["symbol"]))

    manifest = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "project_commit": source_commit,
        "downloader_code_sha256": _sha256(Path(__file__).resolve()),
        "baseline": str(baseline),
        "baseline_sha256": _sha256(baseline),
        "signals": len(signals),
        "endpoint": endpoint,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbols": list(symbols),
        "files": files,
        "source_semantics": SOURCE_SEMANTICS,
        "outcome_used": False,
        "trading_effect": "NONE",
    }
    _atomic_json(output_dir / "MANIFEST.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exact public 5m OI source backfill for MAYAK")
    parser.add_argument("--baseline-csv", type=Path, required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--endpoint", default="https://api.bybit.kz")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--expected-signals", type=int)
    args = parser.parse_args(argv)
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    manifest = run(
        baseline=args.baseline_csv,
        symbols=symbols,
        output_dir=args.output_dir,
        source_commit=args.source_commit,
        endpoint=args.endpoint,
        workers=args.workers,
        expected_signals=args.expected_signals,
    )
    total_rows = sum(int(item.get("rows", 0)) for item in manifest.get("files", []))
    print(
        f"MAYAK_OI_BACKFILL=PASS symbols={len(symbols)} rows={total_rows}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
