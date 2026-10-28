#!/usr/bin/env python3
"""P90L90 SOURCE_ORDER_RESEARCH: exact raw-CSV/ledger + NEW fee-code audit.

WARNING: This is NOT a new-code Entry/Exit decision replay or live WS seq
equivalence. It verifies the immutable reference ledger's 1-based source row
positions, original trdMatchID and timestamps, source gzip SHA256/CRC, and
recomputes every realized after-fee result using the actual P90L90 Python
modeled_pnl_after_fees implementation. It does NOT synthesize exchange seq,
activate Strategies, use PostgreSQL, contact Bybit or change source datasets.

SCRIPT: scripts/research/p90l90_source_order_ledger_audit_v1.py
EFFECTIVE_ACTOR: cripta (UID 107) for server research.
READ: /data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/{inputs,results}
WRITE/CREATE: NONE in --pilot; only a NEW direct child of
  /data/cripta/research/runs for --output-name after approved path preflight.
RENAME: generated *.tmp -> *.json inside the same owned result child.
DELETE: NONE. DB: NONE. SYSTEMD_SANDBOX: None (bounded manual research run).
SOURCE: exact GitHub source commit required; no assumed installed runtime.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bybit_workbench.universal_entry.p90l90_causal import modeled_pnl_after_fees

ROOT = Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024")
RUNS = Path("/data/cripta/research/runs")
SOURCE_DAYS = 100
ALL_SYMBOLS = ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")
GROUPS = {
    "MAIN": ("LTCUSDT", "INJUSDT"),
    "SUB": ("DOTUSDT", "ARBUSDT", "APTUSDT"),
}
LEDGER_DIRS = {
    "MAIN": "p90l90_strategy_second48_main_ltc_inj_20261028_v1",
    "SUB": "p90l90_strategy_second48_sub_dot_arb_apt_20261028_v1",
}
START = "2026-05-08"
STOP = "2026-08-15"
EVAL_START = Decimal("1779062400")
EVAL_END = Decimal("1786838400")
SHA = re.compile(r"^[a-f0-9]{40}$")
NAME = re.compile(r"^p90l90_source_order_audit_[a-z0-9_]{4,50}$")
COLUMNS = (
    "timestamp", "symbol", "side", "size", "price",
    "tickDirection", "trdMatchID",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"expected JSON object: {path}")
    return result


def _dec(value: object) -> Decimal:
    v = Decimal(str(value))
    if not v.is_finite():
        raise ValueError("nonfinite numeric field in frozen reference/raw")
    return v


def load_reference(root: Path, symbols: tuple[str, ...]) -> tuple[
    dict[str, list[tuple[int, str, dict[str, Any]]]],
    dict[str, dict[str, Any]],
    dict[str, int],
]:
    """Map 1-based global source order to all referenced ENTRY/EXIT legs."""
    selected = set(symbols)
    points: dict[str, list[tuple[int, str, dict[str, Any]]]] = defaultdict(list)
    summaries: dict[str, dict[str, Any]] = {}
    loaded_rows: dict[str, int] = {}
    for group, members in GROUPS.items():
        if not selected.intersection(members):
            continue
        folder = root / "results" / LEDGER_DIRS[group]
        summary = _json(folder / "result.json")
        ledger_path = folder / "trade_ledger.jsonl"
        if _sha256(ledger_path) != summary["ledger_sha256"]:
            raise RuntimeError(f"REFERENCE_LEDGER_SHA256_MISMATCH:{group}")
        count = 0
        financial = defaultdict(lambda: [Decimal("0")] * 4)
        with ledger_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                count += 1
                if row["symbol"] not in members:
                    raise RuntimeError("REFERENCE_WRONG_STRATEGY_SYMBOL")
                side, scenario = row["side"], row["portfolio_scenario"]
                if side not in {"LONG", "SHORT"} or scenario not in {
                    "BASELINE", "SECOND_48H"
                }:
                    raise RuntimeError("REFERENCE_INVALID_POSITION_SIDE_OR_SCENARIO")
                if row["scenario"] != "PREV7D_TRAIL035":
                    raise RuntimeError("REFERENCE_ENTRY_EXIT_VERSION_MISMATCH")
                if row["exit_reason"] not in {
                    "RESTING_TP", "MARKETABLE_TP", "PROTECTED_STOP", "HARD_STOP"
                }:
                    raise RuntimeError("REFERENCE_UNKNOWN_EXIT_REASON")
                maker = row["exit_reason"] == "RESTING_TP"
                if bool(row["maker_exit"]) != maker or bool(
                    row["modeled_exchange_fill"]
                ):
                    raise RuntimeError("REFERENCE_FEE_OR_LIVE_MODEL_MISMATCH")
                entry_order, exit_order = (
                    int(row["entry_source_order"]), int(row["exit_source_order"])
                )
                if not 0 < entry_order < exit_order:
                    raise RuntimeError("REFERENCE_SOURCE_ORDER_INVALID")
                entry_ts, exit_ts = _dec(row["entry_ts"]), _dec(row["exit_ts"])
                if not EVAL_START <= entry_ts <= exit_ts < EVAL_END:
                    raise RuntimeError("REFERENCE_WINDOW_OR_EVENT_TIME_INVALID")
                fees = modeled_pnl_after_fees(
                    side=side, entry_price=_dec(row["entry_price"]),
                    exit_price=_dec(row["exit_price"]),
                    entry_notional_usdt=Decimal("10"), maker_exit=maker,
                )
                expected = (
                    ("gross_usdt", fees.gross_usdt),
                    ("entry_fee_usdt", fees.entry_fee_usdt),
                    ("exit_fee_usdt", fees.exit_fee_usdt),
                    ("net_usdt", fees.net_usdt),
                )
                for field, amount in expected:
                    if _dec(row[field]) != amount:
                        raise RuntimeError(
                            f"NEW_CODE_FEE_PARITY_FAILED:{group}:{count}:{field}"
                        )
                totals = financial[scenario]
                for idx, (_, amount) in enumerate(expected):
                    totals[idx] += amount
                if row["symbol"] in selected:
                    points[row["symbol"]].append((entry_order, "ENTRY", row))
                    points[row["symbol"]].append((exit_order, "EXIT", row))
        if count != int(summary["ledger_rows"]):
            raise RuntimeError(f"REFERENCE_LEDGER_ROWS_MISMATCH:{group}")
        for scenario, components in financial.items():
            orig = summary["scenarios"][scenario]["money"]
            if (
                components[0] != _dec(orig["gross_usdt"])
                or components[1] != _dec(orig["entry_fees_usdt"])
                or components[2] != _dec(orig["exit_fees_usdt"])
                or components[3] != _dec(orig["net_usdt"])
                or components[3] != _dec(summary["net_pnl_usdt"][scenario])
            ):
                raise RuntimeError(
                    f"NEW_CODE_TOTAL_NET_PARITY_FAILED:{group}:{scenario}"
                )
        summaries[group] = summary
        loaded_rows[group] = count
    for symbol in symbols:
        points[symbol].sort(key=lambda item: item[0])
    return points, summaries, loaded_rows


def audit_symbol(
    *,
    root: Path,
    symbol: str,
    max_days: int,
    points: list[tuple[int, str, dict[str, Any]]],
) -> dict[str, Any]:
    """Bounded-memory gzip source audit, retaining only ledger endpoints."""
    manifest_path = root / "inputs" / f"manifest_{symbol}.json"
    manifest = _json(manifest_path)
    records = manifest["files"]
    if manifest["symbol"] != symbol or len(records) != SOURCE_DAYS:
        raise RuntimeError("SOURCE_MANIFEST_SYMBOL_OR_COVERAGE_MISMATCH")
    if records[0]["date"] != START or records[-1]["date"] != STOP:
        raise RuntimeError("SOURCE_MANIFEST_DAY_RANGE_INVALID")
    count = 0
    matched = 0
    pending = 0
    digest = hashlib.sha256()
    last_time: Decimal | None = None
    selected = records[:max_days]
    for source in selected:
        path = root / "inputs" / symbol / "public_trades" / source["file"]
        if path.is_symlink() or path.name != f"{symbol}{source['date']}.csv.gz":
            raise RuntimeError("SOURCE_ARCHIVE_PATH_NOT_EXACT")
        if path.stat().st_size != int(source["size"]):
            raise RuntimeError("SOURCE_ARCHIVE_SIZE_MISMATCH")
        actual = _sha256(path)
        if actual != source["sha256"]:
            raise RuntimeError("SOURCE_ARCHIVE_SHA256_MISMATCH")
        digest.update(bytes.fromhex(actual))
        day_count = 0
        with gzip.open(path, "rt", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if not set(COLUMNS).issubset(set(reader.fieldnames or ())):
                raise RuntimeError("SOURCE_ARCHIVE_SCHEMA_INVALID")
            for raw in reader:
                day_count += 1
                count += 1
                if raw["symbol"] != symbol or not raw["trdMatchID"]:
                    raise RuntimeError("SOURCE_ARCHIVE_IDENTITY_INVALID")
                at = _dec(raw["timestamp"])
                if last_time is not None and at < last_time:
                    raise RuntimeError("SOURCE_ARCHIVE_EVENT_TIME_REGRESSION")
                last_time = at
                while pending < len(points) and points[pending][0] == count:
                    ordinal, kind, row = points[pending]
                    if ordinal != count:
                        raise AssertionError("unexpected ordered reference")
                    required_time = _dec(
                        row["entry_ts"] if kind == "ENTRY" else row["exit_ts"]
                    )
                    if at != required_time:
                        raise RuntimeError(
                            f"SOURCE_ORDER_TIMESTAMP_MISMATCH:{symbol}:{ordinal}:{kind}"
                        )
                    if kind == "ENTRY":
                        if (
                            raw["trdMatchID"] != row["entry_trade_id"]
                            or _dec(raw["price"]) != _dec(row["entry_price"])
                        ):
                            raise RuntimeError(
                                f"SOURCE_ORDER_ENTRY_MISMATCH:{symbol}:{ordinal}"
                            )
                    elif row["maker_exit"]:
                        px, target = _dec(raw["price"]), _dec(row["exit_price"])
                        if (row["side"] == "LONG" and px < target) or (
                            row["side"] == "SHORT" and px > target
                        ):
                            raise RuntimeError(
                                f"SOURCE_ORDER_RESTING_TP_NOT_CROSSED:{symbol}:{ordinal}"
                            )
                    elif _dec(raw["price"]) != _dec(row["exit_price"]):
                        raise RuntimeError(
                            f"SOURCE_ORDER_EXIT_PRICE_MISMATCH:{symbol}:{ordinal}"
                        )
                    pending += 1
                    matched += 1
        if day_count != int(source["rows"]):
            raise RuntimeError(f"SOURCE_ARCHIVE_ROW_COUNT_MISMATCH:{symbol}:{source['date']}")
    if max_days == SOURCE_DAYS and (
        count != int(manifest["input_rows_counted_warmup"])
        and count != sum(int(x["rows"]) for x in records)
    ):
        # Full source coverage is always the sum across 100 immutable days;
        # the separately recorded warmup-only count is not the full total.
        raise RuntimeError("SOURCE_MANIFEST_TOTAL_COUNT_MISMATCH")
    if max_days == SOURCE_DAYS and pending != len(points):
        raise RuntimeError("SOURCE_ORDER_REFERENCE_ENDPOINT_NOT_REACHED")
    return {
        "symbol": symbol,
        "days": max_days,
        "source_rows_checked": count,
        "matched_reference_endpoints": matched,
        "remaining_reference_endpoints": len(points) - pending,
        "source_manifest_sha256": _sha256(manifest_path),
        "compressed_archive_sha_chain": digest.hexdigest(),
        "first_to_last_checked": [selected[0]["date"], selected[-1]["date"]],
        "status": "PASS_SOURCE_ORDER_SOURCE_LEDGER_AUDIT_ONLY",
    }


def run(
    *,
    root: Path,
    symbols: tuple[str, ...],
    days: int,
    source_commit: str,
) -> dict[str, Any]:
    if not SHA.fullmatch(source_commit):
        raise ValueError("exact 40-char GitHub source commit required")
    if not 1 <= days <= SOURCE_DAYS or not symbols or any(
        x not in ALL_SYMBOLS for x in symbols
    ) or len(set(symbols)) != len(symbols):
        raise ValueError("invalid source symbols/day scope")
    points, summaries, row_counts = load_reference(root, symbols)
    results = [
        audit_symbol(root=root, symbol=symbol, max_days=days, points=points[symbol])
        for symbol in symbols
    ]
    complete = days == SOURCE_DAYS and set(symbols) == set(ALL_SYMBOLS)
    return {
        "research_kind": "SOURCE_ORDER_LEDGER_AUDIT_V1",
        "source_commit": source_commit,
        "source_identity": "BYBIT_LINEAR_HISTORICAL_GZIP_CSV_ORIGINAL_ORDER",
        "full_new_entry_exit_replay": False,
        "live_websocket_exchange_seq_parity": False,
        "do_not_use_as_strategy_readiness": True,
        "complete_five_symbol_source_audit": complete,
        "source_rows_checked": sum(x["source_rows_checked"] for x in results),
        "reference_ledgers": row_counts,
        "reference_net_after_fees": {
            name: {
                scenario: summary["net_pnl_usdt"][scenario]
                for scenario in ("BASELINE", "SECOND_48H")
            }
            for name, summary in summaries.items()
        },
        "net_is_reference_not_new_trade_decision_parity": True,
        "symbols": results,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--symbols", nargs="+", default=list(ALL_SYMBOLS))
    p.add_argument("--days", type=int, default=SOURCE_DAYS)
    p.add_argument("--pilot", action="store_true",
                   help="read-only and no filesystem write")
    p.add_argument("--output-name",
                   help="only a new direct-child under /data/cripta/research/runs")
    args = p.parse_args()
    if os.geteuid() != 107:
        raise PermissionError("RESEARCH_WRITER_ACTOR_NOT_CRIPTA_UID107")
    if not args.pilot and (
        not args.output_name or not NAME.fullmatch(args.output_name)
    ):
        raise ValueError("output-name is mandatory, immutable and scoped")
    if args.pilot and args.output_name:
        raise ValueError("pilot must be read-only without output-name")
    out = RUNS / args.output_name if args.output_name else None
    if out and (out.exists() or out.is_symlink()):
        raise FileExistsError(f"IMMUTABLE_RESEARCH_RESULT_EXISTS:{out}")
    if out and (
        RUNS.stat().st_uid != 107
        or not os.access(RUNS, os.W_OK | os.X_OK)
        or out.parent.resolve() != RUNS.resolve()
    ):
        raise PermissionError("RESEARCH_RESULT_PARENT_NOT_WRITABLE_EXACT_ACTOR")
    result = run(
        root=ROOT, symbols=tuple(args.symbols), days=args.days,
        source_commit=args.source_commit,
    )
    result["checked_utc"] = datetime.now(UTC).isoformat()
    if out:
        out.mkdir(mode=0o2770, exist_ok=False)
        payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
        partial = out / "result.json.tmp"
        with partial.open("x", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(partial, out / "result.json")
        result["result_path"] = str(out / "result.json")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
