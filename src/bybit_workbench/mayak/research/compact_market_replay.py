from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bybit_workbench.mayak.compact_analytics import (
    COMPACT_ANALYTICS_SCHEMA_VERSION,
)
from bybit_workbench.mayak.compact_reader import compact_panel_projection


@dataclass(frozen=True, slots=True)
class CompactMarketPanel:
    market_source_id: str
    observed_minute: str
    rows: tuple[dict[str, Any], ...]

    def analytical_payloads(self) -> dict[str, dict[str, Any]]:
        return compact_panel_projection(self.rows)


def iter_archived_compact_panels(path: Path) -> Iterator[CompactMarketPanel]:
    """Stream exact R8c panels from archive_v2 market_minutes JSONL.

    archive_v2 orders this table by observed_minute. Rows for one minute may
    contain multiple MarketSource identities, so each minute is partitioned
    without erasing source lineage before a panel is emitted.
    """

    current_minute: str | None = None
    bucket: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            item = json.loads(raw)
            if not isinstance(item, dict):
                raise ValueError(f"compact archive row {line_number} is not an object")
            _validate_archive_row(item, line_number)
            minute = str(item["observed_minute"])

            if current_minute is not None and minute < current_minute:
                raise ValueError(
                    "compact archive is not ordered by observed_minute: "
                    f"line={line_number}"
                )
            if current_minute is not None and minute != current_minute:
                yield from _panels_for_minute(current_minute, bucket)
                bucket = []
            current_minute = minute
            bucket.append(item)

    if current_minute is not None:
        yield from _panels_for_minute(current_minute, bucket)


def _panels_for_minute(
    observed_minute: str,
    rows: list[dict[str, Any]],
) -> Iterator[CompactMarketPanel]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        market_source_id = str(row["market_source_id"])
        by_source.setdefault(market_source_id, []).append(row)
    for market_source_id in sorted(by_source):
        source_rows = tuple(by_source[market_source_id])
        compact_panel_projection(source_rows)
        yield CompactMarketPanel(
            market_source_id=market_source_id,
            observed_minute=observed_minute,
            rows=source_rows,
        )


def _validate_archive_row(row: Mapping[str, Any], line_number: int) -> None:
    required = (
        "observed_minute",
        "market_source_id",
        "instrument_id",
        "external_symbol",
        "product_category",
        "schema_version",
        "config_fingerprint",
    )
    missing = [field for field in required if not row.get(field)]
    if missing:
        raise ValueError(
            f"compact archive row {line_number} missing fields: {','.join(missing)}"
        )
    if row["schema_version"] != COMPACT_ANALYTICS_SCHEMA_VERSION:
        raise ValueError(
            "compact archive schema mismatch: "
            f"line={line_number} expected={COMPACT_ANALYTICS_SCHEMA_VERSION} "
            f"actual={row['schema_version']}"
        )
