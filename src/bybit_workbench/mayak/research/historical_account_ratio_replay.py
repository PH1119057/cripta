from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import urllib.parse
from collections import defaultdict
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bybit_workbench.mayak.research.historical_signal_backfill import Signal, load_baseline
from bybit_workbench.mayak.research.objective_replay import CausalMayakReplay, MarketEvent
from bybit_workbench.research.research_http import read_json_with_retry

VERSION = "mayak-historical-account-ratio-replay-v1"
ENDPOINT_PATH = "/v5/market/account-ratio"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_symbol(
    symbol: str, endpoint: str, start: datetime, end: datetime, out: Path
) -> dict[str, Any]:
    start_ms = int(start.timestamp() * 1000)
    cursor = int(end.timestamp() * 1000) - 1
    rows: dict[int, tuple[str, str]] = {}
    pages = 0
    while cursor >= start_ms:
        pages += 1
        params = {
            "category": "linear",
            "symbol": symbol,
            "period": "5min",
            "limit": 500,
            "startTime": start_ms,
            "endTime": cursor,
        }
        url = f"{endpoint.rstrip('/')}{ENDPOINT_PATH}?{urllib.parse.urlencode(params)}"
        payload = read_json_with_retry(url, label=f"{symbol} account-ratio page {pages}")
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(f"account ratio failed {symbol}: {payload.get('retMsg')}")
        result = payload.get("result")
        raw = result.get("list") if isinstance(result, dict) else None
        if not isinstance(raw, list):
            raise ValueError("account-ratio result.list missing")
        page = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            at = int(str(item["timestamp"]))
            long = str(item["buyRatio"])
            short = str(item["sellRatio"])
            lv = float(long)
            sv = float(short)
            if lv < 0 or sv < 0 or abs((lv + sv) - 1.0) > 0.02:
                raise ValueError(f"invalid ratio {symbol} {at}")
            page.append((at, long, short))
            if start_ms <= at < int(end.timestamp() * 1000):
                rows[at] = (long, short)
        if not page:
            break
        earliest = min(x[0] for x in page)
        if earliest <= start_ms or len(page) < 500:
            break
        nxt = earliest - 1
        if nxt >= cursor:
            raise RuntimeError(f"pagination did not move {symbol}")
        cursor = nxt
    if not rows:
        raise RuntimeError(f"no account-ratio rows {symbol}")
    path = out / f"{symbol}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "long_ratio", "short_ratio"], delimiter=";")
        w.writeheader()
        for at in sorted(rows):
            w.writerow(
                {
                    "timestamp": datetime.fromtimestamp(at / 1000, UTC).isoformat(),
                    "long_ratio": rows[at][0],
                    "short_ratio": rows[at][1],
                }
            )
    os.replace(tmp, path)
    return {
        "symbol": symbol,
        "rows": len(rows),
        "pages": pages,
        "first": datetime.fromtimestamp(min(rows) / 1000, UTC).isoformat(),
        "last": datetime.fromtimestamp(max(rows) / 1000, UTC).isoformat(),
        "sha256": _sha256(path),
    }


def _iter_ratio(path: Path, start: float, end: float) -> Iterator[tuple[float, float, float]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            at = datetime.fromisoformat(row["timestamp"]).astimezone(UTC).timestamp()
            if at < start:
                continue
            if at > end:
                break
            yield at, float(row["long_ratio"]), float(row["short_ratio"])


def _replay_symbol(symbol: str, signals: Sequence[Signal], path: Path) -> list[dict[str, Any]]:
    ordered = sorted(signals, key=lambda x: x.touch_epoch)
    start = ordered[0].touch_epoch - 600
    end = ordered[-1].touch_epoch
    replay = CausalMayakReplay((symbol,), exact_liquidations=False)
    replay.set_supported("linear", {symbol})
    events = iter(_iter_ratio(path, start, end))
    current = next(events, None)
    out = []
    for signal in ordered:
        while current is not None and current[0] <= signal.touch_epoch:
            at, long, short = current
            replay.feed(
                MarketEvent(
                    event_at=at,
                    kind="TICKER",
                    symbol=symbol,
                    payload={"long_ratio": long, "short_ratio": short},
                )
            )
            current = next(events, None)
        snap = replay.snapshot(signal.touch_epoch)
        pos = snap["coin_market_contexts"][symbol]["payload"]["positioning"]
        long = pos.get("long_ratio")
        short = pos.get("short_ratio")
        out.append(
            {
                "signal_key": signal.key,
                "symbol": symbol,
                "direction": signal.direction,
                "touch_at": signal.touch_at,
                "positioning_long_ratio": long,
                "positioning_short_ratio": short,
                "positioning_long_short_imbalance": None
                if long is None or short is None
                else float(long) - float(short),
            }
        )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Exact causal account-ratio replay")
    p.add_argument("--baseline-csv", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--symbols", required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--endpoint", default="https://api.bybit.com")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--expected-signals", type=int, default=1063)
    a = p.parse_args(argv)
    symbols = tuple(x.strip().upper() for x in a.symbols.split(",") if x.strip())
    signals = load_baseline(a.baseline_csv)
    if len(signals) != a.expected_signals:
        raise ValueError(f"signal count mismatch {len(signals)}")
    start = datetime.fromtimestamp(min(x.touch_epoch for x in signals) - 600, UTC)
    end = datetime.fromtimestamp(max(x.touch_epoch for x in signals) + 300, UTC)
    src = a.output_dir / "source"
    src.mkdir(parents=True, exist_ok=True)
    files = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        fut = {ex.submit(_download_symbol, s, a.endpoint, start, end, src): s for s in symbols}
        for f in as_completed(fut):
            item = f.result()
            files.append(item)
            print(
                f"ACCOUNT_RATIO_SOURCE symbol={item['symbol']} rows={item['rows']} stage=DONE",
                flush=True,
            )
    by = defaultdict(list)
    for s in signals:
        by[s.symbol].append(s)
    rows = []
    for symbol in symbols:
        rows.extend(_replay_symbol(symbol, by[symbol], src / f"{symbol}.csv"))
    rows.sort(key=lambda r: r["touch_at"])
    features = a.output_dir / "MAYAK_ACCOUNT_RATIO_FEATURES.csv"
    with features.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    manifest = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "project_commit": a.source_commit,
        "replay_code_sha256": _sha256(Path(__file__).resolve()),
        "baseline_sha256": _sha256(a.baseline_csv),
        "signals": len(rows),
        "symbols": list(symbols),
        "files": sorted(files, key=lambda x: x["symbol"]),
        "source_semantics": "Bybit V5 long/short account ratio 5min; account counts, not capital",
        "outcome_used_by_replay": False,
        "trading_effect": "NONE",
    }
    (a.output_dir / "RUN_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"MAYAK_ACCOUNT_RATIO_REPLAY=PASS signals={len(rows)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
