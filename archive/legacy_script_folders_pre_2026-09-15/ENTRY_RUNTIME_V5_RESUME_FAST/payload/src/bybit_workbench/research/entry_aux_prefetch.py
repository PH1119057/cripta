from __future__ import annotations

import argparse
import sys
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bybit_workbench.research.entry_basis_v6 import (
    BasisResearchConfig,
    _download_price_klines,
)
from bybit_workbench.research.entry_crowding_v5 import (
    CrowdingResearchConfig,
    _download_account_ratio,
)


def prefetch_entry_aux_context(
    *,
    symbol: str,
    endpoint: str,
    dataset_dir: Path,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> None:
    dataset_dir.mkdir(parents=True, exist_ok=True)
    crowding = CrowdingResearchConfig(symbol=symbol, endpoint=endpoint)
    basis = BasisResearchConfig(symbol=symbol, endpoint=endpoint)

    _download_account_ratio(
        dataset_dir / "account_ratio_5m.csv",
        config=crowding,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
    )
    download_start = evaluation_start - timedelta(hours=2)
    _download_price_klines(
        dataset_dir / "mark_price_5m.csv",
        config=basis,
        endpoint_path="/v5/market/mark-price-kline",
        label="mark-price 5m",
        evaluation_start=download_start,
        evaluation_end=evaluation_end,
    )
    _download_price_klines(
        dataset_dir / "index_price_5m.csv",
        config=basis,
        endpoint_path="/v5/market/index-price-kline",
        label="index-price 5m",
        evaluation_start=download_start,
        evaluation_end=evaluation_end,
    )
    print(
        f"AUX CONTEXT READY: symbol={symbol} dataset={dataset_dir}",
        flush=True,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prefetch small Entry context datasets")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    start = datetime.fromisoformat(args.evaluation_start).astimezone(UTC)
    end = datetime.fromisoformat(args.evaluation_end).astimezone(UTC)
    if end <= start:
        raise ValueError("evaluation_end must be after evaluation_start")
    prefetch_entry_aux_context(
        symbol=args.symbol.strip().upper(),
        endpoint=args.endpoint,
        dataset_dir=Path(args.dataset_dir).resolve(),
        evaluation_start=start,
        evaluation_end=end,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stdout)
        raise SystemExit(2)
