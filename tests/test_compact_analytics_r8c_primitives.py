import json
from datetime import UTC, datetime
from pathlib import Path

from bybit_workbench.mayak.compact_analytics import (
    COMPACT_ANALYTICS_SCHEMA_VERSION,
    COMPACT_MARKET_MINUTE_INSERT_COLUMNS,
    compact_market_minute_insert_sql,
    compact_market_minute_rows,
)
from bybit_workbench.mayak.compact_reader import (
    R8C_DIRECT_PATHS,
    R8C_RECOMPUTABLE_PATHS,
    R8C_REQUIRED_ANALYTICAL_PATHS,
    R8C_UNCOVERED_REQUIRED_PATHS,
    compact_panel_projection,
    compact_replacement_ready,
    compare_compact_panel_to_contexts,
)
from bybit_workbench.mayak.core.live import LiveMayakEngine
from bybit_workbench.mayak.research.compact_market_replay import (
    iter_archived_compact_panels,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "operations/sql/20261017_compact_analytics_r8c.sql"
).read_text(encoding="utf-8")
MAYAK = (ROOT / "operations/monitoring/mayak_v2.py").read_text(encoding="utf-8")
RUNTIME_SCHEMA = (
    ROOT / "operations/connectivity/runtime_schema.py"
).read_text(encoding="utf-8")
INSTALLER = (
    ROOT / "operations/infrastructure/install_verified_release.sh"
).read_text(encoding="utf-8")


def _full_snapshot() -> dict[str, object]:
    now = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)
    timestamp = now.timestamp()
    symbols = ("BTCUSDT", "ETHUSDT", "ADAUSDT")
    item = LiveMayakEngine(symbols)

    for market in ("spot", "linear"):
        item.set_instrument_support(market, set(symbols))
        item.on_transport(market, connected=True, timestamp=timestamp - 7200)
        item.on_transport(market, connected=True, timestamp=timestamp)

    trade_offsets = (-7000, -3000, -1200, -500, -90, -50, -10)
    for symbol_index, symbol in enumerate(symbols):
        for market_index, market in enumerate(("spot", "linear")):
            for event_index, offset in enumerate(trade_offsets):
                price = 100 + symbol_index * 3 + market_index + event_index * 0.2
                side = "Buy" if event_index % 2 == 0 else "Sell"
                size = 1 + symbol_index * 0.1 + event_index * 0.05
                item.on_trade(market, symbol, timestamp + offset, side, price, size)

            for offset, scale in ((-910, 1.0), (-310, 1.1), (-70, 1.2), (0, 1.3)):
                mid = 100 + symbol_index * 3 + market_index + scale
                item.on_book(
                    market,
                    symbol,
                    timestamp + offset,
                    [(mid - 0.1, 4 * scale), (mid - 0.2, 6 * scale)],
                    [(mid + 0.1, 5 * scale), (mid + 0.2, 7 * scale)],
                )

        for event_index, offset in enumerate((-3700, -1900, -901, -601, -301, 0)):
            oi = 1000 + symbol_index * 100 + event_index * 25
            item.on_ticker(
                symbol,
                timestamp + offset,
                open_interest=oi,
                open_interest_value=oi * (100 + symbol_index),
                funding_rate=0.00001 * (event_index + 1),
                last_price=102 + symbol_index,
                mark_price=101 + symbol_index,
                index_price=100 + symbol_index,
                long_ratio=0.51 + symbol_index * 0.01,
                short_ratio=0.49 - symbol_index * 0.01,
            )

    return item.snapshot(now)


def test_r8c_migration_is_additive_compact_and_account_independent() -> None:
    upper = MIGRATION.upper()
    for forbidden in (" DROP ", " DELETE ", " TRUNCATE ", " UPDATE "):
        assert forbidden not in upper
    assert MIGRATION.count("ADD COLUMN IF NOT EXISTS") == 64
    assert "jsonb" not in MIGRATION.lower()
    assert "trading_account_id" not in MIGRATION
    assert "strategy_id" not in MIGRATION
    assert "market_minutes_r8c_linear_only" in MIGRATION


def test_r8c_compact_shape_covers_every_known_research_path() -> None:
    assert len(R8C_REQUIRED_ANALYTICAL_PATHS) == 233
    assert len(R8C_DIRECT_PATHS) == 158
    assert len(R8C_RECOMPUTABLE_PATHS) == 75
    assert not (R8C_DIRECT_PATHS & R8C_RECOMPUTABLE_PATHS)
    assert not R8C_UNCOVERED_REQUIRED_PATHS
    assert compact_replacement_ready() is True


def test_r8c_writer_has_exact_static_column_key_parity() -> None:
    snapshot = _full_snapshot()
    rows = compact_market_minute_rows(77, snapshot)
    assert len(rows) == 6
    assert COMPACT_ANALYTICS_SCHEMA_VERSION == "compact-market-minute-v3"
    assert len(COMPACT_MARKET_MINUTE_INSERT_COLUMNS) == 105
    assert all(set(row) == set(COMPACT_MARKET_MINUTE_INSERT_COLUMNS) for row in rows)
    sql = compact_market_minute_insert_sql()
    assert sql.startswith("INSERT INTO analytics.market_minutes(")
    assert sql.count("%(") == len(COMPACT_MARKET_MINUTE_INSERT_COLUMNS)
    assert "ON CONFLICT(market_source_id,instrument_id,observed_minute)" in sql
    assert "compact_market_minute_insert_sql()" in MAYAK


def test_r8c_full_panel_matches_live_context_for_all_233_paths() -> None:
    snapshot = _full_snapshot()
    rows = compact_market_minute_rows(77, snapshot)
    report = compare_compact_panel_to_contexts(
        rows,
        snapshot["coin_market_contexts"],
    )
    assert report.checked_symbols == ("ADAUSDT", "BTCUSDT", "ETHUSDT")
    assert report.checked_paths == 3 * 233
    assert report.mismatches == ()
    assert report.uncovered_paths == ()
    assert report.replacement_ready is True

    projection = compact_panel_projection(rows)
    assert projection["ADAUSDT"]["money"]["derivatives"]["60m"]["turnover_usd"] is not None
    assert projection["BTCUSDT"]["liquidity"]["spot"]["bid_depth_10bps_usd"] is not None
    assert (
        projection["ETHUSDT"]["positioning"]["open_interest_change_60m_pct"]
        is not None
    )
    assert (
        projection["ADAUSDT"]["relative_strength"]["15m"]["relative_to_btc_pct"]
        is not None
    )


def test_r8c_panel_parity_detects_high_horizon_tamper() -> None:
    snapshot = _full_snapshot()
    rows = [dict(row) for row in compact_market_minute_rows(77, snapshot)]
    linear_btc = next(
        row
        for row in rows
        if row["external_symbol"] == "BTCUSDT"
        and row["product_category"] == "LINEAR"
    )
    linear_btc["flow_60m_net_usd"] = float(linear_btc["flow_60m_net_usd"]) + 1
    report = compare_compact_panel_to_contexts(
        rows,
        snapshot["coin_market_contexts"],
    )
    assert "BTCUSDT:money.derivatives.60m.net_usd" in report.mismatches
    assert report.replacement_ready is False


def test_r8c_archive_research_reader_streams_exact_panel(tmp_path: Path) -> None:
    snapshot = _full_snapshot()
    rows = compact_market_minute_rows(77, snapshot)
    path = tmp_path / "market_minutes.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, default=str, sort_keys=True) + "\n")

    panels = list(iter_archived_compact_panels(path))
    assert len(panels) == 1
    panel = panels[0]
    assert panel.market_source_id == "ms-bybit-mainnet-public-v1"
    assert len(panel.rows) == 6
    payloads = panel.analytical_payloads()
    assert set(payloads) == {"BTCUSDT", "ETHUSDT", "ADAUSDT"}

    report = compare_compact_panel_to_contexts(
        panel.rows,
        snapshot["coin_market_contexts"],
    )
    assert report.mismatches == ()
    assert report.replacement_ready is True


def test_r8c_archive_reader_rejects_wrong_schema(tmp_path: Path) -> None:
    snapshot = _full_snapshot()
    row = dict(compact_market_minute_rows(77, snapshot)[0])
    row["schema_version"] = "compact-market-minute-v2"
    path = tmp_path / "market_minutes.jsonl"
    path.write_text(json.dumps(row, default=str) + "\n", encoding="utf-8")

    try:
        list(iter_archived_compact_panels(path))
    except ValueError as exc:
        assert "compact archive schema mismatch" in str(exc)
    else:
        raise AssertionError("R8c reader accepted an old compact schema")


def test_r8c_release_contract_requires_new_migration_and_columns() -> None:
    r8a = INSTALLER.index("operations/sql/20261017_compact_analytics_r8a.sql")
    r8c = INSTALLER.index("operations/sql/20261017_compact_analytics_r8c.sql")
    assert r8a < r8c
    assert "runtime-schema-2026-10-17-compact-analytics-r8c" in RUNTIME_SCHEMA
    for column in (
        "flow_60m_acceleration_usd_per_min2",
        "bid_depth_50bps_usd",
        "imbalance_change_15m",
        "open_interest_change_60m_pct",
        "funding_rate_change_from_previous",
    ):
        assert f'("analytics", "market_minutes", "{column}")' in RUNTIME_SCHEMA
