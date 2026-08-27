from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from bybit_workbench.research.core_runner_split_v16 import (
    SplitConfig,
    SplitPolicyResult,
    SplitPolicySpec,
    simulate_split_policy,
)
from bybit_workbench.research.exit_break_even_v13 import (
    SignalSource,
    TradeDayCache,
    build_path_series,
    discover_source,
    load_core_signals,
)
from bybit_workbench.research.flow_reversal_v1 import _archive_map
from bybit_workbench.research.hourly_trend_correlation_v17 import (
    HourlyTrendFeature,
    PolicyOutcome,
    aggregate_hourly_candles,
    build_feature,
    enrich_ema,
)
from bybit_workbench.research.hourly_trend_correlation_v17 import (
    ProgressReporter as HourlyProgressReporter,
)

HOLDOUT_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "1000PEPEUSDT",
    "SOLUSDT",
    "DOGEUSDT",
    "ADAUSDT",
)
PERIOD_TAG = "20260518_20260816"
FROZEN_POLICY_ID = "CORE050_RUN050_BE_MFE_GB4.00"


class ProgressReporter(HourlyProgressReporter):
    def emit(
        self,
        stage: str,
        *,
        processed: int,
        total: int,
        force: bool = False,
        detail: str = "",
    ) -> None:
        now = time.monotonic()
        if not force and now - self.last_emit < self.interval_seconds:
            return
        elapsed = max(0.0, now - self.started)
        eta = None
        if processed > 0 and total > processed:
            eta = elapsed / processed * (total - processed)
        eta_text = "n/a" if eta is None else _format_duration(eta)
        suffix = f" | {detail}" if detail else ""
        print(
            f"[P47E] stage={stage} processed={processed}/{total} "
            f"elapsed={_format_duration(elapsed)} ETA={eta_text}{suffix}",
            flush=True,
        )
        self.last_emit = now


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def frozen_config() -> SplitConfig:
    return SplitConfig(
        initial_stop_pct=1.0,
        early_activation_pct=0.10,
        early_floor_pct=0.0,
        split_activation_pct=1.10,
        core_exit_pct=1.00,
        horizon_hours=72,
        core_fractions=(1.0, 0.5),
        mfe_giveback_pct=(4.0,),
        target_levels_pct=(1.5, 2.0, 3.0, 5.0, 10.0),
        day_cache_size=6,
        progress_interval_seconds=25.0,
    )


def frozen_policy() -> SplitPolicySpec:
    return SplitPolicySpec(
        policy_id=FROZEN_POLICY_ID,
        family="mfe",
        core_fraction=0.5,
        floor_mode="be",
        giveback_pct=4.0,
    )


def validation_p40(root: Path, symbol: str, *, period_tag: str = PERIOD_TAG) -> Path:
    return root / "reports" / "cross_asset_validation" / f"{symbol}_{period_tag}" / "p40"


def discover_holdout_sources(
    root: Path,
    symbols: tuple[str, ...],
    *,
    period_tag: str = PERIOD_TAG,
) -> tuple[SignalSource, ...]:
    sources: list[SignalSource] = []
    for symbol in symbols:
        p40 = validation_p40(root, symbol, period_tag=period_tag)
        source = discover_source(p40)
        if source.symbol != symbol:
            raise ValueError(
                f"P40 symbol mismatch for {symbol}: summary/features report {source.symbol}"
            )
        sources.append(source)
    return tuple(sources)


def _policy_outcome(result: SplitPolicyResult) -> PolicyOutcome:
    return PolicyOutcome(
        symbol=result.symbol,
        touch_at=result.touch_at,
        exit_reason=result.exit_reason,
        exit_move_pct=result.exit_move_pct,
        split_activated=result.split_activated,
        core_component_pct=result.core_component_pct,
        runner_component_pct=result.runner_component_pct,
        max_favorable_pct=result.max_favorable_pct,
    )


def _pct(part: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(part * 100.0 / total, 6)


def _sum_pct(items: list[HourlyTrendFeature]) -> float:
    return round(sum(item.exit_move_pct for item in items), 6)


def summarise_features(
    features: tuple[HourlyTrendFeature, ...],
    *,
    scope: str,
) -> dict[str, Any]:
    initial_stops = [item for item in features if item.exit_reason == "initial_stop"]
    split = [item for item in features if item.split_activated]
    runners = [item for item in features if item.runner_added]
    strict_with = [item for item in features if item.strict_relation == "with"]
    strict_against = [item for item in features if item.strict_relation == "against"]
    strict_mixed = [item for item in features if item.strict_relation == "mixed"]

    def bucket(items: list[HourlyTrendFeature]) -> dict[str, Any]:
        runner_count = sum(item.runner_added for item in items)
        success_count = sum(item.split_activated for item in items)
        stop_count = sum(item.exit_reason == "initial_stop" for item in items)
        return {
            "signals": len(items),
            "split_success": success_count,
            "split_success_rate_pct": _pct(success_count, len(items)),
            "runner_added": runner_count,
            "runner_added_rate_pct": _pct(runner_count, len(items)),
            "initial_stop": stop_count,
            "initial_stop_rate_pct": _pct(stop_count, len(items)),
            "gross_selected_policy_pct": _sum_pct(items),
        }

    against_runners = sum(item.strict_relation == "against" for item in runners)
    with_runners = sum(item.strict_relation == "with" for item in runners)
    mixed_runners = sum(item.strict_relation == "mixed" for item in runners)
    non_against = strict_with + strict_mixed
    against_rate = _pct(against_runners, len(strict_against))
    non_against_runner_count = with_runners + mixed_runners
    non_against_rate = _pct(non_against_runner_count, len(non_against))

    return {
        "scope": scope,
        "signals": len(features),
        "initial_stop": len(initial_stops),
        "split_success": len(split),
        "runner_added": len(runners),
        "gross_selected_policy_pct": _sum_pct(list(features)),
        "runner_relation_counts": {
            "with": with_runners,
            "against": against_runners,
            "mixed": mixed_runners,
        },
        "runner_against_share_pct": _pct(against_runners, len(runners)),
        "strict_buckets": {
            "with": bucket(strict_with),
            "against": bucket(strict_against),
            "mixed": bucket(strict_mixed),
        },
        "h1_primary_check": {
            "has_runner_observations": bool(runners),
            "runner_majority_strict_against": (
                against_runners > with_runners + mixed_runners if runners else None
            ),
            "runner_rate_strict_against_pct": against_rate,
            "runner_rate_non_against_pct": non_against_rate,
            "runner_rate_lift_against_minus_non_against_pct_points": (
                round(against_rate - non_against_rate, 6)
                if against_rate is not None and non_against_rate is not None
                else None
            ),
        },
    }


def _feature_row(feature: HourlyTrendFeature) -> dict[str, Any]:
    row = asdict(feature)
    row["touch_at"] = feature.touch_at.isoformat()
    row["last_closed_hour_start"] = feature.last_closed_hour_start.isoformat()
    return row


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    pooled = cast(dict[str, Any], summary["holdout_pooled"])
    lines = [
        "# P47E — Frozen 1H Trend Hypothesis Out-of-Sample Validation",
        "",
        "UNI/LINK are development assets and are not used in this run.",
        "",
        "## Frozen before holdout",
        "",
        "- Entry V1 unchanged.",
        "- Early protection: +0.10% -> BE.",
        "- Split activation: +1.10%.",
        "- Core/runner: 50/50; core locks +1.00%.",
        "- Runner floor: BE; MFE giveback: 4.00%.",
        "- 1H strict trend: structure + EMA20 position + EMA20 slope.",
        "- Only fully closed 1H candles are used.",
        "- H1: runner-added trades predominantly occur against strict closed 1H trend.",
        "",
        "## Holdout pooled result",
        "",
        f"Signals: **{pooled['signals']}**",
        f"Initial -1% stops: **{pooled['initial_stop']}**",
        f"Reached +1.10% split: **{pooled['split_success']}**",
        f"Runner-added: **{pooled['runner_added']}**",
        f"Gross selected-policy sum: **{pooled['gross_selected_policy_pct']:.4f}%**",
        "",
        "### Runner relation to strict 1H trend",
        "",
        "| Relation | Runner-added count |",
        "|---|---:|",
        f"| with | {pooled['runner_relation_counts']['with']} |",
        f"| against | {pooled['runner_relation_counts']['against']} |",
        f"| mixed | {pooled['runner_relation_counts']['mixed']} |",
        "",
        (
            "Runner against-share: **"
            f"{pooled['runner_against_share_pct']}%**"
            if pooled["runner_against_share_pct"] is not None
            else "Runner against-share: **n/a (no holdout runners)**"
        ),
        "",
        "## Per-asset",
        "",
        "| Symbol | Signals | -1% stop | +1.10 success | Runner-added | Runner against | Gross % |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary["assets"]:
        runner_against = item["runner_relation_counts"]["against"]
        lines.append(
            f"| {item['scope']} | {item['signals']} | {item['initial_stop']} | "
            f"{item['split_success']} | {item['runner_added']} | {runner_against} | "
            f"{item['gross_selected_policy_pct']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrail",
            "",
            "This is a frozen holdout test. No parameter is selected from holdout outcomes.",
            "If H1 fails, do not retune the 1H definition on these same assets.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_research(
    root: Path,
    *,
    output_dir: Path,
    symbols: tuple[str, ...] = HOLDOUT_SYMBOLS,
    period_tag: str = PERIOD_TAG,
    ema_period: int = 20,
    progress_interval_seconds: float = 25.0,
) -> dict[str, Any]:
    root = root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = discover_holdout_sources(root, symbols, period_tag=period_tag)
    config = frozen_config()
    spec = frozen_policy()

    all_features: list[HourlyTrendFeature] = []
    per_asset: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []

    for asset_index, source in enumerate(sources, start=1):
        signals = load_core_signals(source)
        if not signals:
            raise ValueError(f"no frozen core signals for {source.symbol}")
        archive_by_day = _archive_map(source.dataset_dir)
        reporter = ProgressReporter(progress_interval_seconds)
        print(
            f"[P47E] asset={asset_index}/{len(sources)} symbol={source.symbol} "
            f"signals={len(signals)} archives={len(archive_by_day)}",
            flush=True,
        )
        candles = aggregate_hourly_candles(
            archive_by_day,
            progress=reporter,
            symbol=source.symbol,
        )
        hours = enrich_ema(candles, period=ema_period)
        cache = TradeDayCache(max_days=config.day_cache_size)
        asset_features: list[HourlyTrendFeature] = []
        reporter.emit(
            "frozen-policy+1h",
            processed=0,
            total=len(signals),
            force=True,
            detail=f"symbol={source.symbol}",
        )
        for index, signal in enumerate(signals, start=1):
            path = build_path_series(
                signal,
                archive_by_day,
                horizon_hours=config.horizon_hours,
                cache=cache,
            )
            result = simulate_split_policy(path, spec, config)
            feature = build_feature(signal, _policy_outcome(result), hours)
            asset_features.append(feature)
            reporter.emit(
                "frozen-policy+1h",
                processed=index,
                total=len(signals),
                detail=(
                    f"symbol={source.symbol} cache_hits={cache.hits} "
                    f"cache_misses={cache.misses}"
                ),
            )
        feature_tuple = tuple(asset_features)
        asset_summary = summarise_features(feature_tuple, scope=source.symbol)
        per_asset.append(asset_summary)
        all_features.extend(asset_features)
        source_rows.append(
            {
                "symbol": source.symbol,
                "p40_dir": str(source.p40_dir),
                "dataset_dir": str(source.dataset_dir),
                "signals": len(signals),
                "public_trade_archives": len(archive_by_day),
            }
        )
        reporter.emit(
            "asset-done",
            processed=len(signals),
            total=len(signals),
            force=True,
            detail=(
                f"symbol={source.symbol} runners={asset_summary['runner_added']} "
                f"against={asset_summary['runner_relation_counts']['against']}"
            ),
        )

    pooled = summarise_features(tuple(all_features), scope="HOLDOUT_POOLED")
    summary = {
        "protocol": "P47E frozen H1 out-of-sample validation; no holdout retuning",
        "development_assets_excluded": ["UNIUSDT", "LINKUSDT"],
        "holdout_symbols": list(symbols),
        "period_tag": period_tag,
        "selected_policy_id": spec.policy_id,
        "selected_policy": {
            "initial_stop_pct": config.initial_stop_pct,
            "early_activation_pct": config.early_activation_pct,
            "early_floor_pct": config.early_floor_pct,
            "split_activation_pct": config.split_activation_pct,
            "core_exit_pct": config.core_exit_pct,
            "core_fraction": spec.core_fraction,
            "runner_fraction": spec.runner_fraction,
            "runner_floor_mode": spec.floor_mode,
            "mfe_giveback_pct": spec.giveback_pct,
            "horizon_hours": config.horizon_hours,
        },
        "hourly_definition": {
            "ema_period": ema_period,
            "closed_hours_only": True,
            "ohlc_source": "raw public trades",
            "strict_trend": "structure + EMA20 position + EMA20 slope",
        },
        "frozen_hypothesis": (
            "H1: runner-added trades predominantly occur against strict closed 1H trend"
        ),
        "development_reference": {
            "signals": 227,
            "runner_added": 7,
            "runner_strict_against": 7,
            "runner_strict_with": 0,
            "runner_strict_mixed": 0,
        },
        "sources": source_rows,
        "assets": per_asset,
        "holdout_pooled": pooled,
        "guardrails": [
            "No Entry V1 parameter is changed by P47E.",
            "The selected P47C exit policy is frozen before holdout inspection.",
            "The P47D strict 1H definition is frozen before holdout inspection.",
            "UNI/LINK are excluded from holdout statistics.",
            "No holdout result may be used to retune this same H1 validation pass.",
        ],
    }

    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(output_dir / "summary.md", summary)
    _write_csv(output_dir / "hourly_features.csv", [_feature_row(x) for x in all_features])
    _write_csv(output_dir / "asset_summary.csv", per_asset)
    runner_rows = [_feature_row(item) for item in all_features if item.runner_added]
    _write_csv(output_dir / "runner_events.csv", runner_rows)
    _write_csv(output_dir / "sources.csv", source_rows)
    return summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P47E frozen 1H trend H1 out-of-sample validation",
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--period-tag", default=PERIOD_TAG)
    parser.add_argument("--symbols", default=",".join(HOLDOUT_SYMBOLS))
    parser.add_argument("--ema-period", type=int, default=20)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.root.resolve()
    symbols = tuple(item.strip() for item in args.symbols.split(",") if item.strip())
    if symbols != HOLDOUT_SYMBOLS:
        raise ValueError(
            "P47E is frozen to the seven holdout symbols; do not alter --symbols for this pass"
        )
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output_dir = root / "reports" / "hourly_trend_oos_v1" / f"HOLDOUT7_{stamp}"
    summary = run_research(
        root,
        output_dir=output_dir,
        symbols=cast(tuple[str, ...], symbols),
        period_tag=args.period_tag,
        ema_period=args.ema_period,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    pooled = summary["holdout_pooled"]
    print(f"P47E holdout signals: {pooled['signals']}")
    print(f"P47E runner-added: {pooled['runner_added']}")
    print(
        "P47E runner strict against/with/mixed: "
        f"{pooled['runner_relation_counts']['against']}/"
        f"{pooled['runner_relation_counts']['with']}/"
        f"{pooled['runner_relation_counts']['mixed']}"
    )
    print(f"Report: {output_dir / 'summary.json'}")
    print(f"Readable summary: {output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
