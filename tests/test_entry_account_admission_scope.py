from decimal import Decimal

from bybit_workbench.entry_account_admission_scope import (
    AccountAdmissionEvidence,
    prepare_scoped_entry_legs,
)
from bybit_workbench.entry_account_fanout import select_entry_account_legs
from bybit_workbench.universal_entry.contracts import StrategyExecutionBindingRef


def binding(index):
    return StrategyExecutionBindingRef(
        f"binding-{index}", "activation-1", f"LEG_{index}", f"account-{index}"
    )


def fanout():
    return select_entry_account_legs(
        strategy_activation_id="activation-1",
        bindings=(binding(1), binding(2)),
        authorized_binding_ids=frozenset({"binding-1", "binding-2"}),
    )


def proof(index, **overrides):
    data = dict(
        trading_account_id=f"account-{index}",
        account_ref=f"exchange-account-{index}",
        account_state_generation_id=f"generation-{index}",
        capacity_snapshot_id=f"capacity-{index}",
        capacity_available=Decimal("100"),
        exchange_position_key=f"venue:{index}:BTCUSDT:0",
        connector_id=f"connector-{index}",
        private_exchange_verified=True,
        protection_capability_verified=True,
    )
    data.update(overrides)
    return AccountAdmissionEvidence(**data)


def test_each_leg_keeps_own_account_lineage():
    result = prepare_scoped_entry_legs(
        fanout=fanout(),
        evidence_by_account={"account-1": proof(1), "account-2": proof(2)},
    )
    assert result.ready_for_individual_admission
    assert [v.evidence.account_state_generation_id for v in result.legs] == [
        "generation-1", "generation-2"
    ]


def test_missing_second_account_rejects_entire_batch():
    result = prepare_scoped_entry_legs(
        fanout=fanout(), evidence_by_account={"account-1": proof(1)}
    )
    assert not result.legs
    assert "ACCOUNT_ADMISSION_EVIDENCE_MISSING" in result.reasons


def test_cross_account_identity_is_rejected():
    result = prepare_scoped_entry_legs(
        fanout=fanout(),
        evidence_by_account={
            "account-1": proof(1),
            "account-2": proof(2, trading_account_id="account-1"),
        },
    )
    assert "ACCOUNT_ADMISSION_IDENTITY_MISMATCH" in result.reasons


def test_shared_connector_is_not_independent_account_proof():
    result = prepare_scoped_entry_legs(
        fanout=fanout(),
        evidence_by_account={
            "account-1": proof(1),
            "account-2": proof(2, connector_id="connector-1"),
        },
    )
    assert "ACCOUNT_CONNECTOR_NOT_DISTINCT" in result.reasons


def test_unverified_exchange_and_protection_block():
    result = prepare_scoped_entry_legs(
        fanout=fanout(),
        evidence_by_account={
            "account-1": proof(1),
            "account-2": proof(
                2, private_exchange_verified=False,
                protection_capability_verified=False,
            ),
        },
    )
    assert "ACCOUNT_PRIVATE_EXCHANGE_NOT_VERIFIED" in result.reasons
    assert "ACCOUNT_PROTECTION_CAPABILITY_NOT_VERIFIED" in result.reasons


def test_no_side_effects_or_live_authority():
    from pathlib import Path
    source = Path("src/bybit_workbench/entry_account_admission_scope.py").read_text()
    assert "connection.execute(" not in source
    assert "reserve(" not in source
    assert "create_order(" not in source
