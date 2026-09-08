from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bybit_workbench.domain.models import Candle
from bybit_workbench.entry_bot.config import EntryBotConfig
from bybit_workbench.entry_bot.engine import compute_latest_zone
from bybit_workbench.universal_entry import (
    ActivePlanRegistry,
    FrozenPolicy,
    MarketFactEnvelope,
    StrategyActivation,
    StrategyCard,
    TechnicalReadiness,
    TouchPolicy,
    TradeDirection,
    UniversalEntryEngine,
)
from bybit_workbench.universal_entry.market_watch import GenericOiPoint
from bybit_workbench.universal_entry.parity import (
    ParityPoint,
    V1DeterministicParityRunner,
    compare_parity_points,
)
from bybit_workbench.universal_entry.v1_compat import load_v1_compatibility_bundle

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _candles(symbol: str, timeframe: str, count: int, minutes: int) -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[Candle] = []
    for index in range(count):
        base = Decimal("100") + Decimal(index) / Decimal("20")
        opened = start + timedelta(minutes=index * minutes)
        rows.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                opened_at=opened,
                closed_at=opened + timedelta(minutes=minutes),
                open=base,
                high=base + Decimal("1.0"),
                low=base - Decimal("0.8"),
                close=base + Decimal("0.2"),
                volume=Decimal("10"),
            )
        )
    return tuple(rows)


def test_v1_compatibility_bundle_has_exact_scope_and_calibration_provenance() -> None:
    bundle = load_v1_compatibility_bundle(ROOT)
    assert set(bundle.card.symbols) == set(EntryBotConfig().working_symbols)
    assert len(bundle.card.symbols) == 10
    assert "BTCUSDT" not in bundle.card.symbols
    assert "ETHUSDT" not in bundle.card.symbols
    assert "DOGEUSDT" not in bundle.card.symbols
    assert "1000PEPEUSDT" not in bundle.card.symbols
    assert bundle.calibration_sha256 == (
        "b977bd42d76800a3eac63e42f67da7b75ecbf14e93c88761ff674cb084a32571"
    )
    assert set(bundle.calibration_rows) >= set(bundle.card.symbols)


def test_v1_card_materializes_all_historical_numbers_as_plan_data() -> None:
    bundle = load_v1_compatibility_bundle(ROOT)
    registry = ActivePlanRegistry()
    registry.register_card(bundle.card)
    plan = registry.activate(bundle.activation)
    watch = plan.watch_policy.to_dict()
    geometry = watch["geometry"]
    assert geometry["lookback_by_timeframe"] == {"5": 130, "15": 130}
    assert geometry["atr_period"] == 200
    assert geometry["zone_half_width_atr"] == "0.5"
    assert geometry["confluence_max_gap_percent"] == "0.25"
    assert watch["hourly_swing"]["threshold_percent"] == "10.0"
    assert plan.touch_policy.candidate_cooldown.anchor == "fact.candidate_bar_at"
    assert plan.post_signal_outcome_policy.favorable_threshold == Decimal("0.50")
    assert plan.post_signal_outcome_policy.adverse_threshold == Decimal("-1.00")
    assert plan.post_signal_outcome_policy.horizon == Decimal("360")
    assert plan.post_signal_outcome_policy.optional_embargo.duration == Decimal("60")


def test_generic_watch_range_atr_geometry_matches_legacy_on_same_normalized_history() -> None:
    bundle = load_v1_compatibility_bundle(ROOT)
    registry = ActivePlanRegistry()
    registry.register_card(bundle.card)
    plan = registry.activate(bundle.activation)
    engine = UniversalEntryEngine(registry)
    five = _candles("UNIUSDT", "5", 260, 5)
    fifteen = _candles("UNIUSDT", "15", 260, 15)
    sixty = _candles("UNIUSDT", "60", 260, 60)
    observed = five[-1].closed_at
    engine.load_watch_history(
        "UNIUSDT",
        {"5": five, "15": fifteen, "60": sixty},
        (),
        observed_at=observed,
    )
    snap = engine.watch_snapshot(plan.entry_plan_fingerprint, "UNIUSDT")
    legacy5 = compute_latest_zone(
        five,
        timeframe="5",
        lookback=130,
        atr_period=200,
        width_atr=Decimal("0.5"),
        shock_atr_period=20,
        shock_atr_multiple=Decimal("3.0"),
        minimum_regime_bars=12,
    )
    legacy15 = compute_latest_zone(
        fifteen,
        timeframe="15",
        lookback=130,
        atr_period=200,
        width_atr=Decimal("0.5"),
        shock_atr_period=20,
        shock_atr_multiple=Decimal("3.0"),
        minimum_regime_bars=4,
    )
    assert legacy5 is not None and legacy15 is not None
    assert snap.geometry["5"].support_top == legacy5.support_top
    assert snap.geometry["5"].resistance_bottom == legacy5.resistance_bottom
    assert snap.geometry["15"].support_top == legacy15.support_top
    assert snap.geometry["15"].resistance_bottom == legacy15.resistance_bottom


def _post_signal_test_engine() -> tuple[UniversalEntryEngine, str]:
    lifecycle = {
        "post_signal_outcome_policy": {
            "enabled": True,
            "observation_event_kind": "PUBLIC_TRADE",
            "reference_value_path": "fact.entry_price",
            "observation_value_path": "fact.price",
            "metric": "DIRECTIONAL_PERCENT_CHANGE",
            "favorable_threshold": "0.50",
            "adverse_threshold": "-1.00",
            "horizon": "360",
            "horizon_unit": "minutes",
            "resolution_semantics": "FIRST_THRESHOLD",
            "favorable_resulting_entry_state": "CLEAR",
            "adverse_resulting_entry_state": "EMBARGO",
            "optional_embargo": {
                "enabled": True,
                "on_resolution": "ADVERSE",
                "duration": "60",
                "unit": "minutes",
                "scope": "PER_SYMBOL",
                "anchor": "fact.observed_at",
            },
        }
    }
    card = StrategyCard.build(
        strategy_id="generic-post-signal",
        strategy_version="1",
        name="generic post signal",
        description="test",
        scope=FrozenPolicy.from_mapping({"symbols": ["UNIUSDT"]}),
        symbols=("UNIUSDT",),
        direction_policy=(TradeDirection.LONG,),
        entry_policy=FrozenPolicy.from_mapping(
            {
                "entry_plan_version": "1",
                "predicate": {"op": "TOUCH"},
                "watch_policy": {"enabled": False},
            }
        ),
        exit_policy=FrozenPolicy.from_mapping({"exit_plan_version": "1"}),
        capital_policy=FrozenPolicy.from_mapping({}),
        protection_policy=FrozenPolicy.from_mapping({}),
        lifecycle_policy=FrozenPolicy.from_mapping(lifecycle),
        touch_policy=TouchPolicy(accept_touch_from=1),
        approved_at=NOW,
    )
    activation = StrategyActivation(
        "act-generic-post",
        card.strategy_id,
        card.strategy_version,
        card.strategy_config_fingerprint,
        True,
        NOW,
    )
    registry = ActivePlanRegistry()
    registry.register_card(card)
    plan = registry.activate(activation)
    return UniversalEntryEngine(registry), plan.entry_plan_fingerprint


def _fact(
    fact_id: str,
    kind: str,
    at: datetime,
    attrs: dict[str, object],
) -> MarketFactEnvelope:
    return MarketFactEnvelope(
        fact_id,
        kind,
        "UNIUSDT",
        at,
        at,
        at,
        (f"source:{fact_id}",),
        FrozenPolicy.from_mapping(attrs),
        TradeDirection.LONG,
    )


def test_post_signal_adverse_resolution_creates_entry_embargo_and_favorable_clears() -> None:
    engine, fingerprint = _post_signal_test_engine()
    ready = TechnicalReadiness(True, NOW, "ready")
    created = engine.process(
        _fact("touch-a", "TOUCH", NOW, {"entry_price": "100"}),
        technical_readiness=ready,
    )
    assert len(created) == 1
    assert engine.lifecycle_snapshot(fingerprint, "UNIUSDT").tracked_outcomes == 1
    engine.process(
        _fact("trade-a", "PUBLIC_TRADE", NOW + timedelta(minutes=1), {"price": "98.9"}),
        technical_readiness=ready,
    )
    state = engine.lifecycle_snapshot(fingerprint, "UNIUSDT")
    assert state.last_resolution == "ADVERSE"
    assert state.entry_embargo_until == NOW + timedelta(minutes=61)

    second, second_fp = _post_signal_test_engine()
    second.process(
        _fact("touch-b", "TOUCH", NOW, {"entry_price": "100"}),
        technical_readiness=ready,
    )
    second.process(
        _fact("trade-b", "PUBLIC_TRADE", NOW + timedelta(minutes=1), {"price": "100.6"}),
        technical_readiness=ready,
    )
    state2 = second.lifecycle_snapshot(second_fp, "UNIUSDT")
    assert state2.last_resolution == "FAVORABLE"
    assert state2.entry_embargo_until is None


def test_parity_comparator_reports_first_semantic_mismatch_not_only_count_diff() -> None:
    legacy = (
        ParityPoint("candidate", "k1", {"armed": True, "entry": "100"}),
        ParityPoint("touch", "k1", {"at": "2026-09-08T12:00:01+00:00"}),
    )
    universal = (
        ParityPoint("candidate", "k1", {"armed": True, "entry": "100"}),
        ParityPoint("touch", "k1", {"at": "2026-09-08T12:00:02+00:00"}),
    )
    report = compare_parity_points(legacy, universal)
    assert not report.passed
    assert report.first_mismatch is not None
    assert report.first_mismatch.category == "touch"
    assert report.first_mismatch.causal_key == "k1"
    assert report.first_mismatch.legacy_value != report.first_mismatch.universal_value


def test_generic_modules_contain_no_v1_strategy_branch_or_v1_numbers() -> None:
    generic_names = ("engine.py", "market_watch.py", "lifecycle.py", "dsl.py", "materializer.py")
    body = "\n".join(
        (ROOT / "src/bybit_workbench/universal_entry" / name).read_text(encoding="utf-8")
        for name in generic_names
    )
    forbidden = (
        "entry_v1_core",
        "pressure_then_reversal",
        "b977bd42",
        'Decimal("0.50")',
        'Decimal("-1.00")',
        "lookback = 130",
        "candidate_bar_at ==",
    )
    for token in forbidden:
        assert token not in body


def _aligned_candles(
    symbol: str,
    timeframe: str,
    count: int,
    minutes: int,
    end_at: datetime,
) -> tuple[Candle, ...]:
    start = end_at - timedelta(minutes=count * minutes)
    rows: list[Candle] = []
    for index in range(count):
        base = Decimal("100") + Decimal(index) / Decimal("100")
        opened = start + timedelta(minutes=index * minutes)
        rows.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                opened_at=opened,
                closed_at=opened + timedelta(minutes=minutes),
                open=base,
                high=base + Decimal("0.40"),
                low=base - Decimal("0.40"),
                close=base + Decimal("0.05"),
                volume=Decimal("10"),
            )
        )
    return tuple(rows)


def _source_fact(
    fact_id: str,
    kind: str,
    at: datetime,
    attrs: dict[str, object],
) -> MarketFactEnvelope:
    return MarketFactEnvelope(
        fact_id=fact_id,
        event_kind=kind,
        symbol="UNIUSDT",
        observed_at=at,
        event_at=at,
        received_at=at,
        source_refs=(f"normalized:{fact_id}",),
        attributes=FrozenPolicy.from_mapping(attrs),
    )


def test_stateful_v1_parity_compares_full_causal_sequence_through_adverse_embargo() -> None:
    bundle = load_v1_compatibility_bundle(ROOT)
    end_at = NOW
    five = _aligned_candles("UNIUSDT", "5", 260, 5, end_at)
    fifteen = _aligned_candles("UNIUSDT", "15", 260, 15, end_at)
    sixty = _aligned_candles("UNIUSDT", "60", 260, 60, end_at)
    oi = (
        GenericOiPoint(end_at - timedelta(minutes=65), Decimal("100")),
        GenericOiPoint(end_at - timedelta(minutes=60), Decimal("100")),
        GenericOiPoint(end_at - timedelta(minutes=5), Decimal("100")),
        GenericOiPoint(end_at, Decimal("100")),
    )
    runner = V1DeterministicParityRunner(
        bundle,
        symbol="UNIUSDT",
        candles={"5": five, "15": fifteen, "60": sixty},
        oi_points=oi,
        observed_at=end_at,
    )
    assert runner.result().report.passed

    initial = runner.universal.watch_snapshot(runner.plan.entry_plan_fingerprint, "UNIUSDT")
    assert initial.long_entry is not None and initial.short_entry is not None
    safe_price = (initial.long_entry + initial.short_entry) / Decimal("2")

    # Four pressure minutes followed by one reversal minute, all without touching.
    for offset in range(4):
        result = runner.step(
            _source_fact(
                f"pressure-{offset}",
                "PUBLIC_TRADE",
                end_at + timedelta(minutes=offset, seconds=20),
                {"price": safe_price, "size": "1", "taker_side": "Sell"},
            )
        )
        assert result.report.passed, result.report.first_mismatch
    result = runner.step(
        _source_fact(
            "reversal",
            "PUBLIC_TRADE",
            end_at + timedelta(minutes=4, seconds=20),
            {"price": safe_price, "size": "4", "taker_side": "Buy"},
        )
    )
    assert result.report.passed, result.report.first_mismatch

    next_bar = end_at + timedelta(minutes=5)
    result = runner.step(
        _source_fact(
            "oi-1205",
            "OPEN_INTEREST",
            next_bar,
            {"open_interest": "100"},
        )
    )
    assert result.report.passed, result.report.first_mismatch
    result = runner.step(
        _source_fact(
            "close-1205",
            "CANDLE_CLOSED",
            next_bar,
            {
                "timeframe": "5",
                "opened_at": end_at,
                "closed_at": next_bar,
                "open": safe_price,
                "high": safe_price + Decimal("0.20"),
                "low": safe_price - Decimal("0.20"),
                "close": safe_price,
                "volume": "10",
            },
        )
    )
    assert result.report.passed, result.report.first_mismatch

    armed = runner.universal.watch_snapshot(runner.plan.entry_plan_fingerprint, "UNIUSDT")
    assert armed.long_entry is not None
    entry = armed.long_entry
    result = runner.step(
        _source_fact(
            "touch",
            "PUBLIC_TRADE",
            next_bar + timedelta(seconds=30),
            {"price": entry, "size": "1", "taker_side": "Buy"},
        )
    )
    assert result.report.passed, result.report.first_mismatch
    assert any(
        point.category == "strategy_signal" and point.value is not None
        for point in result.legacy_points
    )

    result = runner.step(
        _source_fact(
            "adverse",
            "PUBLIC_TRADE",
            next_bar + timedelta(minutes=1),
            {"price": entry * Decimal("0.989"), "size": "1", "taker_side": "Sell"},
        )
    )
    assert result.report.passed, result.report.first_mismatch
    lifecycle = runner.universal.lifecycle_snapshot(runner.plan.entry_plan_fingerprint, "UNIUSDT")
    assert lifecycle.last_resolution == "ADVERSE"
    assert lifecycle.entry_embargo_until == next_bar + timedelta(minutes=61)


def _new_parity_runner() -> tuple[V1DeterministicParityRunner, datetime, Decimal]:
    bundle = load_v1_compatibility_bundle(ROOT)
    end_at = NOW
    five = _aligned_candles("UNIUSDT", "5", 260, 5, end_at)
    fifteen = _aligned_candles("UNIUSDT", "15", 260, 15, end_at)
    sixty = _aligned_candles("UNIUSDT", "60", 260, 60, end_at)
    oi = (
        GenericOiPoint(end_at - timedelta(minutes=65), Decimal("100")),
        GenericOiPoint(end_at - timedelta(minutes=60), Decimal("100")),
        GenericOiPoint(end_at - timedelta(minutes=5), Decimal("100")),
        GenericOiPoint(end_at, Decimal("100")),
    )
    runner = V1DeterministicParityRunner(
        bundle,
        symbol="UNIUSDT",
        candles={"5": five, "15": fifteen, "60": sixty},
        oi_points=oi,
        observed_at=end_at,
    )
    snapshot = runner.universal.watch_snapshot(runner.plan.entry_plan_fingerprint, "UNIUSDT")
    assert snapshot.long_entry is not None and snapshot.short_entry is not None
    return runner, end_at, (snapshot.long_entry + snapshot.short_entry) / Decimal("2")


def _advance_to_touchable_bar(
    runner: V1DeterministicParityRunner,
    end_at: datetime,
    safe_price: Decimal,
    *,
    good_flow: bool,
    oi_value: Decimal = Decimal("100"),
) -> Decimal:
    pressure_side = "Sell" if good_flow else "Buy"
    for offset in range(4):
        report = runner.step(
            _source_fact(
                f"prep-pressure-{offset}-{good_flow}-{oi_value}",
                "PUBLIC_TRADE",
                end_at + timedelta(minutes=offset, seconds=20),
                {"price": safe_price, "size": "1", "taker_side": pressure_side},
            )
        ).report
        assert report.passed, report.first_mismatch
    report = runner.step(
        _source_fact(
            f"prep-reversal-{good_flow}-{oi_value}",
            "PUBLIC_TRADE",
            end_at + timedelta(minutes=4, seconds=20),
            {"price": safe_price, "size": "4", "taker_side": "Buy"},
        )
    ).report
    assert report.passed, report.first_mismatch
    next_bar = end_at + timedelta(minutes=5)
    report = runner.step(
        _source_fact(
            f"prep-oi-{good_flow}-{oi_value}",
            "OPEN_INTEREST",
            next_bar,
            {"open_interest": oi_value},
        )
    ).report
    assert report.passed, report.first_mismatch
    report = runner.step(
        _source_fact(
            f"prep-close-{good_flow}-{oi_value}",
            "CANDLE_CLOSED",
            next_bar,
            {
                "timeframe": "5",
                "opened_at": end_at,
                "closed_at": next_bar,
                "open": safe_price,
                "high": safe_price + Decimal("0.20"),
                "low": safe_price - Decimal("0.20"),
                "close": safe_price,
                "volume": "10",
            },
        )
    ).report
    assert report.passed, report.first_mismatch
    armed = runner.universal.watch_snapshot(runner.plan.entry_plan_fingerprint, "UNIUSDT")
    assert armed.long_entry is not None
    return armed.long_entry


def test_v1_parity_flow_veto_still_starts_candidate_cooldown_without_signal() -> None:
    runner, end_at, safe_price = _new_parity_runner()
    entry = _advance_to_touchable_bar(runner, end_at, safe_price, good_flow=False)
    touch_at = end_at + timedelta(minutes=5, seconds=30)
    result = runner.step(
        _source_fact(
            "flow-veto-touch",
            "PUBLIC_TRADE",
            touch_at,
            {"price": entry, "size": "1", "taker_side": "Buy"},
        )
    )
    assert result.report.passed, result.report.first_mismatch
    legacy_signal_points = [
        point for point in result.legacy_points if point.category == "strategy_signal"
    ]
    assert legacy_signal_points[-1].value is None
    cooldown_points = [
        point for point in result.legacy_points if point.category == "candidate_cooldown"
    ]
    assert cooldown_points[-1].value == end_at + timedelta(minutes=35)


def test_v1_parity_oi_tail_veto_matches_without_signal() -> None:
    runner, end_at, safe_price = _new_parity_runner()
    entry = _advance_to_touchable_bar(
        runner,
        end_at,
        safe_price,
        good_flow=True,
        oi_value=Decimal("102"),
    )
    result = runner.step(
        _source_fact(
            "oi-veto-touch",
            "PUBLIC_TRADE",
            end_at + timedelta(minutes=5, seconds=30),
            {"price": entry, "size": "1", "taker_side": "Buy"},
        )
    )
    assert result.report.passed, result.report.first_mismatch
    signals = [point for point in result.legacy_points if point.category == "strategy_signal"]
    assert signals[-1].value is None
    oi_points = [point for point in result.legacy_points if point.category == "oi_result"]
    assert oi_points[-1].value == "TAIL"


def test_v1_parity_favorable_resolution_clears_tracking_without_embargo() -> None:
    runner, end_at, safe_price = _new_parity_runner()
    entry = _advance_to_touchable_bar(runner, end_at, safe_price, good_flow=True)
    touch_at = end_at + timedelta(minutes=5, seconds=30)
    result = runner.step(
        _source_fact(
            "fav-touch",
            "PUBLIC_TRADE",
            touch_at,
            {"price": entry, "size": "1", "taker_side": "Buy"},
        )
    )
    assert result.report.passed, result.report.first_mismatch
    result = runner.step(
        _source_fact(
            "fav-resolution",
            "PUBLIC_TRADE",
            end_at + timedelta(minutes=6),
            {"price": entry * Decimal("1.006"), "size": "1", "taker_side": "Buy"},
        )
    )
    assert result.report.passed, result.report.first_mismatch
    lifecycle = runner.universal.lifecycle_snapshot(runner.plan.entry_plan_fingerprint, "UNIUSDT")
    assert lifecycle.last_resolution == "FAVORABLE"
    assert lifecycle.entry_embargo_until is None
    assert lifecycle.tracked_outcomes == 0


def test_v1_parity_hourly_swing_block_matches_on_load() -> None:
    bundle = load_v1_compatibility_bundle(ROOT)
    five = list(_aligned_candles("UNIUSDT", "5", 260, 5, NOW))
    five[-12] = replace(five[-12], low=Decimal("70"))
    fifteen = _aligned_candles("UNIUSDT", "15", 260, 15, NOW)
    sixty = _aligned_candles("UNIUSDT", "60", 260, 60, NOW)
    oi = (
        GenericOiPoint(NOW - timedelta(minutes=60), Decimal("100")),
        GenericOiPoint(NOW - timedelta(minutes=5), Decimal("100")),
        GenericOiPoint(NOW, Decimal("100")),
    )
    runner = V1DeterministicParityRunner(
        bundle,
        symbol="UNIUSDT",
        candles={"5": tuple(five), "15": fifteen, "60": sixty},
        oi_points=oi,
        observed_at=NOW,
    )
    result = runner.result()
    assert result.report.passed, result.report.first_mismatch
    swing = [point for point in result.legacy_points if point.category == "swing"][-1]
    assert swing.value["blocked"] is True
    candidate = [point for point in result.legacy_points if point.category == "candidate"][-1]
    assert candidate.value is None


def test_v1_parity_shock_reset_maturity_state_matches() -> None:
    bundle = load_v1_compatibility_bundle(ROOT)
    five = list(_aligned_candles("UNIUSDT", "5", 260, 5, NOW))
    fifteen = list(_aligned_candles("UNIUSDT", "15", 260, 15, NOW))
    sixty = _aligned_candles("UNIUSDT", "60", 260, 60, NOW)
    for rows in (five, fifteen):
        shock = rows[-20]
        rows[-20] = replace(
            shock,
            high=shock.high + Decimal("8"),
            low=shock.low - Decimal("8"),
        )
    oi = (
        GenericOiPoint(NOW - timedelta(minutes=60), Decimal("100")),
        GenericOiPoint(NOW - timedelta(minutes=5), Decimal("100")),
        GenericOiPoint(NOW, Decimal("100")),
    )
    runner = V1DeterministicParityRunner(
        bundle,
        symbol="UNIUSDT",
        candles={"5": tuple(five), "15": tuple(fifteen), "60": sixty},
        oi_points=oi,
        observed_at=NOW,
    )
    result = runner.result()
    assert result.report.passed, result.report.first_mismatch
    reset = [point for point in result.legacy_points if point.category == "shock_reset"][-1]
    assert isinstance(reset.value, dict)
    assert reset.value["5"] is not None
    assert reset.value["15"] is not None


def test_v1_parity_short_core_signal_direction_matches() -> None:
    runner, end_at, safe_price = _new_parity_runner()
    # For SHORT, adverse pressure is BUY and the reversal minute is SELL.
    for offset in range(4):
        result = runner.step(
            _source_fact(
                f"short-pressure-{offset}",
                "PUBLIC_TRADE",
                end_at + timedelta(minutes=offset, seconds=20),
                {"price": safe_price, "size": "1", "taker_side": "Buy"},
            )
        )
        assert result.report.passed, result.report.first_mismatch
    result = runner.step(
        _source_fact(
            "short-reversal",
            "PUBLIC_TRADE",
            end_at + timedelta(minutes=4, seconds=20),
            {"price": safe_price, "size": "4", "taker_side": "Sell"},
        )
    )
    assert result.report.passed, result.report.first_mismatch
    next_bar = end_at + timedelta(minutes=5)
    result = runner.step(
        _source_fact(
            "short-oi",
            "OPEN_INTEREST",
            next_bar,
            {"open_interest": "100"},
        )
    )
    assert result.report.passed, result.report.first_mismatch
    result = runner.step(
        _source_fact(
            "short-close",
            "CANDLE_CLOSED",
            next_bar,
            {
                "timeframe": "5",
                "opened_at": end_at,
                "closed_at": next_bar,
                "open": safe_price,
                "high": safe_price + Decimal("0.20"),
                "low": safe_price - Decimal("0.20"),
                "close": safe_price,
                "volume": "10",
            },
        )
    )
    assert result.report.passed, result.report.first_mismatch
    armed = runner.universal.watch_snapshot(runner.plan.entry_plan_fingerprint, "UNIUSDT")
    assert armed.short_entry is not None
    result = runner.step(
        _source_fact(
            "short-touch",
            "PUBLIC_TRADE",
            next_bar + timedelta(seconds=30),
            {"price": armed.short_entry, "size": "1", "taker_side": "Sell"},
        )
    )
    assert result.report.passed, result.report.first_mismatch
    signal_points = [point for point in result.legacy_points if point.category == "strategy_signal"]
    assert signal_points[-1].value is not None
    assert signal_points[-1].value["direction"] == "SHORT"


def test_v1_parity_requires_closed_sixty_minute_history_before_candidate() -> None:
    bundle = load_v1_compatibility_bundle(ROOT)
    five = _aligned_candles("UNIUSDT", "5", 260, 5, NOW)
    fifteen = _aligned_candles("UNIUSDT", "15", 260, 15, NOW)
    # Last 60m candle closes one hour before the candidate boundary.
    sixty = _aligned_candles("UNIUSDT", "60", 260, 60, NOW - timedelta(hours=1))
    oi = (
        GenericOiPoint(NOW - timedelta(minutes=60), Decimal("100")),
        GenericOiPoint(NOW - timedelta(minutes=5), Decimal("100")),
        GenericOiPoint(NOW, Decimal("100")),
    )
    runner = V1DeterministicParityRunner(
        bundle,
        symbol="UNIUSDT",
        candles={"5": five, "15": fifteen, "60": sixty},
        oi_points=oi,
        observed_at=NOW,
    )
    result = runner.result()
    assert result.report.passed, result.report.first_mismatch
    candidate = [point for point in result.legacy_points if point.category == "candidate"][-1]
    assert candidate.value is None
