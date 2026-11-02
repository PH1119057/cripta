from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
INDEX = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")


def test_far_p90_rail_exposes_both_h9_distances_without_changing_entry_edges():
    assert '"h9_5m_low_raw"' in APP
    assert '"h9_15m_low_raw"' in APP
    assert '"h9_5m_high_raw"' in APP
    assert '"h9_15m_high_raw"' in APP
    assert '"h9_distances": h9_distances' in APP
    assert '"long_distance_pct"' in APP
    assert '"short_distance_pct"' in APP
    assert "H9 LONG " in INDEX
    assert "SHORT " in INDEX
    assert "Entry требует overlap с L5-3" in INDEX
    assert '("LONG", "l53_low_raw", "long_depth_pct")' in APP
    assert '("SHORT", "l53_high_raw", "short_depth_pct")' in APP


def test_open_p90_rail_projects_exchange_protection_and_expected_net():
    for token in (
        '"take_profit_price"',
        '"take_profit_move_pct"',
        '"take_profit_expected_net_usdt"',
        '"stop_loss_price"',
        '"stop_loss_move_pct"',
        '"stop_loss_expected_net_usdt"',
        '"trailing_armed"',
        '"trailing_distance_pct"',
        '"protection_net_basis"',
    ):
        assert token in APP
    assert "AFTER_COMMISSIONS_ACTUAL_ENTRY_FEE_NO_FUNDING" in APP
    assert "TP '+signedPct" in INDEX
    assert "SL '+signedPct" in INDEX
    assert "TRAIL ON" in INDEX
    assert "TRAIL OFF" in INDEX


def test_stale_diagnostic_and_command_failures_leave_current_surfaces():
    assert "P90_DIAGNOSTIC_VISIBLE_MS=15*60*1000" in INDEX
    assert "function currentP90Diagnostic(diag)" in INDEX
    assert "currentP90Diagnostic(" in INDEX
    assert "commandVisibleMs=15*60*1000" in INDEX
    assert "Исторический отказ команды" not in INDEX


def test_first_pass_remains_read_only_projection():
    live_state = APP.split("def _live_trading_state(", 1)[1].split(
        "\ndef ", 1
    )[0].upper()
    for token in (
        "INSERT INTO ",
        "UPDATE ",
        "DELETE FROM ",
        "ALTER TABLE ",
        "DROP TABLE ",
        "TRUNCATE ",
    ):
        assert token not in live_state


def test_p90_left_rail_sorts_and_colors_by_nearest_h9_proximity():
    assert "function p90RailH9Proximity(item)" in INDEX
    assert "Math.min(...values)" in INDEX
    assert "value!==null&&value!==undefined&&value!==''" in INDEX
    assert "rows.sort((a,b)=>{" in INDEX
    assert "if(aOpen!==bOpen)return aOpen?-1:1" in INDEX
    assert "if(aOpen&&bOpen)return 0" in INDEX
    assert "if(aDistance!==bDistance)return aDistance-bDistance" in INDEX
    assert "h9Proximity<0.5" in INDEX
    assert "visualNear?'p90-near':'p90-far'" in INDEX
    assert "БЛИЗКО К H9" in INDEX
    assert "песочный — до ближайшей H9 &lt; 0,5%" in INDEX
    assert "Entry всё равно требует overlap с L5-3" in INDEX
