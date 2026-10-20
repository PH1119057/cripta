from pathlib import Path


def test_real_consumer_rechecks_registered_connector_before_dispatch():
    source = Path("operations/connectivity/universal_entry_consumer.py").read_text(
        encoding="utf-8"
    )
    begin = source.index("def _admission_pre_dispatch_status(")
    end = source.index("\ndef ", begin + 5)
    check = source[begin:end]
    assert "FROM identity.account_connectors" in check
    assert "WHERE trading_account_id=%s AND account_connector_id=%s" in check
    assert '(request.trading_account_id, ACCOUNT_CONNECTOR_ID)' in check
    assert 'str(connector["status"]) != "ACTIVE"' in check
    assert "if not ACCOUNT_CONNECTOR_ID:" in check
    assert '"ACCOUNT_CONNECTOR_IDENTITY_NOT_ACTIVE"' in check
    assert check.index("FROM identity.account_connectors") < check.index(
        "account_ref = str(row.get"
    )
