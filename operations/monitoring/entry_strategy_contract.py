from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


@dataclass(frozen=True, slots=True)
class InitialProtectionContract:
    strategy_id: str
    strategy_version: str
    stop_loss_pct: Decimal
    take_profit_pct: Decimal
    trigger_by: str
    tpsl_mode: str

    def payload_for(self, strategy_id: str, strategy_version: str) -> dict[str, str]:
        if strategy_id != self.strategy_id or strategy_version != self.strategy_version:
            raise RuntimeError(
                "initial protection strategy mismatch: "
                f"expected {self.strategy_id}/{self.strategy_version}, "
                f"got {strategy_id}/{strategy_version}"
            )
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "stop_loss_pct": str(self.stop_loss_pct),
            "take_profit_pct": str(self.take_profit_pct),
            "trigger_by": self.trigger_by,
            "tpsl_mode": self.tpsl_mode,
        }


def _candidate_paths() -> tuple[Path, ...]:
    env = os.environ.get("CRIPTA_ENTRY_STRATEGY_CONFIG")
    if env:
        return (Path(env),)
    here = Path(__file__).resolve()
    return (
        here.parents[1] / "config/live_strategies/entry_v1_core.json",
        here.parents[2] / "config/live_strategies/entry_v1_core.json",
    )


def _config_path() -> Path:
    for path in _candidate_paths():
        if path.is_file():
            return path
    rendered = ", ".join(str(path) for path in _candidate_paths())
    raise RuntimeError(f"ENTRY_STRATEGY_CONFIG_MISSING candidates={rendered}")


def _positive_decimal(raw: object, field: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise RuntimeError(f"ENTRY_STRATEGY_CONFIG_INVALID {field}") from exc
    if value <= 0:
        raise RuntimeError(f"ENTRY_STRATEGY_CONFIG_INVALID {field}")
    return value


def load_entry_v1_core_initial_protection() -> InitialProtectionContract:
    path = _config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"ENTRY_STRATEGY_CONFIG_UNREADABLE path={path}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("ENTRY_STRATEGY_CONFIG_INVALID root")
    strategy_id = str(raw.get("strategy_id") or "")
    strategy_version = str(raw.get("strategy_version") or "")
    protection = raw.get("initial_protection")
    if strategy_id != "entry_v1_core" or not strategy_version:
        raise RuntimeError("ENTRY_STRATEGY_CONFIG_INVALID identity")
    if not isinstance(protection, dict):
        raise RuntimeError("ENTRY_STRATEGY_CONFIG_INVALID initial_protection")
    stop_loss_pct = _positive_decimal(protection.get("stop_loss_pct"), "stop_loss_pct")
    take_profit_pct = _positive_decimal(
        protection.get("take_profit_pct"), "take_profit_pct"
    )
    trigger_by = str(protection.get("trigger_by") or "")
    tpsl_mode = str(protection.get("tpsl_mode") or "")
    if trigger_by not in {"LastPrice", "MarkPrice", "IndexPrice"}:
        raise RuntimeError("ENTRY_STRATEGY_CONFIG_INVALID trigger_by")
    if tpsl_mode != "Full":
        raise RuntimeError("ENTRY_STRATEGY_CONFIG_INVALID tpsl_mode")
    if stop_loss_pct > Decimal("10") or take_profit_pct > Decimal("20"):
        raise RuntimeError("ENTRY_STRATEGY_CONFIG_UNSAFE protection_percent")
    return InitialProtectionContract(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trigger_by=trigger_by,
        tpsl_mode=tpsl_mode,
    )


ENTRY_V1_CORE_INITIAL_PROTECTION = load_entry_v1_core_initial_protection()
