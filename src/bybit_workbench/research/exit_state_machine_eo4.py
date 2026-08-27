from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np

VERSION = "EO4_EXIT_STATE_MACHINE_V1"
FROZEN_END = datetime(2026, 8, 16, tzinfo=UTC)
TARGET_PCT = 1.10
STOP_PCT = -1.00
COST_PCT = 0.10
RUNNER_GIVEBACK_PCT = 1.00
WARNING_GIVEBACK_PCT = 0.50
PROVEN_MFE_PCT = 0.50

Policy = Literal["baseline", "structural_exit", "structural_runner", "hybrid"]
State = Literal["EARLY", "HEALTHY", "PROVEN", "RUNNER", "WARNING", "BROKEN"]


@dataclass(frozen=True, slots=True)
class ZoneEvent:
    outcome_at: datetime
    state: str


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    direction: str
    touch_at: datetime
    fill_at: datetime
    fill_price: float


@dataclass(frozen=True, slots=True)
class Result:
    policy: str
    symbol: str
    direction: str
    touch_at: str
    fill_at: str
    exit_at: str
    exit_price: float
    gross_pnl_pct: float
    net_pnl_pct: float
    pnl_usd_100_margin_10x: float
    exit_reason: str
    final_state: str
    duration_hours: float
    mfe_pct: float
    mae_pct: float
    structural_events_seen: int
    entries_during_position: int = 0


def parse_dt(value: str) -> datetime:
    result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def move_pct(direction: str, entry: float, price: float) -> float:
    raw = (price / entry - 1.0) * 100.0
    return raw if direction.lower() == "long" else -raw


def classify(direction: str, role: str, outcome: str) -> str:
    protective = role.lower() == ("support" if direction.lower() == "long" else "resistance")
    clean = outcome.lower() == "clean_break"
    if protective:
        return "protective_clean_break_against" if clean else "protective_hold_reclaim"
    return "obstacle_clean_break_with" if clean else "obstacle_rejection_against"


def load_trades(path: Path, symbols: set[str] | None) -> list[Trade]:
    rows: list[Trade] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            symbol = row["symbol"].upper()
            if symbols and symbol not in symbols:
                continue
            rows.append(Trade(symbol, row["direction"], parse_dt(row["touch_at"]), parse_dt(row["fill_at"]), float(row["fill_price"])))
    rows.sort(key=lambda x: (x.symbol, x.fill_at, x.touch_at))
    if not rows:
        raise ValueError("Не загружено ни одной сделки EO2")
    return rows


def load_events(path: Path, directions: dict[tuple[str, int], str]) -> dict[str, list[ZoneEvent]]:
    result: defaultdict[str, list[ZoneEvent]] = defaultdict(list)
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            outcome = row.get("outcome", "").strip()
            outcome_at = row.get("outcome_at", "").strip()
            if outcome not in {"bounce", "false_break_reclaim", "clean_break"} or not outcome_at:
                continue
            symbol = row["symbol"].upper()
            event_at = parse_dt(row["event_at"])
            direction = directions.get((symbol, int(event_at.timestamp())))
            # Direction is trade-specific. Preserve raw role/outcome in an encoded state.
            result[symbol].append(ZoneEvent(parse_dt(outcome_at), f"{row['role'].lower()}|{outcome.lower()}"))
    for events in result.values():
        events.sort(key=lambda x: x.outcome_at)
    return dict(result)


def load_series(cache_root: Path, symbol: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    timestamps: list[np.ndarray] = []
    highs: list[np.ndarray] = []
    lows: list[np.ndarray] = []
    closes: list[np.ndarray] = []
    for path in sorted((cache_root / symbol).glob("*.npz")):
        with np.load(path, allow_pickle=False) as data:
            timestamps.append(np.asarray(data["minute_ts"], dtype=np.float64))
            highs.append(np.asarray(data["high"], dtype=np.float64))
            lows.append(np.asarray(data["low"], dtype=np.float64))
            closes.append(np.asarray(data["close"], dtype=np.float64))
    if not timestamps:
        raise FileNotFoundError(f"Нет минутного cache EO3 для {symbol}")
    ts = np.concatenate(timestamps)
    order = np.argsort(ts, kind="stable")
    ts = ts[order]
    unique = np.r_[True, np.diff(ts) > 0]
    return ts[unique], np.concatenate(highs)[order][unique], np.concatenate(lows)[order][unique], np.concatenate(closes)[order][unique]


def _state_after_event(current: State, event: str, mfe: float) -> State:
    if event == "protective_clean_break_against":
        return "BROKEN"
    if event == "obstacle_clean_break_with":
        return "RUNNER"
    if event == "obstacle_rejection_against":
        return "WARNING"
    if event == "protective_hold_reclaim":
        return "PROVEN" if mfe >= PROVEN_MFE_PCT else "HEALTHY"
    return current


def simulate(trade: Trade, policy: Policy, series: tuple[np.ndarray, ...], raw_events: list[ZoneEvent]) -> Result:
    ts, high, low, close = series
    start = int(np.searchsorted(ts + 60.0, trade.fill_at.timestamp(), side="left"))
    end = int(np.searchsorted(ts, FROZEN_END.timestamp(), side="left"))
    if start >= end:
        raise ValueError(f"Нет пути после fill: {trade.symbol} {trade.fill_at.isoformat()}")
    events = [ZoneEvent(e.outcome_at, classify(trade.direction, *e.state.split("|", 1))) for e in raw_events if trade.fill_at < e.outcome_at < FROZEN_END]
    event_i = 0
    state: State = "EARLY"
    running_mfe = -math.inf
    mae = math.inf
    seen = 0
    exit_reason = "data_end"
    exit_i = end - 1
    exit_price = float(close[exit_i])
    for i in range(start, end):
        available_at = datetime.fromtimestamp(float(ts[i] + 60.0), tz=UTC)
        favorable = move_pct(trade.direction, trade.fill_price, float(high[i] if trade.direction.lower() == "long" else low[i]))
        adverse = move_pct(trade.direction, trade.fill_price, float(low[i] if trade.direction.lower() == "long" else high[i]))
        running_mfe = max(running_mfe, favorable)
        mae = min(mae, adverse)
        if state == "EARLY" and running_mfe >= PROVEN_MFE_PCT:
            state = "PROVEN"
        # A resting hard stop/target is executable inside the minute. A structural
        # outcome becomes known only at the close, so it cannot cancel a fill that
        # already occurred earlier in that same minute.
        if adverse <= STOP_PCT:
            exit_reason, exit_i = "hard_stop", i
            exit_price = trade.fill_price * (1.0 + (STOP_PCT / 100.0) * (1 if trade.direction.lower() == "long" else -1))
            break
        runner_before_close = state == "RUNNER" and policy in {"structural_runner", "hybrid"}
        if not runner_before_close and favorable >= TARGET_PCT:
            exit_reason, exit_i = "baseline_target", i
            exit_price = trade.fill_price * (1.0 + (TARGET_PCT / 100.0) * (1 if trade.direction.lower() == "long" else -1))
            break
        while event_i < len(events) and events[event_i].outcome_at <= available_at:
            seen += 1
            state = _state_after_event(state, events[event_i].state, running_mfe)
            event_i += 1
        close_move = move_pct(trade.direction, trade.fill_price, float(close[i]))
        if policy in {"structural_exit", "structural_runner", "hybrid"} and state == "BROKEN":
            exit_reason, exit_i, exit_price = "protective_break", i, float(close[i])
            break
        runner = state == "RUNNER" and policy in {"structural_runner", "hybrid"}
        if policy == "hybrid" and runner and running_mfe >= TARGET_PCT and close_move <= running_mfe - RUNNER_GIVEBACK_PCT:
            exit_reason, exit_i, exit_price = "runner_exhaustion", i, float(close[i])
            break
        if policy == "hybrid" and state == "WARNING" and running_mfe >= PROVEN_MFE_PCT and close_move <= max(0.0, running_mfe - WARNING_GIVEBACK_PCT):
            exit_reason, exit_i, exit_price = "warning_giveback", i, float(close[i])
            break
    gross = move_pct(trade.direction, trade.fill_price, exit_price)
    net = gross - COST_PCT
    exit_at = datetime.fromtimestamp(float(ts[exit_i] + 60.0), tz=UTC)
    return Result(policy, trade.symbol, trade.direction, trade.touch_at.isoformat(), trade.fill_at.isoformat(), exit_at.isoformat(), exit_price, gross, net, net * 10.0, exit_reason, state, (exit_at - trade.fill_at).total_seconds() / 3600.0, running_mfe, mae, seen)


def worker(args: tuple[str, list[Trade], Path, list[ZoneEvent], tuple[Policy, ...]]) -> list[Result]:
    symbol, trades, cache_root, events, policies = args
    series = load_series(cache_root, symbol)
    return [simulate(trade, policy, series, events) for trade in trades for policy in policies]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def add_overlap_counts(results: list[Result], trades: list[Trade]) -> list[Result]:
    by_symbol: defaultdict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        by_symbol[trade.symbol].append(trade)
    enriched: list[Result] = []
    for result in results:
        start, end = parse_dt(result.fill_at), parse_dt(result.exit_at)
        count = sum(start < item.fill_at < end for item in by_symbol[result.symbol])
        enriched.append(Result(**{**asdict(result), "entries_during_position": count}))
    return enriched


def summary_rows(results: list[Result]) -> list[dict[str, Any]]:
    grouped: defaultdict[str, list[Result]] = defaultdict(list)
    for row in results:
        grouped[row.policy].append(row)
    output: list[dict[str, Any]] = []
    for policy, rows in grouped.items():
        pnl = [r.pnl_usd_100_margin_10x for r in rows]
        gross_win = sum(x for x in pnl if x > 0)
        gross_loss = -sum(x for x in pnl if x < 0)
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for row in sorted(rows, key=lambda x: x.fill_at):
            equity += row.pnl_usd_100_margin_10x
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        output.append({
            "policy": policy, "trades": len(rows), "wins": sum(x > 0 for x in pnl), "losses": sum(x <= 0 for x in pnl),
            "net_pnl_usd": sum(pnl), "ev_usd": sum(pnl) / len(pnl), "profit_factor": gross_win / gross_loss if gross_loss else None,
            "max_drawdown_usd": max_dd, "mean_duration_hours": sum(r.duration_hours for r in rows) / len(rows),
            "entries_during_position": sum(r.entries_during_position for r in rows), "exit_reasons": json.dumps(Counter(r.exit_reason for r in rows), ensure_ascii=False, sort_keys=True),
        })
    return sorted(output, key=lambda x: x["policy"])


def chronological_replay(results: list[Result]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Allow only one active position per symbol for each policy."""
    grouped: defaultdict[tuple[str, str], list[Result]] = defaultdict(list)
    for row in results:
        grouped[(row.policy, row.symbol)].append(row)
    audit: list[dict[str, Any]] = []
    accepted: list[Result] = []
    for (policy, symbol), rows in sorted(grouped.items()):
        occupied_until: datetime | None = None
        active_touch = ""
        for row in sorted(rows, key=lambda x: (x.fill_at, x.touch_at)):
            fill_at = parse_dt(row.fill_at)
            allowed = occupied_until is None or fill_at >= occupied_until
            audit.append({
                "policy": policy, "symbol": symbol, "touch_at": row.touch_at, "fill_at": row.fill_at,
                "accepted": allowed, "blocked_by_touch_at": "" if allowed else active_touch,
                "candidate_exit_at": row.exit_at, "candidate_exit_reason": row.exit_reason,
            })
            if allowed:
                accepted.append(row)
                occupied_until = parse_dt(row.exit_at)
                active_touch = row.touch_at
    accepted.sort(key=lambda x: (x.fill_at, x.symbol, x.policy))
    summaries = summary_rows(accepted)
    total_by_policy = Counter(row.policy for row in results)
    accepted_by_policy = Counter(row.policy for row in accepted)
    for row in summaries:
        policy = str(row["policy"])
        row["signals_total"] = total_by_policy[policy]
        row["signals_blocked"] = total_by_policy[policy] - accepted_by_policy[policy]
    return audit, summaries


def scope_summary_rows(results: list[Result]) -> list[dict[str, Any]]:
    grouped: defaultdict[tuple[str, str, str], list[Result]] = defaultdict(list)
    for row in results:
        month = row.fill_at[:7]
        for scope, value in (("symbol", row.symbol), ("direction", row.direction), ("month", month)):
            grouped[(row.policy, scope, value)].append(row)
    output: list[dict[str, Any]] = []
    for (policy, scope, value), rows in sorted(grouped.items()):
        base = summary_rows(rows)[0]
        output.append({"policy": policy, "scope": scope, "value": value, **{k: v for k, v in base.items() if k != "policy"}})
    return output


def run(args: argparse.Namespace) -> Path:
    policies: tuple[Policy, ...] = ("baseline", "structural_exit", "structural_runner", "hybrid")
    symbols = {x.strip().upper() for x in args.symbols.split(",") if x.strip()} or None
    trades = load_trades(args.eo2_events, symbols)
    if args.sample_fraction < 1.0:
        selected: list[Trade] = []
        by_symbol: defaultdict[str, list[Trade]] = defaultdict(list)
        for trade in trades:
            by_symbol[trade.symbol].append(trade)
        for rows in by_symbol.values():
            wanted = max(1, math.ceil(len(rows) * args.sample_fraction))
            indexes = np.linspace(0, len(rows) - 1, wanted, dtype=int)
            selected.extend(rows[int(i)] for i in indexes)
        trades = sorted(selected, key=lambda x: (x.symbol, x.fill_at))
    directions = {(t.symbol, int(t.touch_at.timestamp())): t.direction for t in trades}
    events = load_events(args.zone_events, directions)
    by_symbol_trades: defaultdict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        by_symbol_trades[trade.symbol].append(trade)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    partial = args.output_dir / "partial"
    partial.mkdir(exist_ok=True)
    work = [(symbol, rows, args.cache_root, events.get(symbol, []), policies) for symbol, rows in sorted(by_symbol_trades.items())]
    results: list[Result] = []
    started = time.time()
    with ProcessPoolExecutor(max_workers=min(args.workers, len(work))) as pool:
        futures = {pool.submit(worker, item): item[0] for item in work}
        for done, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            rows = future.result()
            write_csv(partial / f"{symbol}.csv", [asdict(x) for x in rows])
            results.extend(rows)
            print(f"[EO4] {done}/{len(work)} {symbol} готово; elapsed={time.time()-started:.1f}s", flush=True)
    results = add_overlap_counts(results, trades)
    results.sort(key=lambda x: (x.fill_at, x.symbol, x.policy))
    write_csv(args.output_dir / "eo4_trade_results.csv", [asdict(x) for x in results])
    summaries = summary_rows(results)
    write_csv(args.output_dir / "policy_summary.csv", summaries)
    chronology, portfolio_summaries = chronological_replay(results)
    write_csv(args.output_dir / "portfolio_chronology.csv", chronology)
    write_csv(args.output_dir / "portfolio_policy_summary.csv", portfolio_summaries)
    write_csv(args.output_dir / "scope_summary.csv", scope_summary_rows(results))
    contract = {
        "version": VERSION, "sample_fraction": args.sample_fraction, "symbols": sorted(by_symbol_trades), "trades": len(trades),
        "policies": list(policies), "frozen": {"entry": "EO2 -0.20%", "target_pct": TARGET_PCT, "hard_stop_pct": STOP_PCT, "cost_pct": COST_PCT,
        "proven_mfe_pct": PROVEN_MFE_PCT, "warning_giveback_pct": WARNING_GIVEBACK_PCT, "runner_giveback_pct": RUNNER_GIVEBACK_PCT,
        "execution": "causal 1m close after confirmed structural outcome; adverse-first OHLC ambiguity"},
        "inputs": {"eo2_events": str(args.eo2_events), "eo2_sha256": sha256(args.eo2_events), "zone_events": str(args.zone_events), "zone_sha256": sha256(args.zone_events)},
        "summary": summaries, "portfolio_summary": portfolio_summaries, "elapsed_seconds": time.time() - started,
    }
    (args.output_dir / "summary.json").write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "RUN_COMPLETE.json").write_text(json.dumps({"version": VERSION, "completed_at": datetime.now(UTC).isoformat(), "trades": len(trades)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return args.output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EO4: причинное сопровождение позиции по структуре зон")
    parser.add_argument("--eo2-events", type=Path, required=True)
    parser.add_argument("--zone-events", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--sample-fraction", type=float, default=1.0)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = parser.parse_args()
    if not 0 < args.sample_fraction <= 1:
        parser.error("--sample-fraction должен быть в диапазоне (0, 1]")
    if args.workers < 1:
        parser.error("--workers должен быть положительным")
    return args


if __name__ == "__main__":
    run(parse_args())
