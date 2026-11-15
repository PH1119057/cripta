"""R13 #470: safety independence and downstream lineage source contracts."""
from pathlib import Path

TRACKER = Path(
    "research/server/monitoring/opportunity_tracker.py"
).read_text(encoding="utf-8")
TRACKER_SERVICE = Path(
    "research/server/monitoring/cripta-opportunity-tracker.service"
).read_text(encoding="utf-8")
REGISTER = Path(
    "research/server/monitoring/register_opportunity.py"
).read_text(encoding="utf-8")
ANALYST = Path(
    "operations/monitoring/m3_trade_analyst.py"
).read_text(encoding="utf-8")
DASHBOARD = Path(
    "operations/dashboard/app.py"
).read_text(encoding="utf-8")
DASHBOARD_HTML = Path(
    "operations/dashboard/index.html"
).read_text(encoding="utf-8")
LIFECYCLE = Path(
    "operations/monitoring/lifecycle_supervisor_runtime.py"
).read_text(encoding="utf-8")
ACCOUNT_LIFECYCLE = Path(
    "operations/monitoring/account_lifecycle_worker.py"
).read_text(encoding="utf-8")
SAFETY = Path(
    "research/server/connectivity/safety_observer.py"
).read_text(encoding="utf-8")
MIGRATION = Path(
    "operations/sql/20261115_r13_opportunity_source_checkpoint.sql"
).read_text(encoding="utf-8")


def test_r13_opportunity_has_no_independent_public_network_reader() -> None:
    lowered = TRACKER.lower()
    assert "websocket" not in lowered
    assert "stream.bybit" not in lowered
    assert "publictrade." not in lowered
    assert "read_opportunity_trade_batch" in TRACKER
    assert "opportunity_source_checkpoints" in TRACKER
    assert '"completed_partial"' in TRACKER


def test_r13_opportunity_service_loads_installed_application_package() -> None:
    assert "User=cripta" in TRACKER_SERVICE
    assert "Group=cripta" in TRACKER_SERVICE
    assert "Environment=PYTHONPATH=/srv/cripta/runtime/current/src" in TRACKER_SERVICE
    assert (
        "ExecStart=/srv/cripta/runtime/current/.venv/bin/python "
        "/srv/cripta/runtime/current/research/server/monitoring/opportunity_tracker.py"
        in TRACKER_SERVICE
    )


def test_r13_registration_uses_publication_ticker_not_bybit_rest() -> None:
    lowered = REGISTER.lower()
    assert "urllib" not in lowered
    assert "api.bybit" not in lowered
    assert "/v5/market/tickers" not in lowered
    assert "read_publication_tickers" in REGISTER
    assert '"trading_rights": "NONE"' in REGISTER


def test_r13_analyst_preserves_lineage_without_market_reader() -> None:
    assert '"market_lineage"' in ANALYST
    assert "market_publication_id" in ANALYST
    assert "source_session_id" in ANALYST
    assert "producer_release_commit" in ANALYST
    assert '"trading_rights": "NONE"' in ANALYST
    assert "api.bybit.kz" not in ANALYST
    assert "websocket" not in ANALYST.lower()


def test_r13_dashboard_public_market_is_pg_read_model_only() -> None:
    assert "read_publication_tickers" in DASHBOARD
    assert "read_market_publication_quality" in DASHBOARD
    assert "dashboard-market-publication" in DASHBOARD
    assert "dashboard-market-quality" in DASHBOARD
    assert "https://api.bybit.kz/v5/market/tickers?category=linear" not in DASHBOARD
    assert '"market_publication_quality": market_publication_quality' in DASHBOARD
    assert "MarketPublication · качество источника" in DASHBOARD_HTML
    assert 'id="marketPublicationQuality"' in DASHBOARD_HTML
    assert "не заменяет независимый signed Private safety" in DASHBOARD_HTML


def test_r13_private_lifecycle_and_safety_authority_is_preserved() -> None:
    assert "direct_account_snapshot" in LIFECYCLE
    assert "read_account_safety" in ACCOUNT_LIFECYCLE
    assert "direct_account_snapshot" in ACCOUNT_LIFECYCLE
    assert "/v5/account/wallet-balance" in SAFETY
    assert "/v5/position/list" in SAFETY
    assert "/v5/order/realtime" in SAFETY
    assert "R13" not in LIFECYCLE
    assert "r13_analytics_source" not in ACCOUNT_LIFECYCLE
    assert "r13_analytics_source" not in SAFETY


def test_r13_checkpoint_acl_is_bounded_and_non_destructive() -> None:
    assert "opportunity_source_checkpoints" in MIGRATION
    assert "GRANT SELECT,INSERT,UPDATE" in MIGRATION
    assert "REVOKE DELETE,TRUNCATE" in MIGRATION
    assert "source_quality IN ('CURRENT','GAP')" in MIGRATION
    assert "GRANT DELETE" not in MIGRATION
