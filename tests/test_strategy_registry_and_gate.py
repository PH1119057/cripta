import unittest
from dataclasses import dataclass
from decimal import Decimal

from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain import InstrumentRules
from bybit_workbench.domain.types import AppMode, AppState
from bybit_workbench.historical import (
    HistoricalEligibilityGate,
    HistoricalEligibilityQuery,
    HistoricalEligibilityRecord,
    eligibility_binding_fingerprint,
    parameters_fingerprint,
)
from bybit_workbench.strategies import (
    ManualProtectedTrade,
    ParameterType,
    StrategyArmingService,
    StrategyKind,
    StrategyParameter,
    StrategyRegistration,
    StrategyRegistry,
    default_strategy_registry,
)
from bybit_workbench.strategies.base import StrategyMetadata


@dataclass
class EligibilityStub:
    result: bool | None
    last_call: tuple[str, str, str, HistoricalEligibilityQuery] | None = None

    def latest_historical_eligibility(
        self,
        strategy_id: str,
        strategy_version: str,
        parameters_fingerprint: str,
        query: HistoricalEligibilityQuery,
    ) -> HistoricalEligibilityRecord | None:
        self.last_call = (strategy_id, strategy_version, parameters_fingerprint, query)
        if self.result is None:
            return None
        dataset_fingerprint = "d" * 64
        binding = eligibility_binding_fingerprint(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            parameters_fingerprint=parameters_fingerprint,
            query=query,
            dataset_fingerprint=dataset_fingerprint,
        )
        return HistoricalEligibilityRecord(
            "fixture-report",
            self.result,
            dataset_fingerprint,
            binding,
            True,
        )


def eligibility_query() -> HistoricalEligibilityQuery:
    rules = InstrumentRules(
        "BTCUSDT",
        Decimal("0.1"),
        Decimal("0.001"),
        Decimal("0.001"),
        Decimal("5"),
        Decimal("1000"),
    )
    return HistoricalEligibilityQuery.from_instrument(
        symbol="BTCUSDT",
        timeframe="60",
        code_version="test-code",
        instrument_rules=rules,
        maker_fee_rate=Decimal("0.0002"),
        taker_fee_rate=Decimal("0.00055"),
        slippage_percent=Decimal("0.1"),
    )


def ready_machine() -> AppStateMachine:
    machine = AppStateMachine()
    machine.transition(AppState.SYNCING, "test sync")
    machine.transition(AppState.READY, "test ready")
    return machine


def automatic_registration() -> StrategyRegistration:
    return StrategyRegistration(
        StrategyMetadata("fixture-auto", "2.1", "Fixture automatic"),
        StrategyKind.AUTOMATIC,
        ManualProtectedTrade,
        (
            StrategyParameter("period", "Period", ParameterType.INTEGER, 20),
            StrategyParameter("risk", "Risk", ParameterType.DECIMAL, Decimal("0.5")),
        ),
    )


class StrategyRegistryAndGateTests(unittest.TestCase):
    def test_default_registry_registers_approved_user_algorithms(self) -> None:
        registry = default_strategy_registry()
        manual = registry.get("manual_protected_trade")
        self.assertFalse(manual.requires_historical_validation)
        self.assertIsInstance(registry.create("manual_protected_trade"), ManualProtectedTrade)
        for strategy_id in ("user_algorithm_1", "user_algorithm_2"):
            registration = registry.get(strategy_id)
            self.assertEqual(registration.kind, StrategyKind.AUTOMATIC)
            self.assertEqual(registration.metadata.version, "0.2.0")
            self.assertEqual(registry.create(strategy_id).metadata(), registration.metadata)

    def test_automatic_registration_cannot_bypass_history(self) -> None:
        with self.assertRaises(ValueError):
            StrategyRegistration(
                StrategyMetadata("unsafe", "1", "Unsafe"),
                StrategyKind.AUTOMATIC,
                ManualProtectedTrade,
                requires_historical_validation=False,
            )

    def test_gate_requires_matching_version_and_parameters(self) -> None:
        store = EligibilityStub(True)
        registration = automatic_registration()
        parameters = {"period": 30, "risk": Decimal("0.25")}
        query = eligibility_query()
        decision = HistoricalEligibilityGate(store).require(registration, parameters, query)
        self.assertTrue(decision.allowed)
        self.assertEqual(
            store.last_call,
            ("fixture-auto", "2.1", parameters_fingerprint(parameters), query),
        )

    def test_tampered_binding_fingerprint_blocks_automatic_gate(self) -> None:
        class TamperedStore:
            def latest_historical_eligibility(
                self,
                strategy_id: str,
                strategy_version: str,
                parameters_fingerprint: str,
                query: HistoricalEligibilityQuery,
            ) -> HistoricalEligibilityRecord:
                del strategy_id, strategy_version, parameters_fingerprint, query
                return HistoricalEligibilityRecord(
                    "tampered", True, "d" * 64, "b" * 64, True
                )

        decision = HistoricalEligibilityGate(TamperedStore()).evaluate(
            automatic_registration(), {"period": 30}, eligibility_query()
        )
        self.assertFalse(decision.allowed)
        self.assertIn("binding fingerprint", decision.reason)

    def test_missing_or_failed_report_blocks_automatic_arming(self) -> None:
        for result in (None, False):
            registry = StrategyRegistry()
            registry.register(automatic_registration())
            service = StrategyArmingService(
                registry,
                HistoricalEligibilityGate(EligibilityStub(result)),
                ready_machine(),
            )
            with self.assertRaises(PermissionError):
                service.arm(
                    "fixture-auto",
                    None,
                    mode=AppMode.TESTNET,
                    historical_query=eligibility_query(),
                )

    def test_eligible_report_arms_testnet_and_manual_is_explicitly_exempt(self) -> None:
        registry = StrategyRegistry()
        registry.register(automatic_registration())
        machine = ready_machine()
        service = StrategyArmingService(
            registry,
            HistoricalEligibilityGate(EligibilityStub(True)),
            machine,
        )
        armed = service.arm(
            "fixture-auto",
            {"period": 30},
            mode=AppMode.TESTNET,
            historical_query=eligibility_query(),
        )
        self.assertEqual(armed.parameters["period"], 30)
        self.assertEqual(machine.state, AppState.ARMED)

        manual = default_strategy_registry().get("manual_protected_trade")
        decision = HistoricalEligibilityGate(EligibilityStub(None)).require(manual, {})
        self.assertTrue(decision.allowed)

    def test_approved_algorithm_arms_testnet_and_non_testnet_is_blocked(self) -> None:
        registry = default_strategy_registry()
        service = StrategyArmingService(
            registry,
            HistoricalEligibilityGate(EligibilityStub(True)),
            ready_machine(),
        )
        armed = service.arm(
            "user_algorithm_1",
            None,
            mode=AppMode.TESTNET,
            historical_query=eligibility_query(),
        )
        self.assertEqual(armed.strategy_version, "0.2.0")
        with self.assertRaises(PermissionError):
            service.arm("manual_protected_trade", None, mode=AppMode.LIVE)


if __name__ == "__main__":
    unittest.main()
