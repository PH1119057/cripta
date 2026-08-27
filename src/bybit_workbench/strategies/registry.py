from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .base import Strategy, StrategyMetadata


class StrategyKind(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    RESERVED = "reserved"


class ParameterType(StrEnum):
    DECIMAL = "decimal"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class StrategyParameter:
    name: str
    label: str
    parameter_type: ParameterType
    default: object
    required: bool = True
    minimum: Decimal | int | None = None
    maximum: Decimal | int | None = None
    choices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.label.strip():
            raise ValueError("strategy parameter name and label are required")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("strategy parameter minimum exceeds maximum")
        if self.choices and self.parameter_type is not ParameterType.TEXT:
            raise ValueError("choices are only valid for text parameters")

    def resolve(self, value: object) -> object:
        if self.parameter_type is ParameterType.BOOLEAN:
            if not isinstance(value, bool):
                raise ValueError(f"{self.name} must be boolean")
            return value
        if self.parameter_type is ParameterType.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{self.name} must be integer")
            resolved: object = value
        elif self.parameter_type is ParameterType.DECIMAL:
            if isinstance(value, (bool, float)):
                raise ValueError(f"{self.name} must be an exact Decimal, integer, or text")
            try:
                resolved = value if isinstance(value, Decimal) else Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"{self.name} must be decimal") from exc
            if not resolved.is_finite():
                raise ValueError(f"{self.name} must be finite")
        elif self.parameter_type is ParameterType.TEXT:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{self.name} must be non-empty text")
            resolved = value.strip()
            if self.choices and resolved not in self.choices:
                raise ValueError(f"{self.name} must be one of {', '.join(self.choices)}")
        else:
            raise TypeError(f"unsupported parameter type: {self.parameter_type}")
        if self.minimum is not None and resolved < self.minimum:  # type: ignore[operator]
            raise ValueError(f"{self.name} must be >= {self.minimum}")
        if self.maximum is not None and resolved > self.maximum:  # type: ignore[operator]
            raise ValueError(f"{self.name} must be <= {self.maximum}")
        return resolved


@dataclass(frozen=True, slots=True)
class StrategyRegistration:
    metadata: StrategyMetadata
    kind: StrategyKind
    factory: Callable[[], Strategy] | None
    parameters: tuple[StrategyParameter, ...] = ()
    requires_historical_validation: bool = True
    cross_validator: Callable[[Mapping[str, object]], None] | None = None

    def __post_init__(self) -> None:
        if self.kind is StrategyKind.RESERVED and self.factory is not None:
            raise ValueError("reserved strategy cannot have an implementation factory")
        if self.kind is not StrategyKind.RESERVED and self.factory is None:
            raise ValueError("implemented strategy requires a factory")
        if self.kind is StrategyKind.AUTOMATIC and not self.requires_historical_validation:
            raise ValueError("automatic strategy cannot bypass historical validation")
        if self.kind is StrategyKind.RESERVED and not self.requires_historical_validation:
            raise ValueError("reserved strategy cannot bypass historical validation")
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("strategy parameter names must be unique")

    def resolve_parameters(
        self,
        supplied: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        supplied_values = dict(supplied or {})
        known = {item.name for item in self.parameters}
        unknown = sorted(set(supplied_values) - known)
        if unknown:
            raise ValueError(f"unknown strategy parameters: {', '.join(unknown)}")
        resolved = {
            item.name: item.resolve(supplied_values.get(item.name, item.default))
            for item in self.parameters
        }
        if self.cross_validator is not None:
            self.cross_validator(resolved)
        return resolved


class StrategyRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, StrategyRegistration] = {}

    def register(self, registration: StrategyRegistration) -> None:
        strategy_id = registration.metadata.strategy_id
        if strategy_id in self._registrations:
            raise ValueError(f"strategy is already registered: {strategy_id}")
        self._registrations[strategy_id] = registration

    def get(self, strategy_id: str) -> StrategyRegistration:
        try:
            return self._registrations[strategy_id]
        except KeyError as exc:
            raise LookupError(f"unknown strategy: {strategy_id}") from exc

    def registrations(self) -> tuple[StrategyRegistration, ...]:
        return tuple(self._registrations.values())

    def create(self, strategy_id: str) -> Strategy:
        registration = self.get(strategy_id)
        if registration.factory is None:
            raise RuntimeError(
                f"strategy {strategy_id} is reserved until its formal rules are approved"
            )
        return registration.factory()
