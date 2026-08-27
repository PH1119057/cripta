from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PERIOD = "20260518_20260816"
TRADE_START = date(2026, 5, 17)
DATA_START = date(2026, 5, 18)
LAST_DAY = date(2026, 8, 15)
INDICATORS = ("BTCUSDT", "ETHUSDT")
TRADING = (
    "1000PEPEUSDT", "AAVEUSDT", "ADAUSDT", "AVAXUSDT", "BNBUSDT",
    "DOGEUSDT", "LINKUSDT", "LTCUSDT", "SOLUSDT", "SUIUSDT",
    "UNIUSDT", "XRPUSDT",
)
SYMBOLS = INDICATORS + TRADING


def days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def content_length(url: str) -> int | None:
    try:
        request = Request(url, method="HEAD", headers={"User-Agent": "cripta-fetch/1"})
        with urlopen(request, timeout=30) as response:
            value = response.headers.get("Content-Length")
            return int(value) if value else 0
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None


def download(url: str, destination: Path, expected: int) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and (expected <= 0 or destination.stat().st_size == expected):
        return "cached"
    part = destination.with_suffix(destination.suffix + ".part")
    command = [
        "curl", "--location", "--fail", "--retry", "8", "--retry-all-errors",
        "--retry-delay", "3", "--connect-timeout", "20", "--continue-at", "-",
        "--output", str(part), url,
    ]
    result = subprocess.run(command, check=False)
    if result.returncode == 33:
        part.unlink(missing_ok=True)
        command[command.index("--continue-at"):command.index("--continue-at") + 2] = []
        result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"curl exit {result.returncode}: {url}")
    if expected > 0 and part.stat().st_size != expected:
        raise RuntimeError(f"size mismatch: {part} got={part.stat().st_size} expected={expected}")
    part.replace(destination)
    return "downloaded"


def write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/data/cripta/datasets/raw"))
    parser.add_argument("--public-only", action="store_true")
    parser.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    parser.add_argument("--state-name", default="download_state.json")
    parser.add_argument("--reserve-gb", type=float, default=0.0)
    args = parser.parse_args()
    symbols = tuple(dict.fromkeys(symbol.upper() for symbol in args.symbols))
    root = args.root / PERIOD
    root.mkdir(parents=True, exist_ok=True)
    if Path(args.state_name).name != args.state_name:
        parser.error("--state-name must be a filename")
    state_path = root / args.state_name
    expected_files = len(symbols) * (91 + (0 if args.public_only else 90))
    state: dict[str, object] = {
        "period": PERIOD,
        "evaluation_start": "2026-05-18T00:00:00Z",
        "evaluation_end": "2026-08-16T00:00:00Z",
        "public_trade_days": ["2026-05-17", "2026-08-15"],
        "orderbook_days": ["2026-05-18", "2026-08-15"],
        "indicator_symbols": list(INDICATORS),
        "trading_symbols": list(TRADING),
        "symbols": list(symbols),
        "status": "running",
        "started_at_epoch": int(time.time()),
        "files_ready": 0,
        "files_expected": expected_files,
        "missing": [],
        "errors": [],
    }
    write_json(state_path, state)
    ready = 0
    missing: list[str] = []
    errors: list[str] = []
    def ensure_space(expected: int) -> None:
        reserve = int(args.reserve_gb * 1024 ** 3)
        free = shutil.disk_usage(root).free
        if free - max(expected, 0) < reserve:
            raise RuntimeError(
                f"disk reserve reached: free={free} required_reserve={reserve} next_file={expected}"
            )

    for symbol in symbols:
        symbol_root = root / symbol
        for day in days(TRADE_START, LAST_DAY):
            stamp = day.isoformat()
            filename = f"{symbol}{stamp}.csv.gz"
            url = f"https://public.bybit.com/trading/{symbol}/{filename}"
            expected = content_length(url)
            if expected is None:
                missing.append(url)
                continue
            try:
                ensure_space(expected)
                download(url, symbol_root / "public_trades" / filename, expected)
                ready += 1
            except RuntimeError as exc:
                errors.append(str(exc))
            state.update(files_ready=ready, missing=missing, errors=errors, current=f"{symbol}:trades:{stamp}")
            write_json(state_path, state)
        if not args.public_only:
            for day in days(DATA_START, LAST_DAY):
                stamp = day.isoformat()
                selected: tuple[str, str, int] | None = None
                for depth in (200, 500, 1000):
                    filename = f"{stamp}_{symbol}_ob{depth}.data.zip"
                    url = f"https://quote-saver.bycsi.com/orderbook/linear/{symbol}/{filename}"
                    expected = content_length(url)
                    if expected is not None:
                        selected = (filename, url, expected)
                        break
                if selected is None:
                    missing.append(f"orderbook:{symbol}:{stamp}")
                    continue
                filename, url, expected = selected
                try:
                    ensure_space(expected)
                    download(url, symbol_root / "orderbook" / filename, expected)
                    ready += 1
                except RuntimeError as exc:
                    errors.append(str(exc))
                state.update(files_ready=ready, missing=missing, errors=errors, current=f"{symbol}:orderbook:{stamp}")
                write_json(state_path, state)
    state.update(status="complete" if not errors else "complete_with_errors", finished_at_epoch=int(time.time()))
    state.pop("current", None)
    write_json(state_path, state)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
