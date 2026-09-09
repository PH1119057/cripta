from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from time import sleep as _sleep

from .contracts import FrozenPolicy, MarketFactEnvelope
from .fingerprint import fingerprint


class OiSlotState(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    MISSED = "MISSED"


@dataclass(frozen=True, slots=True)
class Oi30sConfig:
    fact_source_id: str
    required_symbols: tuple[str, ...]
    slot_seconds: int = 30
    request_timeout_seconds: float = 5.0
    retry_interval_seconds: float = 0.25

    def __post_init__(self) -> None:
        if not self.fact_source_id.strip():
            raise ValueError("fact_source_id is required")
        if not self.required_symbols:
            raise ValueError("required_symbols cannot be empty")
        normalized = tuple(symbol.strip().upper() for symbol in self.required_symbols)
        if any(not symbol for symbol in normalized):
            raise ValueError("required_symbols cannot contain empty symbols")
        if len(set(normalized)) != len(normalized):
            raise ValueError("required_symbols must be unique")
        if self.slot_seconds <= 0:
            raise ValueError("slot_seconds must be positive")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.retry_interval_seconds < 0:
            raise ValueError("retry_interval_seconds cannot be negative")
        object.__setattr__(self, "required_symbols", normalized)


@dataclass(frozen=True, slots=True)
class CurrentOiResponse:
    request_started_at: datetime
    response_received_at: datetime
    exchange_server_observed_at: datetime
    open_interest: Mapping[str, Decimal]
    provenance: str

    def __post_init__(self) -> None:
        for value in (
            self.request_started_at,
            self.response_received_at,
            self.exchange_server_observed_at,
        ):
            if value.tzinfo is None:
                raise ValueError("current OI response timestamps must be timezone-aware")
        if self.response_received_at < self.request_started_at:
            raise ValueError("response_received_at precedes request_started_at")
        if not self.provenance.strip():
            raise ValueError("current OI response provenance is required")


@dataclass(frozen=True, slots=True)
class OiSlotResult:
    fact_source_id: str
    slot_id: str
    nominal_slot_at: datetime
    slot_end_at: datetime
    state: OiSlotState
    attempts: int
    facts: tuple[MarketFactEnvelope, ...]
    missing_symbols: tuple[str, ...]
    invalid_symbols: tuple[str, ...]
    request_started_at: datetime | None
    response_received_at: datetime | None
    exchange_server_observed_at: datetime | None
    provenance: str | None
    reason: str

    @property
    def delivery_delay_seconds(self) -> float | None:
        if self.response_received_at is None:
            return None
        return max(
            0.0,
            (
                self.response_received_at.astimezone(UTC) - self.nominal_slot_at.astimezone(UTC)
            ).total_seconds(),
        )


def slot_at(at: datetime, slot_seconds: int) -> datetime:
    if at.tzinfo is None:
        raise ValueError("slot timestamp must be timezone-aware")
    if slot_seconds <= 0:
        raise ValueError("slot_seconds must be positive")
    observed = at.astimezone(UTC)
    epoch_seconds = int(observed.timestamp())
    floored = epoch_seconds - (epoch_seconds % slot_seconds)
    return datetime.fromtimestamp(floored, UTC)


def slot_id_for(nominal_slot_at: datetime, slot_seconds: int) -> str:
    nominal = slot_at(nominal_slot_at, slot_seconds)
    ordinal = int(nominal.timestamp()) // slot_seconds
    return f"oi-slot-{slot_seconds}s-{ordinal}"


def _decimal_oi(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not result.is_finite() or result <= 0:
        return None
    return result


def _complete_result(
    config: Oi30sConfig,
    nominal_slot_at: datetime,
    response: CurrentOiResponse,
    *,
    attempts: int,
) -> OiSlotResult:
    nominal = slot_at(nominal_slot_at, config.slot_seconds)
    slot_end = nominal + timedelta(seconds=config.slot_seconds)
    rendered = {str(key).upper(): value for key, value in response.open_interest.items()}
    missing: list[str] = []
    invalid: list[str] = []
    normalized: dict[str, Decimal] = {}
    for symbol in config.required_symbols:
        if symbol not in rendered:
            missing.append(symbol)
            continue
        value = _decimal_oi(rendered[symbol])
        if value is None:
            invalid.append(symbol)
            continue
        normalized[symbol] = value
    if missing or invalid:
        return OiSlotResult(
            config.fact_source_id,
            slot_id_for(nominal, config.slot_seconds),
            nominal,
            slot_end,
            OiSlotState.INCOMPLETE,
            attempts,
            (),
            tuple(missing),
            tuple(invalid),
            response.request_started_at.astimezone(UTC),
            response.response_received_at.astimezone(UTC),
            response.exchange_server_observed_at.astimezone(UTC),
            response.provenance,
            "required current OI response is incomplete",
        )
    slot_id = slot_id_for(nominal, config.slot_seconds)
    received_at = response.response_received_at.astimezone(UTC)
    server_at = response.exchange_server_observed_at.astimezone(UTC)
    request_at = response.request_started_at.astimezone(UTC)
    facts: list[MarketFactEnvelope] = []
    for symbol in sorted(config.required_symbols):
        fact_id = (
            "oi-"
            + fingerprint(
                {
                    "fact_source_id": config.fact_source_id,
                    "slot_id": slot_id,
                    "symbol": symbol,
                }
            )[:32]
        )
        source_ref = f"bybit:rest-current-oi:linear:{slot_id}:{symbol}"
        facts.append(
            MarketFactEnvelope(
                fact_id=fact_id,
                event_kind="OPEN_INTEREST",
                symbol=symbol,
                observed_at=received_at,
                event_at=server_at,
                received_at=received_at,
                source_refs=(source_ref,),
                attributes=FrozenPolicy.from_mapping(
                    {
                        "open_interest": str(normalized[symbol]),
                        "fact_source_id": config.fact_source_id,
                        "slot_id": slot_id,
                        "nominal_slot_at": nominal.isoformat(),
                        "request_started_at": request_at.isoformat(),
                        "response_received_at": received_at.isoformat(),
                        "exchange_server_observed_at": server_at.isoformat(),
                        "provenance": response.provenance,
                    }
                ),
            )
        )
    return OiSlotResult(
        config.fact_source_id,
        slot_id,
        nominal,
        slot_end,
        OiSlotState.COMPLETE,
        attempts,
        tuple(facts),
        (),
        (),
        request_at,
        received_at,
        server_at,
        response.provenance,
        "complete current OI source slot",
    )


def poll_current_oi_slot(
    config: Oi30sConfig,
    *,
    nominal_slot_at: datetime,
    fetch: Callable[[float], CurrentOiResponse],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = _sleep,
) -> OiSlotResult:
    nominal = slot_at(nominal_slot_at, config.slot_seconds)
    slot_end = nominal + timedelta(seconds=config.slot_seconds)
    attempts = 0
    last_incomplete: OiSlotResult | None = None
    last_error: str | None = None
    while True:
        current = now().astimezone(UTC)
        remaining = (slot_end - current).total_seconds()
        if remaining <= 0:
            if last_incomplete is not None:
                return last_incomplete
            return OiSlotResult(
                config.fact_source_id,
                slot_id_for(nominal, config.slot_seconds),
                nominal,
                slot_end,
                OiSlotState.MISSED,
                attempts,
                (),
                tuple(config.required_symbols),
                (),
                None,
                None,
                None,
                None,
                last_error or "source slot deadline reached without accepted current OI",
            )
        attempts += 1
        try:
            response = fetch(min(config.request_timeout_seconds, max(0.001, remaining)))
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            after = now().astimezone(UTC)
            if after >= slot_end:
                return OiSlotResult(
                    config.fact_source_id,
                    slot_id_for(nominal, config.slot_seconds),
                    nominal,
                    slot_end,
                    OiSlotState.MISSED,
                    attempts,
                    (),
                    tuple(config.required_symbols),
                    (),
                    None,
                    None,
                    None,
                    None,
                    last_error,
                )
            pause = min(config.retry_interval_seconds, (slot_end - after).total_seconds())
            if pause > 0:
                sleep(pause)
            continue
        received_at = response.response_received_at.astimezone(UTC)
        if received_at >= slot_end:
            return OiSlotResult(
                config.fact_source_id,
                slot_id_for(nominal, config.slot_seconds),
                nominal,
                slot_end,
                OiSlotState.MISSED,
                attempts,
                (),
                tuple(config.required_symbols),
                (),
                response.request_started_at.astimezone(UTC),
                received_at,
                response.exchange_server_observed_at.astimezone(UTC),
                response.provenance,
                "current OI response arrived after source slot deadline",
            )
        result = _complete_result(config, nominal, response, attempts=attempts)
        if result.state is OiSlotState.COMPLETE:
            return result
        last_incomplete = result
        after = now().astimezone(UTC)
        if after >= slot_end:
            return result
        pause = min(config.retry_interval_seconds, (slot_end - after).total_seconds())
        if pause > 0:
            sleep(pause)


class Oi30sHealthTracker:
    def __init__(self, config: Oi30sConfig) -> None:
        self.config = config
        self._last_nominal: datetime | None = None
        self._last_complete_slot: str | None = None
        self._per_symbol_last: dict[str, datetime] = {}
        self._complete_slots = 0
        self._missed_slots = 0
        self._incomplete_slots = 0
        self._silent_gaps = 0
        self._max_slot_delay = 0.0
        self._consecutive_complete = 0
        self._state = "INITIALIZING"

    def _check_progression(self, nominal: datetime) -> None:
        nominal = slot_at(nominal, self.config.slot_seconds)
        if self._last_nominal is not None:
            expected = self._last_nominal + timedelta(seconds=self.config.slot_seconds)
            if nominal != expected:
                self._silent_gaps += 1
                self._state = "NOT_PROVABLE"
                self._consecutive_complete = 0
        self._last_nominal = nominal

    def accept(self, result: OiSlotResult) -> None:
        if result.fact_source_id != self.config.fact_source_id:
            raise ValueError("OI slot source identity changed")
        self._check_progression(result.nominal_slot_at)
        if result.state is OiSlotState.COMPLETE:
            if len(result.facts) != len(self.config.required_symbols):
                raise ValueError("complete OI slot has wrong fact count")
            self._complete_slots += 1
            self._last_complete_slot = result.slot_id
            self._consecutive_complete += 1
            self._state = "HEALTHY" if self._silent_gaps == 0 else "NOT_PROVABLE"
            delay = result.delivery_delay_seconds
            if delay is not None:
                self._max_slot_delay = max(self._max_slot_delay, delay)
            for fact in result.facts:
                self._per_symbol_last[fact.symbol] = fact.received_at.astimezone(UTC)
            return
        self._consecutive_complete = 0
        self._state = "NOT_PROVABLE"
        if result.state is OiSlotState.MISSED:
            self._missed_slots += 1
        else:
            self._incomplete_slots += 1

    def accept_gap(self, nominal_slot_at: datetime, *, state: OiSlotState, reason: str) -> None:
        if state is OiSlotState.COMPLETE:
            raise ValueError("gap cannot be COMPLETE")
        nominal = slot_at(nominal_slot_at, self.config.slot_seconds)
        self.accept(
            OiSlotResult(
                self.config.fact_source_id,
                slot_id_for(nominal, self.config.slot_seconds),
                nominal,
                nominal + timedelta(seconds=self.config.slot_seconds),
                state,
                0,
                (),
                tuple(self.config.required_symbols),
                (),
                None,
                None,
                None,
                None,
                reason,
            )
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "oi_source_state": self._state,
            "fact_source_id": self.config.fact_source_id,
            "last_complete_oi_slot": self._last_complete_slot,
            "per_symbol_last_oi_received_at": {
                symbol: self._per_symbol_last[symbol].isoformat()
                for symbol in sorted(self._per_symbol_last)
            },
            "complete_slots": self._complete_slots,
            "missed_slots": self._missed_slots,
            "incomplete_slots": self._incomplete_slots,
            "silent_gaps": self._silent_gaps,
            "max_slot_delay": self._max_slot_delay,
            "consecutive_complete_slots": self._consecutive_complete,
        }
