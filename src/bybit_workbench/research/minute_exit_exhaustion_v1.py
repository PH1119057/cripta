from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Direction = Literal["Long", "Short"]
ExitAction = Literal["держать", "наблюдать_внимательно", "выйти"]


@dataclass(frozen=True, slots=True)
class ExitObservation:
    direction: Direction
    current_profit_pct: float
    maximum_profit_pct: float
    local_barrier_reached: bool
    continuation_failed: bool
    minute_structure_broken: bool
    aggressive_flow_against: bool
    opposing_book_restored: bool


@dataclass(frozen=True, slots=True)
class ExitDecision:
    action: ExitAction
    explanation_ru: str
    giveback_pct: float
    confirmations: int


def decide_exit(observation: ExitObservation) -> ExitDecision:
    """Причинное решение без обязательного ожидания +1,10%."""
    if observation.maximum_profit_pct < observation.current_profit_pct:
        raise ValueError("максимальная прибыль не может быть меньше текущей")
    giveback = observation.maximum_profit_pct - observation.current_profit_pct
    confirmations = sum(
        (
            observation.continuation_failed,
            observation.minute_structure_broken,
            observation.aggressive_flow_against,
            observation.opposing_book_restored,
        )
    )

    # Одной перекупленности или одной стенки в стакане недостаточно.
    # Выход требует достигнутой локальной преграды, неудачи продолжения и
    # подтверждения либо сломом структуры, либо одновременно потоком и стаканом.
    confirmed_failure = observation.minute_structure_broken or (
        observation.aggressive_flow_against and observation.opposing_book_restored
    )
    if (
        observation.local_barrier_reached
        and observation.continuation_failed
        and confirmed_failure
    ):
        return ExitDecision(
            "выйти",
            "Локальная зона удержалась, продолжение не состоялось, минутная "
            "структура или поток со стаканом подтвердили отказ.",
            giveback,
            confirmations,
        )
    if observation.local_barrier_reached and confirmations >= 1:
        return ExitDecision(
            "наблюдать_внимательно",
            "Цена у локальной преграды и появился первый признак истощения, "
            "но подтверждений для выхода ещё недостаточно.",
            giveback,
            confirmations,
        )
    return ExitDecision(
        "держать",
        "Локальное движение ещё не доказало истощение — позицию сохраняем.",
        giveback,
        confirmations,
    )
