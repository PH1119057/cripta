"""Source contract: Strategy account selector stays separate from trading policy."""
from pathlib import Path

DASHBOARD = Path("operations/dashboard/app.py")
UI = Path("operations/dashboard/index.html")


def _route_span(source: str, start: str, end: str) -> str:
    assert start in source and end in source
    return source.split(start, 1)[1].split(end, 1)[0]


def test_dashboard_financial_account_routes_are_authenticated_and_fail_closed():
    s = DASHBOARD.read_text(encoding="utf-8")
    assert 'U6_STRATEGY_FINANCIAL_ACCOUNTS_PATH = "/api/strategies/financial-accounts"' in s
    assert 'U6_STRATEGY_FINANCIAL_ACCOUNT_SET_PATH = "/api/strategies/financial-account"' in s
    get = _route_span(s, "def do_GET(self)", "def do_POST(self)")
    post = _route_span(s, "def do_POST(self)", "def send_report_path(self")
    assert get.index("self.require_login()") < get.index(
        "path == U6_STRATEGY_FINANCIAL_ACCOUNTS_PATH"
    )
    assert post.index("self.require_login()") < post.index(
        "path in U6_STRATEGY_POST_PATHS"
    )
    route = _route_span(s, "def _u6_set_financial_account(", "def _u6_handle_post(")
    assert "_u6_financial_account_same_origin(handler)" in route
    assert 'request.get("confirmed") is not True' in route
    assert "handler.session_user()" in route
    assert "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE" in route
    assert "a.enabled=false" in route
    assert "active_execution is not None" in route
    assert 'execution_leg="PRIMARY"' in route
    assert "record_financial_account_choice(" in route
    assert "UPDATE control.execution_gates" not in route
    assert "strategy_entry.strategy_cards" not in route
    assert "BYBIT_API_SECRET" not in route


def test_account_catalog_exposes_only_nonsecret_registered_identity():
    s = DASHBOARD.read_text(encoding="utf-8")
    catalog = _route_span(
        s, "def _u6_financial_account_catalog(", "def _u6_financial_account_same_origin("
    )
    assert "SET TRANSACTION READ ONLY" in catalog
    assert '"trading_account_id": str(account)' in catalog
    assert '"account_connector_id": str(connector)' in catalog
    assert '"selectable": decision.ready' in catalog
    assert "external_account_ref" not in catalog
    assert "api_key" not in catalog and "api_secret" not in catalog
    assert "credential_ref" not in catalog


def test_strategy_ui_shows_account_selection_without_mutating_card_fields():
    html = UI.read_text(encoding="utf-8")
    assert "function u6FinancialAccountHtml(" in html
    assert "function u6SaveFinancialAccount(" in html
    assert "/api/strategies/financial-accounts" in html
    assert "/api/strategies/financial-account" in html
    assert "${u6FinancialAccountHtml(x,i)}" in html
    assert "u6FinancialAccounts" in html
    assert "if(!account?.selectable)" in html
    assert "u6StrategyEditorHtml" in html
    assert "Привязка PRIMARY сохранена" in html
