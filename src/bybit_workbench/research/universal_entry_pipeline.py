from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from bybit_workbench.research.materialize_entry_dataset import materialize
from bybit_workbench.research.universal_entry_pool import PoolConfig, run_pool

PIPELINE_ID = "universal-entry-pipeline-v1"


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    raw_root: Path
    work_root: Path
    symbols: tuple[str, ...]
    workers: int
    max_days: int | None = None

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("symbols cannot be empty")
        if self.workers <= 0:
            raise ValueError("workers must be positive")
        if self.max_days is not None and self.max_days <= 0:
            raise ValueError("max_days must be positive")


def _materialize_job(
    raw_root: str,
    dataset_root: str,
    cache_root: str,
    symbol: str,
    max_days: int | None,
) -> dict[str, Any]:
    return materialize(
        raw_root=Path(raw_root),
        output_root=Path(dataset_root),
        cache_root=Path(cache_root),
        symbol=symbol,
        max_days=max_days,
    )


def run_pipeline(config: PipelineConfig) -> dict[str, Any]:
    dataset_root = config.work_root / "datasets"
    cache_root = config.work_root / "day_cache"
    entry_root = config.work_root / "entry"
    config.work_root.mkdir(parents=True, exist_ok=True)
    materialized: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ProcessPoolExecutor(max_workers=min(config.workers, len(config.symbols))) as executor:
        futures = {
            executor.submit(
                _materialize_job,
                str(config.raw_root),
                str(dataset_root),
                str(cache_root),
                symbol,
                config.max_days,
            ): symbol
            for symbol in config.symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                materialized.append(future.result())
            except Exception as exc:
                failures.append({"symbol": symbol, "stage": "dataset", "error": repr(exc)})
    entry_jobs: list[dict[str, Any]] = []
    if not failures:
        entry_jobs = run_pool(
            PoolConfig(
                dataset_root=dataset_root,
                output_root=entry_root,
                symbols=config.symbols,
                workers=config.workers,
                fraction=Decimal("1"),
            )
        )
        failures.extend(
            {
                "symbol": str(item["symbol"]),
                "stage": "entry",
                "error": str(item.get("error", "unknown")),
            }
            for item in entry_jobs
            if item["status"] == "failed"
        )
    status = {
        "pipeline": PIPELINE_ID,
        "symbols": list(config.symbols),
        "workers": config.workers,
        "max_days": config.max_days,
        "datasets_completed": len(materialized),
        "entry_jobs_completed": sum(
            item["status"] in {"completed", "reused"} for item in entry_jobs
        ),
        "failures": failures,
        "complete": not failures and len(entry_jobs) == len(config.symbols),
    }
    (config.work_root / "PIPELINE_STATUS.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return status


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Полный универсальный Entry-конвейер")
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-days", type=int)
    return parser.parse_args()


def main() -> int:
    args = _args()
    status = run_pipeline(
        PipelineConfig(
            raw_root=args.raw_root.resolve(),
            work_root=args.work_root.resolve(),
            symbols=tuple(
                item.strip().upper() for item in args.symbols.split(",") if item.strip()
            ),
            workers=args.workers,
            max_days=args.max_days,
        )
    )
    print(
        json.dumps(
            {
                "pipeline": status["pipeline"],
                "complete": status["complete"],
                "failures": len(status["failures"]),
            },
            ensure_ascii=False,
        )
    )
    return 0 if status["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
