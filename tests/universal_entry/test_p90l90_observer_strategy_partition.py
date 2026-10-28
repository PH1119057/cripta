"""P90 is never silently consumed by the old shared Observer route."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.p90l90_observer_strategy_partition import (
    partition_observer_strategies,
    require_p90_atomic_observer_connection,
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
    assert epoch.index("require_p90_atomic_observer_connection(observer_split)") < (
        epoch.index("registry = ActivePlanRegistry()")
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
    # Existing position Exit remains owned by the generic PAPER runtime.
    assert "paper.bootstrap_l53_history(symbol" in epoch
    assert "paper.on_public_trade(" in epoch
