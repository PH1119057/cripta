import unittest
from decimal import Decimal

from bybit_workbench.domain.types import PositionSide
from bybit_workbench.stops import (
    ATRStop,
    DistanceStop,
    PercentStop,
    ProtectionLevel,
    ProtectionLevelStatus,
    RiskExpansionError,
    StopContext,
    TrailingDistanceStop,
    TrailingPercentStop,
    validate_stop_update,
)


def stop_context(side: PositionSide, **overrides: object) -> StopContext:
    values: dict[str, object] = {
        "side": side,
        "entry_price": Decimal("100"),
        "reference_price": Decimal("100"),
        "tick_size": Decimal("0.1"),
    }
    values.update(overrides)
    return StopContext(**values)  # type: ignore[arg-type]


class StopPolicyTests(unittest.TestCase):
    def test_basic_initial_stop_policies(self) -> None:
        long = stop_context(PositionSide.LONG, atr=Decimal("2"))
        short = stop_context(PositionSide.SHORT, atr=Decimal("2"))
        self.assertEqual(PercentStop(Decimal("2")).calculate(long), Decimal("98"))
        self.assertEqual(DistanceStop(Decimal("3")).calculate(short), Decimal("103"))
        self.assertEqual(ATRStop(Decimal("1.5")).calculate(long), Decimal("97"))

    def test_long_trailing_stop_never_moves_down(self) -> None:
        policy = TrailingDistanceStop(Decimal("5"))
        current: Decimal | None = None
        for reference in map(Decimal, ("100", "103", "102", "110", "106")):
            updated = policy.calculate(
                stop_context(
                    PositionSide.LONG,
                    reference_price=reference,
                    current_stop=current,
                )
            )
            if current is not None:
                self.assertGreaterEqual(updated, current)
            current = updated

    def test_short_trailing_stop_never_moves_up(self) -> None:
        policy = TrailingPercentStop(Decimal("5"))
        current: Decimal | None = None
        for reference in map(Decimal, ("100", "97", "99", "90", "94")):
            updated = policy.calculate(
                stop_context(
                    PositionSide.SHORT,
                    reference_price=reference,
                    current_stop=current,
                )
            )
            if current is not None:
                self.assertLessEqual(updated, current)
            current = updated

    def test_sub_tick_change_is_deduplicated_by_normalization(self) -> None:
        policy = TrailingDistanceStop(Decimal("5"))
        current = Decimal("95.0")
        updated = policy.calculate(
            stop_context(
                PositionSide.LONG,
                reference_price=Decimal("100.09"),
                current_stop=current,
            )
        )
        self.assertEqual(updated, current)

    def test_invalid_atr_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ATRStop(Decimal("2")).calculate(stop_context(PositionSide.LONG))

    def test_risk_expansion_requires_explicit_permission(self) -> None:
        with self.assertRaises(RiskExpansionError):
            validate_stop_update(
                Decimal("95"),
                Decimal("94"),
                PositionSide.LONG,
            )
        validate_stop_update(
            Decimal("95"),
            Decimal("94"),
            PositionSide.LONG,
            allow_risk_expansion=True,
        )

    def test_protection_status_is_explicit(self) -> None:
        level = ProtectionLevel(Decimal("95"), ProtectionLevelStatus.PLANNED)
        self.assertEqual(level.status, ProtectionLevelStatus.PLANNED)


if __name__ == "__main__":
    unittest.main()
