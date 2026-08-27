from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import zipfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from bybit_workbench.research.clean_zone_lifecycle_p451 import (
    CleanZoneLifecycleDetector,
    CoreLifecycleFeature,
    build_core_feature,
)
from bybit_workbench.research.market_regime_full_panel_v2 import (
    CoreSignal as RegimeCoreSignal,
)
from bybit_workbench.research.market_regime_full_panel_v2 import (
    build_regime_row,
    load_price_series,
    parse_datetime,
)
from bybit_workbench.research.multi_touch_sr_p45 import (
    CoreSignal as ZoneCoreSignal,
)
from bybit_workbench.research.multi_touch_sr_p45 import (
    load_candles,
)

Direction = Literal["Long", "Short"]
Outcome = Literal["favorable_first", "adverse_first", "neither"]
CandidateDirection = Literal["positive", "negative"]

DEFAULT_SYMBOLS: tuple[str, ...] = (
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

DISCOVERY_START = datetime(2026, 5, 18, tzinfo=UTC)
DISCOVERY_END = datetime(2026, 8, 16, tzinfo=UTC)
WARMUP_START = datetime(2026, 8, 12, tzinfo=UTC)
HOLDOUT_START = datetime(2026, 8, 19, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 9, 18, tzinfo=UTC)
LATEST_TRADE_DAY = "2026-09-17"
DATA_DAYS = 37
PERIOD_TAG = "20260812_20260918"
HOLDOUT_TAG = "20260819_20260918"

P44_FEATURE = "directional_alt_btc_residual_15m_pct"
P451_FEATURE = "approach_slope_atr_per_bar"
NEAR_ZONE_ATR = 0.50


@dataclass(frozen=True, slots=True)
class FrozenThreshold:
    symbol: str
    p44_residual_q25: float | None
    p451_approach_q25: float


@dataclass(frozen=True, slots=True)
class SignalRecord:
    symbol: str
    direction: Direction
    touch_at: datetime
    entry_price: float
    outcome_05: Outcome
    outcome_10: Outcome
    accepted_after_failure_embargo: bool
    flow_state: str
    oi_tail_danger: bool

    @property
    def is_core(self) -> bool:
        return (
            self.accepted_after_failure_embargo
            and self.flow_state == "pressure_then_reversal"
            and not self.oi_tail_danger
        )


@dataclass(frozen=True, slots=True)
class OutcomeMetrics:
    sample: int
    favorable_05: int
    adverse_05: int
    neither_05: int
    win_05_all_pct: float | None
    decisive_05_pct: float | None
    favorable_10: int
    adverse_10: int
    neither_10: int
    win_10_all_pct: float | None
    decisive_10_pct: float | None


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    name: str
    direction: CandidateDirection
    eligible_assets: int
    minimum_total_sample: int
    minimum_assets_with_sample: int
    minimum_directional_assets: int
    baseline: str


CANDIDATES: tuple[CandidateSpec, ...] = (
    CandidateSpec(
        name="cooldown_60m",
        direction="positive",
        eligible_assets=9,
        minimum_total_sample=200,
        minimum_assets_with_sample=9,
        minimum_directional_assets=8,
        baseline="all_exact_touch",
    ),
    CandidateSpec(
        name="p44_residual_q1",
        direction="positive",
        eligible_assets=8,
        minimum_total_sample=50,
        minimum_assets_with_sample=7,
        minimum_directional_assets=7,
        baseline="all_core_non_btc",
    ),
    CandidateSpec(
        name="zone_approach_slope_q1",
        direction="positive",
        eligible_assets=9,
        minimum_total_sample=50,
        minimum_assets_with_sample=8,
        minimum_directional_assets=8,
        baseline="near_zone_core_0_50atr",
    ),
    CandidateSpec(
        name="zone_second_retest",
        direction="positive",
        eligible_assets=9,
        minimum_total_sample=30,
        minimum_assets_with_sample=6,
        minimum_directional_assets=6,
        baseline="all_core",
    ),
    CandidateSpec(
        name="zone_fourth_plus_retest",
        direction="negative",
        eligible_assets=9,
        minimum_total_sample=25,
        minimum_assets_with_sample=6,
        minimum_directional_assets=6,
        baseline="all_core",
    ),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _csv_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fields})


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no", ""}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _parse_direction(value: str) -> Direction:
    if value not in {"Long", "Short"}:
        raise ValueError(f"unsupported direction: {value!r}")
    return cast(Direction, value)


def _parse_outcome(value: str) -> Outcome:
    if value not in {"favorable_first", "adverse_first", "neither"}:
        raise ValueError(f"unsupported outcome: {value!r}")
    return cast(Outcome, value)


def _float_or_none(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


def _load_threshold_column(
    path: Path,
    *,
    feature: str,
) -> dict[str, float]:
    result: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"symbol", "feature", "q25"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"unexpected threshold file columns: {path}")
        for raw in reader:
            if raw["feature"] != feature:
                continue
            result[raw["symbol"]] = float(raw["q25"])
    return result


def _source_paths(root: Path) -> tuple[Path, Path]:
    p44 = (
        root
        / "reports"
        / "market_regime_p44_full_panel"
        / "ENTRY_V1_20260518_20260816"
        / "calibration_thresholds.csv"
    )
    p451 = (
        root
        / "reports"
        / "clean_zone_lifecycle_p451"
        / "ENTRY_V1_20260518_20260816"
        / "s1_feature_thresholds.csv"
    )
    return p44, p451


def freeze_dir(root: Path) -> Path:
    return root / "reports" / "confirmatory_holdout_p46" / f"FROZEN_{HOLDOUT_TAG}"


def result_dir(root: Path) -> Path:
    return root / "reports" / "confirmatory_holdout_p46" / f"HOLDOUT_{HOLDOUT_TAG}"


def asset_root(root: Path, symbol: str) -> Path:
    return root / "reports" / "cross_asset_validation" / f"{symbol}_{PERIOD_TAG}"


def _freeze_payload(root: Path) -> tuple[dict[str, Any], list[FrozenThreshold]]:
    p44_path, p451_path = _source_paths(root)
    if not p44_path.is_file():
        raise FileNotFoundError(f"P44 frozen thresholds not found: {p44_path}")
    if not p451_path.is_file():
        raise FileNotFoundError(f"P45.1 frozen thresholds not found: {p451_path}")

    residual = _load_threshold_column(p44_path, feature=P44_FEATURE)
    approach = _load_threshold_column(p451_path, feature=P451_FEATURE)
    thresholds: list[FrozenThreshold] = []
    for symbol in DEFAULT_SYMBOLS:
        if symbol not in approach:
            raise ValueError(f"missing P45.1 {P451_FEATURE} q25 for {symbol}")
        residual_value = None if symbol == "BTCUSDT" else residual.get(symbol)
        if symbol != "BTCUSDT" and residual_value is None:
            raise ValueError(f"missing P44 {P44_FEATURE} q25 for {symbol}")
        thresholds.append(
            FrozenThreshold(
                symbol=symbol,
                p44_residual_q25=residual_value,
                p451_approach_q25=approach[symbol],
            )
        )

    payload: dict[str, Any] = {
        "architecture": "p46_confirmatory_holdout_v1",
        "discovery_interval": {
            "start": DISCOVERY_START.isoformat(),
            "end": DISCOVERY_END.isoformat(),
        },
        "warmup_interval": {
            "start": WARMUP_START.isoformat(),
            "end": HOLDOUT_START.isoformat(),
            "purpose": "causal zone/ATR/regime history only; outcomes are excluded",
        },
        "holdout_interval": {
            "start": HOLDOUT_START.isoformat(),
            "end": HOLDOUT_END.isoformat(),
            "latest_complete_trade_day": LATEST_TRADE_DAY,
            "days": 30,
        },
        "data_preparation": {
            "dataset_days": DATA_DAYS,
            "period_tag": PERIOD_TAG,
            "orderbook_required": False,
            "required_stages": ["P30", "P31", "P33", "P34", "P35", "P36"],
        },
        "primary_outcome": "+0.5 before -1.0, favorable / all signals",
        "secondary_outcome": "+1.0 before -1.0, favorable / all signals and decisive",
        "candidate_specs": [asdict(item) for item in CANDIDATES],
        "candidate_semantics": {
            "cooldown_60m": (
                "P34 accepted_after_failure_embargo versus all exact-touch P34 signals"
            ),
            "p44_residual_q1": (
                "non-BTC Core only; directional_alt_btc_residual_15m_pct <= frozen P44 S1 q25"
            ),
            "zone_approach_slope_q1": (
                "near aligned zone <=0.50 ATR; approach_slope_atr_per_bar <= frozen P45.1 S1 q25"
            ),
            "zone_second_retest": (
                "near aligned zone <=0.50 ATR and clean-lifecycle current_test_ordinal == 2"
            ),
            "zone_fourth_plus_retest": (
                "near aligned zone <=0.50 ATR and clean-lifecycle current_test_ordinal >= 4; "
                "expected direction is negative"
            ),
        },
        "guardrails": [
            "Holdout starts after protocol freeze; no 2026-08-16..18 outcomes are evaluated.",
            "No threshold may be recalibrated from holdout data.",
            "P44 and P45.1 q25 values are copied verbatim from discovery artifacts.",
            "No new candidate, interaction, score weight, or cutoff may be promoted from P46.",
            "Sparse candidates may return UNDERPOWERED; that is not permission to retune.",
            "Orderbook P39/P40 is intentionally not required for P46 Core reconstruction.",
            "No live trading, Exit, Risk, leverage, or execution logic is modified.",
        ],
        "sources": {
            "p44_thresholds": str(p44_path),
            "p44_sha256": _sha256_file(p44_path),
            "p451_thresholds": str(p451_path),
            "p451_sha256": _sha256_file(p451_path),
        },
        "thresholds": [asdict(item) for item in thresholds],
    }
    return payload, thresholds


def freeze_protocol(root: Path) -> Path:
    output = freeze_dir(root)
    protocol_path = output / "FROZEN_PROTOCOL.json"
    lock_path = output / "FREEZE_LOCK.sha256"
    payload, thresholds = _freeze_payload(root)
    fingerprint = _canonical_hash(payload)

    if protocol_path.is_file() or lock_path.is_file():
        if not protocol_path.is_file() or not lock_path.is_file():
            raise RuntimeError(f"incomplete existing P46 freeze directory: {output}")
        existing = cast(
            dict[str, Any],
            json.loads(protocol_path.read_text(encoding="utf-8")),
        )
        existing.pop("frozen_at", None)
        existing_fingerprint = _canonical_hash(existing)
        locked = lock_path.read_text(encoding="ascii").strip()
        if existing_fingerprint != fingerprint or locked != fingerprint:
            raise RuntimeError(
                "P46 freeze already exists but does not match current source thresholds; "
                "do not overwrite a preregistered holdout"
            )
        print(f"P46 FREEZE VERIFIED: {output}")
        return output

    output.mkdir(parents=True, exist_ok=False)
    payload_with_time = dict(payload)
    payload_with_time["frozen_at"] = datetime.now(UTC).isoformat()
    _write_json(protocol_path, payload_with_time)
    _write_csv(output / "frozen_thresholds.csv", [asdict(item) for item in thresholds])
    lock_path.write_text(fingerprint + "\n", encoding="ascii")
    print(f"P46 FROZEN: {output}")
    print(f"Holdout: {HOLDOUT_START.isoformat()} -> {HOLDOUT_END.isoformat()}")
    print(f"Freeze fingerprint: {fingerprint}")
    return output


def _load_frozen(root: Path) -> tuple[dict[str, Any], dict[str, FrozenThreshold]]:
    output = freeze_dir(root)
    protocol_path = output / "FROZEN_PROTOCOL.json"
    lock_path = output / "FREEZE_LOCK.sha256"
    if not protocol_path.is_file() or not lock_path.is_file():
        raise FileNotFoundError(
            "P46 is not preregistered. Run freeze_p46_confirmatory_holdout_windows.ps1 first."
        )
    payload = cast(dict[str, Any], json.loads(protocol_path.read_text(encoding="utf-8")))
    canonical = dict(payload)
    canonical.pop("frozen_at", None)
    fingerprint = _canonical_hash(canonical)
    locked = lock_path.read_text(encoding="ascii").strip()
    if fingerprint != locked:
        raise RuntimeError("P46 freeze lock mismatch; refuse holdout evaluation")
    raw_thresholds = cast(list[dict[str, Any]], payload["thresholds"])
    thresholds: dict[str, FrozenThreshold] = {}
    for raw in raw_thresholds:
        thresholds[str(raw["symbol"])] = FrozenThreshold(
            symbol=str(raw["symbol"]),
            p44_residual_q25=(
                None
                if raw.get("p44_residual_q25") is None
                else float(raw["p44_residual_q25"])
            ),
            p451_approach_q25=float(raw["p451_approach_q25"]),
        )
    return payload, thresholds


def _load_signal_records(path: Path, *, symbol: str) -> list[SignalRecord]:
    if not path.is_file():
        raise FileNotFoundError(path)
    result: list[SignalRecord] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {
            "symbol",
            "direction",
            "touch_at",
            "entry_price",
            "first_0_5_vs_1_0",
            "first_1_0_vs_1_0",
            "accepted_after_failure_embargo",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"unexpected signal columns: {path}")
        for raw in reader:
            if raw["symbol"] != symbol:
                continue
            touch_at = parse_datetime(raw["touch_at"])
            if not (HOLDOUT_START <= touch_at < HOLDOUT_END):
                continue
            result.append(
                SignalRecord(
                    symbol=symbol,
                    direction=_parse_direction(raw["direction"]),
                    touch_at=touch_at,
                    entry_price=float(raw["entry_price"]),
                    outcome_05=_parse_outcome(raw["first_0_5_vs_1_0"]),
                    outcome_10=_parse_outcome(raw["first_1_0_vs_1_0"]),
                    accepted_after_failure_embargo=_parse_bool(
                        raw["accepted_after_failure_embargo"]
                    ),
                    flow_state=raw.get("flow_state", ""),
                    oi_tail_danger=_parse_bool(raw.get("oi_tail_danger", "false")),
                )
            )
    result.sort(key=lambda item: item.touch_at)
    return result


def _metrics_from_outcomes(
    rows: Sequence[SignalRecord] | Sequence[CoreLifecycleFeature],
) -> OutcomeMetrics:
    favorable05 = sum(item.outcome_05 == "favorable_first" for item in rows)
    adverse05 = sum(item.outcome_05 == "adverse_first" for item in rows)
    favorable10 = sum(item.outcome_10 == "favorable_first" for item in rows)
    adverse10 = sum(item.outcome_10 == "adverse_first" for item in rows)
    count = len(rows)
    decisive05 = favorable05 + adverse05
    decisive10 = favorable10 + adverse10
    return OutcomeMetrics(
        sample=count,
        favorable_05=favorable05,
        adverse_05=adverse05,
        neither_05=count - favorable05 - adverse05,
        win_05_all_pct=None if count == 0 else 100.0 * favorable05 / count,
        decisive_05_pct=None if decisive05 == 0 else 100.0 * favorable05 / decisive05,
        favorable_10=favorable10,
        adverse_10=adverse10,
        neither_10=count - favorable10 - adverse10,
        win_10_all_pct=None if count == 0 else 100.0 * favorable10 / count,
        decisive_10_pct=None if decisive10 == 0 else 100.0 * favorable10 / decisive10,
    )


def _uplift(selected: OutcomeMetrics, baseline: OutcomeMetrics) -> float | None:
    if selected.win_05_all_pct is None or baseline.win_05_all_pct is None:
        return None
    return selected.win_05_all_pct - baseline.win_05_all_pct


def _near(row: CoreLifecycleFeature) -> bool:
    return (
        row.phase_found
        and row.entry_distance_atr is not None
        and row.entry_distance_atr <= NEAR_ZONE_ATR
    )


def _candidate_row(
    *,
    symbol: str,
    candidate: str,
    baseline: OutcomeMetrics,
    selected: OutcomeMetrics,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "candidate": candidate,
        "baseline_sample": baseline.sample,
        "baseline_win_05_all_pct": baseline.win_05_all_pct,
        "selected_sample": selected.sample,
        "selected_win_05_all_pct": selected.win_05_all_pct,
        "uplift_05_all_pp": _uplift(selected, baseline),
        "selected_decisive_05_pct": selected.decisive_05_pct,
        "selected_win_10_all_pct": selected.win_10_all_pct,
        "selected_decisive_10_pct": selected.decisive_10_pct,
    }


def _median(values: Sequence[float]) -> float | None:
    return None if not values else statistics.median(values)


def _aggregate_candidate(
    rows: Sequence[dict[str, Any]],
    spec: CandidateSpec,
) -> dict[str, Any]:
    subset = [row for row in rows if row["candidate"] == spec.name]
    valid = [
        float(row["uplift_05_all_pp"])
        for row in subset
        if isinstance(row.get("uplift_05_all_pp"), (int, float))
        and int(row.get("selected_sample", 0)) > 0
    ]
    total_sample = sum(int(row.get("selected_sample", 0)) for row in subset)
    positive = sum(value > 0 for value in valid)
    negative = sum(value < 0 for value in valid)
    equal = sum(value == 0 for value in valid)
    directional = positive if spec.direction == "positive" else negative
    median_uplift = _median(valid)
    powered = (
        total_sample >= spec.minimum_total_sample
        and len(valid) >= spec.minimum_assets_with_sample
    )
    direction_ok = directional >= spec.minimum_directional_assets
    sign_ok = (
        median_uplift is not None
        and ((median_uplift > 0) if spec.direction == "positive" else (median_uplift < 0))
    )
    if not powered:
        verdict = "UNDERPOWERED"
    elif direction_ok and sign_ok:
        verdict = "SUPPORTED"
    else:
        verdict = "NOT_SUPPORTED"
    return {
        "candidate": spec.name,
        "expected_direction": spec.direction,
        "baseline": spec.baseline,
        "eligible_assets": spec.eligible_assets,
        "assets_with_sample": len(valid),
        "assets_improved": positive,
        "assets_worsened": negative,
        "assets_equal": equal,
        "median_uplift_05_all_pp": median_uplift,
        "total_selected_sample": total_sample,
        "minimum_total_sample": spec.minimum_total_sample,
        "minimum_assets_with_sample": spec.minimum_assets_with_sample,
        "minimum_directional_assets": spec.minimum_directional_assets,
        "verdict": verdict,
    }


def _build_markdown(summary: dict[str, Any], transfer: Sequence[dict[str, Any]]) -> str:
    lines = [
        "# P46 Confirmatory Holdout",
        "",
        (
            f"Holdout: `{summary['holdout_start']}` -> `{summary['holdout_end']}`. "
            "Thresholds were frozen before the holdout started."
        ),
        "",
        "No holdout threshold calibration and no new candidate search are performed.",
        "",
        "## Confirmatory verdicts",
        "",
        (
            "| candidate | expected | assets | median uplift | total sample | verdict |"
        ),
        "|---|---|---:|---:|---:|---|",
    ]
    for row in transfer:
        median = row.get("median_uplift_05_all_pp")
        median_text = "NA" if median is None else f"{float(median):+.2f} pp"
        lines.append(
            "| {candidate} | {direction} | {assets}/{eligible} | {median} | {sample} | "
            "**{verdict}** |".format(
                candidate=row["candidate"],
                direction=row["expected_direction"],
                assets=row["assets_with_sample"],
                eligible=row["eligible_assets"],
                median=median_text,
                sample=row["total_selected_sample"],
                verdict=row["verdict"],
            )
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- `SUPPORTED` is determined only by preregistered sample and transfer criteria.",
            "- `UNDERPOWERED` is not a failure and must not trigger threshold retuning.",
            "- No interaction/composite score is selected from this holdout.",
            "- Live trading / Exit / Risk logic remains unchanged.",
            "",
        ]
    )
    return "\n".join(lines)


def _create_zip(output_dir: Path) -> Path:
    zip_path = output_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=f"{output_dir.name}/{path.relative_to(output_dir)}")
    return zip_path


def evaluate_holdout(root: Path, *, force: bool = False) -> Path:
    now = datetime.now(UTC)
    if now < HOLDOUT_END:
        raise RuntimeError(
            "P46 holdout is still live. Outcome evaluation is locked until "
            f"{HOLDOUT_END.isoformat()}. Do not peek at partial holdout results."
        )
    frozen, thresholds = _load_frozen(root)
    output = result_dir(root)
    if output.exists() and not force:
        raise FileExistsError(
            f"P46 output already exists; use --force only for exact rerun: {output}"
        )

    print("P46 PRECHECK - confirmatory holdout; no network")
    price_5m: dict[str, Path] = {}
    price_15m: dict[str, Path] = {}
    p34_paths: dict[str, Path] = {}
    p36_paths: dict[str, Path] = {}
    source_manifest: list[dict[str, Any]] = []
    for symbol in DEFAULT_SYMBOLS:
        base = asset_root(root, symbol)
        dataset = base / "p30" / "dataset"
        five = dataset / "trade_5m.csv"
        fifteen = dataset / "trade_15m.csv"
        p34 = base / "p34" / "signals_open_interest.csv"
        p36 = base / "p36" / "signals_basis.csv"
        for path in (five, fifteen, p34, p36):
            if not path.is_file():
                raise FileNotFoundError(f"missing P46 holdout input for {symbol}: {path}")
        price_5m[symbol] = five
        price_15m[symbol] = fifteen
        p34_paths[symbol] = p34
        p36_paths[symbol] = p36
        source_manifest.append(
            {
                "symbol": symbol,
                "trade_5m": str(five),
                "trade_5m_sha256": _sha256_file(five),
                "trade_15m": str(fifteen),
                "trade_15m_sha256": _sha256_file(fifteen),
                "p34": str(p34),
                "p34_sha256": _sha256_file(p34),
                "p36": str(p36),
                "p36_sha256": _sha256_file(p36),
            }
        )
        print(f"  OK {symbol}: 5m + 15m + P34 + P36")

    series_by_symbol = {
        symbol: load_price_series(path, symbol=symbol) for symbol, path in price_5m.items()
    }
    exact_by_symbol: dict[str, list[SignalRecord]] = {}
    core_by_symbol: dict[str, list[SignalRecord]] = {}
    for symbol in DEFAULT_SYMBOLS:
        exact_by_symbol[symbol] = _load_signal_records(p34_paths[symbol], symbol=symbol)
        p36_rows = _load_signal_records(p36_paths[symbol], symbol=symbol)
        core_by_symbol[symbol] = [item for item in p36_rows if item.is_core]

    regime_values: dict[tuple[str, str, str], float] = {}
    for symbol in DEFAULT_SYMBOLS:
        for signal in core_by_symbol[symbol]:
            row = build_regime_row(
                RegimeCoreSignal(
                    symbol=signal.symbol,
                    direction=signal.direction,
                    touch_at=signal.touch_at,
                    outcome_05=signal.outcome_05,
                    outcome_10=signal.outcome_10,
                ),
                start=HOLDOUT_START,
                calibration_days=30,
                series_by_symbol=series_by_symbol,
            )
            value = row.directional_alt_btc_residual_15m_pct
            if value is not None:
                key = (signal.symbol, signal.direction, signal.touch_at.isoformat())
                regime_values[key] = value

    p44_q25 = {
        symbol: item.p44_residual_q25
        for symbol, item in thresholds.items()
        if item.p44_residual_q25 is not None
    }
    zone_features: dict[str, list[CoreLifecycleFeature]] = {}
    for symbol in DEFAULT_SYMBOLS:
        candles = load_candles(price_15m[symbol])
        detector = CleanZoneLifecycleDetector(symbol, candles)
        features: list[CoreLifecycleFeature] = []
        for signal in core_by_symbol[symbol]:
            features.append(
                build_core_feature(
                    ZoneCoreSignal(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        touch_at=signal.touch_at,
                        entry_price=signal.entry_price,
                        outcome_05=signal.outcome_05,
                        outcome_10=signal.outcome_10,
                    ),
                    detector=detector,
                    start=HOLDOUT_START,
                    calibration_days=30,
                    p44_values=regime_values,
                    p44_q25=p44_q25,
                )
            )
        zone_features[symbol] = features
        print(f"FEATURES {symbol}: exact={len(exact_by_symbol[symbol])} core={len(features)}")

    asset_rows: list[dict[str, Any]] = []
    for symbol in DEFAULT_SYMBOLS:
        exact = exact_by_symbol[symbol]
        exact_base = _metrics_from_outcomes(exact)
        cooldown = _metrics_from_outcomes(
            [item for item in exact if item.accepted_after_failure_embargo]
        )
        asset_rows.append(
            _candidate_row(
                symbol=symbol,
                candidate="cooldown_60m",
                baseline=exact_base,
                selected=cooldown,
            )
        )

        features = zone_features[symbol]
        core_base = _metrics_from_outcomes(features)
        near_rows = [item for item in features if _near(item)]
        near_base = _metrics_from_outcomes(near_rows)

        if symbol != "BTCUSDT":
            residual_rows = [item for item in features if item.p44_residual_q1 is True]
            asset_rows.append(
                _candidate_row(
                    symbol=symbol,
                    candidate="p44_residual_q1",
                    baseline=core_base,
                    selected=_metrics_from_outcomes(residual_rows),
                )
            )

        approach_threshold = thresholds[symbol].p451_approach_q25
        approach_rows = [
            item
            for item in near_rows
            if item.approach_slope_atr_per_bar is not None
            and item.approach_slope_atr_per_bar <= approach_threshold
        ]
        asset_rows.append(
            _candidate_row(
                symbol=symbol,
                candidate="zone_approach_slope_q1",
                baseline=near_base,
                selected=_metrics_from_outcomes(approach_rows),
            )
        )

        second = [item for item in features if _near(item) and item.current_test_ordinal == 2]
        fourth = [
            item
            for item in features
            if _near(item) and (item.current_test_ordinal or 0) >= 4
        ]
        asset_rows.append(
            _candidate_row(
                symbol=symbol,
                candidate="zone_second_retest",
                baseline=core_base,
                selected=_metrics_from_outcomes(second),
            )
        )
        asset_rows.append(
            _candidate_row(
                symbol=symbol,
                candidate="zone_fourth_plus_retest",
                baseline=core_base,
                selected=_metrics_from_outcomes(fourth),
            )
        )

    transfer = [_aggregate_candidate(asset_rows, spec) for spec in CANDIDATES]
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "asset_candidate_matrix.csv", asset_rows)
    _write_csv(output / "candidate_transfer.csv", transfer)
    _write_csv(output / "source_manifest.csv", source_manifest)

    feature_rows = [
        {
            "symbol": item.symbol,
            "direction": item.direction,
            "touch_at": item.touch_at,
            "outcome_05": item.outcome_05,
            "outcome_10": item.outcome_10,
            "entry_distance_atr": item.entry_distance_atr,
            "current_test_ordinal": item.current_test_ordinal,
            "approach_slope_atr_per_bar": item.approach_slope_atr_per_bar,
            "p44_residual_15m_pct": item.p44_residual_15m_pct,
            "p44_residual_q1": item.p44_residual_q1,
        }
        for symbol in DEFAULT_SYMBOLS
        for item in zone_features[symbol]
    ]
    _write_csv(output / "holdout_core_features.csv", feature_rows)

    summary: dict[str, Any] = {
        "architecture": "p46_confirmatory_holdout_v1",
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_end": HOLDOUT_END.isoformat(),
        "warmup_start": WARMUP_START.isoformat(),
        "freeze_fingerprint": (freeze_dir(root) / "FREEZE_LOCK.sha256")
        .read_text(encoding="ascii")
        .strip(),
        "frozen_at": frozen.get("frozen_at"),
        "symbols": list(DEFAULT_SYMBOLS),
        "candidate_transfer": transfer,
        "guardrails": cast(list[str], frozen["guardrails"]),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    _write_json(output / "summary.json", summary)
    (output / "summary.md").write_text(
        _build_markdown(summary, transfer),
        encoding="utf-8",
    )
    _write_json(
        output / "RUN_COMPLETE.json",
        {
            "complete": True,
            "generated_at": datetime.now(UTC).isoformat(),
            "holdout_start": HOLDOUT_START.isoformat(),
            "holdout_end": HOLDOUT_END.isoformat(),
        },
    )
    zip_path = _create_zip(output)
    print("P46 COMPLETE")
    print(f"Summary: {output / 'summary.md'}")
    print(f"Result ZIP: {zip_path}")
    return zip_path


def status(root: Path) -> dict[str, Any]:
    frozen = (freeze_dir(root) / "FROZEN_PROTOCOL.json").is_file()
    now = datetime.now(UTC)
    days_remaining = max(0.0, (HOLDOUT_END - now).total_seconds() / 86400.0)
    result = {
        "frozen": frozen,
        "now_utc": now.isoformat(),
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_end": HOLDOUT_END.isoformat(),
        "days_until_evaluation_unlock": days_remaining,
        "data_period": PERIOD_TAG,
        "latest_trade_day": LATEST_TRADE_DAY,
    }
    print(json.dumps(result, indent=2))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P46 confirmatory Entry holdout")
    parser.add_argument("command", choices=("freeze", "status", "evaluate"))
    parser.add_argument("--root", type=Path, default=Path("C:/cripta"))
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    if args.command == "freeze":
        freeze_protocol(root)
    elif args.command == "status":
        status(root)
    else:
        evaluate_holdout(root, force=bool(args.force))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
