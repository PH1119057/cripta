from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .models import WORKING_SYMBOLS, EntryBotCalibration

_SCHEMA = "entry-bot-calibration-v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid {name}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"invalid {name}: {value!r}")
    return result


def load_calibrations(path: Path | str) -> dict[str, EntryBotCalibration]:
    source = Path(path)
    if not source.exists():
        return {}
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA:
        raise ValueError(f"unsupported Entry Bot calibration file: {source}")
    raw_symbols = payload.get("symbols")
    if not isinstance(raw_symbols, dict):
        raise ValueError("Entry Bot calibration symbols object is missing")
    rows: dict[str, EntryBotCalibration] = {}
    for symbol, raw in raw_symbols.items():
        if not isinstance(symbol, str) or not isinstance(raw, dict):
            raise ValueError("invalid Entry Bot calibration row")
        selected = symbol.strip().upper()
        rows[selected] = EntryBotCalibration(
            symbol=selected,
            high_oi_change_60m_pct=_decimal(
                raw.get("high_oi_change_60m_pct"),
                "high_oi_change_60m_pct",
            ),
            low_oi_acceleration_5_vs_60=_decimal(
                raw.get("low_oi_acceleration_5_vs_60"),
                "low_oi_acceleration_5_vs_60",
            ),
            source_period=str(raw.get("source_period") or ""),
            source_summary_sha256=str(raw.get("source_summary_sha256") or ""),
        )
    return rows


def _extract_thresholds(summary_path: Path, symbol: str, period: str) -> dict[str, str]:
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"P35 summary is not an object: {summary_path}")
    block = payload.get("p34_oi_tail_recheck")
    if not isinstance(block, dict):
        raise ValueError(f"P35 OI-tail block is missing: {summary_path}")
    thresholds = block.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError(f"P35 OI-tail thresholds are missing: {summary_path}")
    high = _decimal(thresholds.get("high_oi_change_60m_pct"), "high OI threshold")
    low = _decimal(
        thresholds.get("low_oi_acceleration_5_vs_60"),
        "low OI acceleration threshold",
    )
    return {
        "symbol": symbol,
        "high_oi_change_60m_pct": str(high),
        "low_oi_acceleration_5_vs_60": str(low),
        "source_period": period,
        "source_summary_sha256": _sha256_file(summary_path),
    }


def build_calibration_file(
    project_root: Path,
    output_path: Path,
    *,
    period: str,
    symbols: tuple[str, ...] = WORKING_SYMBOLS,
    require_all: bool = False,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    rows: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    for symbol in symbols:
        summary = (
            project_root
            / "reports"
            / "cross_asset_validation"
            / f"{symbol}_{period}"
            / "p35"
            / "summary.json"
        )
        if not summary.exists():
            missing.append(symbol)
            continue
        rows[symbol] = _extract_thresholds(summary, symbol, period)
    if require_all and missing:
        raise FileNotFoundError("missing P35 summaries: " + ", ".join(missing))
    payload: dict[str, Any] = {
        "schema_version": _SCHEMA,
        "strategy": "ENTRY_V1_CORE",
        "period": period,
        "generated_at": datetime.now(UTC).isoformat(),
        "symbols": rows,
        "missing_symbols": missing,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return tuple(rows), tuple(missing)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build compact Entry Bot OI calibration")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("var/entry_bot_calibration.json"),
    )
    parser.add_argument("--period", default="20260518_20260816")
    parser.add_argument("--require-all", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = args.project_root.resolve()
    output = args.output
    if not output.is_absolute():
        output = project_root / output
    built, missing = build_calibration_file(
        project_root,
        output,
        period=args.period,
        require_all=args.require_all,
    )
    print(f"Entry Bot calibration: {output}")
    print(f"Loaded: {', '.join(built) if built else 'none'}")
    print(f"Missing: {', '.join(missing) if missing else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
