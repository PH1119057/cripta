from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERSION = "mayak-component-oos-inputs-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _at(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _key(symbol: str, direction: str, at: str) -> str:
    return f"{symbol.strip().upper()}|{direction.strip()}|{_at(at)}"


def _signals_path(entry_root: Path, symbol: str) -> Path:
    direct = entry_root / symbol / "signals.csv"
    nested = entry_root / "entry" / symbol / "signals.csv"
    if direct.is_file():
        return direct
    if nested.is_file():
        return nested
    raise FileNotFoundError(f"signals.csv not found for {symbol} below {entry_root}")


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _entry_rows(entry_root: Path, symbol: str) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for row in _read_rows(_signals_path(entry_root, symbol)):
        key = _key(symbol, row["direction"], row["entry_at"])
        if key in rows:
            raise ValueError(f"duplicate Entry key: {key}")
        rows[key] = {
            "symbol": symbol,
            "direction": row["direction"],
            "touch_at": _at(row["entry_at"]),
            "original_entry_price": row["entry_price"],
            "scenario": "BASELINE_0P00",
        }
    return rows


def _path_rows(root: Path, symbol: str) -> dict[str, dict[str, str]]:
    path = root / symbol / "events.csv"
    rows: dict[str, dict[str, str]] = {}
    for row in _read_rows(path):
        if row.get("scenario") != "BASELINE_0P00":
            continue
        if abs(float(row.get("adverse_offset_pct") or "nan")) > 1e-12:
            continue
        key = _key(symbol, row["direction"], row["touch_at"])
        if key in rows:
            raise ValueError(f"duplicate path key in {path}: {key}")
        if row.get("fill_status") != "filled":
            raise ValueError(f"baseline path is not filled: {key}")
        rows[key] = row
    return rows


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def run(
    *,
    symbols: tuple[str, ...],
    entry_root: Path,
    floor_root: Path,
    nofloor_root: Path,
    output_dir: Path,
    source_commit: str,
) -> dict[str, Any]:
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("symbols must be non-empty and unique")
    if len(source_commit) != 40:
        raise ValueError("source_commit must be a full Git SHA")

    baseline: list[dict[str, str]] = []
    audit_010: list[dict[str, str]] = []
    outcome_110: list[dict[str, str]] = []
    source_files: list[dict[str, str]] = []
    outcome_counts_010: Counter[str] = Counter()
    outcome_counts_110: Counter[str] = Counter()

    for symbol in symbols:
        entry_path = _signals_path(entry_root, symbol)
        floor_path = floor_root / symbol / "events.csv"
        nofloor_path = nofloor_root / symbol / "events.csv"
        for role, path in (
            ("entry", entry_path),
            ("floor", floor_path),
            ("nofloor", nofloor_path),
        ):
            source_files.append(
                {"symbol": symbol, "role": role, "path": str(path), "sha256": _sha256(path)}
            )

        entry = _entry_rows(entry_root, symbol)
        floor = _path_rows(floor_root, symbol)
        nofloor = _path_rows(nofloor_root, symbol)
        if set(entry) != set(floor) or set(entry) != set(nofloor):
            raise ValueError(
                f"key mismatch {symbol}: entry={len(entry)} floor={len(floor)} "
                f"nofloor={len(nofloor)}"
            )

        for key in sorted(entry):
            base = entry[key]
            baseline.append(base)

            floor_row = floor[key]
            reason = floor_row.get("exit_reason") or ""
            activation_at = floor_row.get("protection_activation_at") or ""
            if reason == "initial_stop":
                if activation_at:
                    raise ValueError(f"initial stop after recorded +0.10 activation: {key}")
                outcome_counts_010["hit_minus_1p00_before_plus_0p10"] += 1
            elif reason == "target" and not activation_at:
                # The frozen EO1 engine exits at target before recording activation
                # when the first observed fill tick already exceeds +1.10%. That
                # same causal tick necessarily proves +0.10 before -1.00.
                target_at = floor_row.get("exit_at") or ""
                if not target_at:
                    raise ValueError(f"same-tick target has no exit_at: {key}")
                audit_010.append(
                    {
                        "symbol": symbol,
                        "touch_at": base["touch_at"],
                        "outcome": "target_same_tick_implies_plus_0p10",
                        "activation_at": _at(target_at),
                    }
                )
                outcome_counts_010["reached_plus_0p10_before_minus_1p00"] += 1
            elif activation_at:
                audit_010.append(
                    {
                        "symbol": symbol,
                        "touch_at": base["touch_at"],
                        "outcome": "activated_plus_0p10",
                        "activation_at": _at(activation_at),
                    }
                )
                outcome_counts_010["reached_plus_0p10_before_minus_1p00"] += 1
            elif reason in {"horizon", "data_end"}:
                audit_010.append(
                    {
                        "symbol": symbol,
                        "touch_at": base["touch_at"],
                        "outcome": "data_end",
                        "activation_at": "",
                    }
                )
                outcome_counts_010["data_end_before_plus_0p10_or_minus_1p00"] += 1
            else:
                raise ValueError(
                    f"unexpected floor outcome without +0.10 activation: {key} {reason}"
                )

            nofloor_row = nofloor[key]
            reason_110 = nofloor_row.get("exit_reason") or ""
            if reason_110 == "target":
                state_110 = "reached_plus_1p10"
            elif reason_110 == "initial_stop":
                state_110 = "hit_minus_1p00"
            elif reason_110 in {"horizon", "data_end"}:
                state_110 = "data_end"
            else:
                raise ValueError(f"unexpected no-floor outcome: {key} {reason_110}")
            outcome_counts_110[state_110] += 1
            outcome_110.append(
                {
                    "symbol": symbol,
                    "direction": base["direction"],
                    "touch_at": base["touch_at"],
                    "entry_price": base["original_entry_price"],
                    "outcome": state_110,
                    "event_at": nofloor_row.get("exit_at") or "",
                    "complete_horizon": nofloor_row.get("trade_window_complete") or "",
                }
            )

    keys = [_key(row["symbol"], row["direction"], row["touch_at"]) for row in baseline]
    if len(keys) != len(set(keys)):
        raise ValueError("combined NEW15 baseline has duplicate full keys")
    symbol_touch = {(row["symbol"], row["touch_at"]) for row in baseline}
    if len(symbol_touch) != len(baseline):
        raise ValueError(
            "combined baseline has symbol+touch collision; +0.10 audit would be ambiguous"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = output_dir / "NEW15_BASELINE.csv"
    audit_path = output_dir / "NEW15_PLUS010_AUDIT.csv"
    outcome_path = output_dir / "NEW15_PLUS110_OUTCOMES.csv"
    _write_csv(
        baseline_path,
        ("symbol", "direction", "touch_at", "original_entry_price", "scenario"),
        baseline,
    )
    _write_csv(audit_path, ("symbol", "touch_at", "outcome", "activation_at"), audit_010)
    _write_csv(
        outcome_path,
        (
            "symbol",
            "direction",
            "touch_at",
            "entry_price",
            "outcome",
            "event_at",
            "complete_horizon",
        ),
        outcome_110,
    )
    manifest = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "project_commit": source_commit,
        "symbols": list(symbols),
        "signals": len(baseline),
        "unique_full_keys": len(set(keys)),
        "unique_symbol_touch": len(symbol_touch),
        "source_files": source_files,
        "baseline_sha256": _sha256(baseline_path),
        "plus010_audit_sha256": _sha256(audit_path),
        "plus110_outcomes_sha256": _sha256(outcome_path),
        "plus010_counts": dict(outcome_counts_010),
        "plus110_counts": dict(outcome_counts_110),
        "threshold_selection": False,
        "retuning": False,
        "trading_effect": "NONE",
    }
    (output_dir / "RUN_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare exact NEW15 inputs for MAYAK OOS V1")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--entry-root", type=Path, required=True)
    parser.add_argument("--floor-root", type=Path, required=True)
    parser.add_argument("--nofloor-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args(argv)
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    manifest = run(
        symbols=symbols,
        entry_root=args.entry_root,
        floor_root=args.floor_root,
        nofloor_root=args.nofloor_root,
        output_dir=args.output_dir,
        source_commit=args.source_commit,
    )
    print(f"MAYAK_OOS_INPUTS=PASS signals={manifest['signals']} symbols={len(symbols)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
