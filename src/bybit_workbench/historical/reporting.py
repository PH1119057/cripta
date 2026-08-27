from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, cast

from .runner import HistoricalRunConfig, HistoricalRunResult
from .validation import instrument_rules_fingerprint, parameters_fingerprint


def build_backtest_manifest(
    result: HistoricalRunResult,
    config: HistoricalRunConfig,
    *,
    dataset_path: Path,
    mark_path: Path | None = None,
    funding_path: Path | None = None,
    code_version: str = "unknown",
    rerun: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    outcomes = result.outcomes
    submitted_entries = {
        item.intent.intent_id
        for item in outcomes
        if type(item.intent).__name__ == "EnterIntent" and item.submitted
    }
    entry_fill_counts: dict[str, int] = {}
    for fill in result.fills:
        if fill.reason.value == "Entry":
            entry_fill_counts[fill.client_order_id] = (
                entry_fill_counts.get(fill.client_order_id, 0) + 1
            )
    filled_entries = set(entry_fill_counts)
    monthly = _period_pnl(result, quarterly=False)
    quarterly = _period_pnl(result, quarterly=True)
    return cast(
        dict[str, Any],
        _json_safe(
            {
                "schema_version": "backtest-report-v2",
                "badge": "Research only · Micro-Live blocked",
                "code_version": code_version,
                "strategy": {
                    "id": result.metadata.strategy_id,
                    "version": result.metadata.version,
                    "display_name": result.metadata.display_name,
                    "parameters": result.parameters,
                    "parameters_fingerprint": parameters_fingerprint(result.parameters),
                },
                "dataset": {
                    "trade_path": str(dataset_path.resolve()),
                    "mark_path": None if mark_path is None else str(mark_path.resolve()),
                    "funding_path": None if funding_path is None else str(funding_path.resolve()),
                    "fingerprint": result.dataset_fingerprint,
                    "symbol": result.symbol,
                    "timeframe": result.timeframe,
                    "warmup_fingerprint": result.warmup_fingerprint,
                    "quality": {
                        **asdict(result.data_quality),
                        "production_equivalent": result.data_quality.production_equivalent,
                    },
                },
                "instrument_rules": {
                    **asdict(config.instrument_rules),
                    "fingerprint": instrument_rules_fingerprint(config.instrument_rules),
                },
                "execution": {
                    "mode": result.execution_mode,
                    "price_trigger": result.price_trigger,
                    "forced_flatten": result.forced_flatten,
                    "seed": config.replay.seed,
                    "maker_fee_rate": config.replay.effective_maker_fee_rate,
                    "taker_fee_rate": config.replay.effective_taker_fee_rate,
                    "slippage_percent": config.replay.slippage_percent,
                    "funding_events_applied": result.funding_events_applied,
                },
                "capital": {
                    "initial_equity": config.initial_equity,
                    "available_balance": config.available_balance,
                    "ending_equity": config.initial_equity + result.net_realized_pnl,
                    "return_percent": (
                        result.net_realized_pnl / config.initial_equity * Decimal("100")
                    ),
                },
                "counts": {
                    "signals": sum(
                        type(item.intent).__name__ == "EnterIntent" for item in outcomes
                    ),
                    "submitted": sum(item.submitted for item in outcomes),
                    "rejected": sum(
                        item.risk_decision is not None and not item.risk_decision.approved
                        for item in outcomes
                    ),
                    "cancelled": sum(
                        type(item.intent).__name__ == "CancelEntryIntent" and item.submitted
                        for item in outcomes
                    ),
                    "expired": sum(
                        type(item.intent).__name__ == "CancelEntryIntent"
                        and "expired" in item.intent.reason.lower()
                        for item in outcomes
                    ),
                    "skipped_noop": sum(
                        type(item.intent).__name__ == "NoOpIntent" for item in outcomes
                    ),
                    "fills": len(result.fills),
                    "trades": len(result.trades),
                    "limit_fill_rate": (
                        None
                        if not submitted_entries
                        else Decimal(len(filled_entries)) / Decimal(len(submitted_entries))
                    ),
                    "multi_fill_partial_fraction": (
                        None
                        if not filled_entries
                        else Decimal(sum(count > 1 for count in entry_fill_counts.values()))
                        / Decimal(len(filled_entries))
                    ),
                },
                "metrics": result.metrics,
                "equity_curve": result.equity_curve,
                "pnl_breakdown": {
                    "gross_pnl_after_modelled_slippage": sum(
                        (trade.gross_pnl for trade in result.trades), Decimal("0")
                    ),
                    "fees": sum((trade.fees for trade in result.trades), Decimal("0")),
                    "funding": sum((trade.funding for trade in result.trades), Decimal("0")),
                    "net_pnl": result.net_realized_pnl,
                    "slippage_estimate": sum(
                        (fill.slippage_cost for fill in result.fills), Decimal("0")
                    ),
                    "slippage_note": (
                        "Modelled adverse slippage is attributed on taker/protective exits; "
                        "resting limit entries have zero modelled slippage."
                    ),
                },
                "period_results": {"monthly": monthly, "quarterly": quarterly},
                "outcomes": outcomes,
                "fills": result.fills,
                "trades": result.trades,
                "rerun": {} if rerun is None else dict(rerun),
                "limitations": (
                    "Research only. Candle replay approximates intrabar order, queue position, "
                    "liquidity, latency and outages; it is not evidence of future profitability."
                ),
            },
        ),
    )


def write_backtest_json(path: Path | str, manifest: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_trades_csv(path: Path | str, result: HistoricalRunResult) -> None:
    fields = (
        "symbol",
        "side",
        "quantity",
        "entry_price",
        "exit_price",
        "gross_pnl",
        "fees",
        "funding",
        "net_pnl",
        "exit_reason",
        "opened_at",
        "closed_at",
        "ambiguous_bar",
    )
    with Path(path).open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for trade in result.trades:
            row = _json_safe(asdict(trade))
            writer.writerow({name: row[name] for name in fields})


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=str)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _period_pnl(result: HistoricalRunResult, *, quarterly: bool) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    for trade in result.trades:
        if quarterly:
            quarter = (trade.closed_at.month - 1) // 3 + 1
            key = f"{trade.closed_at.year}-Q{quarter}"
        else:
            key = f"{trade.closed_at.year}-{trade.closed_at.month:02d}"
        values[key] = values.get(key, Decimal("0")) + trade.net_pnl
    return values
