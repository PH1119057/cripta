from datetime import UTC, datetime
from pathlib import Path

from bybit_workbench.mayak.compact_analytics import (
    COMPACT_ANALYTICS_SCHEMA_VERSION,
    compact_market_minute_rows,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "operations/sql/20261017_compact_analytics_r8a.sql").read_text(
    encoding="utf-8"
)
MAYAK = (ROOT / "operations/monitoring/mayak_v2.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "operations/infrastructure/install_verified_release.sh").read_text(
    encoding="utf-8"
)
RUNTIME_SCHEMA = (ROOT / "operations/connectivity/runtime_schema.py").read_text(
    encoding="utf-8"
)
ARCHIVE = (ROOT / "operations/dashboard/archive_v2.py").read_text(encoding="utf-8")


def _snapshot() -> dict[str, object]:
    quality = {
        "quality": "FRESH",
        "age_seconds": 1.5,
        "transport_quality": "FRESH",
    }
    return {
        "observed_at": datetime(2026, 10, 17, 5, 17, 3, tzinfo=UTC).isoformat(),
        "architecture_version": "1.1",
        "engine_version": "mayak-v2.3",
        "feature_version": "objective-coin-context-v3",
        "config_fingerprint": "abc123",
        "coins": {
            "BTCUSDT": {
                "spot": {
                    "return_pct": 0.12,
                    "buy_usd": 10.0,
                    "sell_usd": 4.0,
                    "net_usd": 6.0,
                    "turnover_usd": 14.0,
                    "large_buy_usd": 3.0,
                    "large_sell_usd": 1.0,
                },
                "linear": {
                    "return_pct": 0.15,
                    "buy_usd": 20.0,
                    "sell_usd": 8.0,
                    "net_usd": 12.0,
                    "turnover_usd": 28.0,
                    "large_buy_usd": 5.0,
                    "large_sell_usd": 2.0,
                },
                "ticker": {
                    "open_interest": 1000.0,
                    "open_interest_change_pct": 1.2,
                    "funding_rate": 0.0001,
                    "last_price": 68000.0,
                    "mark_price": 68001.0,
                    "index_price": 67999.0,
                    "long_ratio": 0.52,
                    "short_ratio": 0.48,
                },
                "books": {
                    "spot": {
                        "mid_price": 67998.0,
                        "bid_usd": 100.0,
                        "ask_usd": 110.0,
                        "bid_change_pct": 2.0,
                        "ask_change_pct": -1.0,
                    },
                    "linear": {
                        "mid_price": 68000.0,
                        "bid_usd": 200.0,
                        "ask_usd": 210.0,
                        "bid_change_pct": 3.0,
                        "ask_change_pct": -2.0,
                    },
                },
                "quality": {
                    "spot_trades": quality,
                    "spot_book": quality,
                    "linear_trades": quality,
                    "linear_book": quality,
                    "linear_ticker": quality,
                },
                "availability": {"spot": "SUPPORTED", "linear": "SUPPORTED"},
            }
        },
    }


def test_compact_rows_split_spot_and_linear_without_account_dimension() -> None:
    rows = compact_market_minute_rows(77, _snapshot())
    assert len(rows) == 2
    spot, linear = rows
    assert spot["instrument_id"] == "inst-bybit-spot-btcusdt"
    assert linear["instrument_id"] == "inst-bybit-linear-btcusdt"
    assert spot["market_source_id"] == linear["market_source_id"] == "ms-bybit-mainnet-public-v1"
    assert spot["observed_minute"] == datetime(2026, 10, 17, 5, 17, tzinfo=UTC)
    assert spot["return_5m_pct"] == 0.12
    assert linear["return_5m_pct"] == 0.15
    assert spot["open_interest"] is None
    assert linear["open_interest"] == 1000.0
    assert spot["schema_version"] == COMPACT_ANALYTICS_SCHEMA_VERSION
    assert "trading_account_id" not in spot
    assert "strategy_id" not in spot


def test_r8a_migration_is_additive_compact_and_exact_identity_grained() -> None:
    upper = MIGRATION.upper()
    for forbidden in (" DROP ", " DELETE ", " TRUNCATE ", " UPDATE "):
        assert forbidden not in upper
    assert "CREATE TABLE IF NOT EXISTS analytics.market_minutes" in MIGRATION
    assert "PRIMARY KEY (market_source_id, instrument_id, observed_minute)" in MIGRATION
    assert "FOREIGN KEY (market_source_id, venue_id)" in MIGRATION
    assert "instrument_id, venue_id, product_category, external_symbol" in MIGRATION
    assert "source_snapshot_id bigint NOT NULL" in MIGRATION
    assert "config_fingerprint text NOT NULL" in MIGRATION
    assert "payload jsonb" not in MIGRATION.lower()
    assert "trading_account_id" not in MIGRATION
    assert "strategy_id" not in MIGRATION


def test_mayak_dual_writes_compact_rows_after_legacy_coin_minutes() -> None:
    assert "compact_market_minute_rows" in MAYAK
    assert "def _persist_compact_market_minutes" in MAYAK
    assert "INSERT INTO analytics.market_minutes" in MAYAK
    assert MAYAK.index("self._persist_coin_minutes(snapshot_id, snapshot)") < MAYAK.index(
        "self._persist_compact_market_minutes(snapshot_id, snapshot)"
    )


def test_installer_and_runtime_schema_require_compact_analytics() -> None:
    r7 = INSTALLER.index("operations/sql/20261017_market_instrument_identity_r7a.sql")
    r8 = INSTALLER.index("operations/sql/20261017_compact_analytics_r8a.sql")
    assert r7 < r8
    assert '"analytics.market_minutes"' in RUNTIME_SCHEMA
    assert '("analytics", "market_minutes", "market_source_id")' in RUNTIME_SCHEMA
    assert '("analytics", "market_minutes", "instrument_id")' in RUNTIME_SCHEMA
    assert "compact-analytics-r8a" in RUNTIME_SCHEMA


def test_archive_exports_compact_market_minutes_as_public_market_identity() -> None:
    assert '"analytics.market_minutes"' in ARCHIVE
    assert '"market_source_id", "instrument_id"' in ARCHIVE
