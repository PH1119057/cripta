import inspect
from pathlib import Path

from operations.dashboard import app as dashboard


def test_p90_rail_is_inside_approved_live_read_model_scope():
    source = inspect.getsource(dashboard._live_trading_state)
    assert 'P90_MAIN_ID: ("INJUSDT", "LTCUSDT")' in source
    assert 'P90_SUB_ID: ("APTUSDT", "ARBUSDT", "DOTUSDT")' in source
    assert "strategy_entry.p90l90_real_causal_checkpoints" in source
    assert "strategy_entry.p90l90_real_source_epochs" in source
    assert "strategy_entry.strategy_execution_bindings" in source
    assert "runtime.hot_positions" in source
    assert "runtime.position_ownership" in source


def test_p90_rail_uses_canonical_edges_and_strict_half_percent_threshold():
    source = inspect.getsource(dashboard._live_trading_state)
    assert '("LONG", "l53_low_raw", "long_depth_pct")' in source
    assert '("SHORT", "l53_high_raw", "short_depth_pct")' in source
    assert 'Decimal(str(nearest["distance_pct"])) < Decimal("0.5")' in source
    assert 'state = "OPEN"' in source
    assert 'state = "NEAR"' in source
    assert 'state = "FAR"' in source
    assert 'int(item["rank"])' in source


def test_p90_rail_pnl_is_account_aware_and_after_commissions():
    source = inspect.getsource(dashboard._live_trading_state)
    assert "trading_account_id=ANY(%s)" in source
    assert "p90_account_for_symbol" in source
    assert "p90_ownership_fees" in source
    assert "REAL_IMMEDIATE_CLOSE_FEE_RATE" in source
    assert "AFTER_COMMISSIONS_ACTUAL_ENTRY_FEE" in source
    assert "AFTER_COMMISSIONS_ESTIMATED_ENTRY_FEE" in source


def test_p90_rail_backend_projection_has_no_mutation_sql():
    source = inspect.getsource(dashboard._live_trading_state).upper()
    for token in (
        "INSERT INTO ",
        "UPDATE ",
        "DELETE FROM ",
        "ALTER TABLE ",
        "DROP TABLE ",
        "TRUNCATE ",
    ):
        assert token not in source


def test_left_rail_frontend_uses_p90_projection_and_requested_colors():
    index = (
        Path(__file__).resolve().parents[1] / "operations/dashboard/index.html"
    ).read_text(encoding="utf-8")
    assert "Открытые сделки / P90" in index
    assert "жёлтый — до Entry &lt; 0,5%" in index
    assert "p90-open" in index
    assert "p90-near" in index
    assert "p90-far" in index
    assert "renderOpenRailPositions(positions);" in index
    assert "renderP90RailPositions(lt.p90_rail?.items||[],positions)" in index
