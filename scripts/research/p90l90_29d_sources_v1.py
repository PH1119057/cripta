#!/usr/bin/env python3
"""P90L90 Sep21-Oct19 2026 OOS native sources and causal BTC/ETH hour closes.

Research-only. Never uses keys, DB, orders, or runtime. Actor=cripta uid107.
Writes only under /data/cripta/research/runs/p90l90_prev7d_29d_20261025.
Independent untouched source history for future two-arm Entry/Exit replay.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import tempfile
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SYMBOLS = ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
ROOT = Path("/data/cripta/research/runs/p90l90_prev7d_29d_20261025")
PARENT = Path("/data/cripta/research/runs")
EXISTING = PARENT / "l53_h9_entry_30d_20261020_v1/raw"
FIRST = date(2026, 9, 11)
LINK_FROM = date(2026, 9, 18)
LAST = date(2026, 10, 19)
MARKET_FIRST = datetime(2026, 9, 11, tzinfo=timezone.utc)
MARKET_END = datetime(2026, 10, 20, tzinfo=timezone.utc)
MAX_DOWNLOAD = 250 * 1024 * 1024
REQUIRED = {"timestamp", "symbol", "price", "tickDirection", "trdMatchID"}


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for b in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def dates(begin: date, end: date):
    return (begin + timedelta(days=i) for i in range((end - begin).days + 1))


def preflight():
    if os.geteuid() != 107:
        raise PermissionError("exact research actor must be cripta uid107")
    if PARENT.stat().st_uid != 107 or not os.access(PARENT, os.W_OK | os.X_OK):
        raise PermissionError("research parent not writable by cripta")
    if ROOT.exists() and (not ROOT.is_dir() or ROOT.is_symlink()
                          or ROOT.stat().st_uid != 107):
        raise PermissionError("untrusted existing output root")
    os.umask(0o007)
    ROOT.mkdir(mode=0o2770, exist_ok=True)


def day_check(path: Path, sym: str, day: date):
    start = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
    low, high, rows, previous = None, None, 0, None
    with gzip.open(path, "rt", newline="") as source:
        rd = csv.DictReader(source)
        if not REQUIRED.issubset(rd.fieldnames or ()):
            raise ValueError("native Bybit trade schema mismatch")
        for rec in rd:
            t = float(rec["timestamp"])
            if not start <= t < start + 86400 or rec["symbol"] != sym:
                raise ValueError("off-day/off-symbol Bybit trade")
            if previous is not None and t < previous:
                raise ValueError("nonchronological public-trade tape")
            if not rec["trdMatchID"] or not rec["tickDirection"] or float(rec["price"]) <= 0:
                raise ValueError("invalid trade identity/price/direction")
            previous = t
            low = t if low is None else low
            high = t
            rows += 1
    if rows == 0:
        raise ValueError("empty native Bybit day")
    return {"rows": rows, "first_ts": low, "last_ts": high}


def fetch_archive(path: Path, sym: str, day: date):
    url = f"https://public.bybit.com/trading/{sym}/{path.name}"
    req = urllib.request.Request(url, headers={"User-Agent": "cripta-p90l90-29d/1"})
    tmp = None
    try:
        with urllib.request.urlopen(req, timeout=50) as response:
            if response.status != 200:
                raise ValueError("archive http failure")
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > MAX_DOWNLOAD:
                raise ValueError("source archive too large")
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".part_", delete=False) as fh:
                tmp = Path(fh.name)
                size = 0
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise ValueError("oversized download")
                    fh.write(chunk)
                fh.flush()
                os.fsync(fh.fileno())
        if not size or (declared and size != declared):
            raise ValueError("incomplete Bybit download")
        qc = day_check(tmp, sym, day)
        if path.exists() or path.is_symlink():
            raise FileExistsError("immutable raw source collision")
        os.rename(tmp, path)
        tmp = None
        return {**qc, "source_url": url, "new_download": True}
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def raw_symbol(sym: str):
    if sym not in SYMBOLS:
        raise ValueError("outside five-coins cohort")
    dest = ROOT / "inputs" / sym / "public_trades"
    dest.mkdir(mode=0o2770, parents=True, exist_ok=True)
    manifest = []
    for day in dates(FIRST, LAST):
        filename = f"{sym}{day.isoformat()}.csv.gz"
        path = dest / filename
        if day >= LINK_FROM:
            old = EXISTING / sym / "public_trades" / filename
            if not old.is_file() or old.stat().st_size <= 0:
                raise FileNotFoundError(f"missing existing source {filename}")
            if not path.is_symlink():
                if path.exists():
                    raise FileExistsError("raw collision with nonlink")
                path.symlink_to(old.resolve())
            if path.resolve() != old.resolve():
                raise ValueError("linked source path drift")
            qc = day_check(path, sym, day)
            qc["new_download"] = False
        else:
            qc = (day_check(path, sym, day) if path.is_file()
                  else fetch_archive(path, sym, day))
            qc["new_download"] = True
        record = {"file": filename, "sha256": hash_file(path),
                  "bytes": path.stat().st_size, "date": day.isoformat(), **qc}
        manifest.append(record)
        print(json.dumps({"phase": "DAY_PASS", "symbol": sym, "date": str(day),
                          "rows": qc["rows"], "new": qc["new_download"]}), flush=True)
    if len(manifest) != (LAST - FIRST).days + 1:
        raise ValueError("missing raw day")
    out = ROOT / "inputs" / f"manifest_{sym}.json"
    if out.exists():
        old = json.loads(out.read_text())
        if old["files"] != manifest:
            raise ValueError("immutable manifest drift")
    else:
        out.write_text(json.dumps({"research": "P90L90_OOS_29D_NATIVE_V1",
                                   "symbol": sym, "files": manifest,
                                   "warmup_only_before": "2026-09-21"},
                                  indent=2, sort_keys=True) + "\n")
    print(json.dumps({"phase": "SYMBOL_PASS", "symbol": sym,
                      "days": len(manifest), "manifest_sha": hash_file(out)}), flush=True)


def hourly_symbol(symbol: str):
    url = "https://api.bybit.com/v5/market/kline"
    start_ms = int(MARKET_FIRST.timestamp() * 1000)
    end_ms = int(MARKET_END.timestamp() * 1000)
    params = urllib.parse.urlencode({
        "category": "linear", "symbol": symbol, "interval": "60",
        "start": start_ms, "end": end_ms - 1, "limit": 1000,
    })
    req = urllib.request.Request(url + "?" + params,
                                 headers={"User-Agent": "cripta-p90l90-29d/1"})
    with urllib.request.urlopen(req, timeout=45) as response:
        payload = json.load(response)
    if payload.get("retCode") != 0 or payload.get("result", {}).get("symbol") != symbol:
        raise ValueError("Bybit v5 kline response invalid")
    rows = payload["result"]["list"]
    by_end = {}
    for r in rows:
        start = int(r[0]) // 1000
        if start < start_ms // 1000 or start >= end_ms // 1000:
            continue
        if start % 3600 or start in by_end:
            raise ValueError("duplicate/unaligned Bybit hourly kline")
        price = float(r[4])
        if not 0 < price < float("inf"):
            raise ValueError("invalid BTC ETH hourly close")
        by_end[start + 3600] = r[4]
    expected = list(range(start_ms // 1000 + 3600, end_ms // 1000 + 1, 3600))
    if sorted(by_end) != expected:
        raise ValueError(f"hourly source incomplete {symbol} got {len(by_end)}/{len(expected)}")
    return by_end, payload


def market():
    folder = ROOT / "market"
    folder.mkdir(mode=0o2770, parents=True, exist_ok=True)
    csv_path = folder / "btc_eth_hourly_closed.csv"
    manifest = folder / "manifest.json"
    if csv_path.exists() or manifest.exists():
        if not csv_path.is_file() or not manifest.is_file():
            raise ValueError("partial immutable market source")
        print(json.dumps({"phase": "MARKET_EXISTS", "sha256": hash_file(csv_path)}))
        return
    btc, pb = hourly_symbol("BTCUSDT")
    eth, pe = hourly_symbol("ETHUSDT")
    if list(btc) != list(eth):
        raise ValueError("market hourly BTC/ETH timestamps disagree")
    with csv_path.open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["ts", "btc", "eth"])
        for ts in sorted(btc):
            writer.writerow([ts, btc[ts], eth[ts]])
    meta = {
        "source": "Bybit V5 public /v5/market/kline",
        "category": "linear", "interval": "60",
        "availability": "start_time_plus_3600_and_strictly_less_than_trade_timestamp",
        "bars": len(btc), "first_close_ts": min(btc), "last_close_ts": max(btc),
        "csv_sha256": hash_file(csv_path),
        "btc_source_sha256": hashlib.sha256(json.dumps(pb, sort_keys=True).encode()).hexdigest(),
        "eth_source_sha256": hashlib.sha256(json.dumps(pe, sort_keys=True).encode()).hexdigest(),
        "not_trading_execution_data": True,
    }
    manifest.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"phase": "MARKET_PASS", **meta}), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phase", choices=("raw", "market"), required=True)
    p.add_argument("--symbol", choices=SYMBOLS)
    a = p.parse_args()
    preflight()
    if a.phase == "raw":
        if not a.symbol:
            p.error("--symbol required for raw")
        raw_symbol(a.symbol)
    else:
        if a.symbol:
            p.error("--symbol forbidden for market")
        market()


if __name__ == "__main__":
    main()
