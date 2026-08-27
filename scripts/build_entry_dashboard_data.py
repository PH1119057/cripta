from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OFFSETS = (0.0, 0.1, 0.2)
RESOLVED = {"target", "initial_stop"}
EXCLUDED_SYMBOLS = {"DOGEUSDT", "1000PEPEUSDT"}


def _load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _metrics(rows: list[dict[str, str]], offset: float) -> dict[str, Any]:
    selected = [row for row in rows if abs(float(row["adverse_offset_pct"]) - offset) < 1e-9]
    filled = [row for row in selected if row["fill_status"] == "filled"]
    resolved = [row for row in filled if row["exit_reason"] in RESOLVED]
    targets = sum(row["exit_reason"] == "target" for row in resolved)
    return {
        "signals": len(selected),
        "filled": len(filled),
        "fill_rate_pct": 100.0 * len(filled) / len(selected) if selected else None,
        "resolved": len(resolved),
        "targets": targets,
        "stops": sum(row["exit_reason"] == "initial_stop" for row in resolved),
        "target_rate_pct": 100.0 * targets / len(resolved) if resolved else None,
    }


def _key(row: dict[str, str]) -> tuple[str, str]:
    return row["direction"], row["touch_at"]


def _agreement(rows: list[dict[str, str]], offsets: tuple[float, ...]) -> dict[str, Any]:
    by_signal: defaultdict[tuple[str, str], dict[float, str]] = defaultdict(dict)
    for row in rows:
        offset = float(row["adverse_offset_pct"])
        if offset in offsets and row["fill_status"] == "filled" and row["exit_reason"] in RESOLVED:
            by_signal[_key(row)][offset] = row["exit_reason"]
    comparable = [values for values in by_signal.values() if all(offset in values for offset in offsets)]
    same = sum(len({values[offset] for offset in offsets}) == 1 for values in comparable)
    return {
        "comparable": len(comparable),
        "same": same,
        "same_pct": 100.0 * same / len(comparable) if comparable else None,
    }


def _status(metrics: list[dict[str, Any]]) -> tuple[str, str]:
    rates = [float(item["target_rate_pct"]) for item in metrics if item["target_rate_pct"] is not None]
    if not rates:
        return "red", "нет сопоставимых завершённых исходов"
    spread = max(rates) - min(rates)
    if max(rates) < 50.0:
        return "red", "ни один Entry-вариант не достигает 50%"
    if min(rates) < 50.0 or spread >= 10.0:
        return "yellow", f"стратегии расходятся на {spread:.1f} п.п."
    return "green", "все Entry-варианты дают не менее 50%"


def _row(symbol: str, rows: list[dict[str, str]], contract: str) -> dict[str, Any]:
    metrics = [_metrics(rows, offset) for offset in OFFSETS]
    if "ранний floor" in contract:
        empty = {
            "signals": None, "filled": None, "fill_rate_pct": None,
            "resolved": None, "targets": None, "stops": None,
            "target_rate_pct": None,
        }
        metrics = [dict(empty), dict(empty), dict(empty)]
        color, reason = "gray", "ожидает сопоставимого no-floor пересчёта"
    else:
        color, reason = _status(metrics)
    rates = [float(item["target_rate_pct"]) for item in metrics if item["target_rate_pct"] is not None]
    return {
        "symbol": symbol,
        "contract": contract,
        "color": color,
        "reason": reason,
        "e0": metrics[0],
        "e10": metrics[1],
        "e20": metrics[2],
        "agreement_e0_e10": _agreement(rows, (0.0, 0.1)) if color != "gray" else {},
        "agreement_e0_e20": _agreement(rows, (0.0, 0.2)) if color != "gray" else {},
        "agreement_all": _agreement(rows, OFFSETS) if color != "gray" else {},
        "spread_pp": max(rates) - min(rates) if rates else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Данные широкой Entry-таблицы dashboard")
    parser.add_argument("--new-root", type=Path, required=True)
    parser.add_argument("--fallback-root", type=Path)
    parser.add_argument("--legacy-events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source: dict[str, tuple[list[dict[str, str]], str]] = {}
    legacy = _load(args.legacy_events)
    by_symbol: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for row in legacy:
        by_symbol[row["symbol"].upper()].append(row)
    for symbol, rows in by_symbol.items():
        source[symbol] = (rows, "EO1 · ранний floor +0,10%")
    if args.fallback_root:
        for path in sorted(args.fallback_root.glob("*/events.csv")):
            source[path.parent.name.upper()] = (_load(path), "EO1 · ранний floor +0,10%")
    for path in sorted(args.new_root.glob("*/events.csv")):
        source[path.parent.name.upper()] = (_load(path), "no-floor · +1,10% / −1,00%")
    rows = [
        _row(symbol, data, contract)
        for symbol, (data, contract) in source.items()
        if symbol not in EXCLUDED_SYMBOLS
    ]
    rows.sort(key=lambda item: (item["symbol"] != "TRXUSDT", item["color"] != "green", item["symbol"]))
    payload = {
        "state": "ready",
        "generated_at": datetime.now(UTC).isoformat(),
        "thresholds": {
            "green": "все E0/E10/E20 ≥ 50%",
            "yellow": "часть ниже 50% или разброс ≥ 10 п.п.",
            "red": "все варианты ниже 50%",
            "gray": "несопоставимый старый exit-контракт",
        },
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"symbols": len(rows), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
