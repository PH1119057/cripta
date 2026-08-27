from __future__ import annotations

import argparse
import bisect
import csv
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from bybit_workbench.research.exit_break_even_v13 import (
    CoreSignal,
    SignalSource,
    TradeDayCache,
    directional_move_pct,
    discover_source,
    load_core_signals,
)
from bybit_workbench.research.flow_reversal_v1 import _archive_map
from bybit_workbench.research.mtf_entry import Direction

PERIOD_TAG = "20260518_20260816"
ALL_SYMBOLS = (
    "UNIUSDT",
    "LINKUSDT",
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "1000PEPEUSDT",
    "SOLUSDT",
    "DOGEUSDT",
    "ADAUSDT",
)
DEV_SYMBOLS = ("UNIUSDT", "LINKUSDT")
HOLDOUT_SYMBOLS = tuple(symbol for symbol in ALL_SYMBOLS if symbol not in DEV_SYMBOLS)
EXPECTED_COUNTS = {
    "UNIUSDT": 113,
    "LINKUSDT": 114,
    "BTCUSDT": 119,
    "ETHUSDT": 130,
    "XRPUSDT": 125,
    "1000PEPEUSDT": 117,
    "SOLUSDT": 91,
    "DOGEUSDT": 143,
    "ADAUSDT": 111,
}
EXPECTED_ALL9 = 1063

Outcome = Literal["reached_plus_1p10", "hit_minus_1p00", "data_end"]


@dataclass(frozen=True, slots=True)
class Config:
    target_pct: float = 1.10
    stop_pct: float = 1.00
    horizon_hours: int = 72
    day_cache_size: int = 6
    progress_interval_seconds: float = 20.0
    margin_usd: float = 100.0
    leverage: float = 10.0
    illustrative_round_trip_cost_pct: float = 0.10

    def __post_init__(self) -> None:
        if self.target_pct <= 0 or self.stop_pct <= 0:
            raise ValueError("target_pct and stop_pct must be positive")
        if self.horizon_hours <= 0 or self.day_cache_size <= 0:
            raise ValueError("horizon_hours and day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")
        if self.margin_usd <= 0 or self.leverage <= 0:
            raise ValueError("margin_usd and leverage must be positive")
        if self.illustrative_round_trip_cost_pct < 0:
            raise ValueError("illustrative_round_trip_cost_pct cannot be negative")


@dataclass(frozen=True, slots=True)
class Result:
    symbol: str
    direction: Direction
    touch_at: datetime
    entry_price: float
    outcome: Outcome
    event_at: datetime | None
    event_move_pct: float | None
    seconds_to_event: float | None
    complete_horizon: bool


class Progress:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self.last_emit = 0.0

    def emit(self, processed: int, total: int, *, force: bool = False, detail: str = "") -> None:
        now = time.monotonic()
        if not force and now - self.last_emit < self.interval_seconds:
            return
        elapsed = now - self.started
        eta = (
            None
            if processed <= 0 or processed >= total
            else elapsed / processed * (total - processed)
        )
        suffix = f" | {detail}" if detail else ""
        print(
            f"[P47N] processed={processed}/{total} ({100.0 * processed / total:.1f}%) "
            f"elapsed={_duration(elapsed)} ETA={'n/a' if eta is None else _duration(eta)}{suffix}",
            flush=True,
        )
        self.last_emit = now


def _duration(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    hours, rem = divmod(value, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _validation_p40(root: Path, symbol: str) -> Path:
    return root / "reports" / "cross_asset_validation" / f"{symbol}_{PERIOD_TAG}" / "p40"


def discover_sources(root: Path) -> tuple[SignalSource, ...]:
    sources: list[SignalSource] = []
    for symbol in ALL_SYMBOLS:
        source = discover_source(_validation_p40(root, symbol))
        if source.symbol != symbol:
            raise ValueError(f"P40 symbol mismatch: expected {symbol}, got {source.symbol}")
        sources.append(source)
    return tuple(sources)


def load_frozen_signals(sources: tuple[SignalSource, ...]) -> tuple[CoreSignal, ...]:
    signals: list[CoreSignal] = []
    counts: dict[str, int] = {}
    for source in sources:
        current = load_core_signals(source)
        counts[source.symbol] = len(current)
        signals.extend(current)
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"frozen P40 guardrail failed: counts={counts}")
    if len(signals) != EXPECTED_ALL9:
        raise ValueError(f"frozen signal total {len(signals)} != {EXPECTED_ALL9}")
    unique = {(signal.symbol, signal.touch_at) for signal in signals}
    if len(unique) != len(signals):
        raise ValueError("duplicate frozen Entry keys detected")
    return tuple(sorted(signals, key=lambda signal: (signal.symbol, signal.touch_at)))


def _days(start: datetime, hours: int) -> tuple[str, ...]:
    end = start + timedelta(hours=hours)
    current = start.date()
    last = (end - timedelta(microseconds=1)).date()
    values: list[str] = []
    while current <= last:
        values.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(values)


def scan_signal(
    signal: CoreSignal,
    archives: dict[str, Path],
    cache: TradeDayCache,
    config: Config,
) -> Result:
    start_ts = signal.touch_at.timestamp()
    horizon_end = signal.touch_at + timedelta(hours=config.horizon_hours)
    end_ts = horizon_end.timestamp()
    available_days = sorted(archives)
    max_day = available_days[-1] if available_days else ""
    observed_trade = False

    for day in _days(signal.touch_at, config.horizon_hours):
        archive = archives.get(day)
        if archive is None:
            if day <= max_day:
                raise FileNotFoundError(f"internal trade-day gap {signal.symbol} {day}")
            break
        tape = cache.get(archive)
        left = bisect.bisect_left(tape.timestamps, start_ts)
        right = bisect.bisect_right(tape.timestamps, end_ts)
        for index in range(left, right):
            observed_trade = True
            timestamp = tape.timestamps[index]
            move = directional_move_pct(
                signal.direction,
                signal.entry_price,
                tape.prices[index],
            )
            if move >= config.target_pct:
                event_at = datetime.fromtimestamp(timestamp, UTC)
                return Result(
                    symbol=signal.symbol,
                    direction=signal.direction,
                    touch_at=signal.touch_at,
                    entry_price=signal.entry_price,
                    outcome="reached_plus_1p10",
                    event_at=event_at,
                    event_move_pct=move,
                    seconds_to_event=max(0.0, timestamp - start_ts),
                    complete_horizon=True,
                )
            if move <= -config.stop_pct:
                event_at = datetime.fromtimestamp(timestamp, UTC)
                return Result(
                    symbol=signal.symbol,
                    direction=signal.direction,
                    touch_at=signal.touch_at,
                    entry_price=signal.entry_price,
                    outcome="hit_minus_1p00",
                    event_at=event_at,
                    event_move_pct=move,
                    seconds_to_event=max(0.0, timestamp - start_ts),
                    complete_horizon=True,
                )

    if not observed_trade:
        raise ValueError(
            f"no trade observations at or after Entry for {signal.symbol} {signal.touch_at}"
        )
    return Result(
        symbol=signal.symbol,
        direction=signal.direction,
        touch_at=signal.touch_at,
        entry_price=signal.entry_price,
        outcome="data_end",
        event_at=None,
        event_move_pct=None,
        seconds_to_event=None,
        complete_horizon=False,
    )


def _scope_symbols(scope: str) -> tuple[str, ...]:
    if scope == "ALL9":
        return ALL_SYMBOLS
    if scope == "DEV2":
        return DEV_SYMBOLS
    if scope == "HOLDOUT7":
        return HOLDOUT_SYMBOLS
    if scope in ALL_SYMBOLS:
        return (scope,)
    raise ValueError(f"unknown scope: {scope}")


def summarize(results: list[Result], scope: str, config: Config) -> dict[str, Any]:
    symbols = set(_scope_symbols(scope))
    items = [item for item in results if item.symbol in symbols]
    wins = [item for item in items if item.outcome == "reached_plus_1p10"]
    losses = [item for item in items if item.outcome == "hit_minus_1p00"]
    censored = [item for item in items if item.outcome == "data_end"]
    resolved = len(wins) + len(losses)
    win_delays = [item.seconds_to_event for item in wins if item.seconds_to_event is not None]
    loss_delays = [item.seconds_to_event for item in losses if item.seconds_to_event is not None]

    notional = config.margin_usd * config.leverage
    cost = notional * config.illustrative_round_trip_cost_pct / 100.0
    win_gross = notional * config.target_pct / 100.0
    loss_gross = -notional * config.stop_pct / 100.0
    win_net = win_gross - cost
    loss_net = loss_gross - cost
    aggregate_net = len(wins) * win_net + len(losses) * loss_net

    return {
        "scope": scope,
        "signals": len(items),
        "reached_plus_1p10": len(wins),
        "hit_minus_1p00": len(losses),
        "data_end": len(censored),
        "resolved": resolved,
        "resolved_win_rate_pct": round(100.0 * len(wins) / resolved, 6) if resolved else None,
        "resolved_loss_rate_pct": round(100.0 * len(losses) / resolved, 6) if resolved else None,
        "median_seconds_to_plus_1p10": (
            round(float(statistics.median(win_delays)), 6) if win_delays else None
        ),
        "median_seconds_to_minus_1p00": (
            round(float(statistics.median(loss_delays)), 6) if loss_delays else None
        ),
        "illustrative_margin_usd": config.margin_usd,
        "illustrative_leverage": config.leverage,
        "illustrative_notional_usd": round(notional, 8),
        "illustrative_round_trip_cost_pct": config.illustrative_round_trip_cost_pct,
        "illustrative_win_net_usd": round(win_net, 8),
        "illustrative_loss_net_usd": round(loss_net, 8),
        "illustrative_aggregate_net_usd": round(aggregate_net, 8),
        "illustrative_mean_net_per_resolved_trade_usd": (
            round(aggregate_net / resolved, 8) if resolved else None
        ),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(root: Path, output_dir: Path, config: Config) -> Path:
    sources = discover_sources(root)
    signals = load_frozen_signals(sources)
    source_by_symbol = {source.symbol: source for source in sources}
    archives = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir)
        for symbol in ALL_SYMBOLS
    }
    for symbol, mapping in archives.items():
        if not mapping:
            raise FileNotFoundError(f"no local trade archives for {symbol}")

    caches = {symbol: TradeDayCache(max_days=config.day_cache_size) for symbol in ALL_SYMBOLS}
    progress = Progress(config.progress_interval_seconds)
    results: list[Result] = []
    progress.emit(0, len(signals), force=True, detail="untouched -1.00 first-touch vs +1.10")

    for index, signal in enumerate(signals, start=1):
        item = scan_signal(signal, archives[signal.symbol], caches[signal.symbol], config)
        results.append(item)
        cache = caches[signal.symbol]
        progress.emit(
            index,
            len(signals),
            detail=(
                f"symbol={signal.symbol} outcome={item.outcome} "
                f"cache_hits={cache.hits} cache_misses={cache.misses}"
            ),
        )
    progress.emit(len(signals), len(signals), force=True, detail="done")

    if len(results) != EXPECTED_ALL9:
        raise ValueError(f"result count {len(results)} != {EXPECTED_ALL9}")

    output_dir.mkdir(parents=True, exist_ok=True)
    event_rows: list[dict[str, Any]] = []
    for item in results:
        row = asdict(item)
        row["touch_at"] = item.touch_at.isoformat()
        row["event_at"] = item.event_at.isoformat() if item.event_at is not None else ""
        event_rows.append(row)
    _write_csv(output_dir / "event_results.csv", event_rows)

    scopes = ["ALL9", "DEV2", "HOLDOUT7", *ALL_SYMBOLS]
    summaries = [summarize(results, scope, config) for scope in scopes]
    _write_csv(output_dir / "scope_summary.csv", summaries)
    _write_csv(
        output_dir / "sources.csv",
        [
            {
                "symbol": source.symbol,
                "p40_dir": str(source.p40_dir),
                "features_path": str(source.features_path),
                "dataset_dir": str(source.dataset_dir),
            }
            for source in sources
        ],
    )

    all9 = summaries[0]
    payload = {
        "research": "P47N untouched -1.00 vs +1.10 exact first-touch baseline",
        "created_at": datetime.now(UTC).isoformat(),
        "downloads": "DISABLED / fail-closed",
        "entry_v1": "frozen / unchanged",
        "config": asdict(config),
        "guardrails": {
            "period_tag": PERIOD_TAG,
            "expected_all9": EXPECTED_ALL9,
            "expected_counts": EXPECTED_COUNTS,
        },
        "semantics": (
            "From frozen Entry touch_at, keep the structural -1.00% stop untouched. "
            "Scan local public trades causally and stop at the first +1.10% favorable "
            "touch or -1.00% adverse touch. No +0.10/+0.50 activation, no retest rule, "
            "no trailing rule, and no runner logic are applied."
        ),
        "economics_note": (
            "Illustrative economics use the user-requested $100 margin, 10x leverage, "
            "and a simple 0.10% of notional round-trip cost reserve. This is not an "
            "account fee-rate claim and is not production break-even accounting."
        ),
        "scope_summary": summaries,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    md = [
        "# P47N untouched -1.00 vs +1.10 exact baseline",
        "",
        "Entry V1 frozen. Research only. Downloads disabled.",
        "",
        "Rule:",
        "- Entry at frozen touch_at",
        "- keep structural stop at -1.00% untouched",
        "- first +1.10% favorable touch wins",
        "- first -1.00% adverse touch loses",
        "- 72h horizon, data_end reported separately",
        "",
        "## ALL9",
        f"Signals: **{all9['signals']}**",
        f"Reached +1.10 first: **{all9['reached_plus_1p10']}**",
        f"Hit -1.00 first: **{all9['hit_minus_1p00']}**",
        f"Data end/censored: **{all9['data_end']}**",
        f"Resolved win rate: **{all9['resolved_win_rate_pct']}%**",
        f"Resolved loss rate: **{all9['resolved_loss_rate_pct']}%**",
        "",
        "## Illustrative $100 margin / 10x economics",
        f"Notional: **${all9['illustrative_notional_usd']:.2f}**",
        f"Winner net at +1.10 with 0.10% cost reserve: **${all9['illustrative_win_net_usd']:.2f}**",
        f"Loser net at -1.00 with 0.10% cost reserve: **${all9['illustrative_loss_net_usd']:.2f}**",
        f"Aggregate net across resolved signals: **${all9['illustrative_aggregate_net_usd']:.2f}**",
        (
            "Mean net per resolved signal: "
            f"**${all9['illustrative_mean_net_per_resolved_trade_usd']:.4f}**"
        ),
        "",
        (
            "This economics block is illustrative only; exact Bybit fees/slippage/funding "
            "are not used."
        ),
    ]
    (output_dir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(
        "P47N ALL9: "
        f"signals={all9['signals']} "
        f"plus1p10={all9['reached_plus_1p10']} "
        f"minus1={all9['hit_minus_1p00']} "
        f"data_end={all9['data_end']} "
        f"win_rate={all9['resolved_win_rate_pct']} "
        f"loss_rate={all9['resolved_loss_rate_pct']} "
        f"net_usd={all9['illustrative_aggregate_net_usd']}",
        flush=True,
    )
    print(f"Report: {output_dir}", flush=True)
    return output_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P47N exact first-touch: untouched -1.00 stop versus +1.10 target."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--progress-interval-seconds", type=float, default=20.0)
    parser.add_argument("--margin-usd", type=float, default=100.0)
    parser.add_argument("--leverage", type=float, default=10.0)
    parser.add_argument("--illustrative-round-trip-cost-pct", type=float, default=0.10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config(
        progress_interval_seconds=args.progress_interval_seconds,
        margin_usd=args.margin_usd,
        leverage=args.leverage,
        illustrative_round_trip_cost_pct=args.illustrative_round_trip_cost_pct,
    )
    run(args.project_root.resolve(), args.output_dir.resolve(), config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
