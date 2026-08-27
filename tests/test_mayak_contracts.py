from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.mayak.core.contracts import (
    MayakDataStatus,
    MayakMarketContext,
    MayakObservation,
    MayakProvenance,
    MayakSeaState,
)


def provenance() -> MayakProvenance:
    return MayakProvenance("0.1.0", "0.8.5", "d" * 64, "f" * 64, ("BTCUSDT",))


def context(status: MayakDataStatus, state: MayakSeaState) -> MayakMarketContext:
    return MayakMarketContext(
        observed_at=datetime(2026, 8, 20, tzinfo=UTC),
        context_version="1",
        core_version="0.1.0",
        data_status=status,
        data_confidence=1.0,
        freshness=timedelta(0),
        market_direction=None,
        market_velocity=None,
        market_acceleration=None,
        directional_agreement=None,
        btc={},
        eth={},
        breadth={},
        synchronization={},
        synchronization_persistence={},
        dispersion=None,
        normalized_displacement=None,
        sea_state=state,
        score=None,
        previous_state=None,
        regime_started_at=None,
        time_in_regime=None,
        transition_speed=None,
        normalization_progress=None,
        provenance=provenance(),
    )


def test_data_status_and_sea_state_are_independent() -> None:
    assert context(MayakDataStatus.VALID, MayakSeaState.STORM).sea_state is MayakSeaState.STORM
    with pytest.raises(ValueError, match="invalid data"):
        context(MayakDataStatus.STALE, MayakSeaState.CALM)


def test_observation_rejects_unclosed_bar() -> None:
    now = datetime(2026, 8, 20, tzinfo=UTC)
    with pytest.raises(ValueError, match="before it closes"):
        MayakObservation("BTCUSDT", now, now + timedelta(minutes=1), 1.0, 0.0)


def test_mayak_has_no_execution_or_private_client_imports() -> None:
    root = Path(__file__).parents[1] / "src" / "bybit_workbench" / "mayak"
    forbidden = ("execution", "private", "credential", "order")
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not any(word in name.lower() for name in imports for word in forbidden), path
