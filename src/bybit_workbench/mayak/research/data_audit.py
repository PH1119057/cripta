from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from bybit_workbench.mayak.research.universe import MARKET_UNIVERSE, PERIOD_TAG

START = datetime(2026, 5, 18, tzinfo=UTC)
END = datetime(2026, 8, 16, tzinfo=UTC)
STEP = timedelta(minutes=5)
EXPECTED_ROWS = int((END - START) / STEP)


@dataclass(frozen=True, slots=True)
class AssetAudit:
    symbol: str
    dataset_dir: str
    manifest_sha256: str
    rows: int
    duplicate_timestamps: int
    out_of_order: int
    missing_bars: int
    public_trade_archives: int
    manifest_archive_hashes: int
    valid: bool
    errors: tuple[str, ...]


def audit_frozen_panel(root: Path) -> dict[str, Any]:
    assets = tuple(_audit_asset(root, symbol) for symbol in MARKET_UNIVERSE)
    fingerprint = hashlib.sha256(
        "".join(f"{row.symbol}:{row.manifest_sha256}\n" for row in assets).encode()
    ).hexdigest()
    return {
        "audit_version": "mayak-data-audit.1",
        "downloads": "DISABLED",
        "repair_policy": "NONE_FAIL_CLOSED",
        "period_start": START.isoformat(),
        "period_end": END.isoformat(),
        "granularity": "5m closed trade bars plus retained public trade archives",
        "dataset_fingerprint": fingerprint,
        "btc_available": any(row.symbol == "BTCUSDT" and row.valid for row in assets),
        "eth_available": any(row.symbol == "ETHUSDT" and row.valid for row in assets),
        "universe_complete": all(row.valid for row in assets),
        "assets": [asdict(row) for row in assets],
    }


def write_audit(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _audit_asset(root: Path, symbol: str) -> AssetAudit:
    errors: list[str] = []
    summary_path = root / "reports" / "cross_asset_validation"
    summary_path = summary_path / f"{symbol}_{PERIOD_TAG}" / "p40" / "summary.json"
    summary = cast(dict[str, Any], json.loads(summary_path.read_text(encoding="utf-8")))
    dataset_dir = Path(str(summary["dataset_dir"]))
    manifest_path = dataset_dir / "dataset_manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = cast(dict[str, Any], json.loads(manifest_bytes.decode("utf-8-sig")))
    if manifest.get("symbol") != symbol:
        errors.append("manifest symbol mismatch")
    if manifest.get("evaluation_start") != START.isoformat():
        errors.append("evaluation_start mismatch")
    if manifest.get("evaluation_end") != END.isoformat():
        errors.append("evaluation_end mismatch")
    times: list[datetime] = []
    with (dataset_dir / "trade_5m.csv").open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            raw = str(row.get("opened_at") or row.get("timestamp") or "")
            value = datetime.fromisoformat(raw).astimezone(UTC)
            if START <= value < END:
                times.append(value)
    duplicates = len(times) - len(set(times))
    out_of_order = sum(right <= left for left, right in zip(times, times[1:], strict=False))
    expected = {START + index * STEP for index in range(EXPECTED_ROWS)}
    missing = len(expected.difference(times))
    if len(times) != EXPECTED_ROWS:
        errors.append(f"row count {len(times)} != {EXPECTED_ROWS}")
    if duplicates:
        errors.append(f"duplicate timestamps: {duplicates}")
    if out_of_order:
        errors.append(f"out-of-order timestamps: {out_of_order}")
    if missing:
        errors.append(f"missing bars: {missing}")
    archive_dir = dataset_dir / "public_trades"
    archives = tuple(archive_dir.glob("*.csv.gz"))
    hashes = manifest.get("public_trade_archives")
    hash_count = len(hashes) if isinstance(hashes, dict) else 0
    if len(archives) != 91 or hash_count != 91:
        errors.append(f"public archive coverage files={len(archives)} hashes={hash_count}")
    return AssetAudit(
        symbol=symbol,
        dataset_dir=str(dataset_dir),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        rows=len(times),
        duplicate_timestamps=duplicates,
        out_of_order=out_of_order,
        missing_bars=missing,
        public_trade_archives=len(archives),
        manifest_archive_hashes=hash_count,
        valid=not errors,
        errors=tuple(errors),
    )
