from __future__ import annotations

from dataclasses import dataclass


class ContextClass:
    MARKET_FACT = "MARKET_FACT"
    MAYAK_CONTEXT = "MAYAK_CONTEXT"
    DISPATCHER_GLOBAL_CONTEXT = "DISPATCHER_GLOBAL_CONTEXT"
    DISPATCHER_COIN_CONTEXT = "DISPATCHER_COIN_CONTEXT"
    TRADING_CAPACITY = "TRADING_CAPACITY"
    TECHNICAL_READINESS = "TECHNICAL_READINESS"


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    context_id: str
    context_class: str
    description: str


class SensorContextCatalog:
    def __init__(self, entries: tuple[CatalogEntry, ...] = ()) -> None:
        self._entries = {entry.context_id: entry for entry in entries}

    def register(self, entry: CatalogEntry) -> None:
        existing = self._entries.get(entry.context_id)
        if existing is not None and existing != entry:
            raise ValueError(f"catalog id already registered: {entry.context_id}")
        self._entries[entry.context_id] = entry

    def require(self, context_id: str) -> CatalogEntry:
        try:
            return self._entries[context_id]
        except KeyError as exc:
            raise KeyError(f"unknown sensor/context id: {context_id}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))
