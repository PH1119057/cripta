from bybit_workbench.account_binding_readiness import check_binding_readiness


def binding(key, strategy, account, leg="PRIMARY", **overrides):
    return {
        "strategy_execution_binding_id": key,
        "strategy_activation_id": strategy,
        "trading_account_id": account,
        "execution_leg": leg,
        "allocation_mode": "DEDICATED_STRATEGY",
        "binding_state": "ACTIVE",
        **overrides,
    }


def test_one_strategy_many_accounts_is_allowed():
    result = check_binding_readiness(
        active_strategy_ids=["r1-a"],
        active_bindings=[
            binding("b-1", "r1-a", "bybit-sub-1", "LEG_A"),
            binding("b-2", "r1-a", "okx-sub-2", "LEG_B"),
        ],
    )
    assert result.ready
    assert result.strategy_accounts["r1-a"] == ("bybit-sub-1", "okx-sub-2")


def test_distinct_strategies_have_distinct_accounts():
    result = check_binding_readiness(
        active_strategy_ids=["r1-a", "r1-risk"],
        active_bindings=[
            binding("b-1", "r1-a", "bybit-sub-1"),
            binding("b-2", "r1-risk", "bybit-sub-2"),
        ],
    )
    assert result.ready


def test_same_account_cannot_be_owned_by_two_strategies():
    result = check_binding_readiness(
        active_strategy_ids=["r1-a", "r1-risk"],
        active_bindings=[
            binding("b-1", "r1-a", "shared"),
            binding("b-2", "r1-risk", "shared"),
        ],
    )
    assert not result.ready
    assert "TRADING_ACCOUNT_SHARED_BY_STRATEGIES" in result.reasons


def test_missing_binding_blocks_even_if_another_strategy_is_valid():
    result = check_binding_readiness(
        active_strategy_ids=["r1-a", "r1-risk"],
        active_bindings=[binding("b-1", "r1-a", "account-1")],
    )
    assert not result.ready
    assert "STRATEGY_BINDING_MISSING" in result.reasons


def test_one_leg_cannot_point_to_two_accounts():
    result = check_binding_readiness(
        active_strategy_ids=["r1-a"],
        active_bindings=[
            binding("b-1", "r1-a", "account-1"),
            binding("b-2", "r1-a", "account-2"),
        ],
    )
    assert not result.ready
    assert "EXECUTION_LEG_AMBIGUOUS" in result.reasons


def test_empty_or_unknown_identity_fails_closed():
    assert not check_binding_readiness(
        active_strategy_ids=[], active_bindings=[]
    ).ready
    assert "BINDING_IDENTITY_MISSING" in check_binding_readiness(
        active_strategy_ids=["r1-a"],
        active_bindings=[binding("b-1", "r1-a", "")],
    ).reasons


def test_inactive_or_shared_mode_never_proves_exclusive_allocation():
    result = check_binding_readiness(
        active_strategy_ids=["r1-a"],
        active_bindings=[binding("b-1", "r1-a", "account", allocation_mode="SHARED")],
    )
    assert not result.ready
    assert "BINDING_NOT_DEDICATED" in result.reasons


def test_same_account_cannot_be_bound_twice_even_for_same_strategy():
    result = check_binding_readiness(
        active_strategy_ids=["r1-a"],
        active_bindings=[
            binding("b-1", "r1-a", "account-1", "LEG_A"),
            binding("b-2", "r1-a", "account-1", "LEG_B"),
        ],
    )
    assert not result.ready
    assert "TRADING_ACCOUNT_MULTIPLE_ACTIVE_BINDINGS" in result.reasons


def test_null_strategy_identity_is_not_valid():
    result = check_binding_readiness(
        active_strategy_ids=[None], active_bindings=[]
    )
    assert not result.ready
    assert "ACTIVE_STRATEGY_IDENTITY_MISSING" in result.reasons
