from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .engine import PositionSupervisor
from .models import PositionIdentity


@dataclass(frozen=True)
class ExchangePosition:
    position_id: str
    symbol: str
    side: str
    actual_avg_fill: Decimal
    qty: Decimal
    fill_time: datetime
    leverage: Decimal
    break_even_price: Decimal | None

    def identity(self) -> PositionIdentity:
        return PositionIdentity(**self.__dict__)


class SupervisorRegistry:
    """Exchange-truth lifecycle; local flat cannot override an exchange position."""

    def __init__(self) -> None:
        self._items: dict[str, PositionSupervisor] = {}

    def reconcile(self, positions: Iterable[ExchangePosition]) -> tuple[set[str], set[str]]:
        actual = {item.position_id: item for item in positions}
        removed = set(self._items) - set(actual)
        for position_id in removed:
            del self._items[position_id]
        created: set[str] = set()
        for position_id, position in actual.items():
            current = self._items.get(position_id)
            if current is None or current.identity != position.identity():
                self._items[position_id] = PositionSupervisor(position.identity())
                created.add(position_id)
        return created, removed

    def get(self, position_id: str) -> PositionSupervisor:
        return self._items[position_id]

    def ids(self) -> set[str]:
        return set(self._items)
