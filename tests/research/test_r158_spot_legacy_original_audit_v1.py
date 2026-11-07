"""Issue #158 reusable legacy Spot-original audit tests."""
import csv
import gzip
import hashlib
import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_spot_legacy_original_audit_v1.py"
)
spec = importlib.util.spec_from_file_location("r158_spot_legacy_audit", SOURCE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_spot(path, day, rows=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    start = int(
        datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000
    )
    rows = rows or [
        ["1", str(start + 1000), "10.5", "2.0", "buy", "0"],
        ["2", str(start + 2000), "10.6", "1.5", "sell", "1"],
    ]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(m.EXPECTED_HEADER)
        writer.writerows(rows)


def make_manifest(path, entries):
    payload = {"archive_base": m.EXPECTED_ARCHIVE_BASE, "files": entries}
    path.write_text(json.dumps(payload), encoding="utf-8")


def manifest_item(path, symbol, day):
    return {
        "bytes": path.stat().st_size,
        "day": day.isoformat(),
        "path": f"/historical/{symbol}/{path.name}",
        "sha256": file_sha(path),
        "source_symbol": symbol,
        "symbol": symbol,
        "url": m.expected_url(symbol, day),
    }


def test_partial_corpus_reuses_present_original_and_reports_missing(tmp_path):
    symbol = "APTUSDT"
    day = date(2026, 5, 18)
    cache = tmp_path / "cache"
    source = cache / symbol / f"{symbol}_{day}.csv.gz"
    make_spot(source, day)
    manifest = tmp_path / "SOURCE_MANIFEST.json"
    make_manifest(manifest, [manifest_item(source, symbol, day)])

    result = m.audit(
        cache_root=cache,
        source_manifest=manifest,
        cohort=(symbol,),
        start=day,
        days=2,
        require_actor=False,
    )

    assert result["status"] == "PARTIAL_REUSABLE_ORIGINAL_CORPUS_QC_PASS"
    assert result["expected_files"] == 2
    assert result["present_files_qced"] == 1
    assert result["missing_files"] == 1
    assert result["missing_dates_by_symbol"][symbol] == ["2026-05-19"]
    assert result["files"][0]["rows"] == 2
    assert result["research_ready"] is False
    assert result["local_receiver_available_after_verified"] is False


def test_hash_mismatch_fails_closed(tmp_path):
    symbol = "APTUSDT"
    day = date(2026, 5, 18)
    cache = tmp_path / "cache"
    source = cache / symbol / f"{symbol}_{day}.csv.gz"
    make_spot(source, day)
    item = manifest_item(source, symbol, day)
    item["sha256"] = "0" * 64
    manifest = tmp_path / "SOURCE_MANIFEST.json"
    make_manifest(manifest, [item])

    with pytest.raises(ValueError, match="FILE_SHA256_MISMATCH"):
        m.audit(
            cache_root=cache,
            source_manifest=manifest,
            cohort=(symbol,),
            start=day,
            days=1,
            require_actor=False,
        )


def test_physical_file_without_manifest_fails_closed(tmp_path):
    symbol = "APTUSDT"
    day = date(2026, 5, 18)
    cache = tmp_path / "cache"
    source = cache / symbol / f"{symbol}_{day}.csv.gz"
    make_spot(source, day)
    manifest = tmp_path / "SOURCE_MANIFEST.json"
    make_manifest(manifest, [])

    with pytest.raises(ValueError, match="PHYSICAL_FILE_WITHOUT_SOURCE_MANIFEST"):
        m.audit(
            cache_root=cache,
            source_manifest=manifest,
            cohort=(symbol,),
            start=day,
            days=1,
            require_actor=False,
        )


def test_timestamp_outside_day_fails(tmp_path):
    symbol = "APTUSDT"
    day = date(2026, 5, 18)
    next_day = date(2026, 5, 19)
    start_next = int(
        datetime(
            next_day.year, next_day.month, next_day.day, tzinfo=UTC
        ).timestamp()
        * 1000
    )
    cache = tmp_path / "cache"
    source = cache / symbol / f"{symbol}_{day}.csv.gz"
    make_spot(
        source,
        day,
        rows=[["1", str(start_next), "10", "1", "buy", "0"]],
    )
    manifest = tmp_path / "SOURCE_MANIFEST.json"
    make_manifest(manifest, [manifest_item(source, symbol, day)])

    with pytest.raises(ValueError, match="SPOT_TIMESTAMP_OUTSIDE_DAY"):
        m.audit(
            cache_root=cache,
            source_manifest=manifest,
            cohort=(symbol,),
            start=day,
            days=1,
            require_actor=False,
        )
