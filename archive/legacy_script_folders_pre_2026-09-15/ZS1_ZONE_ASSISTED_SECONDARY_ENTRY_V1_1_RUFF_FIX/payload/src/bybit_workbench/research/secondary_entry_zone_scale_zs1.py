from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

VERSION = "ZS1_ZONE_ASSISTED_SECONDARY_ENTRY_V1"
PERIOD_TAG = "20260518_20260816"
PERIOD_DAYS = 90.0
BASE_ADVERSE = "0.5"
BASE_REBOUND = "0.3"
GOLD_CANDIDATE_ID = "A0.50_R0.30_Z_ZLE3"

EXPECTED_SHA256 = {
    "p52_first_full": "5f7c577b2f2bfd673973d63ac42d872af395e7116f3ac7d91bae11cca371f7ee",
    "p52_first_60m": "439454235857415ec2b564819e3c5792181e3793ebf8b0ddecbd169fedd79530",
    "p52_summary": "1bc3707403a7ddf712ae69e1e9698e5f2ac1222650113a5d2d5bed3f43b3b586",
    "se1_events": "1dca79fdaa452c346d5ff5249d3fb028a8ce33e5788fa6e1e53c89215cf41424",
    "se1_summary": "a441ef257958229738c27926eb09811f25f967cb3387bd1000f0795354ae9452",
    "se1_contract": "2ee76fd5512f1bd4b7a90aad2339391718532b069f81910fc995e31566950a3d",
    "se2_selected": "f062663f9636c789e9f5820683346fcd5939ed5a8e053991e46dbeecff9dcb4d",
    "se2_events": "b551ead0b22c11ed5892a328ccd599d8f2d0e328f1450dbb03d9b853b22ea884",
    "se2_summary": "b1b25a7a2b64ae74ca32b0647f778fcbe9d6d24b42f4ebcec511a54937b97729",
    "se2_provenance": "09a0dc10b2a60b7d95ba6579a0ea82ae19d7e1614cf0e14b2119dfbed0ef3919",
}


@dataclass(frozen=True)
class Sources:
    p52_first_full: Path
    p52_first_60m: Path
    p52_summary: Path
    se1_events: Path
    se1_summary: Path
    se1_contract: Path
    se2_selected: Path
    se2_events: Path
    se2_summary: Path
    se2_provenance: Path


@dataclass
class Metric:
    name: str
    rows: int
    resolved: int
    wins_plus_1p10: int
    win_rate_pct: float | None
    ev_usd: float | None
    net_usd: float
    profit_factor: float | None
    plus_0p50_pct: float | None
    plus_1p00_pct: float | None
    plus_2p00_pct: float | None
    plus_3p00_pct: float | None
    signals_per_day: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rows": self.rows,
            "resolved": self.resolved,
            "wins_plus_1p10": self.wins_plus_1p10,
            "win_rate_pct": self.win_rate_pct,
            "ev_usd": self.ev_usd,
            "net_usd": self.net_usd,
            "profit_factor": self.profit_factor,
            "plus_0p50_pct": self.plus_0p50_pct,
            "plus_1p00_pct": self.plus_1p00_pct,
            "plus_2p00_pct": self.plus_2p00_pct,
            "plus_3p00_pct": self.plus_3p00_pct,
            "signals_per_day": self.signals_per_day,
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _truth(value: str) -> bool:
    return value.strip().lower() == "true"


def _target_hits(row: dict[str, str]) -> dict[str, Any]:
    raw = row.get("target_hits_json", "")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("target_hits_json is not an object")
    return cast(dict[str, Any], value)


def _hit(row: dict[str, str], target: str) -> bool:
    return _target_hits(row).get(target) is not None


def primary_benchmark_pnl(row: dict[str, str]) -> float | None:
    """Reproduce SE2 primary benchmark economics for one SE1 row.

    +1.10 target = +10 USD net on 1000 USD notional after 1 USD round-trip cost.
    Structural stop before +1.10 = -(stop distance * 10) - 1 USD.
    Horizon without either event remains unresolved.
    """
    if _hit(row, "1.10"):
        return 10.0
    if row.get("secondary_exit_reason") == "structural_stop":
        distance = float(row["structural_stop_distance_from_scale_pct"])
        return -(distance * 10.0) - 1.0
    return None


def _source_paths(root: Path) -> Sources:
    p52 = root / "reports" / "mfe_giveback_clean_zone_p52" / "ALL9_P52_WORKING"
    se1 = root / "reports" / "secondary_entry_se1" / "ALL9_SE1_WORKING"
    se2 = (
        root
        / "reports"
        / "secondary_entry_se2"
        / "ALL9_SE2_DISCOVERY_20260821_161847"
    )
    return Sources(
        p52_first_full=p52 / "signal_first_structure_full.csv",
        p52_first_60m=p52 / "signal_first_structure_60m.csv",
        p52_summary=p52 / "summary.json",
        se1_events=se1 / "secondary_entry_events.csv",
        se1_summary=se1 / "summary.json",
        se1_contract=se1 / "run_contract.json",
        se2_selected=se2 / "selected_candidates.csv",
        se2_events=se2 / "selected_candidate_events.csv",
        se2_summary=se2 / "summary.json",
        se2_provenance=se2 / "provenance.json",
    )


def validate_sources(sources: Sources) -> dict[str, str]:
    source_map: dict[str, Path] = {
        "p52_first_full": sources.p52_first_full,
        "p52_first_60m": sources.p52_first_60m,
        "p52_summary": sources.p52_summary,
        "se1_events": sources.se1_events,
        "se1_summary": sources.se1_summary,
        "se1_contract": sources.se1_contract,
        "se2_selected": sources.se2_selected,
        "se2_events": sources.se2_events,
        "se2_summary": sources.se2_summary,
        "se2_provenance": sources.se2_provenance,
    }
    hashes: dict[str, str] = {}
    for field_name, expected in EXPECTED_SHA256.items():
        path = source_map[field_name]
        if not path.is_file():
            raise FileNotFoundError(f"required frozen report missing: {path}")
        actual = _sha256(path)
        hashes[field_name] = actual
        if actual != expected:
            raise RuntimeError(
                f"frozen source hash mismatch for {field_name}: "
                f"expected={expected} actual={actual} path={path}"
            )

    p52 = _load_json(sources.p52_summary)
    se1 = _load_json(sources.se1_summary)
    se2 = _load_json(sources.se2_summary)
    provenance = _load_json(sources.se2_provenance)
    if p52.get("exact_resolved_cohort") != 988:
        raise RuntimeError("P52 exact_resolved_cohort is not 988")
    if se1.get("signals") != 1063 or se1.get("triggered_rows") != 17576:
        raise RuntimeError("SE1 frozen cohort contract changed")
    selected_ids = se2.get("selected_candidate_ids")
    if not isinstance(selected_ids, list) or GOLD_CANDIDATE_ID not in selected_ids:
        raise RuntimeError("SE2 GOLD candidate missing")
    if provenance.get("new5_accessed") is not False:
        raise RuntimeError("SE2 provenance says NEW5 was accessed")
    if provenance.get("source_se1_events_sha256") != EXPECTED_SHA256["se1_events"]:
        raise RuntimeError("SE2 provenance SE1 hash does not match frozen SE1 events")
    return hashes


def _load_p52_first(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            key = (row["symbol"], row["touch_at"])
            if key in rows:
                raise RuntimeError(f"duplicate P52 signal key: {key}")
            rows[key] = row
    if len(rows) != 995:
        raise RuntimeError(f"P52 full first-event row count is {len(rows)}, expected 995")
    return rows


def _iter_base_se1(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if (
                row["min_adverse_depth_pct"] == BASE_ADVERSE
                and row["rebound_confirmation_pct"] == BASE_REBOUND
                and row["trigger_status"] == "triggered"
            ):
                yield row


def _load_gold(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["candidate_id"] != GOLD_CANDIDATE_ID:
                continue
            key = (row["symbol"], row["touch_at"])
            if key in rows:
                raise RuntimeError(f"duplicate GOLD signal key: {key}")
            rows[key] = row
    if len(rows) != 69:
        raise RuntimeError(f"GOLD row count is {len(rows)}, expected 69")
    return rows


def classify_zone_relation(
    se1_row: dict[str, str], p52_row: dict[str, str] | None
) -> tuple[str, str, str, float | None]:
    if p52_row is None:
        return ("no_p52_activation", "", "", None)
    if not _truth(p52_row["structure_resolved"]) or not p52_row["zone_outcome_at"]:
        return ("no_first_structure", p52_row["structure_state"], p52_row["structure_sign"], None)

    zone_at = _dt(p52_row["zone_outcome_at"])
    scale_at = _dt(se1_row["scale_entry_at"])
    delay_seconds = (scale_at - zone_at).total_seconds()
    if delay_seconds >= 0.0:
        return (
            "resolved_before_scale",
            p52_row["structure_state"],
            p52_row["structure_sign"],
            delay_seconds,
        )
    return (
        "resolved_after_scale",
        p52_row["structure_state"],
        p52_row["structure_sign"],
        delay_seconds,
    )


def _pct(n: int, d: int) -> float | None:
    if d == 0:
        return None
    return 100.0 * n / d


def _profit_factor(pnls: Iterable[float]) -> float | None:
    gains = 0.0
    losses = 0.0
    for pnl in pnls:
        if pnl > 0.0:
            gains += pnl
        elif pnl < 0.0:
            losses -= pnl
    if losses == 0.0:
        return None if gains == 0.0 else math.inf
    return gains / losses


def metric(name: str, rows: list[dict[str, Any]]) -> Metric:
    pnls = [row["primary_benchmark_pnl_usd"] for row in rows]
    resolved_pnls = [float(value) for value in pnls if value is not None]
    resolved_rows = [row for row in rows if row["primary_benchmark_pnl_usd"] is not None]
    resolved = len(resolved_pnls)
    wins = sum(bool(row["hit_plus_1p10"]) for row in resolved_rows)
    return Metric(
        name=name,
        rows=len(rows),
        resolved=resolved,
        wins_plus_1p10=wins,
        win_rate_pct=_pct(wins, resolved),
        ev_usd=(sum(resolved_pnls) / resolved if resolved else None),
        net_usd=sum(resolved_pnls),
        profit_factor=_profit_factor(resolved_pnls),
        plus_0p50_pct=_pct(sum(bool(row["hit_plus_0p50"]) for row in rows), len(rows)),
        plus_1p00_pct=_pct(sum(bool(row["hit_plus_1p00"]) for row in rows), len(rows)),
        plus_2p00_pct=_pct(sum(bool(row["hit_plus_2p00"]) for row in rows), len(rows)),
        plus_3p00_pct=_pct(sum(bool(row["hit_plus_3p00"]) for row in rows), len(rows)),
        signals_per_day=len(rows) / PERIOD_DAYS,
    )


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = fraction * (len(ordered) - 1)
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    weight = pos - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _timing_summary(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    delays = [
        float(row["seconds_zone_to_se1_scale"])
        for row in rows
        if row["seconds_zone_to_se1_scale"] is not None
        and float(row["seconds_zone_to_se1_scale"]) >= 0.0
    ]
    return {
        "name": name,
        "n": len(delays),
        "p25_minutes": (_percentile(delays, 0.25) / 60.0 if delays else None),
        "median_minutes": (statistics.median(delays) / 60.0 if delays else None),
        "p75_minutes": (_percentile(delays, 0.75) / 60.0 if delays else None),
        "max_hours": (max(delays) / 3600.0 if delays else None),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _scope_rows(category: str, rows: list[dict[str, Any]], key_name: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key_name])].append(row)
    out: list[dict[str, Any]] = []
    for scope in sorted(grouped):
        item = metric(f"{category}:{scope}", grouped[scope]).as_dict()
        item["category"] = category
        item["scope"] = scope
        out.append(item)
    return out


def _direct_p52_quality(path: Path, window: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            grouped[row["structure_state"]].append(row)
    out: list[dict[str, Any]] = []
    for state in sorted(grouped):
        rows = grouped[state]
        resolved_baseline = [
            r
            for r in rows
            if r["baseline_outcome"] in {"reached_plus_1p10", "hit_minus_1p00"}
        ]
        n = len(resolved_baseline)
        plus1 = sum(r["baseline_outcome"] == "reached_plus_1p10" for r in resolved_baseline)
        out.append(
            {
                "window": window,
                "structure_state": state,
                "n": len(rows),
                "resolved_baseline_n": n,
                "plus1p10_first_pct": _pct(plus1, n),
                "minus1_first_pct": _pct(n - plus1, n),
                "plus2_before_minus1_pct": _pct(
                    sum(_truth(r["plus2_before_minus1"]) for r in rows),
                    len(rows),
                ),
                "plus3_before_minus1_pct": _pct(
                    sum(_truth(r["plus3_before_minus1"]) for r in rows),
                    len(rows),
                ),
                "execution_economics": "NOT_AVAILABLE_FROM_P52_AGGREGATE_ROWS",
            }
        )
    return out


def _positive_scope_fraction(
    rows: list[dict[str, Any]],
    key_name: str,
    min_resolved: int,
) -> tuple[int, int, float | None]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key_name])].append(row)
    evaluable = 0
    positive = 0
    for part in grouped.values():
        m = metric("scope", part)
        if m.resolved < min_resolved or m.ev_usd is None:
            continue
        evaluable += 1
        if m.ev_usd > 0.0:
            positive += 1
    return positive, evaluable, _pct(positive, evaluable)


def run(root: Path, output_dir: Path) -> dict[str, Any]:
    print(f"[{VERSION}] stage=1/6 validate frozen inputs")
    sources = _source_paths(root)
    source_hashes = validate_sources(sources)

    print(f"[{VERSION}] stage=2/6 load P52 and SE2 GOLD")
    p52 = _load_p52_first(sources.p52_first_full)
    gold = _load_gold(sources.se2_events)

    print(f"[{VERSION}] stage=3/6 stream SE1 A0.50/R0.30 parent cohort")
    event_rows: list[dict[str, Any]] = []
    for se1_row in _iter_base_se1(sources.se1_events):
        key = (se1_row["symbol"], se1_row["touch_at"])
        p52_row = p52.get(key)
        relation, zone_state, zone_sign, delay_seconds = classify_zone_relation(se1_row, p52_row)
        pnl = primary_benchmark_pnl(se1_row)
        row = {
            "symbol": se1_row["symbol"],
            "direction": se1_row["direction"],
            "touch_at": se1_row["touch_at"],
            "month": se1_row["touch_at"][:7],
            "scale_entry_at": se1_row["scale_entry_at"],
            "scale_entry_move_vs_main_pct": float(se1_row["scale_entry_move_vs_main_pct"]),
            "zero_crossings_before_scale": int(se1_row["zero_crossings_before_scale"]),
            "zone_relation": relation,
            "zone_state": zone_state,
            "zone_sign": zone_sign,
            "zone_outcome_at": (p52_row["zone_outcome_at"] if p52_row is not None else ""),
            "seconds_zone_to_se1_scale": delay_seconds,
            "is_gold": key in gold,
            "hit_plus_0p50": _hit(se1_row, "0.50"),
            "hit_plus_1p00": _hit(se1_row, "1.00"),
            "hit_plus_1p10": _hit(se1_row, "1.10"),
            "hit_plus_2p00": _hit(se1_row, "2.00"),
            "hit_plus_3p00": _hit(se1_row, "3.00"),
            "secondary_exit_reason": se1_row["secondary_exit_reason"],
            "structural_stop_distance_from_scale_pct": float(
                se1_row["structural_stop_distance_from_scale_pct"]
            ),
            "primary_benchmark_pnl_usd": pnl,
        }
        event_rows.append(row)

    if len(event_rows) != 792:
        raise RuntimeError(f"SE1 parent cohort triggered rows={len(event_rows)}, expected 792")

    base_metric = metric("SE1_BASE_A0.50_R0.30", event_rows)
    if base_metric.resolved != 791 or base_metric.ev_usd is None:
        raise RuntimeError("SE1 parent benchmark resolved-count contract changed")
    if not math.isclose(base_metric.ev_usd, -0.9389280293885514, rel_tol=0.0, abs_tol=1e-12):
        raise RuntimeError(f"SE1 base EV mismatch: {base_metric.ev_usd}")

    gold_rows = [row for row in event_rows if bool(row["is_gold"])]
    gold_metric = metric("SE2_GOLD_A0.50_R0.30_ZLE3", gold_rows)
    if gold_metric.rows != 69 or gold_metric.ev_usd is None:
        raise RuntimeError("SE2 GOLD parent-row reconstruction failed")
    if not math.isclose(gold_metric.ev_usd, 2.331309038925068, rel_tol=0.0, abs_tol=1e-12):
        raise RuntimeError(f"SE2 GOLD EV mismatch: {gold_metric.ev_usd}")

    def before(state: str) -> list[dict[str, Any]]:
        return [
            row
            for row in event_rows
            if row["zone_relation"] == "resolved_before_scale" and row["zone_state"] == state
        ]

    zone_b = before("protective_hold_reclaim")
    zone_a = before("obstacle_clean_break_with")
    zone_adverse = before("obstacle_rejection_against") + before("protective_clean_break_against")
    zone_favorable = zone_b + zone_a
    zone_favorable_not_gold = [row for row in zone_favorable if not bool(row["is_gold"])]
    resolved_after_scale = [
        row
        for row in event_rows
        if row["zone_relation"] == "resolved_after_scale"
    ]

    print(f"[{VERSION}] stage=4/6 compute locked comparison and stability")
    categories = {
        "SE1_BASE_A0.50_R0.30": event_rows,
        "ZONE_B_HOLD_BEFORE_SE1_SCALE": zone_b,
        "ZONE_A_BREAK_BEFORE_SE1_SCALE": zone_a,
        "ZONE_FAVORABLE_BEFORE_SE1_SCALE": zone_favorable,
        "ZONE_FAVORABLE_NOT_GOLD": zone_favorable_not_gold,
        "ZONE_ADVERSE_BEFORE_SE1_SCALE": zone_adverse,
        "FIRST_STRUCTURE_AFTER_SE1_SCALE": resolved_after_scale,
        "SE2_GOLD_A0.50_R0.30_ZLE3": gold_rows,
    }
    comparisons = [metric(name, rows).as_dict() for name, rows in categories.items()]

    positive_symbols, evaluable_symbols, positive_symbol_pct = _positive_scope_fraction(
        zone_favorable_not_gold, "symbol", 5
    )
    positive_months, evaluable_months, positive_month_pct = _positive_scope_fraction(
        zone_favorable_not_gold, "month", 10
    )
    middle = metric("ZONE_FAVORABLE_NOT_GOLD", zone_favorable_not_gold)
    middle_class_found = bool(
        middle.resolved >= 60
        and middle.rows >= int(math.ceil(gold_metric.rows * 1.5))
        and middle.ev_usd is not None
        and middle.ev_usd > 0.0
        and middle.profit_factor is not None
        and middle.profit_factor >= 1.2
        and evaluable_symbols >= 7
        and positive_symbol_pct is not None
        and positive_symbol_pct >= 70.0
        and evaluable_months >= 3
        and positive_months >= 3
    )

    timing_rows = [
        _timing_summary("ZONE_B_HOLD_BEFORE_SE1_SCALE", zone_b),
        _timing_summary("ZONE_A_BREAK_BEFORE_SE1_SCALE", zone_a),
        _timing_summary("ZONE_FAVORABLE_BEFORE_SE1_SCALE", zone_favorable),
        _timing_summary("ZONE_ADVERSE_BEFORE_SE1_SCALE", zone_adverse),
    ]

    by_symbol: list[dict[str, Any]] = []
    by_month: list[dict[str, Any]] = []
    for name, rows in categories.items():
        by_symbol.extend(_scope_rows(name, rows, "symbol"))
        by_month.extend(_scope_rows(name, rows, "month"))

    print(f"[{VERSION}] stage=5/6 rederive direct P52 structure quality")
    direct_quality = _direct_p52_quality(sources.p52_first_60m, "first_resolved_within_60m")
    direct_quality.extend(
        _direct_p52_quality(
            sources.p52_first_full,
            "first_resolved_before_baseline_outcome",
        )
    )

    output_dir.mkdir(parents=True, exist_ok=False)
    _write_csv(output_dir / "comparison.csv", comparisons, list(comparisons[0].keys()))
    _write_csv(output_dir / "zone_timing.csv", timing_rows, list(timing_rows[0].keys()))
    _write_csv(output_dir / "by_symbol.csv", by_symbol, list(by_symbol[0].keys()))
    _write_csv(output_dir / "by_month.csv", by_month, list(by_month[0].keys()))
    _write_csv(
        output_dir / "p52_direct_structure_quality.csv",
        direct_quality,
        list(direct_quality[0].keys()),
    )
    event_fields = list(event_rows[0].keys())
    _write_csv(output_dir / "events.csv", event_rows, event_fields)

    verdict = (
        "EVIDENCE_FOR_MIDDLE_CLASS_ZONE_FILTER"
        if middle_class_found
        else "NO_EVIDENCE_FOR_MIDDLE_CLASS_ZONE_FILTER"
    )
    summary = {
        "research_version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "period_tag": PERIOD_TAG,
        "period_days": PERIOD_DAYS,
        "downloads": "DISABLED",
        "new5_accessed": False,
        "entry_changed": False,
        "exit_risk_execution_changed": False,
        "live_changed": False,
        "source_hashes": source_hashes,
        "locked_parent": {
            "se1_min_adverse_pct": 0.50,
            "se1_rebound_pct": 0.30,
            "gold_candidate_id": GOLD_CANDIDATE_ID,
        },
        "economics": {
            "secondary_margin_usd": 100.0,
            "leverage": 10.0,
            "notional_usd": 1000.0,
            "round_trip_cost_usd": 1.0,
            "plus_1p10_target_net_usd": 10.0,
            "loser_formula": "-(structural_stop_distance_pct * 10) - 1 USD",
        },
        "comparison": comparisons,
        "middle_class_gate": {
            "candidate": "ZONE_FAVORABLE_NOT_GOLD",
            "requires_resolved_gte": 60,
            "requires_count_gte_1p5x_gold": int(math.ceil(gold_metric.rows * 1.5)),
            "requires_ev_usd_gt": 0.0,
            "requires_profit_factor_gte": 1.2,
            "requires_evaluable_symbols_gte": 7,
            "requires_positive_symbol_fraction_pct_gte": 70.0,
            "requires_positive_months_gte": 3,
            "positive_symbols": positive_symbols,
            "evaluable_symbols": evaluable_symbols,
            "positive_symbol_fraction_pct": positive_symbol_pct,
            "positive_months": positive_months,
            "evaluable_months": evaluable_months,
            "positive_month_fraction_pct": positive_month_pct,
            "passed": middle_class_found,
        },
        "timing": timing_rows,
        "verdict": verdict,
        "interpretation_contract": {
            "tested": (
                "Whether a fully resolved P52 first zone event, known before the existing "
                "SE1 A0.50/R0.30 scale entry, can filter the broad SE1 parent into a more "
                "frequent positive-EV middle class while preserving exact SE1/SE2 economics."
            ),
            "not_tested": (
                "Opening a NEW Secondary exactly at P52 zone_outcome_at. P52 aggregate rows "
                "do not contain execution-truth entry price at that timestamp, so ZS1 does not "
                "invent a fill. A direct zone-trigger replay requires local trade/1m path data."
            ),
            "why_direct_p52_quality_is_separate": (
                "P52 continuation rates describe the main Entry path after structure resolution; "
                "they are not Secondary PnL from a new fill at the zone event."
            ),
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    readme = (
        f"{VERSION}\n\n"
        f"Verdict: {verdict}\n"
        f"Base SE1: N={base_metric.rows}, EV={base_metric.ev_usd:.6f} USD\n"
        f"Zone favorable not GOLD: N={middle.rows}, EV={middle.ev_usd:.6f} USD\n"
        f"SE2 GOLD: N={gold_metric.rows}, EV={gold_metric.ev_usd:.6f} USD\n\n"
        "This run is discovery/diagnostic on already-seen ALL9 data. It must not be used as "
        "production confirmation. It does not access NEW5 and does not modify "
        "Entry/Exit/Risk/live.\n"
        "If the zone filter fails while direct P52 obstacle-break quality remains strong, the "
        "next distinct hypothesis is a direct zone-event Secondary replay at the actual market "
        "price, not a later SE1 rebound entry.\n"
    )
    (output_dir / "README_RESULT.txt").write_text(readme, encoding="utf-8")

    print(f"[{VERSION}] stage=6/6 complete")
    print(f"Output: {output_dir}")
    print(f"Verdict: {verdict}")
    print(
        f"SE1 base N={base_metric.rows} EV={base_metric.ev_usd:.6f}; "
        f"zone-middle N={middle.rows} EV={middle.ev_usd:.6f}; "
        f"GOLD N={gold_metric.rows} EV={gold_metric.ev_usd:.6f}"
    )
    return summary


def _default_output(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return root / "reports" / "secondary_entry_zone_scale_zs1" / f"ALL9_ZS1_{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser(description=VERSION)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output_dir.resolve() if args.output_dir is not None else _default_output(root)
    run(root, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
