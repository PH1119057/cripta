from pathlib import Path

import pytest

from operations.dashboard.archive_v2 import _validate_public_market_identity

ROOT = Path(__file__).resolve().parents[1]
OBSERVER = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(
    encoding="utf-8"
)
CORRELATOR = (
    ROOT / "operations/monitoring/dispatcher_v2_context_correlator.py"
).read_text(encoding="utf-8")
DASHBOARD = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
ARCHIVE = (ROOT / "operations/dashboard/archive_v2.py").read_text(encoding="utf-8")


def test_universal_entry_reads_only_exact_r7_market_identity() -> None:
    assert "BYBIT_PUBLIC_MARKET_SOURCE_ID" in OBSERVER
    assert "WHERE market_source_id=%s" in OBSERVER
    assert "linear_instrument_id = ANY(%s)" in OBSERVER
    assert "DISPATCHER_COIN_MARKET_IDENTITY_MISMATCH" in OBSERVER
    assert 'bybit_instrument_id("spot", symbol)' in OBSERVER
    assert 'bybit_instrument_id("linear", symbol)' in OBSERVER
    assert "market-source:" in OBSERVER
    assert "instrument:" in OBSERVER


def test_correlator_new_links_require_exact_market_and_instrument_identity() -> None:
    assert "WHERE market_source_id=%s" in CORRELATOR
    assert "FROM identity.instruments" in CORRELATOR
    assert "product_category='SPOT'" in CORRELATOR
    assert "product_category='LINEAR'" in CORRELATOR
    assert "HAVING count(*)=1" in CORRELATOR
    assert "coin.spot_instrument_id=spot_identity.instrument_id" in CORRELATOR
    assert "coin.linear_instrument_id=linear_identity.instrument_id" in CORRELATOR
    assert "'market_source_id',g.market_source_id" in CORRELATOR
    assert "'linear_instrument_id',c.linear_instrument_id" in CORRELATOR


def test_dashboard_current_and_causal_views_use_exact_market_identity() -> None:
    assert "FROM mayak_v2.shared_market_contexts" in DASHBOARD
    assert "WHERE market_source_id=%s" in DASHBOARD
    assert "FROM analytics.market_minutes linear" in DASHBOARD
    assert "JOIN analytics.market_minutes spot" in DASHBOARD
    assert "linear.market_source_id=%s" in DASHBOARD
    assert "spot.instrument_id AS spot_instrument_id" in DASHBOARD
    assert "linear.instrument_id AS linear_instrument_id" in DASHBOARD
    assert "coin_market_source_id" in DASHBOARD
    assert "coin_spot_instrument_id" in DASHBOARD
    assert "coin_linear_instrument_id" in DASHBOARD
    assert "global_market_source_id" in DASHBOARD


def test_archive_exports_registry_and_records_public_identity_provenance() -> None:
    for table in (
        "identity.venues",
        "identity.market_sources",
        "identity.instruments",
    ):
        assert f'"{table}"' in ARCHIVE
    for table in (
        "mayak_v2.coin_minutes",
        "mayak_v2.liquidations",
        "mayak_v2.coin_market_contexts",
        "mayak_v2.shared_market_contexts",
        "dispatcher_v2.global_market_contexts",
        "dispatcher_v2.coin_market_contexts",
    ):
        assert f'"{table}"' in ARCHIVE
    assert '"scope": "PUBLIC_MARKET"' in ARCHIVE
    assert '"market_source_counts": public_market_source_counts' in ARCHIVE
    assert '"instrument_counts": public_instrument_counts' in ARCHIVE


def test_public_archive_identity_preserves_legacy_unknown_without_fake_backfill() -> None:
    assert _validate_public_market_identity(
        {
            "market_source_id": None,
            "spot_instrument_id": None,
            "linear_instrument_id": None,
        },
        ("market_source_id", "spot_instrument_id", "linear_instrument_id"),
    ) == ("LEGACY_UNKNOWN", None, ())

    assert _validate_public_market_identity(
        {
            "market_source_id": "ms-bybit-mainnet-public-v1",
            "spot_instrument_id": "inst-bybit-spot-btcusdt",
            "linear_instrument_id": "inst-bybit-linear-btcusdt",
        },
        ("market_source_id", "spot_instrument_id", "linear_instrument_id"),
    ) == (
        "PROVEN",
        "ms-bybit-mainnet-public-v1",
        ("inst-bybit-spot-btcusdt", "inst-bybit-linear-btcusdt"),
    )


def test_public_archive_rejects_partial_identity() -> None:
    with pytest.raises(RuntimeError, match="partial identity"):
        _validate_public_market_identity(
            {
                "market_source_id": "ms-bybit-mainnet-public-v1",
                "spot_instrument_id": None,
                "linear_instrument_id": "inst-bybit-linear-btcusdt",
            },
            ("market_source_id", "spot_instrument_id", "linear_instrument_id"),
        )


def test_mayak_restart_restores_only_exact_linear_liquidation_identity() -> None:
    source = (ROOT / "operations/monitoring/mayak_v2.py").read_text(encoding="utf-8")
    assert "FROM mayak_v2.liquidations l" in source
    assert "JOIN identity.instruments instrument_identity" in source
    assert "instrument_identity.instrument_id=l.instrument_id" in source
    assert "instrument_identity.product_category='LINEAR'" in source
    assert "instrument_identity.external_symbol=l.symbol" in source
    assert "WHERE l.market_source_id=%s" in source
    assert "(BYBIT_VENUE_ID, BYBIT_PUBLIC_MARKET_SOURCE_ID)" in source


def test_scheduled_mayak_reports_separate_proven_from_legacy_public_identity() -> None:
    source = (ROOT / "operations/monitoring/mayak_v2_report.py").read_text(
        encoding="utf-8"
    )
    assert "MAYAK_REPORT_PARTIAL_PUBLIC_IDENTITY" in source
    assert "legacy_unknown_rows" in source
    assert "FROM analytics.market_minutes" in source
    assert "'PROVEN'::text AS identity_status" in source
    assert "'LEGACY_UNKNOWN'::text AS identity_status" in source
    assert "AND market_source_id IS NULL" in source
    assert "AND spot_instrument_id IS NULL" in source
    assert "AND linear_instrument_id IS NULL" in source
    assert '"proven_source": "analytics.market_minutes"' in source
    assert '"legacy_fallback_source": "mayak_v2.coin_minutes"' in source
