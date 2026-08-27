from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

from bybit_workbench.research.orderbook_cache_utils import find_local_orderbook_archive
from bybit_workbench.research.orderbook_pilot_v8 import (
    PilotWindow,
    analyze_archive,
    discover_archive,
    download_archive,
    load_windows,
)

FEATURES_FOR_QUARTILES = (
    "directional_imbalance_5bps_p0s",
    "directional_imbalance_10bps_p0s",
    "directional_imbalance_25bps_p0s",
    "directional_imbalance_50bps_p0s",
    "directional_imbalance_5bps_change_m30_to_touch",
    "directional_imbalance_10bps_change_m30_to_touch",
    "directional_imbalance_25bps_change_m30_to_touch",
    "directional_imbalance_50bps_change_m30_to_touch",
    "support_wall_distance_advantage_bps_p0s",
    "support_wall_notional_ratio_to_adverse_p0s",
    "support_wall_notional_ratio_m30_to_touch",
    "spread_bps_p0s",
    "spread_change_m30_to_touch_bps",
)

BINARY_STATES = (
    "support_wall_closer",
    "support_wall_larger",
    "both_wall_advantages",
    "near_imbalance_positive",
    "near_imbalance_improving",
    "near_imbalance_positive_or_improving",
    "near_imbalance_positive_and_improving",
)

OUTCOME_LABELS = ("first_0_5_vs_1_0", "first_1_0_vs_1_0")


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    support_distance = _float_or_none(row.get("support_wall_distance_bps_p0s"))
    adverse_distance = _float_or_none(row.get("adverse_wall_distance_bps_p0s"))
    support_notional = _float_or_none(row.get("support_wall_notional_p0s"))
    adverse_notional = _float_or_none(row.get("adverse_wall_notional_p0s"))
    near_imbalance = _float_or_none(row.get("directional_imbalance_5bps_p0s"))
    near_change = _float_or_none(
        row.get("directional_imbalance_5bps_change_m30_to_touch")
    )

    if support_distance is not None and adverse_distance is not None:
        result["support_wall_distance_advantage_bps_p0s"] = (
            adverse_distance - support_distance
        )
        result["support_wall_closer"] = _bool_text(support_distance < adverse_distance)
    else:
        result["support_wall_distance_advantage_bps_p0s"] = None
        result["support_wall_closer"] = ""

    if support_notional is not None and adverse_notional is not None:
        result["support_wall_notional_ratio_to_adverse_p0s"] = (
            support_notional / adverse_notional if adverse_notional > 0 else None
        )
        result["support_wall_larger"] = _bool_text(support_notional > adverse_notional)
    else:
        result["support_wall_notional_ratio_to_adverse_p0s"] = None
        result["support_wall_larger"] = ""

    if result.get("support_wall_closer") and result.get("support_wall_larger"):
        result["both_wall_advantages"] = _bool_text(
            result["support_wall_closer"] == "true"
            and result["support_wall_larger"] == "true"
        )
    else:
        result["both_wall_advantages"] = ""

    if near_imbalance is not None:
        result["near_imbalance_positive"] = _bool_text(near_imbalance > 0)
    else:
        result["near_imbalance_positive"] = ""
    if near_change is not None:
        result["near_imbalance_improving"] = _bool_text(near_change > 0)
    else:
        result["near_imbalance_improving"] = ""

    if near_imbalance is not None and near_change is not None:
        result["near_imbalance_positive_or_improving"] = _bool_text(
            near_imbalance > 0 or near_change > 0
        )
        result["near_imbalance_positive_and_improving"] = _bool_text(
            near_imbalance > 0 and near_change > 0
        )
    else:
        result["near_imbalance_positive_or_improving"] = ""
        result["near_imbalance_positive_and_improving"] = ""
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _outcome_counts(rows: Iterable[dict[str, Any]], label: str) -> dict[str, Any]:
    data = list(rows)
    total = len(data)
    favorable = sum(row.get(label) == "favorable_first" for row in data)
    adverse = sum(row.get(label) == "adverse_first" for row in data)
    neither = total - favorable - adverse
    return {
        "count": total,
        "favorable": favorable,
        "adverse": adverse,
        "neither": neither,
        "favorable_percent": favorable / total * 100.0 if total else None,
        "decisive_favorable_percent": (
            favorable / (favorable + adverse) * 100.0 if favorable + adverse else None
        ),
    }


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        raise ValueError("cannot calculate percentile of an empty series")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def quartile_boundaries(
    rows: list[dict[str, Any]], feature: str
) -> tuple[float, float, float] | None:
    values = sorted(
        value
        for row in rows
        if (value := _float_or_none(row.get(feature))) is not None
    )
    if len(values) < 4:
        return None
    return (
        _percentile(values, 0.25),
        _percentile(values, 0.50),
        _percentile(values, 0.75),
    )


def _quartile(value: float, boundaries: tuple[float, float, float]) -> str:
    q1, q2, q3 = boundaries
    if value <= q1:
        return "Q1"
    if value <= q2:
        return "Q2"
    if value <= q3:
        return "Q3"
    return "Q4"


def build_quartile_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for feature in FEATURES_FOR_QUARTILES:
        boundaries = quartile_boundaries(rows, feature)
        if boundaries is None:
            continue
        buckets: dict[str, list[dict[str, Any]]] = {q: [] for q in ("Q1", "Q2", "Q3", "Q4")}
        for row in rows:
            value = _float_or_none(row.get(feature))
            if value is not None:
                buckets[_quartile(value, boundaries)].append(row)
        for quartile, bucket in buckets.items():
            record: dict[str, Any] = {
                "feature": feature,
                "quartile": quartile,
                "q25": boundaries[0],
                "q50": boundaries[1],
                "q75": boundaries[2],
            }
            for label in OUTCOME_LABELS:
                stats = _outcome_counts(bucket, label)
                record[f"{label}_count"] = stats["count"]
                record[f"{label}_favorable_percent"] = stats["favorable_percent"]
                record[f"{label}_decisive_favorable_percent"] = stats[
                    "decisive_favorable_percent"
                ]
            output.append(record)
    return output


def build_binary_state_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for state in BINARY_STATES:
        for state_value in ("true", "false"):
            bucket = [row for row in rows if row.get(state) == state_value]
            record: dict[str, Any] = {"state": state, "value": state_value}
            for label in OUTCOME_LABELS:
                stats = _outcome_counts(bucket, label)
                record[f"{label}_count"] = stats["count"]
                record[f"{label}_favorable_percent"] = stats["favorable_percent"]
                record[f"{label}_decisive_favorable_percent"] = stats[
                    "decisive_favorable_percent"
                ]
            output.append(record)
    return output


def build_stability_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    groups: list[tuple[str, str, list[dict[str, Any]]]] = []
    for segment in ("1", "2", "3"):
        segment_rows = [
            row for row in rows if str(row.get("segment")) == segment
        ]
        groups.append(("segment", segment, segment_rows))
    for direction in ("Long", "Short"):
        direction_rows = [row for row in rows if row.get("direction") == direction]
        groups.append(("direction", direction, direction_rows))

    for group_type, group_value, bucket in groups:
        record: dict[str, Any] = {
            "group_type": group_type,
            "group_value": group_value,
            "count": len(bucket),
        }
        for label in OUTCOME_LABELS:
            stats = _outcome_counts(bucket, label)
            record[f"{label}_favorable_percent"] = stats["favorable_percent"]
            record[f"{label}_decisive_favorable_percent"] = stats[
                "decisive_favorable_percent"
            ]
        output.append(record)
        for state in BINARY_STATES:
            true_bucket = [row for row in bucket if row.get(state) == "true"]
            state_record: dict[str, Any] = {
                "group_type": f"{group_type}+state",
                "group_value": f"{group_value}|{state}=true",
                "count": len(true_bucket),
            }
            for label in OUTCOME_LABELS:
                stats = _outcome_counts(true_bucket, label)
                state_record[f"{label}_favorable_percent"] = stats["favorable_percent"]
                state_record[f"{label}_decisive_favorable_percent"] = stats[
                    "decisive_favorable_percent"
                ]
            output.append(state_record)
    return output


def _latest_dir(root: Path, report_name: str, required_file: str) -> Path:
    base = root / "reports" / report_name
    candidates = [path for path in base.glob("UNIUSDT_*") if (path / required_file).is_file()]
    if not candidates:
        raise FileNotFoundError(f"no completed {report_name} result found")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def resolve_output_dir(root: Path, symbol: str, p37_dir: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    base = root / "reports" / "entry_research_v12"
    if base.is_dir():
        incomplete: list[Path] = []
        for candidate in base.glob(f"{symbol}_*"):
            state = _read_state(candidate / "run_state.json")
            if (
                state is not None
                and state.get("complete") is False
                and state.get("p37_dir") == str(p37_dir)
            ):
                incomplete.append(candidate)
        if incomplete:
            return max(incomplete, key=lambda path: path.stat().st_mtime)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    return base / f"{symbol}_{stamp}"


def _archive_dir(p36_dir: Path, override: Path | None) -> Path:
    if override is not None:
        return override
    p36_summary = json.loads((p36_dir / "summary.json").read_text(encoding="utf-8"))
    dataset_dir = Path(str(p36_summary["dataset_dir"]))
    return dataset_dir / "orderbook_full"


def _day_cache_path(output_dir: Path, day: date) -> Path:
    return output_dir / "day_features" / f"{day.isoformat()}.csv"


def _day_stats_path(output_dir: Path, day: date) -> Path:
    return output_dir / "day_stats" / f"{day.isoformat()}.json"


def _orderbook_worker_count() -> int:
    raw = os.environ.get("BYBIT_RESEARCH_ORDERBOOK_WORKERS", "1").strip()
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(1, min(4, value))


def _analyze_full_day_task(
    archive_path: Path,
    day_windows: list[PilotWindow],
    archive_depth: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, stats = analyze_archive(archive_path, day_windows)
    enriched: list[dict[str, Any]] = []
    archive_bytes = archive_path.stat().st_size
    for row in rows:
        item = enrich_row(row)
        item["archive_depth"] = archive_depth
        item["archive_bytes"] = archive_bytes
        enriched.append(item)
    return enriched, {
        **stats,
        "depth": archive_depth,
        "feature_rows": len(enriched),
        "missing": False,
    }


def run_full(
    *,
    p37_dir: Path,
    output_dir: Path,
    archive_dir: Path | None,
    keep_archives: bool,
    max_days: int | None,
) -> dict[str, Any]:
    p37_summary = json.loads((p37_dir / "summary.json").read_text(encoding="utf-8"))
    p36_dir = Path(str(p37_summary["p36_dir"]))
    windows = load_windows(p37_dir, p36_dir, pilot_only=False)
    if not windows:
        raise ValueError("P37 orderbook plan has no core windows")
    symbol = windows[0].symbol
    days = sorted({window.day for window in windows})
    if max_days is not None:
        days = days[:max_days]
    archive_dir = _archive_dir(p36_dir, archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        output_dir / "run_state.json",
        {
            "architecture": "p39_orderbook_full_microstructure",
            "p37_dir": str(p37_dir),
            "p36_dir": str(p36_dir),
            "complete": False,
            "expected_days": len(days),
            "expected_windows": sum(window.day in set(days) for window in windows),
        },
    )

    all_rows: list[dict[str, Any]] = []
    all_stats: list[dict[str, Any]] = []
    processed = 0
    pending: list[tuple[date, Path, int, str, list[PilotWindow]]] = []
    for index, day in enumerate(days, start=1):
        day_cache = _day_cache_path(output_dir, day)
        day_stats = _day_stats_path(output_dir, day)
        if day_cache.is_file() and day_stats.is_file():
            cached = _read_csv(day_cache)
            all_rows.extend(cached)
            state = _read_state(day_stats)
            if state is not None:
                all_stats.append(state)
            processed += 1
            print(f"P39 orderbook day {index}/{len(days)}: {day} (reuse cache)")
            continue

        print(f"P39 orderbook day {index}/{len(days)}: {day}")
        local_archive = find_local_orderbook_archive(
            archive_dir, symbol=symbol, day=day
        )
        if local_archive is not None:
            archive_path, archive_depth = local_archive
            filename = archive_path.name
            print(f"  reuse local orderbook archive: {archive_path}")
        else:
            if os.environ.get("BYBIT_RESEARCH_ORDERBOOK_LOCAL_ONLY") == "1":
                raise FileNotFoundError(
                    f"local orderbook archive missing for {symbol} {day} in {archive_dir}; "
                    "heavy downloads are disabled"
                )
            discovery = discover_archive(symbol, day)
            selected = discovery["selected"]
            if selected is None:
                print("  no historical orderbook archive found")
                missing_stats = {
                    "day": day.isoformat(),
                    "missing": True,
                    "probes": discovery["probes"],
                }
                _write_csv(day_cache, [])
                _write_json(day_stats, missing_stats)
                all_stats.append(missing_stats)
                processed += 1
                continue
            url = str(selected["url"])
            filename = url.rsplit("/", 1)[-1]
            target = archive_dir / filename
            size = cast(int | None, selected.get("content_length"))
            if size is not None:
                free = shutil.disk_usage(archive_dir).free
                if free < size + 512 * 1024 * 1024:
                    raise OSError(
                        f"not enough free disk space for {filename}: need about {size} bytes"
                    )
            print(f"  download remote orderbook archive: {filename}")
            archive_path = download_archive(url, target, expected_size=size)
            archive_depth = int(selected["depth"])

        day_windows = [window for window in windows if window.day == day]
        pending.append((day, archive_path, archive_depth, filename, day_windows))

    workers = _orderbook_worker_count()
    if workers > 1 and len(pending) > 1:
        print(f"P39 parallel analysis: workers={workers} pending_days={len(pending)}")
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _analyze_full_day_task, archive_path, day_windows, archive_depth
                ): (day, archive_path, filename)
                for day, archive_path, archive_depth, filename, day_windows in pending
            }
            completed_pending = 0
            for future in as_completed(futures):
                day, archive_path, filename = futures[future]
                enriched, stats_record = future.result()
                stats_record = {"day": day.isoformat(), **stats_record}
                _write_csv(_day_cache_path(output_dir, day), enriched)
                _write_json(_day_stats_path(output_dir, day), stats_record)
                all_rows.extend(enriched)
                all_stats.append(stats_record)
                processed += 1
                completed_pending += 1  # noqa: SIM113
                print(
                    f"P39 parallel completed {completed_pending}/{len(pending)}: {day}",
                    flush=True,
                )
                if not keep_archives:
                    archive_path.unlink(missing_ok=True)
                    print(f"  processed and removed raw archive: {filename}")
    else:
        for pending_index, (
            day,
            archive_path,
            archive_depth,
            filename,
            day_windows,
        ) in enumerate(pending, start=1):
            enriched, stats_record = _analyze_full_day_task(
                archive_path, day_windows, archive_depth
            )
            stats_record = {"day": day.isoformat(), **stats_record}
            _write_csv(_day_cache_path(output_dir, day), enriched)
            _write_json(_day_stats_path(output_dir, day), stats_record)
            all_rows.extend(enriched)
            all_stats.append(stats_record)
            processed += 1
            print(
                f"P39 completed {pending_index}/{len(pending)}: {day}",
                flush=True,
            )
            if not keep_archives:
                archive_path.unlink(missing_ok=True)
                print(f"  processed and removed raw archive: {filename}")

    all_rows.sort(key=lambda row: str(row.get("touch_at", "")))
    _write_csv(output_dir / "orderbook_features.csv", all_rows)
    binary_rows = build_binary_state_rows(all_rows)
    quartile_rows = build_quartile_rows(all_rows)
    stability_rows = build_stability_rows(all_rows)
    _write_csv(output_dir / "orderbook_binary_states.csv", binary_rows)
    _write_csv(output_dir / "orderbook_quartiles.csv", quartile_rows)
    _write_csv(output_dir / "monthly_stability.csv", stability_rows)

    baseline = {label: _outcome_counts(all_rows, label) for label in OUTCOME_LABELS}
    result = {
        "architecture": "p39_orderbook_full_microstructure",
        "p37_dir": str(p37_dir),
        "p36_dir": str(p36_dir),
        "archive_dir": str(archive_dir),
        "keep_archives": keep_archives,
        "planned_days": len(days),
        "processed_days": processed,
        "planned_windows": sum(window.day in set(days) for window in windows),
        "feature_rows": len(all_rows),
        "missing_days": [item["day"] for item in all_stats if item.get("missing")],
        "baseline_outcomes": baseline,
        "archive_stats": all_stats,
        "notes": [
            "P39 changes no live trading, stop-loss, take-profit, exit, or risk-engine logic.",
            "P39 expands P38 from the 13-row pilot to the full P37 core orderbook sample.",
            "Pre-touch features use only orderbook state at or before the requested timestamp.",
            "Post-touch fields remain diagnostic and are not used as executable entry information.",
            "Binary states and quartiles are descriptive research outputs, not trading gates.",
            "Per-day caches make the 59-day download/reconstruction pass safely resumable.",
        ],
    }
    _write_json(output_dir / "summary.json", result)
    _write_json(
        output_dir / "run_state.json",
        {
            "architecture": "p39_orderbook_full_microstructure",
            "p37_dir": str(p37_dir),
            "p36_dir": str(p36_dir),
            "complete": processed == len(days),
            "processed_days": processed,
            "expected_days": len(days),
            "feature_rows": len(all_rows),
        },
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P39 full historical orderbook microstructure")
    parser.add_argument("--p37-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--keep-archives", action="store_true")
    parser.add_argument("--max-days", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path.cwd()
    p37_dir = args.p37_dir or _latest_dir(root, "entry_research_v10", "summary.json")
    p37_summary = json.loads((p37_dir / "summary.json").read_text(encoding="utf-8"))
    p36_dir = Path(str(p37_summary["p36_dir"]))
    windows = load_windows(p37_dir, p36_dir, pilot_only=False)
    symbol = windows[0].symbol if windows else "UNIUSDT"
    output_dir = resolve_output_dir(root, symbol, p37_dir, args.output_dir)
    result = run_full(
        p37_dir=p37_dir,
        output_dir=output_dir,
        archive_dir=args.archive_dir,
        keep_archives=args.keep_archives,
        max_days=args.max_days,
    )
    print(f"P37 source: {p37_dir}")
    print(f"Orderbook days processed: {result['processed_days']}/{result['planned_days']}")
    print(f"Orderbook feature rows: {result['feature_rows']}")
    print(f"Report: {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
