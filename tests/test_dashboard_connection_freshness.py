from pathlib import Path


def test_operator_connection_distinguishes_portal_from_exchange():
    html = Path("operations/dashboard/index.html").read_text()
    line = next(x for x in html.splitlines() if x.startswith("function updateOperatorConnection()"))
    assert "apiAge<=60" in line
    assert "walletAge<=30" in line
    assert "ПОРТАЛ НЕДОСТУПЕН" in line
    assert "ДАННЫЕ АККАУНТА УСТАРЕЛИ" in line
    assert "НЕТ СВЯЗИ" not in line
