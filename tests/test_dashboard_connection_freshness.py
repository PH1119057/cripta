from pathlib import Path


def test_operator_connection_distinguishes_portal_from_account_snapshot():
    html = Path("operations/dashboard/index.html").read_text()
    line = next(
        x for x in html.splitlines()
        if x.startswith("function updateOperatorConnection()")
    )
    assert "apiAge<=60" in line
    assert "walletAge<=30" in line
    assert "text='ONLINE'" in line
    assert "text='OLD DATA'" in line
    assert "text='OFFLINE'" in line
    assert "ПОРТАЛ НЕДОСТУПЕН" not in line
    assert "ДАННЫЕ АККАУНТА УСТАРЕЛИ" not in line


def test_operator_connection_status_is_single_line():
    html = Path("operations/dashboard/index.html").read_text()
    assert (
        ".trade-operator-metric #operatorConnection{"
        "white-space:nowrap;min-height:22px}"
    ) in html
