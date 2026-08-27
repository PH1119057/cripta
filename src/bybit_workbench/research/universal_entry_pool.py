from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from bybit_workbench.research.mtf_entry_v3 import (
    EntryResearchV3Config,
    _read_candles,
    _read_flow,
    _write_v3_signals,
    enrich_with_flow,
    run_local_mtf_research,
)

ENGINE_ID = "universal-entry-15m5m-v1"


@dataclass(frozen=True, slots=True)
class PoolConfig:
    dataset_root: Path
    output_root: Path
    symbols: tuple[str, ...]
    workers: int
    fraction: Decimal

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("symbols cannot be empty")
        if self.workers <= 0:
            raise ValueError("workers must be positive")
        if not Decimal("0") < self.fraction <= Decimal("1"):
            raise ValueError("fraction must be in (0, 1]")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8-sig")))


def _dataset_dir(root: Path, symbol: str) -> Path:
    direct = root / symbol
    if (direct / "dataset_manifest.json").is_file():
        return direct
    matches = sorted(root.glob(f"{symbol}_*/p30/dataset"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"dataset not found for {symbol} below {root}")
    raise RuntimeError(f"ambiguous datasets for {symbol}: {matches}")


def _config_from_manifest(manifest: dict[str, Any], symbol: str) -> EntryResearchV3Config:
    raw = dict(manifest.get("config") or {})
    allowed = {field.name for field in __import__("dataclasses").fields(EntryResearchV3Config)}
    cooked: dict[str, Any] = {key: value for key, value in raw.items() if key in allowed}
    cooked["symbol"] = symbol
    for key in (
        "zone_half_width_atr",
        "confluence_max_gap_percent",
        "shock_atr_multiple",
    ):
        if key in cooked:
            cooked[key] = Decimal(str(cooked[key]))
    if "horizons_minutes" in cooked:
        cooked["horizons_minutes"] = tuple(int(item) for item in cooked["horizons_minutes"])
    cooked["latest_trade_day_override"] = None
    return EntryResearchV3Config(**cooked)


def _evaluation_bounds(manifest: dict[str, Any]) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(str(manifest["evaluation_start"]))
    end = datetime.fromisoformat(str(manifest["evaluation_end"]))
    return start.astimezone(UTC), end.astimezone(UTC)


def _fraction_end(start: datetime, end: datetime, fraction: Decimal) -> datetime:
    seconds = int(Decimal(str((end - start).total_seconds())) * fraction)
    return min(end, start + timedelta(seconds=max(seconds, 1)))


def run_symbol(
    dataset_root: str,
    output_root: str,
    symbol: str,
    fraction_text: str,
) -> dict[str, Any]:
    symbol = symbol.upper()
    fraction = Decimal(fraction_text)
    source = _dataset_dir(Path(dataset_root), symbol)
    destination = Path(output_root) / symbol
    complete = destination / "RUN_COMPLETE.json"
    if complete.is_file():
        payload = _json(complete)
        if payload.get("engine") == ENGINE_ID and payload.get("fraction") == str(fraction):
            return {"symbol": symbol, "status": "reused", "path": str(destination)}

    manifest = _json(source / "dataset_manifest.json")
    config = _config_from_manifest(manifest, symbol)
    evaluation_start, evaluation_end = _evaluation_bounds(manifest)
    selected_end = _fraction_end(evaluation_start, evaluation_end, fraction)
    five = tuple(
        item
        for item in _read_candles(source / "trade_5m.csv", symbol=symbol, timeframe="5")
        if item.opened_at < selected_end
    )
    fifteen = tuple(
        item
        for item in _read_candles(source / "trade_15m.csv", symbol=symbol, timeframe="15")
        if item.opened_at < selected_end
    )
    hourly = tuple(
        item
        for item in _read_candles(source / "trade_60m.csv", symbol=symbol, timeframe="60")
        if item.opened_at < selected_end
    )
    flow = tuple(
        item
        for item in _read_flow(source / "flow_1m.csv")
        if item.opened_at < selected_end
    )
    result = enrich_with_flow(
        run_local_mtf_research(
            five,
            fifteen,
            hourly,
            config,
            evaluation_start=evaluation_start,
        ),
        flow,
    )
    destination.mkdir(parents=True, exist_ok=True)
    signals = destination / "signals.csv"
    _write_v3_signals(signals, result)
    summary = {
        "engine": ENGINE_ID,
        "symbol": symbol,
        "fraction": str(fraction),
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end": selected_end.isoformat(),
        "source_manifest": str(source / "dataset_manifest.json"),
        "source_manifest_sha256": _sha256(source / "dataset_manifest.json"),
        "signals_sha256": _sha256(signals),
        "config": asdict(config),
        "summary": result.summary,
    }
    (destination / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    complete.write_text(
        json.dumps(
            {
                "engine": ENGINE_ID,
                "symbol": symbol,
                "fraction": str(fraction),
                "signals_sha256": summary["signals_sha256"],
                "completed_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"symbol": symbol, "status": "completed", "path": str(destination)}


def run_pool(config: PoolConfig) -> list[dict[str, Any]]:
    config.output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=min(config.workers, len(config.symbols))) as executor:
        futures = {
            executor.submit(
                run_symbol,
                str(config.dataset_root),
                str(config.output_root),
                symbol,
                str(config.fraction),
            ): symbol
            for symbol in config.symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"symbol": symbol, "status": "failed", "error": repr(exc)})
    results.sort(key=lambda item: str(item["symbol"]))
    (config.output_root / "pool_status.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return results


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Универсальный пул Entry 15m+5m")
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--symbols", required=True, help="Список через запятую")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--fraction", default="1", help="0.1 для сквозной проверки 10%")
    return parser.parse_args()


def main() -> int:
    args = _args()
    config = PoolConfig(
        dataset_root=args.dataset_root.resolve(),
        output_root=args.output_root.resolve(),
        symbols=tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip()),
        workers=args.workers,
        fraction=Decimal(str(args.fraction)),
    )
    results = run_pool(config)
    failed = [item for item in results if item["status"] == "failed"]
    print(json.dumps({"jobs": len(results), "failed": len(failed)}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
