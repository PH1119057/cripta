"""Issue #158 immutable first-month Spot source materializer tests."""
import csv
import gzip
import hashlib
import importlib.util
import io
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AUDITOR = (
    ROOT
    / "research/microstructure/experiments/r158_spot_legacy_original_audit_v1.py"
)
SOURCE = (
    ROOT
    / "research/microstructure/experiments/"
    "r158_spot_firstmonth_source_materialize_v1.py"
)
spec = importlib.util.spec_from_file_location("r158_spot_source_materializer", SOURCE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SYMBOL = "APTUSDT"
DAY = date(2026, 5, 18)
COMMIT = "c" * 40


def gzip_bytes(day, *, price="10.5"):
    start = int(
        datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000
    )
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        with io.TextIOWrapper(gz, encoding="utf-8", newline="") as text:
            writer = csv.writer(text)
            writer.writerow(["id", "timestamp", "price", "volume", "side", "rpi"])
            writer.writerow(["1", str(start + 1000), price, "2", "buy", "0"])
            writer.writerow(["2", str(start + 2000), "10.6", "1", "sell", "1"])
    return raw.getvalue()


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def write_legacy(cache, symbol, day, payload):
    path = cache / symbol / f"{symbol}_{day}.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def manifest_item(path, symbol, day):
    return {
        "bytes": path.stat().st_size,
        "day": day.isoformat(),
        "path": f"/historical/{symbol}/{path.name}",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_symbol": symbol,
        "symbol": symbol,
        "url": f"https://public.bybit.com/spot/{symbol}/{symbol}_{day}.csv.gz",
    }


def write_manifest(path, items):
    path.write_text(
        json.dumps({"archive_base": m.ARCHIVE_BASE, "files": items}),
        encoding="utf-8",
    )


def test_reuses_legacy_and_downloads_only_missing_without_mutating_legacy(tmp_path):
    legacy = tmp_path / "legacy"
    first_payload = gzip_bytes(DAY)
    first = write_legacy(legacy, SYMBOL, DAY, first_payload)
    historical_manifest = tmp_path / "SOURCE_MANIFEST.json"
    write_manifest(historical_manifest, [manifest_item(first, SYMBOL, DAY)])

    output_parent = tmp_path / "cache"
    output_parent.mkdir()
    output = output_parent / "new-corpus"
    second_day = date(2026, 5, 19)
    second_payload = gzip_bytes(second_day, price="11")
    calls = []

    def fetch(url):
        calls.append(url)
        return second_payload

    before = hashlib.sha256(first.read_bytes()).hexdigest()
    result = m.materialize(
        COMMIT,
        legacy_root=legacy,
        legacy_manifest=historical_manifest,
        output_root=output,
        cohort=(SYMBOL,),
        start=DAY,
        days=2,
        require_actor=False,
        fetcher=fetch,
    )

    assert result["status"] == "COMPLETE_IMMUTABLE_RESEARCH_SOURCE_CORPUS"
    assert result["files"] == 2
    assert result["legacy_reused"] == 1
    assert result["fresh_downloaded"] == 1
    assert calls == [
        f"https://public.bybit.com/spot/{SYMBOL}/{SYMBOL}_{second_day}.csv.gz"
    ]
    assert hashlib.sha256(first.read_bytes()).hexdigest() == before
    assert (output / SYMBOL / f"{SYMBOL}_{DAY}.csv.gz").read_bytes() == first_payload
    assert (
        output / SYMBOL / f"{SYMBOL}_{second_day}.csv.gz"
    ).read_bytes() == second_payload
    assert not output.with_name(output.name + ".partial").exists()
    saved = json.loads((output / "SOURCE_MANIFEST.json").read_text())
    assert saved["research_ready"] is False
    assert saved["local_receiver_available_after_verified"] is False
    assert (output / "source_snapshot" / SOURCE.name).is_file()
    assert (output / "source_snapshot" / AUDITOR.name).is_file()


def test_corrupt_legacy_manifest_fails_before_any_output_creation(tmp_path):
    legacy = tmp_path / "legacy"
    payload = gzip_bytes(DAY)
    first = write_legacy(legacy, SYMBOL, DAY, payload)
    item = manifest_item(first, SYMBOL, DAY)
    item["sha256"] = "0" * 64
    historical_manifest = tmp_path / "SOURCE_MANIFEST.json"
    write_manifest(historical_manifest, [item])
    output_parent = tmp_path / "cache"
    output_parent.mkdir()
    output = output_parent / "new-corpus"

    with pytest.raises(ValueError, match="FILE_SHA256_MISMATCH"):
        m.materialize(
            COMMIT,
            legacy_root=legacy,
            legacy_manifest=historical_manifest,
            output_root=output,
            cohort=(SYMBOL,),
            start=DAY,
            days=1,
            require_actor=False,
            fetcher=lambda _: b"never",
        )
    assert not output.exists()
    assert not output.with_name(output.name + ".partial").exists()


def test_existing_final_or_partial_root_blocks_mutation(tmp_path):
    legacy = tmp_path / "legacy"
    payload = gzip_bytes(DAY)
    first = write_legacy(legacy, SYMBOL, DAY, payload)
    historical_manifest = tmp_path / "SOURCE_MANIFEST.json"
    write_manifest(historical_manifest, [manifest_item(first, SYMBOL, DAY)])
    output_parent = tmp_path / "cache"
    output_parent.mkdir()
    output = output_parent / "new-corpus"
    output.mkdir()

    with pytest.raises(FileExistsError, match="IMMUTABLE_OUTPUT_ROOT"):
        m.materialize(
            COMMIT,
            legacy_root=legacy,
            legacy_manifest=historical_manifest,
            output_root=output,
            cohort=(SYMBOL,),
            start=DAY,
            days=1,
            require_actor=False,
        )
