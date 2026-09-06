from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import urllib.parse
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bybit_workbench.research.research_http import read_json_with_retry

VERSION = "mayak-historical-funding-backfill-v1"
ENDPOINT_PATH = "/v5/market/funding/history"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_sha256() -> str:
    return _sha256(Path(__file__).resolve())


def _parse_at(value: str) -> datetime:
    item = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if item.tzinfo is None:
        item = item.replace(tzinfo=UTC)
    return item.astimezone(UTC)


def _page_rows(payload: dict[str, Any]) -> list[tuple[int, str]]:
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(
            f"funding history request failed: {payload.get('retCode')} {payload.get('retMsg')}"
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("funding result must be an object")
    raw_list = result.get("list")
    if not isinstance(raw_list, list):
        raise ValueError("funding result.list must be an array")
    rows: list[tuple[int, str]] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        timestamp_ms = int(str(raw["fundingRateTimestamp"]))
        rate = str(raw["fundingRate"])
        float(rate)
        rows.append((timestamp_ms, rate))
    return rows


def download_symbol(
    symbol: str,
    *,
    endpoint: str,
    start: datetime,
    end: datetime,
    output_dir: Path,
) -> dict[str, Any]:
    if end <= start:
        raise ValueError("end must be after start")
    start_ms = int(start.timestamp() * 1000)
    cursor_end_ms = int(end.timestamp() * 1000) - 1
    rows: dict[int, str] = {}
    pages = 0
    while cursor_end_ms >= start_ms:
        pages += 1
        params = {
            "category": "linear",
            "symbol": symbol,
            "startTime": start_ms,
            "endTime": cursor_end_ms,
            "limit": 200,
        }
        url = f"{endpoint.rstrip('/')}{ENDPOINT_PATH}?{urllib.parse.urlencode(params)}"
        payload = read_json_with_retry(url, label=f"{symbol} funding page {pages}")
        page = _page_rows(payload)
        if not page:
            break
        accepted = [(at, rate) for at, rate in page if start_ms <= at < int(end.timestamp() * 1000)]
        for at, rate in accepted:
            old = rows.get(at)
            if old is not None and old != rate:
                raise ValueError(f"conflicting duplicate funding row: {symbol} {at}")
            rows[at] = rate
        earliest = min(at for at, _ in page)
        if earliest <= start_ms or len(page) < 200:
            break
        next_end = earliest - 1
        if next_end >= cursor_end_ms:
            raise RuntimeError(f"funding pagination did not move for {symbol}")
        cursor_end_ms = next_end

    if not rows:
        raise RuntimeError(f"no funding rows for {symbol}")
    target = output_dir / "funding" / f"{symbol}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".partial")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "funding_rate"], delimiter=";")
        writer.writeheader()
        for at in sorted(rows):
            writer.writerow(
                {
                    "timestamp": datetime.fromtimestamp(at / 1000, UTC).isoformat(),
                    "funding_rate": rows[at],
                }
            )
    os.replace(tmp, target)
    return {
        "symbol": symbol,
        "rows": len(rows),
        "first": datetime.fromtimestamp(min(rows) / 1000, UTC).isoformat(),
        "last": datetime.fromtimestamp(max(rows) / 1000, UTC).isoformat(),
        "sha256": _sha256(target),
        "pages": pages,
    }


def _atomic_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def run(
    *,
    symbols: tuple[str, ...],
    endpoint: str,
    start: datetime,
    end: datetime,
    output_dir: Path,
    source_commit: str,
    workers: int,
) -> dict[str, Any]:
    if len(source_commit) != 40:
        raise ValueError("source_commit must be a full Git SHA")
    if workers <= 0:
        raise ValueError("workers must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(workers, len(symbols))) as pool:
        futures = {
            pool.submit(
                download_symbol,
                symbol,
                endpoint=endpoint,
                start=start,
                end=end,
                output_dir=output_dir,
            ): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                "FUNDING_DONE", json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True
            )
    results.sort(key=lambda row: row["symbol"])
    manifest = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "project_commit": source_commit,
        "downloader_code_sha256": _code_sha256(),
        "endpoint_semantics": "public linear historical funding settlement rate",
        "period_start": start.isoformat(),
        "period_end_exclusive": end.isoformat(),
        "symbols": list(symbols),
        "files": results,
        "rows": sum(int(row["rows"]) for row in results),
        "outcome_used": False,
        "trading_effect": "NONE",
    }
    _atomic_json(output_dir / "MANIFEST.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exact public funding source backfill for MAYAK")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--endpoint", default="https://api.bybit.kz")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args(argv)
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    manifest = run(
        symbols=symbols,
        endpoint=args.endpoint,
        start=_parse_at(args.start),
        end=_parse_at(args.end),
        output_dir=args.output_dir,
        source_commit=args.source_commit,
        workers=args.workers,
    )
    print(f"MAYAK_FUNDING_BACKFILL=PASS symbols={len(symbols)} rows={manifest['rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
