"""P90 is never silently consumed by the old shared Observer route."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.p90l90_observer_strategy_partition import (
    partition_observer_strategies,
    require_p90_atomic_observer_connection,
    require_p90_policy_before_registry,
)

P90 = "P90L90_RAW_H9_UNION_CROSSING"
VERSION = "1.1-p90l90-strategy-second48-20261028"


def bundle(
    *,
    activation_id: str,
    strategy_id: str,
    version: str = "R1",
    symbols: tuple[str, ...] = ("LTCUSDT",),
    operator: str | None = None,
):
    return SimpleNamespace(
        activation=SimpleNamespace(
            activation_id=activation_id, strategy_id=strategy_id,
            strategy_version=version, enabled=True,
        ),
        card=SimpleNamespace(
            strategy_id=strategy_id, strategy_version=version,
            symbols=symbols,
        ),
        entry_plan=SimpleNamespace(
            strategy_activation_id=activation_id, symbols=symbols,
            watch_policy=SimpleNamespace(
                to_dict=lambda: {"geometry": {"operator": operator}}
            ),
        ),
    )


def test_ordinary_strategy_bundle_is_untouched_and_never_blocked():
    a = bundle(activation_id="ordinary1", strategy_id="r1_old")
    b = bundle(
        activation_id="ordinary2", strategy_id="another_strategy",
        symbols=("BTCUSDT",),
    )
    split = partition_observer_strategies((a, b))
    assert split.ordinary == (a, b)
    assert split.p90_paper == ()
    require_p90_atomic_observer_connection(split)


def test_exact_p90_strategy_separated_and_old_shared_route_fails_closed():
    ordinary = bundle(activation_id="ordinary", strategy_id="r1_old")
    main = bundle(
        activation_id="p90main", strategy_id="p90l90_legacy_main_ltc_inj",
        version=VERSION, symbols=("LTCUSDT", "INJUSDT"), operator=P90,
    )
    sub = bundle(
        activation_id="p90sub", strategy_id="p90l90_legacy_sub_dot_arb_apt",
        version=VERSION, symbols=("DOTUSDT", "APTUSDT", "ARBUSDT"),
        operator=P90,
    )
    split = partition_observer_strategies((main, ordinary, sub))
    assert split.ordinary == (ordinary,)
    assert split.p90_paper == (main, sub)
    with pytest.raises(RuntimeError, match="P90L90_ATOMIC_OBSERVER_CONNECTION_NOT_PROVEN"):
        require_p90_atomic_observer_connection(split)


def test_unknown_p90_owner_wrong_version_or_partial_universe_hard_fails():
    rows = (
        bundle(
            activation_id="orphan", strategy_id="other",
            operator=P90,
        ),
        bundle(
            activation_id="bad1", strategy_id="p90l90_legacy_main_ltc_inj",
            version="historical-v1", symbols=("INJUSDT", "LTCUSDT"),
            operator=P90,
        ),
        bundle(
            activation_id="bad2", strategy_id="p90l90_legacy_main_ltc_inj",
            version=VERSION, symbols=("LTCUSDT",), operator=P90,
        ),
        bundle(
            activation_id="bad3", strategy_id="p90l90_legacy_main_ltc_inj",
            version=VERSION, symbols=("INJUSDT", "LTCUSDT"), operator=None,
        ),
    )
    for row in rows:
        with pytest.raises(RuntimeError, match="OBSERVER_P90_"):
            partition_observer_strategies((row,))


def test_duplicate_activation_cannot_be_registered_as_two_consumers():
    a = bundle(activation_id="dup", strategy_id="r1_old")
    b = bundle(activation_id="dup", strategy_id="r2_new")
    with pytest.raises(RuntimeError, match="DUPLICATE_STRATEGY_ACTIVATION"):
        partition_observer_strategies((a, b))


def test_working_observer_calls_guard_before_any_registry_activation():
    # Regression contract on the *real* Observer script, not a mocked route:
    # this guard must remain before the first mutation-capable registration.
    source = (Path(__file__).resolve().parents[2]
              / "operations/monitoring/universal_entry_shadow.py").read_text(
        encoding="utf-8"
    )
    epoch = source.split("def _run_observer_epoch(", 1)[1].split(
        "def _run_multi_strategy_observer(", 1
    )[0]
    assert epoch.index("partition_observer_strategies(bundles)") < epoch.index(
        "registry = ActivePlanRegistry()"
    )
    assert epoch.index("require_p90_policy_before_registry(observer_split)") < (
        epoch.index("registry = ActivePlanRegistry()")
    )
    assert epoch.index("p90_consumer.recover_all()") < (
        epoch.index("registry = ActivePlanRegistry()")
    )
    assert epoch.index("sock, _ready_local, _ready_server, buffered = _connect_and_subscribe") < (
        epoch.index("require_p90_atomic_observer_connection(")
    )
    assert epoch.index("require_p90_atomic_observer_connection(") < (
        epoch.index("while not stopping():")
    )



def test_actual_observer_has_one_p90_consumer_and_no_legacy_double_dispatch():
    source = (Path(__file__).resolve().parents[2]
              / "operations/monitoring/universal_entry_shadow.py").read_text(
        encoding="utf-8"
    )
    epoch = source.split("def _run_observer_epoch(", 1)[1].split(
        "def _run_multi_strategy_observer(", 1
    )[0]
    assert "P90L90ObserverPaperConsumer(" in epoch
    assert "p90_consumer.recover_all()" in epoch
    assert "p90_consumer.accept(" in epoch
    assert epoch.index("p90_consumer.recover_all()") < epoch.index(
        "registry = ActivePlanRegistry()"
    )
    assert "for raw_bundle in observer_split.ordinary" in epoch
    assert "observer_split.p90_paper" in epoch
    assert "paper.route_p90l90_native_trade(" not in epoch
    assert "paper.create_p90l90_native_paper_entry(" not in epoch
    assert "P90L90_ENTRY_REACHED_ORDINARY_OBSERVER_OWNER" in epoch
    assert "observer_split.ordinary" in epoch
    # Retired legacy/R1 PAPER rows are NOT active Strategy consumers.
    # P90-only market symbols must never reach the generic PAPER updater,
    # which would otherwise mutate archived open PAPER rows on every tick.
    assert (
        "if symbol in ordinary_symbols:\n"
        "            paper.bootstrap_l53_history(symbol"
    ) in epoch
    assert (
        'if timeframe == "5" and fact.symbol in ordinary_symbols:\n'
        "                paper.on_candle_closed("
    ) in epoch
    assert (
        'if fact.event_kind == "PUBLIC_TRADE" and fact.symbol in ordinary_symbols:\n'
        "            attrs = fact.attributes.to_dict()\n"
        "            paper.on_public_trade("
    ) in epoch
    assert "self._paper.route_p90l90_native_trade(" not in epoch


def test_p90_positive_runtime_gate_requires_real_checkpoint_and_live_ws_proof():
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from bybit_workbench.universal_entry.contracts import StrategyActivation
    from bybit_workbench.universal_entry.materializer import materialize_plans
    from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
    from bybit_workbench.universal_entry.p90l90_observer_strategy_partition import (
        P90NativeBarRuntimeProof,
    )
    from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
        StrategyHardStopEmbargoPolicy,
    )
    from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards
    from bybit_workbench.universal_entry.runtime_loader import ActiveStrategyBundle

    now = datetime.now(UTC)
    policy = StrategyHardStopEmbargoPolicy.parse({
        "enabled": True, "hardstop_count": 2,
        "rolling_hours": 48, "new_entry_pause_hours": 48,
        "close_existing_positions": False, "domain": "STRATEGY_VERSION",
    })
    cards = new_version_cards(
        approved_at=now-timedelta(seconds=1),
        policy_by_strategy_id={MAIN_ID: policy, SUB_ID: policy},
    )
    bundles = []
    for card in cards:
        activation = StrategyActivation(
            activation_id="source-proof-"+card.strategy_id,
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            enabled=True, enabled_at=now-timedelta(seconds=1),
        )
        entry, exit_ = materialize_plans(card, activation)
        bundles.append(ActiveStrategyBundle(card, activation, entry, exit_, now))
    split = partition_observer_strategies(tuple(bundles))
    require_p90_policy_before_registry(split)
    # Merely asking for paper_ready or supplying generic observer_ready is blocked.
    with pytest.raises(RuntimeError, match="NOT_PROVEN"):
        require_p90_atomic_observer_connection(split)
    symbols = frozenset(sym for bundle in bundles for sym in bundle.card.symbols)
    proof = P90NativeBarRuntimeProof(
        source_commit="a" * 40, restored_symbols=symbols,
        subscribed_symbols=symbols,
        subscribed_intervals=frozenset({"5", "15"}),
        mirror_active=True, subscribed_local_at=now,
        subscribed_server_at=now, epoch_started_at=now-timedelta(seconds=2),
    )
    assert proof.verified_for(symbols)
    require_p90_atomic_observer_connection(split, live_proof=proof)
    for weakened in (
        replace(proof, mirror_active=False),
        replace(proof, restored_symbols=frozenset({"LTCUSDT"})),
        replace(proof, subscribed_symbols=frozenset({"LTCUSDT"})),
        replace(proof, subscribed_intervals=frozenset({"5"})),
        replace(proof, subscribed_server_at=now-timedelta(seconds=17)),
        replace(proof, source_commit="not-an-installed-commit"),
    ):
        assert not weakened.verified_for(symbols)
        with pytest.raises(RuntimeError, match="NOT_PROVEN"):
            require_p90_atomic_observer_connection(split, live_proof=weakened)
