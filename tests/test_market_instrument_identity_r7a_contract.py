from pathlib import Path

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    BYBIT_VENUE_ID,
    bybit_instrument_id,
    bybit_instrument_lineage,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "operations/sql/20261017_market_instrument_identity_r7a.sql").read_text(
    encoding="utf-8"
)
MAYAK = (ROOT / "operations/monitoring/mayak_v2.py").read_text(encoding="utf-8")
LIVE = (ROOT / "src/bybit_workbench/mayak/core/live.py").read_text(encoding="utf-8")
DISPATCHER = (ROOT / "operations/monitoring/dispatcher_v2.py").read_text(encoding="utf-8")
PRIVATE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
SCHEMA = (ROOT / "operations/connectivity/runtime_schema.py").read_text(encoding="utf-8")
INSTALLER = (
    ROOT / "operations/infrastructure/install_verified_release.sh"
).read_text(encoding="utf-8")


def test_current_market_identity_helpers_are_exact_and_product_distinct() -> None:
    assert BYBIT_VENUE_ID == "venue-bybit"
    assert BYBIT_PUBLIC_MARKET_SOURCE_ID == "ms-bybit-mainnet-public-v1"
    assert bybit_instrument_id("spot", "BTCUSDT") == "inst-bybit-spot-btcusdt"
    assert bybit_instrument_id("linear", "BTCUSDT") == "inst-bybit-linear-btcusdt"
    assert bybit_instrument_lineage("BTCUSDT") == {
        "spot": "inst-bybit-spot-btcusdt",
        "linear": "inst-bybit-linear-btcusdt",
    }


def test_r7a_seeds_spot_and_linear_instruments_without_historical_backfill() -> None:
    assert "identity.instruments" in MIGRATION
    assert "'SPOT'::text AS product_category" in MIGRATION
    assert "'LINEAR'," in MIGRATION
    assert "'LinearPerpetual'" in MIGRATION
    assert "R7A_MARKET_SOURCE_IDENTITY_MISMATCH" in MIGRATION
    assert "R7A_INSTRUMENT_IDENTITY_MISMATCH" in MIGRATION
    upper = MIGRATION.upper()
    assert "UPDATE MAYAK_V2." not in upper
    assert "UPDATE DISPATCHER_V2." not in upper
    assert "DELETE FROM" not in upper
    assert "DROP " not in upper


def test_r7a_adds_nullable_identity_bridge_with_fk_provenance() -> None:
    for table in (
        "mayak_v2.coin_minutes",
        "mayak_v2.liquidations",
        "mayak_v2.coin_market_contexts",
        "mayak_v2.shared_market_contexts",
        "dispatcher_v2.global_market_contexts",
        "dispatcher_v2.coin_market_contexts",
    ):
        assert f"ALTER TABLE {table}" in MIGRATION
    assert "FOREIGN KEY (%I) REFERENCES identity.market_sources" in MIGRATION
    assert "FOREIGN KEY (%I) REFERENCES identity.instruments" in MIGRATION
    assert "CHECK (%I IS NOT NULL)" not in MIGRATION
    assert "position_mode_states_proven_instrument_check" not in MIGRATION


def test_mayak_dual_writes_identity_without_removing_symbol() -> None:
    assert "_validate_market_identity_registry" in MAYAK
    assert 'bybit_instrument_id("spot", symbol)' in MAYAK
    assert 'bybit_instrument_id("linear", symbol)' in MAYAK
    assert "market_source_id,spot_instrument_id,linear_instrument_id" in MAYAK
    assert '"market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID' in LIVE
    assert '"instrument_lineage": instrument_lineage' in LIVE
    assert '"symbol": symbol' in LIVE


def test_dispatcher_preserves_source_identity_and_legacy_symbol() -> None:
    assert "source.market_source_id" in DISPATCHER
    assert "WHERE source.market_source_id IS NOT NULL" in DISPATCHER
    assert "spot_instrument_id" in DISPATCHER
    assert "linear_instrument_id" in DISPATCHER
    assert "source_mayak_snapshot_id,symbol" in DISPATCHER


def test_private_position_mode_proven_rows_carry_linear_instrument_identity() -> None:
    assert 'instrument_id = bybit_instrument_id("linear", symbol)' in PRIVATE
    assert "instrument_id,position_mode" in PRIVATE
    assert 'item["instrument_id"]' in PRIVATE


def test_runtime_schema_and_installer_require_r7a_before_service_start() -> None:
    assert "runtime-schema-2026-10-17-compact-analytics-r8c" in SCHEMA
    for column in (
        '("mayak_v2", "coin_minutes", "market_source_id")',
        '("mayak_v2", "coin_market_contexts", "linear_instrument_id")',
        '("dispatcher_v2", "global_market_contexts", "market_source_id")',
        '("dispatcher_v2", "coin_market_contexts", "linear_instrument_id")',
    ):
        assert column in SCHEMA
    r3 = INSTALLER.index("20261016_identity_bridge_r3_r4.sql")
    r7a = INSTALLER.index("20261017_market_instrument_identity_r7a.sql")
    r5a = INSTALLER.index("20261016_private_current_state_r5a.sql")
    assert r3 < r7a < r5a
