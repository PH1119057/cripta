from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np

from bybit_workbench.research.entry_offset_adverse_eo1 import (
    Config,
    _fill_contract,
    _simulate_trade,
)
from bybit_workbench.research.exit_break_even_v13 import (
    CoreSignal,
    TradeDayCache,
    build_path_series,
)
from bybit_workbench.research.mtf_entry import Direction

VERSION = "MINUTE_ENTRY_BOOK_REPLAY_V1"
SYMBOLS = (
    "UNIUSDT", "LINKUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT",
    "1000PEPEUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT",
)


@dataclass(frozen=True, slots=True)
class Candidate:
    signal: CoreSignal
    minute_available: bool
    strict_three_timeframes: bool
    minute_shift_pct: float | None
    book_confirms: bool
    book_reason_ru: str


def _truth(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def _key(symbol: str, direction: str, touch_at: str) -> tuple[str, str, str]:
    return symbol, direction, datetime.fromisoformat(touch_at).astimezone(UTC).isoformat()


def _load_p40(paths: list[Path]) -> dict[tuple[str, str, str], dict[str, str]]:
    rows: dict[tuple[str, str, str], dict[str, str]] = {}
    for path in paths:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                symbol = str(row.get("symbol") or "")
                if symbol not in SYMBOLS:
                    continue
                key = _key(symbol, str(row["direction"]), str(row["touch_at"]))
                if key in rows:
                    raise ValueError(f"повтор стаканного события: {key}")
                rows[key] = row
    return rows


def _book_confirmation(row: dict[str, str] | None) -> tuple[bool, str]:
    if row is None:
        return False, "нет причинного стаканного среза"
    price_holds = _truth(row.get("price_favorable_or_flat_30s"))
    absorption = _truth(row.get("adverse_flow_but_price_holds_30s"))
    support = _truth(row.get("support_net_positive_10bps_30s"))
    refill = _truth(row.get("support_refill_present_10bps_30s"))
    confirmed = price_holds and (absorption or (support and refill))
    if confirmed:
        return True, "цена удержалась; подтверждено поглощением или восстановлением поддержки"
    return False, "нет совместного подтверждения удержания ценой и стаканом"


def _server_archive_map(symbol_root: Path, symbol: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in (symbol_root / "public_trades").glob(f"{symbol}*.csv.gz"):
        day = path.name.removeprefix(symbol).removesuffix(".csv.gz")
        result[day] = path
    if not result:
        raise FileNotFoundError(f"не найдены сырые сделки для {symbol}")
    return result


def load_candidates(
    baseline_csv: Path,
    minute_csv: Path,
    p40_paths: list[Path],
) -> list[Candidate]:
    baseline: dict[tuple[str, str, str], CoreSignal] = {}
    with baseline_csv.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["scenario"] != "BASELINE_0P00":
                continue
            direction = cast(Direction, row["direction"])
            touch = datetime.fromisoformat(row["touch_at"]).astimezone(UTC)
            signal = CoreSignal(
                symbol=row["symbol"], direction=direction, touch_at=touch,
                entry_price=float(row["original_entry_price"]), source_row=row,
            )
            baseline[_key(signal.symbol, direction, touch.isoformat())] = signal

    minute_rows: dict[tuple[str, str, str], dict[str, str]] = {}
    with minute_csv.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            minute_rows[_key(row["symbol"], row["direction"], row["touch_at"])] = row
    books = _load_p40(p40_paths)
    if len(baseline) != 1063 or len(minute_rows) != 1063:
        raise ValueError("ожидалось ровно 1063 исходных и минутных сигнала")

    result: list[Candidate] = []
    for key, signal in baseline.items():
        minute = minute_rows.get(key)
        if minute is None:
            raise ValueError(f"нет минутного события: {key}")
        available = _truth(minute.get("pre_touch_zone_available"))
        strict = _truth(minute.get("pre_touch_strict_three_tf_confluent"))
        raw_shift = minute.get("pre_touch_directional_shift_pct", "").strip()
        shift = float(raw_shift) if raw_shift else None
        book_ok, reason = _book_confirmation(books.get(key))
        result.append(Candidate(signal, available, strict, shift, book_ok, reason))
    return sorted(result, key=lambda item: (item.signal.symbol, item.signal.touch_at))


def _scenario_result(
    candidate: Candidate,
    path: Any,
    offset_pct: float,
    config: Config,
) -> dict[str, Any]:
    moves = np.asarray(path.moves_pct, dtype=np.float64)
    fill_index, fill_status, *_ = _fill_contract(path, moves, offset_pct, config)
    result: dict[str, Any] = {
        "fill_status": fill_status,
        "offset_pct": offset_pct,
        "outcome": "not_filled",
        "pnl_usd": 0.0,
    }
    if fill_index is None:
        return result
    _, _, exit_offset, exit_reason, mfe, mae, complete = _simulate_trade(
        path, moves, fill_index, offset_pct, config
    )
    result.update({"outcome": exit_reason, "mfe_pct": mfe, "mae_pct": mae,
                   "complete": complete})
    if exit_offset is not None and exit_reason == "target":
        result["pnl_usd"] = 10.0
    elif exit_offset is not None and exit_reason == "initial_stop":
        result["pnl_usd"] = -11.0
    return result


def _run_symbol(
    symbol: str, candidates: list[Candidate], raw_root: Path
) -> list[dict[str, Any]]:
    config = Config(activation_pct=1.10, positive_floor_pct=0.10,
                    day_cache_size=2, progress_interval_seconds=20.0)
    archive_map = _server_archive_map(raw_root / symbol, symbol)
    cache = TradeDayCache(max_days=2)
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        path = build_path_series(candidate.signal, archive_map,
                                 horizon_hours=config.max_path_hours, cache=cache)
        e0 = _scenario_result(candidate, path, 0.0, config)
        e20 = _scenario_result(candidate, path, 0.20, config)
        gate = (candidate.minute_available and candidate.strict_three_timeframes
                and candidate.book_confirms and candidate.minute_shift_pct is not None)
        minute_offset = max(0.0, -float(candidate.minute_shift_pct or 0.0))
        m0 = (_scenario_result(candidate, path, minute_offset, config) if gate else
              {"fill_status": "cancelled_no_confirmation", "offset_pct": minute_offset,
               "outcome": "cancelled", "pnl_usd": 0.0})
        rows.append({
            "symbol": symbol, "direction": candidate.signal.direction,
            "touch_at": candidate.signal.touch_at.isoformat(),
            "minute_available": candidate.minute_available,
            "strict_three_timeframes": candidate.strict_three_timeframes,
            "book_confirms": candidate.book_confirms,
            "book_reason_ru": candidate.book_reason_ru,
            "minute_shift_pct": candidate.minute_shift_pct,
            "e0": e0, "e20": e20, "m0_book": m0,
        })
        if index % 20 == 0:
            print(f"{symbol}: {index}/{len(candidates)}", flush=True)
    print(f"{symbol}: завершено {len(candidates)}/{len(candidates)}", flush=True)
    return rows


def _checkpoint_path(output_dir: Path, symbol: str) -> Path:
    return output_dir / "blocks" / f"{symbol}.json"


def _save_checkpoint(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_checkpoint(path: Path, expected: int) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) != expected:
        return None
    return cast(list[dict[str, Any]], rows)


def run(
    *, candidates: list[Candidate], raw_root: Path, output_dir: Path, workers: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {}
        active_symbols = [
            symbol for symbol in SYMBOLS
            if any(item.signal.symbol == symbol for item in candidates)
        ]
        for symbol in active_symbols:
            scoped = [item for item in candidates if item.signal.symbol == symbol]
            checkpoint = _load_checkpoint(_checkpoint_path(output_dir, symbol), len(scoped))
            if checkpoint is not None:
                print(f"{symbol}: загружено из контрольной точки", flush=True)
                rows.extend(checkpoint)
                continue
            future = executor.submit(_run_symbol, symbol, scoped, raw_root)
            futures[future] = symbol
        for future in as_completed(futures):
            symbol_rows = future.result()
            _save_checkpoint(_checkpoint_path(output_dir, futures[future]), symbol_rows)
            rows.extend(symbol_rows)
    rows.sort(key=lambda row: (str(row["symbol"]), str(row["touch_at"])))

    flat: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"version": VERSION, "signals": len(rows), "scenarios": {}}
    for scenario in ("e0", "e20", "m0_book"):
        outcomes = Counter(str(row[scenario]["outcome"]) for row in rows)
        filled = sum(row[scenario]["fill_status"] == "filled" for row in rows)
        pnl = sum(float(row[scenario]["pnl_usd"]) for row in rows)
        summary["scenarios"][scenario] = {
            "filled": filled, "fill_pct": 100 * filled / len(rows),
            "outcomes": dict(outcomes), "illustrative_pnl_usd": pnl,
        }
    for row in rows:
        flat.append({
            **{key: value for key, value in row.items() if key not in {"e0", "e20", "m0_book"}},
            **{f"{scenario}_{key}": value for scenario in ("e0", "e20", "m0_book")
               for key, value in row[scenario].items()},
        })
    with (output_dir / "СДЕЛКИ.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        fieldnames = list(dict.fromkeys(key for row in flat for key in row))
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat)
    (output_dir / "ИТОГ.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Сравнение классического, −0,20% и минутно-стаканного входа"
    )
    parser.add_argument("--baseline-csv", type=Path, required=True)
    parser.add_argument("--minute-csv", type=Path, required=True)
    parser.add_argument("--p40", type=Path, action="append", required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--smoke-symbol", choices=SYMBOLS)
    parser.add_argument("--smoke-limit", type=int, default=0)
    args = parser.parse_args()
    candidates = load_candidates(args.baseline_csv, args.minute_csv, args.p40)
    if args.smoke_symbol:
        candidates = [item for item in candidates if item.signal.symbol == args.smoke_symbol]
        if args.smoke_limit > 0:
            candidates = candidates[:args.smoke_limit]
    print(json.dumps(run(candidates=candidates, raw_root=args.raw_root,
                         output_dir=args.output_dir, workers=args.workers), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
