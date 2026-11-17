"""Dashboard-only P90 freshness boundaries after R06 MarketPublication cutover.

Read-model evidence only: stale legacy geometry must not masquerade as a
current Entry. Neither Strategy policy nor live trading paths are changed.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
HTML = (ROOT / "operations/dashboard/index.html").read_text(encoding="utf-8")


def test_legacy_geometry_has_explicit_freshness_and_never_drives_stale_entry():
    assert "p90_geometry_max_age_seconds = 15 * 60" in APP
    assert "0 <= checkpoint_age_seconds <= p90_geometry_max_age_seconds" in APP
    assert 'entry = checkpoint.get("entry", {}) if checkpoint and geometry_fresh else {}' in APP
    assert 'state = "STALE"' in APP
    assert '"geometry_fresh": geometry_fresh' in APP
    assert '"checkpoint_age_seconds": checkpoint_age_seconds' in APP
    assert '"checkpoint_updated_at"' in APP
    assert '"h9_distances": h9_distances' in APP


def test_current_price_comes_from_shared_publication_not_old_previous_price():
    block = APP.split("checkpoint = p90_checkpoints.get(symbol)", 1)[1]
    block = block.split("candidates: list[dict[str, object]]", 1)[0]
    assert 'ticker = tickers.get(symbol) or {}' in block
    assert block.index('ticker.get("last_price")') < block.index(
        'entry.get("previous_price")'
    )
    assert "if current_price is None and geometry_fresh:" in block
    assert '"current_price_source": current_price_source' in APP
    assert '"current_price_publication_id": ticker.get("publication_id")' in APP


def test_stale_ui_never_renders_h9_distance_or_entry_readiness_as_current():
    section = HTML.split("function renderP90RailPositions(", 1)[1].split(
        "let livePollTimer=", 1
    )[0]
    stale = section.split("if(geometryStale){", 1)[1].split(
        "const h9=item.h9_distances", 1
    )[0]
    assert "ГЕОМЕТРИЯ УСТАРЕЛА" in stale
    assert "Актуальные H9 / L5-3 и расстояние до Entry не подтверждены" in stale
    assert "Последний causal checkpoint:" in stale
    assert "return " in stale
    assert "h9NearestDistance" not in stale
    assert "ownerFullContourDiagnostic(" not in stale
    assert "const p=item.position||{}" in section
    assert "renderP90RailPositions(lt.p90_rail?.items||[],positions)" in HTML


def test_right_history_refresh_is_observable_and_remains_exact_read_only():
    assert "railLastFetchedAt=Date.now()" in HTML
    assert " · проверено " in HTML
    assert "fetch('/api/live/state?view=closed',{cache:'no-store'})" in HTML
    history_widget = HTML.split(
        "/* Right rail is a presentation-only view", 1
    )[1]
    assert "method:'POST'" not in history_widget
