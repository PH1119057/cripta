from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

Direction = Literal["Long", "Short"]
EntryAction = Literal["войти", "ждать", "нет_согласованной_зоны"]


@dataclass(frozen=True, slots=True)
class PriceZone:
    lower: float
    upper: float

    def __post_init__(self) -> None:
        if self.lower <= 0 or self.upper <= 0 or self.lower > self.upper:
            raise ValueError("границы зоны заданы неверно")


@dataclass(frozen=True, slots=True)
class EntryDecision:
    action: EntryAction
    baseline_price: float
    proposed_price: float | None
    explanation_ru: str
    minute_zone: PriceZone | None
    five_minute_zone: PriceZone | None


def zones_overlap(left: PriceZone, right: PriceZone) -> bool:
    return max(left.lower, right.lower) <= min(left.upper, right.upper)


def baseline_offset_price(entry_price: float, direction: Direction) -> float:
    if entry_price <= 0:
        raise ValueError("цена исходного входа должна быть положительной")
    return entry_price * (0.998 if direction == "Long" else 1.002)


def decide_entry(
    *,
    direction: Direction,
    entry_price: float,
    current_price: float,
    minute_zone: PriceZone | None,
    five_minute_zone: PriceZone | None,
) -> EntryDecision:
    """Решить, входить на -0,20% или ждать ближайшую согласованную минутную зону.

    Функция не строит зоны и не видит будущего. Она получает только зоны,
    рассчитанные по уже закрытым свечам на момент решения.
    """
    if current_price <= 0:
        raise ValueError("текущая цена должна быть положительной")
    baseline = baseline_offset_price(entry_price, direction)
    if minute_zone is None or five_minute_zone is None:
        return EntryDecision(
            action="нет_согласованной_зоны",
            baseline_price=baseline,
            proposed_price=None,
            explanation_ru=(
                "Минутная и пятиминутная зоны не подтверждают друг друга — "
                "вход не разрешён."
            ),
            minute_zone=minute_zone,
            five_minute_zone=five_minute_zone,
        )
    if not zones_overlap(minute_zone, five_minute_zone):
        return EntryDecision(
            action="нет_согласованной_зоны",
            baseline_price=baseline,
            proposed_price=None,
            explanation_ru="Минутная и пятиминутная зоны не пересекаются — ждём новую структуру.",
            minute_zone=minute_zone,
            five_minute_zone=five_minute_zone,
        )

    overlap_lower = max(minute_zone.lower, five_minute_zone.lower)
    overlap_upper = min(minute_zone.upper, five_minute_zone.upper)
    if direction == "Long":
        proposed = min(baseline, overlap_upper)
        deeper = proposed < baseline
        reached = current_price <= proposed
    else:
        proposed = max(baseline, overlap_lower)
        deeper = proposed > baseline
        reached = current_price >= proposed

    if reached:
        reason = (
            "Цена дошла до согласованной минутной и пятиминутной зоны; вход разрешён."
            if deeper
            else "Минутная структура не требует более глубокой цены; базовый вход −0,20% достигнут."
        )
        action: EntryAction = "войти"
    else:
        reason = (
            "Поддержка для Long находится ниже базового входа — продолжаем ждать."
            if direction == "Long"
            else "Сопротивление для Short находится выше базового входа — продолжаем ждать."
        )
        action = "ждать"
    return EntryDecision(action, baseline, proposed, reason, minute_zone, five_minute_zone)


def _optional_zone(row: dict[str, str], prefix: str) -> PriceZone | None:
    lower = row.get(f"{prefix}_lower", "").strip()
    upper = row.get(f"{prefix}_upper", "").strip()
    return None if not lower or not upper else PriceZone(float(lower), float(upper))


def run_csv(input_path: Path, output_path: Path) -> dict[str, int]:
    rows: list[dict[str, object]] = []
    counts: dict[str, int] = {"войти": 0, "ждать": 0, "нет_согласованной_зоны": 0}
    with input_path.open("r", newline="", encoding="utf-8-sig") as handle:
        for source in csv.DictReader(handle):
            decision = decide_entry(
                direction=source["direction"],  # type: ignore[arg-type]
                entry_price=float(source["entry_price"]),
                current_price=float(source["current_price"]),
                minute_zone=_optional_zone(source, "minute_zone"),
                five_minute_zone=_optional_zone(source, "five_minute_zone"),
            )
            counts[decision.action] += 1
            rows.append({**source, **asdict(decision)})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (output_path.parent / "ИТОГ.json").write_text(
        json.dumps({"решения": counts}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Минутное уточнение цены входа")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_csv(args.input, args.output), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
