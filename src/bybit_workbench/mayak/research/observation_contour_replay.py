from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from bybit_workbench.market_observation_alert import (
    MarketObservationAlert,
    build_market_observation_alert,
)
from bybit_workbench.mayak.context_records import (
    coin_market_context_records,
    shared_market_context_record,
)
from bybit_workbench.mayak.continuity import MinuteContinuityTracker
from bybit_workbench.mayak.research.objective_replay import CausalMayakReplay, MarketEvent

REPLAY_SCHEMA_VERSION = "observation-replay-contour-v1"
ALERT_POLICY_STATUS = "NO_POLICY"


def _default_dispatcher_package_dir() -> Path:
    release_root = Path(__file__).resolve().parents[4]
    return release_root / "production" / "src" / "bybit_workbench" / "dispatcher_v2"


def _load_production_dispatcher(package_dir: Path) -> tuple[ModuleType, ModuleType]:
    package_dir = package_dir.resolve()
    init_file = package_dir / "__init__.py"
    serialization_file = package_dir / "serialization.py"
    if not init_file.is_file() or not serialization_file.is_file():
        raise RuntimeError(f"production dispatcher_v2 package missing: {package_dir}")

    identity = hashlib.sha256(str(package_dir).encode("utf-8")).hexdigest()[:16]
    package_name = f"_cripta_production_dispatcher_v2_{identity}"
    module = sys.modules.get(package_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(
            package_name,
            init_file,
            submodule_search_locations=[str(package_dir)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load production dispatcher_v2 package")
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        spec.loader.exec_module(module)
    serialization = importlib.import_module(f"{package_name}.serialization")
    return module, serialization


def _alert_record(alert: MarketObservationAlert) -> dict[str, Any]:
    return {
        "alert_id": alert.alert_id,
        "alert_class": alert.alert_class,
        "observed_at": alert.observed_at,
        "source_component": alert.source_component,
        "scope_key": alert.scope_key,
        "owner_notifiable": alert.owner_notifiable,
        "payload": dict(alert.payload),
        "provenance": dict(alert.provenance),
        "content_hash": alert.content_hash,
    }


class ObservationContourReplay:
    """Causal replay of the strategy-agnostic observation contour only."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        *,
        exact_liquidations: bool = False,
        dispatcher_package_dir: Path | None = None,
    ) -> None:
        self.mayak = CausalMayakReplay(symbols, exact_liquidations=exact_liquidations)
        package_dir = dispatcher_package_dir or _default_dispatcher_package_dir()
        self.dispatcher, self.dispatcher_serialization = _load_production_dispatcher(
            package_dir
        )
        self.dispatcher_package_dir = package_dir.resolve()
        self.continuity = MinuteContinuityTracker()
        self.explicit_alerts: list[MarketObservationAlert] = []
        self.last_input_at: float | None = None
        self.last_snapshot_at: float | None = None

    def set_supported(self, market: str, symbols: set[str]) -> None:
        self.mayak.set_supported(market, symbols)

    def _check_input_time(self, event_at: float) -> None:
        if self.last_input_at is not None and event_at < self.last_input_at:
            raise ValueError(
                f"OBSERVATION_REPLAY_OUT_OF_ORDER event_at={event_at} "
                f"last_input_at={self.last_input_at}"
            )
        if self.last_snapshot_at is not None and event_at < self.last_snapshot_at:
            raise ValueError(
                f"OBSERVATION_REPLAY_INPUT_BEFORE_SNAPSHOT event_at={event_at} "
                f"last_snapshot_at={self.last_snapshot_at}"
            )

    def feed(self, event: MarketEvent) -> None:
        self._check_input_time(event.event_at)
        self.mayak.feed(event)
        self.last_input_at = event.event_at

    def feed_trade(
        self,
        event_at: float,
        symbol: str,
        market: str,
        side: str,
        price: float,
        size: float,
    ) -> None:
        self._check_input_time(event_at)
        self.mayak.feed_trade(event_at, symbol, market, side, price, size)
        self.last_input_at = event_at

    def feed_alert_fact(
        self,
        *,
        alert_class: str,
        observed_at: datetime,
        source_component: str,
        scope_key: str,
        payload: Mapping[str, object],
        provenance: Mapping[str, object],
        owner_notifiable: bool = False,
    ) -> MarketObservationAlert:
        event_at = observed_at.astimezone(UTC).timestamp()
        self._check_input_time(event_at)
        alert = build_market_observation_alert(
            alert_class=alert_class,
            observed_at=observed_at,
            source_component=source_component,
            scope_key=scope_key,
            payload=payload,
            provenance=provenance,
            owner_notifiable=owner_notifiable,
        )
        self.explicit_alerts.append(alert)
        self.last_input_at = event_at
        return alert

    def snapshot(
        self,
        at: float,
        *,
        snapshot_id: int,
        dispatcher_at: float | None = None,
    ) -> dict[str, Any]:
        if snapshot_id <= 0:
            raise ValueError("snapshot_id must be positive")
        if self.last_input_at is not None and at < self.last_input_at:
            raise ValueError(
                f"OBSERVATION_REPLAY_SNAPSHOT_BEFORE_INPUT snapshot_at={at} "
                f"last_input_at={self.last_input_at}"
            )
        if self.last_snapshot_at is not None and at < self.last_snapshot_at:
            raise ValueError(
                f"OBSERVATION_REPLAY_SNAPSHOT_OUT_OF_ORDER snapshot_at={at} "
                f"last_snapshot_at={self.last_snapshot_at}"
            )
        dispatch_at = at if dispatcher_at is None else dispatcher_at
        if dispatch_at < at:
            raise ValueError("dispatcher_at cannot precede MAYAK snapshot time")

        mayak_snapshot = self.mayak.snapshot(at)
        regular_minute = datetime.fromtimestamp(at, UTC).replace(second=0, microsecond=0)
        mayak_snapshot["collector_continuity"] = self.continuity.advance(regular_minute)

        global_source = shared_market_context_record(snapshot_id, mayak_snapshot)
        coin_sources = coin_market_context_records(snapshot_id, mayak_snapshot)
        dispatch_now = datetime.fromtimestamp(dispatch_at, UTC)

        global_context = self.dispatcher.build_global_market_context(
            global_source,
            now=dispatch_now,
        )
        global_record = self.dispatcher_serialization.global_context_record(global_context)

        coin_records: dict[str, dict[str, Any]] = {}
        for source in coin_sources:
            context = self.dispatcher.build_coin_market_context(
                source,
                global_context_id=global_context.global_context_id,
                now=dispatch_now,
            )
            record = self.dispatcher_serialization.coin_context_record(context)
            coin_records[str(source["symbol"])] = record

        visible_alerts = [
            _alert_record(alert)
            for alert in self.explicit_alerts
            if alert.observed_at.timestamp() <= at
        ]
        self.last_snapshot_at = at

        return {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "observed_at": datetime.fromtimestamp(at, UTC),
            "trading_effect": "NONE",
            "mayak": mayak_snapshot,
            "dispatcher": {
                "global_context": global_record,
                "coin_contexts": coin_records,
                "coin_market_rating": "NOT_IMPLEMENTED",
                "production_package_dir": str(self.dispatcher_package_dir),
            },
            "quality_continuity": {
                "collector_continuity": dict(mayak_snapshot["collector_continuity"]),
                "global_data_quality": global_record["data_quality"],
                "global_coverage": global_record["coverage"],
                "coin_data_quality": {
                    symbol: {
                        "data_quality": record["data_quality"],
                        "coverage": record["coverage"],
                    }
                    for symbol, record in coin_records.items()
                },
            },
            "alerts": {
                "policy_status": ALERT_POLICY_STATUS,
                "automatic_generated": [],
                "replayed_facts": visible_alerts,
            },
            "provenance": {
                "mayak_engine": "LiveMayakEngine",
                "dispatcher_builder_source": str(self.dispatcher_package_dir),
                "alert_policy": ALERT_POLICY_STATUS,
                "trading_command": False,
            },
        }
