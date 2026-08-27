from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

INTERVAL_MINUTES = 5
INTERVAL_MS = INTERVAL_MINUTES * 60 * 1000
LOOKBACK_HOURS = 8


@dataclass(frozen=True)
class Candle:
    start_ms: int
    close: float


@dataclass(frozen=True)
class Series:
    symbol: str
    candles: tuple[Candle, ...]
    index_by_start: dict[int, int]


@dataclass(frozen=True)
class CoreInput:
    symbol: str
    path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P44 UNI/LINK market-regime proxy probe")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--endpoint", default="https://api.bybit.kz")
    parser.add_argument("--start", default="2026-05-18T00:00:00+00:00")
    parser.add_argument("--end", default="2026-08-16T00:00:00+00:00")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--uni-core", type=Path, default=None)
    parser.add_argument("--link-core", type=Path, default=None)
    return parser.parse_args()


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def discover_latest(pattern: str, filename: str, root: Path) -> Path:
    matches = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    for directory in matches:
        candidate = directory / filename
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Could not discover {filename} under {root} with pattern {pattern}")


def discover_inputs(root: Path, uni_core: Path | None, link_core: Path | None) -> list[CoreInput]:
    reports = root / "reports"
    if uni_core is None:
        uni_core = discover_latest(
            "entry_research_v13/UNIUSDT_*",
            "absorption_features.csv",
            reports,
        )
    if link_core is None:
        link_core = (
            reports
            / "cross_asset_validation"
            / "LINKUSDT_20260518_20260816"
            / "p40"
            / "absorption_features.csv"
        )
    if not link_core.exists():
        raise FileNotFoundError(f"LINK core file not found: {link_core}")
    return [CoreInput("UNIUSDT", uni_core), CoreInput("LINKUSDT", link_core)]


def http_json(url: str, retries: int = 5) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "bybit-workbench-p44/1"})
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Expected JSON object")
            return payload
        except Exception as exc:  # noqa: BLE001 - standalone resilient downloader
            last_error = exc
            if attempt + 1 >= retries:
                break
            time.sleep(1.0 + attempt * 1.5)
    raise RuntimeError(f"HTTP request failed after {retries} attempts: {url}") from last_error


def fetch_klines(
    endpoint: str,
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[Candle]:
    start_ms = int(start.timestamp() * 1000)
    cursor_end = int(end.timestamp() * 1000) - 1
    rows: dict[int, Candle] = {}
    page = 0
    while cursor_end >= start_ms:
        page += 1
        params = urllib.parse.urlencode(
            {
                "category": "linear",
                "symbol": symbol,
                "interval": str(INTERVAL_MINUTES),
                "start": start_ms,
                "end": cursor_end,
                "limit": 1000,
            }
        )
        url = f"{endpoint.rstrip('/')}/v5/market/kline?{params}"
        payload = http_json(url)
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(f"Bybit returned error for {symbol}: {payload}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"Missing result for {symbol}")
        raw_list = result.get("list")
        if not isinstance(raw_list, list) or not raw_list:
            break
        oldest = cursor_end
        for raw in raw_list:
            if not isinstance(raw, list) or len(raw) < 5:
                continue
            ts = int(raw[0])
            if start_ms <= ts < int(end.timestamp() * 1000):
                rows[ts] = Candle(ts, float(raw[4]))
            oldest = min(oldest, ts)
        print(f"  {symbol} 5m page {page}: {len(rows)} candles")
        if oldest <= start_ms:
            break
        next_end = oldest - 1
        if next_end >= cursor_end:
            break
        cursor_end = next_end
        time.sleep(0.03)
    return sorted(rows.values(), key=lambda candle: candle.start_ms)


def write_cache(path: Path, candles: Iterable[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["start_ms", "close"])
        for candle in candles:
            writer.writerow([candle.start_ms, f"{candle.close:.12g}"])


def read_cache(path: Path) -> list[Candle]:
    candles: list[Candle] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            candles.append(Candle(int(row["start_ms"]), float(row["close"])))
    return candles


def load_series(
    cache_dir: Path,
    endpoint: str,
    symbol: str,
    start: datetime,
    end: datetime,
) -> Series:
    path = cache_dir / f"{symbol}_5m.csv"
    if path.exists():
        print(f"Reuse {symbol} cache: {path}")
        candles = read_cache(path)
    else:
        print(f"Download {symbol} 5m market-regime series")
        candles = fetch_klines(endpoint, symbol, start, end)
        write_cache(path, candles)
    index = {candle.start_ms: idx for idx, candle in enumerate(candles)}
    return Series(symbol, tuple(candles), index)


def floor_5m(dt: datetime) -> datetime:
    minute = dt.minute - dt.minute % INTERVAL_MINUTES
    return dt.replace(minute=minute, second=0, microsecond=0)


def last_completed_start_ms(touch: datetime) -> int:
    start = floor_5m(touch) - timedelta(minutes=INTERVAL_MINUTES)
    return int(start.timestamp() * 1000)


def return_pct(series: Series, end_idx: int, bars: int) -> float | None:
    start_idx = end_idx - bars
    if start_idx < 0 or end_idx >= len(series.candles):
        return None
    start = series.candles[start_idx].close
    end = series.candles[end_idx].close
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def bar_returns(series: Series, end_idx: int, bars: int) -> list[float]:
    start_idx = max(1, end_idx - bars + 1)
    output: list[float] = []
    for idx in range(start_idx, end_idx + 1):
        prev = series.candles[idx - 1].close
        current = series.candles[idx].close
        if prev > 0:
            output.append((current / prev - 1.0) * 100.0)
    return output


def corr_beta(xs: list[float], ys: list[float]) -> tuple[float | None, float | None]:
    n = min(len(xs), len(ys))
    if n < 6:
        return None, None
    x = xs[-n:]
    y = ys[-n:]
    mean_x = statistics.fmean(x)
    mean_y = statistics.fmean(y)
    dx = [value - mean_x for value in x]
    dy = [value - mean_y for value in y]
    var_x = sum(value * value for value in dx)
    var_y = sum(value * value for value in dy)
    if var_x <= 0 or var_y <= 0:
        return None, None
    cov = sum(a * b for a, b in zip(dx, dy, strict=True))
    corr = cov / math.sqrt(var_x * var_y)
    beta = cov / var_x
    return corr, beta


def z_abs_latest(values: list[float]) -> float | None:
    if len(values) < 12:
        return None
    baseline = values[:-1]
    latest = values[-1]
    if len(baseline) < 6:
        return None
    sigma = statistics.pstdev(baseline)
    if sigma <= 0:
        return None
    return abs(latest) / sigma


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def f(value: float | None) -> str:
    return "" if value is None or not math.isfinite(value) else f"{value:.8f}"


def build_features(
    core: CoreInput,
    alt: Series,
    btc: Series,
    eth: Series,
) -> list[dict[str, Any]]:
    rows = read_rows(core.path)
    output: list[dict[str, Any]] = []
    for row in rows:
        touch = parse_dt(row["touch_at"])
        key = last_completed_start_ms(touch)
        alt_idx = alt.index_by_start.get(key)
        btc_idx = btc.index_by_start.get(key)
        eth_idx = eth.index_by_start.get(key)
        if alt_idx is None or btc_idx is None or eth_idx is None:
            continue
        direction = row["direction"]
        sign = 1.0 if direction == "Long" else -1.0

        btc5 = return_pct(btc, btc_idx, 1)
        btc15 = return_pct(btc, btc_idx, 3)
        btc60 = return_pct(btc, btc_idx, 12)
        eth5 = return_pct(eth, eth_idx, 1)
        eth15 = return_pct(eth, eth_idx, 3)
        eth60 = return_pct(eth, eth_idx, 12)
        alt15 = return_pct(alt, alt_idx, 3)
        alt60 = return_pct(alt, alt_idx, 12)

        btc_ret_1h = bar_returns(btc, btc_idx, 12)
        btc_ret_3h = bar_returns(btc, btc_idx, 36)
        alt_ret_1h = bar_returns(alt, alt_idx, 12)
        alt_ret_3h = bar_returns(alt, alt_idx, 36)
        corr1h, _ = corr_beta(btc_ret_1h, alt_ret_1h)
        corr3h, beta3h = corr_beta(btc_ret_3h, alt_ret_3h)
        shock = z_abs_latest(btc_ret_3h)

        residual15 = None
        residual60 = None
        if beta3h is not None and alt15 is not None and btc15 is not None:
            residual15 = alt15 - beta3h * btc15
        if beta3h is not None and alt60 is not None and btc60 is not None:
            residual60 = alt60 - beta3h * btc60

        feature: dict[str, Any] = {
            "symbol": core.symbol,
            "direction": direction,
            "touch_at": row["touch_at"],
            "first_0_5_vs_1_0": row["first_0_5_vs_1_0"],
            "first_1_0_vs_1_0": row.get("first_1_0_vs_1_0", ""),
            "btc_ret_5m_pct": btc5,
            "btc_ret_15m_pct": btc15,
            "btc_ret_60m_pct": btc60,
            "eth_ret_5m_pct": eth5,
            "eth_ret_15m_pct": eth15,
            "eth_ret_60m_pct": eth60,
            "alt_ret_15m_pct": alt15,
            "alt_ret_60m_pct": alt60,
            "directional_btc_5m_pct": None if btc5 is None else sign * btc5,
            "directional_btc_15m_pct": None if btc15 is None else sign * btc15,
            "directional_btc_60m_pct": None if btc60 is None else sign * btc60,
            "directional_eth_15m_pct": None if eth15 is None else sign * eth15,
            "directional_eth_minus_btc_15m_pct": (
                None if eth15 is None or btc15 is None else sign * (eth15 - btc15)
            ),
            "alt_btc_corr_1h": corr1h,
            "alt_btc_corr_3h": corr3h,
            "alt_btc_beta_3h": beta3h,
            "directional_alt_btc_residual_15m_pct": (
                None if residual15 is None else sign * residual15
            ),
            "directional_alt_btc_residual_60m_pct": (
                None if residual60 is None else sign * residual60
            ),
            "btc_5m_shock_z": shock,
        }
        output.append(feature)
    return output


def quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("No values for quantile")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def outcome_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    good = sum(row["first_0_5_vs_1_0"] == "favorable_first" for row in rows)
    bad = sum(row["first_0_5_vs_1_0"] == "adverse_first" for row in rows)
    return {
        "signals": total,
        "good": good,
        "bad": bad,
        "good_percent": round(100.0 * good / total, 4) if total else 0.0,
        "bad_percent": round(100.0 * bad / total, 4) if total else 0.0,
    }


def quartile_report(rows: list[dict[str, Any]], feature: str) -> list[dict[str, Any]]:
    usable = [row for row in rows if isinstance(row.get(feature), (int, float))]
    values = [float(row[feature]) for row in usable]
    if len(values) < 8:
        return []
    q1 = quantile(values, 0.25)
    q2 = quantile(values, 0.50)
    q3 = quantile(values, 0.75)
    groups: list[tuple[str, Callable[[float], bool]]] = [
        ("Q1", lambda value: value <= q1),
        ("Q2", lambda value: q1 < value <= q2),
        ("Q3", lambda value: q2 < value <= q3),
        ("Q4", lambda value: value > q3),
    ]
    output: list[dict[str, Any]] = []
    for label, predicate in groups:
        group = [row for row in usable if predicate(float(row[feature]))]
        output.append(
            {
                "feature": feature,
                "quartile": label,
                "q25": q1,
                "q50": q2,
                "q75": q3,
                **outcome_metrics(group),
            }
        )
    return output


def thresholds(rows: list[dict[str, Any]]) -> dict[str, tuple[float, float, float]]:
    names = [
        "directional_btc_5m_pct",
        "directional_btc_15m_pct",
        "directional_btc_60m_pct",
        "directional_eth_15m_pct",
        "directional_eth_minus_btc_15m_pct",
        "alt_btc_corr_1h",
        "alt_btc_corr_3h",
        "directional_alt_btc_residual_15m_pct",
        "directional_alt_btc_residual_60m_pct",
        "btc_5m_shock_z",
    ]
    output: dict[str, tuple[float, float, float]] = {}
    for name in names:
        values = [float(row[name]) for row in rows if isinstance(row.get(name), (int, float))]
        if values:
            output[name] = (
                quantile(values, 0.25),
                quantile(values, 0.50),
                quantile(values, 0.75),
            )
    return output


def veto_metrics(
    rows: list[dict[str, Any]],
    name: str,
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    good_all = [row for row in rows if row["first_0_5_vs_1_0"] == "favorable_first"]
    bad_all = [row for row in rows if row["first_0_5_vs_1_0"] == "adverse_first"]
    blocked = [row for row in rows if predicate(row)]
    blocked_good = sum(row["first_0_5_vs_1_0"] == "favorable_first" for row in blocked)
    blocked_bad = sum(row["first_0_5_vs_1_0"] == "adverse_first" for row in blocked)
    bad_blocked_pct = 100.0 * blocked_bad / len(bad_all) if bad_all else 0.0
    good_blocked_pct = 100.0 * blocked_good / len(good_all) if good_all else 0.0
    efficiency = None if good_blocked_pct == 0 else bad_blocked_pct / good_blocked_pct
    precision = 100.0 * blocked_bad / len(blocked) if blocked else 0.0
    return {
        "candidate": name,
        "blocked_signals": len(blocked),
        "blocked_bad": blocked_bad,
        "blocked_good": blocked_good,
        "bad_entries_blocked_percent": round(bad_blocked_pct, 4),
        "good_entries_blocked_percent": round(good_blocked_pct, 4),
        "blocked_bad_precision_percent": round(precision, 4),
        "veto_efficiency_ratio": None if efficiency is None else round(efficiency, 4),
    }


def candidate_vetos(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    t = thresholds(rows)
    required = {
        "directional_btc_15m_pct",
        "directional_btc_5m_pct",
        "alt_btc_corr_3h",
        "directional_alt_btc_residual_15m_pct",
        "directional_eth_15m_pct",
        "btc_5m_shock_z",
    }
    if not required.issubset(t):
        return []
    btc15_q25, _, _ = t["directional_btc_15m_pct"]
    btc5_q25, _, _ = t["directional_btc_5m_pct"]
    _, _, corr_q75 = t["alt_btc_corr_3h"]
    _, residual_median, residual_q75 = t["directional_alt_btc_residual_15m_pct"]
    eth15_q25, _, _ = t["directional_eth_15m_pct"]
    _, _, shock_q75 = t["btc_5m_shock_z"]

    def val(row: dict[str, Any], key: str) -> float:
        value = row.get(key)
        return float(value) if isinstance(value, (int, float)) else math.nan

    candidates: list[tuple[str, Callable[[dict[str, Any]], bool]]] = [
        (
            "btc_15m_most_adverse_quartile",
            lambda row: val(row, "directional_btc_15m_pct") <= btc15_q25,
        ),
        (
            "btc_adverse_shock_5m",
            lambda row: (
                val(row, "directional_btc_5m_pct") <= btc5_q25
                and val(row, "btc_5m_shock_z") >= shock_q75
            ),
        ),
        (
            "btc_adverse_high_coupling",
            lambda row: (
                val(row, "directional_btc_15m_pct") <= btc15_q25
                and val(row, "alt_btc_corr_3h") >= corr_q75
            ),
        ),
        (
            "btc_adverse_high_coupling_no_residual",
            lambda row: (
                val(row, "directional_btc_15m_pct") <= btc15_q25
                and val(row, "alt_btc_corr_3h") >= corr_q75
                and val(row, "directional_alt_btc_residual_15m_pct") <= residual_median
            ),
        ),
        (
            "btc_and_eth_both_adverse",
            lambda row: (
                val(row, "directional_btc_15m_pct") <= btc15_q25
                and val(row, "directional_eth_15m_pct") <= eth15_q25
            ),
        ),
        (
            "decoupling_override_candidate",
            lambda row: (
                val(row, "directional_btc_15m_pct") <= btc15_q25
                and val(row, "directional_alt_btc_residual_15m_pct") >= residual_q75
            ),
        ),
    ]
    return [veto_metrics(rows, name, predicate) for name, predicate in candidates]


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = fieldnames or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: f(value) if isinstance(value, float) else value for key, value in row.items()})


def markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# P44 UNI/LINK MARKET REGIME PROXY PROBE",
        "",
        "Исследование использует только данные, доступные до exact touch: BTC/ETH/ALT 5m klines.",
        "BTC.D, TOTAL3 и USDT.D в этот проход намеренно не подменяются непроверенным proxy.",
        "",
    ]
    for symbol, block in summary["symbols"].items():
        overall = block["overall"]
        lines.extend(
            [
                f"## {symbol}",
                "",
                f"- core signals: {overall['signals']}",
                f"- favorable +0.5/-1: {overall['good_percent']}%",
                "- потенциальные veto/override ниже являются только исследовательскими состояниями.",
                "",
                "| candidate | blocked | bad blocked % | good blocked % | precision bad % | efficiency |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in block["veto_candidates"]:
            efficiency = row["veto_efficiency_ratio"]
            efficiency_text = "" if efficiency is None else str(efficiency)
            lines.append(
                f"| {row['candidate']} | {row['blocked_signals']} | "
                f"{row['bad_entries_blocked_percent']} | {row['good_entries_blocked_percent']} | "
                f"{row['blocked_bad_precision_percent']} | {efficiency_text} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Ограничение",
            "",
            "Этот probe не является новой Entry-версией и ничего не включает в live. "
            "Он нужен, чтобы понять, есть ли у BTC/ETH regime достаточно сильная асимметрия "
            "между плохими и хорошими core Entry, прежде чем добавлять точные dominance/breadth series.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    start = parse_dt(args.start)
    end = parse_dt(args.end)
    fetch_start = start - timedelta(hours=LOOKBACK_HOURS)
    if args.output_dir is None:
        stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        output_dir = root / "reports" / "market_regime_v1" / f"UNI_LINK_{stamp}"
    else:
        output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / "dataset"

    inputs = discover_inputs(root, args.uni_core, args.link_core)
    print("P44 MARKET REGIME PROXY PROBE")
    print(f"Period: {iso_utc(start)} .. {iso_utc(end)}")
    for item in inputs:
        print(f"{item.symbol} core: {item.path}")

    btc = load_series(cache_dir, args.endpoint, "BTCUSDT", fetch_start, end)
    eth = load_series(cache_dir, args.endpoint, "ETHUSDT", fetch_start, end)

    all_features: list[dict[str, Any]] = []
    symbol_summary: dict[str, Any] = {}
    quartile_rows: list[dict[str, Any]] = []
    veto_rows: list[dict[str, Any]] = []
    feature_names = [
        "directional_btc_5m_pct",
        "directional_btc_15m_pct",
        "directional_btc_60m_pct",
        "directional_eth_15m_pct",
        "directional_eth_minus_btc_15m_pct",
        "alt_btc_corr_1h",
        "alt_btc_corr_3h",
        "directional_alt_btc_residual_15m_pct",
        "directional_alt_btc_residual_60m_pct",
        "btc_5m_shock_z",
    ]

    for item in inputs:
        alt = load_series(cache_dir, args.endpoint, item.symbol, fetch_start, end)
        features = build_features(item, alt, btc, eth)
        all_features.extend(features)
        local_quartiles: list[dict[str, Any]] = []
        for name in feature_names:
            local_quartiles.extend(quartile_report(features, name))
        for row in local_quartiles:
            row["symbol"] = item.symbol
        local_vetos = candidate_vetos(features)
        for row in local_vetos:
            row["symbol"] = item.symbol
        quartile_rows.extend(local_quartiles)
        veto_rows.extend(local_vetos)
        symbol_summary[item.symbol] = {
            "core_file": str(item.path),
            "feature_rows": len(features),
            "overall": outcome_metrics(features),
            "thresholds": thresholds(features),
            "veto_candidates": local_vetos,
        }

    summary = {
        "architecture": "p44_market_regime_proxy_v1",
        "research_only": True,
        "evaluation_start": iso_utc(start),
        "evaluation_end": iso_utc(end),
        "data": {
            "exchange": "Bybit",
            "endpoint": args.endpoint,
            "interval": "5m",
            "lookahead": "none; only fully completed candles before exact touch",
            "included": ["BTCUSDT", "ETHUSDT", "ALT/BTC rolling relation"],
            "not_included_yet": ["BTC.D", "TOTAL3", "USDT.D"],
        },
        "symbols": symbol_summary,
        "interpretation": [
            "Candidate veto rows are outcome-independent quantile states, not tuned trading thresholds.",
            "A useful veto should block a materially larger fraction of bad entries than good entries.",
            "Decoupling override is intentionally reported so BTC protection does not suppress independent alt waves.",
            "No P44 result is automatically promoted to Entry V1 or live execution.",
        ],
    }

    write_csv(output_dir / "regime_features.csv", all_features)
    write_csv(output_dir / "feature_quartiles.csv", quartile_rows)
    write_csv(output_dir / "veto_candidates.csv", veto_rows)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "summary.md").write_text(markdown_summary(summary), encoding="utf-8")

    print(f"Feature rows: {len(all_features)}")
    print(f"Report: {output_dir / 'summary.json'}")
    print(f"Readable summary: {output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
