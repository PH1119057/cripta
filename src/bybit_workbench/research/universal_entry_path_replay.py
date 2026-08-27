from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from bybit_workbench.research.entry_offset_adverse_eo1 import (
    Config,
    EventResult,
    analyze_signal_streaming,
)
from bybit_workbench.research.exit_break_even_v13 import CoreSignal, TradeDayCache
from bybit_workbench.research.mtf_entry import Direction

ENGINE_ID = "universal-entry-e0-e10-e20-path-v1"
POLICIES = {"eo1_floor", "no_floor"}


def _raw_archive_map(raw_symbol_dir: Path, symbol: str) -> dict[str, Path]:
    prefix = symbol.upper()
    result: dict[str, Path] = {}
    for path in (raw_symbol_dir / "public_trades").glob(f"{prefix}*.csv.gz"):
        day = path.name.removeprefix(prefix).removesuffix(".csv.gz")
        datetime.fromisoformat(day)  # validate archive naming contract
        result[day] = path
    if not result:
        raise FileNotFoundError(f"no public trades for {symbol}: {raw_symbol_dir}")
    return result


def _load_signals(path: Path, symbol: str) -> tuple[CoreSignal, ...]:
    rows: list[CoreSignal] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            direction = str(row.get("direction") or "")
            if direction not in {"Long", "Short"}:
                raise ValueError(f"unsupported direction: {direction}")
            rows.append(
                CoreSignal(
                    symbol=symbol,
                    direction=cast(Direction, direction),
                    touch_at=datetime.fromisoformat(str(row["entry_at"])).astimezone(UTC),
                    entry_price=float(str(row["entry_price"])),
                    source_row={str(k): str(v or "") for k, v in row.items()},
                )
            )
    return tuple(rows)


def _signals_path(entry_root: Path, symbol: str) -> Path:
    direct = entry_root / symbol / "signals.csv"
    nested = entry_root / "entry" / symbol / "signals.csv"
    if direct.is_file():
        return direct
    if nested.is_file():
        return nested
    raise FileNotFoundError(f"signals.csv not found for {symbol} below {entry_root}")


def _write_events(path: Path, rows: list[EventResult]) -> None:
    records = [asdict(row) for row in rows]
    if not records:
        path.write_text("", encoding="utf-8")
        return
    for record in records:
        for key, value in tuple(record.items()):
            if isinstance(value, datetime):
                record[key] = value.isoformat()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def _generic_summary(rows: list[EventResult]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for offset in (0.0, 0.1, 0.2):
        selected = [row for row in rows if abs(row.adverse_offset_pct - offset) < 1e-12]
        filled = [row for row in selected if row.fill_status == "filled"]
        targets = [row for row in filled if row.exit_reason == "target"]
        stops = [row for row in filled if row.exit_reason == "initial_stop"]
        floors = [row for row in filled if row.exit_reason == "positive_floor"]
        result.append(
            {
                "adverse_offset_pct": offset,
                "signals": len(selected),
                "filled": len(filled),
                "fill_rate_pct": 100.0 * len(filled) / len(selected) if selected else 0.0,
                "original_target_before_fill": sum(
                    row.fill_status == "original_target_before_fill" for row in selected
                ),
                "target_plus_1p10": len(targets),
                "target_rate_per_fill_pct": (
                    100.0 * len(targets) / len(filled) if filled else 0.0
                ),
                "initial_stop_minus_1p00": len(stops),
                "positive_floor_plus_0p10": len(floors),
                "horizon_or_data_end": len(filled) - len(targets) - len(stops) - len(floors),
            }
        )
    return result


def run_symbol(
    raw_root: str,
    entry_root: str,
    output_root: str,
    symbol: str,
    fraction_text: str,
    policy: str,
) -> dict[str, Any]:
    fraction = Decimal(fraction_text)
    if not Decimal("0") < fraction <= Decimal("1"):
        raise ValueError("fraction must be in (0, 1]")
    if policy not in POLICIES:
        raise ValueError(f"unsupported policy: {policy}")
    destination = Path(output_root) / symbol
    complete = destination / "RUN_COMPLETE.json"
    if complete.is_file():
        payload = json.loads(complete.read_text(encoding="utf-8"))
        if (
            payload.get("engine") == ENGINE_ID
            and payload.get("fraction") == str(fraction)
            and payload.get("policy") == policy
        ):
            return {"symbol": symbol, "status": "reused"}
    signals = _load_signals(_signals_path(Path(entry_root), symbol), symbol)
    selected_count = max(1, int(Decimal(len(signals)) * fraction))
    signals = signals[:selected_count]
    archive_by_day = _raw_archive_map(Path(raw_root) / symbol, symbol)
    config = Config() if policy == "eo1_floor" else Config(
        activation_pct=999.0,
        positive_floor_pct=0.0,
    )
    cache = TradeDayCache(max_days=config.day_cache_size)
    events: list[EventResult] = []
    for signal in signals:
        result, _ = analyze_signal_streaming(signal, archive_by_day, config, cache=cache)
        events.extend(result)
    destination.mkdir(parents=True, exist_ok=True)
    _write_events(destination / "events.csv", events)
    summary = _generic_summary(events)
    (destination / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    complete.write_text(
        json.dumps(
            {
                "engine": ENGINE_ID,
                "symbol": symbol,
                "fraction": str(fraction),
                "policy": policy,
                "signals": len(signals),
                "events": len(events),
                "completed_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"symbol": symbol, "status": "completed", "signals": len(signals)}


def run_pool(
    raw_root: Path,
    entry_root: Path,
    output_root: Path,
    symbols: tuple[str, ...],
    workers: int,
    fraction: Decimal,
    policy: str,
) -> list[dict[str, Any]]:
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=min(workers, len(symbols))) as executor:
        futures = {
            executor.submit(
                run_symbol,
                str(raw_root),
                str(entry_root),
                str(output_root),
                symbol,
                str(fraction),
                policy,
            ): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"symbol": symbol, "status": "failed", "error": repr(exc)})
    results.sort(key=lambda item: str(item["symbol"]))
    (output_root / "POOL_STATUS.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Универсальный повтор траекторий E0/E10/E20")
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--entry-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--fraction", default="1", help="0.1 для проверки 10%")
    parser.add_argument("--policy", choices=sorted(POLICIES), default="eo1_floor")
    args = parser.parse_args()
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    results = run_pool(
        args.raw_root,
        args.entry_root,
        args.output_root,
        symbols,
        args.workers,
        Decimal(str(args.fraction)),
        args.policy,
    )
    failed = [item for item in results if item["status"] == "failed"]
    print(json.dumps({"jobs": len(results), "failed": len(failed)}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
