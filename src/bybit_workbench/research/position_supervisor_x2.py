from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

ENGINE_VERSION = "position-supervisor-x2-preflight-v1"
MANDATORY_POST_FILL = ("price_path", "structure", "flow", "absorption", "orderbook", "oi_price")


@dataclass(frozen=True)
class CoverageRow:
    symbol: str
    direction: str
    fill_at: str
    fill_price: str
    price_path: str
    structure: str
    flow: str
    absorption: str
    orderbook: str
    oi_price: str
    ready: bool
    reason: str


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _fingerprint(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path).encode())
        digest.update(str(path.stat().st_size).encode())
        with path.open("rb") as stream:
            digest.update(stream.read(1_048_576))
    return digest.hexdigest()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _period_fraction(rows: list[dict[str, str]], fraction: float) -> list[dict[str, str]]:
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    ordered = sorted(rows, key=lambda row: _parse(row["fill_at"]))
    if fraction == 1 or len(ordered) < 2:
        return ordered
    start, end = _parse(ordered[0]["fill_at"]), _parse(ordered[-1]["fill_at"])
    boundary = start + (end - start) * fraction
    return [row for row in ordered if _parse(row["fill_at"]) <= boundary]


def _events_by_trade(path: Path | None, time_field: str) -> set[tuple[str, str, str]]:
    if path is None:
        return set()
    events = _read_csv(path)
    required = {"symbol", "direction", "fill_at", time_field}
    if events and not required.issubset(events[0]):
        raise ValueError(f"{path} lacks columns: {sorted(required - set(events[0]))}")
    found: set[tuple[str, str, str]] = set()
    for row in events:
        if row.get(time_field) and _parse(row[time_field]) >= _parse(row["fill_at"]):
            found.add((row["symbol"], row["direction"], row["fill_at"]))
    return found


def run(args: argparse.Namespace) -> dict[str, object]:
    cohort = _read_csv(args.cohort)
    required = {"symbol", "direction", "fill_at", "fill_price"}
    if not cohort or not required.issubset(cohort[0]):
        raise ValueError(
            f"cohort lacks columns: {sorted(required - set(cohort[0]) if cohort else required)}"
        )
    selected = [row for row in cohort if row["symbol"] == args.symbol]
    selected = _period_fraction(selected, args.fraction)
    event_sets = {
        name: _events_by_trade(getattr(args, name), "observed_at") for name in MANDATORY_POST_FILL
    }
    coverage: list[CoverageRow] = []
    for row in selected:
        trade = (row["symbol"], row["direction"], row["fill_at"])
        states = {name: "есть" if trade in events else "нет" for name, events in event_sets.items()}
        missing = [name for name, state in states.items() if state == "нет"]
        coverage.append(
            CoverageRow(
                symbol=row["symbol"],
                direction=row["direction"],
                fill_at=row["fill_at"],
                fill_price=row["fill_price"],
                **states,
                ready=not missing,
                reason="готово" if not missing else "нет post-fill слоёв: " + ", ".join(missing),
            )
        )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with (args.output_dir / "coverage.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(asdict(coverage[0]).keys()) if coverage else ["symbol"]
        )
        writer.writeheader()
        writer.writerows(asdict(row) for row in coverage)
    ready = sum(row.ready for row in coverage)
    inputs = [args.cohort] + [
        getattr(args, name) for name in MANDATORY_POST_FILL if getattr(args, name)
    ]
    summary: dict[str, object] = {
        "status": "ГОТОВО" if ready == len(coverage) and coverage else "ЗАБЛОКИРОВАНО",
        "engine_version": ENGINE_VERSION,
        "symbol": args.symbol,
        "fraction_of_period": args.fraction,
        "selected_trades": len(coverage),
        "ready_trades": ready,
        "blocked_trades": len(coverage) - ready,
        "downloads": "DISABLED",
        "input_fingerprint": _fingerprint(inputs),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "REPORT_RU.md").write_text(
        "# X2 — проверка готовности причинных данных\n\n"
        f"- Статус: **{summary['status']}**\n"
        f"- Монета: **{args.symbol}**\n"
        f"- Сделок в выбранных первых {args.fraction:.0%} периода: **{len(coverage)}**\n"
        f"- Полностью обеспечены обязательными post-fill событиями: **{ready}**\n"
        f"- Заблокированы: **{len(coverage) - ready}**\n\n"
        "Отсутствующий слой не заменяется нейтральным значением. Загрузки отключены.\n",
        encoding="utf-8",
    )
    return summary


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="X2 causal post-fill data readiness and replay gate"
    )
    result.add_argument("--cohort", type=Path, required=True)
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--symbol", required=True)
    result.add_argument("--fraction", type=float, default=1.0)
    for name in MANDATORY_POST_FILL:
        result.add_argument(f"--{name.replace('_', '-')}", dest=name, type=Path)
    return result


def main() -> None:
    summary = run(parser().parse_args())
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
