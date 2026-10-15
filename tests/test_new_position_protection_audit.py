from pathlib import Path


def test_new_position_identity_forces_fresh_authenticated_exchange_read():
    source = Path(
        "operations/monitoring/lifecycle_supervisor_runtime.py"
    ).read_text(encoding="utf-8")
    # The active LIVE-arm cohort already contributes all five symbols to
    # the account snapshot scope before an Entry creates its position.
    # Checking symbol-set changes alone misses the first protection audit.
    assert 'position_ids = frozenset(str(p["position_id"]) for p in positions)' in source
    assert 'new_position_identity = position_ids != last_position_ids' in source
    assert "if new_position_identity:\n                account = None" in source
    assert (
        "or new_position_identity\n                or time.monotonic() >= next_read"
        in source
    )
    assert "last_position_ids = position_ids" in source
    assert "_independent_exchange_snapshot(symbols)" in source
    assert "account_protection_verdicts(" in source


def test_no_grace_period_or_failure_suppression_added():
    source = Path(
        "operations/monitoring/lifecycle_supervisor_runtime.py"
    ).read_text(encoding="utf-8")
    assert "account = None" in source
    assert "account_read_complete = bool(" in source
    assert "protection = account_protection_verdicts(" in source
    assert "protection_grace_seconds" not in source
    assert "skip_initial_protection_fault" not in source


def test_entry_order_carries_exchange_server_side_sl_tp():
    source = Path(
        "operations/connectivity/private_runtime.py"
    ).read_text(encoding="utf-8")
    beginning = source.index('    elif kind == "entry":')
    end = source.index('        result=api_post("/v5/order/create", order, key, secret)', beginning)
    entry = source[beginning:end]
    assert '"stopLoss": str(stop)' in entry
    assert '"slOrderType": "Market"' in entry
    assert '"takeProfit": str(target)' in entry
    assert '"tpOrderType": "Market"' in entry
    assert '"PostOnly"' in entry
