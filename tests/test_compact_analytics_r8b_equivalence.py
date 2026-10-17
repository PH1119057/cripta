from datetime import UTC, datetime
from pathlib import Path

from bybit_workbench.mayak.compact_analytics import compact_market_minute_rows
from bybit_workbench.mayak.compact_reader import (
    R8B_DIRECT_PATHS,
    R8B_EXACT_EVENT_FAMILIES,
    R8B_RECOMPUTABLE_PATHS,
    R8B_REQUIRED_ANALYTICAL_PATHS,
    R8B_UNCOVERED_REQUIRED_PATHS,
    compare_compact_pair_to_context,
)
from bybit_workbench.mayak.core.live import LiveMayakEngine

ROOT = Path(__file__).resolve().parents[1]
REPORT = (ROOT / "operations/monitoring/mayak_v2_report.py").read_text(
    encoding="utf-8"
)
COMPACT_WRITER = (
    ROOT / "src/bybit_workbench/mayak/compact_analytics.py"
).read_text(encoding="utf-8")


def _engine() -> LiveMayakEngine:
    item = LiveMayakEngine(("BTCUSDT", "ETHUSDT"))
    now = datetime(2026, 10, 17, 5, 30, tzinfo=UTC).timestamp()
    for market in ("spot", "linear"):
        item.set_instrument_support(market, set(item.symbols))
        item.on_transport(market, connected=True, timestamp=now - 3600)
        item.on_transport(market, connected=True, timestamp=now)
    return item


def _snapshot() -> dict[str, object]:
    now = datetime(2026, 10, 17, 5, 30, tzinfo=UTC)
    timestamp = now.timestamp()
    item = _engine()
    for symbol in item.symbols:
        item.on_trade("spot", symbol, timestamp - 299, "Buy", 100, 1)
        item.on_trade("spot", symbol, timestamp - 10, "Sell", 101, 0.25)
        item.on_trade("linear", symbol, timestamp - 299, "Buy", 100, 2)
        item.on_trade("linear", symbol, timestamp - 10, "Sell", 102, 0.5)
        item.on_book(
            "spot",
            symbol,
            timestamp - 10,
            [(99.9, 3), (99.8, 4)],
            [(100.1, 2), (100.2, 5)],
        )
        item.on_book(
            "linear",
            symbol,
            timestamp - 10,
            [(100.0, 4), (99.9, 6)],
            [(100.2, 3), (100.3, 5)],
        )
        item.on_ticker(
            symbol,
            timestamp - 301,
            open_interest=100,
            funding_rate=0.00005,
        )
        item.on_ticker(
            symbol,
            timestamp,
            open_interest=110,
            funding_rate=0.0001,
            last_price=102,
            mark_price=101,
            index_price=100,
            long_ratio=0.52,
            short_ratio=0.48,
        )
    return item.snapshot(now)


def _btc_rows(snapshot: dict[str, object]) -> tuple[dict[str, object], ...]:
    return tuple(
        row
        for row in compact_market_minute_rows(77, snapshot)
        if row["external_symbol"] == "BTCUSDT"
    )


def test_r8b_feature_coverage_is_exhaustive_and_fail_closed() -> None:
    assert not (R8B_DIRECT_PATHS & R8B_RECOMPUTABLE_PATHS)
    assert (
        R8B_DIRECT_PATHS
        | R8B_RECOMPUTABLE_PATHS
        | R8B_UNCOVERED_REQUIRED_PATHS
    ) == R8B_REQUIRED_ANALYTICAL_PATHS
    assert "liquidations.*" in R8B_EXACT_EVENT_FAMILIES
    assert "money.derivatives.5m.net_usd" in R8B_DIRECT_PATHS
    assert "money.derivatives.5m.net_share" in R8B_RECOMPUTABLE_PATHS
    assert "money.derivatives.1m.net_usd" in R8B_UNCOVERED_REQUIRED_PATHS
    assert "liquidity.derivatives.bid_depth_10bps_usd" in R8B_UNCOVERED_REQUIRED_PATHS
    assert "positioning.open_interest_change_60m_pct" in R8B_UNCOVERED_REQUIRED_PATHS
    assert len(R8B_UNCOVERED_REQUIRED_PATHS) == 182


def test_compact_writer_uses_frozen_coin_context_not_earlier_coin_snapshot() -> None:
    snapshot = _snapshot()
    context = snapshot["coin_market_contexts"]["BTCUSDT"]["payload"]
    expected = context["money"]["derivatives"]["5m"]["net_usd"]
    snapshot["coins"]["BTCUSDT"]["linear"]["net_usd"] = 999999999.0
    rows = _btc_rows(snapshot)
    linear = next(row for row in rows if row["product_category"] == "LINEAR")
    assert linear["net_usd"] == expected
    assert linear["net_usd"] != 999999999.0
    assert "contexts = _mapping(snapshot.get(\"coin_market_contexts\"))" in COMPACT_WRITER
    assert 'coins = _mapping(snapshot.get("coins"))' not in COMPACT_WRITER


def test_r8a_covered_projection_matches_current_coin_context_exactly() -> None:
    snapshot = _snapshot()
    rows = _btc_rows(snapshot)
    payload = snapshot["coin_market_contexts"]["BTCUSDT"]["payload"]
    parity = compare_compact_pair_to_context(rows, payload)
    assert parity.mismatches == ()
    assert "price.returns.5m" in parity.checked_paths
    assert "money.spot.5m.net_share" in parity.checked_paths
    assert "positioning.mark_index_premium_pct" in parity.checked_paths
    assert "liquidity.derivatives.imbalance" in parity.checked_paths
    assert parity.uncovered_paths
    assert parity.replacement_ready is False


def test_compact_parity_detects_tampered_fact() -> None:
    snapshot = _snapshot()
    rows = [dict(row) for row in _btc_rows(snapshot)]
    linear = next(row for row in rows if row["product_category"] == "LINEAR")
    linear["net_usd"] = float(linear["net_usd"]) + 1
    payload = snapshot["coin_market_contexts"]["BTCUSDT"]["payload"]
    parity = compare_compact_pair_to_context(rows, payload)
    assert "money.derivatives.5m.net_usd" in parity.mismatches
    assert parity.replacement_ready is False


def test_scheduled_report_is_compact_only_after_r10_cutover() -> None:
    assert "FROM analytics.market_minutes" in REPORT
    assert '"proven_source": "analytics.market_minutes"' in REPORT
    assert '"legacy_fallback_source": None' in REPORT
    assert "FROM mayak_v2.coin_minutes" not in REPORT
    assert "compact_replacement_ready()" in REPORT
