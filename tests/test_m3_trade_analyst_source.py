from pathlib import Path


SOURCE = Path("operations/monitoring/m3_trade_analyst.py").read_text(encoding="utf-8")


def test_analyst_is_read_only_for_trading_and_links_exact_ids() -> None:
    assert "runtime.trade_commands(" not in SOURCE
    assert "order_id=%s" in SOURCE and "order_link_id=%s" in SOURCE
    assert "exec_ids" in SOURCE
    assert "entry_decision" in SOURCE and "consumed_context" in SOURCE
    assert "exit_decision" in SOURCE and "close_fill" in SOURCE


def test_analyst_keeps_unknown_funding_explicit() -> None:
    assert '"funding": None' in SOURCE
    assert '"PARTIAL"' in SOURCE
