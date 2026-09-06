from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import bybit_workbench

PRODUCTION_PACKAGE = Path(__file__).parents[1] / "production" / "src" / "bybit_workbench"
if str(PRODUCTION_PACKAGE) not in bybit_workbench.__path__:
    bybit_workbench.__path__.append(str(PRODUCTION_PACKAGE))

dispatcher_v2 = importlib.import_module("bybit_workbench.dispatcher_v2")


def test_global_context_is_strategy_agnostic_and_causal() -> None:
    now = datetime(2026, 9, 6, 12, 0, 30, tzinfo=UTC)
    source = {
        "market_context_id": "MC-test",
        "mayak_snapshot_id": 42,
        "observed_at": now - timedelta(seconds=20),
        "mayak_version": "mayak-v2.2",
        "schema_version": "shared-market-context-v1",
        "config_fingerprint": "mayak-config",
        "data_quality": "HIGH",
        "content_hash": "source-hash",
        "payload": {
            "architecture_version": "1.1",
            "dispatcher_features": {
                "market.direction": {
                    "value": "DOWN",
                    "status": "VALID",
                    "coverage": {"valid": 20, "total": 20},
                },
                "event.context": {"status": "NO_DATA", "coverage": {"valid": 0, "total": 20}},
            },
        },
    }
    item = dispatcher_v2.build_global_market_context(source, now=now)
    assert item.trading_effect == "NONE"
    assert item.freshness.status.value == "FRESH"
    assert item.coverage["feature_valid"] == 1
    assert item.provenance["coin_market_rating"] == "NOT_IMPLEMENTED"
    assert "profile_id" not in item.objective_facts


def test_coin_context_preserves_physical_layers_without_rating() -> None:
    now = datetime(2026, 9, 6, 12, 0, 30, tzinfo=UTC)
    source = {
        "coin_context_id": "CMC-test",
        "mayak_snapshot_id": 42,
        "observed_at": now - timedelta(seconds=10),
        "symbol": "LINKUSDT",
        "schema_version": "coin-market-context-v1",
        "engine_version": "mayak-v2.2",
        "feature_version": "objective-coin-context-v2",
        "config_fingerprint": "mayak-config",
        "data_quality": "MEDIUM",
        "content_hash": "coin-source-hash",
        "payload": {
            "money": {"spot": {"5m": {"net_usd": 12.5}}},
            "liquidity": {"derivatives": {"imbalance": -0.1}},
            "positioning": {"open_interest": 123.0},
            "liquidations": {"status": "WARMUP"},
            "relative_strength": {"5m": {"relative_to_panel_pct": 0.2}},
            "event_context": {"status": "NO_DATA"},
            "data_quality": {"coverage": 0.6, "fresh_sources": 3, "expected_sources": 5},
        },
    }
    item = dispatcher_v2.build_coin_market_context(
        source, global_context_id="DGC-test", now=now
    )
    assert item.symbol == "LINKUSDT"
    assert item.objective_facts["money"]["spot"]["5m"]["net_usd"] == 12.5
    assert item.provenance["coin_market_rating"] == "NOT_IMPLEMENTED"
    assert "score" not in item.objective_facts


def test_capacity_uses_exchange_reported_free_balance_without_double_subtraction() -> None:
    now = datetime(2026, 9, 6, 12, 0, 30, tzinfo=UTC)
    source = dispatcher_v2.TradingAccountState(
        observed_at=now - timedelta(seconds=5),
        source_adapter="test-adapter",
        source_account_ref="account:test",
        account_type="UNIFIED",
        total_equity=Decimal("100.00"),
        wallet_balance=Decimal("100.00"),
        used_position_margin=Decimal("20.00"),
        reserved_order_margin=Decimal("10.00"),
        free_balance=Decimal("70.00"),
        open_positions_count=1,
        active_orders_count=1,
        source_payload={"test": True},
    )
    item = dispatcher_v2.build_trading_capacity_snapshot(source, now=now)
    assert item.available_for_new_trading == Decimal("70.00")
    assert item.data_quality.value == "HIGH"
    assert item.trading_effect == "NONE"


def test_capacity_keeps_unknown_margin_unknown() -> None:
    now = datetime(2026, 9, 6, 12, 0, 30, tzinfo=UTC)
    source = dispatcher_v2.TradingAccountState(
        observed_at=now,
        source_adapter="test-adapter",
        source_account_ref="account:test",
        account_type=None,
        total_equity=Decimal("100"),
        wallet_balance=Decimal("100"),
        used_position_margin=None,
        reserved_order_margin=None,
        free_balance=Decimal("100"),
        open_positions_count=0,
        active_orders_count=0,
        source_payload={},
    )
    item = dispatcher_v2.build_trading_capacity_snapshot(source, now=now)
    assert item.used_position_margin is None
    assert item.reserved_order_margin is None
    assert item.data_quality.value == "MEDIUM"


def test_contract_rejects_trading_effect() -> None:
    contracts = importlib.import_module("bybit_workbench.dispatcher_v2.contracts")
    freshness = contracts.Freshness(contracts.FreshnessStatus.FRESH, 0.0, 90)
    with pytest.raises(ValueError, match="trading effect"):
        contracts.GlobalMarketContext(
            global_context_id="x",
            source_market_context_id="s",
            source_mayak_snapshot_id=1,
            observed_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            dispatcher_version="v",
            schema_version="s",
            config_fingerprint="f",
            data_quality=contracts.DataQuality.HIGH,
            freshness=freshness,
            coverage={},
            objective_facts={},
            provenance={},
            content_hash="h",
            trading_effect="FULL_LIVE",
        )
