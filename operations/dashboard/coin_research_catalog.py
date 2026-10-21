"""Read-only proposed per-instrument research evidence catalogue.

This module has NO writer, DB connection, or trading dependency.
The Git-versioned physical-store proposal is review-only until an explicit
owner storage/identity decision and a verified release.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("instrument_research_catalog.json")
SCHEMA = "CRIPTA_COIN_RESEARCH_PROPOSAL_V1"


def read_catalog(path: Path | None = None) -> dict[str, Any]:
    """Fail closed on absent, non-versioned or inconsistent evidence.

    Missing exact instrument_id is allowed only as explicit PROPOSAL status,
    not as REGISTERED research evidence.
    """
    source = CATALOG_PATH if path is None else path
    if not source.is_file() or source.stat().st_size > 500_000:
        raise ValueError("RESEARCH_CATALOG_ABSENT_OR_TOO_LARGE")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("RESEARCH_CATALOG_UNREADABLE") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError("RESEARCH_CATALOG_SCHEMA")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("RESEARCH_CATALOG_EMPTY")
    if len(records) != payload.get("record_count"):
        raise ValueError("RESEARCH_CATALOG_COUNT")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("RESEARCH_CATALOG_RECORD_TYPE")
        key = record.get("record_id")
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("RESEARCH_CATALOG_IDEMPOTENCY")
        seen.add(key)
        identity = record.get("instrument")
        quality = record.get("quality")
        if not isinstance(identity, dict) or not isinstance(quality, dict):
            raise ValueError("RESEARCH_CATALOG_PROVENANCE")
        if not identity.get("symbol") or not identity.get("venue_id"):
            raise ValueError("RESEARCH_CATALOG_INSTRUMENT")
        if identity.get("instrument_id") is None and (
            identity.get("identity_status") != "PENDING_EXACT_INSTRUMENT_ID_RESOLUTION"
            or quality.get("publication_qc") != "PROPOSAL_ONLY"
        ):
            raise ValueError("RESEARCH_CATALOG_UNRESOLVED_IDENTITY")
        counts = record.get("counts")
        money = record.get("economics")
        if not isinstance(counts, dict) or not isinstance(money, dict):
            raise ValueError("RESEARCH_CATALOG_NUMERIC_FIELDS")
        def _count(name: str) -> int:
            val = counts.get(name)
            if type(val) is not int or val < 0:
                raise ValueError("RESEARCH_CATALOG_INVALID_COUNT")
            return val

        opened = _count("opened")
        closed = _count("closed")
        if (
            opened != closed + _count("open_censored")
            or _count("qualified_entry_candidates") != opened + _count("skipped_occupied")
            or closed != sum(
                _count(x) for x in (
                    "tp_plus", "tp_minus", "protected_stop_plus",
                    "protected_stop_minus", "hard_stop",
                )
            )
        ):
            raise ValueError("RESEARCH_CATALOG_COUNT_BALANCE")
        names = (
            "profit_tp_usdt", "profit_protected_usdt", "loss_protected_usdt",
            "loss_hard_usdt", "realized_net_usdt",
            "gross_positive_after_fees_usdt", "total_negative_after_fees_usdt",
        )
        if any(
            type(money.get(name)) not in (float, int)
            or not math.isfinite(money[name])
            for name in names
        ):
            raise ValueError("RESEARCH_CATALOG_INVALID_PNL")
        plus = money["profit_tp_usdt"] + money["profit_protected_usdt"]
        losses = money["loss_protected_usdt"] + money["loss_hard_usdt"]
        if (
            abs(plus - money["gross_positive_after_fees_usdt"]) > 1e-7
            or abs(losses - money["total_negative_after_fees_usdt"]) > 1e-7
            or abs(plus + losses - money["realized_net_usdt"]) > 1e-7
        ):
            raise ValueError("RESEARCH_CATALOG_PNL_BALANCE")
    return payload


def read_symbol_records(symbol: str, path: Path | None = None) -> dict[str, Any]:
    """Projection only; NEVER aggregate distinct scenarios into a fake ROI."""
    catalog = read_catalog(path)
    if not symbol.isascii() or not symbol.isalnum() or len(symbol) > 32:
        raise ValueError("RESEARCH_CATALOG_INVALID_SYMBOL")
    selected = [
        r for r in catalog["records"]
        if r["instrument"]["symbol"] == symbol
    ]
    return {
        "schema": catalog["schema"],
        "storage_approval": catalog["storage_approval"],
        "symbol": symbol,
        "record_count": len(selected),
        "records": sorted(
            selected,
            key=lambda r: (r["historical_date"], r["record_id"]),
            reverse=True,
        ),
    }
