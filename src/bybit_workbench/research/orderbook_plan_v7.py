from __future__ import annotations

import argparse
import csv
import json
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

Direction = Literal["Long", "Short"]


@dataclass(frozen=True, slots=True)
class OrderbookPlanConfig:
    pre_seconds: int = 120
    post_seconds: int = 60
    pilot_segments: int = 3

    def __post_init__(self) -> None:
        if self.pre_seconds <= 0:
            raise ValueError("pre_seconds must be positive")
        if self.post_seconds <= 0:
            raise ValueError("post_seconds must be positive")
        if self.pilot_segments <= 0:
            raise ValueError("pilot_segments must be positive")


@dataclass(frozen=True, slots=True)
class BasisSignal:
    symbol: str
    direction: Direction
    candidate_bar_at: datetime
    touch_at: datetime
    entry_price: str
    flow_state: str
    accepted_after_failure_embargo: bool
    oi_tail_danger: bool
    basis_accel_quartile: str

    @property
    def is_core(self) -> bool:
        return (
            self.accepted_after_failure_embargo
            and self.flow_state == "pressure_then_reversal"
            and not self.oi_tail_danger
        )


@dataclass(frozen=True, slots=True)
class OrderbookWindow:
    symbol: str
    direction: Direction
    candidate_bar_at: datetime
    touch_at: datetime
    entry_price: str
    day: date
    segment: int
    window_start: datetime
    window_end: datetime
    flow_state: str
    basis_accel_quartile: str


@dataclass(frozen=True, slots=True)
class DayPlan:
    day: date
    segment: int
    core_signals: int
    accepted_signals: int
    all_signals: int
    priority_rank: int
    cumulative_core_signals: int
    cumulative_core_percent: float


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no", ""}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_basis_signals(path: Path) -> list[BasisSignal]:
    signals: list[BasisSignal] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            signals.append(
                BasisSignal(
                    symbol=row["symbol"],
                    direction=cast(Direction, row["direction"]),
                    candidate_bar_at=_parse_datetime(row["candidate_bar_at"]),
                    touch_at=_parse_datetime(row["touch_at"]),
                    entry_price=row["entry_price"],
                    flow_state=row["flow_state"],
                    accepted_after_failure_embargo=_parse_bool(
                        row["accepted_after_failure_embargo"]
                    ),
                    oi_tail_danger=_parse_bool(row["oi_tail_danger"]),
                    basis_accel_quartile=row["basis_accel_quartile"],
                )
            )
    return signals


def _segment_for(timestamp: datetime, evaluation_start: datetime) -> int:
    elapsed = timestamp - evaluation_start
    return max(1, int(elapsed.total_seconds() // (30 * 24 * 60 * 60)) + 1)


def build_windows(
    signals: list[BasisSignal],
    *,
    evaluation_start: datetime,
    config: OrderbookPlanConfig,
) -> list[OrderbookWindow]:
    windows: list[OrderbookWindow] = []
    for signal in signals:
        if not signal.is_core:
            continue
        windows.append(
            OrderbookWindow(
                symbol=signal.symbol,
                direction=signal.direction,
                candidate_bar_at=signal.candidate_bar_at,
                touch_at=signal.touch_at,
                entry_price=signal.entry_price,
                day=signal.touch_at.date(),
                segment=_segment_for(signal.candidate_bar_at, evaluation_start),
                window_start=signal.touch_at - timedelta(seconds=config.pre_seconds),
                window_end=signal.touch_at + timedelta(seconds=config.post_seconds),
                flow_state=signal.flow_state,
                basis_accel_quartile=signal.basis_accel_quartile,
            )
        )
    return windows


def rank_days(
    signals: list[BasisSignal], windows: list[OrderbookWindow]
) -> list[DayPlan]:
    core_counts: dict[date, int] = {}
    segment_by_day: dict[date, int] = {}
    for window in windows:
        core_counts[window.day] = core_counts.get(window.day, 0) + 1
        segment_by_day.setdefault(window.day, window.segment)

    accepted_counts: dict[date, int] = {}
    all_counts: dict[date, int] = {}
    for signal in signals:
        day = signal.touch_at.date()
        all_counts[day] = all_counts.get(day, 0) + 1
        if signal.accepted_after_failure_embargo:
            accepted_counts[day] = accepted_counts.get(day, 0) + 1

    ranked_days = sorted(
        core_counts,
        key=lambda day: (-core_counts[day], segment_by_day[day], day),
    )
    total_core = sum(core_counts.values())
    cumulative = 0
    plans: list[DayPlan] = []
    for rank, day in enumerate(ranked_days, start=1):
        cumulative += core_counts[day]
        percent = 0.0 if total_core == 0 else cumulative / total_core * 100.0
        plans.append(
            DayPlan(
                day=day,
                segment=segment_by_day[day],
                core_signals=core_counts[day],
                accepted_signals=accepted_counts.get(day, 0),
                all_signals=all_counts.get(day, 0),
                priority_rank=rank,
                cumulative_core_signals=cumulative,
                cumulative_core_percent=round(percent, 2),
            )
        )
    return plans


def choose_pilot_days(days: list[DayPlan], segments: int) -> list[DayPlan]:
    chosen: list[DayPlan] = []
    for segment in range(1, segments + 1):
        candidates = [item for item in days if item.segment == segment]
        if not candidates:
            continue
        chosen.append(
            sorted(
                candidates,
                key=lambda item: (-item.core_signals, item.day),
            )[0]
        )
    return chosen


def probe_orderbook_archive(path: Path, max_records: int = 50) -> dict[str, Any]:
    if max_records <= 0:
        raise ValueError("max_records must be positive")
    record_types: dict[str, int] = {}
    top_level_keys: set[str] = set()
    data_keys: set[str] = set()
    parsed = 0
    member_name = ""
    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if not members:
            raise ValueError("orderbook archive contains no files")
        member = members[0]
        member_name = member.filename
        with archive.open(member, "r") as handle:
            for raw_line in handle:
                if parsed >= max_records:
                    break
                line = raw_line.decode("utf-8", errors="strict").strip()
                if not line:
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                parsed += 1
                top_level_keys.update(str(key) for key in payload)
                record_type = str(payload.get("type", "unknown"))
                record_types[record_type] = record_types.get(record_type, 0) + 1
                data = payload.get("data")
                if isinstance(data, dict):
                    data_keys.update(str(key) for key in data)

    return {
        "archive": str(path),
        "member": member_name,
        "records_parsed": parsed,
        "record_types": record_types,
        "top_level_keys": sorted(top_level_keys),
        "data_keys": sorted(data_keys),
        "looks_like_v5_snapshot_delta": (
            parsed > 0
            and bool({"snapshot", "delta"}.intersection(record_types))
            and "data" in top_level_keys
        ),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def serializer(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        raise TypeError(f"cannot serialize {type(value).__name__}")

    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=serializer) + "\n",
        encoding="utf-8",
    )


def _latest_p36_dir(root: Path) -> Path:
    base = root / "reports" / "entry_research_v9"
    candidates = [
        path
        for path in base.glob("UNIUSDT_*")
        if (path / "summary.json").is_file() and (path / "signals_basis.csv").is_file()
    ]
    if not candidates:
        raise FileNotFoundError("no completed P36 result found")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _default_output_dir(root: Path, symbol: str) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    return root / "reports" / "entry_research_v10" / f"{symbol}_{stamp}"


def run_plan(
    *,
    p36_dir: Path,
    output_dir: Path,
    config: OrderbookPlanConfig,
    probe_archive: Path | None,
) -> dict[str, Any]:
    summary_path = p36_dir / "summary.json"
    signals_path = p36_dir / "signals_basis.csv"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    evaluation_start = _parse_datetime(str(summary["evaluation_start"]))
    evaluation_end = _parse_datetime(str(summary["evaluation_end"]))
    signals = load_basis_signals(signals_path)
    windows = build_windows(
        signals,
        evaluation_start=evaluation_start,
        config=config,
    )
    days = rank_days(signals, windows)
    pilot = choose_pilot_days(days, config.pilot_segments)

    _write_csv(output_dir / "orderbook_windows.csv", [asdict(item) for item in windows])
    _write_csv(output_dir / "orderbook_days.csv", [asdict(item) for item in days])
    _write_csv(output_dir / "pilot_days.csv", [asdict(item) for item in pilot])

    probe = None
    if probe_archive is not None:
        probe = probe_orderbook_archive(probe_archive)
        _write_json(output_dir / "archive_probe.json", probe)

    accepted = sum(1 for signal in signals if signal.accepted_after_failure_embargo)
    result: dict[str, Any] = {
        "architecture": "p37_orderbook_research_planner",
        "p36_dir": p36_dir,
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "config": asdict(config),
        "signals": {
            "all": len(signals),
            "accepted_after_60m_pause": accepted,
            "core_pressure_reversal_without_oi_tail": len(windows),
            "core_unique_days": len(days),
        },
        "pilot_days": [asdict(item) for item in pilot],
        "archive_probe": probe,
        "notes": [
            "P37 changes no live trading, stop-loss, take-profit, or exit logic.",
            "The planner requests only windows around existing core entry candidates.",
            "Historical orderbook data can be large, so P37 does not bulk-download 90 days.",
            "The archive probe checks schema before a full snapshot/delta reconstructor is added.",
            "Orderbook features remain diagnostic until they survive multi-month validation.",
        ],
    }
    _write_json(output_dir / "summary.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P37 historical orderbook research planner")
    parser.add_argument("--p36-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--pre-seconds", type=int, default=120)
    parser.add_argument("--post-seconds", type=int, default=60)
    parser.add_argument("--probe-archive", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path.cwd()
    p36_dir = args.p36_dir or _latest_p36_dir(root)
    output_dir = args.output_dir or _default_output_dir(root, "UNIUSDT")
    config = OrderbookPlanConfig(
        pre_seconds=args.pre_seconds,
        post_seconds=args.post_seconds,
    )
    result = run_plan(
        p36_dir=p36_dir,
        output_dir=output_dir,
        config=config,
        probe_archive=args.probe_archive,
    )
    signal_info = cast(dict[str, Any], result["signals"])
    print(f"P36 source: {p36_dir}")
    print(f"Core orderbook candidates: {signal_info['core_pressure_reversal_without_oi_tail']}")
    print(f"Unique orderbook days: {signal_info['core_unique_days']}")
    print(f"Report: {output_dir / 'summary.json'}")
    if args.probe_archive is None:
        print("No historical orderbook archive was supplied; plan-only mode completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
