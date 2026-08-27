from __future__ import annotations

import argparse
import csv
import json
import shutil
import ssl
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import certifi

from bybit_workbench.research.orderbook_plan_v7 import Direction, _parse_datetime

ARCHIVE_BASE = "https://quote-saver.bycsi.com/orderbook/linear"
DEPTH_CANDIDATES = (200, 500, 1000)
SAMPLE_OFFSETS_SECONDS = (-120, -60, -30, -10, 0, 10, 30, 60)
DEPTH_BANDS_BPS = (5, 10, 25, 50)


@dataclass(frozen=True, slots=True)
class PilotWindow:
    symbol: str
    direction: Direction
    candidate_bar_at: datetime
    touch_at: datetime
    entry_price: float
    day: date
    segment: int
    window_start: datetime
    window_end: datetime
    flow_state: str
    basis_accel_quartile: str
    first_0_5_vs_1_0: str
    first_1_0_vs_1_0: str


@dataclass(slots=True)
class BookState:
    bids: dict[str, float]
    asks: dict[str, float]
    ready: bool = False

    @classmethod
    def empty(cls) -> BookState:
        return cls(bids={}, asks={}, ready=False)

    def apply(self, record_type: str, data: dict[str, Any]) -> None:
        bids = _levels(data.get("b"))
        asks = _levels(data.get("a"))
        if record_type == "snapshot" or not self.ready:
            if record_type != "snapshot":
                return
            self.bids = {price: qty for price, qty in bids if qty > 0}
            self.asks = {price: qty for price, qty in asks if qty > 0}
            self.ready = True
            return
        if record_type != "delta":
            return
        _apply_delta(self.bids, bids)
        _apply_delta(self.asks, asks)


def _levels(value: Any) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, list) or len(item) < 2:
            continue
        price = str(item[0])
        try:
            qty = float(item[1])
        except (TypeError, ValueError):
            continue
        result.append((price, qty))
    return result


def _apply_delta(side: dict[str, float], levels: Iterable[tuple[str, float]]) -> None:
    for price, qty in levels:
        if qty <= 0:
            side.pop(price, None)
        else:
            side[price] = qty


def _event_timestamp(payload: dict[str, Any]) -> datetime | None:
    data = payload.get("data")
    data_map = data if isinstance(data, dict) else {}
    raw = payload.get("cts", data_map.get("cts", payload.get("ts", data_map.get("ts"))))
    if not isinstance(raw, (str, int, float)):
        return None
    try:
        millis = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000.0, tz=UTC)


def _normalize_event(payload: Any) -> tuple[str, datetime, dict[str, Any]] | None:
    if not isinstance(payload, dict):
        return None
    record_type = str(payload.get("type", "")).lower()
    if record_type not in {"snapshot", "delta"}:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    timestamp = _event_timestamp(payload)
    if timestamp is None:
        return None
    return record_type, timestamp, data


def archive_url(symbol: str, day: date, depth: int) -> str:
    filename = f"{day.isoformat()}_{symbol}_ob{depth}.data.zip"
    return f"{ARCHIVE_BASE}/{symbol}/{filename}"


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def probe_url(url: str, *, timeout: float = 20.0) -> dict[str, Any]:
    headers = {"User-Agent": "BybitStrategyWorkbench/0.8.5"}
    request = urllib.request.Request(url, headers=headers, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            return {
                "exists": 200 <= response.status < 400,
                "status": response.status,
                "content_length": _safe_int(response.headers.get("Content-Length")),
                "content_type": response.headers.get("Content-Type", ""),
                "url": url,
            }
    except urllib.error.HTTPError as exc:
        if exc.code not in {403, 405}:
            return {"exists": False, "status": exc.code, "url": url}
    except urllib.error.URLError as exc:
        return {"exists": False, "status": None, "error": str(exc), "url": url}

    range_request = urllib.request.Request(
        url,
        headers={**headers, "Range": "bytes=0-0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            range_request, timeout=timeout, context=_ssl_context()
        ) as response:
            return {
                "exists": response.status in {200, 206},
                "status": response.status,
                "content_length": _content_range_total(response.headers)
                or _safe_int(response.headers.get("Content-Length")),
                "content_type": response.headers.get("Content-Type", ""),
                "url": url,
            }
    except urllib.error.HTTPError as exc:
        return {"exists": False, "status": exc.code, "url": url}
    except urllib.error.URLError as exc:
        return {"exists": False, "status": None, "error": str(exc), "url": url}


def _safe_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _content_range_total(headers: Any) -> int | None:
    value = headers.get("Content-Range")
    if not value or "/" not in value:
        return None
    return _safe_int(str(value).rsplit("/", 1)[-1])


def discover_archive(symbol: str, day: date) -> dict[str, Any]:
    probes: list[dict[str, Any]] = []
    for depth in DEPTH_CANDIDATES:
        result = probe_url(archive_url(symbol, day, depth))
        result["depth"] = depth
        probes.append(result)
        if result.get("exists"):
            return {"selected": result, "probes": probes}
    return {"selected": None, "probes": probes}


def download_archive(url: str, target: Path, *, expected_size: int | None = None) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and (expected_size is None or target.stat().st_size == expected_size):
        print(f"Reuse orderbook archive: {target}")
        return target
    part = target.with_suffix(target.suffix + ".part")
    start = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "BybitStrategyWorkbench/0.8.5"}
    if start > 0:
        headers["Range"] = f"bytes={start}-"
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=60.0, context=_ssl_context()) as response:
        append = start > 0 and response.status == 206
        if start > 0 and not append:
            start = 0
        mode = "ab" if append else "wb"
        downloaded = start
        next_log = ((downloaded // (50 * 1024 * 1024)) + 1) * 50 * 1024 * 1024
        with part.open(mode) as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if downloaded >= next_log:
                    print(f"  {downloaded / (1024 * 1024):.0f} MiB downloaded")
                    next_log += 50 * 1024 * 1024
    if expected_size is not None and part.stat().st_size != expected_size:
        raise OSError(
            f"archive size mismatch: got {part.stat().st_size}, expected {expected_size}"
        )
    part.replace(target)
    return target


def _book_metrics(state: BookState, *, direction: Direction, entry_price: float) -> dict[str, Any]:
    if not state.ready or not state.bids or not state.asks:
        return {"book_ready": False}
    bids = [(float(price), qty) for price, qty in state.bids.items() if qty > 0]
    asks = [(float(price), qty) for price, qty in state.asks.items() if qty > 0]
    if not bids or not asks:
        return {"book_ready": False}
    best_bid = max(price for price, _ in bids)
    best_ask = min(price for price, _ in asks)
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0:
        return {"book_ready": False}
    result: dict[str, Any] = {
        "book_ready": True,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": (best_ask - best_bid) / mid * 10000.0,
        "entry_to_mid_bps": (entry_price / mid - 1.0) * 10000.0,
    }
    for band_bps in DEPTH_BANDS_BPS:
        fraction = band_bps / 10000.0
        bid_floor = mid * (1.0 - fraction)
        ask_ceiling = mid * (1.0 + fraction)
        bid_notional = sum(price * qty for price, qty in bids if price >= bid_floor)
        ask_notional = sum(price * qty for price, qty in asks if price <= ask_ceiling)
        total = bid_notional + ask_notional
        imbalance = 0.0 if total <= 0 else (bid_notional - ask_notional) / total
        directed = imbalance if direction == "Long" else -imbalance
        result[f"bid_notional_{band_bps}bps"] = bid_notional
        result[f"ask_notional_{band_bps}bps"] = ask_notional
        result[f"imbalance_{band_bps}bps"] = imbalance
        result[f"directional_imbalance_{band_bps}bps"] = directed

    support_side = bids if direction == "Long" else asks
    adverse_side = asks if direction == "Long" else bids
    result.update(_wall_metrics(support_side, mid, prefix="support"))
    result.update(_wall_metrics(adverse_side, mid, prefix="adverse"))
    return result


def _wall_metrics(levels: list[tuple[float, float]], mid: float, *, prefix: str) -> dict[str, Any]:
    within = [
        (price, qty, price * qty)
        for price, qty in levels
        if abs(price / mid - 1.0) * 10000.0 <= 50.0
    ]
    if not within:
        return {
            f"{prefix}_wall_notional": 0.0,
            f"{prefix}_wall_distance_bps": None,
        }
    price, _qty, notional = max(within, key=lambda row: row[2])
    return {
        f"{prefix}_wall_notional": notional,
        f"{prefix}_wall_distance_bps": abs(price / mid - 1.0) * 10000.0,
    }


def _sample_targets(windows: list[PilotWindow]) -> list[tuple[datetime, int, int]]:
    targets: list[tuple[datetime, int, int]] = []
    for window_index, window in enumerate(windows):
        for offset in SAMPLE_OFFSETS_SECONDS:
            targets.append((window.touch_at + timedelta(seconds=offset), window_index, offset))
    return sorted(targets, key=lambda item: item[0])


def analyze_archive(
    path: Path, windows: list[PilotWindow]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not windows:
        return [], {"records": 0, "snapshots": 0, "deltas": 0, "samples": 0}
    targets = _sample_targets(windows)
    first_target = targets[0][0]
    last_target = targets[-1][0]
    state = BookState.empty()
    captured: dict[tuple[int, int], dict[str, Any]] = {}
    target_index = 0
    records = snapshots = deltas = 0
    first_event: datetime | None = None
    last_event: datetime | None = None

    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if not members:
            raise ValueError(f"orderbook archive has no file: {path}")
        with archive.open(members[0], "r") as handle:
            for raw_line in handle:
                if (
                    target_index >= len(targets)
                    and last_event is not None
                    and last_event > last_target
                ):
                    break
                try:
                    payload = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                event = _normalize_event(payload)
                if event is None:
                    continue
                record_type, event_at, data = event
                records += 1
                first_event = first_event or event_at
                last_event = event_at

                while target_index < len(targets) and targets[target_index][0] < event_at:
                    target_at, window_index, offset = targets[target_index]
                    if target_at >= first_target:
                        captured[(window_index, offset)] = _book_metrics(
                            state,
                            direction=windows[window_index].direction,
                            entry_price=windows[window_index].entry_price,
                        )
                    target_index += 1

                state.apply(record_type, data)
                if record_type == "snapshot":
                    snapshots += 1
                elif record_type == "delta":
                    deltas += 1

                while target_index < len(targets) and targets[target_index][0] == event_at:
                    _target_at, window_index, offset = targets[target_index]
                    captured[(window_index, offset)] = _book_metrics(
                        state,
                        direction=windows[window_index].direction,
                        entry_price=windows[window_index].entry_price,
                    )
                    target_index += 1

    while target_index < len(targets):
        _target_at, window_index, offset = targets[target_index]
        captured[(window_index, offset)] = _book_metrics(
            state,
            direction=windows[window_index].direction,
            entry_price=windows[window_index].entry_price,
        )
        target_index += 1

    rows: list[dict[str, Any]] = []
    for index, window in enumerate(windows):
        row: dict[str, Any] = {
            "symbol": window.symbol,
            "direction": window.direction,
            "candidate_bar_at": window.candidate_bar_at.isoformat(),
            "touch_at": window.touch_at.isoformat(),
            "entry_price": window.entry_price,
            "day": window.day.isoformat(),
            "segment": window.segment,
            "flow_state": window.flow_state,
            "basis_accel_quartile": window.basis_accel_quartile,
            "first_0_5_vs_1_0": window.first_0_5_vs_1_0,
            "first_1_0_vs_1_0": window.first_1_0_vs_1_0,
        }
        for offset in SAMPLE_OFFSETS_SECONDS:
            metrics = captured.get((index, offset), {"book_ready": False})
            suffix = _offset_suffix(offset)
            for key, value in metrics.items():
                row[f"{key}_{suffix}"] = value
        row.update(_dynamic_features(row))
        rows.append(row)

    stats = {
        "archive": str(path),
        "records": records,
        "snapshots": snapshots,
        "deltas": deltas,
        "samples": len(captured),
        "first_event": first_event.isoformat() if first_event else None,
        "last_event": last_event.isoformat() if last_event else None,
    }
    return rows, stats


def _offset_suffix(offset: int) -> str:
    return f"m{abs(offset)}s" if offset < 0 else f"p{offset}s"


def _dynamic_features(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for band in DEPTH_BANDS_BPS:
        key = f"directional_imbalance_{band}bps"
        before = _float_or_none(row.get(f"{key}_m30s"))
        touch = _float_or_none(row.get(f"{key}_p0s"))
        if before is not None and touch is not None:
            result[f"{key}_change_m30_to_touch"] = touch - before
    support_before = _float_or_none(row.get("support_wall_notional_m30s"))
    support_touch = _float_or_none(row.get("support_wall_notional_p0s"))
    if support_before is not None and support_touch is not None and support_before > 0:
        result["support_wall_notional_ratio_m30_to_touch"] = support_touch / support_before
    spread_before = _float_or_none(row.get("spread_bps_m30s"))
    spread_touch = _float_or_none(row.get("spread_bps_p0s"))
    if spread_before is not None and spread_touch is not None:
        result["spread_change_m30_to_touch_bps"] = spread_touch - spread_before
    return result


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_windows(p37_dir: Path, p36_dir: Path, *, pilot_only: bool = True) -> list[PilotWindow]:
    pilot_days = {
        row["day"]
        for row in csv.DictReader((p37_dir / "pilot_days.csv").open("r", encoding="utf-8-sig"))
    }
    outcomes: dict[tuple[str, str], tuple[str, str]] = {}
    with (p36_dir / "signals_basis.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            outcomes[(row["direction"], row["touch_at"])] = (
                row["first_0_5_vs_1_0"],
                row["first_1_0_vs_1_0"],
            )

    windows: list[PilotWindow] = []
    with (p37_dir / "orderbook_windows.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if pilot_only and row["day"] not in pilot_days:
                continue
            outcome = outcomes.get((row["direction"], row["touch_at"]), ("", ""))
            windows.append(
                PilotWindow(
                    symbol=row["symbol"],
                    direction=cast(Direction, row["direction"]),
                    candidate_bar_at=_parse_datetime(row["candidate_bar_at"]),
                    touch_at=_parse_datetime(row["touch_at"]),
                    entry_price=float(row["entry_price"]),
                    day=date.fromisoformat(row["day"]),
                    segment=int(row["segment"]),
                    window_start=_parse_datetime(row["window_start"]),
                    window_end=_parse_datetime(row["window_end"]),
                    flow_state=row["flow_state"],
                    basis_accel_quartile=row["basis_accel_quartile"],
                    first_0_5_vs_1_0=outcome[0],
                    first_1_0_vs_1_0=outcome[1],
                )
            )
    return windows


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
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _latest_dir(root: Path, report_name: str, required_file: str) -> Path:
    base = root / "reports" / report_name
    candidates = [path for path in base.glob("UNIUSDT_*") if (path / required_file).is_file()]
    if not candidates:
        raise FileNotFoundError(f"no completed {report_name} result found")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _default_output_dir(root: Path, symbol: str) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    return root / "reports" / "entry_research_v11" / f"{symbol}_{stamp}"


def run_pilot(
    *,
    p37_dir: Path,
    output_dir: Path,
    archive_dir: Path | None,
    keep_archives: bool,
    probe_only: bool,
) -> dict[str, Any]:
    p37_summary = json.loads((p37_dir / "summary.json").read_text(encoding="utf-8"))
    p36_dir = Path(str(p37_summary["p36_dir"]))
    windows = load_windows(p37_dir, p36_dir, pilot_only=True)
    if not windows:
        raise ValueError("P37 pilot plan has no windows")
    symbol = windows[0].symbol
    days = sorted({window.day for window in windows})
    if archive_dir is None:
        p36_summary = json.loads((p36_dir / "summary.json").read_text(encoding="utf-8"))
        dataset_dir = Path(str(p36_summary["dataset_dir"]))
        archive_dir = dataset_dir / "orderbook_pilot"
    archive_dir.mkdir(parents=True, exist_ok=True)

    probes: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    archive_stats: list[dict[str, Any]] = []
    for index, day in enumerate(days, start=1):
        print(f"P38 orderbook pilot day {index}/{len(days)}: {day}")
        discovery = discover_archive(symbol, day)
        probes.append({"day": day.isoformat(), **discovery})
        selected = discovery["selected"]
        if selected is None:
            print(f"  no historical orderbook archive found for {day}")
            continue
        if probe_only:
            size_label = selected.get("content_length")
            print(f"  archive available: ob{selected['depth']} size={size_label}")
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
        print(f"  download {filename}")
        archive_path = download_archive(url, target, expected_size=size)
        day_windows = [window for window in windows if window.day == day]
        rows, stats = analyze_archive(archive_path, day_windows)
        for row in rows:
            row["archive_depth"] = selected["depth"]
            row["archive_bytes"] = archive_path.stat().st_size
        all_rows.extend(rows)
        archive_stats.append({"day": day.isoformat(), **stats, "depth": selected["depth"]})
        if not keep_archives:
            archive_path.unlink(missing_ok=True)
            print(f"  processed and removed raw archive: {filename}")

    _write_csv(output_dir / "orderbook_pilot_features.csv", all_rows)
    result = {
        "architecture": "p38_orderbook_pilot_microstructure",
        "p37_dir": str(p37_dir),
        "p36_dir": str(p36_dir),
        "pilot_days": [day.isoformat() for day in days],
        "pilot_windows": len(windows),
        "features_rows": len(all_rows),
        "probe_only": probe_only,
        "keep_archives": keep_archives,
        "archive_dir": str(archive_dir),
        "archive_probes": probes,
        "archive_stats": archive_stats,
        "notes": [
            "P38 changes no live trading, stop-loss, take-profit, or exit logic.",
            "The pilot validates historical orderbook availability "
            "and reconstruction on three separated days.",
            "Only book state at or before each sample timestamp is used for pre-touch features.",
            "Post-touch samples are diagnostic only and are not executable entry information.",
            "No orderbook feature becomes a gate or score from this pilot sample.",
        ],
    }
    _write_json(output_dir / "summary.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P38 historical orderbook pilot microstructure")
    parser.add_argument("--p37-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--keep-archives", action="store_true")
    parser.add_argument("--probe-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path.cwd()
    p37_dir = args.p37_dir or _latest_dir(root, "entry_research_v10", "summary.json")
    output_dir = args.output_dir or _default_output_dir(root, "UNIUSDT")
    result = run_pilot(
        p37_dir=p37_dir,
        output_dir=output_dir,
        archive_dir=args.archive_dir,
        keep_archives=args.keep_archives,
        probe_only=args.probe_only,
    )
    print(f"P37 source: {p37_dir}")
    print(f"Pilot windows planned: {result['pilot_windows']}")
    print(f"Pilot feature rows: {result['features_rows']}")
    print(f"Report: {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
