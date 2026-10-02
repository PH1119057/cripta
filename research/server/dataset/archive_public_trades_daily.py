from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ARCHIVE_SCHEMA_VERSION = "bybit-public-trades-daily-v1"
SOURCE_BASE = "https://public.bybit.com/trading"
DEFAULT_ROOT = Path("/data/cripta/datasets/raw/bybit_public_trades_daily_v1")
QUARANTINED = {"1000PEPEUSDT", "DOGEUSDT", "NEARUSDT", "XLMUSDT"}
DEFAULT_SYMBOLS = (
    "AAVEUSDT",
    "ADAUSDT",
    "APTUSDT",
    "ARBUSDT",
    "AVAXUSDT",
    "BCHUSDT",
    "BNBUSDT",
    "DOTUSDT",
    "HBARUSDT",
    "INJUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "OPUSDT",
    "SOLUSDT",
    "SUIUSDT",
    "TRXUSDT",
    "UNIUSDT",
    "XRPUSDT",
    "BTCUSDT",
    "ETHUSDT",
)


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from exc


def iter_days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def remote_metadata(url: str) -> tuple[int, str | None] | None:
    request = Request(url, method="HEAD", headers={"User-Agent": "cripta-public-archive/1"})
    try:
        with urlopen(request, timeout=30) as response:
            raw_length = response.headers.get("Content-Length")
            if not raw_length:
                raise RuntimeError(f"missing Content-Length: {url}")
            return int(raw_length), response.headers.get("Last-Modified")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"HEAD failed http={exc.code}: {url}") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"HEAD failed: {url}: {exc}") from exc


def ensure_space(root: Path, required_bytes: int, reserve_gb: float) -> None:
    reserve = int(reserve_gb * 1024**3)
    free = shutil.disk_usage(root).free
    if free - required_bytes < reserve:
        raise RuntimeError(
            "disk reserve reached: "
            f"free={free} reserve={reserve} next_file={required_bytes}"
        )


def download(url: str, destination: Path, expected_bytes: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    command = [
        "curl",
        "--location",
        "--fail",
        "--retry",
        "8",
        "--retry-all-errors",
        "--retry-delay",
        "3",
        "--connect-timeout",
        "20",
        "--continue-at",
        "-",
        "--output",
        str(part),
        url,
    ]
    result = subprocess.run(command, check=False)
    if result.returncode == 33:
        part.unlink(missing_ok=True)
        command[command.index("--continue-at") : command.index("--continue-at") + 2] = []
        result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"curl exit {result.returncode}: {url}")
    if part.stat().st_size != expected_bytes:
        raise RuntimeError(
            f"size mismatch: {part} got={part.stat().st_size} expected={expected_bytes}"
        )
    part.replace(destination)


def load_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "source_base": SOURCE_BASE,
            "entries": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
        raise RuntimeError(f"manifest schema mismatch: {path}")
    if not isinstance(payload.get("entries"), dict):
        raise RuntimeError(f"manifest entries invalid: {path}")
    return payload


def resolve_range(args: argparse.Namespace) -> tuple[date, date]:
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    if (args.start is None) != (args.end is None):
        raise SystemExit("--start and --end must be provided together")
    if args.start is not None:
        start, end = args.start, args.end
    else:
        end = yesterday
        start = end - timedelta(days=args.lookback_days - 1)
    if start > end:
        raise SystemExit("start date is after end date")
    if end > yesterday:
        raise SystemExit(
            f"end date must be a completed UTC day (latest allowed {yesterday.isoformat()})"
        )
    return start, end


def main() -> int:
    configured = os.environ.get(
        "CRIPTA_PUBLIC_TRADE_ARCHIVE_SYMBOLS", ",".join(DEFAULT_SYMBOLS)
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=[value.strip() for value in configured.split(",") if value.strip()],
    )
    parser.add_argument("--start", type=parse_day)
    parser.add_argument("--end", type=parse_day)
    parser.add_argument("--lookback-days", type=int, default=3)
    parser.add_argument("--reserve-gb", type=float, default=15.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.lookback_days < 1:
        parser.error("--lookback-days must be >= 1")
    if args.reserve_gb < 0:
        parser.error("--reserve-gb must be >= 0")

    symbols = tuple(dict.fromkeys(symbol.upper() for symbol in args.symbols))
    forbidden = sorted(set(symbols) & QUARANTINED)
    if forbidden:
        raise SystemExit(f"quarantined symbols are forbidden: {','.join(forbidden)}")
    start, end = resolve_range(args)

    args.root.mkdir(parents=True, exist_ok=True)
    lock_path = args.root / ".archive.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("another public-trade archive run is active") from exc

        manifest_path = args.root / "MANIFEST.json"
        state_path = args.root / "archive_state.json"
        manifest = load_manifest(manifest_path)
        entries = manifest["entries"]
        assert isinstance(entries, dict)
        run_id = f"archive-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{os.getpid()}"
        state: dict[str, object] = {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "run_id": run_id,
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "symbols": list(symbols),
            "dry_run": args.dry_run,
            "files_checked": 0,
            "files_downloaded": 0,
            "files_cached": 0,
            "bytes_downloaded": 0,
            "bytes_required": 0,
            "missing": [],
            "errors": [],
        }
        write_json(state_path, state)

        for symbol in symbols:
            for day in iter_days(start, end):
                stamp = day.isoformat()
                filename = f"{symbol}{stamp}.csv.gz"
                relative = Path(symbol) / "public_trades" / filename
                destination = args.root / relative
                url = f"{SOURCE_BASE}/{symbol}/{filename}"
                state["current"] = relative.as_posix()
                try:
                    metadata = remote_metadata(url)
                    state["files_checked"] = int(state["files_checked"]) + 1
                    if metadata is None:
                        missing = state["missing"]
                        assert isinstance(missing, list)
                        missing.append(url)
                        write_json(state_path, state)
                        continue
                    expected, last_modified = metadata
                    existing = entries.get(relative.as_posix())
                    if destination.exists() and destination.stat().st_size == expected:
                        if not isinstance(existing, dict) or not existing.get("sha256"):
                            entries[relative.as_posix()] = {
                                "bytes": expected,
                                "sha256": sha256(destination),
                                "url": url,
                                "last_modified": last_modified,
                            }
                        state["files_cached"] = int(state["files_cached"]) + 1
                    else:
                        state["bytes_required"] = int(state["bytes_required"]) + expected
                        if not args.dry_run:
                            ensure_space(args.root, expected, args.reserve_gb)
                            download(url, destination, expected)
                            entries[relative.as_posix()] = {
                                "bytes": expected,
                                "sha256": sha256(destination),
                                "url": url,
                                "last_modified": last_modified,
                            }
                            state["files_downloaded"] = int(state["files_downloaded"]) + 1
                            state["bytes_downloaded"] = int(state["bytes_downloaded"]) + expected
                    manifest["updated_at"] = datetime.now(UTC).isoformat()
                    manifest["symbols"] = sorted(
                        {path.split("/", 1)[0] for path in entries}
                    )
                    if not args.dry_run:
                        write_json(manifest_path, manifest)
                    write_json(state_path, state)
                except Exception as exc:  # noqa: BLE001 - retain per-file evidence
                    errors = state["errors"]
                    assert isinstance(errors, list)
                    errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
                    write_json(state_path, state)

        state.pop("current", None)
        state["finished_at"] = datetime.now(UTC).isoformat()
        missing = state["missing"]
        errors = state["errors"]
        assert isinstance(missing, list) and isinstance(errors, list)
        if errors:
            state["status"] = "failed"
        elif missing:
            state["status"] = "incomplete_missing"
        else:
            state["status"] = "complete"
        write_json(state_path, state)
        if args.dry_run:
            print(json.dumps(state, ensure_ascii=False, sort_keys=True))
        return 0 if state["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
