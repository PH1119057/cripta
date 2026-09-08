from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .contracts import (
    CandidateCooldown,
    CooldownScope,
    FrozenPolicy,
    StrategyActivation,
    StrategyCard,
    TouchPolicy,
    TradeDirection,
)


@dataclass(frozen=True, slots=True)
class V1CompatibilityBundle:
    card: StrategyCard
    activation: StrategyActivation
    calibration_sha256: str
    calibration_rows: dict[str, dict[str, object]]
    calibration_relative_path: str


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"invalid {label}") from None
    if not result.is_finite():
        raise ValueError(f"invalid {label}")
    return result


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _touch_policy(raw: dict[str, Any]) -> TouchPolicy:
    cooldown_raw = _mapping(raw.get("candidate_cooldown"), "candidate_cooldown")
    enabled = bool(cooldown_raw.get("enabled"))
    cooldown = CandidateCooldown(enabled=False)
    if enabled:
        try:
            scope = CooldownScope(str(cooldown_raw.get("scope")))
        except ValueError:
            raise ValueError("invalid compatibility cooldown scope") from None
        cooldown = CandidateCooldown(
            enabled=True,
            duration=_decimal(cooldown_raw.get("duration"), "candidate cooldown duration"),
            unit=str(cooldown_raw.get("unit") or ""),
            scope=scope,
            trigger_event=str(cooldown_raw.get("trigger_event") or ""),
            anchor=str(cooldown_raw.get("anchor") or ""),
        )
    numbers = raw.get("accepted_touch_numbers")
    if not isinstance(numbers, list):
        raise ValueError("accepted_touch_numbers must be a list")
    reset_on = raw.get("reset_on")
    if not isinstance(reset_on, list):
        raise ValueError("reset_on must be a list")
    accept_from_raw = raw.get("accept_touch_from")
    maximum_raw = raw.get("maximum_touch_count")
    return TouchPolicy(
        accepted_touch_numbers=tuple(int(str(item)) for item in numbers),
        accept_touch_from=None if accept_from_raw is None else int(str(accept_from_raw)),
        require_exit_from_zone=bool(raw.get("require_exit_from_zone")),
        maximum_touch_count=None if maximum_raw is None else int(str(maximum_raw)),
        reset_on=tuple(str(item) for item in reset_on),
        candidate_cooldown=cooldown,
    )


def load_v1_compatibility_bundle(
    project_root: Path,
    *,
    card_relative_path: str = "config/strategy_compat/entry_v1_core_u4.json",
) -> V1CompatibilityBundle:
    card_path = project_root / card_relative_path
    raw = _load_json(card_path)
    provenance = _mapping(raw.get("calibration_provenance"), "calibration_provenance")
    relative = str(provenance.get("relative_path") or "")
    calibration_path = project_root / relative
    calibration_bytes = calibration_path.read_bytes()
    actual_sha = hashlib.sha256(calibration_bytes).hexdigest()
    expected_sha = str(provenance.get("sha256") or "")
    if actual_sha != expected_sha:
        raise ValueError(
            f"compatibility calibration SHA mismatch: expected={expected_sha} actual={actual_sha}"
        )
    calibration = json.loads(calibration_bytes.decode("utf-8"))
    if not isinstance(calibration, dict):
        raise ValueError("compatibility calibration root must be object")
    for field, expected in (
        ("schema_version", provenance.get("schema")),
        ("strategy", provenance.get("strategy")),
        ("period", provenance.get("period")),
        ("missing_symbols", provenance.get("missing_symbols")),
    ):
        if calibration.get(field) != expected:
            raise ValueError(f"compatibility calibration provenance mismatch: {field}")
    rows = calibration.get("symbols")
    if not isinstance(rows, dict):
        raise ValueError("compatibility calibration symbols are missing")
    symbols_raw = raw.get("symbols")
    if not isinstance(symbols_raw, list) or not symbols_raw:
        raise ValueError("compatibility card symbols are missing")
    symbols = tuple(str(item).upper() for item in symbols_raw)
    missing_rows = tuple(symbol for symbol in symbols if symbol not in rows)
    if missing_rows:
        raise ValueError(f"compatibility calibration missing trading symbols: {missing_rows}")

    entry_policy = _mapping(raw.get("entry_policy"), "entry_policy")
    entry_policy = json.loads(json.dumps(entry_policy))
    watch = _mapping(entry_policy.get("watch_policy"), "entry_policy.watch_policy")
    oi = _mapping(watch.get("oi"), "entry_policy.watch_policy.oi")
    oi["calibration_rows"] = {symbol: rows[symbol] for symbol in symbols}

    directions_raw = raw.get("directions")
    if not isinstance(directions_raw, list) or not directions_raw:
        raise ValueError("compatibility directions are missing")
    directions = tuple(TradeDirection(str(item)) for item in directions_raw)
    approved_at = datetime.fromisoformat(str(raw.get("approved_at"))).astimezone(UTC)
    card = StrategyCard.build(
        strategy_id=str(raw.get("strategy_id") or ""),
        strategy_version=str(raw.get("strategy_version") or ""),
        name=str(raw.get("name") or ""),
        description=str(raw.get("description") or ""),
        scope=FrozenPolicy.from_mapping(_mapping(raw.get("scope"), "scope")),
        symbols=symbols,
        direction_policy=directions,
        entry_policy=FrozenPolicy.from_mapping(entry_policy),
        exit_policy=FrozenPolicy.from_mapping(_mapping(raw.get("exit_policy"), "exit_policy")),
        capital_policy=FrozenPolicy.from_mapping(
            _mapping(raw.get("capital_policy"), "capital_policy")
        ),
        protection_policy=FrozenPolicy.from_mapping(
            _mapping(raw.get("protection_policy"), "protection_policy")
        ),
        lifecycle_policy=FrozenPolicy.from_mapping(
            _mapping(raw.get("lifecycle_policy"), "lifecycle_policy")
        ),
        touch_policy=_touch_policy(_mapping(raw.get("touch_policy"), "touch_policy")),
        market_sensor_policy=(),
        mayak_context_policy=(),
        dispatcher_context_policy=(),
        approved_at=approved_at,
        approved_source=str(raw.get("approved_source") or ""),
    )
    activation_raw = _mapping(raw.get("activation"), "activation")
    activation = StrategyActivation(
        activation_id=str(activation_raw.get("activation_id") or ""),
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=bool(activation_raw.get("enabled")),
        enabled_at=approved_at,
        scope=FrozenPolicy.from_mapping({"mode": "PARITY_ONLY"}),
        operator=str(activation_raw.get("operator") or ""),
        source=str(activation_raw.get("source") or ""),
    )
    typed_rows: dict[str, dict[str, object]] = {}
    for symbol, row in rows.items():
        if isinstance(symbol, str) and isinstance(row, dict):
            typed_rows[symbol] = dict(row)
    return V1CompatibilityBundle(card, activation, actual_sha, typed_rows, relative)
