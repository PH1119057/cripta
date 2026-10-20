from bybit_workbench.entry_account_fanout import select_entry_account_legs
from bybit_workbench.universal_entry.contracts import StrategyExecutionBindingRef


def ref(name, account, leg, activation="strategy-a"):
    return StrategyExecutionBindingRef(name, activation, leg, account)


def test_one_strategy_two_accounts_yields_independent_legs():
    result = select_entry_account_legs(
        strategy_activation_id="strategy-a",
        bindings=(ref("b2", "ta-okx", "LEG_B"), ref("b1", "ta-bybit", "LEG_A")),
        authorized_binding_ids=frozenset({"b1", "b2"}),
    )
    assert result.ready_for_account_admission
    assert [x.trading_account_id for x in result.legs] == ["ta-bybit", "ta-okx"]


def test_no_bindings_blocks():
    result = select_entry_account_legs(
        strategy_activation_id="strategy-a", bindings=(),
        authorized_binding_ids=frozenset(),
    )
    assert not result.ready_for_account_admission
    assert "EXECUTION_BINDINGS_MISSING" in result.reasons


def test_one_unauthorized_leg_blocks_whole_selection():
    result = select_entry_account_legs(
        strategy_activation_id="strategy-a",
        bindings=(ref("b1", "ta-1", "LEG_A"), ref("b2", "ta-2", "LEG_B")),
        authorized_binding_ids=frozenset({"b1"}),
    )
    assert not result.legs
    assert "BINDING_NOT_AUTHORIZED" in result.reasons


def test_same_account_twice_blocks():
    result = select_entry_account_legs(
        strategy_activation_id="strategy-a",
        bindings=(ref("b1", "ta-1", "LEG_A"), ref("b2", "ta-1", "LEG_B")),
        authorized_binding_ids=frozenset({"b1", "b2"}),
    )
    assert "ACCOUNT_BOUND_TWICE" in result.reasons


def test_same_leg_twice_blocks():
    result = select_entry_account_legs(
        strategy_activation_id="strategy-a",
        bindings=(ref("b1", "ta-1", "LEG_A"), ref("b2", "ta-2", "LEG_A")),
        authorized_binding_ids=frozenset({"b1", "b2"}),
    )
    assert "EXECUTION_LEG_DUPLICATE" in result.reasons


def test_cross_strategy_binding_blocks():
    result = select_entry_account_legs(
        strategy_activation_id="strategy-a",
        bindings=(ref("b1", "ta-1", "LEG_A", activation="strategy-b"),),
        authorized_binding_ids=frozenset({"b1"}),
    )
    assert "BINDING_ACTIVATION_MISMATCH" in result.reasons
