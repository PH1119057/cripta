"""Transaction boundary for one source fact shared by ordinary and P90 PAPER.

The caller owns rebuilding an entire in-memory Observer epoch on failure.
All consumer persistence must use this SAME connection, without committing
independently. The ordinary-only branch is unchanged.
"""
from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any


def dispatch_observer_fact_atomically[T](
    connection: Any,
    *,
    symbol: str,
    p90_symbols: Collection[str],
    p90_enabled: bool,
    dispatch: Callable[[], T],
) -> T:
    if p90_enabled and symbol in p90_symbols:
        with connection.transaction():
            return dispatch()
    return dispatch()
