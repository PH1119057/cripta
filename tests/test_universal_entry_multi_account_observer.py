from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest

from bybit_workbench.strategy_position_binding import exchange_position_key
from bybit_workbench.universal_entry import (
    ActivePlanRegistry,
    DataQuality,
    MarketFactEnvelope,
    StrategyExecutionBindingRef,
    TechnicalReadiness,
    TradingCapacitySnapshot,
    UniversalEntryEngine,
)
from bybit_workbench.universal_entry.engine import AccountAdmissionContext
from operations.monitoring.universal_entry_shadow import (
    _blocked_multi_account_admissions,
    _multi_account_admissions,
    _unarmed_multi_account_candidate_bindings,
)

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


class Rows:
    def __init__(self, rows: list[tuple[str, str, str, str]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[str, str, str, str]]:
        return self.rows


class CandidateDB:
    def __init__(self, rows: list[tuple[str, str, str, str]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> Rows:
        self.queries.append((sql, params))
        return Rows(self.rows)


class Activation:
    def __init__(self, activation_id: str) -> None:
        self.activation_id = activation_id


class Bundle:
    def __init__(self, activation_id: str) -> None:
        self.activation = Activation(activation_id)


def binding(number: int, activation: str = "act-one") -> StrategyExecutionBindingRef:
    return StrategyExecutionBindingRef(
        f"binding-{number}",
        activation,
        f"LEG_{number}",
        f"trading-account-{number}",
    )


def test_live_observer_sees_every_active_account_without_creating_authority() -> None:
    one, two, three = binding(1), binding(2), binding(3, "act-single")
    rows = [
        (item.strategy_execution_binding_id, item.strategy_activation_id,
         item.execution_leg, item.trading_account_id)
        for item in (one, two, three)
    ]
    db = CandidateDB(rows)
    result = _unarmed_multi_account_candidate_bindings(
        db, (Bundle("act-one"), Bundle("act-single")),
        observed_at=NOW,
    )
    assert result == {"act-one": (one, two), "act-single": (three,)}
    assert len(db.queries) == 1
    query, params = db.queries[0]
    assert "strategy_entry.strategy_execution_bindings" in query
    assert "effective_from<=%s" in query
    assert "binding_state='ACTIVE'" in query
    assert "execution_leg='PRIMARY'" not in query
    assert "trading_account_id=%s" not in query
    assert params == (NOW, NOW)
    assert query.lstrip().startswith("SELECT")


def test_unarmed_multi_account_bindings_are_blocked_per_leg_no_admission_port() -> None:
    one, two = binding(1), binding(2)
    candidates = {"act-one": (one, two)}
    prepared = _blocked_multi_account_admissions(
        candidates, symbol="BTCUSDT", observed_at=NOW
    )
    assert set(prepared) == {"act-one"}
    assert len(prepared["act-one"]) == 2
    for proof, ref in zip(prepared["act-one"], (one, two), strict=True):
        assert isinstance(proof, AccountAdmissionContext)
        assert proof.binding == ref
        assert proof.entry_admission_port is None
        assert proof.capacity is None
        assert not proof.technical_readiness.ready
        assert proof.technical_readiness.reason == "MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN"
        assert proof.exchange_position_key == exchange_position_key(
            ref.trading_account_id, "BTCUSDT", 0
        )
    assert len({x.exchange_position_key for x in prepared["act-one"]}) == 2


def test_single_main_keeps_legacy_while_single_sub_requires_account_arm() -> None:
    main = StrategyExecutionBindingRef(
        "binding-main", "act-one", "PRIMARY", "trading-account-main"
    )
    sub = binding(1)

    def roster(ref: StrategyExecutionBindingRef) -> CandidateDB:
        return CandidateDB([(
            ref.strategy_execution_binding_id,
            ref.strategy_activation_id,
            ref.execution_leg,
            ref.trading_account_id,
        )])

    with patch(
        "operations.monitoring.universal_entry_shadow.TRADING_ACCOUNT_ID",
        "trading-account-main",
    ):
        assert _unarmed_multi_account_candidate_bindings(
            roster(main), (Bundle("act-one"),), observed_at=NOW
        ) == {}
        assert _unarmed_multi_account_candidate_bindings(
            roster(sub), (Bundle("act-one"),), observed_at=NOW
        ) == {"act-one": (sub,)}
        # No fallback to a default or legacy arm for an unproven SUB.
        assert _unarmed_multi_account_candidate_bindings(
            roster(sub), (Bundle("act-other"),), observed_at=NOW
        ) == {}


def test_legacy_and_fanout_preflight_allow_only_disjoint_activations() -> None:
    one, two = binding(1), binding(2)
    ctx = _blocked_multi_account_admissions(
        {"act-one": (one, two)}, symbol="BTCUSDT", observed_at=NOW
    )
    engine = UniversalEntryEngine(ActivePlanRegistry())
    fact = MarketFactEnvelope(
        fact_id="sample",
        event_kind="TOUCH",
        symbol="BTCUSDT",
        observed_at=NOW,
        event_at=NOW,
        received_at=NOW,
        source_refs=("test:isolated",),
    )
    # Unregistered strategy plans cannot cause an Entry in either path.
    assert engine.process(
        fact,
        account_admissions=ctx,
        execution_bindings={"act-other": binding(3, "act-other")},
        real_admission_required_for=frozenset({"act-one", "act-other"}),
    ) == ()
    with pytest.raises(ValueError, match="REAL_FANOUT_AND_LEGACY_BINDING_ACTIVATION_OVERLAP"):
        engine.process(
            fact,
            account_admissions=ctx,
            execution_bindings={"act-one": one},
        )


def test_unarmed_candidate_requires_aware_snapshot() -> None:
    with pytest.raises(ValueError, match="aware timestamp"):
        _unarmed_multi_account_candidate_bindings(
            CandidateDB([]), (Bundle("act-one"),),
            observed_at=datetime(2026, 10, 24),
        )


class _ScopedCard:
    def __init__(self) -> None:
        self.strategy_id = "strategy-1"
        self.strategy_version = "1"
        self.strategy_config_fingerprint = "strategy-fp"
        self.symbols = ("BTCUSDT",)


class _ScopedBundle(Bundle):
    def __init__(self) -> None:
        super().__init__("act-one")
        self.card = _ScopedCard()


class _ScopedRows:
    def __init__(self, items: list[tuple[object, ...]]) -> None:
        self.items = items

    def fetchone(self) -> tuple[object, ...] | None:
        return self.items[0] if self.items else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.items


class _ScopedDB:
    def __init__(
        self, *, gate: bool = True, missing_second: bool = False,
        blocked_second: bool = False,
    ) -> None:
        self.gate = gate
        self.missing_second = missing_second
        self.blocked_second = blocked_second
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _ScopedRows:
        self.queries.append((sql, params))
        if "control.execution_gates" in sql:
            return _ScopedRows([(self.gate,)])
        if "FROM identity.account_connectors c" in sql:
            if self.missing_second and params[0] == "trading-account-2":
                return _ScopedRows([])
            return _ScopedRows([(f"connector-{params[0]}",)])
        if "to_regclass" in sql:
            return _ScopedRows([("runtime.account_entry_safety_blocks",)])
        if "FROM runtime.account_entry_safety_blocks" in sql:
            blocked = self.blocked_second and params[0] == "trading-account-2"
            return _ScopedRows([(1,)] if blocked else [])
        raise AssertionError(f"unexpected query in isolated admission test: {sql[:80]}")


def _known_capacity(account_id: str) -> TradingCapacitySnapshot:
    return TradingCapacitySnapshot(
        f"generation-{account_id}", NOW, Decimal("100"), DataQuality.HIGH,
        f"runtime.account_state_generations:generation-{account_id}",
    )


def test_producer_resolves_two_independent_armed_account_admissions() -> None:
    one, two = binding(1), binding(2)
    db = _ScopedDB()
    seen_accounts: list[str] = []

    def generation(_connection: object, *, symbol: str, observed_at: datetime,
                   trading_account_id: str) -> tuple[TechnicalReadiness, TradingCapacitySnapshot]:
        assert symbol == "BTCUSDT" and observed_at == NOW
        seen_accounts.append(trading_account_id)
        return TechnicalReadiness(True, NOW), _known_capacity(trading_account_id)

    with (
        patch(
            "operations.monitoring.universal_entry_shadow.LOADED_COMMIT",
            "a" * 40,
        ),
        patch(
            "operations.monitoring.universal_entry_shadow.account_live_arm_ready",
            return_value=(True, ()),
        ) as owner_arm,
        patch(
            "operations.monitoring.universal_entry_shadow._real_entry_account_state",
            side_effect=generation,
        ),
    ):
        resolved, ready = _multi_account_admissions(
            db, (_ScopedBundle(),), {"act-one": (one, two)},
            symbol="BTCUSDT", observed_at=NOW,
            real_selected_for=frozenset({"act-one"}),
        )
    assert ready == frozenset({"act-one"})
    assert seen_accounts == ["trading-account-1", "trading-account-2"]
    assert len(owner_arm.call_args_list) == 2
    scopes = resolved["act-one"]
    assert len(scopes) == 2
    assert all(item.technical_readiness.ready for item in scopes)
    assert all(item.capacity is not None for item in scopes)
    assert all(item.entry_admission_port is not None for item in scopes)
    assert scopes[0].exchange_position_key != scopes[1].exchange_position_key
    assert owner_arm.call_args_list[0].kwargs["trading_account_id"] == one.trading_account_id
    assert owner_arm.call_args_list[1].kwargs["trading_account_id"] == two.trading_account_id


def test_one_unarmed_account_does_not_block_authorized_other_account() -> None:
    one, two = binding(1), binding(2)
    db = _ScopedDB()
    def arm(_connection: object, **fields: object) -> tuple[bool, tuple[str, ...]]:
        return ((True, ()) if fields["trading_account_id"] == one.trading_account_id
                else (False, ("EXACT_ACCOUNT_RELEASE_OWNER_ARM_NOT_ACTIVE",)))
    with (
        patch("operations.monitoring.universal_entry_shadow.LOADED_COMMIT", "a" * 40),
        patch("operations.monitoring.universal_entry_shadow.account_live_arm_ready",
              side_effect=arm),
        patch(
            "operations.monitoring.universal_entry_shadow._real_entry_account_state",
            return_value=(TechnicalReadiness(True, NOW), _known_capacity("one")),
        ),
    ):
        resolved, ready = _multi_account_admissions(
            db, (_ScopedBundle(),), {"act-one": (one, two)},
            symbol="BTCUSDT", observed_at=NOW,
            real_selected_for=frozenset({"act-one"}),
        )
    assert ready == frozenset({"act-one"})
    assert resolved["act-one"][0].entry_admission_port is not None
    assert resolved["act-one"][1].entry_admission_port is None
    assert not resolved["act-one"][1].technical_readiness.ready
    assert (
        resolved["act-one"][1].technical_readiness.reason
        == "EXACT_ACCOUNT_RELEASE_OWNER_ARM_NOT_ACTIVE"
    )


def test_missing_connector_never_borrows_other_account_authorization() -> None:
    one, two = binding(1), binding(2)
    db = _ScopedDB(missing_second=True)
    with (
        patch("operations.monitoring.universal_entry_shadow.LOADED_COMMIT", "a" * 40),
        patch(
            "operations.monitoring.universal_entry_shadow.account_live_arm_ready",
            return_value=(True, ()),
        ) as arm,
        patch(
            "operations.monitoring.universal_entry_shadow._real_entry_account_state",
            return_value=(TechnicalReadiness(True, NOW), _known_capacity("one")),
        ),
    ):
        resolved, ready = _multi_account_admissions(
            db, (_ScopedBundle(),), {"act-one": (one, two)},
            symbol="BTCUSDT", observed_at=NOW,
            real_selected_for=frozenset({"act-one"}),
        )
    assert ready == frozenset({"act-one"})
    assert len(arm.call_args_list) == 1
    assert resolved["act-one"][1].technical_readiness.reason == "ACCOUNT_CONNECTOR_ROUTE_NOT_READY"
    assert resolved["act-one"][1].entry_admission_port is None


def test_disarmed_mainnet_and_shadow_mode_never_authorize_private_admission() -> None:
    one, two = binding(1), binding(2)
    db = _ScopedDB(gate=False)
    with (
        patch("operations.monitoring.universal_entry_shadow.LOADED_COMMIT", "a" * 40),
        patch(
            "operations.monitoring.universal_entry_shadow.account_live_arm_ready"
        ) as arm,
    ):
        result, ready = _multi_account_admissions(
            db, (_ScopedBundle(),), {"act-one": (one, two)},
            symbol="BTCUSDT", observed_at=NOW,
            real_selected_for=frozenset({"act-one"}),
        )
        assert ready == frozenset()
        assert all(x.entry_admission_port is None for x in result["act-one"])
        arm.assert_not_called()
        queries_before = len(db.queries)
        shadow, ready = _multi_account_admissions(
            db, (_ScopedBundle(),), {"act-one": (one, two)},
            symbol="BTCUSDT", observed_at=NOW,
            real_selected_for=frozenset(),
        )
        assert ready == frozenset()
        assert len(db.queries) == queries_before
        assert all(x.entry_admission_port is None for x in shadow["act-one"])


def test_one_private_account_safety_latch_blocks_only_its_own_entry() -> None:
    one, two = binding(1), binding(2)
    db = _ScopedDB(blocked_second=True)
    with (
        patch("operations.monitoring.universal_entry_shadow.LOADED_COMMIT", "a" * 40),
        patch(
            "operations.monitoring.universal_entry_shadow.account_live_arm_ready",
            return_value=(True, ()),
        ),
        patch(
            "operations.monitoring.universal_entry_shadow._real_entry_account_state",
            return_value=(TechnicalReadiness(True, NOW), _known_capacity("one")),
        ) as generation,
    ):
        scopes, ready = _multi_account_admissions(
            db, (_ScopedBundle(),), {"act-one": (one, two)},
            symbol="BTCUSDT", observed_at=NOW,
            real_selected_for=frozenset({"act-one"}),
        )
    assert ready == frozenset({"act-one"})
    assert generation.call_count == 1
    assert scopes["act-one"][0].entry_admission_port is not None
    assert scopes["act-one"][1].entry_admission_port is None
    assert scopes["act-one"][1].technical_readiness.reason == "ACCOUNT_ENTRY_SAFETY_BLOCKED"


class _P90MainBundle(Bundle):
    def __init__(self) -> None:
        super().__init__("act-main-v3")
        self.card = type("P90Card", (), {
            "strategy_id": "p90l90_legacy_main_ltc_inj",
            "strategy_version": "2.0-p90l90-real-main-sub-20261028",
            "strategy_config_fingerprint": "config-p90-main",
            "symbols": ("LTCUSDT", "INJUSDT"),
        })()


class _P90WrongAccountKindDB(_ScopedDB):
    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _ScopedRows:
        if "FROM identity.account_connectors c" in sql:
            return _ScopedRows([("connector-sub", "SUB")])
        return super().execute(sql, params)


def test_p90_real_main_must_not_bind_to_signed_sub_even_if_armed() -> None:
    binding_main = StrategyExecutionBindingRef(
        "binding-p90-main", "act-main-v3", "PRIMARY", "ta-sub",
    )
    db = _P90WrongAccountKindDB()
    with (
        patch("operations.monitoring.universal_entry_shadow.LOADED_COMMIT", "a" * 40),
        patch(
            "operations.monitoring.universal_entry_shadow.account_live_arm_ready"
        ) as arm,
        patch(
            "operations.monitoring.universal_entry_shadow._real_entry_account_state"
        ) as capacity,
    ):
        scoped, ready = _multi_account_admissions(
            db, (_P90MainBundle(),),
            {"act-main-v3": (binding_main,)},
            symbol="LTCUSDT",
            observed_at=NOW,
            real_selected_for=frozenset({"act-main-v3"}),
        )
    assert ready == frozenset()
    assert scoped["act-main-v3"][0].entry_admission_port is None
    assert (
        scoped["act-main-v3"][0].technical_readiness.reason
        == "P90_REAL_ACCOUNT_KIND_MISMATCH"
    )
    arm.assert_not_called()
    capacity.assert_not_called()


def test_p90_real_main_cannot_reuse_legacy_main_primary_arm_path() -> None:
    main = StrategyExecutionBindingRef(
        "binding-p90-main", "act-main-v3", "PRIMARY", "ta-main",
    )
    roster = CandidateDB([(
        main.strategy_execution_binding_id,
        main.strategy_activation_id,
        main.execution_leg,
        main.trading_account_id,
    )])
    with patch(
        "operations.monitoring.universal_entry_shadow.TRADING_ACCOUNT_ID",
        "ta-main",
    ):
        selected = _unarmed_multi_account_candidate_bindings(
            roster, (_P90MainBundle(),), observed_at=NOW,
        )
    assert selected == {"act-main-v3": (main,)}
