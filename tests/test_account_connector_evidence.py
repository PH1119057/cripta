from bybit_workbench.account_connector_evidence import (
    check_account_connector_evidence,
)


def account(key, *, status="ACTIVE", environment="MAINNET"):
    return dict(trading_account_id=key, venue_id="venue-bybit",
                status=status, environment=environment)


def connector(key, account_id, *, status="ACTIVE", adapter="bybit", credential="secret-ref"):
    return dict(account_connector_id=key, trading_account_id=account_id,
                adapter_id=adapter, credential_ref=credential, status=status)


def test_one_strategy_accounts_with_different_venues_are_independently_registered():
    result = check_account_connector_evidence(
        required_account_ids=["bybit-a", "okx-b"],
        accounts=[account("bybit-a"), dict(account("okx-b"), venue_id="venue-okx")],
        connectors=[connector("c-1", "bybit-a"), connector("c-2", "okx-b", adapter="okx")],
    )
    assert result.ready
    assert result.account_connectors["bybit-a"] == ("c-1",)


def test_account_without_connector_fails_closed():
    result = check_account_connector_evidence(
        required_account_ids=["a"], accounts=[account("a")], connectors=[]
    )
    assert not result.ready
    assert "ACTIVE_ACCOUNT_CONNECTOR_MISSING" in result.reasons


def test_disabled_connector_cannot_prove_readiness():
    result = check_account_connector_evidence(
        required_account_ids=["a"], accounts=[account("a")],
        connectors=[connector("c", "a", status="RETIRED")],
    )
    assert not result.ready


def test_missing_registered_account_blocks_even_with_connector():
    result = check_account_connector_evidence(
        required_account_ids=["a"], accounts=[],
        connectors=[connector("c", "a")],
    )
    assert "ACCOUNT_IDENTITY_NOT_REGISTERED" in result.reasons


def test_wrong_environment_and_missing_adapter_fail():
    result = check_account_connector_evidence(
        required_account_ids=["a"], accounts=[account("a", environment="TESTNET")],
        connectors=[connector("c", "a", adapter="")],
    )
    assert {"ACCOUNT_ENVIRONMENT_NOT_MAINNET", "CONNECTOR_ADAPTER_MISSING"} <= set(result.reasons)


def test_missing_credentials_reference_cannot_prove_registration():
    result = check_account_connector_evidence(
        required_account_ids=["a"], accounts=[account("a")],
        connectors=[connector("c", "a", credential="")],
    )
    assert "CONNECTOR_CREDENTIAL_REFERENCE_MISSING" in result.reasons


def test_registration_does_not_write_or_create_live_authorization():
    from pathlib import Path
    source = Path("src/bybit_workbench/account_connector_evidence.py").read_text()
    assert "INSERT INTO" not in source
    assert "UPDATE control.execution_gates" not in source
    assert "OWNER ARM" in source
