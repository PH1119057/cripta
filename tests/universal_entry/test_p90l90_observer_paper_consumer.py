"""Independent P90 Strategy consumer never reuses an ordinary Strategy engine."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, StrategyActivation
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_observer_paper_consumer import (
    P90L90ObserverPaperConsumer,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards
from bybit_workbench.universal_entry.runtime_loader import ActiveStrategyBundle

B = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
SHA = "3" * 40


def _bundle(ident: str) -> ActiveStrategyBundle:
    embargo = StrategyHardStopEmbargoPolicy.parse({
        "enabled": True, "hardstop_count": 2,
        "rolling_hours": 48, "new_entry_pause_hours": 48,
        "close_existing_positions": False, "domain": "STRATEGY_VERSION",
    })
    card = next(
        c for c in new_version_cards(
            approved_at=B, policy_by_strategy_id={
                MAIN_ID: embargo, SUB_ID: embargo,
            },
        ) if c.strategy_id == ident
    )
    activation = StrategyActivation(
        activation_id="activation-"+ident,
        strategy_id=card.strategy_id, strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        enabled=True, enabled_at=B,
    )
    entry, exit_ = materialize_plans(card, activation)
    return ActiveStrategyBundle(card, activation, entry, exit_, B)


def _consumer(bundles):
    return P90L90ObserverPaperConsumer(
        object(), bundles=bundles, source_commit=SHA,
        provenance=FrozenPolicy.from_mapping({"source": "test-no-exchange"}),
    )


def test_p90_has_unique_separate_entry_engine_and_five_symbol_owners():
    main, sub = _bundle(MAIN_ID), _bundle(SUB_ID)
    c = _consumer((main,sub))
    assert set(c._by_symbol) == set(main.card.symbols)|set(sub.card.symbols)
    assert len(c._processors) == 5
    assert c._recovered is False
    with pytest.raises(RuntimeError, match="NOT_RESTORED"):
        c.accept(object())


def test_p90_duplicate_strategy_symbol_or_disabled_activation_rejected():
    main = _bundle(MAIN_ID)
    with pytest.raises(RuntimeError, match="DUPLICATE_SYMBOL_OWNER"):
        _consumer((main, main))
    from dataclasses import replace
    disabled = replace(main, activation=replace(main.activation,enabled=False))
    with pytest.raises(RuntimeError, match="UNVERIFIED_EXACT_STRATEGY"):
        _consumer((disabled,))


def test_p90_consumer_cannot_be_created_without_owner_or_exact_version():
    with pytest.raises(ValueError, match="needs exact"):
        _consumer(())
    from dataclasses import replace
    main = _bundle(MAIN_ID)
    changed = replace(main, card=replace(main.card,strategy_version="historical-v1"))
    with pytest.raises(RuntimeError, match="UNVERIFIED_EXACT_STRATEGY"):
        _consumer((changed,))



def test_p90_cannot_recover_unwarmed_entry_and_treat_pending_as_ready():
    from types import SimpleNamespace

    from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalEntry

    consumer = _consumer((_bundle(MAIN_ID),))
    consumer._processors = {
        "LTCUSDT": SimpleNamespace(
            recover=lambda: (
                SimpleNamespace(closed_5m_at=B),
                P90L90CausalEntry(),
            )
        ),
    }
    with pytest.raises(RuntimeError, match="SEED_NOT_CAUSALLY_WARMED"):
        consumer.recover_all()
    assert consumer._recovered is False



def test_p90_recovery_and_dispatch_do_not_require_ordinary_engine():
    from datetime import timedelta
    from decimal import Decimal
    from types import SimpleNamespace

    from bybit_workbench.domain.models import Candle
    from bybit_workbench.universal_entry.p90l90_causal import (
        P90L90CausalEntry,
        P90L90EntryState,
    )

    consumer = _consumer((_bundle(MAIN_ID),))
    geometry = P90L90EntryState(
        effective_at=B,
        l53_low_raw=Decimal("100"), l53_high_raw=Decimal("110"),
        h9_5m_low_raw=Decimal("98"), h9_5m_high_raw=Decimal("112"),
        h9_15m_low_raw=Decimal("98"), h9_15m_high_raw=Decimal("112"),
        raw_h9_union_span=Decimal("14"),
        working_width_pct=Decimal("1"),
        previous_seven_day_median=Decimal("1"),
        long_depth_pct=Decimal("4"),
        short_depth_pct=None,
    )
    bars = tuple(
        Candle(
            symbol="LTCUSDT", timeframe="5",
            opened_at=B-timedelta(minutes=5*(36-i)),
            closed_at=B-timedelta(minutes=5*(35-i)),
            open=Decimal("100"), high=Decimal("101"),
            low=Decimal("99"), close=Decimal("100"),
            volume=Decimal("2"), is_closed=True,
        )
        for i in range(36)
    )

    class BarArchive:
        def closed_at_or_before(self, **kwargs):
            return B

        def history(self, **kwargs):
            return bars

    class MockProcessor:
        def recover(self):
            return (
                SimpleNamespace(closed_5m_at=B, closed_15m_at=B),
                P90L90CausalEntry(state=geometry),
            )

        def accept(self, event):
            return (event,)

    consumer._archive = BarArchive()
    consumer._processors = {"LTCUSDT": MockProcessor()}
    consumer.recover_all()
    assert consumer._recovered is True
    assert consumer._paper._l53_candles["LTCUSDT"][-1] == bars[-1]
    native = SimpleNamespace(symbol="LTCUSDT")
    assert consumer.accept(native) == (native,)
    with pytest.raises(RuntimeError, match="DOUBLE_RECOVERY"):
        consumer.recover_all()
