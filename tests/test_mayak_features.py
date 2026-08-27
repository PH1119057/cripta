from __future__ import annotations

import pytest

from bybit_workbench.mayak.core.features import (
    acceleration,
    agreement_persistence,
    breadth,
    directional_agreement,
    normalized_displacement,
    pearson,
    realised_volatility,
    simple_return,
    synchronization,
    velocity,
)


def test_return_velocity_acceleration_and_volatility() -> None:
    assert simple_return(100.0, 101.0) == pytest.approx(0.01)
    assert velocity((0.01, 0.03)) == pytest.approx(0.02)
    assert acceleration((0.01, 0.01, 0.03, 0.03)) == pytest.approx(0.02)
    assert realised_volatility((100.0, 101.0, 100.0)) > 0


def test_normalized_displacement_uses_recent_scale() -> None:
    assert normalized_displacement(0.02, (0.01, -0.01, 0.01, -0.01)) > 1.0


def test_breadth_leave_one_out_and_direction() -> None:
    values = {"BTC": 0.01, "ETH": 0.02, "TARGET": -0.03}
    result = breadth(values, excluded_symbol="TARGET")
    assert result["up_share"] == 1.0
    assert directional_agreement(values) == pytest.approx(2 / 3)


def test_synchronization_and_persistence() -> None:
    assert pearson((1.0, 2.0, 3.0), (2.0, 4.0, 6.0)) == pytest.approx(1.0)
    assert synchronization({"A": (1.0, 2.0), "B": (2.0, 4.0)}) == pytest.approx(1.0)
    assert agreement_persistence((0.8, 0.5, 0.7, 0.9), 0.6) == 2.0
