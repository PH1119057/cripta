from pathlib import Path

APP = Path("operations/dashboard/app.py")
HTML = Path("operations/dashboard/index.html")


def test_dashboard_projects_active_clock_safety_until_reconciliation() -> None:
    app = APP.read_text(encoding="utf-8")
    assert '"clock_safety_alerts": clock_safety_alerts' in app
    assert "details_json::jsonb->>'error'='UnsafeBybitClock'" in app
    assert "PRE_MUTATION_SAFETY_BLOCK:UnsafeBybitClock%" in app
    assert "runtime.reconciliation_runs" in app
    assert "AND ok=1" in app
    assert "AND ok=true" not in app
    assert "recovered_at_epoch_ms < b.blocked_at_epoch_ms" in app
    assert '"reason": "BYBIT_TIME_PROOF_UNAVAILABLE"' in app
    assert '"mutation_sent": False' in app


def test_dashboard_renders_visible_clock_safety_operator_alert() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert 'id="clockSafetyAlert"' in html
    assert "function renderClockSafetyAlert(lt)" in html
    assert "ENTRY временно заблокирован" in html
    assert "Bybit time proof unavailable" in html
    assert "Ордер не отправлен" in html
    assert "renderClockSafetyAlert(d.live_trading||{})" in html
