from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Any, cast

from bybit_workbench.mayak.research.universe import (
    DISCOVERY_SYMBOLS,
    EXPECTED_DISCOVERY_EVENTS,
    PERIOD_TAG,
)


class AnchorType(StrEnum):
    ENTRY_DECISION = "ENTRY_DECISION"


class PrimaryLabel(StrEnum):
    CONTINUATION_1_10 = "CONTINUATION_1_10"
    INITIAL_OR_PRE05_STOP = "INITIAL_OR_PRE05_STOP"
    PLUS_050_TO_MINUS_050 = "PLUS_050_TO_MINUS_050"
    UNRESOLVED_DATA_END = "UNRESOLVED_DATA_END"


@dataclass(frozen=True, slots=True)
class NormalizedEntryEvent:
    event_id: str
    symbol: str
    side: str
    anchor_type: AnchorType
    anchor_time: datetime
    entry_time: datetime
    outcome_time: datetime | None
    primary_label: PrimaryLabel
    cluster_metadata: dict[str, Any]
    portfolio_metadata: dict[str, Any]
    entry_fingerprint: str
    event_source: str
    dataset_fingerprint: str
    research_version: str


def load_discovery_events(root: Path) -> tuple[NormalizedEntryEvent, ...]:
    """Adapt frozen ALL9 Entry and future truth without recomputing outcomes."""
    feature_rows: dict[tuple[str, str], dict[str, str]] = {}
    source_hash = hashlib.sha256()
    dataset_hash = hashlib.sha256()
    for symbol in DISCOVERY_SYMBOLS:
        asset = root / "reports" / "cross_asset_validation" / f"{symbol}_{PERIOD_TAG}"
        feature_path = asset / "p40" / "absorption_features.csv"
        summary_path = asset / "p40" / "summary.json"
        source_hash.update(feature_path.read_bytes())
        summary = cast(dict[str, Any], json.loads(summary_path.read_text(encoding="utf-8")))
        manifest_path = _resolve_dataset_manifest(root, str(summary["dataset_dir"]))
        dataset_hash.update(manifest_path.read_bytes())
        with feature_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                key = (symbol, str(row["touch_at"]))
                if key in feature_rows:
                    raise ValueError(f"duplicate frozen Entry key: {key}")
                feature_rows[key] = {str(k): str(v or "") for k, v in row.items()}

    outcome_path = (
        root
        / "reports"
        / "early_protection_plus05_minus05_v1"
        / "ALL9_20260819_115647"
        / "event_results.csv"
    )
    source_hash.update(outcome_path.read_bytes())
    entry_fingerprint = source_hash.hexdigest()
    dataset_fingerprint = dataset_hash.hexdigest()
    cluster_ranges = _load_cluster_ranges(root)
    events: list[NormalizedEntryEvent] = []
    with outcome_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row["symbol"])
            touch_raw = str(row["touch_at"])
            feature = feature_rows.pop((symbol, touch_raw), None)
            if feature is None:
                raise ValueError(f"future truth has no exact frozen Entry: {symbol} {touch_raw}")
            entry_time = datetime.fromisoformat(touch_raw).astimezone(UTC)
            outcome_raw = str(row["outcome"])
            label = _label(outcome_raw)
            outcome_time = _optional_utc(str(row.get("event_at") or ""))
            is_failure = label in {
                PrimaryLabel.INITIAL_OR_PRE05_STOP,
                PrimaryLabel.PLUS_050_TO_MINUS_050,
            }
            clustered = bool(
                is_failure
                and outcome_time
                and any(start <= outcome_time <= end for start, end in cluster_ranges)
            )
            event_id = hashlib.sha256(f"{symbol}|{touch_raw}".encode()).hexdigest()
            events.append(
                NormalizedEntryEvent(
                    event_id=event_id,
                    symbol=symbol,
                    side=str(feature["direction"]).upper(),
                    anchor_type=AnchorType.ENTRY_DECISION,
                    anchor_time=entry_time,
                    entry_time=entry_time,
                    outcome_time=outcome_time,
                    primary_label=label,
                    cluster_metadata={
                        "isolated_or_clustered": (
                            "clustered"
                            if clustered
                            else "isolated"
                            if is_failure
                            else "not_failure"
                        )
                    },
                    portfolio_metadata={},
                    entry_fingerprint=entry_fingerprint,
                    event_source=outcome_path.as_posix(),
                    dataset_fingerprint=dataset_fingerprint,
                    research_version="mayak-p1.0.0",
                )
            )
    if feature_rows:
        raise ValueError(f"{len(feature_rows)} frozen Entries have no future truth")
    if len(events) != EXPECTED_DISCOVERY_EVENTS:
        raise ValueError(f"ALL9 count {len(events)} != {EXPECTED_DISCOVERY_EVENTS}")
    hour_counts = Counter(
        event.outcome_time.replace(minute=0, second=0, microsecond=0).isoformat()
        for event in events
        if event.outcome_time
        and event.primary_label
        in {PrimaryLabel.INITIAL_OR_PRE05_STOP, PrimaryLabel.PLUS_050_TO_MINUS_050}
    )
    day_counts = Counter(
        event.outcome_time.date().isoformat()
        for event in events
        if event.outcome_time
        and event.primary_label
        in {PrimaryLabel.INITIAL_OR_PRE05_STOP, PrimaryLabel.PLUS_050_TO_MINUS_050}
    )
    enriched = []
    for event in events:
        hour_key = (
            event.outcome_time.replace(minute=0, second=0, microsecond=0).isoformat()
            if event.outcome_time
            else ""
        )
        day_key = event.outcome_time.date().isoformat() if event.outcome_time else ""
        payload = asdict(event)
        payload["portfolio_metadata"] = {
            "portfolio_hour_severity": hour_counts.get(hour_key, 0),
            "portfolio_day_severity": day_counts.get(day_key, 0),
        }
        enriched.append(NormalizedEntryEvent(**payload))
    return tuple(enriched)


def write_normalized_events(path: Path, events: tuple[NormalizedEntryEvent, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for event in events:
        row = asdict(event)
        row["anchor_type"] = event.anchor_type.value
        row["primary_label"] = event.primary_label.value
        row["anchor_time"] = event.anchor_time.isoformat()
        row["entry_time"] = event.entry_time.isoformat()
        row["outcome_time"] = event.outcome_time.isoformat() if event.outcome_time else None
        payload.append(row)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _label(outcome: str) -> PrimaryLabel:
    if outcome == "reached_1p10":
        return PrimaryLabel.CONTINUATION_1_10
    if outcome in {"baseline_initial_stop", "initial_stop_before_0p50"}:
        return PrimaryLabel.INITIAL_OR_PRE05_STOP
    if outcome == "floor_minus_0p50":
        return PrimaryLabel.PLUS_050_TO_MINUS_050
    if outcome == "data_end_no_activation":
        return PrimaryLabel.UNRESOLVED_DATA_END
    raise ValueError(f"unsupported frozen future outcome: {outcome}")


def _optional_utc(value: str) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if value else None


def _resolve_dataset_manifest(root: Path, dataset_dir_raw: str) -> Path:
    direct = Path(dataset_dir_raw) / "dataset_manifest.json"
    if direct.is_file():
        return direct
    windows_parts = PureWindowsPath(dataset_dir_raw).parts
    try:
        reports_index = tuple(part.lower() for part in windows_parts).index("reports")
    except ValueError as error:
        message = f"dataset report path is not portable: {dataset_dir_raw}"
        raise FileNotFoundError(message) from error
    relative = Path(*windows_parts[reports_index + 1 :]) / "dataset_manifest.json"
    for candidate in (
        root / "reports" / relative,
        root / "test_data" / "fixtures" / relative,
        root.parent / "reports" / relative,
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"dataset manifest is absent for frozen report: {relative}")


def _load_cluster_ranges(root: Path) -> tuple[tuple[datetime, datetime], ...]:
    path = (
        root
        / "reports"
        / "portfolio_replay_v1"
        / "ALL9_20260819_205330"
        / "stop_clusters_15m.csv"
    )
    ranges = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["policy_id"] != "NO_CAP_50_30_20":
                continue
            ranges.append(
                (
                    datetime.fromisoformat(row["cluster_start"]).astimezone(UTC),
                    datetime.fromisoformat(row["cluster_end"]).astimezone(UTC),
                )
            )
    return tuple(ranges)
