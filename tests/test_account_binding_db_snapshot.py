from datetime import UTC, datetime

import pytest

from bybit_workbench.account_binding_db_snapshot import read_binding_readiness

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, strategy_ids, bindings, strategy_identity=None):
        self.strategy_ids = strategy_ids
        self.bindings = bindings
        self.strategy_identity = strategy_identity or {}
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append((sql, tuple(params)))
        if "FROM strategy_entry.strategy_activations" in sql:
            return Rows([
                (
                    activation,
                    self.strategy_identity.get(activation, ("ordinary_strategy", "v1"))[0],
                    self.strategy_identity.get(activation, ("ordinary_strategy", "v1"))[1],
                )
                for activation in self.strategy_ids
            ])
        if "FROM strategy_entry.strategy_execution_bindings" in sql:
            return Rows(self.bindings)
        raise AssertionError(sql)


def binding(
    key,
    strategy,
    account,
    leg="PRIMARY",
    *,
    allocation_mode="DEDICATED_STRATEGY",
    strategy_id="ordinary_strategy",
    strategy_version="v1",
):
    return (
        key,
        strategy,
        account,
        leg,
        allocation_mode,
        "ACTIVE",
        strategy_id,
        strategy_version,
    )


def test_db_snapshot_one_strategy_multiple_accounts():
    db = FakeConnection(["activation-1"], [
        binding("b-1", "activation-1", "account-1", "LEG_A"),
        binding("b-2", "activation-1", "account-2", "LEG_B"),
    ])
    result = read_binding_readiness(db, now=NOW)
    assert result.ready
    assert len(result.strategy_accounts["activation-1"]) == 2
    assert db.queries[1][1] == (NOW, NOW)
    assert "effective_from<=%s" in db.queries[1][0]
    assert "effective_to>%s" in db.queries[1][0]
    assert all(not sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
               for sql, _ in db.queries)


def test_db_snapshot_missing_bindings_is_blocked():
    result = read_binding_readiness(
        FakeConnection(["activation-1"], []), now=NOW
    )
    assert not result.ready
    assert "STRATEGY_BINDING_MISSING" in result.reasons


def test_db_snapshot_shared_account_is_blocked():
    result = read_binding_readiness(FakeConnection(
        ["activation-1", "activation-2"],
        [binding("b-1", "activation-1", "shared"),
         binding("b-2", "activation-2", "shared")],
    ), now=NOW)
    assert not result.ready
    assert "TRADING_ACCOUNT_SHARED_BY_STRATEGIES" in result.reasons





def test_exact_owner_diagnostic_is_excluded_from_ordinary_binding_readiness():
    diagnostic_activation = "activation-owner-full-contour-main-20261101"
    db = FakeConnection(
        ["activation-1", diagnostic_activation],
        [
            binding("b-1", "activation-1", "shared"),
            binding(
                "binding-owner-full-contour-main-20261101",
                diagnostic_activation,
                "shared",
                allocation_mode="SUBORDINATE_DIAGNOSTIC",
                strategy_id="owner_full_contour_diag_main",
                strategy_version="1.0-owner-full-contour-20261101",
            ),
        ],
        strategy_identity={
            diagnostic_activation: (
                "owner_full_contour_diag_main",
                "1.0-owner-full-contour-20261101",
            )
        },
    )
    result = read_binding_readiness(db, now=NOW)
    assert result.ready
    assert result.strategy_accounts == {"activation-1": ("shared",)}


def test_requires_absolute_evaluation_instant():
    with pytest.raises(ValueError):
        read_binding_readiness(
            FakeConnection([], []), now=datetime(2026, 10, 20)
        )
